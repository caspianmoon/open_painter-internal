"""Cycles bake with temporary targets and exception-safe state restoration."""

import bpy

from ..constants import OWNER
from ..core.image_manager import create
from ..utils.logging import logger


def bake(context: bpy.types.Context, ts, kind: str) -> bpy.types.Image:
    scene = context.scene
    settings = ts.bake
    low = list(dict.fromkeys(ref.object for ref in ts.assignments if ref.object))
    high = list(dict.fromkeys(ref.object for ref in settings.high_objects if ref.object))
    active = context.view_layer.objects.active
    selected = list(context.selected_objects)
    mode = active.mode if active else "OBJECT"
    bake = scene.render.bake
    attributes = (
        "use_selected_to_active",
        "use_clear",
        "margin",
        "normal_space",
        "use_cage",
        "cage_extrusion",
        "max_ray_distance",
        "cage_object",
    )
    saved = {key: getattr(bake, key) for key in attributes}
    engine, samples, device = scene.render.engine, scene.cycles.samples, scene.cycles.device
    visibility = [
        (obj, obj.hide_get(), obj.hide_render, obj.hide_viewport, obj.hide_select)
        for obj in low + high
    ]
    materials = []
    image = create(
        f"{ts.name}_Baked{kind.title()}",
        int(ts.resolution),
        (0.5, 0.5, 1, 1) if kind == "NORMAL" else (1, 1, 1, 1),
    )
    scratch = create("OP Bake Unassigned Slots", 32)
    temp_materials = []
    try:
        if mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
        scene.cycles.samples = settings.samples
        bake.use_selected_to_active = settings.selected_to_active
        bake.margin = settings.margin
        bake.normal_space = "TANGENT"
        bake.use_cage = settings.cage_mode != "NONE"
        bake.cage_extrusion = settings.ray_distance if settings.cage_mode == "AUTO" else 0
        bake.max_ray_distance = settings.ray_distance
        bake.cage_object = settings.cage_object if settings.cage_mode == "CUSTOM" else None
        for obj in low + high:
            obj.hide_set(False)
            obj.hide_render = False
            obj.hide_viewport = False
            obj.hide_select = False
        for index, obj in enumerate(low):
            for other in context.view_layer.objects:
                if other.select_get():
                    other.select_set(False)
            obj.select_set(True)
            context.view_layer.objects.active = obj
            for source in high if settings.selected_to_active else []:
                source.select_set(True)
            assigned = {ref.slot_index for ref in ts.assignments if ref.object == obj}
            for slot_index, slot in enumerate(obj.material_slots):
                material = bpy.data.materials.new("OP Temporary Bake Target")
                material[OWNER] = True
                temp_materials.append(material)
                material.use_nodes = True
                node = material.node_tree.nodes.new("ShaderNodeTexImage")
                node.image = image if slot_index in assigned else scratch
                node.select = True
                material.node_tree.nodes.active = node
                materials.append((obj, slot_index, slot.material))
                slot.material = material
            bake.use_clear = index == 0
            logger.info("Baking %s for %s", kind, obj.name)
            bpy.ops.object.bake(type=kind)
        image.pack()
        return image
    except Exception:
        bpy.data.images.remove(image)
        raise
    finally:
        for obj, index, material in materials:
            obj.material_slots[index].material = material
        for material in temp_materials:
            bpy.data.materials.remove(material)
        bpy.data.images.remove(scratch)
        for key, value in saved.items():
            setattr(bake, key, value)
        scene.cycles.samples, scene.cycles.device = samples, device
        scene.render.engine = engine
        for obj in context.view_layer.objects:
            if obj.select_get():
                obj.select_set(False)
        for obj, hidden, render_hidden, viewport_hidden, select_hidden in visibility:
            obj.hide_set(hidden)
            obj.hide_render = render_hidden
            obj.hide_viewport = viewport_hidden
            obj.hide_select = select_hidden
        for obj in selected:
            obj.select_set(True)
        context.view_layer.objects.active = active
        if active and mode != "OBJECT":
            bpy.ops.object.mode_set(mode=mode)
