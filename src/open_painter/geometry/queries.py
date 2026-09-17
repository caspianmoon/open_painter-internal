"""Bounded main-thread world-space BVH queries over explicit evaluated meshes.

API contract: https://docs.blender.org/api/current/mathutils.bvhtree.html
Temporary evaluated meshes and acceleration trees live only during one analysis.
"""
from contextlib import contextmanager
import bpy
import numpy as np
from mathutils.bvhtree import BVHTree


@contextmanager
def acceleration(scene, objects):
    from .snapshots import depsgraph_for
    graph = depsgraph_for(scene, objects)
    trees = []
    try:
        for obj in objects:
            evaluated = obj.evaluated_get(graph)
            mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=graph)
            try:
                mesh.calc_loop_triangles()
                vertices = [evaluated.matrix_world @ vertex.co for vertex in mesh.vertices]
                triangles = [tuple(tri.vertices) for tri in mesh.loop_triangles]
                if triangles:
                    trees.append(BVHTree.FromPolygons(vertices, triangles, all_triangles=True))
            finally:
                evaluated.to_mesh_clear()
        yield trees
    finally:
        trees.clear()


def distance(trees, points, maximum):
    return nearest(trees, points, maximum)[0]


def nearest(trees, points, maximum):
    """(distance clipped to ``maximum``, index of the nearest tree or -1) per point."""
    values = np.full(len(points), maximum, np.float32)
    which = np.full(len(points), -1, np.int64)
    for index, point in enumerate(points):
        for tree_index, tree in enumerate(trees):
            _, _, _, value = tree.find_nearest(point, maximum)
            if value is not None and value < values[index]:
                values[index], which[index] = value, tree_index
    return values, which


def exposure(trees, points, normals, direction, maximum):
    values = np.ones(len(points), np.float32)
    epsilon = max(maximum * 1e-5, 1e-6)
    for index, (point, normal) in enumerate(zip(points, normals)):
        origin = point + normal * epsilon
        if any(tree.ray_cast(origin, direction, maximum)[0] is not None for tree in trees):
            values[index] = 0
    return values
