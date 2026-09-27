"""Explicit material-slot assignments; never delete user datablocks."""

import bpy

from ..constants import OWNER
from ..core.surface_settings import FIELDS as SURFACE_FIELDS
from ..utils.ids import new_id
from .project_manager import project_for


def create(scene: bpy.types.Scene, name: str = "Texture Set", resolution=None):
    project = project_for(scene)
    if not project.texture_sets:
        # A project born here already holds the current format (selector angles
        # in radians); stamped now, it never enters ``cache._migrate_angles`` on
        # load. Only the first set stamps: a legacy file opened before the
        # extension was enabled (no load_post) keeps its old stamp until it is
        # reopened and migrated.
        from ..properties.mesh_selector import FORMAT_VERSION
        project.format_version = FORMAT_VERSION
    ts = project.texture_sets.add()
    from ..evaluation.shader_preview import texture_sets_changed
    texture_sets_changed(scene)   # the array may have been reallocated under live sessions
    ts.id, ts.name = new_id(), name
    from .layer_store import ensure
    ensure(ts)   # the stack's hidden store, before anything adds a layer
    if resolution is not None:
        ts.resolution = str(resolution)
    from ..targets import apply, get
    apply(get(project.target), ts.export)   # a set added after the target was chosen follows it too
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
    if ts.udim_tile:
        from .udim import local_uv
        faces = [p.index for p in obj.data.polygons if p.material_index == index]
        if faces:
            local_uv(obj.data, ts, faces)
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
    from ..evaluation import physical
    physical.forget(ts)
    physical.sync(ts)


def unassign(ts, index: int) -> None:
    ref = ts.assignments[index]
    if ref.udim_original_slot >= 0:
        from .udim import restore_assignment
        restore_assignment(ts, ref)
    obj = ref.object
    if obj and ref.slot_index < len(obj.material_slots):
        slot = obj.material_slots[ref.slot_index]
        if slot.material == ts.material:
            # Retain the slot to avoid reindexing other texture-set assignments.
            slot.material = ref.original_material
    ts.assignments.remove(index)
    from ..evaluation import physical
    physical.forget(ts)
    physical.sync(ts)


def _release_image(owner, field, ts) -> None:
    """Drop a managed image the set alone owns. A datablock the artist picked, or a
    derived one they wired into a material of their own, stays in the file.

    Only the fake user goes (see ``layer_stack.release_image``): the datablock
    stays for an undo of the removal, and the next save purges it along with
    the set's now unused material."""
    image = getattr(owner, field)
    if image is None or image.get("texture_set_id", ts.id) != ts.id:
        return
    from .layer_stack import release_image
    release_image(image)


def remove(scene: bpy.types.Scene, index: int) -> None:
    project = scene.open_painter
    ts = project.texture_sets[index]
    from .shared_stacks import removing
    removing(ts)
    if project.paint_set_id == ts.id or any(entry.set_id == ts.id for entry in project.paint_canvases):
        # The canvas goes with the set: end the session while its mesh state is still resolvable,
        # or the object stays in Texture Paint with the face mask restricted and no canvas.
        if project.paint_correction:
            from ..baking.corrections import end_brush
            end_brush(bpy.context)
        else:
            from ..painting.paint_session import finish
            finish(bpy.context, evaluate=False)
    while ts.assignments:
        unassign(ts, len(ts.assignments) - 1)
    # Outputs, cache images, paint canvases and correction canvases belong to this set
    # alone: a removed set must not leave packed 4k floats behind in the file.
    from ..evaluation.layer_cache import release_layer
    from ..painting.decals import release as release_decal
    from .layer_stack import release_images
    for layer in ts.layers:
        release_layer(layer)
        release_decal(layer)
        release_images(layer)
    for prop in (*ts.outputs, *ts.baked_maps):
        _release_image(prop, "image", ts)
    material = ts.material
    if material is not None and material.get(OWNER) and material.users <= 1 and material.use_nodes:
        # The set's material outlives the set unused, and its output nodes would
        # keep the released images users: packed and written once more, purged
        # only on the save after. An artist who put the material on a mesh of
        # their own keeps it showing the outputs.
        for node in material.node_tree.nodes:
            if node.bl_idname == "ShaderNodeTexImage" and node.image is not None and node.image.get(OWNER):
                node.image = None
        from .texture_set_node import release as release_node_tree
        release_node_tree(ts, material)
    from ..baking.corrections import clear as clear_corrections
    clear_corrections(ts)
    store = ts.layer_store
    project.texture_sets.remove(index)
    from .layer_store import release
    release(store)   # the stack's store goes with its set (undo brings both back)
    from ..evaluation.shader_preview import texture_sets_changed
    texture_sets_changed(scene)   # the sets that followed moved down one slot
    scene.open_painter.active_texture_set = max(0, index - 1)


def _set_by_id(project, identifier):
    return next((item for item in project.texture_sets if item.id == identifier), None)


def duplicate(scene, index):
    """Copy a texture project without stealing the source object's material slots."""
    from .image_manager import clone
    from .layer_stack import copy_ids, replay, restore, snapshot

    source = scene.open_painter.texture_sets[index]
    name = source.name + " Copy"
    resolution = source.resolution
    layers = [snapshot(item) for item in source.layers]
    maps = [(m.kind, m.image) for m in source.baked_maps]
    settings = snapshot(source.bake)
    surface = {key: getattr(source, key) for key in
               (*SURFACE_FIELDS, "udim_tile", "udim_group", "udim_name", "udim_source_uv")}
    # Read everything from ``source`` before ``create`` grows the collection: the reference is stale after that.
    from ..aging import engine as aging_engine
    aging_settings = aging_engine.export_settings(source.aging) if source.aging.folder_id else None
    source_id = source.id
    ts = create(scene, name, resolution)
    for key, value in surface.items():
        setattr(ts, key, value)
    if ts.udim_tile:
        # An independent copy must not silently overwrite the source tile's export.
        ts.udim_group = new_id()
        ts.udim_name += ' Copy ' + ts.id[:8]
    ids, tokens = copy_ids(layers)
    mapping = {item["id"]: ids[item["id"]] for item in layers}
    from ..evaluation.anchors import derived_mapping
    anchor_mapping = {**ids, **derived_mapping(mapping)}
    from ..painting.strokes import copy_key
    project = scene.open_painter
    stroke_keys = {}   # the source's stroke keys -> keys of the copy's own strokes
    with replay(ts):
        for data in layers:
            layer = ts.layers.add()
            restore(data, layer)
            layer.id = mapping[data["id"]]
            layer.parent_id = mapping.get(data["parent_id"], "")
            if data["id"] in tokens:
                layer.module_instance = tokens[data["id"]]
            for row in layer.cache:
                # A cached image belongs to one layer; the copy rebuilds its own.
                row.image = None
                row.provenance = ''
            # The copy renders copies of the strokes the source set may render, under keys
            # of its own scoped to the copy (0.13 hardening, H2): sharing the source's keys
            # would let a stroke painted on either set afterwards reach the other, and a
            # scoped stroke of the source would not render on the copy at all.
            if getattr(layer, 'stroke_key', ''):
                key = layer.stroke_key
                if key not in stroke_keys:
                    stroke_keys[key] = copy_key(project, key, _set_by_id(project, source_id), ts)
                layer.stroke_key = stroke_keys[key]
            for channel in layer.channels:
                channel.layer_id = layer.id
                if channel.anchor:
                    channel.anchor = anchor_mapping.get(channel.anchor, channel.anchor)
                for effect in channel.effects:
                    if effect.id:
                        effect.id = ids.get(effect.id, effect.id)
                if layer.type == "PAINT" and channel.image:
                    channel.image = clone(channel.image)
            for mask in layer.masks:
                mask.id = ids[mask.id]
                if mask.source == 'STROKES' and mask.stroke_key:
                    key = mask.stroke_key
                    if key not in stroke_keys:
                        stroke_keys[key] = copy_key(project, key, _set_by_id(project, source_id), ts)
                    mask.stroke_key = stroke_keys[key]
                if mask.parent_id:   # mask group membership (0.9.3) follows the new ids
                    mask.parent_id = ids.get(mask.parent_id, '')
                if mask.source == 'REFERENCE':
                    mask.reference_layer = mapping.get(mask.reference_layer, mask.reference_layer)
                    mask.reference_mask = ids.get(mask.reference_mask, mask.reference_mask)
                for field in ('reference_anchor', 'detail_height_anchor', 'detail_normal_anchor'):
                    value = getattr(mask, field)
                    if value:
                        setattr(mask, field, anchor_mapping.get(value, value))
                if mask.source == "PAINT" and mask.image:
                    mask.image = clone(mask.image)
        for kind, image in maps:
            prop = ts.baked_maps.add()
            prop.kind = kind
            prop.image = clone(image) if image else None
        restore(settings, ts.bake)
        if aging_settings is not None:
            # The copy ages on its own: same history, its own folder (0.10.0).
            aging_engine.import_settings(ts.aging, aging_settings, mapping.get(aging_settings["folder_id"], ""))
        from ..baking.corrections import FIELDS
        for field in FIELDS.values():
            # Correction canvases belong to one set: clearing them in either would orphan the other.
            image = getattr(ts.bake.corrections, field)
            if image is not None:
                setattr(ts.bake.corrections, field, clone(image))
    return ts
