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

from ..evaluation import metrics

SAMPLE_WIDTH = 13   # px py pz nx ny nz pressure radius tilt_x tilt_y tx ty tz
CHUNK = 256
MAX_PAIRS = 40_000_000   # texel × sample pairs evaluated at once
MAX_WORKING_BYTES = 1024 * 1024 * 1024
PAIR_BYTES = 64   # indices, weights, position and normal of one (texel, triangle) pair


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
    from ..native_bridge import paint
    if (lib := paint.cpu_library()) is not None:
        return paint.resample(lib, points, spacing)
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
    from ..native_bridge import paint
    if (lib := paint.cpu_library()) is not None:
        return paint.bounds(lib, samples, margin)
    centres, radius = samples[:, :3], samples[:, 7:8]
    return (centres - radius).min(axis=0) - margin, (centres + radius).max(axis=0) + margin


# ---- kernel ----------------------------------------------------------------------

def kernel(positions, normals, samples, hardness=.8, flow=1.0, accumulate='MAX', planar=None,
           alpha_texture=None, alpha_rotation='RAKE', alpha_angle=0.0, depth_limit=1.0, eligible=None):
    """Coverage (K,) of texels at world ``positions`` (K, 3) with ``normals`` (K, 3).

    Each sample is a disc of its radius in the plane of its normal (or of the
    ``planar`` normal for planar projection), with a depth band of ``depth_limit``
    radii either side of that plane, front-facing only, falling off from
    ``hardness`` outwards and scaled by the sample's pressure column and ``flow``.
    Samples union with ``MAX`` (a stroke never darkens where it overlaps itself)
    or ``FLOW`` (1 - prod(1 - a): builds up). ``eligible(texels, start, stop)``, when
    given, answers which (texel, sample) pairs a stamp may paint at all (the surface
    reach of a SURFACE stroke, ``surface_reach.Sheets.mask``): pairs it refuses score 0.
    """
    from ..native_bridge import paint
    if (lib := paint.cpu_library()) is not None:
        return paint.coverage(lib, positions, normals, samples,
                              dict(hardness=hardness, flow=flow, accumulate=accumulate, planar=planar,
                                   alpha_rotation=alpha_rotation, alpha_angle=alpha_angle, depth_limit=depth_limit),
                              alpha_texture, eligible)
    positions = np.ascontiguousarray(positions, np.float32)
    normals = np.ascontiguousarray(normals, np.float32)
    count = len(positions)
    result = np.zeros(count, np.float32)
    if not count or not len(samples):
        return result
    samples = np.ascontiguousarray(samples, np.float32)
    soft = max(1.0 - float(hardness), 1e-5)
    depth_limit = max(float(depth_limit), 1e-3)
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
            inside = facing & (radial < 1.0) & (np.abs(depth) < radius[None] * depth_limit)
            if eligible is not None:
                inside &= eligible(inside_box, start + sub_start, start + sub_start + len(sub))
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
    helper = np.where(np.abs(axis[:, 2:3]) < .9, np.array([[0, 0, 1]], np.float32), np.array([[1, 0, 0]], np.float32))
    fallback = np.cross(axis, helper)
    if rotation == 'RAKE' and tangent is not None and np.any(tangent):
        # The first sample of a gesture has no previous point, so its tangent is
        # zero: a zero frame would read the texture's centre texel over the whole disc.
        t = np.array(tangent, np.float32, copy=True)
        degenerate = np.linalg.norm(t, axis=1) < 1e-8
        t[degenerate] = fallback[degenerate]
    else:
        t = fallback
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
    """The red channel of a brush alpha as authored: decoded like every other image input,
    so a packed or file-backed alpha whose float buffer Blender associates (premultiplies)
    reads the same after a reopen as the generated image it was painted with."""
    if image is None:
        return None
    from ..evaluation.image_snapshots import read
    return read(None, image)[..., 0].astype(np.float32)


def _batches(uv, n):
    """Index runs of ``uv`` (T, 3, 2) whose covered texels fit one rasterisation pass.

    The per-texel temporaries (indices, weights, positions, normals) scale with
    covered texels, not with the triangle count: a dense mesh under a small
    stroke is cheap, a whole-UV stroke on a coarse mesh is not."""
    extent = np.clip(uv.max(axis=1) - uv.min(axis=1), 0.0, 1.0)
    estimate = extent[:, 0] * extent[:, 1] * float(n * n) + 1.0
    groups = np.cumsum(estimate) // (MAX_WORKING_BYTES // PAIR_BYTES)
    return np.split(np.arange(len(uv)), np.flatnonzero(np.diff(groups)) + 1)


def uv_window(corners, uv, lo, hi, margin=0.0):
    """Per-triangle UV boxes (T, 2, 2) enclosing where the world box [lo, hi] meets each
    triangle: the box corners projected onto the triangle's plane, mapped through its
    barycentric frame into UV space, bounded (an affine map keeps convex hulls, so the box's
    section of the plane lies inside the projected corners' hull). ``margin`` (UV units)
    widens the box: one texel of it keeps the neighbours of every marked texel rasterised,
    so the seam dilation sees the same coverage as an unclipped raster."""
    from ..native_bridge import paint
    if (lib := paint.cpu_library()) is not None:
        return paint.uv_window(lib, corners, uv, lo, hi, margin)
    corners = np.asarray(corners, np.float64)
    p0, e1, e2 = corners[:, 0], corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]
    box = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])], np.float64)
    d = box[None, :, :] - p0[:, None, :]                                     # (T, 8, 3)
    d00 = np.einsum('tc,tc->t', e1, e1)[:, None]
    d01 = np.einsum('tc,tc->t', e1, e2)[:, None]
    d11 = np.einsum('tc,tc->t', e2, e2)[:, None]
    d20 = np.einsum('tkc,tc->tk', d, e1)
    d21 = np.einsum('tkc,tc->tk', d, e2)
    denominator = d00 * d11 - d01 * d01
    safe = np.abs(denominator) > 1e-18
    denominator = np.where(safe, denominator, 1.0)
    v = (d11 * d20 - d01 * d21) / denominator
    w = (d00 * d21 - d01 * d20) / denominator
    u = 1.0 - v - w
    uv = np.asarray(uv, np.float64)
    projected = u[..., None] * uv[:, None, 0] + v[..., None] * uv[:, None, 1] + w[..., None] * uv[:, None, 2]
    window = np.stack((projected.min(axis=1) - margin, projected.max(axis=1) + margin), axis=1).astype(np.float32)
    whole = np.stack((uv.min(axis=1), uv.max(axis=1)), axis=1).astype(np.float32)
    return np.where(safe[..., None], window, whole)


def reach_for(entries, stroke, samples, planar, anchors=None):
    """The surface reach of a SURFACE stroke on each target (``surface_reach.Sheets`` per entry),
    or Nones for a depth-band stroke (recorded before 0.13.0, or with Reach set to Depth Band).
    ``anchors`` (a gesture's, not yet recorded) stand in for the stroke's own."""
    if getattr(stroke, 'reach', 'DEPTH') != 'SURFACE':
        return [None] * len(entries)
    from .surface_reach import for_targets, unpack_anchors
    if anchors is None:
        anchors = unpack_anchors(getattr(stroke, 'anchors', ''))
    return for_targets(entries, samples, anchors if len(anchors) == len(samples) else None, planar,
                       float(getattr(stroke, 'depth_limit', 1.0)))


def eligibility(sheets, target, picked):
    """The kernel's ``eligible`` callback for texels on the chosen triangles ``picked``."""
    if sheets is None:
        return None
    triangles = np.asarray(target['tri_indices'], np.int64)[picked]
    return lambda texels, start, stop: sheets.mask(triangles[texels], start, stop)


def _rasterise(stroke, n, entries):
    """(n, n) coverage of one stroke over the evaluated ``entries`` of a set."""
    from ..geometry.uv_rasterizer import interpolate, rasterize
    metrics.count('stroke_rasterisations')
    samples = unpack_samples(stroke.samples)
    if getattr(stroke, 'dab_payload', ''):
        from .payload import decode, dab_stroke
        from .surface_reach import unpack_anchors
        dabs = decode(stroke, len(samples))
        if any('planar' in dab or 'values' in dab for dab in dabs):
            result = np.zeros((n, n), np.float32)
            anchors = unpack_anchors(stroke.anchors)
            for index, (sample, dab) in enumerate(zip(samples, dabs)):
                one = dab_stroke(stroke, sample, dab, anchors[index] if len(anchors) == len(samples) else None)
                one.opacity = 1.0
                result = union_rows(result, _rasterise(one, n, entries), stroke.accumulate)
            return result * float(stroke.opacity)
    result = np.zeros((n, n), np.float32)
    covered = np.zeros((n, n), bool)
    if len(samples) and entries:
        lo, hi = bounds(samples)
        texture = alpha_pixels(stroke.alpha_image)
        planar = tuple(stroke.plane_normal) if stroke.projection == 'PLANAR' else None
        flat_result = result.reshape(-1)
        for target, sheets in zip(entries, reach_for(entries, stroke, samples, planar)):
            tri_vertices, tri_loops = target['tri_vertices'], target['tri_loops']
            corners = target['positions'][tri_vertices]                       # (T, 3, 3)
            chosen = np.flatnonzero(np.all(corners.max(axis=1) >= lo, axis=1) & np.all(corners.min(axis=1) <= hi, axis=1))
            if not len(chosen):
                continue
            uv = target['uv'][tri_loops[chosen]]
            window = uv_window(corners[chosen], uv, lo, hi, margin=1.5 / n)
            for batch in _batches(uv, n):
                rows, cols, tri, weights = rasterize(uv[batch], n, unique=False, clip=window[batch])
                if not len(rows):
                    continue
                picked = chosen[batch][tri]
                positions = interpolate(weights, target['positions'][tri_vertices[picked]])
                normals = interpolate(weights, target['world_normals'][tri_loops[picked]])
                alpha = kernel(positions, normals, samples, stroke.hardness, stroke.flow, stroke.accumulate, planar,
                               texture, stroke.alpha_rotation, stroke.alpha_angle, stroke.depth_limit,
                               eligible=eligibility(sheets, target, picked))
                np.maximum.at(flat_result, rows * n + cols, alpha)
                covered[rows, cols] = True
        if covered.any():
            # Dilation on the rows the stroke touched (with one row of slack): a full-canvas
            # pass at 4k costs more than the raster itself.
            hit = np.flatnonzero(covered.any(axis=1))
            lo_row, hi_row = max(int(hit[0]) - 1, 0), min(int(hit[-1]) + 2, n)
            result[lo_row:hi_row] = dilate(result[lo_row:hi_row], covered[lo_row:hi_row])
    return (result * float(stroke.opacity)).astype(np.float32)


def _compact(result):
    """The covered rows of a raster, or its covered texels when those are smaller.

    Strokes are local: memoising every stroke as a full canvas (64 MiB at 4k)
    let a handful of them outgrow the shared allowance, and the CPU tiles then
    evicted each stroke just before the next tile needed it again."""
    n = result.shape[1]
    hit = np.flatnonzero(result.any(axis=1))
    if not len(hit):
        return ('rows', 0, np.zeros((0, n), np.float32))
    lo, hi = int(hit[0]), int(hit[-1]) + 1
    band = result[lo:hi].copy()   # never a view: the full canvas must be released
    flat = np.flatnonzero(band)
    if len(flat) * 8 < band.nbytes:
        return ('texels', lo, (flat + lo * n).astype(np.int32), band.reshape(-1)[flat])
    return ('rows', lo, band)


def _expand(compact, y0, y1, n):
    """Rows ``y0:y1`` of a compact raster as (y1 - y0, n) float32."""
    rows = np.zeros((y1 - y0, n), np.float32)
    kind, lo, *data = compact
    if kind == 'rows':
        band = data[0]
        start, stop = max(y0, lo), min(y1, lo + len(band))
        if start < stop:
            rows[start - y0:stop - y0] = band[start - lo:stop - lo]
    else:
        flat, values = data
        first, last = np.searchsorted(flat, (y0 * n, y1 * n))
        rows.reshape(-1)[flat[first:last] - y0 * n] = values[first:last]
    return rows


def raster_band(compact, n):
    """[low, high) rows a compact raster covers (empty as [0, 0))."""
    kind, lo, *data = compact
    if kind == 'rows':
        return [int(lo), int(lo) + len(data[0])] if len(data[0]) else [0, 0]
    flat = data[0]
    return [int(lo), int(flat[-1] // n) + 1] if len(flat) else [0, 0]


def _key(ts, stroke, n, resources):
    """Memo key of one stroke's raster: (stroke revision, n, the set's geometry stamp)."""
    stamp = getattr(resources, 'geometry_stamp', None) if resources is not None else None
    return ('stroke', ts.id, stroke.id, stroke.revision, n, repr(stamp))


def rasters(ts, strokes, n, resources=None, cache=None):
    """Compact coverage of each stroke on one texture set, memoised per (stroke revision, n, geometry).

    The set's mesh is evaluated at most once per call, and only when a stroke
    misses the cache: each stroke used to run its own to_mesh. A job's ``cache``
    keeps the rasters its every strip reads when the shared allowance evicts them."""
    from contextlib import nullcontext
    from ..geometry.snapshots import targets
    keys = [_key(ts, stroke, n, resources) for stroke in strokes]
    found = {}
    for key in keys:
        if cache is not None and key in cache:
            found[key] = cache[key]
        elif resources is not None and key in resources:
            found[key] = resources[key]
    missing = [(stroke, key) for stroke, key in zip(strokes, keys) if key not in found]
    if missing:
        ready = bool(ts.assignments) and all(ref.object for ref in ts.assignments)
        with (targets(ts) if ready else nullcontext([])) as entries:
            for stroke, key in missing:
                found[key] = _compact(_rasterise(stroke, n, entries))
                if resources is not None:
                    resources[key] = found[key]
    if cache is not None:
        cache.update(found)
    return [found[key] for key in keys]


def coverage(ts, stroke, n, resources=None):
    """(n, n) coverage of one stroke on one texture set."""
    return _expand(rasters(ts, [stroke], n, resources)[0], 0, n, n)


def marks(raster):
    """Whether a compact raster covers any texel.

    Read off the compact form: a miss compacts to zero rows, and expanding a
    4k canvas only to ask ``any()`` of it would cost more than the raster."""
    kind, _, data, *_ = raster
    return kind == 'texels' or len(data) > 0


def touches(ts, stroke, n, resources=None):
    """Whether the stroke marks any texel of the set at resolution ``n``."""
    return marks(rasters(ts, [stroke], n, resources)[0])


def adopt(ts, stroke, n, resources, raster):
    """Memoise a compact ``raster`` of the stroke under the key ``resources`` stamps it with now.

    A set carries a geometry stamp only while something on it reads the assigned
    geometry, and the evaluation keys stroke rasters under that stamp: a raster
    built to decide whether a stroke links the set (which reads no geometry until
    it is linked) would be keyed under no stamp at all, and the first evaluation
    of the linked set would build the same raster again. Linking and adopting
    happen in one call, so the raster describes the geometry the stamp does."""
    resources[_key(ts, stroke, n, resources)] = raster


def channel_rows(ts, layer, channel, n, y0, y1, resources=None, base=None, cache=None):
    """Straight RGBA rows of a layer's strokes for one channel, composited over ``base`` rows.
    Only the strokes whose scope permits ``ts`` take part (``strokes.strokes_for``)."""
    from .strokes import channel_value, stroke_channels, strokes_for
    project = ts.id_data.open_painter
    rows = np.zeros((y1 - y0, n, 4), np.float32) if base is None else np.array(base, np.float32, copy=True)
    strokes = [stroke for stroke in strokes_for(project, layer.stroke_key, ts) if channel in stroke_channels(stroke)]
    if any(s.blend == 'SMUDGE' or getattr(s, 'dab_payload', '') for s in strokes):
        from .payload import replay_channel
        from ..evaluation.channel_eval import _image_source
        from ..evaluation.snapshots import capture
        prop = next(c for c in layer.channels if c.channel == channel)
        full_base = base if y0 == 0 and y1 == n else (None if prop.image is None else
            _image_source(prop.image, capture(prop), 'PAINT', n, 0, n, cache or {}, ts, resources or {}))
        key = ('resolved-strokes', ts.id, layer.id, channel, n, tuple((s.id, s.revision) for s in strokes),
               repr(getattr(resources, 'geometry_stamp', None)), ts.edit_serial)
        if cache is not None and key in cache:
            return cache[key][y0:y1].copy()
        full = replay_channel(ts, layer, channel, strokes, n, full_base, cache, resources)
        if cache is not None:
            cache[key] = full
        return full[y0:y1].copy()
    for stroke, raster in zip(strokes, rasters(ts, strokes, n, resources, cache)):
        if not _touches_rows(raster, y0, y1, n):
            continue   # a stroke elsewhere on the canvas costs these rows nothing
        coverage = _expand(raster, y0, y1, n)
        # Only the columns the stroke reaches in these rows: a brush-sized stroke on a
        # 4k canvas touches a few percent of a strip.
        columns = np.flatnonzero(coverage.any(axis=0))
        if not len(columns):
            continue
        c0, c1 = int(columns[0]), int(columns[-1]) + 1
        alpha = coverage[:, c0:c1, None]
        window = rows[:, c0:c1]
        if stroke.blend == 'ERASE':
            window[..., 3:4] *= 1.0 - alpha
            # A texel erased to nothing carries no colour: the rows then compose the same
            # whichever strips or column windows produced them (incremental = full).
            window[..., :3][window[..., 3] <= 1e-8] = 0.0
            continue
        color = np.asarray(channel_value(stroke, channel), np.float32)
        # Source-over of straight colour.
        out_alpha = alpha + window[..., 3:4] * (1.0 - alpha)
        premultiplied = color * alpha + window[..., :3] * window[..., 3:4] * (1.0 - alpha)
        window[..., :3] = np.divide(premultiplied, out_alpha, out=np.zeros_like(premultiplied), where=out_alpha > 1e-8)
        window[..., 3:4] = out_alpha
    return rows


def mask_rows(ts, mask, n, y0, y1, resources=None, cache=None):
    """Coverage rows of a mask's strokes (union of the strokes of its key)."""
    from .strokes import strokes_for
    project = ts.id_data.open_painter
    rows = np.zeros((y1 - y0, n), np.float32)
    strokes = strokes_for(project, mask.stroke_key, ts)
    for stroke, raster in zip(strokes, rasters(ts, strokes, n, resources, cache)):
        if not _touches_rows(raster, y0, y1, n):
            continue
        alpha = _expand(raster, y0, y1, n)
        rows = (rows * (1.0 - alpha)) if stroke.blend == 'ERASE' else np.maximum(rows, alpha)
    return rows


def _touches_rows(compact, y0, y1, n):
    low, high = raster_band(compact, n)
    return low < y1 and high > y0
