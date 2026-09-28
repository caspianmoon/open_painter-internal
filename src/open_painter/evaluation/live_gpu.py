"""GPU-resident texture composition, with the CPU evaluator's linear math.

This module only combines already prepared texture inputs. It never evaluates
mesh maps, creates shader nodes, reads a GPU texture, or writes/updates an Image.
The caller owns uploaded inputs and the output Image's shared GPU texture.
Disposable intermediate textures are reused by depth, not retained per layer.
"""
from collections import OrderedDict
from contextlib import contextmanager

import numpy as np

from ..constants import DEFAULTS
from . import metrics


# These indices are shader opcodes, independent of future RNA enum additions.
MODES = {name: index for index, name in enumerate((
    'NORMAL', 'MULTIPLY', 'ADD', 'SUBTRACT', 'OVERLAY', 'DIVIDE', 'DARKEN',
    'LIGHTEN', 'DIFFERENCE', 'EXCLUSION', 'SCREEN', 'LINEAR_BURN', 'COLOR_BURN',
    'COLOR_DODGE', 'SOFT_LIGHT', 'HARD_LIGHT', 'VIVID_LIGHT', 'LINEAR_LIGHT',
    'PIN_LIGHT', 'DISABLE', 'HUE', 'SATURATION', 'COLOR', 'LUMINOSITY',
    'NORMAL_COMBINE', 'RNM', 'NORMAL_ADD', 'NORMAL_REPLACE'))}

# op: clear, composite, passthrough, finish, copy, downsample.
# flags: source texture, mask, field, premultiplied source, opaque, normal final.
# Keep one compiled program for every layer, channel and scalar property edit.
FRAGMENT = r'''
float divide_safe(float a, float b) { return abs(b)>1e-8 ? a/b : 1.0; }
vec3 divide_safe(vec3 a, vec3 b) {
  return vec3(divide_safe(a.x,b.x),divide_safe(a.y,b.y),divide_safe(a.z,b.z));
}
vec3 burn(vec3 b, vec3 s) {
  vec3 r=1.0-min(vec3(1.0),divide_safe(1.0-b,s));
  return mix(mix(r,vec3(0.0),lessThanEqual(s,vec3(0.0))),vec3(1.0),greaterThanEqual(b,vec3(1.0)));
}
vec3 dodge(vec3 b, vec3 s) {
  vec3 r=min(vec3(1.0),divide_safe(b,1.0-s));
  return mix(mix(r,vec3(1.0),greaterThanEqual(s,vec3(1.0))),vec3(0.0),lessThanEqual(b,vec3(0.0)));
}
float lum(vec3 c) { return dot(c,vec3(.3,.59,.11)); }
float saturation(vec3 c) { return max(max(c.x,c.y),c.z)-min(min(c.x,c.y),c.z); }
vec3 set_lum(vec3 c, float l) {
  c+=l-lum(c);
  float low=min(min(c.x,c.y),c.z), high=max(max(c.x,c.y),c.z);
  if(low<0.0) c=l+(c-l)*divide_safe(l,l-low);
  if(high>1.0) c=l+(c-l)*divide_safe(1.0-l,high-l);
  return c;
}
vec3 set_sat(vec3 c, float s) {
  float low=min(min(c.x,c.y),c.z), delta=saturation(c);
  return delta>1e-8 ? (c-low)*divide_safe(s,delta) : vec3(0.0);
}
vec3 normal_unit(vec3 c) {
  float size=length(c);
  return size>1e-8 ? c/max(size,1e-8) : vec3(0.0,0.0,1.0);
}
vec3 blend_value(vec3 b, vec3 s, int mode) {
  if(mode==0) return s;
  if(mode==1) return b*s;
  if(mode==2) return b+s;
  if(mode==3) return b-s;
  if(mode==4) return mix(1.0-2.0*(1.0-b)*(1.0-s),2.0*b*s,lessThanEqual(b,vec3(.5)));
  if(mode==5) return clamp(divide_safe(b,s),0.0,1.0);
  if(mode==6) return min(b,s);
  if(mode==7) return max(b,s);
  if(mode==8) return abs(b-s);
  if(mode==9) return b+s-2.0*b*s;
  if(mode==10) return 1.0-(1.0-b)*(1.0-s);
  if(mode==11) return max(vec3(0.0),b+s-1.0);
  if(mode==12) return burn(b,s);
  if(mode==13) return dodge(b,s);
  if(mode==14) {
    vec3 curve=mix(sqrt(max(b,vec3(0.0))),((16.0*b-12.0)*b+4.0)*b,lessThanEqual(b,vec3(.25)));
    return mix(b+(2.0*s-1.0)*(curve-b),b-(1.0-2.0*s)*b*(1.0-b),lessThanEqual(s,vec3(.5)));
  }
  if(mode==15) return mix(1.0-2.0*(1.0-b)*(1.0-s),2.0*b*s,lessThanEqual(s,vec3(.5)));
  if(mode==16) return mix(dodge(b,2.0*s-1.0),burn(b,2.0*s),lessThanEqual(s,vec3(.5)));
  if(mode==17) return clamp(b+2.0*s-1.0,0.0,1.0);
  if(mode==18) return mix(max(b,2.0*s-1.0),min(b,2.0*s),lessThanEqual(s,vec3(.5)));
  if(mode==19) return b;
  if(mode==20) return set_lum(set_sat(s,saturation(b)),lum(b));
  if(mode==21) return set_lum(set_sat(b,saturation(s)),lum(b));
  if(mode==22) return set_lum(s,lum(b));
  if(mode==23) return set_lum(b,lum(s));
  vec3 n1=b*2.0-1.0, n2=s*2.0-1.0, result=n2;
  if(mode==24) result=vec3(n1.xy+n2.xy,n1.z*n2.z);
  if(mode==25) {
    vec3 t=n1+vec3(0.0,0.0,1.0), u=n2*vec3(-1.0,-1.0,1.0);
    result=t.z>1e-6 ? t*dot(t,u)/max(t.z,1e-6)-u : n2;
  }
  if(mode==26) result=n1+n2-vec3(0.0,0.0,1.0);
  return (normal_unit(result)+1.0)*.5;
}
void main() {
  ivec2 p=ivec2(gl_FragCoord.xy);
  int opcode=control.x, flags=control.z;
  if(opcode==0) { fragColor=constant_color; return; }
  if(opcode==5) {
    ivec2 size=textureSize(current_image,0), q=p*2;
    fragColor=(texelFetch(current_image,min(q,size-1),0)+
               texelFetch(current_image,min(q+ivec2(1,0),size-1),0)+
               texelFetch(current_image,min(q+ivec2(0,1),size-1),0)+
               texelFetch(current_image,min(q+ivec2(1,1),size-1),0))*.25;
    return;
  }
  vec4 b=texelFetch(current_image,p,0);
  if(opcode==4) {
    // Explicit filtered LOD at texel centers also works on Metal releases
    // whose texelFetch implementation ignores its nonzero LOD argument.
    fragColor=control.w==0 ? b : textureLod(current_image,
      (vec2(p)+.5)/vec2(textureSize(current_image,control.w)),float(control.w));
    return;
  }
  if(opcode==3) {
    vec3 rgb=b.rgb;
    if((flags&32)!=0) {
      vec3 normal=rgb*2.0-1.0;
      float size=length(normal);
      rgb=(size>1e-8 ? normal/size : vec3(0.0))*.5+.5;
    }
    fragColor=vec4(clamp(rgb,0.0,1.0),1.0); return;
  }
  float amount=parameters.x;
  if((flags&2)!=0) amount*=texelFetch(mask_image,p,0).r;
  vec4 s=(flags&1)!=0 ? texelFetch(source_image,p,0) : constant_color;
  if(opcode==2) { fragColor=b+amount*(s-b); return; }
  if((flags&8)!=0) s.rgb=s.a>1e-8 ? s.rgb/s.a : vec3(0.0);
  if((flags&4)!=0) {
    float field=texelFetch(field_image,p,0).r;
    if(control.w==1) s.rgb*=1.0+(field-.5)*parameters.y;
    if(control.w==2) s.rgb+=(field-.5)*parameters.y*.35;
    if(control.w==3) s.rgb=vec3(.5+(field-.5)*parameters.z);
    s.rgb=clamp(s.rgb,0.0,1.0);
  }
  float alpha=clamp(s.a*amount,0.0,1.0);
  if((flags&16)!=0) {
    fragColor=vec4(b.rgb+alpha*(blend_value(b.rgb,s.rgb,control.y)-b.rgb),1.0);
  } else {
    vec3 backdrop=b.a>1e-8 ? b.rgb/b.a : vec3(0.0);
    vec3 mixed=blend_value(backdrop,s.rgb,control.y);
    fragColor=vec4((1.0-alpha)*b.rgb+alpha*((1.0-b.a)*s.rgb+b.a*mixed),alpha+b.a*(1.0-alpha));
  }
}
'''


class Device:
    """An explicit window-context GPU compositor; call close before teardown.

    ``upload`` returns a caller-owned texture. The caller releases cached inputs
    by dropping those references; this device retains no uploaded layer images.
    It owns a one-pixel sampler fallback, a bounded intermediate pool and an
    optional 256 MiB LRU of complete first-root groups, including their coverage.
    """

    def __init__(self, *, checkpoint_bytes=256 * 1024 * 1024):
        import bpy
        import gpu
        from gpu_extras.batch import batch_for_shader

        if bpy.app.background or not bpy.context.window:
            raise RuntimeError("Live GPU composition requires a Blender window context")
        self.window = bpy.context.window.as_pointer()
        self.shader = self.batch = self.dummy = None
        self._scalar_device = self._mask_device = None
        self._pool = []
        self._mips = {}
        self._leased = set()
        self._pool_n = None
        self._checkpoints = OrderedDict()
        self._checkpoint_bytes = 0
        self._checkpoint_limit = min(256 * 1024 * 1024, max(0, int(checkpoint_bytes)))
        info = gpu.types.GPUShaderCreateInfo()
        info.vertex_in(0, 'VEC2', 'position')
        info.fragment_out(0, 'VEC4', 'fragColor')
        for index, name in enumerate(('current_image', 'source_image', 'mask_image', 'field_image')):
            info.sampler(index, 'FLOAT_2D', name)
        info.push_constant('IVEC4', 'control')
        info.push_constant('VEC4', 'parameters')
        info.push_constant('VEC4', 'constant_color')
        info.vertex_source('void main(){gl_Position=vec4(position,0.0,1.0);}')
        info.fragment_source(FRAGMENT)
        self.shader = gpu.shader.create_from_info(info)
        metrics.count('live_gpu_shader_compilations')
        self.batch = batch_for_shader(self.shader, 'TRIS', {
            'position': ((-1, -1), (3, -1), (-1, 3))})
        self.dummy = self.upload(np.ones((1, 1), np.float32))

    def _check(self):
        import bpy
        if self.shader is None or not bpy.context.window or bpy.context.window.as_pointer() != self.window:
            raise RuntimeError("Live GPU compositor window was closed or changed")

    def upload(self, array):
        import gpu
        self._check()
        array = np.ascontiguousarray(array, dtype=np.float32)
        if array.ndim == 2:
            texture_format = 'R32F'
        elif array.ndim == 3 and array.shape[2] == 4:
            texture_format = 'RGBA32F'
        else:
            raise ValueError("GPU inputs must be H×W float or H×W×4 RGBA")
        h, w = array.shape[:2]
        if h < 1 or w < 1:
            raise ValueError("GPU inputs cannot be empty")
        texture = gpu.types.GPUTexture((w, h), format=texture_format,
            data=gpu.types.Buffer('FLOAT', array.size, array.ravel()))
        metrics.count('live_gpu_uploads')
        metrics.count('live_gpu_upload_bytes', array.nbytes)
        metrics.count('live_gpu_allocations')
        metrics.count('live_gpu_allocated_bytes', array.nbytes)
        return texture

    def _acquire(self, n):
        import gpu
        for texture in self._pool:
            if id(texture) not in self._leased:
                self._leased.add(id(texture))
                return texture
        texture = gpu.types.GPUTexture((n, n), format='RGBA32F')
        self._pool.append(texture)
        self._leased.add(id(texture))
        metrics.count('live_gpu_allocations')
        metrics.count('live_gpu_allocated_bytes', n*n*16)
        metrics.gauge('live_gpu_scratch_bytes', len(self._pool)*n*n*16)
        return texture

    def _release(self, texture):
        self._leased.discard(id(texture))

    def _drop_checkpoint(self, key, counter='live_checkpoint_evictions', recycle=False):
        texture, size = self._checkpoints.pop(key)
        self._checkpoint_bytes -= size
        if recycle:
            # Adoption took this texture out of the scratch pool. A drop made
            # while composing is followed by a compose that needs that scratch
            # texture straight away; returning it avoids a full-size
            # allocation and free per replaced checkpoint. Every checkpoint
            # matches the pool resolution: compose clears both on a change.
            self._pool.append(texture)
            metrics.gauge('live_gpu_scratch_bytes', len(self._pool)*size)
        metrics.count(counter)
        metrics.gauge('live_checkpoint_cache_bytes', self._checkpoint_bytes)

    def discard_checkpoints(self, owner=None):
        """Release disposable group results for one texture-set owner or all."""
        for key in tuple(self._checkpoints):
            if owner is None or key[0] == owner:
                self._drop_checkpoint(key, 'live_checkpoint_invalidations')

    def prune_checkpoints(self, active_owners):
        """Remove deleted scene/texture-set owners without touching other sets."""
        active = set(active_owners)
        for key in tuple(self._checkpoints):
            if key[0] not in active:
                self._drop_checkpoint(key, 'live_checkpoint_invalidations')

    def _checkpoint_key(self, layer, n, channel):
        key = layer.get('checkpoint_key')
        if (channel is None or not isinstance(key, tuple) or len(key) != 5
                or key[1] != n or key[2] != channel):
            return None
        if (not isinstance(key[4], tuple) or len(key[4]) != 2
                or key[4][0] is not bool(layer.get('passthrough', False))):
            return None
        try:
            hash(key)
        except TypeError:
            return None
        return key

    def _checkpoint_get(self, key):
        # The first root starts from the channel default or transparent coverage.
        # Child edits, mode changes and replacement roots invalidate this owner/
        # channel bucket; do not retain undo history as GPU textures.
        for previous in tuple(self._checkpoints):
            if previous[:3] == key[:3] and previous != key:
                self._drop_checkpoint(previous, 'live_checkpoint_invalidations', recycle=True)
        entry = self._checkpoints.get(key)
        metrics.count('live_checkpoint_hits' if entry is not None else 'live_checkpoint_misses')
        if entry is not None:
            self._checkpoints.move_to_end(key)
            return entry[0]
        return None

    def _checkpoint_put(self, key, texture, n):
        size = n*n*16
        if size > self._checkpoint_limit:
            metrics.count('live_checkpoint_oversize_skips')
            return
        while self._checkpoints and self._checkpoint_bytes + size > self._checkpoint_limit:
            # One owner's channels are composed as a fixed cycle after every
            # root edit. When the cycle outgrows the cache, plain LRU evicts
            # the sibling the next edit needs first and never hits again;
            # keeping the resident siblings serves most of the cycle. Other
            # owners still age out, so the cache follows the edited set.
            victim = next((previous for previous in self._checkpoints
                           if previous[0] != key[0]), None)
            if victim is None:
                metrics.count('live_checkpoint_capacity_skips')
                return
            self._drop_checkpoint(victim, recycle=True)
        # Adopt the completed leased texture. It can never be selected by the
        # scratch allocator while retained as a checkpoint, and no copy pass is
        # needed to keep it. The caller's subsequent release is harmless.
        for index, candidate in enumerate(self._pool):
            if candidate is texture:
                self._pool.pop(index)
                break
        else:
            raise RuntimeError('Checkpoint source does not belong to the scratch pool')
        self._leased.discard(id(texture))
        self._checkpoints[key] = (texture, size)
        self._checkpoint_bytes += size
        metrics.count('live_checkpoint_adoptions')
        metrics.gauge('live_checkpoint_cache_bytes', self._checkpoint_bytes)
        metrics.gauge('live_gpu_scratch_bytes', len(self._pool)*size)

    def generate_scalar(self, kind, n, **parameters):
        from .live_procedurals import ScalarDevice, _configuration
        # Reject unsupported inputs before compiling or allocating GPU state.
        _configuration(kind, n, parameters.get('scale', 8.), parameters.get('seed', 1),
                       {k: v for k, v in parameters.items() if k not in {'scale', 'seed'}})
        self._check()
        if self._scalar_device is None:
            self._scalar_device = ScalarDevice()
        return self._scalar_device.generate(kind, n, **parameters)

    def generate_mask(self, ts, layer, n, **inputs):
        from .live_masks import MaskDevice, validate
        validate(ts, layer, n)
        self._check()
        if self._mask_device is None:
            self._mask_device = MaskDevice()
        return self._mask_device.generate(ts, layer, n, **inputs)

    @contextmanager
    def _state(self):
        import gpu
        self._check()
        viewport = gpu.state.viewport_get()
        scissor = gpu.state.scissor_get()
        blend = gpu.state.blend_get()
        depth = gpu.state.depth_test_get()
        depth_mask = gpu.state.depth_mask_get()
        try:
            gpu.state.blend_set('NONE')
            gpu.state.depth_test_set('NONE')
            gpu.state.depth_mask_set(False)
            yield
        finally:
            gpu.state.viewport_set(*viewport)
            gpu.state.scissor_set(*scissor)
            gpu.state.blend_set(blend)
            gpu.state.depth_test_set(depth)
            gpu.state.depth_mask_set(depth_mask)

    def _pass(self, target, n, *, opcode, current=None, source=None, mask=None,
              field=None, color=(0, 0, 0, 0), mode='NORMAL', opacity=1,
              detail=0, height=0, procedural=0, premultiplied=False,
              opaque=False, normal=False, mip=0):
        import gpu
        flags = ((1 if source is not None else 0) | (2 if mask is not None else 0) |
                 (4 if field is not None else 0) | (8 if premultiplied else 0) |
                 (16 if opaque else 0) | (32 if normal else 0))
        framebuffer = gpu.types.GPUFrameBuffer(color_slots={'texture': target, 'mip': mip})
        with framebuffer.bind():
            gpu.state.viewport_set(0, 0, n, n)
            # Preserve the caller's scissor-test enable state. A full target
            # rectangle works whether the test is currently enabled or disabled.
            gpu.state.scissor_set(0, 0, n, n)
            self.shader.bind()
            for name, texture in (('current_image', current), ('source_image', source),
                                  ('mask_image', mask), ('field_image', field)):
                self.shader.uniform_sampler(name, texture if texture is not None else self.dummy)
            self.shader.uniform_int('control', (opcode, MODES[mode], flags, procedural))
            self.shader.uniform_float('parameters', (opacity, detail, height, 0))
            self.shader.uniform_float('constant_color', color)
            self.batch.draw(self.shader)
        metrics.count('live_gpu_passes')

    def _stack(self, layers, current, n, opaque, checkpoint_channel=None):
        for index, layer in enumerate(layers):
            mode = layer.get('mode', 'NORMAL')
            amount = layer.get('opacity', 1.)
            if mode == 'DISABLE' or amount == 0:
                continue
            if mode not in MODES:
                raise ValueError(f"Unsupported GPU blend mode: {mode}")
            children = layer.get('children')
            mask = layer.get('mask')
            if children is not None:
                passthrough = layer.get('passthrough', False)
                if passthrough and amount == 1 and mask is None:
                    # No backdrop interpolation is needed for an organizational
                    # folder. Evaluate its children on the current target, with
                    # the same coverage mode; avoid a full-size copy and blend.
                    current = self._stack(children, current, n, opaque)
                    metrics.count('live_gpu_passthrough_elisions')
                    continue
                key = (self._checkpoint_key(layer, n, checkpoint_channel)
                       if index == 0 else None)
                group = self._checkpoint_get(key) if key is not None else None
                if group is None:
                    group = self._acquire(n)
                    self._pass(group, n, opcode=4 if passthrough else 0, current=current)
                    group = self._stack(children, group, n, opaque if passthrough else False)
                    if key is not None:
                        self._checkpoint_put(key, group, n)
                result = self._acquire(n)
                self._pass(result, n, opcode=2 if passthrough else 1,
                           current=current, source=group, mask=mask, mode=mode,
                           opacity=amount, opaque=opaque, premultiplied=not passthrough)
                self._release(group)
            else:
                source = layer.get('source')
                if source is None:
                    continue
                result = self._acquire(n)
                self._pass(result, n, opcode=1, current=current,
                           source=source.get('texture'), field=source.get('field'),
                           color=source.get('color', (0, 0, 0, 1)), mask=mask,
                           mode=mode, opacity=amount, detail=source.get('detail', 0),
                           height=source.get('height', 0), procedural=source.get('procedural', 0),
                           premultiplied=source.get('premultiplied', False), opaque=opaque)
            self._release(current)
            current = result
        return current

    @metrics.measured('live_gpu_composition')
    def compose(self, layers, channel, n, target_gpu):
        """Combine bottom-to-top layer records into a GPU-only RGBA32F result.

        ``source`` fields are texture/color, optional grayscale field, procedural
        kind (0 none, 1 color multiply, 2 scalar add, 3 height replace), detail,
        height and premultiplied. All texture inputs already occupy this UV grid.
        The final target is touched only after the complete stack succeeds.
        """
        if channel not in DEFAULTS or n < 1:
            raise ValueError("Invalid GPU output channel or resolution")
        if target_gpu.width != n or target_gpu.height != n or target_gpu.format != 'RGBA32F':
            raise ValueError("Live GPU output must be authored-size RGBA32F")
        if self._leased:
            raise RuntimeError("GPU compositor cannot be entered recursively")
        if self._pool_n != n:
            self._pool.clear()
            self._mips.clear()
            self.discard_checkpoints()
            self._pool_n = n
        with self._state():
            try:
                current = self._acquire(n)
                self._pass(current, n, opcode=0, color=DEFAULTS[channel])
                current = self._stack(layers, current, n, True, checkpoint_channel=channel)
                self._pass(target_gpu, n, opcode=3, current=current, normal=channel == 'NORMAL')
                metrics.count('live_gpu_compositions')
            finally:
                self._leased.clear()

    @metrics.measured('live_gpu_mipmaps')
    def generate_mips(self, target_gpu, n):
        """Update an Image-backed texture's existing mip chain, without readback.

        Independent shrinking scratch textures avoid sampling any image while
        that image is attached for writing. No uploaded texture data is changed.
        Blender's Image-backed textures allocate the mip chain; arbitrary
        GPUTexture outputs generally do not and should not call this method.
        """
        import gpu
        if target_gpu.width != n or target_gpu.height != n or n < 1:
            raise ValueError("Mipmap source dimensions do not match")
        with self._state():
            current, size, level = target_gpu, n, 0
            while size > 1:
                size = max(1, size // 2)
                level += 1
                scratch = self._mips.get(size)
                if scratch is None:
                    scratch = gpu.types.GPUTexture((size, size), format='RGBA32F')
                    self._mips[size] = scratch
                    metrics.count('live_gpu_allocations')
                    metrics.count('live_gpu_allocated_bytes', size*size*16)
                    metrics.gauge('live_gpu_mipmap_scratch_bytes', sum(s*s*16 for s in self._mips))
                self._pass(scratch, size, opcode=5, current=current)
                self._pass(target_gpu, size, opcode=4, current=scratch, mip=level)
                current = scratch
                metrics.count('live_gpu_mip_levels')

    def close(self):
        for child in (self._scalar_device, self._mask_device):
            if child is not None:
                child.close()
        self._scalar_device = self._mask_device = None
        if self.shader is not None:
            import gpu
            # Metal retains the currently bound program in context state.
            # Releasing that program while bound leaves a dangling C++ object:
            # the next Image GPU allocation can call its freed virtual methods.
            # Unbind before releasing shader/textures; no pixel readback or
            # synchronous GPU fence is needed in the interactive path.
            gpu.shader.unbind()
        self._leased.clear()
        self._pool.clear()
        self._mips.clear()
        self.discard_checkpoints()
        self.dummy = self.batch = self.shader = None
        metrics.gauge('live_gpu_scratch_bytes', 0)
        metrics.gauge('live_gpu_mipmap_scratch_bytes', 0)
