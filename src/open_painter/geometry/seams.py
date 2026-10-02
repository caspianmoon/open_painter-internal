"""Seam maps (0.9.4): make UV-space filters follow the mesh across UV seams.

A ``SeamMap`` knows, for every texel outside an island but within ``halo``
texels of one of its border edges, where that texel lands on the surface
when the neighbouring face is continued across the seam: the same 3D edge
seen from the other island, at the same parameter along the edge and the
same distance from it (scaled by the islands' relative UV density). ``pad``
fills those texels from across the seam, so a blur, morphology, warp or
gradient that then runs in image space sees the surface neighbourhood
instead of the margin. Per-triangle tangent Jacobians (``uv_direction``)
turn a world direction into a per-texel UV direction for directional effects.

Limits: the correspondence is first order (a straight continuation of the
adjacent face), and islands packed closer than the filter footprint still
see each other — padding never overwrites covered texels.
"""
import numpy as np

_maps = {}
MAX_MAPS = 3      # cached maps' worth of texels at the largest cached resolution
DEFAULT_HALO = 48


class SeamMap:
    def __init__(self, n):
        self.n = n
        self.covered = np.zeros((n, n), bool)
        self.rows = np.zeros(0, np.int64)
        self.cols = np.zeros(0, np.int64)
        self.source_x = np.zeros(0, np.float32)    # pixel coordinates across the seam
        self.source_y = np.zeros(0, np.float32)
        self.depth = np.zeros(0, np.float32)       # texels outside the island
        self.normal = np.zeros((0, 2), np.float32) # outward seam normal per band texel (diagnostics)
        self.island = np.zeros(0, np.int64)        # island each band texel pads (entries are per island)
        self._grouped = None                        # band entry order by island and each island's [start, stop)
        self.island_of = np.full((n, n), -1, np.int32)   # island id per covered texel
        self.bounds = {}                            # island id -> (r0, r1, c0, c1) of its covered texels
        self.triangle = np.full((n, n), -1, np.int32)
        self.jacobians = np.zeros((0, 2, 3), np.float32)   # dUV (in texels) / dWorld per triangle
        self.sides = None      # (s0, s1, s_centroid, o0, o1, o_centroid, island) per seam side, pixels (diagnostics)
        self.seam_edges = 0

    @property
    def halo(self):
        return float(self.depth.max()) if len(self.depth) else 0.0

    def pad(self, image, halo, passes=3):
        """A copy of ``image`` whose texels outside the islands, within ``halo`` of a seam,
        hold the values from across the closest seam. Bilinear; several passes so samples
        that fall just outside the neighbouring island read padded values too."""
        if not len(self.rows) or halo <= 0:
            return np.array(image, np.float32, copy=True)
        from ..texture_engine.filters import sample
        pick = (self.depth <= halo) & self._nearest
        rows, cols = self.rows[pick], self.cols[pick]
        sx, sy = self.source_x[pick], self.source_y[pick]
        padded = np.array(image, np.float32, copy=True)
        for _ in range(passes):
            padded[rows, cols] = sample(padded, sx, sy)
        return padded

    def island_crop(self, padded, island, halo):
        """(crop, (r0, r1, c0, c1)) of one island plus ``halo``, with every texel of the
        island's own band re-padded from *its* seams — even where another island's seam is
        closer — so islands packed tighter than the filter footprint keep their own
        continuation. ``padded`` is the whole-image ``pad`` result (its far borders are
        already filled, so one gather suffices)."""
        from ..texture_engine.filters import sample
        r0, r1, c0, c1 = self.bounds[island]
        r0, c0 = max(r0 - halo, 0), max(c0 - halo, 0)
        r1, c1 = min(r1 + halo + 1, self.n), min(c1 + halo + 1, self.n)
        crop = np.array(padded[r0:r1, c0:c1], copy=True)
        # Only this island's band entries: scanning the whole band once per
        # island made a many-island set quadratic.
        entries = self.entries(island)
        entries = entries[self.depth[entries] <= halo]
        if len(entries):
            rows, cols = self.rows[entries], self.cols[entries]
            within = (rows >= r0) & (rows < r1) & (cols >= c0) & (cols < c1)
            rows, cols, entries = rows[within], cols[within], entries[within]
            crop[rows - r0, cols - c0] = sample(padded, self.source_x[entries], self.source_y[entries])
        return crop, (r0, r1, c0, c1)

    @property
    def spans(self):
        """island id -> [start, stop) of its entries in the island-grouped band order."""
        if self._grouped is None:
            order = np.argsort(self.island, kind='stable')
            ids, starts, counts = np.unique(self.island[order], return_index=True, return_counts=True)
            self._grouped = (order, {int(i): (int(s), int(s + c)) for i, s, c in zip(ids, starts, counts)})
        return self._grouped[1]

    def entries(self, island):
        """Indices of one island's band entries; the band is grouped once, lazily."""
        start, stop = self.spans.get(island, (0, 0))
        return self._grouped[0][start:stop]

    def uv_direction(self, world_direction):
        """(n, n, 2) unit UV-space direction (pixels) of a world direction on each texel's face."""
        from ..native_bridge.lifecycle import cpu_library
        if len(self.jacobians) and (lib := cpu_library()) is not None:
            from ..native_bridge.geometry import directions
            return directions(lib, self.triangle, self.jacobians, world_direction)
        direction = np.asarray(world_direction, np.float32)
        direction = direction / max(float(np.linalg.norm(direction)), 1e-12)
        field = np.zeros((self.n, self.n, 2), np.float32)
        if not len(self.jacobians):
            return field
        inside = self.triangle >= 0
        local = self.jacobians[self.triangle[inside]] @ direction   # (k, 2)
        length = np.linalg.norm(local, axis=1, keepdims=True)
        field[inside] = np.where(length > 1e-9, local / np.maximum(length, 1e-9), 0)
        return field


def _edge_sides(mesh, uv, faces):
    """(va, vb, polygon, loop_a, loop_b) for every polygon edge of the chosen faces."""
    count = len(mesh.polygons)
    loop_start = np.empty(count, np.int32)
    loop_total = np.empty(count, np.int32)
    mesh.polygons.foreach_get('loop_start', loop_start)
    mesh.polygons.foreach_get('loop_total', loop_total)
    loop_vertex = np.empty(len(mesh.loops), np.int32)
    mesh.loops.foreach_get('vertex_index', loop_vertex)
    faces = np.asarray(faces, np.int64)
    totals = loop_total[faces].astype(np.int64)
    polygon = np.repeat(faces, totals)
    # Each loop as its face's first loop plus its position in the face: no per-face arange.
    position = np.arange(int(totals.sum())) - np.repeat(np.cumsum(totals) - totals, totals)
    loops = np.repeat(loop_start[faces].astype(np.int64), totals) + position
    following = loops + 1
    at_end = position == np.repeat(totals - 1, totals)
    following[at_end] = loop_start[polygon[at_end]]
    return loop_vertex[loops], loop_vertex[following], polygon, loops, following


ARENA_TEXELS = 1 << 21     # dense closest-seam texels one island group holds at a time
BAND_TEXELS = 1 << 20      # grid texels one vectorised band chunk evaluates


def build(ts, n, halo=DEFAULT_HALO):
    """Seam correspondence, island coverage and tangent Jacobians for the assigned meshes."""
    from .snapshots import targets
    from .uv_rasterizer import rasterize
    seam = SeamMap(n)
    sides = []          # per target: both sides of every seam edge, in pixels
    jacobians = []
    triangle_base = 0
    island_base = 0
    with targets(ts) as meshes:
        for target in meshes:
            mesh, uv = target['mesh'], target['uv']
            tri_loops, tri_vertices = target['tri_loops'], target['tri_vertices']
            from ..native_bridge.lifecycle import cpu_library
            native_topology = None
            target_island_base = island_base
            if (lib := cpu_library()) is not None:
                from ..native_bridge.topology import from_target
                native_topology = from_target(lib, target, n, seams=True)
                island_of_face = np.where(native_topology.islands >= 0,
                                          native_topology.islands + island_base, -1)
                island_base += native_topology.island_count
            else:
                from ..baking.maps.utility import _islands
                labels, _ = _islands(target)
                roots, island_of_face = np.unique(labels, return_inverse=True)
                unassigned = int(len(roots) > 0 and roots[0] < 0)
                island_of_face = np.where(labels >= 0, island_of_face.astype(np.int64) - unassigned + island_base, -1)
                island_base += len(roots) - unassigned
            if (lib := cpu_library()) is not None and hasattr(lib, 'op_uv_raster_assign'):
                # One native pass, with no per-texel arrays: the ids the scatter below writes.
                from ..native_bridge.geometry import raster_assign
                raster_assign(lib, uv[tri_loops], n,
                              [(np.arange(len(tri_loops), dtype=np.int64) + triangle_base, seam.triangle),
                               (np.asarray(island_of_face[target['tri_polygons']], np.int64), seam.island_of)],
                              seam.covered)
            else:
                rows, cols, tri, _ = rasterize(uv[tri_loops], n)
                if len(rows):
                    seam.covered[rows, cols] = True
                    seam.triangle[rows, cols] = tri + triangle_base
                    seam.island_of[rows, cols] = island_of_face[target['tri_polygons'][tri]]
            # Tangent Jacobian per triangle: dUV(px)/dWorld on the triangle's plane.
            p = target['positions'][tri_vertices].astype(np.float64)
            t = uv[tri_loops].astype(np.float64) * n
            from ..native_bridge.lifecycle import cpu_library
            if (lib := cpu_library()) is not None:
                from ..native_bridge.geometry import jacobians as native_jacobians
                jacobians.append(native_jacobians(lib, p, uv[tri_loops], n))
            else:
                e = np.stack((p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]), axis=1)           # (T, 2, 3)
                f = np.stack((t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1)           # (T, 2, 2)
                gram = e @ e.transpose(0, 2, 1)                                          # (T, 2, 2)
                det = gram[:, 0, 0] * gram[:, 1, 1] - gram[:, 0, 1] * gram[:, 1, 0]
                inverse = np.zeros_like(gram)
                ok = np.abs(det) > 1e-18
                inverse[ok, 0, 0] = gram[ok, 1, 1] / det[ok]
                inverse[ok, 1, 1] = gram[ok, 0, 0] / det[ok]
                inverse[ok, 0, 1] = -gram[ok, 0, 1] / det[ok]
                inverse[ok, 1, 0] = -gram[ok, 1, 0] / det[ok]
                jacobians.append((f.transpose(0, 2, 1) @ inverse @ e).astype(np.float32))   # (T, 2, 3)
            triangle_base += len(tri_loops)
            if native_topology is not None:
                seam.seam_edges += len(native_topology.sides) // 2
                if (native_sides := native_topology.seam_sides(target_island_base)) is not None:
                    sides.append(native_sides)
                continue
            # Seam edges: shared 3D edges whose two sides carry different UVs.
            faces = np.unique(target['tri_polygons'])
            va, vb, polygon, la, lb = _edge_sides(mesh, uv, faces)
            keys = np.minimum(va, vb).astype(np.int64) * len(mesh.vertices) + np.maximum(va, vb)
            order = np.argsort(keys, kind='stable')
            sorted_keys = keys[order]
            _, first, counts = np.unique(sorted_keys, return_index=True, return_counts=True)
            pairs = first[counts == 2]
            side_a, side_b = order[pairs], order[pairs + 1]
            # Orient both sides from the lower vertex index.
            flip_a, flip_b = va[side_a] > vb[side_a], va[side_b] > vb[side_b]
            a0 = np.where(flip_a[:, None], uv[lb[side_a]], uv[la[side_a]])
            a1 = np.where(flip_a[:, None], uv[la[side_a]], uv[lb[side_a]])
            b0 = np.where(flip_b[:, None], uv[lb[side_b]], uv[la[side_b]])
            b1 = np.where(flip_b[:, None], uv[la[side_b]], uv[lb[side_b]])
            seams = np.flatnonzero((np.abs(a0 - b0).max(axis=1) > 1e-6) | (np.abs(a1 - b1).max(axis=1) > 1e-6))
            if not len(seams):
                continue
            seam.seam_edges += len(seams)
            centroids = np.zeros((len(mesh.polygons), 2), np.float64)
            counts_per_face = np.zeros(len(mesh.polygons), np.float64)
            np.add.at(centroids, polygon, uv[la])
            np.add.at(counts_per_face, polygon, 1)
            centroids /= np.maximum(counts_per_face, 1)[:, None]
            pa, pb = polygon[side_a[seams]], polygon[side_b[seams]]
            ea0, ea1, eb0, eb1 = a0[seams] * n, a1[seams] * n, b0[seams] * n, b1[seams] * n
            ca, cb = (centroids[pa] * n).astype(np.float32), (centroids[pb] * n).astype(np.float32)
            # Side a then side b of each edge (the order breaks distance ties).
            both = lambda first, second: np.stack((first, second), axis=1).reshape((-1,) + first.shape[1:])
            sides.append((both(ea0, eb0), both(ea1, eb1), both(ca, cb), both(eb0, ea0), both(eb1, ea1), both(cb, ca),
                          both(island_of_face[pa], island_of_face[pb])))
    if jacobians:
        seam.jacobians = np.concatenate(jacobians)
    closest = None
    if sides:
        seam.sides = tuple(np.concatenate(items) for items in zip(*sides))
        from ..native_bridge.lifecycle import cpu_library
        if (lib := cpu_library()) is not None:
            from ..native_bridge.seams import bands
            result = bands(lib, seam.covered, n, halo, *seam.sides, with_nearest=True)
            if result is not None:
                closest, seam._nearest = result[:7], result[7]
        else:
            closest = _bands(seam.covered, n, halo, *seam.sides)
    if closest is not None:
        seam.rows, seam.cols, seam.source_x, seam.source_y, seam.depth, seam.normal, seam.island = closest
        # The globally closest seam of each texel, for whole-image padding (ties to the
        # lower island): the same depth-then-owner race as the per-island arenas.
        if not hasattr(seam, '_nearest'):
            linear = seam.rows * n + seam.cols
            composite = (seam.depth.view(np.int32).astype(np.int64) << 32) | seam.island
            race = np.full(n * n, np.iinfo(np.int64).max, np.int64)
            np.minimum.at(race, linear, composite)
            seam._nearest = race[linear] == composite
    else:
        seam._nearest = np.zeros(0, bool)
    from ..native_bridge.lifecycle import cpu_library
    if (lib := cpu_library()) is not None:
        from ..native_bridge.topology import island_bounds
        seam.bounds = island_bounds(lib, seam.island_of, island_base)
        return seam
    # Island bounds in one pass over the covered texels rather than one scan per island.
    rows, cols = np.nonzero(seam.island_of >= 0)
    if len(rows):
        ids = seam.island_of[rows, cols]
        count = int(ids.max()) + 1
        r0, c0 = np.full(count, n, np.int64), np.full(count, n, np.int64)
        r1, c1 = np.full(count, -1, np.int64), np.full(count, -1, np.int64)
        np.minimum.at(r0, ids, rows); np.maximum.at(r1, ids, rows)
        np.minimum.at(c0, ids, cols); np.maximum.at(c1, ids, cols)
        for island in np.flatnonzero(r1 >= 0):
            seam.bounds[int(island)] = (int(r0[island]), int(r1[island]), int(c0[island]), int(c1[island]))
    return seam


def _bands(covered, n, halo, s0, s1, s_centroid, o0, o1, o_centroid, island):
    """The closest seam per (island, texel) outside the islands: (rows, cols, x, y,
    depth, normal, island), or None without a band.

    Coordinates are pixels. Each side ``s0→s1`` and ``o0→o1`` is the same 3D edge on
    two islands, oriented alike; the outside of ``s`` maps to the inside of ``o`` at
    the same edge parameter and distance, scaled by the islands' UV densities. The
    band extends past the edge ends by ``halo`` so the corner squares between two
    seams are filled too (clamped to the far edge's ends); the closest seam wins where
    bands overlap, the earlier side among equals.

    Bands are cut into pieces of ``halo`` along the edge and evaluated many pieces per
    grid, and every band texel races in a dense per-island arena on (depth, side), so
    the overlapping bands of a dense mesh never pile up: memory is the arena and one
    grid chunk, and the winners are read back from the arena.
    """
    from ..native_bridge.lifecycle import cpu_library
    if (lib := cpu_library()) is not None:
        from ..native_bridge.seams import bands
        return bands(lib, covered, n, halo, s0, s1, s_centroid, o0, o1, o_centroid, island)
    s0, s1, o0, o1 = (np.asarray(v, np.float32) for v in (s0, s1, o0, o1))
    edge, other_edge = s1 - s0, o1 - o0
    length, other_length = np.hypot(edge[:, 0], edge[:, 1]), np.hypot(other_edge[:, 0], other_edge[:, 1])
    side = np.flatnonzero((length >= 1e-6) & (other_length >= 1e-6))
    if not len(side):
        return None
    s0, o0, island = s0[side], o0[side], np.asarray(island, np.int64)[side]
    edge, other_edge, length, other_length = edge[side], other_edge[side], length[side], other_length[side]
    tangent = edge / length[:, None]
    normal = np.stack((-tangent[:, 1], tangent[:, 0]), axis=1)
    normal[np.einsum('ij,ij->i', s_centroid[side] - s0, normal) > 0] *= -1        # outward from the face on side s
    other_tangent = other_edge / other_length[:, None]
    other_normal = np.stack((-other_tangent[:, 1], other_tangent[:, 0]), axis=1)
    other_normal[np.einsum('ij,ij->i', o_centroid[side] - o0, other_normal) < 0] *= -1   # inward on side o
    density = other_length / length
    halo = np.float32(halo)

    def offsets(w, px, py):
        """(along, depth) of texel offsets ``px, py`` from ``s0`` on sides ``w``."""
        return px * tangent[w, 0] + py * tangent[w, 1], px * normal[w, 0] + py * normal[w, 1]

    def sample(w, along, depth):
        """Pixel coordinates across the seam. The sample stays a texel inside the far island
        (its edge ends and border), so bilinear reads never straddle the far island's margin."""
        margin = np.minimum(np.float32(.75), other_length[w] / 2)
        t = np.clip(np.clip(along / length[w], 0, 1) * other_length[w], margin, other_length[w] - margin)
        d = np.maximum(depth * density[w], np.float32(.75))
        return (o0[w, 0] + other_tangent[w, 0] * t + other_normal[w, 0] * d - np.float32(.5),
                o0[w, 1] + other_tangent[w, 1] * t + other_normal[w, 1] * d - np.float32(.5))

    # Pieces of ``halo`` along [-halo, length + halo], half-open except the last, each
    # with the box of its rotated rectangle clamped to the image.
    count = np.maximum(np.ceil((length + 2 * halo) / halo), 1).astype(np.int64)
    of = np.repeat(np.arange(len(side)), count)
    index = np.arange(len(of)) - np.repeat(np.cumsum(count) - count, count)
    lo = (index * halo - halo).astype(np.float32)
    hi = np.minimum(lo + halo, length[of] + halo).astype(np.float32)
    last = index == count[of] - 1
    p0, p1 = s0[of] + tangent[of] * lo[:, None], s0[of] + tangent[of] * hi[:, None]
    reach = normal[of] * halo
    corners = np.stack((p0, p1, p0 + reach, p1 + reach), axis=1)                 # (P, 4, 2)
    start = np.maximum(np.floor(corners.min(axis=1) - 1), 0).astype(np.int64)
    stop = np.minimum(np.ceil(corners.max(axis=1) + 1), n - 1).astype(np.int64)
    pieces = np.flatnonzero((stop >= start).all(axis=1))
    if not len(pieces):
        return None
    of, lo, hi, last, start, stop = of[pieces], lo[pieces], hi[pieces], last[pieces], start[pieces], stop[pieces]
    extent = (stop - start).max(axis=1) + 1
    # Arenas: one dense box per island (the union of its pieces' boxes), islands taken
    # in order into groups of at most ARENA_TEXELS.
    ids, member = np.unique(island[of], return_inverse=True)
    box0, box1 = np.full((len(ids), 2), n, np.int64), np.full((len(ids), 2), -1, np.int64)
    np.minimum.at(box0, member, start); np.maximum.at(box1, member, stop)
    width = box1[:, 0] - box0[:, 0] + 1
    area = width * (box1[:, 1] - box0[:, 1] + 1)
    group, offset = np.zeros(len(ids), np.int64), np.zeros(len(ids), np.int64)
    groups, first, total = [], 0, 0
    for i in range(len(ids)):
        if total and total + area[i] > ARENA_TEXELS:
            groups.append((first, i, total))
            first, total = i, 0
        group[i], offset[i] = len(groups), total
        total += area[i]
    groups.append((first, len(ids), total))
    order = np.lexsort((extent, group[member]))          # by group, alike grids together
    bounds = np.searchsorted(group[member][order], np.arange(len(groups) + 1))
    sentinel = np.iinfo(np.int64).max
    results = []
    for g, (first, past, size) in enumerate(groups):
        arena = np.full(size, sentinel, np.int64)
        chosen = order[bounds[g]:bounds[g + 1]]
        at = 0
        while at < len(chosen):
            # One grid per chunk: pieces come in extent order, so the last piece's box
            # holds every piece of the chunk.
            block = int(extent[chosen[at]])
            take = max(BAND_TEXELS // (block * block), 1)
            block = int(extent[chosen[min(at + take, len(chosen)) - 1]])
            sub = chosen[at:at + max(BAND_TEXELS // (block * block), 1)]
            at += len(sub)
            block = int(extent[sub[-1]])
            w = of[sub]
            steps = np.arange(block, dtype=np.float32)
            gx = start[sub, 0, None, None].astype(np.float32) + steps[None, None, :]
            gy = start[sub, 1, None, None].astype(np.float32) + steps[None, :, None]
            along, depth = offsets(w[:, None, None], gx + np.float32(.5) - s0[w, 0, None, None],
                                   gy + np.float32(.5) - s0[w, 1, None, None])
            far = hi[sub, None, None]
            inside = ((depth > 0) & (depth <= halo) & (along >= lo[sub, None, None])
                      & ((along < far) | (last[sub, None, None] & (along == far)))
                      & (gx <= stop[sub, 0, None, None]) & (gy <= stop[sub, 1, None, None]))
            piece, yy, xx = np.nonzero(inside)
            rows, cols = start[sub[piece], 1] + yy, start[sub[piece], 0] + xx
            keep = ~covered[rows, cols]            # covered texels are never padded
            piece, rows, cols, yy, xx = piece[keep], rows[keep], cols[keep], yy[keep], xx[keep]
            if not len(piece):
                continue
            w, along, depth = of[sub[piece]], along[piece, yy, xx], depth[piece, yy, xx]
            overshoot = np.maximum(np.maximum(-along, along - length[w]), 0)
            distance = np.hypot(depth, overshoot)   # true distance to the edge segment
            owner = member[sub[piece]]
            linear = offset[owner] + (rows - box0[owner, 1]) * width[owner] + (cols - box0[owner, 0])
            np.minimum.at(arena, linear, (distance.view(np.int32).astype(np.int64) << 32) | w)
        won = np.flatnonzero(arena != sentinel)
        if not len(won):
            continue
        key = arena[won]
        del arena
        w = key & 0xffffffff
        distance = (key >> 32).astype(np.int32).view(np.float32)
        owner = np.repeat(np.arange(first, past), area[first:past])[won]
        won -= offset[owner]
        rows, cols = box0[owner, 1] + won // width[owner], box0[owner, 0] + won % width[owner]
        del key, won
        x, y = sample(w, *offsets(w, cols.astype(np.float32) + np.float32(.5) - s0[w, 0],
                                  rows.astype(np.float32) + np.float32(.5) - s0[w, 1]))
        results.append([rows, cols, x, y, distance, normal[w], ids[owner]])
    if not results:
        return None
    # Field by field, releasing each group's part as it is joined, so the join never
    # holds two copies of the whole map.
    joined = []
    for field in range(7):
        joined.append(np.concatenate([part[field] for part in results]))
        for part in results:
            part[field] = None
    return tuple(joined)


def _placement(ref):
    """What the seam map reads of an assignment: the mesh and its modifiers, and of the
    world matrix only orientation and size (the Jacobians use world edge vectors), so a
    move keeps the map."""
    from .provenance import object_record
    rotation = tuple(round(v, 6) + 0.0 for row in ref.object.matrix_world.to_3x3().normalized() for v in row)
    return repr(object_record(ref.object, transform='SCALE')), rotation, ref.slot_index


def map_for(ts, n, geometry_cache=None):
    """The seam map of a set at resolution ``n``, cached against its evaluated geometry."""
    scene = ts.id_data
    key = (scene.as_pointer(), ts.id, n, tuple(_placement(ref) for ref in ts.assignments if ref.object))
    found = _maps.pop(key, None)
    if found is None:
        found = build(ts, n)
    _maps[key] = found
    # MAX_MAPS maps' worth of texels at the largest cached resolution, most recently
    # used last: a set's preview map never evicts its full-resolution one.
    budget = MAX_MAPS * max(held.n for held in _maps.values()) ** 2
    while len(_maps) > 1 and sum(held.n ** 2 for held in _maps.values()) > budget:
        _maps.pop(next(iter(_maps)))
    return found


def clear():
    _maps.clear()
