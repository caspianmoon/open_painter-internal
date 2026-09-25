"""Owned evaluated-mesh scope; callers cache arrays, never mesh references.

The context owns all to_mesh allocations and releases them on success/error.
Render UVs and evaluated corner normals are shared by baking and projection.
"""
from contextlib import contextmanager
import bpy
import numpy as np


def _unit(values):
    return values / np.maximum(np.linalg.norm(values, axis=-1, keepdims=True), 1e-12)


def _object_key(obj):
    return f"{obj.library.filepath if obj.library else ''}|{obj.name_full}"


def _view_layers(scene):
    """The scene's view layers, the artist's own first: the CPU reference must read the
    geometry the viewport (and the native shader preview) draws."""
    active = getattr(bpy.context, 'view_layer', None)
    if active is None or active.id_data != scene:
        return list(scene.view_layers)
    return [active] + [layer for layer in scene.view_layers if layer != active]


def _enabled_collections(layer_collection, found):
    """Collections whose objects have an enabled base under ``layer_collection``."""
    if layer_collection.exclude or layer_collection.collection.hide_viewport:
        return found
    found.add(layer_collection.collection)
    for child in layer_collection.children:
        _enabled_collections(child, found)
    return found


def _has_base_in(layer):
    """Predicate: does an object have an enabled base in ``layer``?

    Read from the collection tree rather than ``ViewLayer.objects``, which lags behind an
    exclude toggle until something syncs the layer, and which still lists an object that is
    disabled in viewports. The tree is walked once here, not per object: ``evaluable`` and
    the bake stages test every target of a set against it.
    """
    enabled = _enabled_collections(layer.layer_collection, set())
    master = layer.layer_collection.collection   # ``users_collection`` leaves the scene collection out
    return lambda obj: not obj.hide_viewport and (
        obj.name in master.objects or any(owner in enabled for owner in obj.users_collection))


def _enabled_in(layer, objects):
    """The first object without an enabled base in ``layer``, or None."""
    has_base = _has_base_in(layer)
    return next((obj for obj in objects if not has_base(obj)), None)


def evaluable(scene, objects):
    """The objects some view layer of ``scene`` gives an enabled base (cheap: no depsgraph)."""
    tests = [_has_base_in(layer) for layer in scene.view_layers]
    return [obj for obj in objects if any(has_base(obj) for has_base in tests)]


def _evaluation(scene, objects):
    """(view layer, its depsgraph) of the first layer (the artist's own first) that evaluates
    every object.

    An object without an enabled base in a layer (excluded collection, disabled in viewports)
    is not evaluated there: ``evaluated_get`` hands back the original, or a copy nothing
    keeps current when the set's pointer to the object gave it a depsgraph node, and
    ``to_mesh`` the base mesh without modifiers; nothing raises. Such a layer is skipped;
    when no layer evaluates an object the caller gets a ValueError instead of the wrong
    geometry.
    """
    objects = list(objects)
    missing = None
    for layer in _view_layers(scene):
        # Bases first: building another layer's depsgraph is the expensive part.
        absent = _enabled_in(layer, objects)
        if absent is None:
            with bpy.context.temp_override(scene=scene, view_layer=layer):
                depsgraph = bpy.context.evaluated_depsgraph_get()
            absent = next((obj for obj in objects if not obj.evaluated_get(depsgraph).is_evaluated), None)
            if absent is None:
                return layer, depsgraph
        missing = missing or absent
    raise ValueError(f'{missing.name} is not evaluated in any view layer of {scene.name} '
                     '(excluded collection or disabled in viewports).')


def depsgraph_for(scene, objects):
    """Depsgraph of the first view layer (the artist's own first) that evaluates every object.

    Every reader of evaluated geometry for a texture set — the CPU reference, bakes and
    their face maps, the doctor, diagnostics, offline analysis — goes through here (or
    ``view_layer_for``) with the assigned targets in ``objects``, so face indices agree
    between them: a per-layer exclusion must not hand one reader another evaluation than
    the rest. Readers of other objects add the ``evaluable`` ones: a high-poly source or a
    generated helper is disabled in viewports on purpose and is read from its original.
    """
    return _evaluation(scene, objects)[1]


def view_layer_for(scene, objects):
    """The view layer ``depsgraph_for`` evaluates ``objects`` in, for callers that stage
    geometry under a context override rather than reading the depsgraph directly."""
    return _evaluation(scene, objects)[0]


class Target(dict):
    """One evaluated mesh's arrays; the RNA triangle list is built only on request."""

    def __missing__(self, key):
        if key == "triangles":
            triangles = self["mesh"].loop_triangles
            self["triangles"] = value = [triangles[int(i)] for i in self["tri_indices"]]
            return value
        raise KeyError(key)


def _triangle_arrays(mesh):
    """Corner/vertex/polygon indices of every loop triangle, without per-triangle RNA."""
    count = len(mesh.loop_triangles)
    vertices = np.empty(count * 3, np.int32)
    mesh.loop_triangles.foreach_get("vertices", vertices)
    loops = np.empty(count * 3, np.int32)
    mesh.loop_triangles.foreach_get("loops", loops)
    material = np.empty(count, np.int32)
    mesh.loop_triangles.foreach_get("material_index", material)
    polygons = np.empty(count, np.int32)
    if hasattr(mesh, "loop_triangle_polygons"):
        mesh.loop_triangle_polygons.foreach_get("value", polygons)
    else:
        mesh.loop_triangles.foreach_get("polygon_index", polygons)
    return vertices.reshape(-1, 3), loops.reshape(-1, 3), material, polygons


@contextmanager
def targets(ts):
    """Keep evaluated meshes alive only for the duration of this bake."""
    if not ts.assignments or any(not ref.object for ref in ts.assignments):
        raise ValueError("Assign valid target meshes before baking utility maps.")
    scene = getattr(ts, 'id_data', bpy.context.scene)
    if not isinstance(scene, bpy.types.Scene):
        scene = bpy.context.scene
    objects = sorted({ref.object for ref in ts.assignments}, key=_object_key)
    for obj in objects:
        if obj.type != "MESH":
            raise ValueError(f"{obj.name} is not a mesh.")
    depsgraph = depsgraph_for(scene, objects)
    temporary, targets = [], []
    try:
        for obj in objects:
            evaluated = obj.evaluated_get(depsgraph)
            mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
            temporary.append(evaluated)
            from ..core.udim import source_uv, local_uv
            uv = source_uv(mesh, ts)
            if uv is None:
                raise ValueError(f"{obj.name} requires a render UV map.")
            slots = {ref.slot_index for ref in ts.assignments if ref.object == obj}
            mesh.calc_loop_triangles()
            tri_vertices, tri_loops, tri_material, tri_polygons = _triangle_arrays(mesh)
            chosen = np.flatnonzero(np.isin(tri_material, np.fromiter(slots, np.int32, len(slots))))
            if not len(chosen):
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
            uv_coords = local_uv(mesh, ts, np.unique(tri_polygons[chosen]))
            used = np.unique(tri_vertices[chosen])
            targets.append(Target({
                "object": obj, "mesh": mesh, "slots": slots, "tri_indices": chosen,
                "tri_vertices": tri_vertices[chosen], "tri_loops": tri_loops[chosen],
                "tri_polygons": tri_polygons[chosen], "tri_material": tri_material[chosen],
                # Every triangle of the object, all slots: the surface reach of a material
                # stroke walks the whole mesh (a slot seam is not a fold), then paints the set's own.
                "all_tri_vertices": tri_vertices, "all_tri_polygons": tri_polygons,
                "positions": coords, "object_normals": loop_normals,
                "world_normals": world_normals, "uv": uv_coords,
                "minimum": coords[used].min(axis=0), "maximum": coords[used].max(axis=0),
                "mirrored": np.linalg.det(world[:3, :3]) < 0,
            }))
        yield targets
    finally:
        for evaluated in temporary:
            evaluated.to_mesh_clear()
