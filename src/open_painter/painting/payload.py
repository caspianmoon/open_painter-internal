"""Versioned, resolved per-dab authoring data; no live graph or physics references."""
import json

import numpy as np

VERSION = 1


def encode(dabs):
    validate(dabs, len(dabs))
    return json.dumps({'version': VERSION, 'dabs': dabs}, separators=(',', ':'), allow_nan=False)


def decode(stroke, count):
    text = getattr(stroke, 'dab_payload', '')
    if not text:
        return [{} for _ in range(count)]
    doc = json.loads(text)
    if not isinstance(doc, dict) or doc.get('version') != VERSION:
        raise ValueError('Unsupported stroke payload version')
    dabs = doc.get('dabs')
    validate(dabs, count)
    return dabs


def validate(dabs, count):
    if not isinstance(dabs, list) or len(dabs) != count:
        raise ValueError('Stroke payload must contain one record per sample')
    for dab in dabs:
        if not isinstance(dab, dict) or set(dab) - {'donor', 'values', 'planar'}:
            raise ValueError('Invalid resolved dab settings')
        for name in ('donor', 'planar'):
            if name in dab and (np.shape(dab[name]) != (3,) or not np.isfinite(dab[name]).all()):
                raise ValueError('Invalid resolved dab position or direction')
        if 'values' in dab:
            if not isinstance(dab['values'], dict):
                raise ValueError('Invalid resolved brush values')
            for key, value in dab['values'].items():
                if key not in {'radius', 'flow', 'opacity', 'alpha_angle', 'base_color', 'roughness', 'metallic',
                               'height', 'ao', 'emission', 'opacity_value', 'normal'} or not np.isfinite(value).all():
                    raise ValueError('Invalid resolved material value')
                vector = key in {'base_color', 'emission', 'normal'}
                if (vector and np.shape(value) not in {(3,), (4,)}) or (not vector and np.shape(value) != ()):
                    raise ValueError('Invalid resolved brush value dimensions')


def donor_uv(entries, point, normal):
    """Nearest eligible triangle's barycentric UV, without GPU readback."""
    best, result = float('inf'), None
    point, normal = np.asarray(point), np.asarray(normal)
    for entry in entries:
        vertices, loops = entry['tri_vertices'], entry['tri_loops']
        corners = entry['positions'][vertices]
        p, e, f = corners[:, 0], corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]
        d = point - p
        ee, ef, ff = np.sum(e * e, 1), np.sum(e * f, 1), np.sum(f * f, 1)
        det = ee * ff - ef * ef
        valid = abs(det) > 1e-18
        det = np.where(valid, det, 1)
        de, df = np.sum(d * e, 1), np.sum(d * f, 1)
        v, w = (ff * de - ef * df) / det, (ee * df - ef * de) / det
        weights = np.stack((1 - v - w, v, w), -1)
        valid &= (weights.min(1) >= -1e-5)
        normals = entry['world_normals'][loops].mean(1)
        valid &= normals @ normal > .05
        projected = np.sum(corners * weights[..., None], 1)
        distance = np.where(valid, np.sum((projected - point) ** 2, 1), np.inf)
        if len(distance):
            index = int(np.argmin(distance))
            if distance[index] < best:
                best = float(distance[index])
                result = np.sum(entry['uv'][loops[index]] * weights[index, :, None], 0)
    return result


def smudge(base, coverage, uv, normal=False):
    """Transport premultiplied color and coverage together from the previous dab."""
    from ..evaluation.blend_modes import sample_uv
    source = sample_uv(base, np.asarray(uv[0]), np.asarray(uv[1]))
    weight = np.asarray(coverage, np.float32)[..., None]
    alpha = base[..., 3:4] * (1 - weight) + source[3] * weight
    premul = base[..., :3] * base[..., 3:4] * (1 - weight) + source[:3] * source[3] * weight
    color = np.divide(premul, alpha, out=np.zeros_like(premul), where=alpha > 1e-8)
    if normal:
        vectors = color * 2 - 1
        lengths = np.linalg.norm(vectors, axis=-1, keepdims=True)
        vectors = np.divide(vectors, lengths, out=np.zeros_like(vectors), where=lengths > 1e-8)
        color = np.where((alpha > 1e-8) & (weight > 0), vectors * .5 + .5, color)
    return np.concatenate((color, alpha), -1).astype(np.float32)


def dab_stroke(stroke, sample, dab, anchor=None):
    from types import SimpleNamespace
    from .stroke_raster import pack_samples
    from .surface_reach import pack_anchors
    fields = ('hardness', 'flow', 'opacity', 'accumulate', 'projection', 'plane_normal', 'alpha_image',
              'alpha_rotation', 'alpha_angle', 'depth_limit', 'reach')
    data = {key: getattr(stroke, key) for key in fields}
    data.update(samples=pack_samples(np.asarray(sample)[None]), anchors=pack_anchors([anchor]) if anchor is not None else '')
    data.update({key: value for key, value in dab.get('values', {}).items() if key in data})
    if 'planar' in dab:
        data.update(projection='PLANAR', plane_normal=dab['planar'])
    return SimpleNamespace(**data)


def replay_channel(ts, layer, channel, records, n, base, cache, resources):
    """Full-image CPU reference for strokes that sample preceding paint."""
    from . import stroke_raster as raster
    from .strokes import channel_value
    from .surface_reach import unpack_anchors
    from ..geometry.snapshots import targets
    with targets(ts) as entries:
        return replay_entries(entries, channel, records, n, base)


def replay_entries(entries, channel, records, n, base):
    from . import stroke_raster as raster
    from .strokes import channel_value
    from .surface_reach import unpack_anchors
    rows = np.zeros((n, n, 4), np.float32) if base is None else np.array(base, copy=True)
    for stroke in records:
        samples = raster.unpack_samples(stroke.samples)
        dabs, anchors = decode(stroke, len(samples)), unpack_anchors(stroke.anchors)
        if stroke.blend != 'SMUDGE' and not any('values' in dab for dab in dabs):
            alpha = raster._rasterise(stroke, n, entries)[..., None]
            if stroke.blend == 'ERASE':
                rows[..., 3:4] *= 1 - alpha
                rows[..., :3][rows[..., 3] <= 1e-8] = 0
            else:
                out_alpha = alpha + rows[..., 3:4] * (1 - alpha)
                color = np.asarray(channel_value(stroke, channel), np.float32)
                premul = color * alpha + rows[..., :3] * rows[..., 3:4] * (1 - alpha)
                rows[..., :3] = np.divide(premul, out_alpha, out=np.zeros_like(premul), where=out_alpha > 1e-8)
                rows[..., 3:4] = out_alpha
            continue
        previous_coverage = np.zeros((n, n), np.float32)
        for index, (sample, dab) in enumerate(zip(samples, dabs)):
            one = dab_stroke(stroke, sample, dab, anchors[index] if len(anchors) == len(samples) else None)
            coverage = raster._rasterise(one, n, entries)
            if stroke.blend == 'SMUDGE':
                uv = donor_uv(entries, dab.get('donor', sample[:3]), sample[3:6])
                if uv is not None:
                    rows = smudge(rows, coverage, uv, channel == 'NORMAL')
                continue
            # MAX only adds the new coverage; FLOW always adds this dab.
            if stroke.accumulate == 'MAX':
                union = np.maximum(previous_coverage, coverage)
                coverage = np.divide(union - previous_coverage, 1 - previous_coverage,
                    out=np.zeros_like(union), where=previous_coverage < 1 - 1e-8)
                previous_coverage = union
            alpha = coverage[..., None]
            if stroke.blend == 'ERASE':
                rows[..., 3:4] *= 1 - alpha
                rows[..., :3][rows[..., 3] <= 1e-8] = 0
                continue
            from types import SimpleNamespace
            values = dict(dab.get('values', {}))
            from .strokes import CHANNEL_FIELDS
            field = CHANNEL_FIELDS[channel]
            color = channel_value(SimpleNamespace(**{field: values.get(field, getattr(stroke, field))}), channel)
            out_alpha = alpha + rows[..., 3:4] * (1 - alpha)
            premul = np.asarray(color, np.float32) * alpha + rows[..., :3] * rows[..., 3:4] * (1 - alpha)
            rows[..., :3] = np.divide(premul, out_alpha, out=np.zeros_like(premul), where=out_alpha > 1e-8)
            rows[..., 3:4] = out_alpha
    return rows
