"""Evaluated-mesh data baked into UV texture pixels, without material nodes.

POSITION stores world XYZ normalized to the shared *assigned surface* bounds.
WORLD_NORMAL / OBJECT_NORMAL encode unit XYZ as XYZ * .5 + .5.
IDs are flat deterministic RGB colors, with their source names stored as JSON.
UV_BORDER is a grayscale distance falloff inside UV island boundaries.
THICKNESS is an inward ray intersection distance / thickness_distance_scale;
alpha is zero outside assigned UVs and where no opposing surface was found.

All maps describe the evaluated target mesh (including viewport modifiers), in
the render UV map's 0..1 tile. They do not transfer high-poly surface detail.
"""

import hashlib
import json
import math
from contextlib import contextmanager

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from ...core import image_manager as images


UTILITY_KINDS = (
    "POSITION", "WORLD_NORMAL", "OBJECT_NORMAL", "MATERIAL_ID", "OBJECT_ID",
    "UV_ISLAND", "UV_BORDER", "THICKNESS",
)


def _unit(values):
    return values / np.maximum(np.linalg.norm(values, axis=-1, keepdims=True), 1e-12)


def _object_key(obj):
    return f"{obj.library.filepath if obj.library else ''}|{obj.name_full}"


@contextmanager
def _targets(ts):
    """Keep evaluated meshes alive only for the duration of this bake."""
    if not ts.assignments or any(not ref.object for ref in ts.assignments):
        raise ValueError("Assign valid target meshes before baking utility maps.")
    depsgraph = bpy.context.evaluated_depsgraph_get()
    temporary, targets = [], []
    try:
        objects = sorted({ref.object for ref in ts.assignments}, key=_object_key)
        for obj in objects:
            if obj.type != "MESH":
                raise ValueError(f"{obj.name} is not a mesh.")
            evaluated = obj.evaluated_get(depsgraph)
            mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
            temporary.append(evaluated)
            uv = next((uv for uv in mesh.uv_layers if uv.active_render), mesh.uv_layers.active)
            if uv is None:
                raise ValueError(f"{obj.name} requires a render UV map.")
            slots = {ref.slot_index for ref in ts.assignments if ref.object == obj}
            mesh.calc_loop_triangles()
            selected = [tri for tri in mesh.loop_triangles if tri.material_index in slots]
            if not selected:
                raise ValueError(f"{obj.name} has no evaluated faces in its assigned slots.")
            local = np.empty((len(mesh.vertices), 3), dtype=np.float32)
            mesh.vertices.foreach_get("co", local.ravel())
            world = np.asarray(evaluated.matrix_world, dtype=np.float64)
            coords = local @ world[:3, :3].T + world[:3, 3]
            loop_normals = np.empty((len(mesh.loops), 3), dtype=np.float32)
            # Blender 5.2 corner normals honor smooth/sharp faces and custom normals.
            if len(mesh.corner_normals) == len(mesh.loops):
                mesh.corner_normals.foreach_get("vector", loop_normals.ravel())
            else:
                for face in mesh.polygons:
                    for index in face.loop_indices:
                        loop_normals[index] = (
                            mesh.vertices[mesh.loops[index].vertex_index].normal
                            if face.use_smooth else face.normal
                        )
            normal_matrix = np.asarray(
                evaluated.matrix_world.to_3x3().inverted_safe().transposed(), dtype=np.float64
            )
            world_normals = _unit(loop_normals @ normal_matrix.T).astype(np.float32)
            uv_coords = np.empty((len(mesh.loops), 2), dtype=np.float32)
            uv.data.foreach_get("uv", uv_coords.ravel())
            used = np.unique(np.asarray([tri.vertices[:] for tri in selected], dtype=np.int32))
            targets.append({
                "object": obj, "mesh": mesh, "slots": slots, "triangles": selected,
                "positions": coords, "object_normals": loop_normals,
                "world_normals": world_normals, "uv": uv_coords,
                "minimum": coords[used].min(axis=0), "maximum": coords[used].max(axis=0),
                "mirrored": np.linalg.det(world[:3, :3]) < 0,
            })
        yield targets
    finally:
        for evaluated in temporary:
            evaluated.to_mesh_clear()


def _tiles(uv, n):
    """Yield bounded raster tiles and barycentric weights at pixel centers."""
    coords = uv * n - 0.5
    lo = np.maximum(np.ceil(coords.min(axis=0)).astype(int), 0)
    hi = np.minimum(np.floor(coords.max(axis=0)).astype(int) + 1, n)
    a, b, c = coords
    det = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
    if abs(det) < 1e-8 or np.any(hi <= lo):
        return
    for y0 in range(lo[1], hi[1], 64):
        y1 = min(y0 + 64, hi[1])
        x, y = np.meshgrid(
            np.arange(lo[0], hi[0], dtype=np.float32),
            np.arange(y0, y1, dtype=np.float32),
        )
        w0 = ((b[1] - c[1]) * (x - c[0]) + (c[0] - b[0]) * (y - c[1])) / det
        w1 = ((c[1] - a[1]) * (x - c[0]) + (a[0] - c[0]) * (y - c[1])) / det
        w2 = 1 - w0 - w1
        inside = (w0 >= -1e-5) & (w1 >= -1e-5) & (w2 >= -1e-5)
        if inside.any():
            yield (slice(y0, y1), slice(lo[0], hi[0])), inside, np.stack((w0, w1, w2), -1), x, y


def _islands(target):
    """Topology plus continuous UV endpoints; triangulation never adds borders."""
    mesh, uv = target["mesh"], target["uv"]
    faces = [face for face in mesh.polygons if face.material_index in target["slots"]]
    parents = {face.index: face.index for face in faces}
    adjacency = {}

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for face in faces:
        loops = list(face.loop_indices)
        for a, b in zip(loops, loops[1:] + loops[:1]):
            va, vb = mesh.loops[a].vertex_index, mesh.loops[b].vertex_index
            ordered = (a, b) if va < vb else (b, a)
            key = tuple(sorted((va, vb)))
            adjacency.setdefault(key, []).append((face.index, ordered))
    borders = {}
    for entries in adjacency.values():
        joined = False
        if len(entries) == 2:
            (a, la), (b, lb) = entries
            if np.allclose(uv[list(la)], uv[list(lb)], atol=1e-6, rtol=0):
                parents[root(b)] = root(a)
                joined = True
        if not joined:
            for face, (a, b) in entries:
                borders.setdefault(face, []).append((uv[a], uv[b]))
    labels = {index: root(index) for index in parents}
    return labels, borders


def _id_color(key, palette):
    if key not in palette:
        digest = hashlib.sha256(key.encode("utf8")).digest()
        # ID colors are data, not display colors; avoid pure black reserved for invalid pixels.
        color = [max(1, value) / 255.0 for value in digest[:3]]
        # Deterministically avoid even rare hash collisions within this bake.
        nonce = 0
        while color in palette.values():
            nonce += 1
            digest = hashlib.sha256(f"{key}:{nonce}".encode("utf8")).digest()
            color = [max(1, value) / 255.0 for value in digest[:3]]
        palette[key] = color
    return palette[key]


def _material_key(ts, obj, slot):
    ref = next(ref for ref in ts.assignments if ref.object == obj and ref.slot_index == slot)
    material = ref.original_material or (obj.material_slots[slot].material if slot < len(obj.material_slots) else None)
    if material:
        library = material.library.filepath if material.library else ""
        return f"{library}|{material.name_full}"
    return f"{_object_key(obj)}|slot:{slot}"


def _rasterize(targets, ts, kind, n, minimum, maximum, palette):
    result = np.zeros((n, n, 4), dtype=np.float32)
    span = maximum - minimum
    nondegenerate = span > max(float(np.linalg.norm(span)) * 1e-6, 1e-12)
    for target in targets:
        islands, borders = _islands(target) if kind in {"UV_ISLAND", "UV_BORDER"} else ({}, {})
        island_borders = {}
        if kind == "UV_BORDER":
            for face, edges in borders.items():
                island_borders.setdefault(islands[face], []).extend(edges)
            island_borders = {key: np.asarray(edges) for key, edges in island_borders.items()}
        obj = target["object"]
        for tri in target["triangles"]:
            uv = target["uv"][list(tri.loops)]
            if kind == "POSITION":
                values = np.divide(
                    target["positions"][list(tri.vertices)] - minimum, span,
                    out=np.full((3, 3), 0.5, dtype=np.float64), where=nondegenerate,
                ).astype(np.float32)
            elif kind in {"WORLD_NORMAL", "OBJECT_NORMAL"}:
                values = target["world_normals" if kind == "WORLD_NORMAL" else "object_normals"][list(tri.loops)]
            elif kind in {"MATERIAL_ID", "OBJECT_ID", "UV_ISLAND"}:
                key = _material_key(ts, obj, tri.material_index) if kind == "MATERIAL_ID" else _object_key(obj)
                if kind == "UV_ISLAND":
                    key += f"|island:{islands[tri.polygon_index]}"
                color = _id_color(key, palette)
            elif kind == "UV_BORDER":
                # Border falloff can extend into adjacent faces of an island.
                # Limit segment tests spatially without treating triangle edges as borders.
                edge_array = island_borders.get(islands[tri.polygon_index], np.empty((0, 2, 2)))
                pad = ts.bake.uv_border_width / n
                selected_edges = edge_array[
                    np.all(edge_array.max(axis=1) >= uv.min(axis=0) - pad, axis=1)
                    & np.all(edge_array.min(axis=1) <= uv.max(axis=0) + pad, axis=1)
                ]
            for region, inside, weights, x, y in _tiles(uv, n):
                tile = result[region]
                if kind in {"POSITION", "WORLD_NORMAL", "OBJECT_NORMAL"}:
                    current = weights @ values
                    if kind != "POSITION":
                        current = _unit(current) * 0.5 + 0.5
                    tile[..., :3][inside] = current[inside]
                elif kind in {"MATERIAL_ID", "OBJECT_ID", "UV_ISLAND"}:
                    tile[..., :3][inside] = color
                elif kind == "UV_BORDER":
                    coverage = np.zeros_like(x)
                    for ua, ub in selected_edges:
                        start, end = ua * n - 0.5, ub * n - 0.5
                        delta = end - start
                        length = float(delta @ delta)
                        if length < 1e-12:
                            continue
                        t = np.clip(((x - start[0]) * delta[0] + (y - start[1]) * delta[1]) / length, 0, 1)
                        distance = np.hypot(x - start[0] - t * delta[0], y - start[1] - t * delta[1])
                        np.maximum(coverage, np.clip(1 - distance / ts.bake.uv_border_width, 0, 1), out=coverage)
                    tile[..., :3][inside] = coverage[inside, None]
                tile[..., 3][inside] = 1
    return result


def _thickness(targets, ts, n, distance_scale):
    """A bounded number of actual ray intersections against each target's full mesh."""
    resolution = min(n, int(ts.bake.thickness_resolution))
    samples = int(ts.bake.thickness_samples)
    result = np.zeros((resolution, resolution, 4), dtype=np.float32)
    sampled = np.zeros((resolution, resolution), dtype=bool)
    epsilon = max(distance_scale * 1e-5, 1e-7)
    ray_count = hit_count = 0
    # Last surface wins for overlapping UVs, matching the other data maps. Work
    # backwards so even heavily stacked UVs never exceed resolution² * samples.
    for target in reversed(targets):
        tree = BVHTree.FromPolygons(
            target["positions"].tolist(),
            [tuple(tri.vertices) for tri in target["mesh"].loop_triangles], all_triangles=True,
        )
        for tri in reversed(target["triangles"]):
            uv = target["uv"][list(tri.loops)]
            positions = target["positions"][list(tri.vertices)]
            normals = target["world_normals"][list(tri.loops)]
            for region, inside, weights, _x, _y in _tiles(uv, resolution):
                inside &= ~sampled[region]
                if not inside.any():
                    continue
                sampled[region] |= inside
                points = (weights @ positions)[inside]
                directions = -_unit((weights @ normals)[inside])
                values = np.zeros((len(points), 4), dtype=np.float32)
                for index, (point, direction) in enumerate(zip(points, directions)):
                    inward = Vector(direction)
                    tangent = inward.orthogonal().normalized()
                    bitangent = inward.cross(tangent)
                    origin = Vector(point) + inward * epsilon
                    distances = []
                    for sample in range(samples):
                        angle = 2 * math.pi * sample / max(1, samples - 1)
                        ray = inward if sample == 0 else (
                            inward + 0.18 * (math.cos(angle) * tangent + math.sin(angle) * bitangent)
                        ).normalized()
                        _hit, normal, _face, distance = tree.ray_cast(origin, ray, distance_scale)
                        ray_count += 1
                        if distance is None or distance <= epsilon:
                            continue
                        # Require an exit-facing surface. An open sheet hitting a separate
                        # entering shell is not a material thickness measurement.
                        sign = -1 if target["mirrored"] else 1
                        if normal.dot(ray) * sign <= 1e-6:
                            continue
                        distances.append(distance * ray.dot(inward) + epsilon)
                        hit_count += 1
                    if distances:
                        values[index, :3] = min(1.0, float(np.mean(distances)) / distance_scale)
                        values[index, 3] = 1
                result[region][inside] = values
    if resolution != n:
        # Nearest resampling preserves valid/invalid classification and does not
        # invent measurements across UV islands. The sampling grid is explicit.
        indices = np.minimum(((np.arange(n) + 0.5) * resolution / n).astype(int), resolution - 1)
        result = result[indices[:, None], indices[None, :]]
        # Reapply precise high-resolution UV coverage; no expanded island edges.
        coverage = np.zeros((n, n), dtype=bool)
        for target in targets:
            for tri in target["triangles"]:
                for region, inside, *_ in _tiles(target["uv"][list(tri.loops)], n):
                    coverage[region] |= inside
        result[~coverage] = 0
    return result, {"thickness_sampling_resolution": resolution, "thickness_samples": samples,
                    "thickness_ray_count": ray_count, "thickness_valid_hits": hit_count,
                    "thickness_distance_scale": float(distance_scale)}


def bake_utility(ts, kind):
    """Return a packed managed image; registration with ts.baked_maps is explicit."""
    if kind not in UTILITY_KINDS:
        raise ValueError(f"Unsupported utility mesh map: {kind}")
    n, palette, metadata = int(ts.resolution), {}, {}
    with _targets(ts) as targets:
        minimum = np.min([target["minimum"] for target in targets], axis=0)
        maximum = np.max([target["maximum"] for target in targets], axis=0)
        if kind == "THICKNESS":
            # Opposing faces may use other material slots. The search distance
            # must include the complete target shell, unlike Position bounds.
            automatic_distance = max(float(np.linalg.norm(np.ptp(target["positions"], axis=0))) for target in targets)
            distance_scale = ts.bake.thickness_max_distance or automatic_distance
            if distance_scale <= 1e-10:
                raise ValueError("Thickness needs non-zero target bounds or a positive maximum distance.")
            pixels, metadata = _thickness(targets, ts, n, distance_scale)
        else:
            pixels = _rasterize(targets, ts, kind, n, minimum, maximum, palette)
    image = images.create(f"{ts.name}_Baked{kind.title().replace('_', '')}", n)
    try:
        image["open_painter_mesh_map"] = kind
        image["open_painter_utility_method"] = "EVALUATED_TARGET_UV_V1"
        image["world_min"], image["world_max"] = minimum.tolist(), maximum.tolist()
        image["alpha_encoding"] = "Assigned UV coverage; thickness also requires an opposing-surface hit"
        if palette:
            image["id_palette"] = json.dumps(palette, sort_keys=True)
        for key, value in metadata.items():
            image[key] = value
        images.write(image, pixels)
    except Exception:
        bpy.data.images.remove(image)
        raise
    return image
