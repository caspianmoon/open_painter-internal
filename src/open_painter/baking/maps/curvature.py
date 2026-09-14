"""Bake convex edges of the evaluated target into the assigned render UVs.

Two signals are combined by maximum: hard creases above the minimum angle as
fixed-pixel-width lines (the original GEOMETRIC_CREASES_V1 behaviour), and the
convex part of the physical-radius curvature in ``mean_curvature`` so smooth
bevels and tessellated rims register too. This is an edge map of the target
mesh, not a derivative of tangent normals; high-poly normal detail is not
curvature.
"""

from math import cos, radians
import bpy
import numpy as np
from ...core import image_manager as images


def crease_edges(mesh, world, minimum_angle, concave=False):
    """Return convex (or concave) shared mesh edges above the geometric threshold."""
    adjacency = {}
    normals, centers = {}, {}
    transform = world.to_3x3().inverted_safe().transposed()
    for face in mesh.polygons:
        normals[face.index] = (transform @ face.normal).normalized()
        centers[face.index] = world @ face.center
        for edge in face.edge_keys:
            adjacency.setdefault(tuple(sorted(edge)), []).append(face.index)
    limit = cos(radians(minimum_angle))
    result = set()
    for edge, faces in adjacency.items():
        if len(faces) != 2:
            continue  # Open/nonmanifold boundaries are not convex surface creases.
        a, b = faces
        dot = normals[a].dot(normals[b])
        if dot >= limit or dot > 1 - 1e-6:
            continue
        separation = centers[b] - centers[a]
        if concave:
            if normals[a].dot(separation) > 1e-8 and normals[b].dot(separation) < -1e-8:
                result.add(edge)
        elif normals[a].dot(separation) < -1e-8 and normals[b].dot(separation) > 1e-8:
            result.add(edge)
    return result


def rasterize(mesh, world, slots, n, minimum_angle, width, concave=False):
    result = np.zeros((n, n), dtype=np.float32)
    uv_layer = next((uv for uv in mesh.uv_layers if uv.active_render), mesh.uv_layers.active)
    if uv_layer is None:
        raise ValueError("Curvature requires a render UV map.")
    creases = crease_edges(mesh, world, minimum_angle, concave)
    mesh.calc_loop_triangles()
    for tri in mesh.loop_triangles:
        if tri.material_index not in slots:
            continue
        edges = [
            (i, j)
            for i, j in ((0, 1), (1, 2), (2, 0))
            if tuple(sorted((tri.vertices[i], tri.vertices[j]))) in creases
        ]
        if not edges:
            continue
        coords = np.array([uv_layer.data[i].uv[:] for i in tri.loops], dtype=np.float32) * n - 0.5
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


METHOD = "GEOMETRIC_CREASES_V2"


def bake_curvature(ts):
    n = int(ts.resolution)
    values = np.zeros((n, n), dtype=np.float32)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for obj in dict.fromkeys(ref.object for ref in ts.assignments if ref.object):
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            slots = {ref.slot_index for ref in ts.assignments if ref.object == obj}
            current = rasterize(
                mesh, obj.matrix_world, slots, n, ts.bake.curvature_angle, ts.bake.curvature_width
            )
            np.maximum(values, current, out=values)
        finally:
            evaluated.to_mesh_clear()
    # Smooth convex curvature within the physical radius: rims, bevels and
    # dense edges that never exceed the crease angle still become edge wear.
    from ...geometry.snapshots import targets
    from .mean_curvature import convex_map
    with targets(ts) as meshes:
        np.maximum(values, convex_map(meshes, n, ts.bake.mean_curvature_distance,
                                      radians(ts.bake.curvature_angle)), out=values)
    rgba = np.ones((n, n, 4), dtype=np.float32)
    rgba[..., :3] = values[..., None]
    image = images.create(f"{ts.name}_BakedCurvature", n)
    image["open_painter_curvature_method"] = METHOD
    image["mean_curvature_distance"] = float(ts.bake.mean_curvature_distance)
    images.write(image, rgba)
    return image
