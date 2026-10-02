"""Opt-in physical procedural coordinates, shared by CPU and shader fields.

Coordinates are rasterized through assigned UV triangles, not inferred from UV
area. This preserves exact world distance under object scale and multiple mesh
extents. The shader uses the same scalar boundary; no second surface evaluator.
"""
import numpy as np

FIELDS = ("material_mapping_version", "material_period_m", "material_meters_per_unit",
          "material_frame", "material_origin", "material_axis_u", "material_axis_v",
          "material_image_extent_m", "material_source_id", "material_directional")


def identity(owner):
    return tuple(tuple(value) if hasattr(value, "__len__") and not isinstance(value, str) else value
                 for value in (getattr(owner, name, 0 if name == "material_mapping_version" else "") for name in FIELDS))


def enabled(owner):
    return getattr(owner, "material_mapping_version", 0) == 1


def frame_axes(u, v):
    u, v = np.asarray(u, np.float64), np.asarray(v, np.float64)
    u /= max(np.linalg.norm(u), 1e-15)
    v = v - u * np.dot(u, v)
    if np.linalg.norm(v) < 1e-8 or np.linalg.norm(u) < .5:
        raise ValueError("Material frame axes must be nonzero and independent")
    v /= np.linalg.norm(v)
    return np.stack((u, v, np.cross(u, v)))


def component_frames(positions, triangles):
    """Long-axis frames of connected members, without altering any mesh data.

    Principal directions describe geometry only; they do not assert semantic
    wood membership. Repeated symmetric members use deterministic axis signs.
    """
    parent = np.arange(len(positions))
    def root(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index
    for a, b, c in triangles:
        ra = root(a)
        for vertex in (b, c):
            rb = root(vertex)
            if ra != rb:
                parent[rb] = ra
    groups = {}
    for vertex in np.unique(triangles):
        groups.setdefault(root(vertex), []).append(vertex)
    frames = {}
    for key, vertices in groups.items():
        points = positions[vertices]
        origin = points.mean(axis=0)
        _, _, right = np.linalg.svd(points - origin, full_matrices=False)
        along = right[0]
        if along[np.argmax(np.abs(along))] < 0:
            along = -along
        # Fix transverse orientation from the least-parallel world axis. This
        # avoids random eigenvectors when a beam has a square cross-section.
        basis = np.eye(3)[np.argmin(np.abs(along))]
        across = np.cross(along, basis)
        across /= max(np.linalg.norm(across), 1e-15)
        axes = frame_axes(across, along)
        frames[key] = (origin, axes)
    return [(frames[root(int(tri[0]))]) for tri in triangles]


def raster(ts, n, owner, cache):
    key = ("material-coordinate-v1", getattr(cache, "geometry_stamp", None), n, owner.material_frame, tuple(owner.material_origin),
           tuple(owner.material_axis_u), tuple(owner.material_axis_v))
    if key in cache:
        return cache[key]
    from ..geometry.snapshots import targets
    from ..geometry.uv_rasterizer import rasterize, interpolate
    positions = np.zeros((n, n, 3), np.float32)
    normals = np.zeros_like(positions)
    normals[..., 2] = 1
    with targets(ts) as entries:
        for entry in entries:
            world = entry["positions"]
            triangles = entry["tri_vertices"]
            tri_points = world[triangles]
            tri_normals = entry["world_normals"][entry["tri_loops"]]
            if owner.material_frame == "COMPONENT":
                frames = component_frames(world, triangles)
                origins = np.asarray([frame[0] for frame in frames])
                axes = np.asarray([frame[1] for frame in frames])
                tri_points = np.einsum("tci,tji->tcj", tri_points - origins[:, None, :], axes)
                tri_normals = np.einsum("tci,tji->tcj", tri_normals, axes)
            else:
                if owner.material_frame == "OBJECT":
                    matrix = np.asarray(entry["object"].matrix_world, np.float64)
                    # Rotation only: distances remain world lengths despite
                    # non-unit/nonuniform object transforms.
                    axes = frame_axes(matrix[:3, 0], matrix[:3, 1])
                    origin = matrix[:3, 3]
                else:
                    axes = frame_axes(owner.material_axis_u, owner.material_axis_v)
                    origin = np.asarray(owner.material_origin)
                tri_points = (tri_points - origin) @ axes.T
                tri_normals = tri_normals @ axes.T
            from ..native_bridge.lifecycle import cpu_library
            lib = cpu_library()
            if lib is not None and hasattr(lib, 'op_uv_raster_interpolate'):
                # One native pass, with no per-texel arrays: the maps the code below writes.
                from ..native_bridge.geometry import raster_interpolate
                raster_interpolate(lib, entry["uv"][entry["tri_loops"]], n,
                                   [(tri_points, positions), (tri_normals, normals)])
                continue
            rows, cols, tri, weights = rasterize(entry["uv"][entry["tri_loops"]], n)
            positions[rows, cols] = interpolate(weights, tri_points[tri])
            normals[rows, cols] = interpolate(weights, tri_normals[tri])
    cache[key] = positions, normals
    return positions, normals


def evaluate_points(owner, positions, normals):
    """Evaluate the declared periodic field at already framed world positions."""
    from ..texture_engine.procedurals import evaluate
    multiplier = float(owner.material_meters_per_unit) / max(float(owner.material_period_m), 1e-12)
    coords = np.asarray(positions) * multiplier
    # Weave's legacy primitive has a two-cell alternating repeat. Version1
    # calls its *complete repeat* one period and uses a periodic analytic weave
    # without nonperiodic random variation, so measured 1mm really repeats1mm.
    def sample(u, v):
        if owner.procedural == "FABRIC_WEAVE":
            x, y = 2 * u, 2 * v
            fx, fy = x % 1, y % 1
            warp = np.sin(fx * np.pi) ** .45
            weft = np.sin(fy * np.pi) ** .45
            return np.where((np.floor(x) + np.floor(y)) % 2 == 0,
                            warp * (.8 + .2 * weft), weft * (.8 + .2 * warp))
        return evaluate(owner.procedural, u, v, scale=1, seed=owner.procedural_seed)
    if owner.procedural in {"WOOD_GRAIN", "WOOD_PORES"}:
        # Every frame has an explicit Along axis v, whether supplied by the
        # artist or a connected member. Cylinder coordinates retain that grain
        # direction on side faces instead of swapping v into primitive u.
        return np.asarray(sample(np.sqrt(coords[..., 0] ** 2 + coords[..., 2] ** 2), coords[..., 1]), np.float32)
    weights = np.abs(normals)
    weights = (weights / np.maximum(weights.max(axis=-1, keepdims=True), 1e-8)) ** owner.triplanar_hardness
    weights /= np.maximum(weights.sum(axis=-1, keepdims=True), 1e-8)
    value = np.zeros(positions.shape[:-1], np.float32)
    for axis, (a, b) in enumerate(((1, 2), (0, 2), (0, 1))):
        if np.any(weights[..., axis] > 1e-8):
            value += sample(coords[..., a], coords[..., b]) * weights[..., axis]
    return value


def block(owner, n, low, high, ts, cache):
    positions, normals = raster(ts, n, owner, cache)
    return evaluate_points(owner, positions[low:high], normals[low:high])


def height_amplitude(owner, ts):
    """Encoded height range whose world displacement equals declared metres."""
    if not enabled(owner):
        return owner.procedural_height
    return owner.material_height_m / max(owner.material_meters_per_unit * ts.height_distance, 1e-15)


def sample_image(texture, owner, n, low, high, ts, cache):
    """Use the same metric frame for imported/generated color and data maps."""
    from .blend_modes import sample_uv
    positions, normals = raster(ts, n, owner, cache)
    positions, normals = positions[low:high] * owner.material_meters_per_unit, normals[low:high]
    extent = np.maximum(np.asarray(owner.material_image_extent_m), 1e-12)
    def sample(u, v):
        return sample_uv(texture, u / extent[0], v / extent[1], owner.filtering, owner.tiling)
    if owner.material_frame == "COMPONENT" or getattr(owner, "material_directional", False):
        return sample(np.sqrt(positions[..., 0] ** 2 + positions[..., 2] ** 2), positions[..., 1])
    weights = np.abs(normals)
    weights = (weights / np.maximum(weights.max(axis=-1, keepdims=True), 1e-8)) ** owner.triplanar_hardness
    weights /= np.maximum(weights.sum(axis=-1, keepdims=True), 1e-8)
    value = np.zeros((*positions.shape[:2], texture.shape[-1]), np.float32)
    for axis, (a, b) in enumerate(((1, 2), (0, 2), (0, 1))):
        if np.any(weights[..., axis] > 1e-8):
            value += sample(positions[..., a], positions[..., b]) * weights[..., axis:axis + 1]
    return value
