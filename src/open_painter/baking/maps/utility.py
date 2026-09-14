"""Evaluated-mesh data baked into UV texture pixels, without material nodes.

POSITION stores world XYZ normalized to the shared *assigned surface* bounds.
WORLD_NORMAL / OBJECT_NORMAL encode unit XYZ as XYZ * .5 + .5.
IDs are flat deterministic RGB colors, with their source names stored as JSON.
UV_BORDER is a grayscale distance falloff inside UV island boundaries.
THICKNESS is an inward ray intersection distance / thickness_distance_scale;
alpha is zero outside assigned UVs and where no opposing surface was found.
MEAN_CURVATURE is signed smooth curvature (0.5 flat, >0.5 convex, <0.5 concave).
SELECTION is the current face selection (1 selected, 0 otherwise).

All maps describe the evaluated target mesh (including viewport modifiers), in
the render UV map's 0..1 tile. They do not transfer high-poly surface detail.
"""

import hashlib
import json
import math

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from ...core import image_manager as images


# Append only: OP_BakedMap.kind and mask map types mirror this order.
UTILITY_KINDS = (
    "POSITION", "WORLD_NORMAL", "OBJECT_NORMAL", "MATERIAL_ID", "OBJECT_ID",
    "UV_ISLAND", "UV_BORDER", "THICKNESS", "MEAN_CURVATURE", "SELECTION",
)


from ...geometry.snapshots import targets as _targets, _triangle_arrays, _unit, _object_key
from ...geometry.uv_rasterizer import interpolate as _interpolate, rasterize as _rasterize_batched, tiles as _tiles


def _islands(target):
    """UV islands of the assigned faces: (root per polygon, border segments per island).

    Topology plus continuous UV endpoints; triangulation never adds borders.
    Vectorised: polygon loops come from foreach_get, shared edges pair up by
    sorted key, and joins run through an array union-find.
    """
    mesh, uv = target["mesh"], target["uv"]
    count = len(mesh.polygons)
    material = np.empty(count, np.int32)
    mesh.polygons.foreach_get("material_index", material)
    loop_start = np.empty(count, np.int32)
    mesh.polygons.foreach_get("loop_start", loop_start)
    loop_total = np.empty(count, np.int32)
    mesh.polygons.foreach_get("loop_total", loop_total)
    loop_vertex = np.empty(len(mesh.loops), np.int32)
    mesh.loops.foreach_get("vertex_index", loop_vertex)
    faces = np.flatnonzero(np.isin(material, np.fromiter(target["slots"], np.int32, len(target["slots"]))))
    labels = np.full(count, -1, np.int64)
    if not len(faces):
        return labels, {}
    face_of_loop = np.repeat(faces, loop_total[faces])
    loops = np.concatenate([np.arange(loop_start[f], loop_start[f] + loop_total[f]) for f in faces]) \
        if len(faces) < 4096 else np.flatnonzero(np.isin(np.repeat(np.arange(count), loop_total), faces))
    following = loops + 1
    last = loop_start[faces] + loop_total[faces] - 1
    following[np.isin(loops, last)] = loop_start[face_of_loop[np.isin(loops, last)]]
    va, vb = loop_vertex[loops], loop_vertex[following]
    swap = va > vb
    a = np.where(swap, following, loops)
    b = np.where(swap, loops, following)
    keys = np.minimum(va, vb).astype(np.int64) * len(mesh.vertices) + np.maximum(va, vb)
    order = np.argsort(keys, kind="stable")
    sorted_keys = keys[order]
    _, first, counts = np.unique(sorted_keys, return_index=True, return_counts=True)
    paired = first[counts == 2]
    entry_a, entry_b = order[paired], order[paired + 1]
    joined = (np.all(np.abs(uv[a[entry_a]] - uv[a[entry_b]]) <= 1e-6, axis=1)
              & np.all(np.abs(uv[b[entry_a]] - uv[b[entry_b]]) <= 1e-6, axis=1))
    # Union in the original insertion order (first appearance of each shared
    # edge) with root(b) hooked under root(a): island representatives, and so
    # the island ID colors keyed by them, stay identical to earlier bakes.
    parent = list(range(count))

    def root(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    sequence = np.argsort(entry_a[joined], kind="stable")
    for fa, fb in zip(face_of_loop[entry_a[joined]][sequence].tolist(),
                      face_of_loop[entry_b[joined]][sequence].tolist()):
        parent[root(fb)] = root(fa)
    labels[faces] = [root(int(face)) for face in faces]
    border = np.ones(len(loops), bool)
    border[entry_a[joined]] = False
    border[entry_b[joined]] = False
    segments = np.stack((uv[a[border]], uv[b[border]]), axis=1)
    islands = labels[face_of_loop[border]]
    borders = {}
    for island in np.unique(islands):
        borders[int(island)] = segments[islands == island]
    return labels, borders


def _border_coverage(rows, cols, texel_island, borders, n, width):
    """Distance falloff from an island's own border edges, evaluated for every edge at once."""
    ids = {island: index for index, island in enumerate(borders)}
    if not ids or not len(rows):
        return np.zeros(len(rows), np.float32)
    segments = np.concatenate([borders[island] for island in ids]).astype(np.float32) * n - 0.5
    edge_island = np.concatenate([np.full(len(borders[island]), ids[island]) for island in ids])
    start, end = segments[:, 0], segments[:, 1]
    delta = end - start
    length = delta[:, 0] * delta[:, 0] + delta[:, 1] * delta[:, 1]
    reach = int(math.ceil(width)) + 1
    x0 = np.clip(np.floor(np.minimum(start[:, 0], end[:, 0])).astype(np.int64) - reach, 0, n)
    x1 = np.clip(np.ceil(np.maximum(start[:, 0], end[:, 0])).astype(np.int64) + reach + 1, 0, n)
    y0 = np.clip(np.floor(np.minimum(start[:, 1], end[:, 1])).astype(np.int64) - reach, 0, n)
    y1 = np.clip(np.ceil(np.maximum(start[:, 1], end[:, 1])).astype(np.int64) + reach + 1, 0, n)
    valid = (length >= 1e-12) & (x1 > x0) & (y1 > y0)
    extent = np.maximum(x1 - x0, y1 - y0)
    keys, values = [], []
    block, previous = 8, 0
    while previous < extent.max(initial=0):
        chosen = np.flatnonzero(valid & (extent > previous) & (extent <= block))
        previous, block = block, block * 2
        if not len(chosen):
            continue
        offsets = np.arange(previous, dtype=np.float32)
        chunk = max(64, (1 << 21) // (previous * previous))
        for at in range(0, len(chosen), chunk):
            sub = chosen[at:at + chunk]
            x = x0[sub].astype(np.float32)[:, None, None] + offsets[None, None, :]
            y = y0[sub].astype(np.float32)[:, None, None] + offsets[None, :, None]
            sx, sy = start[sub, 0, None, None], start[sub, 1, None, None]
            dx, dy = delta[sub, 0, None, None], delta[sub, 1, None, None]
            t = np.clip(((x - sx) * dx + (y - sy) * dy) / length[sub, None, None], 0, 1)
            distance = np.hypot(x - sx - t * dx, y - sy - t * dy)
            value = np.clip(1 - distance / width, 0, 1)
            inside = (x < x1[sub, None, None]) & (y < y1[sub, None, None]) & (value > 0)
            index, yy, xx = np.nonzero(inside)
            texel = (y0[sub][index] + yy) * n + (x0[sub][index] + xx)
            keys.append(texel * len(ids) + edge_island[sub][index])
            values.append(value[inside])
    result = np.zeros(len(rows), np.float32)
    if not keys:
        return result
    keys, values = np.concatenate(keys), np.concatenate(values)
    order = np.argsort(keys, kind="stable")
    keys, values = keys[order], values[order]
    unique, first = np.unique(keys, return_index=True)
    peak = np.maximum.reduceat(values, first)
    wanted = (rows * n + cols) * len(ids) + np.array([ids.get(int(island), -1) for island in texel_island])
    position = np.searchsorted(unique, wanted)
    position = np.minimum(position, len(unique) - 1)
    matched = unique[position] == wanted
    result[matched] = peak[position[matched]]
    return result


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


def _rasterize(targets, ts, kind, n, minimum, maximum, palette, metadata=None):
    result = np.zeros((n, n, 4), dtype=np.float32)
    span = maximum - minimum
    nondegenerate = span > max(float(np.linalg.norm(span)) * 1e-6, 1e-12)
    if kind == "MEAN_CURVATURE":
        from .mean_curvature import signed_map
        values, covered, extra = signed_map(targets, n, ts.bake.mean_curvature_distance,
                                            ts.bake.curvature_angle, ts.bake.curvature_width)
        result[..., :3] = values[..., None]
        result[..., 3] = covered
        if metadata is not None:
            metadata.update(extra)
        return result
    for index, target in enumerate(targets):
        islands, borders = _islands(target) if kind in {"UV_ISLAND", "UV_BORDER"} else (None, {})
        faces = None
        if kind == "SELECTION":
            from .selection import polygon_flags
            faces = polygon_flags(target)
            if metadata is not None:
                metadata.setdefault("selected_faces", {})[target["object"].name] = np.flatnonzero(faces).tolist()
        obj = target["object"]
        tri_loops, tri_vertices, tri_polygons = target["tri_loops"], target["tri_vertices"], target["tri_polygons"]
        if kind == "UV_BORDER":
            rows, cols, tri, _ = _rasterize_batched(target["uv"][tri_loops], n)
            if not len(rows):
                continue
            texel_island = islands[tri_polygons[tri]]
            result[rows, cols, :3] = _border_coverage(rows, cols, texel_island, borders, n,
                                                      float(ts.bake.uv_border_width))[:, None]
            result[rows, cols, 3] = 1
            continue
        # Every other map interpolates per-corner or per-triangle data: rasterize
        # the whole target at once instead of one Python iteration per triangle.
        rows, cols, tri, weights = _rasterize_batched(target["uv"][tri_loops], n)
        if not len(rows):
            continue
        if faces is not None:
            result[rows, cols, :3] = faces[tri_polygons[tri]][:, None]
        elif kind == "POSITION":
            normalized = np.divide(
                target["positions"] - minimum, span,
                out=np.full_like(target["positions"], 0.5), where=nondegenerate,
            ).astype(np.float32)
            result[rows, cols, :3] = _interpolate(weights, normalized[tri_vertices[tri]])
        elif kind in {"WORLD_NORMAL", "OBJECT_NORMAL"}:
            corners = target["world_normals" if kind == "WORLD_NORMAL" else "object_normals"][tri_loops[tri]]
            result[rows, cols, :3] = _unit(_interpolate(weights, corners)) * 0.5 + 0.5
        elif kind in {"MATERIAL_ID", "OBJECT_ID", "UV_ISLAND"}:
            # Keys enter the palette in triangle order, as the sequential bake did.
            if kind == "OBJECT_ID":
                keys = np.full(len(tri_loops), _object_key(obj), dtype=object)
            elif kind == "MATERIAL_ID":
                slots_seen = {int(slot): _material_key(ts, obj, int(slot)) for slot in np.unique(target["tri_material"])}
                keys = np.array([slots_seen[int(slot)] for slot in target["tri_material"]], dtype=object)
            else:
                keys = np.array([f"{_object_key(obj)}|island:{int(islands[int(polygon)])}" for polygon in tri_polygons],
                                dtype=object)
            colors = np.empty((len(tri_loops), 3), np.float32)
            for key in dict.fromkeys(keys.tolist()):
                colors[keys == key] = _id_color(key, palette)
            result[rows, cols, :3] = colors[tri]
        result[rows, cols, 3] = 1
    return result


def _orthogonal(vectors):
    """mathutils.Vector.orthogonal for rows of (N, 3): perpendicular via the dominant axis."""
    x, y, z = vectors[:, 0], vectors[:, 1], vectors[:, 2]
    dominant = np.argmax(np.abs(vectors), axis=1)
    result = np.empty_like(vectors)
    result[:, 0] = np.select([dominant == 0, dominant == 1], [-y - z, y], z)
    result[:, 1] = np.select([dominant == 0, dominant == 1], [x, -x - z], z)
    result[:, 2] = np.select([dominant == 0, dominant == 1], [x, y], -x - y)
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
    # Sample directions: the inward normal plus a cone of tilted rays.
    angles = 2 * math.pi * np.arange(samples) / max(1, samples - 1)
    cone = np.stack((np.cos(angles), np.sin(angles)), axis=-1) * 0.18
    for target in reversed(targets):
        all_vertices, all_loops, _, _ = _triangle_arrays(target["mesh"])
        tree = BVHTree.FromPolygons(
            target["positions"].tolist(), all_vertices.tolist(), all_triangles=True,
        )
        sign = -1 if target["mirrored"] else 1
        rows, cols, tri, weights = _rasterize_batched(target["uv"][target["tri_loops"]], resolution)
        fresh = ~sampled[rows, cols]
        rows, cols, tri, weights = rows[fresh], cols[fresh], tri[fresh], weights[fresh]
        if not len(rows):
            continue
        sampled[rows, cols] = True
        points = _interpolate(weights, target["positions"][target["tri_vertices"][tri]])
        inward = -_unit(_interpolate(weights, target["world_normals"][target["tri_loops"][tri]]))
        # Orthonormal frames for every point at once; only the intersection
        # queries remain per ray (mathutils has no batch API).
        tangent = _unit(_orthogonal(inward))
        bitangent = np.cross(inward, tangent)
        rays = inward[:, None, :] + cone[None, :, 0, None] * tangent[:, None, :] + cone[None, :, 1, None] * bitangent[:, None, :]
        rays = _unit(rays)
        rays[:, 0] = inward
        # Plain Python lists feed mathutils far faster than ndarray rows.
        along = np.einsum("psk,pk->ps", rays, inward).tolist()
        origins = (points + inward * epsilon).tolist()
        rays = rays.tolist()
        values = np.zeros((len(points), 4), dtype=np.float32)
        cast = tree.ray_cast
        for index, origin in enumerate(origins):
            origin = Vector(origin)
            total, count = 0.0, 0
            for sample, direction in enumerate(rays[index]):
                ray = Vector(direction)
                _hit, normal, _face, distance = cast(origin, ray, distance_scale)
                ray_count += 1
                if distance is None or distance <= epsilon:
                    continue
                # Require an exit-facing surface. An open sheet hitting a separate
                # entering shell is not a material thickness measurement.
                if normal.dot(ray) * sign <= 1e-6:
                    continue
                total += distance * along[index][sample] + epsilon
                count += 1
                hit_count += 1
            if count:
                values[index, :3] = min(1.0, total / count / distance_scale)
                values[index, 3] = 1
        result[rows, cols] = values
    if resolution != n:
        # Nearest resampling preserves valid/invalid classification and does not
        # invent measurements across UV islands. The sampling grid is explicit.
        indices = np.minimum(((np.arange(n) + 0.5) * resolution / n).astype(int), resolution - 1)
        result = result[indices[:, None], indices[None, :]]
        # Reapply precise high-resolution UV coverage; no expanded island edges.
        coverage = np.zeros((n, n), dtype=bool)
        for target in targets:
            rows, cols, _, _ = _rasterize_batched(target["uv"][target["tri_loops"]], n)
            coverage[rows, cols] = True
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
            pixels = _rasterize(targets, ts, kind, n, minimum, maximum, palette, metadata)
    if kind == "SELECTION":
        metadata["selected_faces"] = json.dumps(metadata.get("selected_faces", {}), sort_keys=True)
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
