"""UV padding for exported maps (add-on quality remedy, M2A): geometry-derived ownership,
coverage-aware resampling and deterministic nearest-donor extension into the gutters.

Everything here reads the evaluated geometry, never the pixels: a texel is valid when a
face of an assigned material slot covers its centre in the export UV layer at the export
resolution, whatever its colour or alpha. Empty texels within the padding distance copy
the nearest valid texel; when two islands' fronts meet, the nearer one wins and ties go
to the lower island id, then the lower texel index. Values are copied, never averaged
across islands, and never wrapped across the atlas border. Occupied texels are never
changed.

Distance metric: Euclidean distance between texel centres, in output pixels. The
production path (``nearest_donors``) is an exact banded separable transform for
distances up to ``FILL_BAND`` pixels: a vertical scan finds each column's nearest valid
texel, a horizontal window of ±distance pixels then finds the global minimum of
(d², island, index) per texel, so any tie resolves by the same key whatever the
processing order. "Fill Empty Space" continues beyond the band at 8×, 64×, 512×… coarser
resolution with the same transform until the band spans the whole grid, so it terminates
with every empty texel resolved whenever any texel is valid (``unresolved_texels`` and
``donors_valid`` are the checks the exporter runs before publishing). A texel resolved at
a level with cells of ``c`` texels takes the representative (lowest index) of the nearest
valid cell: its donor is at most 2·√2·c texels farther than the true nearest valid texel
(the declared error bound, tested against the oracle). ``reference_donors`` is the
brute-force oracle the tests hold the production path to.

Memory: the ownership map (bool coverage + int32 islands) is built once per export and
shared by every channel; the transform keeps int64 keys only for the row band it
processes (16 MiB) plus the two result arrays (donor int32, d² int16: 96 MiB at 4K);
channels are extended one at a time, each borrowing one image-sized copy.
"""
import numpy as np

FILL_BAND = 64            # pixels the exact transform reaches; the fill mode continues coarser
COARSE_STEP = 8           # the fill mode's cells grow by this factor per level until the band spans the grid
D2_SHIFT, ISLAND_SHIFT = 46, 26
INDEX_MASK = (1 << ISLAND_SHIFT) - 1
INF = np.int64(1) << np.int64(62)
UNDERSIZED_TEXELS = 16    # an island with fewer valid texels cannot survive a 2× mip
MODES = [('AUTOMATIC', 'Automatic', 'A gutter sized for the export resolution: 4 px at 512 up to 64 px at 8K'),
         ('CUSTOM', 'Custom', 'A gutter of the given width in output pixels'),
         ('FILL', 'Fill Empty Space', 'Extend the nearest surface over every empty texel'),
         ('NONE', 'None', 'No gutter (diagnostics and special workflows)')]


def automatic_padding(n):
    """The automatic gutter width for an output side of ``n`` pixels."""
    for side, pixels in ((512, 4), (1024, 8), (2048, 16), (4096, 32)):
        if n <= side:
            return pixels
    return 64


def effective_padding(settings, n):
    """The padding of an export in output pixels: an int, ``'FILL'`` or 0 for none."""
    mode = getattr(settings, 'padding_mode', 'AUTOMATIC')
    if mode == 'NONE':
        return 0
    if mode == 'FILL':
        return 'FILL'
    if mode == 'CUSTOM':
        return max(0, int(getattr(settings, 'padding_pixels', 0)))
    return automatic_padding(n)


def describe(settings, n):
    """'UV Padding: 16 output pixels' for the Export view."""
    padding = effective_padding(settings, n)
    if padding == 'FILL':
        return 'UV Padding: fill empty space'
    if not padding:
        return 'UV Padding: none'
    mode = getattr(settings, 'padding_mode', 'AUTOMATIC')
    return f'UV Padding: {padding} output pixels' + (' (automatic)' if mode == 'AUTOMATIC' else '')


# ---- ownership ----------------------------------------------------------------------------------

class Ownership:
    """Valid texels and their islands at one resolution, from the evaluated geometry."""

    def __init__(self, n, covered, island, diagnostics):
        self.n = n
        self.covered = covered          # (n, n) bool
        self.island = island            # (n, n) int32, -1 outside
        self.diagnostics = diagnostics  # islands, overlaps, degenerate triangles, undersized islands


def build_ownership(entries, n):
    """Ownership from evaluated targets (``geometry.snapshots.targets`` entries): coverage at
    texel centres, island ids from UV-connected faces, overlap and degeneracy counts."""
    from ..baking.maps.utility import _islands
    from ..geometry.uv_rasterizer import rasterize
    from ..native_bridge.lifecycle import cpu_library
    from ..painting.stroke_raster import _batches
    lib = cpu_library()
    covered = np.zeros((n, n), bool)
    island = np.full((n, n), -1, np.int32)
    conflicts = np.zeros((n, n), bool)
    degenerate, island_base = 0, 0
    for target in entries:
        uv = target['uv'][target['tri_loops']]
        labels, _ = _islands(target)
        roots, island_of_face = np.unique(labels, return_inverse=True)
        unassigned = int(len(roots) > 0 and roots[0] < 0)
        island_of_face = np.where(labels >= 0, island_of_face.astype(np.int64) - unassigned + island_base, -1)
        island_base += len(roots) - unassigned
        of_triangle = island_of_face[target['tri_polygons']]
        du, dv = uv[:, 1] - uv[:, 0], uv[:, 2] - uv[:, 0]
        degenerate += int((np.abs(du[:, 0] * dv[:, 1] - du[:, 1] * dv[:, 0]) < 1e-12).sum())
        batches = _batches(uv, n)
        if lib is not None and hasattr(lib, 'op_uv_raster_ownership'):
            # One native pass over the same batches, with no per-texel arrays or sorting.
            from ..native_bridge.geometry import raster_ownership
            raster_ownership(lib, uv, n, of_triangle.astype(np.int32), [batch[0] for batch in batches if len(batch)],
                             island, covered, conflicts)
            continue
        for batch in batches:
            rows, cols, tri, _ = rasterize(uv[batch], n, unique=False)
            if not len(rows):
                continue
            owner = of_triangle[batch[tri]].astype(np.int32)
            flat = rows * n + cols
            # Later triangles win, as every other geometry raster of the add-on: write in
            # order, then flag texels whose earlier writers belonged to another island.
            first = np.full(n * n, -2, np.int32)
            first_order = np.unique(flat, return_index=True)[1]
            first[flat[first_order]] = owner[first_order]
            island.reshape(-1)[flat] = owner
            covered.reshape(-1)[flat] = True
            differing = first[flat] != owner
            conflicts.reshape(-1)[flat[differing]] = True
    ids, counts = np.unique(island[island >= 0], return_counts=True)
    sizes = {int(i): int(c) for i, c in zip(ids, counts)}
    diagnostics = {
        'islands': len(ids), 'valid_texels': int(covered.sum()), 'overlap_texels': int(conflicts.sum()),
        'degenerate_triangles': degenerate,
        'undersized_islands': sorted(i for i, c in sizes.items() if c < UNDERSIZED_TEXELS),
        'island_texels': sizes,
    }
    return Ownership(n, covered, island, diagnostics)


_cache = {}
CACHE_TEXELS = 3 * 4096 * 4096


def ownership(ts, n):
    """The set's ownership at ``n``, cached against its evaluated geometry, UVs and
    assignments (a UV edit or a slot change re-derives it)."""
    from ..geometry.seams import _placement
    from ..geometry.snapshots import targets
    scene = ts.id_data
    key = (scene.as_pointer(), ts.id, n, tuple(_placement(ref) for ref in ts.assignments if ref.object))
    found = _cache.pop(key, None)
    if found is None:
        with targets(ts) as entries:
            found = build_ownership(entries, n)
    _cache[key] = found
    while len(_cache) > 1 and sum(item.n ** 2 for item in _cache.values()) > CACHE_TEXELS:
        _cache.pop(next(iter(_cache)))
    return found


def clear_cache():
    _cache.clear()


# ---- nearest donors -------------------------------------------------------------------------

def _vertical_keys(covered, island, limit, r0, r1):
    """For rows ``r0:r1``, the packed key (d², island, index) of the nearest valid texel in
    each texel's own column within ``limit`` rows, or INF; valid texels' own key has d² = 0.
    Only the rows within ``limit`` of the band are read."""
    n = covered.shape[0]
    lo, hi = max(0, r0 - limit), min(n, r1 + limit)
    keys = np.empty((r1 - r0, n), np.int64)
    chunk = max(16, (1 << 19) // (hi - lo))     # columns per pass: int64 temporaries stay at 4 MiB
    rows = np.arange(lo, hi, dtype=np.int64)[:, None]
    inner = slice(r0 - lo, r1 - lo)
    for c0 in range(0, n, chunk):
        c1 = min(c0 + chunk, n)
        cov = covered[lo:hi, c0:c1]
        cols = np.arange(c0, c1, dtype=np.int64)[None, :]
        up = np.maximum.accumulate(np.where(cov, rows, -1), axis=0)[inner]
        down = np.minimum.accumulate(np.where(cov, rows, n)[::-1], axis=0)[::-1][inner]
        best = np.full((r1 - r0, c1 - c0), INF, np.int64)
        band_rows = rows[inner]
        for donor_row in (up, down):
            valid = (donor_row >= 0) & (donor_row < n)
            distance = np.abs(band_rows - donor_row)
            valid &= distance <= limit
            safe_row = np.clip(donor_row, 0, n - 1)
            donor_island = island[safe_row, cols].astype(np.int64)
            key = ((distance * distance).astype(np.int64) << D2_SHIFT) | (donor_island << ISLAND_SHIFT) | (safe_row * n + cols)
            best = np.minimum(best, np.where(valid, key, INF))
        keys[:, c0:c1] = best
    return keys


def _banded(covered, island, limit):
    """Exact nearest donor within ``limit`` pixels: (donor index or -1, d² as int16) per texel.
    Processed in row bands: the whole-image state is the two result arrays."""
    from ..native_bridge.lifecycle import cpu_library
    if (lib := cpu_library()) is not None:
        from ..native_bridge.geometry import padding_donors
        return padding_donors(lib, covered, island, int(limit))
    n = covered.shape[0]
    limit = int(limit)
    donor = np.full((n, n), -1, np.int32)
    d2 = np.full((n, n), -1, np.int16)
    band = max(32, (1 << 21) // n)      # rows per pass: the band's keys take 16 MiB
    limit2 = limit * limit
    for r0 in range(0, n, band):
        r1 = min(r0 + band, n)
        source = _vertical_keys(covered, island, limit, r0, r1)
        best = source.copy()
        for dx in range(1, limit + 1):
            shift = np.int64(dx * dx) << D2_SHIFT
            np.minimum(best[:, dx:], source[:, :-dx] + shift, out=best[:, dx:])
            np.minimum(best[:, :-dx], source[:, dx:] + shift, out=best[:, :-dx])
        found = best < INF
        distance = (best >> D2_SHIFT).astype(np.int64)
        found &= distance <= limit2
        donor[r0:r1] = np.where(found, (best & INDEX_MASK).astype(np.int32), -1)
        d2[r0:r1] = np.where(found, distance.astype(np.int16), -1)
    donor[covered] = -1
    d2[covered] = 0
    return donor, d2


def nearest_donors(covered, island, limit):
    """Donor texel (linear index) per empty texel within ``limit`` pixels of a valid one,
    -1 elsewhere and on valid texels; with ``limit='FILL'`` every empty texel gets a donor.
    Also returns d² in output pixels (approximate beyond ``FILL_BAND`` in fill mode)."""
    n = covered.shape[0]
    if island.shape != covered.shape or n != covered.shape[1]:
        raise ValueError('ownership arrays must be square and alike')
    if not covered.any():
        return np.full((n, n), -1, np.int32), np.full((n, n), -1, np.int32)
    if limit != 'FILL':
        if limit <= 0:
            return np.full((n, n), -1, np.int32), np.where(covered, 0, -1).astype(np.int32)
        if limit > FILL_BAND:
            raise ValueError(f'padding beyond {FILL_BAND} pixels is the fill mode')
        donor, d2 = _banded(covered, island, limit)
        return donor, d2.astype(np.int32)
    donor, d2 = _banded(covered, island, min(FILL_BAND, n))
    d2 = d2.astype(np.int32)
    factor = COARSE_STEP
    index = np.arange(n * n, dtype=np.int64).reshape(n, n)
    while True:
        unresolved = ~covered & (donor < 0)
        if not unresolved.any():
            break
        # One level coarser: each cell's representative is its lowest valid texel index; the
        # banded transform on the coarse grid reaches ``limit`` cells, and the loop continues
        # with 8× larger cells until the band spans the whole grid (nc·√2 ≤ limit) — so it
        # terminates with every empty texel resolved whenever any valid texel exists.
        nc = -(-n // factor)
        padded = n if nc * factor == n else nc * factor
        blocks = np.full((padded, padded), np.int64(n * n), np.int64)
        blocks[:n, :n] = np.where(covered, index, np.int64(n * n))
        blocks = blocks.reshape(nc, factor, nc, factor).min(axis=(1, 3))
        coarse_covered = blocks < n * n
        representative = np.where(coarse_covered, blocks, 0)
        coarse_island = np.where(coarse_covered, island.reshape(-1)[representative], -1).astype(np.int32)
        limit = min(FILL_BAND, int(np.ceil(nc * np.sqrt(2.0))) + 1)   # the band spans the grid once it can
        coarse_donor, coarse_d2 = _banded(coarse_covered, coarse_island, limit)
        coarse_d2 = coarse_d2.astype(np.int32)
        # An occupied cell resolves its own empty texels with its representative; an empty
        # cell takes the representative of its nearest occupied cell.
        fine_donor = np.where(coarse_covered, representative,
                              np.where(coarse_donor >= 0, representative.reshape(-1)[np.clip(coarse_donor, 0, None)], -1))
        expanded = np.repeat(np.repeat(fine_donor, factor, axis=0), factor, axis=1)[:n, :n]
        expanded_d2 = np.repeat(np.repeat(coarse_d2 * (factor * factor), factor, axis=0), factor, axis=1)[:n, :n]
        take = unresolved & (expanded >= 0)
        donor[take] = expanded[take].astype(np.int32)
        d2[take] = np.minimum(expanded_d2[take], np.iinfo(np.int32).max).astype(np.int32)
        if nc == 1:
            break
        factor *= COARSE_STEP
    return donor, d2


def unresolved_texels(covered, donor):
    """Empty texels without a donor (must be 0 in fill mode when any texel is valid)."""
    return int((~covered & (donor < 0)).sum())


def donors_valid(covered, donor):
    """Every donor index points at a valid texel of the same image."""
    chosen = donor[donor >= 0]
    return bool(chosen.size == 0 or ((chosen < covered.size).all() and covered.ravel()[chosen].all()))


def reference_donors(covered, island, limit):
    """Brute force oracle for small images: every empty texel against every valid texel."""
    n = covered.shape[0]
    donor = np.full((n, n), -1, np.int32)
    d2 = np.full((n, n), -1, np.int32)
    valid = np.flatnonzero(covered.ravel())
    if not len(valid):
        return donor, d2
    vy, vx = valid // n, valid % n
    tie_key = (island.ravel()[valid].astype(np.int64) << ISLAND_SHIFT) | valid.astype(np.int64)
    empty = np.flatnonzero(~covered.ravel())
    limit2 = None if limit == 'FILL' else int(limit) * int(limit)
    for start in range(0, len(empty), 2048):
        chunk = empty[start:start + 2048]
        ey, ex = chunk // n, chunk % n
        distance = ((ey[:, None] - vy[None, :]) ** 2 + (ex[:, None] - vx[None, :]) ** 2).astype(np.int64)
        best_d2 = distance.min(axis=1)
        # Among the nearest, the lowest (island, index): the same order the production key uses.
        contenders = np.where(distance == best_d2[:, None], tie_key[None, :], np.int64(1) << np.int64(62))
        best = contenders.min(axis=1)
        take = np.ones(len(chunk), bool) if limit2 is None else best_d2 <= limit2
        if limit2 is not None and limit2 <= 0:
            take[:] = False
        donor.reshape(-1)[chunk[take]] = (best[take] & INDEX_MASK).astype(np.int32)
        d2.reshape(-1)[chunk[take]] = np.minimum(best_d2[take], np.iinfo(np.int32).max).astype(np.int32)
    d2[covered] = 0
    return donor, d2


def extend(values, donor, in_place=False):
    """``values`` (n, n, C) with every texel that has a donor replaced by its donor's value.
    ``in_place``: ``values`` may be overwritten (a donor never has a donor of its own), which
    saves the copy and, natively, every temporary: at 4K a map is 256 MiB."""
    values = np.asarray(values, np.float32)
    from ..native_bridge.lifecycle import cpu_library
    lib = cpu_library()
    if (lib is not None and hasattr(lib, 'op_padding_extend') and values.ndim == 3
            and values.flags.c_contiguous and donor.dtype == np.int32 and donor.flags.c_contiguous):
        from ..native_bridge.geometry import padding_extend
        return padding_extend(lib, values, donor, values if in_place else np.empty_like(values))
    flat = values.reshape(-1, values.shape[-1])
    targets = np.flatnonzero(donor.ravel() >= 0)
    if not len(targets):
        return values
    out = values.copy()
    out.reshape(-1, values.shape[-1])[targets] = flat[donor.ravel()[targets]]
    return out


# ---- coverage-aware resampling ------------------------------------------------------------------

def resample(values, covered, n_out, vector=False):
    """``values`` (n, n, C) at the source resolution to ``n_out``, weighting by source
    coverage so empty texels never enter a valid sample: a box filter over the valid
    source texels when shrinking, a coverage-weighted bilinear fetch when enlarging.
    Output texels that see no valid source keep the plain filter of the evaluated image
    (the layer defaults outside every island, as earlier releases exported them).
    Returns (values_out, has_data) where has_data marks output texels that received any
    valid source. Tangent normals (``vector``) are filtered as decoded vectors and renormalised."""
    values = np.asarray(values, np.float32)
    n = values.shape[0]
    if n_out == n:
        return values if not vector else _encode(values, decode=True), covered.copy()
    if vector:
        values = values.copy()
        values[..., :3] = values[..., :3] * 2 - 1
    weight = covered.astype(np.float32)
    if n_out < n:
        if n % n_out:
            raise ValueError('export resolutions are powers of two')
        factor = n // n_out
        channels = values.shape[-1]
        summed = np.zeros((n_out, n_out, channels), np.float32)
        plain = np.zeros((n_out, n_out, channels), np.float32)
        total = np.zeros((n_out, n_out), np.float32)
        band = max(factor, (1 << 20) // n) // factor * factor
        for r0 in range(0, n, band):
            r1 = min(r0 + band, n)
            rows = values[r0:r1]
            block = rows * weight[r0:r1, :, None]
            summed[r0 // factor:r1 // factor] += block.reshape((r1 - r0) // factor, factor, n_out, factor, channels).sum(axis=(1, 3))
            plain[r0 // factor:r1 // factor] += rows.reshape((r1 - r0) // factor, factor, n_out, factor, channels).sum(axis=(1, 3))
            total[r0 // factor:r1 // factor] += weight[r0:r1].reshape((r1 - r0) // factor, factor, n_out, factor).sum(axis=(1, 3))
        has_data = total > 0
        out = np.divide(summed, total[..., None], out=plain / float(factor * factor), where=has_data[..., None])
    else:
        if n_out % n:
            raise ValueError('export resolutions are powers of two')
        channels = values.shape[-1]
        out = np.empty((n_out, n_out, channels), np.float32)
        has_data = np.empty((n_out, n_out), bool)
        flat = values.reshape(-1, channels)
        flat_weight = weight.reshape(-1)
        xs = (np.arange(n_out, dtype=np.float32) + .5) / n_out * n - .5
        ix = np.floor(xs).astype(np.int64)
        fx = (xs - ix).astype(np.float32)
        band = max(1, (1 << 20) // n_out)
        for r0 in range(0, n_out, band):
            r1 = min(r0 + band, n_out)
            ys = (np.arange(r0, r1, dtype=np.float32) + .5) / n_out * n - .5
            iy = np.floor(ys).astype(np.int64)
            fy = (ys - iy).astype(np.float32)
            accumulated = np.zeros((r1 - r0, n_out, channels), np.float32)
            plain = np.zeros((r1 - r0, n_out, channels), np.float32)
            total = np.zeros((r1 - r0, n_out), np.float32)
            for dy, wy in ((0, 1 - fy), (1, fy)):
                yy = np.clip(iy + dy, 0, n - 1)[:, None]
                for dx, wx in ((0, 1 - fx), (1, fx)):
                    xx = np.clip(ix + dx, 0, n - 1)[None, :]
                    linear = (yy * n + xx).ravel()
                    fetched = flat[linear].reshape(r1 - r0, n_out, channels)
                    bilinear = (wy[:, None] * wx[None, :])
                    w = bilinear * flat_weight[linear].reshape(r1 - r0, n_out)
                    accumulated += fetched * w[..., None]
                    plain += fetched * bilinear[..., None]
                    total += w
            has_data[r0:r1] = total > 0
            out[r0:r1] = np.divide(accumulated, total[..., None], out=plain, where=has_data[r0:r1, :, None])
    if vector:
        out = _encode(out)
    return out, has_data


def _encode(values, decode=False):
    """Normalized and encoded tangent normals (decoded from [0, 1] first when ``decode``)."""
    from ..native_bridge.lifecycle import cpu_library
    lib = cpu_library()
    if lib is not None and values.dtype == np.float32 and values.shape[-1] in (3, 4) and hasattr(lib, 'op_normal_encode'):
        from ..native_bridge.pixels import normal_encode
        return normal_encode(lib, values, decode)   # the arithmetic below, in one pass
    if decode:
        values = values.copy()
        values[..., :3] = values[..., :3] * 2 - 1
    return _encode_numpy(values)


def _encode_numpy(values):
    xyz = values[..., :3]
    length = np.linalg.norm(xyz, axis=-1, keepdims=True)
    xyz = np.divide(xyz, length, out=np.zeros_like(xyz), where=length > 1e-8)
    xyz[..., 2] = np.where(length[..., 0] > 1e-8, xyz[..., 2], 1.0)
    out = values.copy()
    out[..., :3] = xyz * .5 + .5
    return out


# ---- diagnostics ---------------------------------------------------------------------------------

def gutters(ownership, donor, d2, padding):
    """Where padding fronts of different islands meet: per island the narrowest gutter (the
    distance between the two islands along the contact, in output pixels) and the list of
    islands whose gutter is narrower than the padding; also which islands got no room at all."""
    n = ownership.n
    island = ownership.island
    owner = np.where(ownership.covered, island, np.where(donor >= 0, island.reshape(-1)[np.clip(donor, 0, None)], -1))
    distance = np.sqrt(np.maximum(d2, 0).astype(np.float32))
    narrowest = {}
    for before, after in (((slice(0, n - 1),), (slice(1, n),)), ((Ellipsis, slice(0, n - 1)), (Ellipsis, slice(1, n)))):
        a, b = owner[before], owner[after]   # neighbours down the rows, then across the columns
        contact = (a >= 0) & (b >= 0) & (a != b)
        if not contact.any():
            continue
        da = distance[before][contact]
        db = distance[after][contact]
        width = da + db + 1.0
        for side in (a[contact], b[contact]):
            for identifier in np.unique(side):
                value = float(width[side == identifier].min())
                narrowest[int(identifier)] = min(narrowest.get(int(identifier), np.inf), value)
    threshold = float(padding) if padding not in ('FILL', 0) else float(automatic_padding(n))
    tight = sorted(i for i, w in narrowest.items() if w < 2 * threshold)
    return {'narrowest_gutter_px': {i: round(w, 1) for i, w in sorted(narrowest.items())},
            'tight_islands': tight, 'padding_px': threshold}
