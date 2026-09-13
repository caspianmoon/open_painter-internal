"""Evaluate owned copies with render modifier visibility and subdivision levels.

Blender exposes a viewport depsgraph to Python. Explicitly mirror render switches
on disposable copies before evaluating, rather than silently baking viewport LOD.
"""
import bpy
from contextlib import contextmanager


@contextmanager
def scope(context, originals):
    scene = bpy.data.scenes.new('OP Bake Geometry Check')
    owned_objects, owned_meshes = [], []
    try:
        yield objects(context, originals, scene, owned_objects, owned_meshes)
    finally:
        for obj in reversed(owned_objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for mesh in reversed(owned_meshes):
            bpy.data.meshes.remove(mesh)
        bpy.data.scenes.remove(scene)


def objects(context, originals, scene, owned_objects, owned_meshes):
    graph = context.evaluated_depsgraph_get()
    scene.render.fps = context.scene.render.fps
    scene.render.fps_base = context.scene.render.fps_base
    scene.frame_set(context.scene.frame_current, subframe=context.scene.frame_subframe)
    scene.unit_settings.system = context.scene.unit_settings.system
    scene.unit_settings.scale_length = context.scene.unit_settings.scale_length
    copies = {}
    for original in originals:
        evaluated = original.evaluated_get(graph)
        obj = original.copy()
        owned_objects.append(obj)
        obj.data = original.data.copy()
        owned_meshes.append(obj.data)
        # Freeze animated parameters/transforms at this frame. Constraints and
        # drivers must not run a second time in a different scene context.
        obj.animation_data_clear()
        obj.parent = None
        obj.constraints.clear()
        obj.matrix_world = evaluated.matrix_world.copy()
        obj.hide_render = obj.hide_viewport = obj.hide_select = False
        for modifier in obj.modifiers:
            value = evaluated.modifiers.get(modifier.name)
            if value:
                for prop in modifier.bl_rna.properties:
                    if prop.is_readonly or prop.identifier in {'rna_type','name','type'}:
                        continue
                    if prop.type in {'BOOLEAN','INT','FLOAT','ENUM','STRING'}:
                        try:
                            setattr(modifier, prop.identifier, getattr(value, prop.identifier))
                        except (AttributeError, TypeError):
                            pass
                for key in value.keys() if modifier.type == 'NODES' else ():
                    if key != '_RNA_UI':
                        item = value[key]
                        modifier[key] = item.original if isinstance(item, bpy.types.ID) else item
            modifier.show_viewport = modifier.show_render
            if modifier.type in {'SUBSURF','MULTIRES'}:
                modifier.levels = modifier.render_levels
        scene.collection.objects.link(obj)
        copies[original] = obj
    for obj in copies.values():
        for modifier in obj.modifiers:
            for prop in modifier.bl_rna.properties:
                if prop.type == 'POINTER' and not prop.is_readonly:
                    value = getattr(modifier, prop.identifier, None)
                    if isinstance(value, bpy.types.Object) and value in copies:
                        setattr(modifier, prop.identifier, copies[value])
    with context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
        graph = context.evaluated_depsgraph_get()
        frozen = []
        for obj in copies.values():
            evaluated = obj.evaluated_get(graph)
            mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=graph)
            owned_meshes.append(mesh)
            for i, slot in enumerate(evaluated.material_slots):
                if i < len(mesh.materials):
                    # Evaluated IDs belong to the depsgraph, not Main. Keeping
                    # one in a persistent mesh can crash the next graph update.
                    mesh.materials[i] = slot.material.original if slot.material else None
            frozen.append((obj, mesh, evaluated.matrix_world.copy()))
        # Evaluate every dependency before replacing any of its inputs.
        for obj, mesh, matrix in frozen:
            obj.modifiers.clear()
            obj.data = mesh
            # Effective OBJECT overrides were captured above. Staged targets
            # must resolve through their owned mesh slots, never artist nodes.
            for slot in obj.material_slots:
                slot.link = 'DATA'
            obj.matrix_world = matrix
    return copies
