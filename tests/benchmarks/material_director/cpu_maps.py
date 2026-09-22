"""Deterministic geometry AO for the Eevee benchmark fixtures.

No scene renderer, shader graph or source texture is used. The fixture builder
registers the returned packed map with Open Painter's normal provenance system.
All fixtures use the same bounded cosine-hemisphere ray sampling. This is fixture
preparation evidence, never a tested Material Director operation.
"""
import math
import time

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from open_painter.baking.maps.utility import _orthogonal
from open_painter.core import image_manager as images
from open_painter.geometry.snapshots import targets, _triangle_arrays, _unit
from open_painter.geometry.uv_rasterizer import interpolate, rasterize

AO_SAMPLING_RESOLUTION = 1024
AO_SAMPLES = 32
AO_DISTANCE = .20
AO_EPSILON = 2e-5


def hemisphere(samples=AO_SAMPLES):
    """Equal-weight cosine-distributed directions; no process/global RNG state."""
    k = np.arange(samples, dtype=np.float64)
    radius = np.sqrt((k + .5) / samples)
    phi = k * (math.pi * (3 - math.sqrt(5)))
    return np.stack((radius * np.cos(phi), radius * np.sin(phi), np.sqrt(1 - radius ** 2)), axis=-1)


def model_tree(scene):
    collection = bpy.data.collections.get("TEST_MODEL")
    if collection is None:
        raise ValueError("CPU fixture AO requires the sterilized TEST_MODEL collection")
    graph = bpy.context.evaluated_depsgraph_get()
    vertices, faces = [], []
    for obj in sorted(collection.all_objects, key=lambda item: item.name):
        if obj.type != "MESH":
            raise ValueError("TEST_MODEL contains a non-mesh object")
        evaluated = obj.evaluated_get(graph)
        mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=graph)
        try:
            mesh.calc_loop_triangles()
            offset = len(vertices)
            vertices.extend(tuple(evaluated.matrix_world @ vertex.co) for vertex in mesh.vertices)
            faces.extend(tuple(offset + i for i in tri.vertices) for tri in mesh.loop_triangles)
        finally:
            evaluated.to_mesh_clear()
    if not faces:
        raise ValueError("CPU fixture AO has no occluding geometry")
    return BVHTree.FromPolygons(vertices, faces, all_triangles=True)


def sample_accessibility(tree, points, normals, *, samples=AO_SAMPLES, distance=AO_DISTANCE,
                         epsilon=AO_EPSILON, progress=None):
    """Accessibility, 1=open and 0=occluded, from genuine geometric intersections."""
    if samples < 1 or distance <= 0 or epsilon <= 0 or epsilon >= distance:
        raise ValueError("Invalid fixed AO sampling parameters")
    normals = _unit(np.asarray(normals, dtype=np.float64))
    points = np.asarray(points, dtype=np.float64)
    if points.shape != normals.shape or points.ndim != 2 or points.shape[1] != 3:
        raise ValueError("AO points and normals must be matching N×3 arrays")
    if not np.isfinite(points).all() or not np.isfinite(normals).all():
        raise ValueError("AO input contains non-finite geometry")
    if np.any(np.linalg.norm(normals, axis=1) < .99):
        raise ValueError("AO input contains degenerate normals")
    tangent = _unit(_orthogonal(normals))
    bitangent = np.cross(normals, tangent)
    local = hemisphere(samples)
    result = np.empty(len(points), np.float32)
    cast = tree.ray_cast
    for start in range(0, len(points), 4096):
        stop = min(len(points), start + 4096)
        directions = (tangent[start:stop, None, :] * local[None, :, 0, None] +
            bitangent[start:stop, None, :] * local[None, :, 1, None] +
            normals[start:stop, None, :] * local[None, :, 2, None]).tolist()
        origins = (points[start:stop] + normals[start:stop] * epsilon).tolist()
        for offset, (origin, rays) in enumerate(zip(origins, directions)):
            origin = Vector(origin)
            open_rays = sum(cast(origin, Vector(direction), distance)[0] is None for direction in rays)
            result[start + offset] = open_rays / samples
        if progress:
            progress(stop, len(points))
    return result


def bake_ao(ts, *, sampling_resolution=AO_SAMPLING_RESOLUTION, samples=AO_SAMPLES,
            distance=AO_DISTANCE, tree=None):
    scene = ts.id_data
    if scene.render.engine != "BLENDER_EEVEE":
        raise ValueError("Fixture AO must leave the benchmark renderer on Eevee")
    started = time.monotonic()
    tree = tree or model_tree(scene)
    n = int(ts.resolution)
    resolution = min(n, int(sampling_resolution))
    if resolution < 1:
        raise ValueError("AO sampling resolution must be positive")
    sampled = np.zeros((resolution, resolution), bool)
    values = np.zeros((resolution, resolution), np.float32)
    coverage = np.zeros((n, n), bool)
    sample_count = 0
    with targets(ts) as surfaces:
        for target in reversed(surfaces):
            full_rows, full_cols, _, _ = rasterize(target["uv"][target["tri_loops"]], n)
            coverage[full_rows, full_cols] = True
            rows, cols, tri, weights = rasterize(target["uv"][target["tri_loops"]], resolution)
            fresh = ~sampled[rows, cols]
            rows, cols, tri, weights = rows[fresh], cols[fresh], tri[fresh], weights[fresh]
            if not len(rows):
                continue
            points = interpolate(weights, target["positions"][target["tri_vertices"][tri]])
            normals = _unit(interpolate(weights, target["world_normals"][target["tri_loops"][tri]]))
            print(f"BENCHMARK_CPU_AO {ts.name}: {len(points)} surface samples × {samples} rays", flush=True)
            values[rows, cols] = sample_accessibility(tree, points, normals, samples=samples, distance=distance)
            sampled[rows, cols] = True
            sample_count += len(points)
        indices = np.minimum(((np.arange(n) + .5) * resolution / n).astype(int), resolution - 1)
        upsampled = values[indices[:, None], indices[None, :]]
        measured = sampled[indices[:, None], indices[None, :]] & coverage
        # Thin UV islands can miss the lower sampling grid. Evaluate those
        # precise output texels rather than inventing fully open/dark values.
        missing = coverage & ~measured
        if missing.any():
            for target in reversed(surfaces):
                rows, cols, tri, weights = rasterize(target["uv"][target["tri_loops"]], n)
                needed = missing[rows, cols]
                rows, cols, tri, weights = rows[needed], cols[needed], tri[needed], weights[needed]
                if not len(rows):
                    continue
                points = interpolate(weights, target["positions"][target["tri_vertices"][tri]])
                normals = _unit(interpolate(weights, target["world_normals"][target["tri_loops"][tri]]))
                upsampled[rows, cols] = sample_accessibility(tree, points, normals, samples=samples, distance=distance)
                missing[rows, cols] = False
                sample_count += len(points)
    output = np.zeros((n, n, 4), np.float32)
    output[coverage, :3] = upsampled[coverage, None]
    output[coverage, 3] = 1
    image = images.create(ts.name + "_BenchmarkGeometryAO", n)
    try:
        image["open_painter_mesh_map"] = "AO"
        image["benchmark_bake_method"] = "CPU_BVH_COSINE_HEMISPHERE_V1"
        image["sampling_resolution"] = resolution
        image["samples"] = samples
        image["ray_count"] = sample_count * samples
        image["max_distance"] = distance
        image["origin_epsilon"] = AO_EPSILON
        image["resampling"] = "Nearest with exact UV coverage and directly sampled missing thin-island texels"
        image["source_appearance_reused"] = False
        images.write(image, output)
    except Exception:
        bpy.data.images.remove(image)
        raise
    print(f"BENCHMARK_CPU_AO_DONE {ts.name}: {time.monotonic() - started:.2f}s", flush=True)
    return image
