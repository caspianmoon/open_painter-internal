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


@contextmanager
def targets(ts):
    """Keep evaluated meshes alive only for the duration of this bake."""
    if not ts.assignments or any(not ref.object for ref in ts.assignments):
        raise ValueError("Assign valid target meshes before baking utility maps.")
    scene = getattr(ts, 'id_data', bpy.context.scene)
    if not isinstance(scene, bpy.types.Scene):
        scene = bpy.context.scene
    with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
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
