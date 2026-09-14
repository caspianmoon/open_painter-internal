"""Explicit material-slot assignments; never delete user datablocks."""

import bpy

from ..constants import OWNER
from ..utils.ids import new_id
from .project_manager import project_for


def create(scene: bpy.types.Scene, name: str = "Texture Set", resolution=None):
    project = project_for(scene)
    ts = project.texture_sets.add()
    ts.id, ts.name = new_id(), name
    if resolution is not None:
        ts.resolution = str(resolution)
    material = bpy.data.materials.new(f"OP · {name}")
    material[OWNER], material["texture_set_id"] = True, ts.id
    material.use_nodes = True
    material.node_tree.nodes.clear()
    ts.material = material
    project.active_texture_set = len(project.texture_sets) - 1
    from ..evaluation.shader_preview import requested
    if requested(ts):
        from .material_manager import repair
        repair(ts)
    else:
        from ..evaluation.evaluator import LayerEvaluator
        # Explicit CPU mode writes real linear pixels instead of relying on
        # Blender's color-managed generated-color initialization.
        LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
    return ts


def assign(
    scene: bpy.types.Scene, ts, obj: bpy.types.Object, slot_index: int | None = None
) -> None:
    if obj.type != "MESH":
        raise ValueError(f"{obj.name} is not a mesh.")
    if not obj.data.uv_layers:
        raise ValueError(f"No UV map found on {obj.name}. Unwrap the mesh first.")
    if obj.data.users > 1:
        raise ValueError(f"{obj.name} has shared mesh data. Make it single-user before assigning.")
    index = obj.active_material_index if slot_index is None else slot_index
    if index < 0 or (obj.material_slots and index >= len(obj.material_slots)):
        raise ValueError("Material slot does not exist.")
    for other in scene.open_painter.texture_sets:
        for ref in other.assignments:
            if ref.object == obj and ref.slot_index == index:
                if other.id == ts.id:
                    return
                raise ValueError(f"{obj.name} slot {index + 1} already belongs to {other.name}.")
    ref = ts.assignments.add()
    ref.object, ref.object_name, ref.slot_index = obj, obj.name, index
    ref.created_slot = not obj.material_slots
    if ref.created_slot:
        obj.data.materials.append(ts.material)
    else:
        ref.original_material = obj.material_slots[index].material
        obj.material_slots[index].material = ts.material


def unassign(ts, index: int) -> None:
    ref = ts.assignments[index]
    obj = ref.object
    if obj and ref.slot_index < len(obj.material_slots):
        slot = obj.material_slots[ref.slot_index]
        if slot.material == ts.material:
            # Retain the slot to avoid reindexing other texture-set assignments.
            slot.material = ref.original_material
    ts.assignments.remove(index)


def remove(scene: bpy.types.Scene, index: int) -> None:
    ts = scene.open_painter.texture_sets[index]
    while ts.assignments:
        unassign(ts, len(ts.assignments) - 1)
    scene.open_painter.texture_sets.remove(index)
    scene.open_painter.active_texture_set = max(0, index - 1)


def duplicate(scene, index):
    """Copy a texture project without stealing the source object's material slots."""
    from .layer_stack import invalidate, restore, snapshot

    source = scene.open_painter.texture_sets[index]
    name = source.name + " Copy"
    resolution = source.resolution
    layers = [snapshot(item) for item in source.layers]
    maps = [(m.kind, m.image) for m in source.baked_maps]
    settings = snapshot(source.bake)
    surface = {key: getattr(source, key) for key in
               ("anisotropy", "coat_weight", "coat_roughness", "height_distance")}
    ts = create(scene, name, resolution)
    for key, value in surface.items():
        setattr(ts, key, value)
    mapping = {item["id"]: new_id() for item in layers}
    mask_mapping = {mask['id']: new_id() for item in layers for mask in item['masks']}
    effect_mapping = {e['id']: new_id() for item in layers for c in item['channels'] for e in c['effects'] if e['id']}
    anchor_mapping = {**mask_mapping, **effect_mapping}
    for data in layers:
        layer = ts.layers.add()
        restore(data, layer)
        layer.id = mapping[data["id"]]
        layer.parent_id = mapping.get(data["parent_id"], "")
        for channel in layer.channels:
            channel.layer_id = layer.id
            if channel.anchor:
                channel.anchor = anchor_mapping.get(channel.anchor, channel.anchor)
            for effect in channel.effects:
                if effect.id:
                    effect.id = effect_mapping.get(effect.id, effect.id)
            if layer.type == "PAINT" and channel.image:
                channel.image = channel.image.copy()
        for mask in layer.masks:
            mask.id = mask_mapping[mask.id]
            if mask.source == 'REFERENCE':
                mask.reference_layer = mapping.get(mask.reference_layer, mask.reference_layer)
                mask.reference_mask = mask_mapping.get(mask.reference_mask, mask.reference_mask)
            for field in ('reference_anchor', 'detail_height_anchor', 'detail_normal_anchor'):
                value = getattr(mask, field)
                if value:
                    setattr(mask, field, anchor_mapping.get(value, value))
            if mask.source == "PAINT" and mask.image:
                mask.image = mask.image.copy()
    for kind, image in maps:
        prop = ts.baked_maps.add()
        prop.kind = kind
        prop.image = image.copy() if image else None
    restore(settings, ts.bake)
    invalidate(ts)
    return ts
