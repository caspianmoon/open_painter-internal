"""Edge-map sets with geometric smooth-edge coverage or an opt-in Cycles Bevel shader.

The geometric method remains the default for smooth rims and dense assets. The
Cycles method reproduces the contributor's Bevel reference, one pass per reach.
"""

from math import cos, radians
from types import SimpleNamespace
import bpy
import numpy as np

from ..cycles_baker import CURVATURE_METHOD, bake_curvature_reach
from ...core import image_manager as images


def crease_edges(mesh, world, minimum_angle, concave=False):
    """Return convex (or concave) shared mesh edges above the geometric threshold.

    Vectorised over polygons and loops: face normals/centers come from
    foreach_get, edges shared by exactly two faces are found by sorting keys.
    """
    count = len(mesh.polygons)
    if not count:
        return set()
    normals = np.empty(count * 3, np.float32)
    mesh.polygons.foreach_get("normal", normals)
    centers = np.empty(count * 3, np.float32)
    mesh.polygons.foreach_get("center", centers)
    loop_start = np.empty(count, np.int32)
    mesh.polygons.foreach_get("loop_start", loop_start)
    loop_total = np.empty(count, np.int32)
    mesh.polygons.foreach_get("loop_total", loop_total)
    loop_vertex = np.empty(len(mesh.loops), np.int32)
    mesh.loops.foreach_get("vertex_index", loop_vertex)
    transform = np.asarray(world.to_3x3().inverted_safe().transposed(), np.float64)
    normals = normals.reshape(-1, 3).astype(np.float64) @ transform.T
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
    matrix = np.asarray(world, np.float64)
    centers = centers.reshape(-1, 3).astype(np.float64) @ matrix[:3, :3].T + matrix[:3, 3]
    # Each loop starts one polygon edge; the next loop wraps within the polygon.
    face = np.repeat(np.arange(count), loop_total)
    loop = np.arange(len(loop_vertex))
    following = loop + 1
    last = loop_start + loop_total - 1
    following[last] = loop_start
    va, vb = loop_vertex, loop_vertex[following]
    keys = np.minimum(va, vb).astype(np.int64) * len(mesh.vertices) + np.maximum(va, vb)
    order = np.argsort(keys, kind="stable")
    sorted_keys = keys[order]
    unique, first, counts = np.unique(sorted_keys, return_index=True, return_counts=True)
    shared = counts == 2  # Open/nonmanifold boundaries are not convex surface creases.
    a = face[order[first[shared]]]
    b = face[order[first[shared] + 1]]
    limit = cos(radians(minimum_angle))
    dot = np.einsum("ij,ij->i", normals[a], normals[b])
    candidate = ~((dot >= limit) | (dot > 1 - 1e-6))
    separation = centers[b] - centers[a]
    along_a = np.einsum("ij,ij->i", normals[a], separation)
    along_b = np.einsum("ij,ij->i", normals[b], separation)
    if concave:
        chosen = candidate & (along_a > 1e-8) & (along_b < -1e-8)
    else:
        chosen = candidate & (along_a < -1e-8) & (along_b > 1e-8)
    edge_keys = unique[shared][chosen]
    vertices = len(mesh.vertices)
    return set(zip((edge_keys // vertices).tolist(), (edge_keys % vertices).tolist()))


def prepare_creases(mesh, world, slots, n, minimum_angle, width, concave=False, uv_coords=None):
    """Read crease-carrying triangles into arrays (inside the evaluated-mesh scope)."""
    from types import SimpleNamespace
    uv_layer = next((uv for uv in mesh.uv_layers if uv.active_render), mesh.uv_layers.active)
    if uv_layer is None:
        raise ValueError("Curvature requires a render UV map.")
    creases = crease_edges(mesh, world, minimum_angle, concave)
    prepared = SimpleNamespace(n=n, width=width, uv=None, tri_loops=None, flags=None, selected=np.empty(0, np.int64))
    if not creases:
        return prepared
    mesh.calc_loop_triangles()
    from ...geometry.snapshots import _triangle_arrays
    tri_vertices, tri_loops, tri_material, _ = _triangle_arrays(mesh)
    uv = uv_coords
    if uv is None:
        uv = np.empty((len(mesh.loops), 2), np.float32)
        uv_layer.data.foreach_get("uv", uv.ravel())
    vertices = len(mesh.vertices)
    crease_keys = np.fromiter((a * vertices + b for a, b in creases), np.int64, len(creases))
    flags = np.zeros((len(tri_vertices), 3), bool)
    for column, (i, j) in enumerate(SIDES):
        va, vb = tri_vertices[:, i], tri_vertices[:, j]
        flags[:, column] = np.isin(np.minimum(va, vb).astype(np.int64) * vertices + np.maximum(va, vb), crease_keys)
    selected = np.isin(tri_material, np.fromiter(slots, np.int32, len(slots))) & flags.any(axis=1)
    prepared.uv, prepared.tri_loops, prepared.flags = uv, tri_loops, flags
    prepared.selected = np.flatnonzero(selected)
    return prepared


SIDES = ((0, 1), (1, 2), (2, 0))


def rasterize_steps(prepared):
    """Crease lines of prepared triangles; yields progress, returns the (n, n) map."""
    n, width = prepared.n, prepared.width
    result = np.zeros((n, n), dtype=np.float32)
    selected = prepared.selected
    from time import perf_counter
    last_yield = perf_counter()
    # Only triangles touching a crease rasterize; the arithmetic is unchanged.
    for position, triangle in enumerate(selected):
        if perf_counter() - last_yield > .03:
            yield position / len(selected)
            last_yield = perf_counter()
        edges = [side for column, side in enumerate(SIDES) if prepared.flags[triangle, column]]
        coords = prepared.uv[prepared.tri_loops[triangle]] * n - 0.5
        lo = np.maximum(np.ceil(coords.min(axis=0)).astype(int), 0)
        hi = np.minimum(np.floor(coords.max(axis=0)).astype(int) + 1, n)
        a, b, c = coords
        det = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(det) < 1e-8 or np.any(hi <= lo):
            continue
        for y0 in range(lo[1], hi[1], 128):
            y1 = min(y0 + 128, hi[1])
            x, y = np.meshgrid(
                np.arange(lo[0], hi[0], dtype=np.float32), np.arange(y0, y1, dtype=np.float32)
            )
            w0 = ((b[1] - c[1]) * (x - c[0]) + (c[0] - b[0]) * (y - c[1])) / det
            w1 = ((c[1] - a[1]) * (x - c[0]) + (a[0] - c[0]) * (y - c[1])) / det
            inside = (w0 >= -1e-5) & (w1 >= -1e-5) & (w0 + w1 <= 1 + 1e-5)
            coverage = np.zeros_like(x)
            for i, j in edges:
                start, end = coords[i], coords[j]
                delta = end - start
                length = float(delta @ delta)
                if length < 1e-8:
                    continue
                t = np.clip(((x - start[0]) * delta[0] + (y - start[1]) * delta[1]) / length, 0, 1)
                distance = np.sqrt(
                    (x - start[0] - t * delta[0]) ** 2 + (y - start[1] - t * delta[1]) ** 2
                )
                np.maximum(coverage, np.clip(1 - distance / width, 0, 1), out=coverage)
            tile = result[y0:y1, lo[0] : hi[0]]
            np.maximum(tile, np.where(inside, coverage, 0), out=tile)
    return result


def rasterize(mesh, world, slots, n, minimum_angle, width, concave=False):
    steps = rasterize_steps(prepare_creases(mesh, world, slots, n, minimum_angle, width, concave))
    while True:
        try:
            next(steps)
        except StopIteration as done:
            return done.value


METHOD = 'GEOMETRIC_CREASES_V2'


def method_of(ts):
    return CURVATURE_METHOD if ts.bake.curvature_method == 'CYCLES_BEVEL' else METHOD


# Both methods bake four physical reaches so Edge Size can blend them live.
EDGE_MAP_KINDS = ("CURVATURE_THIN", "CURVATURE", "CURVATURE_WIDE", "CURVATURE_XWIDE")
EDGE_SUFFIXES = {"CURVATURE_THIN": "Thin", "CURVATURE": "", "CURVATURE_WIDE": "Wide", "CURVATURE_XWIDE": "XWide"}
EDGE_RADIUS_FACTORS = {"CURVATURE_THIN": .5, "CURVATURE": 1., "CURVATURE_WIDE": 2., "CURVATURE_XWIDE": 4.}


def edge_map_set_complete(ts):
    """True when every map of the edge set exists (Edge Size can blend live)."""
    return all(any(m.kind == kind and m.image for m in ts.baked_maps) for kind in EDGE_MAP_KINDS)


def _cycles_steps(context, ts, kinds=EDGE_MAP_KINDS):
    """Bake the edge set with the Cycles Bevel shader: one blocking bake per reach.

    Each reach follows the reference procedure (Bevel 12, Normal sockets,
    Smootherstep, Diffuse COLOR bake) at its own radius multiple.

    Yields progress (0..1) between reaches so the modal refresh keeps responding
    and Esc stops between maps; returns {kind: image}. The settings are read
    before the first yield: a modal caller may mutate the project between steps.
    Closing the generator releases the reaches baked so far, mirroring the old
    cooperative cleanup: only landed maps persist.
    """
    scene_name, set_id = ts.id_data.name, ts.id
    from ...geometry.provenance import record
    expected = record(ts, 'CURVATURE')
    base = float(ts.bake.mean_curvature_distance)
    radii = {kind: base * EDGE_RADIUS_FACTORS[kind] for kind in kinds}
    yield 0.02
    result = {}
    try:
        for position, kind in enumerate(kinds):
            # RNA collections may have moved during a yield. Resolve again by identity.
            scene = bpy.data.scenes.get(scene_name)
            ts = next((item for item in scene.open_painter.texture_sets if item.id == set_id), None) if scene else None
            if ts is None or record(ts, 'CURVATURE') != expected:
                raise ValueError('Bake inputs changed; restart the edge-map bake')
            with context.temp_override(scene=scene):
                result[kind] = bake_curvature_reach(context, ts, kind, radii[kind])
            yield 0.15 + 0.83 * (position + 1) / len(kinds)
    except BaseException:
        # Stopped (GeneratorExit from the modal's Esc/cancel) or failed part-way:
        # the reaches already produced are packed fake-user images nobody
        # registers, so they would otherwise persist in the file. Published
        # maps were never used as scratch and keep their previous pixels.
        from ...core.image_manager import live_bake_pointers
        live = live_bake_pointers(ts)
        for image in result.values():
            try:
                if image.as_pointer() in live:
                    continue
                bpy.data.images.remove(image)
            except ReferenceError:
                pass  # a memfile undo mid-job already freed it
        raise
    return result


def _geometric_steps(ts, kinds=EDGE_MAP_KINDS):
    """Bake the edge map set cooperatively: yields progress (0..1), returns {kind: image}.

    Mesh data is read into arrays in one evaluated-mesh scope up front; the
    crease lines (shared by every reach) and the smooth convex curvature at
    each reach then accumulate across steps, so a modal operator can keep the
    interface responsive.
    """
    from ...geometry.snapshots import targets
    from .mean_curvature import convex_map_steps, prepare_raster, prepare_reach
    n = int(ts.resolution)
    base = ts.bake.mean_curvature_distance
    # Everything the set provides is read before the first yield: a modal caller
    # may add or remove texture sets (or undo) between steps, which reallocates
    # the collection under this RNA reference.
    name, set_id = ts.name, ts.id
    radii = {kind: base * EDGE_RADIUS_FACTORS[kind] for kind in kinds}
    with targets(ts) as meshes:
        # No yields while evaluated meshes are alive: the depsgraph may change between steps.
        creases = [prepare_creases(target["mesh"], target["object"].matrix_world, target["slots"], n,
                                   ts.bake.curvature_angle, ts.bake.curvature_width,
                                   uv_coords=target['uv']) for target in meshes]
        # Face turning and the raster are radius-independent: one copy serves
        # every reach (four 4k rasters would hold gigabytes for the whole job).
        rasters = [prepare_raster(target, n, radians(ts.bake.curvature_angle)) for target in meshes]
        fields = {kind: [prepare_reach(target, raster, radii[kind]) for target, raster in zip(meshes, rasters)]
                  for kind in kinds}
    yield 0.02
    lines = np.zeros((n, n), dtype=np.float32)
    for index, prepared in enumerate(creases):
        steps = rasterize_steps(prepared)
        while True:
            try:
                yield 0.02 + 0.13 * (index + next(steps)) / len(creases)
            except StopIteration as done:
                np.maximum(lines, done.value, out=lines)
                break
    result = {}
    try:
        for position, kind in enumerate(kinds):
            # Smooth convex curvature within the physical radius: rims, bevels and
            # dense edges that never exceed the crease angle still become edge wear.
            values = lines.copy()
            steps = convex_map_steps(fields.pop(kind), n, radii[kind])
            while True:
                try:
                    yield 0.15 + 0.83 * (position + next(steps)) / len(kinds)
                except StopIteration as done:
                    np.maximum(values, done.value, out=values)
                    break
            rgba = np.ones((n, n, 4), dtype=np.float32)
            rgba[..., :3] = values[..., None]
            image = result[kind] = images.create(f"{name}_BakedCurvature{EDGE_SUFFIXES[kind]}", n)
            image["op_bake_result"] = set_id
            image["open_painter_curvature_method"] = METHOD
            image["mean_curvature_distance"] = float(radii[kind])
            image["open_painter_edge_kind"] = kind
            images.write(image, rgba)
    except BaseException:
        # Stopped (GeneratorExit from the modal's Esc/cancel) or failed part-way:
        # the reaches already produced are packed fake-user images nobody
        # registers, so they would otherwise persist in the file.
        for image in result.values():
            try:
                bpy.data.images.remove(image)
            except ReferenceError:
                pass  # a memfile undo mid-job already freed it
        raise
    return result


def curvature_steps(context, ts, kinds=EDGE_MAP_KINDS):
    """Dispatch the chosen method; both yield progress and publish only complete sets."""
    if ts.bake.curvature_method == 'CYCLES_BEVEL':
        return (yield from _cycles_steps(context, ts, kinds))
    return (yield from _geometric_steps(ts, kinds))


def bake_curvature_set(ts, kinds=EDGE_MAP_KINDS, context=None):
    """Synchronous edge-map set bake: {kind: image}."""
    steps = curvature_steps(context or bpy.context, ts, kinds)
    while True:
        try:
            next(steps)
        except StopIteration as done:
            return done.value


def bake_curvature(ts, context=None):
    """The CURVATURE map alone (scripts and tests); the queue bakes the whole set."""
    return bake_curvature_set(ts, ("CURVATURE",), context=context)["CURVATURE"]
