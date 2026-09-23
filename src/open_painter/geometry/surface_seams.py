"""Seam-aware atlas accumulation: UV islands are storage, adjacency is physical.

Two texels on different UV islands can be neighbouring surface samples. The paired
seam records already describe which UV edges are the same physical edge and how the
two sides are oriented, so a similarity transform between the sides carries fused
appearance across an island boundary in the direction the surface actually
continues — including mirrored, rotated and separately placed islands.

This never invents appearance: it only moves values that fusion already produced
for a physically adjacent surface sample. Nothing here reads Blender data.
"""
import numpy as np

BAND = 3  # pad rings carried across a seam; wider bands drift away from the shared edge


def _side(edge, center):
    """Unit edge direction and the perpendicular pointing into that island's face."""
    a, b = np.asarray(edge, np.float64)
    direction = b - a
    length = float(np.linalg.norm(direction))
    if length < 1e-12:
        return None
    direction /= length
    perpendicular = np.array([-direction[1], direction[0]])
    if float(perpendicular @ (np.asarray(center, np.float64) - a)) < 0:
        perpendicular = -perpendicular
    return a, direction, perpendicular, length


def continuation(seam):
    """The similarity carrying island A across the shared edge into island B.

    It maps a point outside A's face, past the shared edge, onto the matching point
    inside B's face, and a point inside A onto B's own outside. Both directions are
    returned so each island can donate to the other.
    """
    first = _side(seam['side_a']['uv_edge'], seam['side_a']['uv_center'])
    second = _side(seam['side_b']['uv_edge'], seam['side_b']['uv_center'])
    if first is None or second is None:
        return None
    a0, ea, na, la = first
    b0, eb, nb, lb = second
    scale = lb / la
    # Endpoints already match physically, so the linear part is fixed by the two
    # edge directions; its handedness follows from which side each face lies on.
    cos = float(ea @ eb)
    sin = float(ea[0] * eb[1] - ea[1] * eb[0])
    rotation = np.array([[cos, -sin], [sin, cos]])
    flip = np.array([[1., 0.], [0., -1.]])
    reflection = np.stack((ea, na)).T @ flip @ np.stack((ea, na))
    # Pick the handedness that sends A's interior to B's exterior: crossing the
    # shared edge out of one face enters the other, however the islands are placed.
    forward = rotation * scale
    for linear in (rotation * scale, rotation @ reflection * scale):
        if float((linear @ na) @ nb) < 0:
            forward = linear
            break
    inverse = np.linalg.inv(forward)
    return dict(forward=forward, inverse=inverse, a0=a0, b0=b0, ea=ea, eb=eb,
                na=na, nb=nb, length_a=la, length_b=lb)


def _pairs(transform, resolution, band):
    """Gutter texels on each side and the covered texel that physically continues it."""
    rows, cols, donor_rows, donor_cols = [], [], [], []
    for source, target, origin, image_origin, direction, outward, length, linear in (
            ('a', 'b', transform['a0'], transform['b0'], transform['ea'], -transform['na'],
             transform['length_a'], transform['forward']),
            ('b', 'a', transform['b0'], transform['a0'], transform['eb'], -transform['nb'],
             transform['length_b'], transform['inverse'])):
        steps = max(2, int(np.ceil(length * resolution * 2)))
        t = (np.arange(steps) + .5) / steps * length
        along = origin + direction * t[:, None]
        for ring in range(band):
            offset = (ring + .5) / resolution
            points = along + outward * offset
            donors = image_origin + (linear @ (points - origin).T).T
            rows.append(points[:, 1])
            cols.append(points[:, 0])
            donor_rows.append(donors[:, 1])
            donor_cols.append(donors[:, 0])
    return rows, cols, donor_rows, donor_cols


def donors(seams, resolution, *, band=BAND, limit=200000):
    """Texel donor pairs across every paired seam, in atlas row/column indices."""
    resolution = int(resolution)
    rows, cols, donor_rows, donor_cols = [], [], [], []
    for seam in seams:
        transform = continuation(seam)
        if transform is None:
            continue
        chunk = _pairs(transform, resolution, band)
        for store, values in zip((rows, cols, donor_rows, donor_cols), chunk):
            store.extend(values)
        if sum(len(item) for item in rows) > limit:
            break
    if not rows:
        empty = np.empty(0, np.int64)
        return empty, empty, empty, empty
    def index(values):
        return np.clip((np.concatenate(values) * resolution).astype(np.int64), 0, resolution - 1)
    return index(rows), index(cols), index(donor_rows), index(donor_cols)


def carry(values, covered, seams, resolution, *, band=BAND):
    """Copy fused appearance into uncovered texels that continue a covered island.

    Only texels the fusion left uncovered are written, and only from a donor texel
    fusion did cover. Returns the number of texels carried across seams.
    """
    rows, cols, donor_rows, donor_cols = donors(seams, resolution, band=band)
    if not len(rows):
        return 0
    usable = covered[donor_rows, donor_cols] & ~covered[rows, cols]
    if not usable.any():
        return 0
    rows, cols = rows[usable], cols[usable]
    donor_rows, donor_cols = donor_rows[usable], donor_cols[usable]
    # A gutter texel can face several seams; the first donor wins deterministically.
    flat = rows * resolution + cols
    order = np.argsort(flat, kind='stable')
    _, first = np.unique(flat[order], return_index=True)
    keep = order[first]
    rows, cols = rows[keep], cols[keep]
    for array in values:
        array[rows, cols] = array[donor_rows[keep], donor_cols[keep]]
    covered[rows, cols] = True
    return int(len(keep))
