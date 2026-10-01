"""Surface-local reach of a stamp (0.13 hardening, H2 §6.4).

A stroke recorded with ``reach == 'SURFACE'`` paints, for each sample, only the surface the
pointer hit and what connects to it without turning away from the stamp. The walk starts on
the evaluated polygon the ray hit (the sample's *anchor*: the hit point and that polygon,
stored with the stroke) and crosses shared edges onto neighbouring triangles that face the
stamp axis and lie inside the stamp's box. A same-facing sheet behind or beside the hit
surface, a disconnected piece of the same object and another object are never reached,
however close; a fold, a rim or a recess wall that turns away from the stamp stops the walk.
The kernel's own facing, radial and depth-band tests still apply to every texel reached.

The rule reads only the geometry snapshot (``geometry.snapshots.targets``): the accepted
surface is the same after the camera moves, after the project reopens and at every
resolution, because it never looks at a view. An anchor whose polygon no longer holds it (the
mesh was edited or a modifier renumbered its faces) seeds from the nearest triangle within the
stamp's depth band instead, so old strokes keep following their surface; an anchor that finds
no surface there paints nothing on that target.

``Sheets`` answers the kernel's question per (texel triangle, sample) pair; everything here is
pure numpy and unit-tested against small meshes (``tests/unit/test_surface_reach.py``).
"""
import numpy as np

from ..geometry.triangle_bounds import TriangleBounds

FACING = .05        # the kernel's front-facing threshold, applied to triangle normals during the walk
CHUNK = 256         # samples per walk (matches the kernel's sample chunk)
ANCHOR_WIDTH = 4    # x y z polygon


# ---- per-target preparation ----------------------------------------------------------------

def prepare(target):
    """Per-triangle geometric normals, boxes and a polygon index over every triangle of the
    target's object (all material slots, so a walk crosses a slot seam on one mesh), cached
    on the target dict."""
    cached = target.get('_reach')
    from ..native_bridge.lifecycle import cpu_library
    lib = cpu_library()
    if cached is not None and (lib is None or 'native' in cached):
        return cached
    if lib is not None:
        from ..native_bridge.reach import Reach
        cached = Reach(lib, target).snapshot()
        target['_reach'] = cached
        return cached
    positions = np.asarray(target['positions'], np.float64)
    tri_vertices = np.asarray(target['all_tri_vertices'], np.int64)
    corners = positions[tri_vertices]                                     # (T, 3, 3)
    normals = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    length = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = np.divide(normals, length, out=np.zeros_like(normals), where=length > 1e-18)
    if target.get('mirrored'):
        normals = -normals   # a mirrored placement reverses the world winding
    polygons = np.asarray(target['all_tri_polygons'], np.int64)
    order = np.argsort(polygons, kind='stable')
    cached = {'normals': normals.astype(np.float32), 'lo': corners.min(axis=1).astype(np.float32),
              'hi': corners.max(axis=1).astype(np.float32), 'corners': corners.astype(np.float32),
              'order': order, 'sorted_polygons': polygons[order], 'count': len(tri_vertices)}
    cached['bounds_index'] = TriangleBounds(cached['lo'], cached['hi'])
    target['_reach'] = cached
    return cached


def adjacency(tri_vertices):
    """CSR edge adjacency (indptr, indices) of triangles: neighbours share an edge (a
    non-manifold edge connects every triangle on it)."""
    from ..native_bridge.lifecycle import cpu_library
    if (lib := cpu_library()) is not None:
        from ..native_bridge.geometry import adjacency as native_adjacency
        return native_adjacency(lib, tri_vertices)
    tri_vertices = np.asarray(tri_vertices, np.int64)
    count = len(tri_vertices)
    if not count:
        return np.zeros(1, np.int64), np.zeros(0, np.int64)
    edges = np.stack([tri_vertices[:, [0, 1]], tri_vertices[:, [1, 2]], tri_vertices[:, [2, 0]]], axis=1).reshape(-1, 2)
    edges.sort(axis=1)
    base = int(tri_vertices.max()) + 1
    keys = edges[:, 0] * base + edges[:, 1]
    order = np.argsort(keys, kind='stable')
    sorted_keys, tri_of = keys[order], order // 3
    starts = np.flatnonzero(np.r_[True, sorted_keys[1:] != sorted_keys[:-1]])
    ends = np.r_[starts[1:], len(sorted_keys)]
    sizes = ends - starts
    pairs = []
    two = np.flatnonzero(sizes == 2)
    if len(two):
        a, b = tri_of[starts[two]], tri_of[starts[two] + 1]
        pairs.append(np.stack([np.r_[a, b], np.r_[b, a]], axis=1))
    for group in np.flatnonzero(sizes > 2):   # non-manifold: rare, every ordered pair
        members = tri_of[starts[group]:ends[group]]
        grid_a, grid_b = np.meshgrid(members, members, indexing='ij')
        off = grid_a != grid_b
        pairs.append(np.stack([grid_a[off], grid_b[off]], axis=1))
    if not pairs:
        return np.zeros(count + 1, np.int64), np.zeros(0, np.int64)
    pairs = np.concatenate(pairs)
    pairs = pairs[np.lexsort((pairs[:, 1], pairs[:, 0]))]
    indptr = np.zeros(count + 1, np.int64)
    np.add.at(indptr, pairs[:, 0] + 1, 1)
    return np.cumsum(indptr), pairs[:, 1]


# ---- anchors ---------------------------------------------------------------------------------

def point_triangle_distance(points, corners):
    """Distance from each point (N, 3) to its triangle (N, 3, 3) (Ericson, vectorised)."""
    from ..native_bridge import paint
    if (lib := paint.cpu_library()) is not None:
        return paint.distances(lib, points, corners)
    p = np.asarray(points, np.float64)
    a, b, c = (np.asarray(corners[:, i], np.float64) for i in range(3))
    ab, ac, ap = b - a, c - a, p - a
    d1, d2 = np.einsum('ij,ij->i', ab, ap), np.einsum('ij,ij->i', ac, ap)
    bp = p - b
    d3, d4 = np.einsum('ij,ij->i', ab, bp), np.einsum('ij,ij->i', ac, bp)
    cp = p - c
    d5, d6 = np.einsum('ij,ij->i', ab, cp), np.einsum('ij,ij->i', ac, cp)
    closest = np.empty_like(p)
    done = np.zeros(len(p), bool)

    def settle(mask, value):
        take = mask & ~done
        closest[take] = value[take]
        done[take] = True

    settle((d1 <= 0) & (d2 <= 0), a)
    settle((d3 >= 0) & (d4 <= d3), b)
    settle((d6 >= 0) & (d5 <= d6), c)
    vc = d1 * d4 - d3 * d2
    with np.errstate(divide='ignore', invalid='ignore'):
        v = np.where(np.abs(d1 - d3) > 0, d1 / (d1 - d3), 0.0)
    settle((vc <= 0) & (d1 >= 0) & (d3 <= 0), a + ab * v[:, None])
    vb = d5 * d2 - d1 * d6
    with np.errstate(divide='ignore', invalid='ignore'):
        w = np.where(np.abs(d2 - d6) > 0, d2 / (d2 - d6), 0.0)
    settle((vb <= 0) & (d2 >= 0) & (d6 <= 0), a + ac * w[:, None])
    va = d3 * d6 - d5 * d4
    with np.errstate(divide='ignore', invalid='ignore'):
        w = np.where(np.abs((d4 - d3) + (d5 - d6)) > 0, (d4 - d3) / ((d4 - d3) + (d5 - d6)), 0.0)
    settle((va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0), b + (c - b) * w[:, None])
    denominator = va + vb + vc
    with np.errstate(divide='ignore', invalid='ignore'):
        inv = np.where(np.abs(denominator) > 0, 1.0 / denominator, 0.0)
    v, w = vb * inv, vc * inv
    settle(np.ones(len(p), bool), a + ab * v[:, None] + ac * w[:, None])
    return np.linalg.norm(p - closest, axis=1)


def validate(reach, anchors, radii):
    """(triangle, sample) pairs whose anchor polygon still holds the anchor on this target,
    and which samples that resolved."""
    from ..native_bridge.lifecycle import cpu_library
    if 'native' in reach and cpu_library() is not None:
        return reach['native'].pairs(anchors, radii)
    anchors = np.asarray(anchors, np.float32)
    count = len(anchors)
    resolved = np.zeros(count, bool)
    if not count:
        return np.zeros(0, np.int64), np.zeros(0, np.int64), resolved
    points, polygons = anchors[:, :3], anchors[:, 3].astype(np.int64)
    sorted_polygons, order, corners = reach['sorted_polygons'], reach['order'], reach['corners']
    tolerance = np.maximum(1e-3 * np.asarray(radii, np.float32), 1e-5)
    known = polygons >= 0
    if not known.any():
        return np.zeros(0, np.int64), np.zeros(0, np.int64), resolved
    first = np.searchsorted(sorted_polygons, polygons, side='left')
    last = np.searchsorted(sorted_polygons, polygons, side='right')
    counts = np.where(known, last - first, 0)
    samples = np.repeat(np.arange(count), counts)
    offsets = np.arange(counts.sum()) - np.repeat(np.cumsum(counts) - counts, counts)
    triangles = order[first[samples] + offsets]
    if not len(triangles):
        return np.zeros(0, np.int64), np.zeros(0, np.int64), resolved
    distance = point_triangle_distance(points[samples], corners[triangles])
    hit = distance <= tolerance[samples]
    resolved[samples[hit]] = True
    return triangles[hit], samples[hit], resolved


def nearest(reach, anchors, axes, band, which):
    """For the samples ``which``: (triangle, distance) of the nearest triangle within the
    sample's depth band that faces its axis (-1, inf when none). A Python loop: only the rare
    anchors no target holds any more come here."""
    from ..native_bridge.lifecycle import cpu_library
    if 'native' in reach and cpu_library() is not None:
        return reach['native'].nearest(anchors, axes, band, which)
    anchors = np.asarray(anchors, np.float32)
    triangles = np.full(len(anchors), -1, np.int64)
    distances = np.full(len(anchors), np.inf, np.float64)
    corners = reach['corners']
    for sample in np.flatnonzero(which):
        reach_box = float(band[sample])
        point = anchors[sample, :3]
        near = reach['bounds_index'].query(point - reach_box, point + reach_box)
        if not len(near):
            continue
        facing = reach['normals'][near] @ np.asarray(axes[sample], np.float32) > FACING
        near = near[facing]
        if not len(near):
            continue
        distance = point_triangle_distance(np.repeat(point[None], len(near), axis=0), corners[near])
        best = int(np.argmin(distance))
        if distance[best] <= reach_box:
            triangles[sample], distances[sample] = near[best], distance[best]
    return triangles, distances


def seeds(reach, anchors, axes, radii, band, fallback=None):
    """Seed triangles of each sample: the anchor's polygon when it still holds the anchor,
    else — for the samples ``fallback`` allows (those no target of the stroke resolved, on
    the target nearest to them) — the nearest triangle within the sample's depth band that
    faces its axis. Returns (triangle index, sample index) pairs and the samples left
    without a seed."""
    from ..native_bridge.lifecycle import cpu_library
    if 'native' in reach and cpu_library() is not None:
        triangles, samples, resolved = reach['native'].pairs(anchors, radii, axes, band, fallback)
        return triangles, samples, np.flatnonzero(~resolved)
    anchors = np.asarray(anchors, np.float32)
    count = len(anchors)
    if not count:
        return np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros(0, np.int64)
    tri_list, sample_list = [], []
    triangles, samples, resolved = validate(reach, anchors, radii)
    if len(triangles):
        tri_list.append(triangles); sample_list.append(samples)
    allowed = np.ones(count, bool) if fallback is None else np.asarray(fallback, bool)
    wanted = ~resolved & allowed
    if wanted.any():
        found, _ = nearest(reach, anchors, axes, band, wanted)
        hit = np.flatnonzero(found >= 0)
        if len(hit):
            tri_list.append(found[hit]); sample_list.append(hit)
            resolved[hit] = True
    if tri_list:
        return np.concatenate(tri_list), np.concatenate(sample_list), np.flatnonzero(~resolved)
    return np.zeros(0, np.int64), np.zeros(0, np.int64), np.flatnonzero(~resolved)


# ---- the walk --------------------------------------------------------------------------------

class Sheets:
    """Which (triangle, sample) pairs of one target a SURFACE stroke may paint.

    ``samples`` are kernel rows (centre 0:3, normal 3:6, radius 7), ``anchors`` one
    (x, y, z, polygon) row per sample, ``planar`` the plane normal of a planar stroke or
    None, ``depth_limit`` the stroke's band in radii. The walk of each sample is bounded by
    its stamp box (centre ± radius, widened to hold its anchor). Chunks of samples are walked
    on demand as the kernel asks for them.
    """

    def __init__(self, target, samples, anchors, planar=None, depth_limit=1.0, fallback=None):
        self.reach = reach = prepare(target)
        self.samples = samples = np.ascontiguousarray(samples, np.float32)
        self.anchors = anchors = complete_anchors(samples, anchors)
        # Which samples may seed by proximity here: those whose anchor no target of the stroke
        # holds any more (``for_targets``); by default all of them (a single target).
        self.fallback = np.ones(len(samples), bool) if fallback is None else np.asarray(fallback, bool)
        radius = np.maximum(samples[:, 7], 1e-8)
        self.radius = radius
        self.band = radius * max(float(depth_limit), 1e-3)
        if planar is not None:
            axis = np.asarray(planar, np.float32)
            axis = axis / max(float(np.linalg.norm(axis)), 1e-8)
            self.axes = np.broadcast_to(axis, (len(samples), 3)).astype(np.float32)
        else:
            self.axes = samples[:, 3:6]
        centre = samples[:, :3]
        self.box_lo = np.minimum(centre - radius[:, None], anchors[:, :3])
        self.box_hi = np.maximum(centre + radius[:, None], anchors[:, :3])
        # Candidate triangles: those a stamp box of the stroke can reach.
        if len(samples):
            lo, hi = self.box_lo.min(axis=0), self.box_hi.max(axis=0)
            self.candidates = reach['bounds_index'].query(lo, hi)
        else:
            self.candidates = np.zeros(0, np.int64)
        self.tri_vertices = np.asarray(target['all_tri_vertices'], np.int64)[self.candidates]
        self._adjacency = None
        self._chunks = {}
        self.unseeded = None   # samples without a seed on this target (all chunks walked)

    @property
    def adjacency_csr(self):
        if self._adjacency is None:
            self._adjacency = adjacency(self.tri_vertices)
        return self._adjacency

    def chunk(self, index):
        """(candidate triangles, chunk samples) eligibility of chunk ``index``."""
        if index in self._chunks:
            return self._chunks[index]
        start = index * CHUNK
        stop = min(start + CHUNK, len(self.samples))
        count, width = len(self.candidates), stop - start
        visited = np.zeros((count, width), bool)
        if count and width:
            triangles, samples, _ = seeds(self.reach, self.anchors[start:stop], self.axes[start:stop],
                                          self.radius[start:stop], self.band[start:stop], self.fallback[start:stop])
            local, keep = self._local(triangles)
            frontier_t, frontier_s = local[keep], samples[keep]
            visited[frontier_t, frontier_s] = True
            indptr, indices = self.adjacency_csr
            normals, lo, hi = self.reach['normals'][self.candidates], self.reach['lo'][self.candidates], self.reach['hi'][self.candidates]
            axes = self.axes[start:stop]
            box_lo, box_hi = self.box_lo[start:stop], self.box_hi[start:stop]
            from ..native_bridge import paint
            if (lib := paint.cpu_library()) is not None:
                visited = paint.walk(lib, indptr, indices, normals, lo, hi, axes, box_lo, box_hi, visited)
                self._chunks[index] = visited
                return visited
            while len(frontier_t):
                degree = indptr[frontier_t + 1] - indptr[frontier_t]
                if not degree.any():
                    break
                sample = np.repeat(frontier_s, degree)
                offsets = np.arange(degree.sum()) - np.repeat(np.cumsum(degree) - degree, degree)
                neighbour = indices[np.repeat(indptr[frontier_t], degree) + offsets]
                pair = neighbour * width + sample
                pair = np.unique(pair)
                neighbour, sample = pair // width, pair % width
                fresh = ~visited[neighbour, sample]
                neighbour, sample = neighbour[fresh], sample[fresh]
                if not len(neighbour):
                    break
                inside = (np.all(lo[neighbour] <= box_hi[sample], axis=1) & np.all(hi[neighbour] >= box_lo[sample], axis=1))
                facing = np.einsum('ij,ij->i', normals[neighbour], axes[sample]) > FACING
                accept = inside & facing
                frontier_t, frontier_s = neighbour[accept], sample[accept]
                visited[frontier_t, frontier_s] = True
        self._chunks[index] = visited
        return visited

    def _local(self, triangles):
        """Positions of full-list triangle indices among the candidates, and which are candidates."""
        triangles = np.asarray(triangles, np.int64)
        if not len(self.candidates):
            return np.zeros(len(triangles), np.int64), np.zeros(len(triangles), bool)
        position = np.minimum(np.searchsorted(self.candidates, triangles), len(self.candidates) - 1)
        return position, self.candidates[position] == triangles

    def mask(self, triangles, start, stop):
        """(len(triangles), stop - start) eligibility of texels on ``triangles`` (indices into
        the target's full triangle list) for samples ``start:stop``."""
        local, known = self._local(triangles)
        result = np.zeros((len(local), stop - start), bool)
        if not known.any():
            return result
        at = start
        while at < stop:
            index = at // CHUNK
            visited = self.chunk(index)
            until = min(stop, (index + 1) * CHUNK)
            result[known, at - start:until - start] = visited[local[known], at - index * CHUNK:until - index * CHUNK]
            at = until
        return result

    def mask_sorted(self, triangles, start, stop):
        """Eligibility in a strictly increasing, unique material triangle list.

        An immutable Surface validates that precondition once. Looking up the
        sparse candidates in the material list avoids searching every material
        triangle for each dab; the returned full mask has the same row order.
        """
        triangles = np.asarray(triangles, np.int64)
        if len(self.candidates) >= len(triangles):
            # A broad brush may include many other material slots. Search the
            # smaller material list when inverse lookup would do more work.
            return self.mask(triangles, start, stop)
        result = np.zeros((len(triangles), stop - start), bool)
        if not len(triangles) or not len(self.candidates):
            return result
        positions = np.minimum(np.searchsorted(triangles, self.candidates), len(triangles) - 1)
        known = triangles[positions] == self.candidates
        if not known.any():
            return result
        rows = positions[known]
        at = start
        while at < stop:
            index = at // CHUNK
            visited = self.chunk(index)
            until = min(stop, (index + 1) * CHUNK)
            result[rows, at - start:until - start] = visited[known, at - index * CHUNK:until - index * CHUNK]
            at = until
        return result

    def seeded_samples(self):
        """Samples that found a seed on this target (every chunk walked)."""
        found = np.zeros(len(self.samples), bool)
        for index in range((len(self.samples) + CHUNK - 1) // CHUNK):
            visited = self.chunk(index)
            found[index * CHUNK:index * CHUNK + visited.shape[1]] = visited.any(axis=0)
        return found


def complete_anchors(samples, anchors):
    """Anchors as (S, 4): the stroke's own when they describe these samples, else each sample's
    centre with no polygon (seeds by proximity)."""
    samples = np.asarray(samples, np.float32)
    anchors = np.asarray(anchors, np.float32).reshape(-1, ANCHOR_WIDTH) if anchors is not None else None
    if anchors is None or len(anchors) != len(samples):
        anchors = np.concatenate([samples[:, :3], np.full((len(samples), 1), -1, np.float32)], axis=1)
    return anchors


def for_targets(entries, samples, anchors, planar=None, depth_limit=1.0):
    """One ``Sheets`` per target of a stroke, or None for targets nothing seeds.

    Anchors are validated against every target first: a sample seeds by proximity only where
    no target holds its anchor polygon any more (the mesh was edited or renumbered) — never
    on a target its ray did not hit, however close that target lies."""
    samples = np.ascontiguousarray(samples, np.float32)
    anchors = complete_anchors(samples, anchors)
    radii = np.maximum(samples[:, 7], 1e-8)
    band = radii * max(float(depth_limit), 1e-3)
    if planar is not None:
        axis = np.asarray(planar, np.float32)
        axes = np.broadcast_to(axis / max(float(np.linalg.norm(axis)), 1e-8), (len(samples), 3))
    else:
        axes = samples[:, 3:6]
    usable = [target for target in entries if 'all_tri_vertices' in target]
    validations = [validate(prepare(target), anchors, radii)[2] for target in usable]
    resolved_anywhere = np.any(validations, axis=0) if usable else np.zeros(len(samples), bool)
    # An anchor no target holds seeds on the one target nearest to it (within its band), so a
    # renumbered mesh keeps its strokes without handing them to a neighbouring surface as well.
    fallback = {id(target): np.zeros(len(samples), bool) for target in usable}
    unresolved = ~resolved_anywhere
    if unresolved.any() and usable:
        distances = np.stack([nearest(prepare(target), anchors, axes, band, unresolved)[1] for target in usable])
        from ..native_bridge.lifecycle import cpu_library
        if (lib := cpu_library()) is not None:
            from ..native_bridge.reach import choose
            choices = choose(lib, validations, distances)
            for index, target in enumerate(usable):
                fallback[id(target)] = choices[index]
        else:
            best = np.argmin(distances, axis=0)
            found = np.isfinite(distances.min(axis=0))
            for index, target in enumerate(usable):
                fallback[id(target)] = unresolved & found & (best == index)
    return [Sheets(target, samples, anchors, planar, depth_limit, fallback=fallback[id(target)])
            if 'all_tri_vertices' in target else None for target in entries]


def pack_anchors(anchors):
    """(S, 4) float32 → text for a StringProperty."""
    import base64
    array = np.ascontiguousarray(anchors, np.float32).reshape(-1, ANCHOR_WIDTH)
    return base64.b64encode(array.tobytes()).decode('ascii')


def unpack_anchors(text):
    import base64
    if not text:
        return np.zeros((0, ANCHOR_WIDTH), np.float32)
    return np.frombuffer(base64.b64decode(text), np.float32).reshape(-1, ANCHOR_WIDTH).copy()
