"""Native Blender 5.2 texture brush assets and unified settings."""

import bpy


def ensure_brush(context):
    paint = context.scene.tool_settings.image_paint
    if (paint.brush is None or paint.brush.library) and (
        context.object is None or context.object.mode != "TEXTURE_PAINT"
    ):
        raise ValueError(
            "Start painting on a paint layer or painted mask before choosing a brush or alpha."
        )
    if paint.brush is None:
        bpy.ops.brush.asset_activate(
            asset_library_type="ESSENTIALS",
            relative_asset_identifier="brushes/essentials_brushes-mesh_texture.blend/Brush/Paint Hard",
        )
    if paint.brush is None:
        raise ValueError("Choose a Texture Paint brush from Blender’s asset shelf.")
    if paint.brush.library:
        # Blender permits scalar edits on essential brushes but rejects local
        # texture IDs on them. Activate a .blend-local brush asset first.
        from ..constants import OWNER

        source = paint.brush
        source_key = source.library.filepath + "/" + source.name
        local = next(
            (
                b
                for b in bpy.data.brushes
                if not b.library and b.get("open_painter_brush_source") == source_key
            ),
            None,
        )
        if local is None:
            local = source.copy()
            local.name = "OP · " + source.name
            local[OWNER] = True
            local["open_painter_brush_source"] = source_key
            local.use_fake_user = True
            local.asset_mark()
        bpy.ops.brush.asset_activate(
            asset_library_type="LOCAL", relative_asset_identifier="Brush/" + local.name
        )
        if paint.brush != local:
            raise ValueError("Could not activate the local paint brush.")
    return paint.brush


def draw(layout, context):
    paint = context.scene.tool_settings.image_paint
    brush = paint.brush
    if not brush:
        layout.label(text="Enter Paint to load a brush.")
        return
    unified = paint.unified_paint_settings
    for key, toggle in (
        ("size", "use_unified_size"),
        ("strength", "use_unified_strength"),
        ("color", "use_unified_color"),
    ):
        layout.prop(unified if getattr(unified, toggle, False) else brush, key)
    layout.prop(brush, "spacing")
    project = context.scene.open_painter
    if project.paint_mask or project.paint_channel not in {"BASE_COLOR", "NORMAL"}:
        layout.prop(project, "paint_value", text="Paint Value", slider=True)
    layout.prop(brush, "blend", text="Brush Blend")
    layout.template_ID(brush, "mask_texture", new="texture.new")
    row = layout.row(align=True)
    row.operator("open_painter.import_alpha", text="Import Alpha", icon="IMAGE_DATA")
    row.operator("open_painter.brush_action", text="", icon="X").action = "CLEAR_ALPHA"
    if brush.mask_texture:
        layout.prop(brush.mask_texture_slot, "mask_map_mode", text="Alpha Mapping")
        layout.prop(brush.mask_texture_slot, "angle", text="Alpha Rotation")


def color_owner(scene):
    """Return the native owner of the brush color, respecting unified settings."""
    paint = scene.tool_settings.image_paint
    if not paint.brush:
        return None
    return (
        paint.unified_paint_settings
        if paint.unified_paint_settings.use_unified_color
        else paint.brush
    )


def get_value(project):
    owner = color_owner(project.id_data)
    return owner.color[0] if owner else 0.5


def set_value(project, value):
    owner = color_owner(project.id_data)
    if owner:
        owner.color = (value, value, value)
