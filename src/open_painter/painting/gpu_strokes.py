"""GPU material strokes; CPU topology chooses eligible faces, shaders rasterize dabs.

The UV surface raster is immutable for a gesture. Ambiguous overlapping surfaces
raise UnsupportedSurface before a preview is bound and use the CPU implementation.
"""
from types import SimpleNamespace
from weakref import WeakSet

import numpy as np

from ..evaluation.engine import runtime
from ..evaluation.engine.resources import TextureResource
from ..geometry.triangle_bounds import TriangleBounds
from . import stroke_raster as raster

_canvases = WeakSet()


def active_bytes(set_id):
    """Owned gesture resources; borrowed retained inputs are counted by their owner."""
    textures, surfaces = {}, {}
    for canvas in _canvases:
        if canvas.set_id != set_id:
            continue
        owned = [t for channel, t in canvas.bases.items() if channel not in canvas.borrowed]
        owned.extend(r.texture for r in canvas.outputs.values())
        owned.extend((canvas.coverage, canvas.scratch, canvas.alpha, canvas.depth_eligible))
        if canvas.owns_surface or canvas.surface.pending_close:
            owned.extend((canvas.surface.position, canvas.surface.normal))
            surfaces[id(canvas.surface)] = canvas.surface
        textures.update((id(t), t) for t in owned if t is not None)
    leases = runtime.device()._leases
    return sum(leases.get(identifier, 0) for identifier in textures) + sum(s.bounds_bytes for s in surfaces.values())


class UnsupportedSurface(ValueError):
    pass


REDUCE_BASE = r'''
void main() {
  ivec2 p = ivec2(gl_FragCoord.xy) * op_factor;
  op_out0 = texelFetch(op_source, p, 0);
}
'''


STAMP = r'''
void main() {
  ivec2 p = ivec2(gl_FragCoord.xy);
  vec4 world = texelFetch(op_position, p, 0);
  float old = texelFetch(op_before, p, 0).r;
  float a = 0.0;
  int triangle = int(world.a) - 1;
  if (triangle >= 0) {
    int width = textureSize(op_eligible, 0).x;
    float eligible = texelFetch(op_eligible, ivec2(triangle % width, triangle / width), 0).r;
    vec3 rel = world.xyz - op_params.p[0].xyz;
    float radius = max(op_params.p[0].w, 1e-8);
    vec3 axis = op_params.p[1].xyz;
    float depth = dot(rel, axis);
    vec3 lateral = rel - depth * axis;
    float radial = length(lateral) / radius;
    vec3 normal = texelFetch(op_normal, p, 0).xyz;
    if (eligible > 0.5 && all(lessThanEqual(abs(rel), vec3(radius))) &&
        dot(normal, axis) > 0.05 && radial < 1.0 && abs(depth) < radius * op_params.p[3].w) {
      a = clamp((1.0 - radial) / max(1.0 - op_params.p[2].w, 1e-5), 0.0, 1.0) * op_params.p[1].w;
      vec3 tangent = op_params.p[2].xyz;
      vec3 bitangent = op_params.p[3].xyz;
      vec2 uv = vec2(dot(lateral, tangent), dot(lateral, bitangent)) / radius * 0.5 + 0.5;
      ivec2 size = textureSize(op_alpha, 0);
      a *= texelFetch(op_alpha, clamp(ivec2(uv * vec2(size)), ivec2(0), size - 1), 0).r;
    }
  }
  float result = op_flow == 1 ? 1.0 - (1.0 - old) * (1.0 - a) : max(old, a);
  op_out0 = vec4(result);
}
'''

COMPOSITE = r'''
vec4 donor(vec2 uv) {
  ivec2 size = textureSize(op_base, 0);
  vec2 at = fract(uv) * vec2(size) - 0.5;
  ivec2 lo = ivec2(floor(at)); vec2 f = fract(at);
  ivec2 a = (lo + size) % size, b = (lo + 1 + size) % size;
  return mix(mix(texelFetch(op_base, a, 0), texelFetch(op_base, ivec2(b.x, a.y), 0), f.x),
             mix(texelFetch(op_base, ivec2(a.x, b.y), 0), texelFetch(op_base, b, 0), f.x), f.y);
}
void main() {
  ivec2 p = ivec2(gl_FragCoord.xy), size = textureSize(op_coverage, 0);
  float a = texelFetch(op_coverage, p, 0).r;
  if (texelFetch(op_position, p, 0).a == 0.0) {
    a = max(a, texelFetch(op_coverage, clamp(p + ivec2(1, 0), ivec2(0), size - 1), 0).r);
    a = max(a, texelFetch(op_coverage, clamp(p - ivec2(1, 0), ivec2(0), size - 1), 0).r);
    a = max(a, texelFetch(op_coverage, clamp(p + ivec2(0, 1), ivec2(0), size - 1), 0).r);
    a = max(a, texelFetch(op_coverage, clamp(p - ivec2(0, 1), ivec2(0), size - 1), 0).r);
  }
  a *= op_params.p[1].x;
  vec4 base = texelFetch(op_base, p, 0);
  if (op_base_scalar == 1) { base = vec4(base.rrr, base.g); }
  if (op_base_premul == 1) { base.rgb = base.a > 1e-8 ? base.rgb / base.a : vec3(0.0); }
  vec4 result;
  if (op_smudge == 1) {
    vec4 source = donor(op_params.p[2].xy);
    if (op_base_scalar == 1) { source = vec4(source.rrr, source.g); }
    if (op_base_premul == 1) { source.rgb = source.a > 1e-8 ? source.rgb / source.a : vec3(0.0); }
    float alpha = mix(base.a, source.a, a);
    vec3 rgb = mix(base.rgb * base.a, source.rgb * source.a, a);
    result = vec4(alpha > 1e-8 ? rgb / alpha : vec3(0.0), alpha);
    if (op_normal == 1 && a > 0.0 && alpha > 1e-8) {
      vec3 vector = result.rgb * 2.0 - 1.0;
      result.rgb = vector / max(length(vector), 1e-8) * 0.5 + 0.5;
    }
  } else if (op_erase == 1) {
    result = vec4(base.rgb, base.a * (1.0 - a));
    if (result.a <= 1e-8) { result.rgb = vec3(0.0); }
  } else {
    float alpha = a + base.a * (1.0 - a);
    vec3 rgb = alpha > 1e-8 ? (op_params.p[0].rgb * a + base.rgb * base.a * (1.0 - a)) / alpha : vec3(0.0);
    result = vec4(rgb, alpha);
  }
  if (op_premul == 1) { result.rgb *= result.a; }
  op_out0 = op_scalar == 1 ? vec4(result.r, result.a, 0.0, 0.0) : result;
}
'''


def program(name, source, samplers, slots, ints):
    dev = runtime.device()
    key = 'stroke_' + name
    if key not in dev._fixed:
        from ..native_bridge.compiler import fixed_source
        source = fixed_source('paint.' + name, source)
        dev._fixed[key] = dev._create(source, samplers, slots, ints=ints)
    return dev._fixed[key]


def uniforms(values):
    import gpu
    values = np.ascontiguousarray(values, np.float32)
    return gpu.types.GPUUniformBuf(gpu.types.Buffer('FLOAT', values.size, values.ravel()))


class Surface:
    def __init__(self, entries, n):
        from ..geometry.uv_rasterizer import rasterize, interpolate
        self.entries, self.n = entries, n
        self.pins, self.pending_close = 0, False
        self.position = self.normal = None
        self.triangle_bounds = []
        self.triangle_indexes = []
        self.sorted_triangle_indices = []
        positions = np.zeros((n, n, 4), np.float32)
        normals = np.zeros_like(positions)
        interiors = np.zeros((n, n), bool)
        self.count = 0
        for entry in entries:
            vertices, loops = entry['tri_vertices'], entry['tri_loops']
            # Geometry is immutable for this surface. Keep only the two bounds
            # per triangle; each dab gathers corners only inside its brush box.
            corners = entry['positions'][vertices]
            self.triangle_bounds.append((corners.min(axis=1), corners.max(axis=1)))
            self.triangle_indexes.append(TriangleBounds(*self.triangle_bounds[-1]))
            indices = np.asarray(entry['tri_indices'], np.int64)
            self.sorted_triangle_indices.append(bool(np.all(indices[1:] > indices[:-1])))
            del corners
            uv = entry['uv'][loops]
            for batch in raster._batches(uv, n):
                rows, cols, tri, weights = rasterize(uv[batch], n, unique=False)
                selected = batch[tri]
                points = interpolate(weights, entry['positions'][vertices[selected]])
                normal = interpolate(weights, entry['world_normals'][loops[selected]])
                index = rows * n + cols
                order = np.argsort(index, kind='stable')
                repeated = index[order[1:]] == index[order[:-1]]
                interior = weights.min(axis=1) > 1e-5
                if np.any((interior[order[1:]] | interior[order[:-1]]) & repeated):
                    raise UnsupportedSurface('Overlapping UV interiors use CPU stroke accumulation')
                if np.any(np.linalg.norm(points[order[1:]][repeated] - points[order[:-1]][repeated], axis=1) > 1e-5):
                    raise UnsupportedSurface('Overlapping UV surfaces use CPU stroke accumulation')
                if np.any(np.linalg.norm(normal[order[1:]][repeated] - normal[order[:-1]][repeated], axis=1) > 1e-5):
                    raise UnsupportedSurface('Overlapping UV normals use CPU stroke accumulation')
                old = positions[rows, cols]
                occupied = old[:, 3] > 0
                if np.any(occupied & (interior | interiors[rows, cols])):
                    raise UnsupportedSurface('Overlapping UV interiors use CPU stroke accumulation')
                if np.any(np.linalg.norm(old[occupied, :3] - points[occupied], axis=1) > 1e-5):
                    raise UnsupportedSurface('Overlapping UV surfaces use CPU stroke accumulation')
                if np.any(np.linalg.norm(normals[rows[occupied], cols[occupied], :3] - normal[occupied], axis=1) > 1e-5):
                    raise UnsupportedSurface('Overlapping UV normals use CPU stroke accumulation')
                positions[rows, cols, :3] = points
                positions[rows, cols, 3] = selected + self.count + 1
                normals[rows, cols, :3] = normal
                interiors[rows, cols] = interior
            self.count += len(vertices)
        if self.count >= 2 ** 24:
            raise UnsupportedSurface('Surface triangle identifiers exceed float precision')
        dev = runtime.device()
        try:
            self.position = dev.upload(positions)
            self.normal = dev.upload(normals)
        except Exception:
            self.close()
            raise

    def eligible(self, sample, settings, anchor):
        pseudo = SimpleNamespace(reach=settings['reach'], depth_limit=settings['depth_limit'], anchors='')
        sheets = raster.reach_for(self.entries, pseudo, sample[None], settings['planar'],
                                  None if anchor is None else anchor[None])
        width = min(1024, max(1, self.count))
        values = np.zeros(((max(1, self.count) + width - 1) // width, width), np.float32)
        offset = 0
        windows = []
        lo, hi = raster.bounds(sample[None])
        for entry, sheet, bounds_index, sorted_indices in zip(
                self.entries, sheets, self.triangle_indexes, self.sorted_triangle_indices):
            count = len(entry['tri_vertices'])
            if sheet is None:
                allowed = np.ones(count, bool)
            elif sorted_indices:
                allowed = sheet.mask_sorted(np.asarray(entry['tri_indices'], np.int64), 0, 1)[:, 0]
            else:
                picked = np.arange(count)
                allowed = raster.eligibility(sheet, entry, picked)(picked, 0, 1)[:, 0]
            values.ravel()[offset:offset + count] = allowed
            candidates = bounds_index.query(lo, hi)
            chosen = candidates[allowed[candidates]]
            if len(chosen):
                uv = entry['uv'][entry['tri_loops'][chosen]]
                corners = entry['positions'][entry['tri_vertices'][chosen]]
                windows.append(raster.uv_window(corners, uv, lo, hi, margin=2 / self.n))
            offset += count
        if not windows:
            return values, None
        boxes = np.concatenate(windows)
        low = np.clip(np.floor(boxes[:, 0].min(axis=0) * self.n).astype(int), 0, self.n)
        high = np.clip(np.ceil(boxes[:, 1].max(axis=0) * self.n).astype(int), 0, self.n)
        return values, tuple(map(int, (*low, *(high - low)))) if np.all(high > low) else None

    @property
    def bounds_bytes(self):
        indexes = {id(index): index for index in self.triangle_indexes}
        for entry in self.entries:
            index = entry.get('_reach', {}).get('bounds_index')
            if index is not None:
                indexes[id(index)] = index
        return sum(array.nbytes for bounds in self.triangle_bounds for array in bounds) + sum(
            index.extra_bytes for index in indexes.values())

    def close(self):
        if self.pins:
            self.pending_close = True
            return
        for name in ('position', 'normal'):
            texture = getattr(self, name, None)
            if texture is not None:
                runtime.device().discard(texture)
                setattr(self, name, None)
        self.triangle_bounds.clear()
        self.triangle_indexes.clear()
        self.sorted_triangle_indices.clear()
        self.entries = ()


class Canvas:
    def __init__(self, entries, n, bases, premultiplied, surface=None, precise=False, set_id=None):
        from ..evaluation.engine.formats import SCALAR_CHANNELS
        if runtime.device().max_samplers < 5:
            raise UnsupportedSurface('The GPU stroke pass needs five texture bindings')
        self.n, self.premultiplied = n, premultiplied
        self.set_id, self.closed = set_id, False
        self.owns_surface = surface is None
        self.surface = surface or Surface(entries, n)
        self.surface.pins += 1
        self.bases, self.outputs = {}, {}
        self.borrowed, self.base_scalar = set(), set()
        self.coverage = self.scratch = None
        self.alpha, self.alpha_key = None, None
        self.depth_eligible = None
        self.region = None
        self.total_region = None
        self.published_region = None
        self.base_premul = set()
        _canvases.add(self)
        dev = runtime.device()
        texture = None
        try:
            self.coverage = dev.texture(n, 'R32F')
            self.scratch = dev.texture(n, 'R32F')
            dev.fill(self.coverage, n, (0, 0, 0, 0))
            for channel, base in bases.items():
                scalar = channel in SCALAR_CHANNELS
                fmt = ('RG32F' if precise or channel == 'HEIGHT' else 'RG16F') if scalar else ('RGBA32F' if precise else 'RGBA16F')
                if isinstance(base, TextureResource):
                    self.bases[channel] = base.texture
                    self.borrowed.add(channel)
                    if base.dimensions != (n, n):
                        if base.dimensions[0] != base.dimensions[1] or base.dimensions[0] % n:
                            raise UnsupportedSurface('GPU stroke preview requires an integral texture reduction')
                        reduced = dev.texture(n, base.format)
                        self.bases[channel] = reduced
                        self.borrowed.discard(channel)
                        with dev.state():
                            dev.draw(program('reduce_base', REDUCE_BASE, ['op_source'], 0, ('op_factor',)),
                                     [reduced], n, [('op_source', base.texture)],
                                     ints={'op_factor': base.dimensions[0] // n})
                    if base.mapping == 'rg':
                        self.base_scalar.add(channel)
                    texture = dev.texture(n, fmt)
                    with dev.state():
                        dev.draw(dev.fixed('copy'), [texture], n, [('op_source', self.bases[channel])])
                else:
                    self.bases[channel] = dev.upload(base)
                    initial = base.copy()
                    if premultiplied[channel]:
                        initial[..., :3] *= initial[..., 3:4]
                    if scalar:
                        initial = np.ascontiguousarray(initial[..., [0, 3]])
                    texture = dev.upload(initial, fmt)
                self.outputs[channel] = TextureResource(('gesture', id(self), channel), texture, fmt, 'rg' if scalar else 'rgba')
                texture = None   # self.outputs owns the successfully initialized target
        except Exception:
            if texture is not None:
                dev.discard(texture)   # an initial copy can fail before self.outputs takes ownership
            self.close()
            raise

    def stamp(self, samples, settings, anchors=None):
        dev = runtime.device()
        if settings['blend'] == 'SMUDGE' and not settings.get('_dab'):
            from .payload import donor_uv
            dabs = settings.get('dabs', [{} for _ in samples])
            changed = None
            for index, (sample, dab) in enumerate(zip(samples, dabs)):
                uv = donor_uv(self.surface.entries, dab.get('donor', sample[:3]), sample[3:6])
                if uv is None:
                    continue
                one = dict(settings, _dab=True, smudge_uv=uv, accumulate='MAX')
                if 'planar' in dab:
                    one['planar'] = dab['planar']
                with dev.state():
                    dev.fill(self.coverage, self.n, (0, 0, 0, 0))
                self.stamp(sample[None], one, anchors[index:index + 1] if anchors is not None else None)
                if self.region is None:
                    continue
                changed = union_rect(changed, self.region)
                self.publish(one)
                self._advance_base()
            self.region = self.published_region = union_rect(self.published_region, changed) if changed else self.published_region
            return
        if settings['blend'] not in {'PAINT', 'ERASE', 'SMUDGE'}:
            raise ValueError('GPU stroke mode is not supported')
        alpha = settings['alpha_texture']
        if self.alpha is None or self.alpha_key is not alpha:
            if self.alpha is not None:
                dev.discard(self.alpha)
            self.alpha = dev.upload(np.ones((1, 1), np.float32) if alpha is None else alpha, 'R32F')
            self.alpha_key = alpha
        from ..native_bridge import paint
        lib = paint.cpu_library()
        batch = paint.uniforms(lib, samples, settings) if lib is not None else None
        shader = program('stamp', STAMP, ['op_position', 'op_normal', 'op_before', 'op_eligible', 'op_alpha'], 4, ('op_flow',))
        with dev.state():
            for index, sample in enumerate(samples):
                allowed, rect = self.surface.eligible(sample, settings, anchors[index] if anchors is not None else None)
                if rect is None:
                    continue
                # DEPTH admits every triangle on this immutable surface. Reuse
                # its texture throughout the gesture instead of uploading and
                # retiring the same mask for every dab. SURFACE varies per hit.
                depth = settings['reach'] != 'SURFACE'
                if depth:
                    if self.depth_eligible is None:
                        self.depth_eligible = dev.upload(allowed, 'R32F')
                    eligible = self.depth_eligible
                else:
                    eligible = dev.upload(allowed, 'R32F')
                try:
                    if batch is not None:
                        params = uniforms(batch[index])
                    else:
                        axis = sample[3:6] if settings['planar'] is None else np.asarray(settings['planar'], np.float32)
                        axis = axis / max(float(np.linalg.norm(axis)), 1e-8)
                        tangent = np.array(sample[10:13], copy=True)
                        if settings['alpha_rotation'] != 'RAKE' or np.linalg.norm(tangent) < 1e-8:
                            tangent = np.cross(sample[3:6], (0, 0, 1) if abs(sample[5]) < .9 else (1, 0, 0))
                        tangent /= max(float(np.linalg.norm(tangent)), 1e-8)
                        angle = settings['alpha_angle']
                        tangent = tangent * np.cos(angle) + np.cross(sample[3:6], tangent) * np.sin(angle)
                        bitangent = np.cross(sample[3:6], tangent)
                        params = uniforms([(*sample[:3], sample[7]), (*axis, sample[6] * settings['flow']),
                                           (*tangent, settings['hardness']), (*bitangent, settings['depth_limit'])])
                    samplers = [('op_position', self.surface.position), ('op_normal', self.surface.normal),
                                ('op_before', self.coverage), ('op_eligible', eligible), ('op_alpha', self.alpha)]
                    dev.draw(shader, [self.scratch], self.n, samplers, params,
                             ints={'op_flow': int(settings['accumulate'] == 'FLOW')}, region=rect)
                    dev.draw(dev.fixed('copy'), [self.coverage], self.n, [('op_source', self.scratch)], region=rect)
                    self.region = union_rect(self.region, rect)
                    self.total_region = union_rect(self.total_region, rect)
                finally:
                    if not depth:
                        dev.discard(eligible)

    def publish(self, settings):
        if self.published_region is not None and not settings.get('_dab'):
            self.region = self.published_region = None
            return True
        if self.region is None:
            return False
        from .strokes import channel_value
        dev = runtime.device()
        shader = program('composite', COMPOSITE, ['op_coverage', 'op_position', 'op_base'], 3,
                         ('op_erase', 'op_premul', 'op_scalar', 'op_base_scalar', 'op_base_premul', 'op_smudge', 'op_normal'))
        x, y, w, h = self.region
        low = max(0, x - 1), max(0, y - 1)
        region = (*low, min(self.n, x + w + 1) - low[0], min(self.n, y + h + 1) - low[1])
        with dev.state():
            for channel, resource in self.outputs.items():
                params = uniforms([(*channel_value(settings['values'], channel)[:3], 1), (settings['opacity'], 0, 0, 0),
                                   (*settings.get('smudge_uv', (0, 0)), 0, 0)])
                dev.draw(shader, [resource.texture], self.n,
                         [('op_coverage', self.coverage), ('op_position', self.surface.position), ('op_base', self.bases[channel])],
                         params, ints={'op_erase': int(settings['blend'] == 'ERASE'),
                                       'op_premul': int(self.premultiplied[channel]), 'op_scalar': int(resource.mapping == 'rg'),
                                       'op_base_scalar': int(channel in self.base_scalar),
                                       'op_base_premul': int(channel in self.base_premul),
                                       'op_smudge': int(settings['blend'] == 'SMUDGE'), 'op_normal': int(channel == 'NORMAL')}, region=region)
                resource.touch()
        self.region = None
        return True

    def _advance_base(self):
        """Snapshot each completed dab before the next reads it; never read a draw target."""
        dev = runtime.device()
        with dev.state():
            for channel, resource in self.outputs.items():
                if channel in self.borrowed or self.bases[channel].format != resource.format:
                    if channel not in self.borrowed:
                        dev.discard(self.bases[channel])
                    self.bases[channel] = dev.texture(self.n, resource.format)
                    self.borrowed.discard(channel)
                dev.draw(dev.fixed('copy'), [self.bases[channel]], self.n, [('op_source', resource.texture)])
                if resource.mapping == 'rg':
                    self.base_scalar.add(channel)
                if self.premultiplied[channel]:
                    self.base_premul.add(channel)

    def close(self):
        if self.closed:
            return
        self.closed = True
        _canvases.discard(self)
        dev = runtime.device()
        self.surface.pins = max(0, self.surface.pins - 1)
        if self.owns_surface or (not self.surface.pins and self.surface.pending_close):
            self.surface.close()
        for texture in (*(t for c, t in self.bases.items() if c not in self.borrowed),
                         *(r.texture for r in self.outputs.values()), self.alpha, self.depth_eligible):
            if texture is not None:
                dev.discard(texture)
        for texture in (self.coverage, self.scratch):
            if texture is not None:
                dev.release(texture, self.n, 'R32F')
        self.bases.clear()
        self.outputs.clear()
        self.coverage = self.scratch = self.alpha = self.depth_eligible = None


def union_rect(a, b):
    if a is None:
        return b
    x, y = min(a[0], b[0]), min(a[1], b[1])
    return x, y, max(a[0] + a[2], b[0] + b[2]) - x, max(a[1] + a[3], b[1] + b[3]) - y
