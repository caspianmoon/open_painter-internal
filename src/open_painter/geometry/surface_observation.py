"""Orthographic surface observations: exact per-pixel triangle identity.

An observation is a camera view of the evaluated mesh whose every covered pixel
resolves to a visible triangle and a barycentric point, and from there to a world
position, a surface normal and a UV coordinate. Screen-space rasterization reuses
the barycentric rasterizer UV baking already relies on, so a pixel resolves to the
same triangle that texture correspondence reports for the same ray.

Observations are orthographic on purpose: a pixel footprint is then constant across
the frame, which keeps projected resolution, silhouette distance and reprojection
weights exact instead of depth dependent. Nothing here reads or writes Blender data.
"""
import numpy as np

from .uv_rasterizer import rasterize as _raster

MAX_SIDE = 1024
MAX_TRIANGLES = 1_200_000
EDGE_ON = .12  # cosine below which a surface is treated as edge-on, never as evidence


def basis(direction, up):
    """Orthonormal rows (u, v, n); ``n`` points from the surface toward the camera."""
    n = np.asarray(direction, np.float64)
    if n.shape != (3,) or not np.isfinite(n).all() or np.linalg.norm(n) < 1e-9:
        raise ValueError('An observation direction must be a finite nonzero world vector')
    n = n / np.linalg.norm(n)
    v = np.asarray(up, np.float64)
    if v.shape != (3,) or not np.isfinite(v).all():
        raise ValueError('An observation up direction must be a finite world vector')
    v = v - n * (v @ n)
    if np.linalg.norm(v) < 1e-6:
        raise ValueError('An observation up direction must not be parallel to its viewing direction')
    v /= np.linalg.norm(v)
    return np.stack((np.cross(v, n), v, n))


def frame(points, direction, up, *, margin=1.06):
    """Square orthographic framing of ``points`` seen from ``direction``.

    Returns the camera matrices and the world size of one pixel, which the coverage
    and fusion stages use as the observation's actual surface sampling rate.
    """
    points = np.asarray(points, np.float64).reshape(-1, 3)
    if not len(points) or not np.isfinite(points).all():
        raise ValueError('An observation needs finite geometry to frame')
    axes = basis(direction, up)
    local = points @ axes.T
    lo, hi = local.min(axis=0), local.max(axis=0)
    center_local = (lo + hi) / 2
    extent = max(float(np.max((hi - lo)[:2])) * margin / 2, 1e-6)
    depth = max(float(hi[2] - lo[2]), 1e-6)
    center = center_local @ axes
    distance = depth * .5 + extent * 2 + 1e-6
    eye = center + axes[2] * distance
    matrix = np.eye(4)
    matrix[:3, :3] = axes
    matrix[:3, 3] = -(axes @ eye)
    near, far = max(distance - depth * .5 - extent, 1e-7), distance + depth * .5 + extent
    projection = np.eye(4)
    projection[0, 0] = projection[1, 1] = 1 / extent
    projection[2, 2] = -2 / (far - near)
    projection[2, 3] = -(far + near) / (far - near)
    return dict(matrix=matrix, projection=projection, axes=axes, center=center,
                extent=extent, near=near, far=far, distance=distance)


def project(matrix, projection, points):
    """Normalized top-left image coordinates and linear camera depth of world points."""
    points = np.asarray(points, np.float64).reshape(-1, 3)
    matrix = np.asarray(matrix, np.float64)
    camera = points @ matrix[:3, :3].T + matrix[:3, 3]
    clip = camera @ np.asarray(projection, np.float64)[:3, :3].T + np.asarray(projection, np.float64)[:3, 3]
    image = np.column_stack(((clip[:, 0] + 1) * .5, (1 - clip[:, 1]) * .5))
    return image.astype(np.float32), (-camera[:, 2]).astype(np.float32)


def rasterize(positions, triangles, owner, matrix, projection, side):
    """Nearest visible triangle per pixel, with barycentric weights and linear depth.

    ``owner`` labels each triangle: values >= 0 identify a reprojection target,
    -1 marks geometry that may occlude but never contributes appearance. Occluders
    take part in the depth test exactly like target geometry, so a pixel showing a
    hidden surface is reported as owned by the occluder rather than the target.
    """
    side = int(side)
    if not 8 <= side <= MAX_SIDE:
        raise ValueError(f'Observation resolution must be between 8 and {MAX_SIDE} pixels')
    triangles = np.asarray(triangles, np.int64).reshape(-1, 3)
    owner = np.asarray(owner, np.int32).reshape(-1)
    if len(owner) != len(triangles):
        raise ValueError('Every observed triangle needs an owner label')
    if len(triangles) > MAX_TRIANGLES:
        raise ValueError(f'A surface observation supports at most {MAX_TRIANGLES} evaluated triangles')
    empty = dict(triangle=np.full((side, side), -1, np.int32), owner=np.full((side, side), -1, np.int32),
                 depth=np.full((side, side), np.inf, np.float32),
                 barycentric=np.zeros((side, side, 3), np.float32), side=side)
    if not len(triangles):
        return empty
    image, depth = project(matrix, projection, np.asarray(positions, np.float64).reshape(-1, 3))
    corners = image[triangles]
    # Triangles entirely outside the frame cost only this bounding-box test.
    lo, hi = corners.min(axis=1), corners.max(axis=1)
    inside = np.flatnonzero((hi[:, 0] > 0) & (hi[:, 1] > 0) & (lo[:, 0] < 1) & (lo[:, 1] < 1))
    if not len(inside):
        return empty
    rows, cols, index, weights = _raster(corners[inside], side, unique=False)
    if not len(rows):
        return empty
    chosen = inside[index]
    z = (weights * depth[triangles[chosen]]).sum(axis=1)
    flat = rows.astype(np.int64) * side + cols
    order = np.lexsort((z, flat))
    _, first = np.unique(flat[order], return_index=True)
    keep = order[first]
    empty['triangle'].reshape(-1)[flat[keep]] = chosen[keep].astype(np.int32)
    empty['owner'].reshape(-1)[flat[keep]] = owner[chosen[keep]]
    empty['depth'].reshape(-1)[flat[keep]] = z[keep].astype(np.float32)
    empty['barycentric'].reshape(-1, 3)[flat[keep]] = weights[keep].astype(np.float32)
    return empty


def interpolate(buffers, corners):
    """Per-pixel interpolation of a (T, 3, C) triangle-corner attribute."""
    triangle, weights = buffers['triangle'], buffers['barycentric']
    corners = np.asarray(corners, np.float32)
    flat = np.clip(triangle.reshape(-1), 0, max(len(corners) - 1, 0))
    values = (weights.reshape(-1, 3, 1) * corners[flat]).sum(axis=1)
    values[triangle.reshape(-1) < 0] = 0
    return values.reshape(*triangle.shape, corners.shape[2])


def silhouette_distance(visible, depth, *, limit=8, break_ratio=.01):
    """Pixels to the nearest observation boundary: background, or a depth cliff.

    A projected object boundary and a self-occlusion edge are both places where a
    generated pixel cannot be attributed to one surface point with confidence.
    """
    visible = np.asarray(visible, bool)
    depth = np.asarray(depth, np.float32)
    finite = depth[visible]
    scale = float(np.ptp(finite)) if finite.size else 0.
    tolerance = max(scale * break_ratio, 1e-6)
    interior = visible.copy()
    safe = np.where(visible, depth, 0.)
    for axis in (0, 1):
        for shift in (-1, 1):
            neighbour_visible = np.roll(visible, shift, axis=axis)
            neighbour_depth = np.roll(safe, shift, axis=axis)
            step = np.abs(safe - neighbour_depth) > tolerance
            interior &= neighbour_visible & ~step
    # Edge pixels are distance 0; each erosion pushes the interior one pixel inward.
    distance = np.zeros(visible.shape, np.float32)
    current = interior
    for step in range(1, int(limit) + 1):
        distance[current] = step
        if not current.any():
            break
        shrunk = current.copy()
        for axis in (0, 1):
            for shift in (-1, 1):
                shrunk &= np.roll(current, shift, axis=axis)
        # Rolling wraps; the frame border is always an observation boundary.
        shrunk[0, :] = shrunk[-1, :] = shrunk[:, 0] = shrunk[:, -1] = False
        current = shrunk
    return np.where(visible, distance, 0.)


def sample(image, coords):
    """Bilinear sample of an (H, W, C) image at normalized top-left coordinates."""
    image = np.asarray(image, np.float32)
    h, w = image.shape[:2]
    coords = np.asarray(coords, np.float32).reshape(-1, 2)
    x = np.clip(coords[:, 0] * w - .5, 0, w - 1)
    y = np.clip(coords[:, 1] * h - .5, 0, h - 1)
    x0, y0 = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64)
    x1, y1 = np.minimum(x0 + 1, w - 1), np.minimum(y0 + 1, h - 1)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    top = image[y0, x0] * (1 - fx) + image[y0, x1] * fx
    bottom = image[y1, x0] * (1 - fx) + image[y1, x1] * fx
    return top * (1 - fy) + bottom * fy


def nearest(buffer, coords):
    """Nearest-pixel indices of normalized top-left coordinates in a square buffer."""
    side = buffer.shape[0]
    coords = np.asarray(coords, np.float32).reshape(-1, 2)
    cols = np.clip((coords[:, 0] * side).astype(np.int64), 0, side - 1)
    rows = np.clip((coords[:, 1] * side).astype(np.int64), 0, side - 1)
    return rows, cols
