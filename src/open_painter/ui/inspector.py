"""The inspector: controls for whatever the Painter session has selected (UI plan phase 2).

One drawing entry, ``draw(layout, context, target)``, dispatches on the resolved target
kind (ui/selection.py) and is shared by the sidebar panel, the companion Properties
area and the 2D editor. Appearance controls come first — a smart material's authored
master controls, a fill layer's channels and projection, a paint layer's destination
and brush, a mask's stack and levels, a filter's parameters — and publishing, modules,
caching and profiling sit in collapsed sections after them. Drawing reads presentation
data only; nothing here evaluates, bakes or renders.
"""

from ..core.layer_stack import channel_for
from ..core.surface_settings import FIELDS as SURFACE_FIELDS

COLOR_CHANNELS = {"BASE_COLOR", "NORMAL", "EMISSION"}
CHANNEL_SHORT = {"BASE_COLOR": "Color", "ROUGHNESS": "Rough", "METALLIC": "Metal", "NORMAL": "Normal",
                 "HEIGHT": "Height", "AO": "AO", "EMISSION": "Emit", "OPACITY": "Alpha"}


def draw(layout, context, target, *, compact=False):
    """Selection-specific controls, appearance first."""
    if target.ts is None:
        layout.label(text="Create a texture set to start painting.", icon="INFO")
        return
    if target.layer is None:
        draw_set(layout, context, target)
        return
    # A narrow sidebar keeps every label readable: fields carry their own label instead of
    # the split's 40 % column, which would ellipsize essential words.
    layout.use_property_split = not compact
    layout.use_property_decorate = False
    kind = target.kind
    if kind == "MATERIAL":
        draw_material(layout, context, target, compact=compact)
    elif kind == "FOLDER":
        draw_folder(layout, context, target)
    elif kind == "MASK":
        draw_mask(layout, context, target)
    elif kind == "FILTER":
        draw_filter(layout, context, target)
    elif kind == "PAINT":
        draw_paint(layout, context, target)
    else:
        draw_fill(layout, context, target)


# ---- texture set ------------------------------------------------------------------------

def draw_set(layout, context, target):
    ts = target.ts
    objects = sorted({ref.object.name for ref in ts.assignments if ref.object})
    col = layout.column(align=True)
    col.label(text=f"{ts.name} · {ts.resolution} px", icon="TEXTURE")
    col.label(text=("Assigned: " + ", ".join(objects[:3]) + (" …" if len(objects) > 3 else ""))
              if objects else "No object assigned yet", icon="MESH_DATA")
    layout.label(text="Select a layer or a mask to edit it, or apply a material from the shelf.", icon="INFO")


# ---- smart material -----------------------------------------------------------------------

def draw_material(layout, context, target, *, compact=False):
    from ..smart_materials import controls
    ts, folder = target.ts, target.layer
    bindings = controls.bindings(ts, folder)
    box = layout.box()
    header = box.row(align=True)
    header.label(text=folder.module_name or folder.name, icon="MATERIAL")
    if folder.module_version:
        header.label(text=f"v{folder.module_version}")
    if bindings:
        column = box.column()
        column.use_property_split = not compact
        for binding in bindings:
            control = binding.control
            if not binding.ok:
                row = column.row()
                row.alert = True
                row.label(text=f"{control.label}: {binding.reason}", icon="ERROR")
                continue
            if control.kind == "color":
                column.prop(binding.owner, binding.field, text=control.label)
            elif control.computed:
                # One component of the folder's family vector: it reads and writes its members.
                column.prop(binding.owner, binding.field, index=binding.index, text=control.label,
                            slider=control.kind == "factor")
            else:
                column.prop(binding.owner, binding.field, text=control.label, slider=True)
    else:
        box.label(text="No authored master controls; edit its component layers below.", icon="INFO")
    _draw_components(layout, ts, folder)
    draw_folder_settings(layout, context, ts, folder)
    draw_secondary(layout, context, ts, folder, module=True)


def _draw_components(layout, ts, folder):
    from ..smart_materials import controls
    head, body = layout.panel(f"painter_components_{folder.id}", default_closed=True)
    members = controls.components(ts, folder)
    head.label(text=f"Component Layers ({len(members)})", icon="OUTLINER")
    if body is None:
        return
    column = body.column(align=True)
    for layer in members:
        row = column.row(align=True)
        row.prop(layer, "visible", text="", icon="HIDE_OFF" if layer.visible else "HIDE_ON", emboss=False)
        op = row.operator("open_painter.native_select", text=layer.name,
                          icon={"PAINT": "BRUSH_DATA", "FILL": "MATERIAL", "FOLDER": "FILE_FOLDER"}[layer.type],
                          emboss=False)
        op.layer_id = layer.id
        if layer.masks:
            op = row.operator("open_painter.native_select", text="", icon="MOD_MASK")
            op.layer_id, op.mask = layer.id, True


# ---- folder -------------------------------------------------------------------------------

def draw_folder(layout, context, target):
    ts, folder = target.ts, target.layer
    if folder.surface_id:
        box = layout.box()
        box.label(text=folder.surface_family.replace("_", " ").title(), icon="MATERIAL")
        for member in ts.layers:
            if member.type != "FILL" or member.surface_id != folder.surface_id:
                continue
            body = box.column()
            body.label(text=member.name)
            color = channel_for(member, "BASE_COLOR")
            rough = channel_for(member, "ROUGHNESS")
            palette = next((e for e in color.effects if e.id == member.id + ".palette" and e.kind == "GRADIENT_MAP"), None)
            if palette:
                body.prop(palette, "color", text="Palette Dark")
                body.prop(palette, "color2", text="Palette Light")
            else:
                body.prop(color, "color", text="Palette")
            rough_transfer = next((e for e in rough.effects if e.id == member.id + ".roughness-range" and e.kind == "LEVELS"), None)
            if rough_transfer:
                body.prop(rough_transfer, "output_black", text="Roughness Low")
                body.prop(rough_transfer, "output_white", text="Roughness High")
            else:
                body.prop(rough, "value", text="Mean Roughness")
            if color.material_mapping_version == 1:
                for channel in member.channels:
                    if channel.enabled and channel.procedural:
                        body.prop(channel, "material_period_m", text=CHANNEL_SHORT[channel.channel] + " Repeat (m)")
                body.prop(channel_for(member, "HEIGHT"), "material_height_m")
            for mask in member.masks:
                if member.surface_role == "COATING" and mask.surface_role == "PAINT_LOSS":
                    body.prop(mask, "strength", text="Paint-loss Opacity")
                if mask.surface_role == "CHIP_PATTERN" and mask.material_mapping_version and mask.procedural == "DOTS":
                    body.prop(mask, "chip_radius_m")
                if mask.surface_role in {"WEAR_PLACEMENT", "PAINT_LOSS"} and mask.influence.physical_mapping_version:
                    body.prop(mask.influence, "distance_m", text="Edge Reach (m)")
                if member.surface_role == "BAND" and mask.source == "FILTER":
                    body.prop(mask.effect, "black", text="Band Lower Edge")
                    body.prop(mask.effect, "white", text="Band Upper Edge")
        _draw_components(layout, ts, folder)
    draw_folder_settings(layout, context, ts, folder)
    draw_secondary(layout, context, ts, folder, module=True)


def draw_folder_settings(layout, context, ts, folder):
    column = layout.column()
    column.prop(folder, "opacity_percent", text="Group Opacity", slider=True)
    column.prop(folder, "group_mode", text="Group Compositing")
    if folder.group_mode == "ISOLATED":
        column.prop(folder, "blend_mode", text="Group Blend")
    else:
        column.label(text="Passthrough: children blend with the layers below the folder", icon="INFO")
    _draw_mask_summary(column, ts, folder, "Group Mask")


def _draw_mask_summary(layout, ts, layer, label="Mask"):
    row = layout.row(align=True)
    if layer.masks:
        names = ", ".join(mask.name for mask in layer.masks[:3]) + (" …" if len(layer.masks) > 3 else "")
        row.label(text=f"{label}: {names}", icon="MOD_MASK")
        op = row.operator("open_painter.native_select", text="Edit", icon="PREFERENCES")
        op.layer_id, op.mask = layer.id, True
    else:
        row.label(text=f"{label}: none", icon="MOD_MASK")
        row.menu("OP_MT_native_masks", text="Add", icon="ADD")


# ---- fill layer ---------------------------------------------------------------------------

def draw_layer_blend(layout, ts, layer):
    column = layout.column(align=True)
    column.prop(layer, "opacity_percent", text="Layer Opacity", slider=True)
    column.prop(layer, "blend_mode", text="Layer Blend")


def draw_channel_toggles(layout, layer, compact=False):
    """Enabled channels as a toggle row: what this layer contributes."""
    row = layout.row(align=True)
    row.use_property_split = False
    for prop in layer.channels:
        row.prop(prop, "enabled", text=CHANNEL_SHORT.get(prop.channel, prop.channel.title()) if not compact else "",
                 toggle=True)


def draw_fill(layout, context, target):
    ts, layer = target.ts, target.layer
    project = context.scene.open_painter
    draw_channel_toggles(layout, layer)
    row = layout.row(align=True)
    row.prop(project, "properties_channel", text="Edit Channel")
    channel = channel_for(layer, project.properties_channel)
    if channel is not None:
        draw_channel_content(layout, context, ts, layer, channel)
    layout.separator()
    draw_layer_blend(layout, ts, layer)
    if channel is not None:
        _draw_channel_blend(layout, layer, channel)
    _draw_mask_summary(layout, ts, layer)
    from .native import draw_generator_controls
    draw_generator_controls(layout, ts, layer)
    from .decals import draw_decal_controls
    draw_decal_controls(layout, context, ts, layer)
    draw_secondary(layout, context, ts, layer, surface=True)


def _draw_channel_blend(layout, layer, channel):
    head, body = layout.panel("painter_channel_blend", default_closed=True)
    label = channel.channel.replace("_", " ").title()
    head.label(text=f"{label} Blend Override", icon="NODE_COMPOSITING")
    if body is None:
        return
    body.prop(channel, "blend_mode", text=f"{label} Blend")
    body.prop(channel, "opacity_percent", text=f"{label} Opacity", slider=True)
    if channel.blend_mode == "INHERIT":
        body.label(text=f"Inherits the layer blend ({layer.blend_mode.replace('_', ' ').title()})", icon="INFO")
    body.label(text="Channel opacity multiplies the layer opacity", icon="INFO")


def draw_channel_content(layout, context, ts, layer, channel):
    """Content of one fill channel: value or texture, surface detail, projection and scale."""
    body = layout.column()
    body.enabled = channel.enabled
    if not channel.enabled:
        body.label(text=f"{channel.channel.replace('_', ' ').title()} is disabled on this layer", icon="INFO")
    from .anchors import draw_anchor_field
    anchor = draw_anchor_field(body, ts, "CHANNEL", channel.anchor, text="Content Anchor")
    if channel.anchor:
        from .anchors import draw_anchor_channel
        draw_anchor_channel(body, anchor, channel)
        body.label(text="Anchor replaces the color and image; detail and filters still apply", icon="INFO")
    is_color = channel.channel in COLOR_CHANNELS
    body.prop(channel, "color" if is_color else "value", text="Color" if is_color else "Value")
    body.template_ID(channel, "image", open="image.open")
    body.label(text="Surface Detail", icon="TEXTURE")
    body.template_ID(channel, "detail_image", open="image.open")
    row = body.row(align=True)
    source_label = ("Replace with Procedural" if channel.detail_image else
                    channel.procedural.replace("_", " ").title() if channel.procedural else
                    "Choose Procedural")
    row.operator("open_painter.choose_procedural", text=source_label, icon="TEXTURE")
    if channel.procedural and not channel.detail_image:
        row.operator("open_painter.clear_procedural", text="", icon="X")
        from .physical_scale import draw_size
        if channel.material_mapping_version == 1:
            body.prop(channel, "material_period_m")
            body.prop(channel, "material_frame")
            if channel.material_frame == "WORLD":
                for field in ("material_origin", "material_axis_u", "material_axis_v"):
                    body.prop(channel, field)
            from ..authoring.physical_space import resolution_diagnostic
            info = resolution_diagnostic(ts, channel.material_period_m)
            if info["status"] == "UNDER_RESOLVED":
                body.label(text="Detail is below reliable texture sampling", icon="ERROR")
            body.label(text="Source: " + channel.material_source_id[:36], icon="LINKED")
        else:
            draw_size(body, ts, channel, "procedural_scale", "feature_size", "Feature Size")
        body.prop(channel, "procedural_seed")
    if channel.material_mapping_version == 1 and (channel.image or channel.detail_image):
        body.prop(channel, "material_image_extent_m")
        body.prop(channel, "material_frame")
        body.prop(channel, "material_directional")
        if channel.material_frame == "WORLD":
            for field in ("material_origin", "material_axis_u", "material_axis_v"):
                body.prop(channel, field)
    if channel.detail_image or channel.procedural:
        if channel.channel not in {"HEIGHT", "NORMAL"}:
            body.prop(channel, "procedural_detail")
        if channel.channel in {"HEIGHT", "NORMAL"}:
            body.prop(channel, "material_height_m" if channel.material_mapping_version == 1 else "procedural_height")
    if (channel.image or channel.detail_image or channel.procedural) and channel.material_mapping_version != 1:
        head, panel = body.panel("painter_fill_projection", default_closed=False)
        head.label(text="Projection and Scale", icon="ORIENTATION_LOCAL")
        if panel is not None:
            for key in ("projection", "filtering", "tiling"):
                panel.prop(channel, key)
            for key in ("scale", "rotation", "offset"):
                panel.prop(channel, key)
            if channel.projection == "TRIPLANAR":
                panel.prop(channel, "triplanar_hardness")
    from .texture_operations import draw_channel_effects
    draw_channel_effects(body, context, layer)


# ---- paint layer --------------------------------------------------------------------------

def draw_paint_destination(layout, context, target):
    """The actual brush destination, next to the tool."""
    project = context.scene.open_painter
    paint = target.paint
    box = layout.box()
    row = box.row(align=True)
    if paint.kind == "NONE":
        row.label(text="Paint destination: " + paint.reason, icon="ERROR")
        return box
    where = f"{target.layer.name} › {target.mask.name}" if paint.kind == "MASK" and target.mask else \
        f"{target.layer.name} › {paint.label}"
    row.label(text=("Painting " if paint.live else "Paint destination: ") + where, icon="BRUSH_DATA")
    if paint.live:
        row.operator("open_painter.paint", text="Finish", icon="CHECKMARK").stop = True
    else:
        op = row.operator("open_painter.paint", text="Paint", icon="BRUSH_DATA")
        op.mask = paint.kind == "MASK"
    if paint.kind == "LAYER":
        box.prop(project, "paint_channel", text="Paint Channel")
        box.prop(project, "paint_all_sets")
    return box


def draw_paint(layout, context, target):
    ts, layer = target.ts, target.layer
    draw_paint_destination(layout, context, target)
    draw_channel_toggles(layout, layer)
    from ..painting.brush_bridge import draw as draw_brush
    from .vector_brush import draw_material_brush, draw_vector_brush
    head, body = layout.panel("painter_material_brush", default_closed=False)
    head.label(text="Paint Material", icon="MATERIAL")
    if body is not None:
        draw_material_brush(body, context)
    head, body = layout.panel("painter_brush", default_closed=True)
    head.label(text="Paint Channel Brush", icon="BRUSH_DATA")
    if body is not None:
        draw_brush(body, context)
    head, body = layout.panel("painter_paint_tools", default_closed=True)
    head.label(text="Stamps and Stroke Details", icon="CURVE_BEZCURVE")
    if body is not None:
        body.operator("open_painter.material_stamp", text="Material Stamp (4 Channels)", icon="BRUSH_DATA")
        from .strokes_panel import draw_strokes
        from .vector_brush import draw_vector_brush
        draw_vector_brush(body, context)
        draw_strokes(body, context, ts, layer)
    layout.separator()
    draw_layer_blend(layout, ts, layer)
    _draw_mask_summary(layout, ts, layer)
    head, body = layout.panel("painter_paint_filters", default_closed=True)
    head.label(text="Channel Filters", icon="MODIFIER")
    if body is not None:
        from .texture_operations import draw_channel_effects
        draw_channel_effects(body, context, layer)
    draw_secondary(layout, context, ts, layer)


# ---- mask -----------------------------------------------------------------------------------

def draw_mask_header(layout, context, target):
    """View Mask / Back to Material and Add Paint to Mask, with the snapshot's state."""
    from . import mask_view
    ts, layer, mask = target.ts, target.layer, target.mask
    box = layout.box()
    row = box.row(align=True)
    view = mask_view.active(ts)
    viewing = view is not None and view["layer_id"] == layer.id
    if viewing:
        row.operator("open_painter.view_mask", text="Back to Material", icon="MATERIAL").action = "BACK"
        row.operator("open_painter.view_mask", text="", icon="FILE_REFRESH").action = "REFRESH"
    else:
        row.operator("open_painter.view_mask", text="View Mask", icon="HIDE_OFF").action = "VIEW"
    row.operator("open_painter.mask_paint", text="Add Paint to Mask", icon="BRUSH_DATA")
    # Short lines: a narrow sidebar truncates the middle of a long label, which is worse than none.
    if viewing:
        stale = mask_view.stale(ts, view)
        box.label(text="Snapshot out of date · refresh" if stale else "Snapshot current",
                  icon="TIME" if stale else "IMAGE_DATA")
    elif view is not None:
        other = mask_view.viewed_layer(ts)
        box.label(text=f"Viewing {other.name if other else 'another layer'}'s mask", icon="HIDE_OFF")
    paint = target.paint
    if paint.kind == "MASK":
        row = box.row(align=True)
        row.label(text=("Painting" if paint.live else "Paintable mask"), icon="BRUSH_DATA")
        if paint.live:
            row.operator("open_painter.paint", text="Finish", icon="CHECKMARK").stop = True
        else:
            row.operator("open_painter.paint", text="Paint Mask", icon="BRUSH_DATA").mask = True
        if mask is not None and mask.source == "PAINT":
            # What the brush does here, in the words of the correction the mask was added for.
            box.label(text={"ADD": "Paint white to add coverage",
                            "MULTIPLY": "Paint black to remove coverage"}.get(
                          mask.operation, "White shows the layer, black hides"), icon="INFO")
    elif mask is not None and mask.source != "PAINT":
        box.label(text=f"{mask.name}: procedural mask", icon="INFO")
        box.label(text="Corrections are painted above it", icon="BLANK1")
    return box


def draw_mask(layout, context, target):
    from .properties_panel import draw_mask_settings, draw_mask_stack
    ts, layer = target.ts, target.layer
    draw_mask_header(layout, context, target)
    draw_mask_stack(layout, context, ts, layer)
    mask = target.mask
    if mask is None:
        return
    head, body = layout.panel("painter_mask_settings", default_closed=False)
    head.label(text=f"{mask.name or mask.source.title()} Settings", icon="MOD_MASK")
    if body is not None:
        draw_mask_settings(body, context, ts, layer, mask)


# ---- channel filter -------------------------------------------------------------------------

def draw_filter(layout, context, target):
    from .texture_operations import draw_effect_settings
    ts, layer, effect, channel = target.ts, target.layer, target.effect, target.channel
    box = layout.box()
    row = box.row(align=True)
    row.prop(effect, "enabled", text="")
    row.label(text=effect.kind.replace("_", " ").title(), icon="MODIFIER")
    op = row.operator("open_painter.choose_filter", text="Change", icon="MODIFIER")
    op.target, op.create = "CHANNEL", False
    box.label(text=f"Filters {channel.channel.replace('_', ' ').title()} of {layer.name}", icon="INFO")
    body = box.column()
    body.enabled = effect.enabled
    if effect.kind == "ANCHOR":
        from .anchors import draw_anchor_settings
        draw_anchor_settings(body, ts, effect)
    else:
        draw_effect_settings(body, effect)
    head, body = layout.panel("painter_filter_stack", default_closed=False)
    head.label(text="Filter Stack", icon="MODIFIER")
    if body is not None:
        from .texture_operations import draw_channel_effects
        draw_channel_effects(body, context, layer)
    op = layout.operator("open_painter.native_select", text=f"Back to {layer.name}", icon="LOOP_BACK")
    op.layer_id = layer.id


# ---- secondary sections ---------------------------------------------------------------------

def draw_secondary(layout, context, ts, layer, *, module=False, surface=False):
    """Publishing, modules, surface response and performance: after the appearance controls."""
    layout.separator()
    head, body = layout.panel("painter_publish", default_closed=True)
    head.label(text="Published Outputs", icon="ANCHOR_CENTER")
    if body is not None:
        from .anchors import draw_publish_controls
        draw_publish_controls(body, ts, layer)
    if module and layer.type == "FOLDER":
        head, body = layout.panel("painter_module", default_closed=True)
        head.label(text="Module" + (f" · v{layer.module_version}" if layer.module_id else ""), icon="PACKAGE")
        if body is not None:
            from .modules import draw_module_box
            draw_module_box(body, context, ts, layer)
    if surface:
        head, body = layout.panel("painter_material_surface", default_closed=True)
        head.label(text="Surface Response", icon="SHADING_RENDERED")
        if body is not None:
            for key in SURFACE_FIELDS:
                body.prop(ts, key)
    from .performance import draw_performance_section
    draw_performance_section(layout, context, ts, layer)
    head, body = layout.panel("painter_layer_ai", default_closed=True)
    head.label(text="AI Protection", icon="LOCKED")
    if body is not None:
        _draw_protection(body, context, ts, layer)


def _draw_protection(layout, context, ts, layer):
    """AI protection, separate from preview bypass and visibility."""
    from ..authoring import commands, schema
    try:
        locks = commands.locks(ts)
    except schema.AuthoringError as exc:   # invalid saved protection data must not break drawing
        layout.label(text=str(exc), icon="ERROR")
        return
    path = ("layers", layer.id)
    locked = path in locks
    row = layout.row(align=True)
    row.operator("open_painter.director_protect", text=("Unlock Layer" if locked else "Protect Layer"),
                 icon="LOCKED" if locked else "UNLOCKED").kind = "LAYER"
    if layer.type == "PAINT":
        layout.label(text="Paint layers are always protected from AI changes", icon="INFO")
