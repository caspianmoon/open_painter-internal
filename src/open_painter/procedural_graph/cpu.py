"""The CPU evaluation of a Mask Graph (export, snapshots, the CPU reference).

Each step follows the formula the engine compiles for it (``gpu``), operation
by operation in float32, including Blender's Math semantics the engine ports
(safe division, the compatible power and modulo). Values stay scalars until a
row-varying input reaches them, so constant subgraphs cost nothing.
"""
import numpy as np

from .program import build

F32 = np.float32


def _f(value):
    return np.asarray(value, F32)


def safe_divide(a, b):
    a, b = _f(a), _f(b)
    with np.errstate(divide='ignore', invalid='ignore'):
        return np.where(b != 0, a / np.where(b != 0, b, F32(1)), F32(0)).astype(F32)


def compatible_pow(x, y):
    """``op_compatible_pow``: 1 for a zero exponent, sign-preserving odd powers, 0 for 0."""
    x, y = _f(x), _f(y)
    with np.errstate(all='ignore'):
        magnitude = np.power(np.abs(x), y).astype(F32)
        even = (-y - F32(2) * np.floor(-y / F32(2))) == 0          # GLSL mod(-y, 2.0) == 0
        negative = np.where(even, magnitude, -magnitude).astype(F32)
        result = np.where(x < 0, negative, np.where(x == 0, F32(0), magnitude))
        return np.where(y == 0, F32(1), result).astype(F32)


def power(a, b):
    """``op_power``: Blender Math's Power (negative bases take near-integer exponents only)."""
    a, b = _f(a), _f(b)
    fraction = np.abs(b) - np.floor(np.abs(b))                     # GLSL mod(abs(b), 1.0)
    integral = (fraction > F32(.999)) | (fraction < F32(.001))
    rounded = compatible_pow(a, np.floor(b + F32(.5)))
    return np.where(a >= 0, compatible_pow(a, b), np.where(integral, rounded, F32(0))).astype(F32)


def modulo(a, b):
    """``op_compatible_mod``: a − trunc(a / b)·b, 0 for b = 0."""
    a, b = _f(a), _f(b)
    with np.errstate(divide='ignore', invalid='ignore'):
        quotient = np.trunc(a / np.where(b != 0, b, F32(1)))
    return np.where(b != 0, a - quotient * b, F32(0)).astype(F32)


def clamp(value):
    return np.minimum(np.maximum(_f(value), F32(0)), F32(1))


def math(operation, a, b):
    a, b = _f(a), _f(b)
    with np.errstate(all='ignore'):
        if operation == 'ADD':
            return a + b
        if operation == 'SUBTRACT':
            return a - b
        if operation == 'MULTIPLY':
            return a * b
        if operation == 'DIVIDE':
            return safe_divide(a, b)
        if operation == 'POWER':
            return power(a, b)
        if operation == 'MINIMUM':
            return np.minimum(a, b)
        if operation == 'MAXIMUM':
            return np.maximum(a, b)
        if operation == 'LESS_THAN':
            return (a < b).astype(F32)
        if operation == 'GREATER_THAN':
            return (a > b).astype(F32)
        if operation == 'MODULO':
            return modulo(a, b)
        if operation == 'SNAP':
            return np.floor(safe_divide(a, b)) * b
        if operation == 'ONE_MINUS':
            return F32(1) - a
        if operation == 'ABSOLUTE':
            return np.abs(a)
        if operation == 'SQRT':
            return np.where(a > 0, np.sqrt(np.maximum(a, F32(0))), F32(0)).astype(F32)
        if operation == 'ROUND':
            return np.floor(a + F32(.5))
        if operation == 'FLOOR':
            return np.floor(a)
        if operation == 'CEIL':
            return np.ceil(a)
        if operation == 'FRACT':
            return a - np.floor(a)
        if operation == 'SINE':
            return np.sin(a)
        if operation == 'COSINE':
            return np.cos(a)
    raise ValueError(f'Unknown Mask Graph operation {operation}')


def mix(blend, factor, a, b):
    f, a, b = _f(factor), _f(a), _f(b)

    def toward(x):
        return a + (x - a) * f

    if blend == 'MIX':
        return toward(b)
    if blend == 'MULTIPLY':
        return toward(a * b)
    if blend == 'SCREEN':
        return toward(F32(1) - (F32(1) - a) * (F32(1) - b))
    if blend == 'OVERLAY':
        low = (a < F32(.5)).astype(F32)
        return toward(low * (F32(2) * a * b) + (F32(1) - low) * (F32(1) - F32(2) * (F32(1) - a) * (F32(1) - b)))
    if blend == 'ADD':
        return a + b * f
    if blend == 'SUBTRACT':
        return a - b * f
    if blend == 'DIFFERENCE':
        return toward(np.abs(a - b))
    if blend == 'LIGHTEN':
        return toward(np.maximum(a, b))
    if blend == 'DARKEN':
        return toward(np.minimum(a, b))
    raise ValueError(f'Unknown Mask Graph blend {blend}')


def ramp(mode, x, stops):
    """``stops``: (position, value) pairs in position order; see ``gpu.ramp`` for the formula."""
    x = _f(x)
    result = _f(stops[0][1])
    for (p0, v0), (p1, v1) in zip(stops, stops[1:]):
        step = _f(v1) - _f(v0)
        if mode == 'CONSTANT':
            weight = F32(1) - (x < _f(p1)).astype(F32)
        else:
            weight = clamp(safe_divide(x - _f(p0), np.maximum(_f(p1) - _f(p0), F32(1e-6))))
            if mode == 'EASE':
                weight = weight * weight * (F32(3) - F32(2) * weight)
        result = result + step * weight
    return result


def levels(value, black, white, gamma, output_black, output_white):
    normalized = clamp(safe_divide(_f(value) - _f(black), _f(white) - _f(black)))
    exponent = safe_divide(F32(1), np.maximum(_f(gamma), F32(1e-5)))
    return _f(output_black) + power(normalized, exponent) * (_f(output_white) - _f(output_black))


def mapping(vector, offset, rotation, scale):
    x = (vector[..., 0] - F32(.5)) * F32(scale[0])
    y = (vector[..., 1] - F32(.5)) * F32(scale[1])
    c, s = np.cos(_f(rotation)), np.sin(_f(rotation))
    u = c * x - s * y
    v = s * x + c * y
    return np.stack(((u + F32(.5)) + F32(offset[0]), (v + F32(.5)) + F32(offset[1]), vector[..., 2]), axis=-1)


def _geometry(ts, n, y0, y1, geometry_cache):
    if 'geometry' not in geometry_cache:
        from ..painting.projection import geometry
        geometry_cache['geometry'] = geometry(ts, n)
    positions, normals = geometry_cache['geometry']
    return positions[y0:y1], normals[y0:y1]


def evaluate(ts, mask, n, y0, y1, cache, geometry_cache, u, v):
    """The mask's graph over rows ``y0:y1`` (``u``/``v``: texel-centre coordinates of those rows)."""
    from ..evaluation.blend_modes import sample_uv
    from ..texture_engine.procedurals import evaluate as procedural
    geometry_cache = geometry_cache if geometry_cache is not None else {}
    program = build(mask.node_group)
    shape = (y1 - y0, n)
    uv = np.stack((u, v, np.zeros_like(u)), axis=-1).astype(F32)
    outputs = {}

    def read(ref):
        if ref[0] == 'link':
            return outputs[(ref[1], ref[2])]
        if ref[0] == 'uv':
            return uv
        return F32(ref[1].default_value)

    for position, step in enumerate(program.steps):
        node, kind, inputs = step.node, step.kind, step.inputs
        if kind == 'coordinates':
            positions, normals = _geometry(ts, n, y0, y1, geometry_cache)
            outputs.update({(position, 'UV'): uv, (position, 'Position'): positions, (position, 'Normal'): normals})
            continue
        if kind == 'value':
            value = F32(node.value)
        elif kind == 'procedural':
            vector = np.broadcast_to(read(inputs['Vector']), (*shape, 3))
            value = procedural(node.kind, np.ascontiguousarray(vector[..., 0], F32),
                               np.ascontiguousarray(vector[..., 1], F32), seed=int(node.seed), scale=float(node.scale))
        elif kind == 'text':
            from ..evaluation.text_source import ensure
            from ..core.image_manager import pixels
            vector = np.broadcast_to(read(inputs['Vector']), (*shape, 3))
            value = sample_uv(pixels(ensure(node.text, n)), vector[..., 0], vector[..., 1])[..., 0]
        elif kind == 'mesh_map':
            from ..evaluation.mask_eval import map_pixels
            value = sample_uv(map_pixels(ts, node.kind, cache), u, v)[..., 0].astype(F32)
        elif kind == 'math':
            b = read(inputs['B']) if 'B' in inputs else F32(0)
            value = math(node.operation, read(inputs['A']), b)
            if node.use_clamp:
                value = clamp(value)
        elif kind == 'mix':
            value = mix(node.blend, read(inputs['Factor']), read(inputs['A']), read(inputs['B']))
            if node.use_clamp:
                value = clamp(value)
        elif kind == 'ramp':
            stops = [(F32(node.stops[i].position), F32(node.stops[i].value)) for i in step.order]
            value = ramp(node.interpolation, read(inputs['Fac']), stops)
        elif kind == 'levels':
            value = levels(read(inputs['Value']), node.black, node.white, node.gamma, node.output_black,
                           node.output_white)
        elif kind == 'mapping':
            vector = np.broadcast_to(read(inputs['Vector']), (*shape, 3)).astype(F32)
            outputs[(position, 'Vector')] = mapping(vector, node.offset, node.rotation, node.scale)
            continue
        else:
            raise ValueError(f'Unknown Mask Graph step {kind}')
        outputs[(position, 'Value')] = value
    return np.ascontiguousarray(np.broadcast_to(_f(read(program.result)), shape), F32)
