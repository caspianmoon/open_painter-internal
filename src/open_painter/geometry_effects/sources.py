"""Where a geometry effect goes: placement fields derived from what the material shows.

A ``Placement`` is a density raster over the set's UV space (0–1), an optional
direction raster (which way a fragment faces, in UV space) and, for spot
placement, one point per painted blob. Sources:

- ``MATERIAL``: the outcome the effect materialises — rust flakes where the
  Material Aging folder shows corrosion, peel fragments along the edge of the
  coating loss, moss where growth is, splinters where wood checks and dents —
  and, on a set without aging, the mesh features that stand for them (edges,
  cavities, upward faces, the ground);
- ``MASK``: the active layer's mask, the way the effects worked before 0.10.1;
- ``SPOTS``: the blobs of the active layer's mask, one instance each, sized by
  the blob (bolts, rivets, rocks where the artist painted dots);
- ``SURFACE``: everywhere the set is assigned (scales, bark).

Everything is evaluated by the CPU reference path at a bounded resolution, so a
placement costs a mask evaluation, not a full-set bake.
"""
from dataclasses import dataclass, field

import numpy as np

# Aging roles each effect reads; (role, weight) pairs summed and clipped.
OUTCOMES = {
    "RUST": (("corrosion", 1.0), ("oxidation", 0.5)),
    "MOSS": (("biological", 1.0),),
    "SPLINTERS": (("wood_checking", 1.0), ("impact", 0.8)),
    "PEEL": (("paint", 1.0),),          # intact paint coverage; the loss is its complement
}
DEFAULT_SOURCE = {"RUST": "MATERIAL", "MOSS": "MATERIAL", "PEEL": "MATERIAL", "SPLINTERS": "MATERIAL", "SNOW": "MATERIAL",
                  "BOLT": "SPOTS", "SCALE": "SURFACE", "BARK": "SURFACE", "ROCK": "MASK"}
SOURCES = (("MATERIAL", "What the material shows", "The aging outcome this effect materialises; mesh features without aging"),
           ("MASK", "Active layer mask", "Density from the active layer's mask"),
           ("SPOTS", "Painted spots", "One instance per blob of the active layer's mask, sized by the blob"),
           ("SURFACE", "Whole surface", "Everywhere the set is assigned"))
MAX_FIELD = 1024


@dataclass
class Placement:
    density: np.ndarray                     # (n, n) float32, 0–1
    source: str
    label: str
    direction: np.ndarray = None            # (n, n, 2) unit UV-space direction a fragment faces, or None
    spots: list = field(default_factory=list)   # [(world position, world normal, diameter in metres, uv)]
    layer_id: str = ""
    color_offset: float = 0.0               # texels along ``direction`` at which the surface colour is read
    color: tuple = None                     # one colour for every piece (a chip is the paint that left), or None to sample

    @property
    def n(self):
        return self.density.shape[0]


def resolution(ts):
    return min(int(ts.resolution), MAX_FIELD)


def _snapshot(ts, n):
    from ..evaluation.snapshots import capture
    return capture(ts, n / int(ts.resolution)).replaced(resolution=str(n))


def mask_field(ts, layer, n):
    """The layer's mask coverage at ``n`` (its stack, post filters), like the published mask."""
    from ..evaluation.mask_eval import evaluate_masks
    snapshot = _snapshot(ts, n)
    captured = next((l for l in snapshot.layers if l.id == layer.id), None)
    if captured is None:
        raise ValueError("The layer no longer exists")
    if not captured.masks:
        return np.ones((n, n), np.float32)
    result = np.empty((n, n), np.float32)
    cache, geometry = {}, {}
    for y0 in range(0, n, 128):
        y1 = min(n, y0 + 128)
        result[y0:y1] = evaluate_masks(snapshot, captured, n, y0, y1, cache, geometry)
    return np.clip(result, 0, 1)


def channel_raster(ts, channel, n):
    """The channel composite at ``n`` as straight RGB, without publishing an image."""
    from ..constants import DEFAULTS
    from ..evaluation.composite import Composer
    snapshot = _snapshot(ts, n)
    composer = Composer(snapshot, channel, n, {}, {})
    result = np.empty((n, n, 3), np.float32)
    for y0 in range(0, n, 64):
        y1 = min(n, y0 + 64)
        rgb, _ = composer.compose("", y0, y1, DEFAULTS[channel][:composer.components], set(), 1)
        rgb = np.asarray(rgb, np.float32)          # a constant when nothing is stacked, scalar rows for scalar channels
        if rgb.ndim < 3:
            rgb = np.broadcast_to(rgb, (y1 - y0, n, rgb.shape[-1] if rgb.ndim else 1))
        if rgb.shape[-1] == 1:
            rgb = np.broadcast_to(rgb, (y1 - y0, n, 3))
        result[y0:y1] = np.clip(rgb[..., :3], 0, 1)
    return result


def features(ts, n):
    """Mesh features of the assigned faces at ``n``: ``valid``, ``up`` and ``low`` come from the
    geometry itself; ``cavity`` and ``edges`` from the baked AO and Curvature maps when they exist."""
    from ..evaluation.blend_modes import sample_uv
    from ..evaluation.mask_eval import map_pixels
    from ..painting.projection import geometry
    positions, normals, valid = geometry(ts, n, coverage=True)
    up = np.clip(normals[..., 2], 0, 1).astype(np.float32)
    down = np.clip(-normals[..., 2], 0, 1).astype(np.float32)
    z = positions[..., 2]
    if valid.any():
        low_z, high_z = float(z[valid].min()), float(z[valid].max())
        low = np.clip((high_z - z) / max(high_z - low_z, 1e-6), 0, 1).astype(np.float32)
    else:
        low = np.zeros((n, n), np.float32)
    u, v = np.meshgrid((np.arange(n, dtype=np.float32) + .5) / n, (np.arange(n, dtype=np.float32) + .5) / n)
    cache = {}
    result = {"valid": valid, "up": up, "down": down, "low": low, "cavity": None, "edges": None}
    for name, kind in (("cavity", "AO"), ("edges", "CURVATURE")):
        if any(m.kind == kind and m.image and all(m.image.size) for m in ts.baked_maps):
            values = sample_uv(map_pixels(ts, kind, cache), u, v)[..., 0]
            result[name] = np.clip(1 - values if kind == "AO" else values, 0, 1).astype(np.float32)
    return result


def _blur(values, radius):
    """Box blur by summed-area table (radius in texels); edges clamp."""
    if radius <= 0:
        return values
    padded = np.pad(values.astype(np.float32), radius, mode="edge")
    table = np.zeros((padded.shape[0] + 1, padded.shape[1] + 1), np.float64)
    table[1:, 1:] = padded.cumsum(0).cumsum(1)
    size = 2 * radius + 1
    n0, n1 = values.shape
    total = (table[size:size + n0, size:size + n1] - table[:n0, size:size + n1]
             - table[size:size + n0, :n1] + table[:n0, :n1])
    return (total / (size * size)).astype(np.float32)


def _grow(binary, radius):
    """Dilate a boolean raster by ``radius`` texels (square structuring element)."""
    return _blur(binary.astype(np.float32), radius) > 1e-6


def boundary_band(region, width):
    """Texels of ``region`` (bool) within ``width`` texels of its complement: the edge of a
    painted area, seen from the side that lost the paint."""
    return region & _grow(~region, width)


def direction_toward(values, radius):
    """Unit UV-space direction toward higher ``values`` (blurred), per texel; zero where flat."""
    smooth = _blur(values, radius)
    dv, du = np.gradient(smooth)
    length = np.sqrt(du * du + dv * dv)
    with np.errstate(invalid="ignore", divide="ignore"):
        direction = np.stack((du / length, dv / length), axis=-1)
    direction[length < 1e-6] = 0
    return direction.astype(np.float32)


def label_runs(binary):
    """Connected components (8-connected) of a boolean raster by run-length union-find.

    Returns (labels int32 with 0 = background, count). Rows are decomposed into
    runs, so the Python-level work scales with the number of runs, not texels.
    """
    n0, n1 = binary.shape
    labels = np.zeros((n0, n1), np.int32)
    parent = [0]

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        a, b = find(a), find(b)
        if a != b:
            parent[max(a, b)] = min(a, b)

    previous = []      # (start, end, label) of the previous row
    for row in range(n0):
        line = binary[row]
        if not line.any():
            previous = []
            continue
        edges = np.flatnonzero(np.diff(np.concatenate(([False], line, [False])).astype(np.int8)))
        starts, ends = edges[0::2], edges[1::2]
        current = []
        for start, end in zip(starts.tolist(), ends.tolist()):
            label = None
            for p_start, p_end, p_label in previous:
                if p_start <= end and start <= p_end:      # 8-connected: touching corners count
                    if label is None:
                        label = p_label
                    else:
                        union(label, p_label)
            if label is None:
                parent.append(len(parent))
                label = len(parent) - 1
            labels[row, start:end] = label
            current.append((start, end, label))
        previous = current
    if len(parent) == 1:
        return labels, 0
    roots = np.array([find(i) for i in range(len(parent))], np.int32)
    unique, compact = np.unique(roots, return_inverse=True)
    lookup = compact.astype(np.int32)       # root 0 (background) maps to 0
    lookup[0] = 0
    return lookup[labels], int(len(unique) - 1)


def spots(ts, density, n, threshold=.5, minimum=3):
    """One (world position, normal, diameter) per blob of ``density`` above ``threshold``."""
    from ..painting.projection import geometry
    from .curves import _texel_size
    labels, count = label_runs(density > threshold)
    if not count:
        return []
    positions, normals, valid = geometry(ts, n, coverage=True)
    texel = _texel_size(ts, n)
    flat = labels.ravel()
    order = np.argsort(flat, kind="stable")
    sorted_labels = flat[order]
    bounds = np.searchsorted(sorted_labels, np.arange(1, count + 2))
    result = []
    for index in range(count):
        texels = order[bounds[index]:bounds[index + 1]]
        if len(texels) < minimum:
            continue
        rows, cols = np.divmod(texels, n)
        good = valid[rows, cols]
        if not good.any():
            continue
        position = positions[rows[good], cols[good]].mean(axis=0)
        normal = normals[rows[good], cols[good]].mean(axis=0)
        normal /= max(float(np.linalg.norm(normal)), 1e-8)
        radius = float(np.sqrt(len(texels) / np.pi)) * texel
        uv = np.array(((cols.mean() + .5) / n, (rows.mean() + .5) / n), np.float32)
        result.append((position.astype(np.float32), normal.astype(np.float32), 2 * radius, uv))
    return result


def _outcome(ts, kind, n):
    """Density from the Material Aging folder's role layers (coverage × opacity), or None."""
    from ..aging import engine
    folder = engine.folder_of(ts)
    if folder is None or kind not in OUTCOMES:
        return None
    roles = engine.roles_of(ts, folder)
    total = None
    for role, weight in OUTCOMES[kind]:
        layer = roles.get(role)
        if layer is None:
            continue
        coverage = mask_field(ts, layer, n) * float(layer.opacity) * weight
        total = coverage if total is None else total + coverage
    return None if total is None else np.clip(total, 0, 1)


def material_placement(ts, kind, n):
    """The MATERIAL source: aging outcomes when the set is aged, mesh features otherwise."""
    feats = features(ts, n)
    valid = feats["valid"].astype(np.float32)
    cavity, edges, up, down, low = feats["cavity"], feats["edges"], feats["up"], feats["down"], feats["low"]
    outcome = _outcome(ts, kind, n)
    direction, color_offset, color = None, 0.0, None
    if kind == "PEEL":
        if outcome is not None:
            from ..aging import engine
            from ..core.layer_stack import channel_for
            paint_layer = engine.roles_of(ts, engine.folder_of(ts)).get("paint")
            prop = channel_for(paint_layer, "BASE_COLOR") if paint_layer is not None else None
            if prop is not None:
                color = tuple(float(c) for c in prop.color[:3])     # the chip's face is the paint as it is now
            # Fragments sit on the bare side of the paint edge and hinge toward the paint that holds.
            width = max(2, n // 96)
            intact = _blur(outcome, max(1, width // 2)) > .5      # patches, not speckles, have edges
            density = boundary_band(~intact, width).astype(np.float32) * valid
            direction = direction_toward(outcome, width)
            color_offset = width * 2.0      # the chip's painted face is the paint beside it, not the bare metal under it
            label = "edge of the coating loss"
        else:
            density = (edges if edges is not None else valid) * valid
            label = "mesh edges"
    elif kind == "RUST":
        if outcome is not None:
            density = outcome * (.6 + .4 * up)
            label = "corrosion and surface rust"
        else:
            parts = [p for p in (edges, cavity) if p is not None]
            density = (np.clip(sum(parts) * .7, 0, 1) if parts else valid * .5) * valid
            label = "mesh edges and cavities"
    elif kind == "MOSS":
        # Growth sits where the material shows it, in hollows, rarely on the underside.
        shade = (1.0 - .7 * down)
        if outcome is not None:
            density = outcome * (1.0 if cavity is None else .5 + .5 * cavity) * shade
            label = "biological growth"
        else:
            base = cavity if cavity is not None else valid * .35
            density = base * (.6 + .4 * (1 - up)) * (.7 + .3 * low) * shade * valid
            label = "cavities, shade and the ground"
    elif kind == "SPLINTERS":
        if outcome is not None:
            density = outcome * (1.0 if edges is None else .5 + .5 * edges)
            label = "checking and dents"
        else:
            density = (edges if edges is not None else valid * .3) * valid
            label = "mesh edges"
    elif kind == "SNOW":
        density = valid * up ** 2.5 * (1.0 if cavity is None else .6 + .6 * cavity)   # settles on top, drifts fill hollows
        label = "upward faces"
    else:
        density = valid
        label = "the whole surface"
    # The state decides where; the density the artist chose decides how many. A faint outcome
    # (a little rust, early growth) is normalised so the strongest places reach the full density.
    density = np.clip(density, 0, 1).astype(np.float32)
    covered = density[density > 1e-4]
    if len(covered):
        density = np.clip(density / max(float(np.percentile(covered, 98)), 1e-4), 0, 1).astype(np.float32)
        density[density < 1e-3] = 0
    return Placement(density, "MATERIAL", label, direction, color_offset=color_offset, color=color)


def placement(ts, kind, source, layer=None, n=None):
    """The placement for ``kind`` from ``source``; ``layer`` is the active layer for MASK and SPOTS."""
    n = n or resolution(ts)
    if source == "MATERIAL":
        return material_placement(ts, kind, n)
    if source == "SURFACE":
        return Placement(features(ts, n)["valid"].astype(np.float32), "SURFACE", "the whole surface")
    if layer is None:
        raise ValueError("Select the layer whose mask places the geometry")
    density = mask_field(ts, layer, n)
    if source == "MASK":
        return Placement(density, "MASK", f"the mask of {layer.name}", layer_id=layer.id)
    if source == "SPOTS":
        found = spots(ts, density, n)
        if not found:
            raise ValueError(f"No spots in the mask of {layer.name}: paint dots where the pieces go")
        return Placement(density, "SPOTS", f"{len(found)} spots of {layer.name}", spots=found, layer_id=layer.id)
    raise ValueError("Unknown placement source: " + str(source))
