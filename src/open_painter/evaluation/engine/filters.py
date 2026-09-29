"""Neighborhood passes over canonical straight RGBA textures.

The CPU filter API remains the reference. Radius/strength edits change uniforms
and small coefficient tables, never shader source or full-size CPU pixel arrays.
"""
import numpy as np

from . import runtime
from ..operation_contracts import SURFACE_KINDS

KINDS = frozenset('''BLUR GAUSSIAN_BLUR HIGH_PASS SHARPEN SMOOTH_MASK DILATE ERODE
MORPHOLOGICAL_EXPAND MORPHOLOGICAL_CONTRACT EDGE_DETECT HEIGHT_TO_NORMAL
DIRECTIONAL_BLUR RADIAL_BLUR SLOPE_BLUR WARP DIRECTIONAL_WARP VECTOR_WARP
UV_DISTORTION TWIRL RIPPLE WAVE NON_UNIFORM_TRANSFORM'''.split())
FIELDS = ('kind', 'radius', 'amount', 'angle', 'samples', 'mode', 'noise', 'scale', 'seed',
          'transform_scale', 'offset', 'space', 'direction')


def signature(effect):
    return tuple((field, tuple(value) if hasattr(value, '__len__') and not isinstance(value, str) else value)
                 for field in FIELDS if (value := getattr(effect, field, None)) is not None)


def supported(effect):
    from .procedural_glsl import supported as field_supported
    if effect.kind not in KINDS:
        return False
    if runtime.device().max_samplers < 4:
        return False
    return effect.kind not in {'SLOPE_BLUR', 'WARP', 'DIRECTIONAL_WARP', 'VECTOR_WARP', 'UV_DISTORTION'} or field_supported(effect.noise)


FETCH = r'''
vec4 read_pixel(sampler2D tex, ivec2 p) {
  return texelFetch(tex, clamp(p, ivec2(0), ivec2(op_params.p[3].xy) - 1), 0);
}
vec4 sample_pixel(sampler2D tex, vec2 p) {
  p = clamp(p, vec2(0.0), op_params.p[3].xy - 1.0);
  ivec2 lo = ivec2(floor(p)); vec2 f = fract(p);
  return mix(mix(read_pixel(tex, lo), read_pixel(tex, lo + ivec2(1, 0)), f.x),
             mix(read_pixel(tex, lo + ivec2(0, 1)), read_pixel(tex, lo + ivec2(1, 1)), f.x), f.y);
}
float luma(vec3 v) { return dot(v, vec3(0.2126, 0.7152, 0.0722)); }
vec2 gradient_field(ivec2 p) {
  return vec2(read_pixel(op_field, p + ivec2(1, 0)).r - read_pixel(op_field, p - ivec2(1, 0)).r,
              read_pixel(op_field, p + ivec2(0, 1)).r - read_pixel(op_field, p - ivec2(0, 1)).r) * 0.5;
}
'''

AXIS = FETCH + r'''
void main() {
  ivec2 p = ivec2(gl_FragCoord.xy), axis = op_axis == 0 ? ivec2(1, 0) : ivec2(0, 1);
  vec4 value = vec4(0.0);
  for (int i = -op_half; i <= op_half; ++i) {
    value += read_pixel(op_source, p + axis * i) * texelFetch(op_weights, ivec2(i + op_half, 0), 0).r;
  }
  op_out0 = value;
}
'''

MORPH = FETCH + r'''
void main() {
  ivec2 p = ivec2(gl_FragCoord.xy), axis = op_axis == 0 ? ivec2(op_half, 0) : ivec2(0, op_half);
  vec4 a = read_pixel(op_source, p), b = read_pixel(op_source, p - axis), c = read_pixel(op_source, p + axis);
  op_out0 = op_max == 1 ? max(a, max(b, c)) : min(a, min(b, c));
}
'''

BODIES = {
    'GAUSSIAN_BLUR': 'value = read_pixel(op_source, p).rgb;',
    'BLUR': 'value = read_pixel(op_source, p).rgb;',
    'HIGH_PASS': 'value = original.rgb - read_pixel(op_source, p).rgb + 0.5;',
    'SHARPEN': 'value = original.rgb + (original.rgb - read_pixel(op_source, p).rgb) * amount;',
    'SMOOTH_MASK': 'vec3 t = clamp((read_pixel(op_source, p).rgb - 0.25) * 2.0, 0.0, 1.0); value = t * t * (3.0 - 2.0 * t);',
    'EDGE_DETECT': r'''
      vec2 g = vec2(0.0);
      for (int i = -1; i <= 1; ++i) {
        float weight = i == 0 ? 0.5 : 0.25;
        g.x += (luma(read_pixel(op_source, p + ivec2(1, i)).rgb) - luma(read_pixel(op_source, p + ivec2(-1, i)).rgb)) * weight;
        g.y += (luma(read_pixel(op_source, p + ivec2(i, 1)).rgb) - luma(read_pixel(op_source, p + ivec2(i, -1)).rgb)) * weight;
      }
      value = vec3(length(g) * amount);
    ''',
    'HEIGHT_TO_NORMAL': r'''
      float dx = (luma(read_pixel(op_source, p + ivec2(1, 0)).rgb) - luma(read_pixel(op_source, p - ivec2(1, 0)).rgb)) * 0.5;
      float dy = (luma(read_pixel(op_source, p + ivec2(0, 1)).rgb) - luma(read_pixel(op_source, p - ivec2(0, 1)).rgb)) * 0.5;
      value = normalize(vec3(-dx * amount, -dy * amount, 1.0)) * 0.5 + 0.5;
    ''',
    'DIRECTIONAL_BLUR': r'''
      for (int i = 0; i < count; ++i) {
        float step = -1.0 + 2.0 * float(i) / float(count - 1);
        value += sample_pixel(op_source, vec2(p) + direction * radius * step).rgb / float(count);
      }
    ''',
    'RADIAL_BLUR': r'''
      vec2 q = vec2(p) - center;
      for (int i = 0; i < count; ++i) {
        float step = -1.0 + 2.0 * float(i) / float(count - 1);
        float theta = radians(radius * step), c = cos(theta), s = sin(theta);
        value += sample_pixel(op_source, center + vec2(q.x * c - q.y * s, q.x * s + q.y * c)).rgb / float(count);
      }
    ''',
    'WARP': 'value = sample_pixel(op_source, vec2(p) + gradient_field(p) * radius * amount).rgb;',
    'DIRECTIONAL_WARP': 'value = sample_pixel(op_source, vec2(p) + direction * (read_pixel(op_field, p).r * 2.0 - 1.0) * radius * amount).rgb;',
    'VECTOR_WARP': 'value = sample_pixel(op_source, vec2(p) + (read_pixel(op_field, p).rg * 2.0 - 1.0) * radius * amount).rgb;',
    'SLOPE_BLUR': r'''
      vec3 old = read_pixel(op_source, p).rgb;
      vec3 moved = sample_pixel(op_source, vec2(p) + gradient_field(p) * radius * amount / op_params.p[2].x).rgb;
      value = op_mode == 1 ? min(old, moved) : op_mode == 2 ? max(old, moved) : (old + moved) * 0.5;
    ''',
    'TWIRL': r'''
      vec2 q = vec2(p) - center;
      float t = 1.0 - clamp(length(q) / max(radius, 1e-6), 0.0, 1.0);
      float theta = t * t * angle, c = cos(theta), s = sin(theta);
      value = sample_pixel(op_source, center + vec2(q.x * c - q.y * s, q.x * s + q.y * c)).rgb;
    ''',
    'RIPPLE': r'''
      vec2 q = vec2(p) - center;
      float dist = length(q), shift = sin(dist / max(radius, 1e-6) * 6.28318530718) * radius * amount;
      value = sample_pixel(op_source, center + q + q / max(dist, 1e-6) * shift).rgb;
    ''',
    'WAVE': r'''
      vec2 q = vec2(p) - center;
      float shift = sin(dot(q, direction) / max(radius, 1e-6) * 6.28318530718) * radius * amount;
      value = sample_pixel(op_source, vec2(p) + vec2(-direction.y, direction.x) * shift).rgb;
    ''',
    'NON_UNIFORM_TRANSFORM': r'''
      vec2 q = vec2(p) - center;
      vec2 rotated = vec2(q.x * cos(angle) + q.y * sin(angle), -q.x * sin(angle) + q.y * cos(angle));
      value = sample_pixel(op_source, center + rotated / max(abs(op_params.p[1].xy), vec2(1e-6)) - op_params.p[1].zw).rgb;
    ''',
}
BODIES['UV_DISTORTION'] = BODIES['VECTOR_WARP']
for _kind in ('DILATE', 'ERODE', 'MORPHOLOGICAL_EXPAND', 'MORPHOLOGICAL_CONTRACT'):
    BODIES[_kind] = BODIES['BLUR']


def program(name, source, samplers, ints=()):
    dev = runtime.device()
    key = 'filter_' + name
    if key not in dev._fixed:
        if not name.startswith('field_'):
            from ...native_bridge.compiler import fixed_source
            source = fixed_source('filter.' + name, source)
        dev._fixed[key] = dev._create(source, samplers, parameter_slots=4, ints=ints)
    return dev._fixed[key]


def uniforms(effect, width, height):
    import gpu
    values = np.asarray([(effect.radius, effect.amount, effect.angle, min(max(effect.samples, 2), 64)),
                         (*effect.transform_scale, *effect.offset),
                         (min(max(effect.samples, 1), 32), 0, 0, 0), (width, height, 0, 0)], np.float32)
    return gpu.types.GPUUniformBuf(gpu.types.Buffer('FLOAT', values.size, values.ravel()))


def field_texture(effect, n, origin=(0, 0), global_size=None):
    from .procedural_glsl import canonical, function_name, library
    from ...painting.gpu_strokes import uniforms as raw_uniforms
    kind = canonical(effect.noise)
    from ...native_bridge.compiler import field_source
    code = field_source(kind)
    if code is None:
        fn = function_name(kind)
        code = library((kind,)) + r'''
    void main() {
      vec2 uv = (gl_FragCoord.xy + op_params.p[1].xy) / op_params.p[1].z;
      uint seed = uint(op_params.p[0].y) * 65536u + uint(op_params.p[0].z);
    ''' + f'  op_out0 = vec4({fn}(uv, op_params.p[0].x, seed), {fn}(uv, op_params.p[0].x, seed + 37u), 0.0, 1.0);\n}}'
    shader = program('field_' + kind, code, [])
    values = [(effect.scale, (effect.seed >> 16) & 65535, effect.seed & 65535, 0),
              (*origin, global_size or n, 0), (0, 0, 0, 0), (n, n, 0, 0)]
    texture = runtime.device().texture(n, 'RGBA32F')
    try:
        runtime.device().draw(shader, [texture], n, ubo=raw_uniforms(values))
    except Exception:
        runtime.device().release(texture, n, 'RGBA32F')
        raise
    return texture


def _apply_uv(source, effect, n, extent=None, origin=(0, 0), global_size=None, direction=None):
    """New owned RGBA32F result; callers release it through Device.release."""
    dev = runtime.device()
    kind = effect.kind
    buffers, field, weights = [], None, None
    params = uniforms(effect, *(extent or (n, n)))
    before = source
    try:
        # Two reusable intermediates bound peak scratch even for iterative filters.
        for _ in range(2):
            buffers.append(dev.texture(n, 'RGBA32F'))
        def draw(shader, samplers, ints=None):
            nonlocal before
            target = buffers[1] if before is buffers[0] else buffers[0]
            dev.draw(shader, [target], n, [('op_source', before), *samplers], params, ints=ints)
            before = target
        from ...native_bridge.lifecycle import active_library
        lib = active_library()
        native_repeats = None
        if lib is not None:
            from ...native_bridge.filters import plan
            coefficients, passes, native_repeats, _ = plan(lib, effect)
            if len(coefficients):
                weights = dev.upload(coefficients[None, :], 'R32F')
            for operation, axis, distance, maximum in passes:
                if operation == 0:
                    shader = program('axis', AXIS, ['op_source', 'op_weights', 'op_field'], ('op_axis', 'op_half'))
                    draw(shader, [('op_weights', weights), ('op_field', source)], {'op_axis': axis, 'op_half': distance})
                else:
                    shader = program('morph', MORPH, ['op_source', 'op_field'], ('op_axis', 'op_half', 'op_max'))
                    draw(shader, [('op_field', source)], {'op_axis': axis, 'op_half': distance, 'op_max': maximum})
        else:
            gaussian = kind in {'GAUSSIAN_BLUR', 'HIGH_PASS', 'SHARPEN', 'SMOOTH_MASK'}
            if gaussian or kind == 'BLUR':
                radius = max(float(effect.radius), 0)
                half = min(int(np.ceil(radius * (3 if gaussian else 1))), 192)
                x = np.arange(-half, half + 1, dtype=np.float32)
                kernel = (np.exp(-(x * x) / (2 * radius * radius)) if gaussian and radius >= 1e-5 else np.ones_like(x))
                kernel /= kernel.sum()
                weights = dev.upload(kernel[None, :], 'R32F')
                shader = program('axis', AXIS, ['op_source', 'op_weights', 'op_field'], ('op_axis', 'op_half'))
                for axis in (0, 1):
                    draw(shader, [('op_weights', weights), ('op_field', source)], {'op_axis': axis, 'op_half': half})
            elif kind in {'DILATE', 'ERODE', 'MORPHOLOGICAL_EXPAND', 'MORPHOLOGICAL_CONTRACT'}:
                shader = program('morph', MORPH, ['op_source', 'op_field'], ('op_axis', 'op_half', 'op_max'))
                for axis in (0, 1):
                    remaining, covered = min(max(int(np.ceil(effect.radius)), 0), 192), 0
                    while remaining:
                        distance = min(remaining, covered * 2 + 1)
                        draw(shader, [('op_field', source)], {'op_axis': axis, 'op_half': distance,
                             'op_max': int(kind in {'DILATE', 'MORPHOLOGICAL_EXPAND'})})
                        remaining -= distance
                        covered += distance
        if kind in {'WARP', 'DIRECTIONAL_WARP', 'VECTOR_WARP', 'UV_DISTORTION', 'SLOPE_BLUR'}:
            field = field_texture(effect, n, origin, global_size)
        body = FETCH + r'''
void main() {
  ivec2 p = ivec2(gl_FragCoord.xy);
  vec4 original = read_pixel(op_original, p);
  float radius = max(op_params.p[0].x, 0.0), amount = op_params.p[0].y, angle = op_params.p[0].z;
  int count = int(op_params.p[0].w);
  vec2 direction = vec2(cos(angle), sin(angle)), center = (op_params.p[3].xy - 1.0) * 0.5;
  if (op_surface_direction == 1) { direction = read_pixel(op_direction, p).rg; }
  vec3 value = vec3(0.0);
''' + BODIES[kind] + '\n  op_out0 = vec4(clamp(value, 0.0, 1.0), original.a);\n}\n'
        shader = program(kind, body, ['op_source', 'op_field', 'op_original', 'op_direction'], ('op_mode', 'op_surface_direction'))
        repeats = native_repeats if native_repeats is not None else (min(max(effect.samples, 1), 32) if kind == 'SLOPE_BLUR' else 1)
        for _ in range(repeats):
            draw(shader, [('op_field', field if field is not None else source), ('op_original', source),
                          ('op_direction', direction if direction is not None else source)],
                 {'op_mode': {'MIN': 1, 'MAX': 2}.get(effect.mode, 0), 'op_surface_direction': int(direction is not None)})
        result = before
        buffers.remove(result)
        return result
    finally:
        for texture in buffers:
            dev.release(texture, n, 'RGBA32F')
        if field is not None:
            dev.release(field, n, 'RGBA32F')
        if weights is not None:
            dev.discard(weights)


GATHER = FETCH + r'''
void main() {
  ivec2 p = ivec2(gl_FragCoord.xy);
  vec2 donor = texelFetch(op_map, p, 0).rg;
  op_out0 = donor.x < 0.0 ? read_pixel(op_source, p) : sample_pixel(op_source, donor);
}
'''
MERGE = r'''
void main() {
  ivec2 p = ivec2(gl_FragCoord.xy), local = p - ivec2(op_params.p[0].xy);
  if (texelFetch(op_mask, local, 0).r < 0.5) { discard; }
  op_out0 = vec4(texelFetch(op_source, local, 0).rgb, texelFetch(op_original, p, 0).a);
}
'''


def apply(source, effect, n, ts=None):
    if getattr(effect, 'space', 'UV') != 'SURFACE' or effect.kind not in SURFACE_KINDS or ts is None:
        return _apply_uv(source, effect, n)
    from ..channel_eval import seam_map_for
    from ..operation_contracts import footprint
    from ...painting.gpu_strokes import uniforms as raw_uniforms
    seams = seam_map_for(ts, n, [effect])
    if seams is None:
        return _apply_uv(source, effect, n)
    if not seams.bounds:
        # CPU Surface mode with an empty map uses the filter's own default
        # displacement field, which differs from the ordinary UV noise field.
        from ..channel_eval import apply_effect
        from types import SimpleNamespace
        from ..snapshots import capture
        values = runtime.device().readback(source, n)
        params = capture(effect).replaced(strength=1.)
        pixels = apply_effect(np.asarray(values).reshape(n, n, 4), params, seams)
        return runtime.device().upload(pixels)
    halo = footprint(effect.kind, effect.radius, 'UV')
    halo = int(seams.halo if halo is None else min(max(halo, 2), seams.halo))
    dev = runtime.device()
    gather = program('gather', GATHER, ['op_source', 'op_map', 'op_field'])
    merge = program('merge', MERGE, ['op_source', 'op_original', 'op_mask'])
    scratch, uploads = [], []
    result = None
    try:
        remap = np.full((n, n, 2), -1, np.float32)
        pick = (seams.depth <= halo) & seams._nearest
        remap[seams.rows[pick], seams.cols[pick], 0] = seams.source_x[pick]
        remap[seams.rows[pick], seams.cols[pick], 1] = seams.source_y[pick]
        map_texture = dev.upload(remap, 'RG32F')
        uploads.append(map_texture)
        for _ in range(2):
            scratch.append(dev.texture(n, 'RGBA32F'))
        padded = source
        params = uniforms(effect, n, n)
        for _ in range(3):
            target = scratch[1] if padded is scratch[0] else scratch[0]
            dev.draw(gather, [target], n, [('op_source', padded), ('op_map', map_texture), ('op_field', source)], params)
            padded = target
        result = dev.texture(n, 'RGBA32F')
        dev.draw(dev.fixed('copy'), [result], n, [('op_source', source)])
        direction = seams.uv_direction(tuple(effect.direction)) if effect.kind in {'DIRECTIONAL_BLUR', 'DIRECTIONAL_WARP'} else None
        for island, bounds in seams.bounds.items():
            r0, r1, c0, c1 = bounds
            r0, c0 = max(r0 - halo, 0), max(c0 - halo, 0)
            r1, c1 = min(r1 + halo + 1, n), min(c1 + halo + 1, n)
            width, height = c1 - c0, r1 - r0
            side = max(width, height)
            y, x = np.indices((side, side), dtype=np.float32)
            coords = np.stack((x + c0, y + r0), axis=-1)
            entries = seams.entries(island)
            entries = entries[(seams.depth[entries] <= halo) & (seams.rows[entries] >= r0) & (seams.rows[entries] < r1)
                              & (seams.cols[entries] >= c0) & (seams.cols[entries] < c1)]
            coords[seams.rows[entries] - r0, seams.cols[entries] - c0] = np.stack((seams.source_x[entries], seams.source_y[entries]), axis=-1)
            lookup = mask_tex = crop = filtered = direction_tex = None
            try:
                lookup = dev.upload(coords, 'RG32F')
                mask = np.zeros((side, side), np.float32)
                mask[:height, :width] = seams.island_of[r0:r1, c0:c1] == island
                mask_tex = dev.upload(mask, 'R32F')
                crop = dev.texture(side, 'RGBA32F')
                dev.draw(gather, [crop], side, [('op_source', padded), ('op_map', lookup), ('op_field', source)], params)
                if direction is not None:
                    values = np.zeros((side, side, 2), np.float32)
                    values[:height, :width] = direction[r0:r1, c0:c1]
                    direction_tex = dev.upload(values, 'RG32F')
                filtered = _apply_uv(crop, effect, side, (width, height), (c0, r0), n, direction_tex)
                merge_params = raw_uniforms([(c0, r0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0), (n, n, 0, 0)])
                dev.draw(merge, [result], n, [('op_source', filtered), ('op_original', source), ('op_mask', mask_tex)],
                         merge_params, region=(c0, r0, width, height))
            finally:
                for texture in (lookup, mask_tex, direction_tex):
                    if texture is not None:
                        dev.discard(texture)
                dev.release(crop, side, 'RGBA32F')
                if filtered is not None:
                    dev.release(filtered, side, 'RGBA32F')
        output, result = result, None
        return output
    finally:
        if result is not None:
            dev.release(result, n, 'RGBA32F')
        for texture in scratch:
            dev.release(texture, n, 'RGBA32F')
        for texture in uploads:
            dev.discard(texture)
