"""Rasterise vector strokes: stroke data in, per-set coverage and channel rows out.

A stroke is a list of samples in world space (centre, surface normal, pressure,
radius, tangent). ``kernel`` scores every texel of a set against those samples
in geometry space — the same disc-with-depth-band stamp as the material stamp,
unioned along the stroke — so one stroke crosses UV seams, texture sets and
resolutions: the rasteriser only needs each set's world-position and normal
rasters, which every set derives from its own UVs. The kernel is pure numpy
(unit-tested); ``coverage``/``channel_rows`` bind it to Blender data.
"""
import base64
import numpy as np

SAMPLE_WIDTH = 13   # px py pz nx ny nz pressure radius tilt_x tilt_y tx ty tz
CHUNK = 256
MAX_PAIRS = 40_000_000   # texel × sample pairs evaluated at once
MAX_WORKING_BYTES = 1024 * 1024 * 1024


# ---- stroke samples ----------------------------------------------------------

def pack_samples(samples):
    """(S, 12) float32 → text for a StringProperty (one RNA write, one undo blob)."""
    array = np.ascontiguousarray(samples, np.float32).reshape(-1, SAMPLE_WIDTH)
    return base64.b64encode(array.tobytes()).decode('ascii')


def unpack_samples(text):
    if not text:
        return np.zeros((0, SAMPLE_WIDTH), np.float32)
    return np.frombuffer(base64.b64decode(text), np.float32).reshape(-1, SAMPLE_WIDTH).copy()


def resample(points, spacing):
    """Indices of ``points`` (S, 3) kept when consecutive kept points are >= spacing apart."""
    if not len(points):
        return np.zeros(0, np.int64)
    kept = [0]
    last = points[0]
    for index in range(1, len(points)):
        if np.linalg.norm(points[index] - last) >= spacing:
            kept.append(index)
            last = points[index]
    return np.asarray(kept, np.int64)


def bounds(samples, margin=0.0):
    """World AABB (min, max) of samples including their radii."""
    if not len(samples):
        return np.zeros(3, np.float32), np.zeros(3, np.float32)
    centres, radius = samples[:, :3], samples[:, 7:8]
    return (centres - radius).min(axis=0) - margin, (centres + radius).max(axis=0) + margin


# ---- kernel ----------------------------------------------------------------------

def kernel(positions, normals, samples, hardness=.8, flow=1.0, accumulate='MAX', planar=None,
           alpha_texture=None, alpha_rotation='RAKE', alpha_angle=0.0):
    """Coverage (K,) of texels at world ``positions`` (K, 3) with ``normals`` (K, 3).

    Each sample is a disc of its radius in the plane of its normal (or of the
    ``planar`` normal for planar projection), with a depth band of one radius,
    front-facing only, falling off from ``hardness`` outwards and scaled by
    pressure and ``flow``. Samples union with ``MAX`` (a stroke never darkens
    where it overlaps itself) or ``FLOW`` (1 - prod(1 - a): builds up).
    """
    positions = np.ascontiguousarray(positions, np.float32)
    normals = np.ascontiguousarray(normals, np.float32)
    count = len(positions)
    result = np.zeros(count, np.float32)
    if not count or not len(samples):
        return result
    samples = np.ascontiguousarray(samples, np.float32)
    soft = max(1.0 - float(hardness), 1e-5)
    if planar is not None:
        plane = np.asarray(planar, np.float32)
        plane = plane / max(np.linalg.norm(plane), 1e-8)
    for start in range(0, len(samples), CHUNK):
        chunk = samples[start:start + CHUNK]
        lo, hi = bounds(chunk)
        inside_box = np.flatnonzero(np.all((positions >= lo) & (positions <= hi), axis=1))
        if not len(inside_box):
            continue
        step = max(1, min(len(chunk), MAX_PAIRS // max(len(inside_box), 1)))
        for sub_start in range(0, len(chunk), step):
            sub = chunk[sub_start:sub_start + step]
            centres, sample_normals = sub[:, :3], sub[:, 3:6]
            pressure, radius = sub[:, 6], np.maximum(sub[:, 7], 1e-8)
            rel = positions[inside_box, None, :] - centres[None, :, :]            # (k, s, 3)
            axis = np.broadcast_to(plane, sample_normals.shape) if planar is not None else sample_normals
            depth = np.einsum('ksc,sc->ks', rel, axis)
            lateral = rel - depth[..., None] * axis[None]
            radial = np.linalg.norm(lateral, axis=-1) / radius[None]
            facing = np.einsum('kc,sc->ks', normals[inside_box], axis) > .05
            inside = facing & (radial < 1.0) & (np.abs(depth) < radius[None])
            alpha = np.clip((1.0 - radial) / soft, 0.0, 1.0) * pressure[None] * flow
            if alpha_texture is not None:
                alpha = alpha * _alpha_lookup(alpha_texture, lateral, sub, radius, alpha_rotation, alpha_angle)
            alpha = np.where(inside, alpha, 0.0).astype(np.float32)
            if accumulate == 'FLOW':
                union = 1.0 - np.prod(1.0 - alpha, axis=1)
                result[inside_box] = 1.0 - (1.0 - result[inside_box]) * (1.0 - union)
            else:
                result[inside_box] = np.maximum(result[inside_box], alpha.max(axis=1))
    return result


def _alpha_lookup(texture, lateral, samples, radius, rotation, angle):
    """Sample a (H, W) alpha texture in each sample's disc frame (tangent from the stroke)."""
    axis = samples[:, 3:6]
    tangent = samples[:, 10:13] if samples.shape[1] >= 13 else None
    if rotation == 'RAKE' and tangent is not None and np.any(tangent):
        t = tangent
    else:
        helper = np.where(np.abs(axis[:, 2:3]) < .9, np.array([[0, 0, 1]], np.float32), np.array([[1, 0, 0]], np.float32))
        t = np.cross(axis, helper)
    t = t / np.maximum(np.linalg.norm(t, axis=1, keepdims=True), 1e-8)
    if angle:
        b0 = np.cross(axis, t)
        t = t * np.cos(angle) + b0 * np.sin(angle)
    b = np.cross(axis, t)
    u = np.einsum('ksc,sc->ks', lateral, t) / radius[None] * .5 + .5
    v = np.einsum('ksc,sc->ks', lateral, b) / radius[None] * .5 + .5
    h, w = texture.shape[:2]
    x = np.clip((u * w).astype(np.int64), 0, w - 1)
    y = np.clip((v * h).astype(np.int64), 0, h - 1)
    return texture[y, x]


def union_rows(accumulated, incoming, accumulate='MAX'):
    if accumulate == 'FLOW':
        return 1.0 - (1.0 - accumulated) * (1.0 - incoming)
    return np.maximum(accumulated, incoming)


def dilate(mask, coverage, iterations=1):
    """Grow a raster by one texel into uncovered neighbours (seam cracks), keeping covered texels."""
    result = mask.copy()
    for _ in range(iterations):
        grown = result.copy()
        from ..texture_engine.filters import shift
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            shifted = shift(result, dy, dx)   # never across the image border
            grown = np.maximum(grown, np.where(coverage, 0.0, shifted))
        result = grown
    return result


# ---- Blender binding ---------------------------------------------------------------

def alpha_pixels(image):
    if image is None:
        return None
    from ..core import image_manager as images
    return images.pixels(image)[..., 0].astype(np.float32)


def coverage(ts, stroke, n, resources=None):
    """(n, n) coverage of one stroke on one texture set, cached per (stroke revision, n, geometry)."""
    from ..geometry.snapshots import targets
    from ..geometry.uv_rasterizer import interpolate, rasterize
    stamp = getattr(resources, 'geometry_stamp', None) if resources is not None else None
    key = ('stroke', ts.id, stroke.id, stroke.revision, n, repr(stamp))
    if resources is not None and key in resources:
        return resources[key]
    samples = unpack_samples(stroke.samples)
    result = np.zeros((n, n), np.float32)
    covered = np.zeros((n, n), bool)
    if len(samples) and ts.assignments and all(ref.object for ref in ts.assignments):
        lo, hi = bounds(samples)
        texture = alpha_pixels(stroke.alpha_image)
        planar = tuple(stroke.plane_normal) if stroke.projection == 'PLANAR' else None
        with targets(ts) as entries:
            for target in entries:
                tri_vertices, tri_loops = target['tri_vertices'], target['tri_loops']
                corners = target['positions'][tri_vertices]                       # (T, 3, 3)
                chosen = np.flatnonzero(np.all(corners.max(axis=1) >= lo, axis=1) & np.all(corners.min(axis=1) <= hi, axis=1))
                if not len(chosen):
                    continue
                if len(chosen) * n * 16 > MAX_WORKING_BYTES:
                    raise ValueError('The stroke covers too many triangles for one rasterisation; split it')
                rows, cols, tri, weights = rasterize(target['uv'][tri_loops[chosen]], n, unique=False)
                if not len(rows):
                    continue
                positions = interpolate(weights, target['positions'][tri_vertices[chosen][tri]])
                normals = interpolate(weights, target['world_normals'][tri_loops[chosen][tri]])
                alpha = kernel(positions, normals, samples, stroke.hardness, stroke.flow, stroke.accumulate, planar,
                               texture, stroke.alpha_rotation, stroke.alpha_angle)
                flat = rows * n + cols
                accumulated = np.zeros(n * n, np.float32)
                np.maximum.at(accumulated, flat, alpha)
                result = np.maximum(result, accumulated.reshape(n, n))
                covered[rows, cols] = True
        if covered.any():
            result = dilate(result, covered)
    result = (result * float(stroke.opacity)).astype(np.float32)
    result.flags.writeable = False
    if resources is not None:
        resources[key] = result
    return result


def channel_rows(ts, layer, channel, n, y0, y1, resources=None, base=None):
    """Straight RGBA rows of a layer's strokes for one channel, composited over ``base`` rows."""
    from .strokes import channel_value, stroke_channels, strokes_for
    project = ts.id_data.open_painter
    rows = np.zeros((y1 - y0, n, 4), np.float32) if base is None else np.array(base, np.float32, copy=True)
    for stroke in strokes_for(project, layer.stroke_key):
        if channel not in stroke_channels(stroke):
            continue
        alpha = coverage(ts, stroke, n, resources)[y0:y1][..., None]
        if stroke.blend == 'ERASE':
            rows[..., 3:4] *= 1.0 - alpha
            continue
        color = np.asarray(channel_value(stroke, channel), np.float32)
        # Source-over of straight colour.
        out_alpha = alpha + rows[..., 3:4] * (1.0 - alpha)
        premultiplied = color * alpha + rows[..., :3] * rows[..., 3:4] * (1.0 - alpha)
        rows[..., :3] = np.divide(premultiplied, out_alpha, out=np.zeros_like(premultiplied), where=out_alpha > 1e-8)
        rows[..., 3:4] = out_alpha
    return rows


def mask_rows(ts, mask, n, y0, y1, resources=None):
    """Coverage rows of a mask's strokes (union of the strokes of its key)."""
    from .strokes import strokes_for
    project = ts.id_data.open_painter
    rows = np.zeros((y1 - y0, n), np.float32)
    for stroke in strokes_for(project, mask.stroke_key):
        alpha = coverage(ts, stroke, n, resources)[y0:y1]
        rows = (rows * (1.0 - alpha)) if stroke.blend == 'ERASE' else np.maximum(rows, alpha)
    return rows
