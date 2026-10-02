"""Restricted GPU generator mask stacks, using the CPU evaluator's arithmetic.

The caller owns dependency keys, field/map caches and the returned R32F raster.
This module never creates authored Images or touches their pixels. Unsupported
stacks are rejected in full before requesting any scalar field or baked map.
"""

from contextlib import contextmanager
import math
import re

from . import metrics
from .live_procedurals import QUALIFIED_BACKENDS, UnsupportedSource, _configuration


KINDS = {name: index for index, name in enumerate((
    'EDGE_WEAR', 'CHIPS', 'DIRT', 'CAVITY_DIRT', 'RUST', 'SCRATCHES'))}
REQUIRED = {'EDGE_WEAR': ('CURVATURE',), 'CHIPS': ('CURVATURE',),
            'DIRT': ('AO', 'CURVATURE'), 'CAVITY_DIRT': ('AO',),
            'RUST': ('AO',), 'SCRATCHES': ()}


class UnsupportedMask(UnsupportedSource):
    """The complete mask stack must retain its CPU reference path."""


def _fields(mask):
    if mask.generator == 'SCRATCHES':
        return ('SCRATCHES',)
    return ('FBM', 'VORONOI') if mask.generator == 'CHIPS' else ('FBM',)


def validate(ts, layer, n):
    """Pure preflight. No GPU allocation, scalar evaluation or image reading."""
    if not isinstance(n, int) or n < 1 or n > 8192 or n & (n - 1):
        raise UnsupportedMask('GPU masks require an authored power-of-two square grid up to 8192')
    masks = tuple(mask for mask in layer.masks if mask.enabled and getattr(mask, 'source', '') != 'ANCHOR')
    if not masks:
        raise UnsupportedMask('An empty stack needs no GPU mask texture')
    # Match map_pixels and the CPU edge generators: a non-null Image is present,
    # even when its pixels could not load. Preserve first-match selection when
    # a legacy project contains duplicate map kinds.
    images = {}
    for item in ts.baked_maps:
        if item.image:
            images.setdefault(item.kind, item.image)
    available = set(images)
    required = set()
    for mask in masks:
        if mask.source != 'GENERATOR' or mask.generator not in KINDS:
            raise UnsupportedMask('This mask source requires the CPU evaluator')
        if (mask.projection != 'UV' or tuple(mask.scale) != (1., 1.)
                or mask.rotation != 0 or tuple(mask.offset) != (0., 0.)
                or mask.tiling != 'REPEAT' or mask.filtering != 'BILINEAR'):
            raise UnsupportedMask('Transformed or non-default sampling masks require CPU evaluation')
        if mask.operation not in {'NORMAL', 'MULTIPLY', 'ADD'}:
            raise UnsupportedMask('Mask combination requires CPU evaluation')
        if mask.generator in {'EDGE_WEAR', 'CHIPS'} and getattr(mask, 'edge_width', 1.0) < 1:
            raise UnsupportedMask('A narrowed edge width requires CPU evaluation')
        if getattr(mask, 'edge_source', 'BAKED') == 'LIVE':
            raise UnsupportedMask('Live geometry edges require the CPU evaluator')
        if getattr(mask, 'detail_height_anchor', '') or getattr(mask, 'detail_normal_anchor', ''):
            raise UnsupportedMask('Micro detail anchors require CPU evaluation')
        for name, low, high in (('amount', 0., 1.), ('contrast', .01, 20.),
                                ('noise_strength', 0., 1.), ('black', 0., 1.),
                                ('white', 0., 1.), ('strength', 0., 1.)):
            value = getattr(mask, name)
            if not math.isfinite(value) or not low <= value <= high:
                raise UnsupportedMask('Mask parameters are outside their validated domain')
        gap = mask.white - mask.black
        if gap < .25:
            # A reversed/near-zero interval turns a small floating-point field
            # difference into a discontinuous mask change. The actual 2K probe
            # found such a mismatch; preserve CPU results for these settings.
            raise UnsupportedMask('Narrow or reversed mask Levels require CPU evaluation')
        if mask.generator == 'SCRATCHES' and 2 * mask.amount * mask.contrast / gap > 2:
            # The verified scalar scratch field differs by up to 6.26e-6 on
            # Metal. Bound its amplification below the mask parity tolerance;
            # Worn Painted Metal has gain .84 and remains eligible.
            raise UnsupportedMask('Strong scratch mask amplification requires CPU evaluation')
        for kind in _fields(mask):
            _configuration(kind, n, mask.noise_scale, mask.seed, {})
        required.update(REQUIRED[mask.generator])
        # CPU edge wear multiplies AO when available, although only curvature
        # is mandatory. Preserve that optional dependency exactly.
        if mask.generator in {'EDGE_WEAR', 'CHIPS'} and 'AO' in available:
            required.add('AO')
    missing = required - available
    if missing:
        raise UnsupportedMask('Baked maps are missing: ' + ', '.join(sorted(missing)))
    if any(not all(images[kind].size) for kind in required):
        # In particular, an unreadable optional AO must not be treated as absent:
        # CPU evaluation still consumes that image and reports its loading error.
        raise UnsupportedMask('Referenced baked map images have no loaded pixels')
    return masks, tuple(sorted(required))


def supports(ts, layer, n):
    """Pure full-stack eligibility, before compiling or requesting GPU inputs."""
    try:
        validate(ts, layer, n)
    except (UnsupportedSource, TypeError, ValueError, OverflowError):
        return False
    return True


FRAGMENT = r'''
ivec2 repeat_pixel(ivec2 p, ivec2 size) {
  return ivec2((p.x%size.x+size.x)%size.x, (p.y%size.y+size.y)%size.y);
}
float sample_map(sampler2D image, vec2 uv) {
  // Match sample_uv's center convention, repeat borders and float32 operation
  // order. Hardware linear filtering has less precision on some GPUs.
  ivec2 size=textureSize(image,0);
  vec2 p=fract(uv)*vec2(size)-0.5;
  ivec2 cell=ivec2(floor(p));
  vec2 f=p-vec2(cell);
  float a=texelFetch(image,repeat_pixel(cell,size),0).r*(1.0-f.x)
         +texelFetch(image,repeat_pixel(cell+ivec2(1,0),size),0).r*f.x;
  float b=texelFetch(image,repeat_pixel(cell+ivec2(0,1),size),0).r*(1.0-f.x)
         +texelFetch(image,repeat_pixel(cell+ivec2(1,1),size),0).r*f.x;
  return a*(1.0-f.y)+b*f.y;
}
float threshold_value(float value) {
  return clamp((value-(1.0-generator.x))*generator.y+0.5,0.0,1.0);
}
void main() {
  ivec2 p=ivec2(gl_FragCoord.xy);
  vec2 uv=gl_FragCoord.xy/vec2(control.w);
  int kind=control.x;
  float amount=generator.x, contrast=generator.y, noise_strength=generator.z;
  float noise=texelFetch(breakup_field,p,0).r;
  float value;
  if(kind==0||kind==1) {
    float curvature=clamp(sample_map(curvature_map,uv),0.0,1.0);
    value=clamp((curvature*(2.0*amount))*contrast,0.0,1.0);
    value*=((1.0-noise_strength)+clamp(noise,0.0,1.0)*noise_strength);
    if(generator.w>0.5)value*=clamp(sample_map(ao_map,uv),0.0,1.0);
    value=clamp(value,0.0,1.0);
    if(kind==1)value*=0.15+0.85*threshold_value(texelFetch(chip_field,p,0).r);
  }
  else if(kind==2) {
    float ao=sample_map(ao_map,uv), curvature=sample_map(curvature_map,uv);
    value=(1.0-ao)*(0.8+0.2*(1.0-curvature))+(amount-0.5)+(noise-0.5)*noise_strength;
    value=clamp(value*contrast,0.0,1.0);
  }
  else if(kind==3||kind==4) {
    float cavity=clamp(1.0-sample_map(ao_map,uv),0.0,1.0);
    value=kind==3?clamp((cavity*(1.0+amount*8.0))*contrast,0.0,1.0)
                 :threshold_value(noise+cavity*0.55);
    value*=((1.0-noise_strength)+noise_strength*noise);
  }
  else {
    value=clamp(((noise*amount)*2.0)*contrast,0.0,1.0);
  }
  value=clamp((value-levels.x)/levels.y,0.0,1.0);
  if(control.z!=0)value=1.0-value;
  float previous=texelFetch(previous_mask,control.y<0?ivec2(0):p,0).r;
  float strength=levels.z;
  if(control.y<0)fragColor=(1.0-strength)+value*strength;
  else if(control.y==0)fragColor=previous*(1.0-strength)+value*strength;
  else if(control.y==1)fragColor=previous*((1.0-strength)+value*strength);
  else fragColor=clamp(previous+value*strength,0.0,1.0);
}
'''
# Blender translates these functions into Metal struct methods. Floating-point
# directives are legal inside their bodies, not at the enclosing struct scope.
FRAGMENT = re.sub(r'(^\w+ \w+\([^\n]*\) \{)',
    r'\1\n#ifdef GPU_METAL\n#pragma clang fp contract(off)\n#pragma clang fp reassociate(off)\n#endif\n',
    FRAGMENT, flags=re.MULTILINE)


class MaskDevice:
    """Generate exact-structure mask rasters from caller-cached GPU inputs.

    ``field(kind, n, scale=..., seed=...)`` returns an R32F scalar texture,
    normally ScalarDevice.generate behind a dependency cache. ``mesh_map(kind)``
    returns CPU-decoded baked pixels uploaded as R32F or RGBA32F, without color
    conversion or hardware resampling. The result belongs to the caller.
    """

    def __init__(self):
        import bpy
        import gpu
        from gpu_extras.batch import batch_for_shader
        if bpy.app.background or not bpy.context.window:
            raise RuntimeError('GPU masks require a Blender window')
        if gpu.platform.backend_type_get() not in QUALIFIED_BACKENDS:
            raise UnsupportedMask('GPU mask arithmetic is validated on Metal, Vulkan and OpenGL; use CPU')
        self.window = bpy.context.window.as_pointer()
        info = gpu.types.GPUShaderCreateInfo()
        info.vertex_in(0, 'VEC2', 'position')
        info.fragment_out(0, 'FLOAT', 'fragColor')
        for slot, name in enumerate(('previous_mask', 'breakup_field', 'chip_field',
                                      'ao_map', 'curvature_map')):
            info.sampler(slot, 'FLOAT_2D', name)
        info.push_constant('IVEC4', 'control')
        info.push_constant('VEC4', 'generator')
        info.push_constant('VEC4', 'levels')
        info.vertex_source('void main(){gl_Position=vec4(position,0.0,1.0);}')
        info.fragment_source(FRAGMENT)
        self.shader = gpu.shader.create_from_info(info)
        self.batch = batch_for_shader(self.shader, 'TRIS', {
            'position': ((-1,-1),(3,-1),(-1,3))})
        self.white = gpu.types.GPUTexture((1,1), format='R32F',
            data=gpu.types.Buffer('FLOAT',1,[1.]))
        metrics.count('live_gpu_mask_shader_compilations')
        metrics.count('live_gpu_allocations')
        metrics.count('live_gpu_allocated_bytes',4)
        metrics.count('live_gpu_uploads')
        metrics.count('live_gpu_upload_bytes',4)

    @contextmanager
    def _state(self):
        import bpy
        import gpu
        if (self.shader is None or not bpy.context.window
                or bpy.context.window.as_pointer() != self.window):
            raise RuntimeError('GPU mask window was closed or changed')
        viewport, scissor = gpu.state.viewport_get(), gpu.state.scissor_get()
        blend, depth, depth_mask = gpu.state.blend_get(), gpu.state.depth_test_get(), gpu.state.depth_mask_get()
        try:
            gpu.state.blend_set('NONE')
            gpu.state.depth_test_set('NONE')
            gpu.state.depth_mask_set(False)
            yield
        finally:
            gpu.shader.unbind()
            gpu.state.viewport_set(*viewport)
            gpu.state.scissor_set(*scissor)
            gpu.state.blend_set(blend)
            gpu.state.depth_test_set(depth)
            gpu.state.depth_mask_set(depth_mask)

    @metrics.measured('live_gpu_mask_generation')
    def generate(self, ts, layer, n, *, field, mesh_map):
        import gpu
        masks, required = validate(ts, layer, n)
        maps = {kind: mesh_map(kind) for kind in required}
        if any(texture.format not in {'R32F','RGBA32F'} for texture in maps.values()):
            raise UnsupportedMask('Baked GPU masks require full float32 map textures')
        with self._state():
            targets = [gpu.types.GPUTexture((n,n), format='R32F')
                       for _ in range(min(2,len(masks)))]
            metrics.count('live_gpu_allocations',len(targets))
            metrics.count('live_gpu_allocated_bytes',len(targets)*n*n*4)
            previous = self.white
            for index, mask in enumerate(masks):
                fields = {kind: field(kind, n, scale=mask.noise_scale, seed=mask.seed)
                          for kind in _fields(mask)}
                if any(texture.format != 'R32F' or (texture.width,texture.height)!=(n,n)
                       for texture in fields.values()):
                    raise UnsupportedMask('Mask scalar inputs must be full-size R32F')
                target = targets[index%len(targets)]
                framebuffer = gpu.types.GPUFrameBuffer(color_slots=target)
                with framebuffer.bind():
                    gpu.state.viewport_set(0,0,n,n)
                    gpu.state.scissor_set(0,0,n,n)
                    self.shader.bind()
                    self.shader.uniform_sampler('previous_mask',previous)
                    self.shader.uniform_sampler('breakup_field', fields[
                        'SCRATCHES' if mask.generator=='SCRATCHES' else 'FBM'])
                    self.shader.uniform_sampler('chip_field',fields.get('VORONOI',self.white))
                    self.shader.uniform_sampler('ao_map',maps.get('AO',self.white))
                    self.shader.uniform_sampler('curvature_map',maps.get('CURVATURE',self.white))
                    operation = -1 if index==0 else {'NORMAL':0,'MULTIPLY':1,'ADD':2}[mask.operation]
                    self.shader.uniform_int('control',(KINDS[mask.generator],operation,int(mask.invert),n))
                    self.shader.uniform_float('generator',(mask.amount,mask.contrast,
                        mask.noise_strength,float('AO' in maps)))
                    self.shader.uniform_float('levels',(mask.black,max(1e-6,mask.white-mask.black),mask.strength,0.))
                    self.batch.draw(self.shader)
                metrics.count('live_gpu_passes')
                metrics.count('live_gpu_mask_operations')
                previous = target
            metrics.count('live_gpu_mask_stacks')
            return previous

    def close(self):
        import gpu
        if self.shader is not None:
            gpu.shader.unbind()
        self.shader = self.batch = self.white = None
