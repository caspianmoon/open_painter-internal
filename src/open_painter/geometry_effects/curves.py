"""Mask → curves: thin a painted or generated mask to its centre lines and trace them.

Pure numpy (``skeleton``, ``trace``, ``simplify``) so the algorithms are unit
tested; ``to_world`` and ``create_curve`` bind the polylines to a texture set's
UV→world raster and to a Blender CURVE object the artist can edit.
"""
import numpy as np


def skeleton_steps(mask, threshold=.5, max_iterations=64):
    """Zhang–Suen thinning as a generator (yields per iteration); returns the 1-px skeleton."""
    image = (np.asarray(mask, np.float32) > threshold)
    padded = np.zeros((image.shape[0] + 2, image.shape[1] + 2), bool)
    padded[1:-1, 1:-1] = image
    for iteration in range(max_iterations):
        changed = False
        for step in (0, 1):
            p = padded
            p2 = p[:-2, 1:-1]; p3 = p[:-2, 2:]; p4 = p[1:-1, 2:]; p5 = p[2:, 2:]
            p6 = p[2:, 1:-1]; p7 = p[2:, :-2]; p8 = p[1:-1, :-2]; p9 = p[:-2, :-2]
            centre = p[1:-1, 1:-1]
            neighbours = np.stack((p2, p3, p4, p5, p6, p7, p8, p9), axis=0).astype(np.int8)
            count = neighbours.sum(axis=0)
            rolled = np.roll(neighbours, -1, axis=0)
            transitions = ((neighbours == 0) & (rolled == 1)).sum(axis=0)
            if step == 0:
                cond = (p2 & p4 & p6) == 0
                cond &= (p4 & p6 & p8) == 0
            else:
                cond = (p2 & p4 & p8) == 0
                cond &= (p2 & p6 & p8) == 0
            remove = centre & (count >= 2) & (count <= 6) & (transitions == 1) & cond
            if remove.any():
                changed = True
                padded[1:-1, 1:-1][remove] = False
        yield iteration
        if not changed:
            break
    return padded[1:-1, 1:-1]


def skeleton(mask, threshold=.5, max_iterations=64):
    steps = skeleton_steps(mask, threshold, max_iterations)
    while True:
        try:
            next(steps)
        except StopIteration as done:
            return done.value


OFFSETS = [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)]


def trace(skeleton_mask, min_length=3):
    """Polylines [(row, col), ...] along a 1-px skeleton: paths between endpoints/junctions, and loops."""
    pixels = set(zip(*np.nonzero(skeleton_mask)))
    if not pixels:
        return []

    def neighbours(point):
        r, c = point
        return [(r + dr, c + dc) for dr, dc in OFFSETS if (r + dr, c + dc) in pixels]

    degree = {point: len(neighbours(point)) for point in pixels}
    visited_edges = set()
    paths = []
    nodes = [point for point in pixels if degree[point] != 2]
    for start in sorted(nodes):
        for nxt in neighbours(start):
            edge = frozenset((start, nxt))
            if edge in visited_edges:
                continue
            path = [start, nxt]
            visited_edges.add(edge)
            previous, current = start, nxt
            while degree[current] == 2:
                following = [p for p in neighbours(current) if p != previous]
                if not following:
                    break
                step = following[0]
                edge = frozenset((current, step))
                if edge in visited_edges:
                    break
                visited_edges.add(edge)
                path.append(step)
                previous, current = current, step
            paths.append(path)
    # Closed loops have no nodes: start anywhere on them.
    covered = {p for path in paths for p in path}
    for start in sorted(pixels - covered):
        if start in covered:
            continue
        path, previous, current = [start], None, start
        while True:
            following = [p for p in neighbours(current) if p != previous and p not in path[1:]]
            if not following:
                break
            step = following[0]
            if step == start:
                path.append(start)
                break
            path.append(step)
            previous, current = current, step
        covered.update(path)
        paths.append(path)
    return [path for path in paths if len(path) >= min_length]


def simplify(points, tolerance=1.0):
    """Douglas–Peucker on an (N, 2+) polyline."""
    points = np.asarray(points, np.float64)
    return points[simplify_indices(points, tolerance)]


def simplify_indices(points, tolerance=1.0):
    """Boolean keep-mask of Douglas–Peucker on an (N, 2+) polyline."""
    points = np.asarray(points, np.float64)
    if len(points) < 3:
        return np.ones(len(points), bool)
    keep = np.zeros(len(points), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        segment = points[b] - points[a]
        length = np.linalg.norm(segment)
        if length < 1e-12:
            distances = np.linalg.norm(points[a + 1:b] - points[a], axis=1)
        else:
            rel = points[a + 1:b] - points[a]
            projection = (rel @ segment) / (length * length)
            distances = np.linalg.norm(rel - np.outer(projection, segment), axis=1)
        index = int(np.argmax(distances))
        if distances[index] > tolerance:
            split = a + 1 + index
            keep[split] = True
            stack.append((a, split)); stack.append((split, b))
    return keep


def split_jumps(points, maximum):
    """Split a world polyline where consecutive points are farther apart than ``maximum`` (island jumps)."""
    points = np.asarray(points, np.float64)
    if len(points) < 2:
        return [points] if len(points) else []
    gaps = np.linalg.norm(np.diff(points, axis=0), axis=1)
    cuts = np.flatnonzero(gaps > maximum) + 1
    pieces = np.split(points, cuts)
    return [piece for piece in pieces if len(piece) >= 2]


# ---- Blender binding -------------------------------------------------------------

def to_world(ts, polylines, n, mask_shape):
    """Pixel polylines (row, col) → world polylines with normals, split at seams."""
    from ..painting.projection import geometry
    positions, normals, valid = geometry(ts, n, coverage=True)
    scale = n / mask_shape[0]
    texel = _texel_size(ts, n)
    result = []
    for path in polylines:
        rc = np.asarray(path, np.float64) * scale
        rows = np.clip(np.round(rc[:, 0]).astype(int), 0, n - 1)
        cols = np.clip(np.round(rc[:, 1]).astype(int), 0, n - 1)
        ok = valid[rows, cols]
        world = positions[rows, cols]
        normal = normals[rows, cols]
        # Drop invalid texels, then cut where the world distance jumps (another island).
        current_p, current_n = [], []
        for point, nrm, good in zip(world, normal, ok):
            if not good:
                if len(current_p) >= 2:
                    result.append((np.asarray(current_p), np.asarray(current_n)))
                current_p, current_n = [], []
                continue
            if current_p and np.linalg.norm(point - current_p[-1]) > 4 * texel:
                if len(current_p) >= 2:
                    result.append((np.asarray(current_p), np.asarray(current_n)))
                current_p, current_n = [], []
            current_p.append(point); current_n.append(nrm)
        if len(current_p) >= 2:
            result.append((np.asarray(current_p), np.asarray(current_n)))
    return result


def _texel_size(ts, n):
    """Median world size of one texel of the set's assigned triangles."""
    from ..geometry.snapshots import targets
    world = uv = 0.0
    with targets(ts) as meshes:
        for target in meshes:
            xyz = target['positions'][target['tri_vertices']]
            world += float(np.linalg.norm(np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0]), axis=1).sum() * .5)
            st = target['uv'][target['tri_loops']]
            a, b = st[:, 1] - st[:, 0], st[:, 2] - st[:, 0]
            uv += float(np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]).sum() * .5)
    return float(np.sqrt(max(world, 1e-12) / max(uv, 1e-12)) / n)


def create_curve(scene, obj, polylines, name, tag):
    """A POLY-spline CURVE object (editable, parented to the source) from world polylines."""
    import bpy
    # Before any datablock exists: inverted() raises on a zero-scaled source and would
    # leave the linked curve behind, re-transformed by the parent; inverted_safe never does.
    inverse = obj.matrix_world.inverted_safe()
    data = bpy.data.curves.new(name, 'CURVE')
    data.dimensions = '3D'
    for points, normals in polylines:
        spline = data.splines.new('POLY')
        spline.points.add(len(points) - 1)
        for point, (x, y, z), normal in zip(spline.points, points, normals):
            point.co = (x, y, z, 1.0)
            point.tilt = 0.0
    curve = bpy.data.objects.new(name, data)
    curve[tag] = 'CURVE'
    scene.collection.objects.link(curve)
    curve.parent = obj
    curve.matrix_parent_inverse = inverse
    return curve


def extract_steps(ts, mask_image, n=512, threshold=.5, tolerance=1.0):
    """Cooperative: thin the mask, trace, simplify, project; returns world polylines."""
    from ..core.image_manager import pixels
    from ..evaluation.blend_modes import sample_uv
    raster = pixels(mask_image)[..., 0]
    if raster.shape[0] != n:
        u, v = np.meshgrid((np.arange(n, dtype=np.float32) + .5) / n, (np.arange(n, dtype=np.float32) + .5) / n)
        raster = sample_uv(pixels(mask_image), u, v)[..., 0]
    steps = skeleton_steps(raster, threshold)
    thin = None
    while True:
        try:
            iteration = next(steps)
            yield min(.6, iteration / 64 * .6)
        except StopIteration as done:
            thin = done.value
            break
    paths = trace(thin)
    yield .8
    # Project the dense pixel paths first (seam splits need adjacent samples), then
    # simplify in world space with the tolerance measured in texels.
    texel = _texel_size(ts, n)
    result = []
    for points, normals in to_world(ts, paths, n, raster.shape):
        keep = simplify_indices(points, tolerance * texel)
        result.append((points[keep], normals[keep]))
    return result
