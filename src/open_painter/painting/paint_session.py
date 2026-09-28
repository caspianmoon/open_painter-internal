"""Native paint targeting, material-slot isolation and raster change detection."""

from time import monotonic

import bpy
import numpy as np

from ..core.image_manager import pixels
from ..core.layer_stack import active, channel_for
from ..core.project_manager import active_set
from .brush_bridge import ensure_brush
from .channel_paint import mark_changed, target

_checks = {}
_rasters = {}
_scratch = {}


def restore_faces(project) -> None:
    """Restore the artist's face selection, including after a save/reload."""
    obj = project.paint_object
    from ..core.udim import restore_paint_uv
    restore_paint_uv(project, obj)
    if obj and obj.type == "MESH":
        mesh = obj.data
        saved = project.paint_face_selection
        if obj.mode == "EDIT":
            # Tab out of texture paint ends the session from Edit Mode, where the
            # edit BMesh is authoritative: a polygon write would be discarded on exit.
            import bmesh
            bm = bmesh.from_edit_mesh(mesh)
            bm.faces.ensure_lookup_table()
            if len(saved) == len(bm.faces):
                for face in bm.faces:   # deselecting a face clears its vertices too: pass first
                    face.select = False
                for face, value in zip(bm.faces, saved):
                    if value == "1":
                        face.select = True
                # No select-mode flush: in vertex mode it would grow the faces from
                # their vertices; the face flags are what leaving Edit Mode writes back.
                bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
        else:
            if len(saved) == len(mesh.polygons):
                mesh.polygons.foreach_set("select", [value == "1" for value in saved])
            mesh.update()
        mesh.use_paint_mask = project.paint_original_face_mask
    project.paint_face_selection = ""
    project.paint_object = None


def restrict_faces(project, obj, ts) -> None:
    """Only assigned material-slot faces may receive a 3D stroke."""
    restore_faces(project)
    mesh = obj.data
    project.paint_object = obj
    project.paint_original_face_mask = mesh.use_paint_mask
    project.paint_face_selection = "".join("1" if p.select else "0" for p in mesh.polygons)
    slots = {r.slot_index for r in ts.assignments if r.object == obj}
    mesh.polygons.foreach_set("select", [p.material_index in slots for p in mesh.polygons])
    mesh.use_paint_mask = True
    mesh.update()


def paintable(ts, layer, mask: bool) -> bool:
    if layer is None or not layer.visible:
        return False
    by_id = {item.id: item for item in ts.layers}
    cursor, seen = layer.parent_id, {layer.id}
    while cursor:
        parent = by_id.get(cursor)
        if parent is None or cursor in seen or not parent.visible:
            return False
        seen.add(cursor)
        cursor = parent.parent_id
    if mask:
        return bool(
            layer.masks
            and layer.masks[min(max(0, layer.active_mask), len(layer.masks) - 1)].source == "PAINT"
        )
    return layer.type == "PAINT"


def update_editors(context, image, painting: bool = True) -> None:
    """Update painter areas, without changing unrelated image editors or viewports."""
    from ..ui.editor import is_area

    for area in context.screen.areas if context.screen else []:
        if area.type == "IMAGE_EDITOR" and (is_area(area) or area == context.area):
            area.spaces.active.image = image
            area.spaces.active.ui_mode = "PAINT" if painting else "VIEW"
            area.tag_redraw()


def begin(context: bpy.types.Context, ts, channel: str, mask: bool = False) -> bpy.types.Image:
    layer = active(ts)
    if not paintable(ts, layer, mask):
        raise ValueError("Select a visible paint layer or painted mask first.")
    obj = context.object
    assigned = [r.object for r in ts.assignments if r.object]
    if obj not in assigned:
        obj = next(iter(assigned), None)
    if obj is None:
        raise ValueError("Assign a UV-unwrapped mesh to this texture set first.")
    if not obj.data.uv_layers:
        raise ValueError(f"No UV map found on {obj.name}.")
    if obj.name not in context.view_layer.objects or not obj.visible_get():
        raise ValueError(f"Show {obj.name} in the active view layer before painting.")
    refs = [r for r in ts.assignments if r.object == obj]
    if any(
        r.slot_index >= len(obj.material_slots)
        or obj.material_slots[r.slot_index].material != ts.material
        for r in refs
    ):
        raise ValueError("Texture-set material slots changed. Repair or reassign the mesh first.")
    project = context.scene.open_painter
    end_correction(context)
    # Flush the old target before changing channel or mask identity.
    detect(context.scene, force=True)
    from ..evaluation.cache import stroke_finished
    stroke_finished(context.scene)
    release_canvases(context.scene)
    release_idle_keys(project)
    image = target(ts, layer, channel, mask)
    if context.object and context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    context.view_layer.objects.active = obj
    obj.select_set(True)
    obj.active_material_index = refs[0].slot_index
    paint = context.scene.tool_settings.image_paint
    paint.mode = "IMAGE"
    paint.canvas = image
    try:
        bpy.ops.object.mode_set(mode="TEXTURE_PAINT")
        ensure_brush(context)
        restrict_faces(project, obj, ts)
        from ..core.udim import paint_uv
        paint_uv(project, obj, [ts])
    except Exception:
        finish(context, evaluate=False)
        raise
    project.paint_image = image
    project.paint_set_id = ts.id
    project.paint_layer_id = layer.id
    project.paint_channel = channel
    project.paint_target_channel = channel
    project.paint_mask = mask
    project.paint_mask_id = (
        layer.masks[min(max(0, layer.active_mask), len(layer.masks) - 1)].id if mask else ""
    )
    _checks[context.scene.as_pointer()] = (monotonic(), None)
    _rasters[context.scene.as_pointer()] = (image.as_pointer(), pixels(image))
    update_editors(context, image)
    return image


CANVAS_ROLE = "Paint Canvas"



def canvas_node(ts, image):
    """An unlinked image node in the set's material that Blender's material paint mode targets."""
    from ..constants import OWNER
    material = ts.material
    if material is None or not material.use_nodes:
        raise ValueError(f"{ts.name} has no display material yet; evaluate it once.")
    nodes = material.node_tree.nodes
    node = next((n for n in nodes if n.get(OWNER) and n.get("role") == CANVAS_ROLE), None)
    if node is None:
        node = nodes.new("ShaderNodeTexImage")
        node[OWNER], node["role"] = True, CANVAS_ROLE
        node.label = "Open Painter · " + CANVAS_ROLE
        node.location = (-900, -900)
    if node.image != image:
        node.image = image
    nodes.active = node
    return node


def drop_canvas_nodes(ts):
    from ..constants import OWNER
    material = ts.material
    if material is None or not material.use_nodes:
        return
    for node in list(material.node_tree.nodes):
        if node.get(OWNER) and node.get("role") == CANVAS_ROLE:
            material.node_tree.nodes.remove(node)


def release_canvases(scene):
    """Drop a previous multi-set session's canvases so detection never scans them again.

    Returns the ids of the sets that took part.
    """
    project = scene.open_painter
    members = []
    for entry in project.paint_canvases:
        member = next((s for s in project.texture_sets if s.id == entry.set_id), None)
        if member is not None:
            drop_canvas_nodes(member)
            members.append(member.id)
        key = (scene.as_pointer(), entry.image.as_pointer() if entry.image else 0)
        for table in (_rasters, _checks, _scratch):
            table.pop(key, None)
    project.paint_canvases.clear()
    return members


def end_correction(context):
    """A bake-correction brush session gives way to a layer session, as the reverse does."""
    if context.scene.open_painter.paint_correction:
        from ..baking.corrections import end_brush
        end_brush(context)


def release_idle_keys(project):
    """Stroke keys that render nothing move over to the paint link.

    Sessions before ``paint_link`` linked the sets through the stroke key, which
    left the layers on the vector-stroke boundary path with no strokes to show.
    """
    from .strokes import strokes_for
    referenced = {mask.stroke_key for ts in project.texture_sets for layer in ts.layers
                  for mask in layer.masks if mask.stroke_key}
    for ts in project.texture_sets:
        for layer in ts.layers:
            key = layer.stroke_key
            if not key or key in referenced or strokes_for(project, key):
                continue
            if not layer.paint_link:
                layer.paint_link = key
            layer.stroke_key = ""


def participants(project, ts, layer, obj):
    """(set, layer) pairs painting the same link on this object: the active set first."""
    from ..core import layer_stack as stack
    from ..utils.ids import new_id
    set_id, layer_id = ts.id, layer.id
    if not layer.paint_link:
        layer.paint_link = new_id()
    link, key = layer.paint_link, layer.stroke_key
    # Adding a layer can synchronize a shared recipe and relocate RNA collections.
    # Keep stable IDs until all structural edits have finished.
    found = [(set_id, layer_id)]
    for other in project.texture_sets:
        if other.id == ts.id or not any(r.object == obj for r in other.assignments):
            continue
        # A layer the vector brush linked through the stroke key is the same brush target.
        shared = ts.shared_stack_id and other.shared_stack_id == ts.shared_stack_id
        linked = next((item for item in other.layers if item.type == "PAINT"
                       and ((shared and item.id == layer_id) or item.paint_link == link
                            or (key and item.stroke_key == key))), None)
        if linked is None:
            linked = stack.add(other, "PAINT", layer.name)
            if key:
                linked.stroke_key = key
        linked.paint_link = link
        found.append((other.id, linked.id))
    return [(member, next(item for item in member.layers if item.id == identifier))
            for member_id, identifier in found
            for member in project.texture_sets if member.id == member_id]


def begin_multi(context, ts, channel):
    """Paint one channel of the active paint layer's link across every set assigned to the object.

    Blender's material paint mode writes each face into its material's active
    image node, so each participating set gets a canvas node pointing at its own
    layer image; the face restriction covers the union of the sets' slots.
    """
    layer = active(ts)
    if not paintable(ts, layer, False):
        raise ValueError("Select a visible paint layer first.")
    obj = context.object
    assigned = [r.object for r in ts.assignments if r.object]
    if obj not in assigned:
        obj = next(iter(assigned), None)
    if obj is None or not obj.data.uv_layers:
        raise ValueError("Assign a UV-unwrapped mesh to this texture set first.")
    project = context.scene.open_painter
    # Material paint mode writes each face through the material on its slot: a slot the
    # artist swapped would receive the stroke in their own material's image node. Checked
    # before participants() so no linked layer is left behind by the refusal.
    for member_ts in project.texture_sets:
        if any(
            r.object == obj
            and (r.slot_index >= len(obj.material_slots)
                 or obj.material_slots[r.slot_index].material != member_ts.material)
            for r in member_ts.assignments
        ):
            raise ValueError("Texture-set material slots changed. Repair or reassign the mesh first.")
    end_correction(context)
    detect(context.scene, force=True)
    from ..evaluation.cache import stroke_finished
    stroke_finished(context.scene)
    release_canvases(context.scene)
    release_idle_keys(project)
    members = participants(project, ts, layer, obj)
    if len(members) == 1:
        return begin(context, ts, channel, False)
    canvases = []
    for member_ts, member_layer in members:
        image = target(member_ts, member_layer, channel, False)
        canvas_node(member_ts, image)
        canvases.append((member_ts, member_layer, image))
    if context.object and context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    context.view_layer.objects.active = obj
    obj.select_set(True)
    paint = context.scene.tool_settings.image_paint
    paint.mode = "MATERIAL"
    try:
        bpy.ops.object.mode_set(mode="TEXTURE_PAINT")
        ensure_brush(context)
        restore_faces(project)
        mesh = obj.data
        project.paint_object = obj
        project.paint_original_face_mask = mesh.use_paint_mask
        project.paint_face_selection = "".join("1" if p.select else "0" for p in mesh.polygons)
        slots = {r.slot_index for member_ts, _ in members for r in member_ts.assignments if r.object == obj}
        mesh.polygons.foreach_set("select", [p.material_index in slots for p in mesh.polygons])
        mesh.use_paint_mask = True
        mesh.update()
        from ..core.udim import paint_uv
        paint_uv(project, obj, [member for member, _ in members])
    except Exception:
        finish(context, evaluate=False)
        raise
    project.paint_canvases.clear()
    for member_ts, member_layer, image in canvases:
        entry = project.paint_canvases.add()
        entry.set_id, entry.layer_id, entry.image = member_ts.id, member_layer.id, image
    primary = canvases[0][2]
    project.paint_image = primary
    project.paint_set_id, project.paint_layer_id = ts.id, layer.id
    project.paint_channel = project.paint_target_channel = channel
    project.paint_mask, project.paint_mask_id = False, ""
    key = context.scene.as_pointer()
    _checks[key] = (monotonic(), None)
    _rasters[key] = (primary.as_pointer(), pixels(primary))
    for _, _, image in canvases[1:]:
        _checks[(key, image.as_pointer())] = (monotonic(), None)
        _rasters[(key, image.as_pointer())] = (image.as_pointer(), pixels(image))
    update_editors(context, primary)
    return primary


def finish(context, evaluate: bool = True) -> None:
    """Flush the actual session target even if the active texture set changed."""
    scene = context.scene
    project = scene.open_painter
    detect(scene, force=True)
    # Deferred stroke work catches up while every participating set is still resolvable.
    from ..evaluation.cache import stroke_finished
    stroke_finished(scene)
    identifier = project.paint_set_id
    image = project.paint_image
    extra_sets = [member for member in release_canvases(scene) if member != identifier]
    if scene.tool_settings.image_paint.mode == "MATERIAL" and extra_sets:
        scene.tool_settings.image_paint.mode = "IMAGE"
    restore_faces(project)
    if context.object and context.object.mode == "TEXTURE_PAINT":
        bpy.ops.object.mode_set(mode="OBJECT")
    paint = scene.tool_settings.image_paint
    if paint.canvas == image:
        paint.canvas = None
    project.paint_image = None
    project.paint_set_id = ""
    project.paint_layer_id = ""
    project.paint_mask_id = ""
    project.paint_target_channel = ""
    project.paint_mask = False
    if project.paint_correction:
        project.paint_correction = ""
        from ..baking.corrections import remove_overlay
        remove_overlay(scene)
    _checks.pop(scene.as_pointer(), None)
    _rasters.pop(scene.as_pointer(), None)
    _scratch.pop(scene.as_pointer(), None)
    if evaluate:
        from ..evaluation.evaluator import LayerEvaluator
        from ..evaluation import shader_preview

        for member_id in [identifier, *extra_sets]:
            target_set = next((s for s in project.texture_sets if s.id == member_id), None)
            if target_set is None:
                continue
            if shader_preview.requested(target_set):
                shader_preview.tick(scene, target_set)
            else:
                LayerEvaluator(scene).evaluate_texture_set(member_id)
    ts = active_set(context)
    preview = (
        next((out.image for out in ts.outputs if out.channel == project.paint_channel), None)
        if ts
        else None
    )
    if ts:
        from ..evaluation.shader_preview import requested
        if requested(ts):
            # Keep the image the artist just painted. A 2D composite is a
            # separate explicit snapshot, never an implicit bake on brush exit.
            preview = image
        else:
            from ..evaluation.live_display import displayed_image
            preview = displayed_image(scene, ts, project.paint_channel)
    update_editors(context, preview, painting=False)


def synchronize(context, mask: bool | None = None) -> bool:
    """Follow selection before a new stroke; stop on non-paintable content."""
    project = context.scene.open_painter
    if not project.paint_image:
        return False
    if not context.object or context.object.mode != "TEXTURE_PAINT":
        finish(context, evaluate=False)
        return False
    if project.paint_correction:
        # A bake-correction session follows no layer selection; it ends explicitly.
        return True
    ts = active_set(context)
    layer = active(ts) if ts else None
    mask = project.paint_mask if mask is None else mask
    if not ts or not paintable(ts, layer, mask):
        finish(context, evaluate=False)
        return False
    selected_mask = (
        layer.masks[min(max(0, layer.active_mask), len(layer.masks) - 1)] if mask else None
    )
    channel = channel_for(layer, project.paint_channel) if not mask else None
    expected = selected_mask.image if mask else channel.image if channel else None
    mask_id = selected_mask.id if mask else ""
    if (
        project.paint_set_id,
        project.paint_layer_id,
        project.paint_target_channel,
        project.paint_mask_id,
    ) == (ts.id, layer.id, project.paint_channel, mask_id):
        if not expected or (channel and not channel.enabled):
            finish(context, evaluate=False)
            return False
        if expected == project.paint_image and context.object == project.paint_object:
            return True
    if project.paint_all_sets and not mask:
        begin_multi(context, ts, project.paint_channel)
    else:
        begin(context, ts, project.paint_channel, mask)
    return True


def detect(scene: bpy.types.Scene, force: bool = False) -> bool:
    changed = _detect_primary(scene, force)
    for entry in list(scene.open_painter.paint_canvases)[1:]:
        changed = _detect_canvas(scene, entry, force) or changed
    return changed


def _detect_canvas(scene, entry, force):
    """Change detection for a secondary canvas of a multi-set session (whole-image marking)."""
    project = scene.open_painter
    image = entry.image
    if image is None:
        return False
    key = (scene.as_pointer(), image.as_pointer())
    now = monotonic()
    last, _ = _checks.get(key, (0, None))
    # A painted canvas stays dirty until it is packed: the same 0.75 s pace as
    # the primary keeps idle ticks from re-reading it whole.
    if not force and (now - last < 0.75 or not image.is_dirty):
        return False
    raster = pixels(image, _scratch.pop(key, None))
    previous = _rasters.get(key)
    if previous is not None:
        _scratch[key] = previous[1]
    _rasters[key] = (image.as_pointer(), raster)
    _checks[key] = (now, None)
    if previous is not None and previous[1].shape == raster.shape and np.array_equal(previous[1], raster):
        return False
    ts = next((s for s in project.texture_sets if s.id == entry.set_id), None)
    layer = next((item for item in ts.layers if item.id == entry.layer_id), None) if ts else None
    if layer is None:
        return False
    image['open_painter_pixel_revision'] = image.get('open_painter_pixel_revision', 0) + 1
    mark_changed(ts, layer, project.paint_target_channel or project.paint_channel, False)
    from ..evaluation import cache
    cache._regions.pop(cache.key_for(scene, ts), None)
    return True


def _detect_primary(scene: bpy.types.Scene, force: bool = False) -> bool:
    project = scene.open_painter
    image = project.paint_image
    if not image:
        return False
    key = scene.as_pointer()
    now = monotonic()
    last, _ = _checks.get(key, (0, None))
    if not force and (now - last < 0.75 or not image.is_dirty):
        return False
    raster = pixels(image, _scratch.pop(key, None))
    previous = _rasters.get(key)
    if previous and previous[0] == image.as_pointer():
        _scratch[key] = previous[1]
    _rasters[key] = (image.as_pointer(), raster)
    _checks[key] = (now, None)
    rows = None
    if previous and previous[0] == image.as_pointer() and previous[1].shape == raster.shape:
        # Bound temporary comparison storage to 256 rows rather than another
        # full-resolution RGBA raster on every live paint check.
        changed = np.zeros(raster.shape[0], dtype=bool)
        for start in range(0, raster.shape[0], 256):
            stop = start + 256
            changed[start:stop] = np.any(raster[start:stop] != previous[1][start:stop], axis=(1, 2))
        rows = np.flatnonzero(changed)
        if not len(rows):
            return False
    ts = next((s for s in project.texture_sets if s.id == project.paint_set_id), None)
    layer = (
        next((item for item in ts.layers if item.id == project.paint_layer_id), None)
        if ts
        else None
    )
    if layer:
        from ..evaluation import cache

        # Native brush writes bypass image_manager.write. Give procedural graph
        # inputs their own pixel revision so unrelated material edits can reuse
        # the painted texture without rereading or uploading it.
        image['open_painter_pixel_revision'] = image.get('open_painter_pixel_revision', 0) + 1

        channel = project.paint_target_channel or project.paint_channel
        masking = bool(project.paint_mask_id) or project.paint_mask
        region_channel = '*' if masking else channel
        output = next((out for out in ts.outputs if out.channel == channel), None)
        old_region = cache._regions.get(cache.key_for(scene, ts))
        regional = (
            rows is not None
            and image.colorspace_settings.name != "sRGB"
            and output
            and raster.shape[:2] == (int(ts.resolution), int(ts.resolution))
            and (
                (not any(item.dirty for item in ts.outputs) if masking else not output.dirty)
                or (old_region and old_region[0] == ts.edit_serial and old_region[1] == region_channel)
            )
        )
        mark_changed(
            ts,
            layer,
            project.paint_target_channel or project.paint_channel,
            bool(project.paint_mask_id) or project.paint_mask,
        )
        if regional:
            low, high = int(rows[0]), int(rows[-1]) + 1
            if old_region and old_region[1] == region_channel:
                low, high = min(low, old_region[2]), max(high, old_region[3])
            cache._regions[cache.key_for(scene, ts)] = (ts.edit_serial, region_channel, low, high, image.as_pointer(), raster)
        else:
            cache._regions.pop(cache.key_for(scene, ts), None)
        return True
    return False


def reset():
    _checks.clear()
    _rasters.clear()
    _scratch.clear()
