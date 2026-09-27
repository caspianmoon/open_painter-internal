"""Searchable native Blender controls for texture sources, filters and recipes."""

import json
import math

import bpy

from ..core.layer_stack import active, channel_for, invalidate
from ..core.project_manager import require_set
from ..utils.ids import new_id
from .dock import state
from .operators import SafeOperator

_enum_cache = {}


def _items(key, values):
    # Blender keeps pointers to dynamic enum strings: retain these tuples.
    if key not in _enum_cache:
        _enum_cache[key] = [(name, name.replace("_", " ").title(), description)
                            for name, description in values]
    return _enum_cache[key]


def procedural_items(self, context):
    from ..texture_engine.procedurals import CATALOG
    return _items("procedurals", CATALOG.items())


def filter_items(self, context):
    from ..texture_engine.filters import CATALOG
    return _items("filters", CATALOG.items())


def material_items(self, context):
    from ..smart_materials.catalog import CATALOG
    from ..smart_materials.portable import entries
    installed = entries(context)
    key = ('materials', tuple((entry[0], entry[2]) for entry in installed))
    if key not in _enum_cache:
        _enum_cache[key] = [(name, name, data["category"] + ": " + data["description"])
                            for name, data in CATALOG.items()] + [
            ('library:' + entry[2], entry[0], 'Installed smart material') for entry in installed]
    return _enum_cache[key]


def effect_items(self, context):
    from ..smart_materials.composer import EFFECTS
    return _items("effects", ((name, "Add an editable masked texture layer") for name in EFFECTS))


def _id_palette(context):
    ts, layer = _layer(context)
    mask = _mask(layer)
    if mask.source != "GENERATOR" or mask.generator not in {"MATERIAL_ID", "OBJECT_ID", "UV_ISLAND"}:
        raise ValueError("Select a Material ID, Object ID or UV Island generator first.")
    image = next((entry.image for entry in ts.baked_maps if entry.kind == mask.generator and entry.image), None)
    if image is None:
        raise ValueError("Bake the " + mask.generator.replace("_", " ").title() + " map before choosing an ID.")
    raw = image.get("id_palette", "{}")
    try:
        palette = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("The baked ID palette is invalid. Rebake this ID map.") from exc
    if not isinstance(palette, dict) or not palette:
        raise ValueError("This image has no named ID palette. Rebake the ID map or choose a color manually.")
    for key, color in palette.items():
        if (not isinstance(key, str) or not isinstance(color, (list, tuple)) or len(color) != 3
                or any(not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1 for value in color)):
            raise ValueError("The baked ID palette is invalid. Rebake this ID map.")
    return ts, mask, image, palette, raw


def id_items(self, context):
    try:
        _ts, _mask_value, image, palette, raw = _id_palette(context)
    except (ValueError, AttributeError):
        key = "no_id_palette"
        if key not in _enum_cache:
            _enum_cache[key] = [("NONE", "Bake an ID map first", "Bake an ID map, then select its ID generator")]
        return _enum_cache[key]
    key = ("id_palette", image.as_pointer(), hash(raw))
    if key not in _enum_cache:
        items = []
        for identity in sorted(palette):
            parts = identity.split("|")
            label = (parts[-2] + " / " + parts[-1].replace("island:", "Island ")) if len(parts) > 2 and parts[-1].startswith("island:") else parts[-1]
            items.append((identity, label or identity, identity))
        _enum_cache[key] = items
    return _enum_cache[key]


def _layer(context):
    ts = require_set(context)
    layer = active(ts)
    if layer is None:
        raise ValueError("Select a layer first.")
    return ts, layer


def _channel(context, layer):
    if layer.type == "FOLDER":
        raise ValueError("Select a paint or fill layer to edit its channel textures.")
    project = context.scene.open_painter
    key = project.paint_channel if layer.type == "PAINT" else project.properties_channel
    prop = channel_for(layer, key)
    if prop is None:
        raise ValueError("Select a valid texture channel.")
    return prop


def _mask(layer, create=False, source=None):
    if create:
        mask = layer.masks.add()
        mask.id = new_id()
        layer.active_mask = len(layer.masks) - 1
    elif layer.masks:
        mask = layer.masks[max(0, min(layer.active_mask, len(layer.masks) - 1))]
    else:
        raise ValueError("Add a mask first.")
    if source:
        mask.source = source
    return mask


def _redraw(context, ts, *, mask=None):
    invalidate(ts)
    if context.area:
        selected = active(ts)
        if selected:
            state.get(context.area).selected_layers = {selected.id}
        if mask is not None:
            state.get(context.area).mask_selected = mask
        context.area.tag_redraw()


class SearchPicker:
    bl_property = "choice"

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}


class OP_OT_choose_procedural(SearchPicker, SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.choose_procedural"
    bl_label = "Choose Procedural Texture"
    bl_options = {"REGISTER", "UNDO"}
    bl_property = "choice"
    choice: bpy.props.EnumProperty(name="Procedural", items=procedural_items)
    # SKIP_SAVE: a button that leaves these unset must not inherit the mask
    # panel's target from the previous click and rewrite the active mask.
    target: bpy.props.EnumProperty(items=[("CHANNEL", "Channel", ""), ("MASK", "Mask", "")], options={"SKIP_SAVE"})
    create: bpy.props.BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})

    def run(self, context):
        ts, layer = _layer(context)
        if self.target == "MASK":
            mask = _mask(layer, self.create, "PROCEDURAL")
            mask.procedural = self.choice
            mask.name = self.choice.replace("_", " ").title()
        else:
            if layer.type != "FILL":
                raise ValueError("Procedural fills belong on fill layers. Paint layers keep painted pixels.")
            prop = _channel(context, layer)
            prop.image = None  # Switch source; preserve the image datablock itself.
            prop.image_name = ""
            prop.detail_image = None
            prop.detail_image_name = ""
            prop.procedural = self.choice
            prop.enabled = True
        _redraw(context, ts, mask=self.target == "MASK")


class OP_OT_clear_procedural(SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.clear_procedural"
    bl_label = "Clear Procedural Fill"
    bl_options = {"REGISTER", "UNDO"}

    def run(self, context):
        ts, layer = _layer(context)
        _channel(context, layer).procedural = ""
        _redraw(context, ts)


class OP_OT_choose_filter(SearchPicker, SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.choose_filter"
    bl_label = "Choose Texture Filter"
    bl_options = {"REGISTER", "UNDO"}
    bl_property = "choice"
    choice: bpy.props.EnumProperty(name="Filter", items=filter_items)
    target: bpy.props.EnumProperty(items=[("CHANNEL", "Channel", ""), ("MASK", "Mask", "")], options={"SKIP_SAVE"})
    create: bpy.props.BoolProperty(default=True, options={"HIDDEN", "SKIP_SAVE"})

    def run(self, context):
        ts, layer = _layer(context)
        if self.target == "MASK":
            mask = _mask(layer, self.create, "FILTER")
            effect = mask.effect
            mask.name = self.choice.replace("_", " ").title()
        else:
            prop = _channel(context, layer)
            if self.create:
                effect = prop.effects.add()
                prop.active_effect = len(prop.effects) - 1
            elif prop.effects:
                effect = prop.effects[max(0, min(prop.active_effect, len(prop.effects) - 1))]
            else:
                raise ValueError("Add a channel filter first.")
        effect.kind, effect.name = self.choice, self.choice.replace("_", " ").title()
        # Neutral defaults depend on the filter: .5 is a useful threshold, but
        # would immediately brighten colors or weaken normal details.
        from ..texture_engine.filter_defaults import initial_amount
        effect.amount = initial_amount(self.choice)
        _redraw(context, ts, mask=self.target == "MASK")


class OP_OT_channel_effect(SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.channel_effect"
    bl_label = "Edit Channel Filter Stack"
    bl_options = {"REGISTER", "UNDO"}
    action: bpy.props.EnumProperty(items=[(name, name.title(), "") for name in ("UP", "DOWN", "DELETE")])

    def run(self, context):
        ts, layer = _layer(context)
        prop = _channel(context, layer)
        if not prop.effects:
            raise ValueError("Add a channel filter first.")
        index = max(0, min(prop.active_effect, len(prop.effects) - 1))
        if self.action == "DELETE":
            from .anchors import broken_labels, report_broken
            effect = prop.effects[index]
            broken = broken_labels(ts, [effect.id] if effect.kind == "ANCHOR" and effect.id else [])
            prop.effects.remove(index)
            prop.active_effect = max(0, index - 1)
            report_broken(self, context, broken)
        else:
            destination = max(0, min(len(prop.effects) - 1, index + (-1 if self.action == "UP" else 1)))
            prop.effects.move(index, destination)
            prop.active_effect = destination
        _redraw(context, ts)


class OP_OT_effect_noise(SearchPicker, SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.effect_noise"
    bl_label = "Choose Driving Noise"
    bl_options = {"REGISTER", "UNDO"}
    bl_property = "choice"
    choice: bpy.props.EnumProperty(name="Noise", items=procedural_items)
    target: bpy.props.EnumProperty(items=[("CHANNEL", "Channel", ""), ("MASK", "Mask", "")])

    def run(self, context):
        ts, layer = _layer(context)
        if self.target == "MASK":
            effect = _mask(layer).effect
        else:
            prop = _channel(context, layer)
            if not prop.effects:
                raise ValueError("Add a channel filter first.")
            effect = prop.effects[max(0, min(prop.active_effect, len(prop.effects) - 1))]
        effect.noise = self.choice
        _redraw(context, ts)


class OP_OT_choose_baked_id(SearchPicker, SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.choose_baked_id"
    bl_label = "Choose Baked ID"
    bl_options = {"REGISTER", "UNDO"}
    bl_property = "choice"
    choice: bpy.props.EnumProperty(name="ID", items=id_items)

    def run(self, context):
        ts, mask, _image, palette, _raw = _id_palette(context)
        if self.choice not in palette:
            raise ValueError("That ID is no longer in the baked palette. Choose an ID again.")
        mask.id_color = palette[self.choice]
        _redraw(context, ts, mask=True)


class OP_OT_catalog_material(SearchPicker, SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.catalog_material"
    bl_label = "Add Smart Material"
    bl_options = {"REGISTER", "UNDO"}
    bl_property = "choice"
    choice: bpy.props.EnumProperty(name="Material", items=material_items)

    def run(self, context):
        from ..smart_materials.composer import apply, requirements
        from ..baking.bake_manager import refresh_stale_curvature
        ts = require_set(context)
        if self.choice.startswith('library:'):
            from .assets_panel import apply_document
            apply_document(context, ts, self.choice[len('library:'):])
            _redraw(context, ts, mask=False)
            return
        refreshed = refresh_stale_curvature(ts, requirements(self.choice), context)
        if refreshed:
            self.report({"INFO"}, "Rebaking the edge map in the background; the material updates when it lands"
                        if refreshed == 'QUEUED' else "Edge map rebaked with smooth curvature before adding the material")
        apply(ts, self.choice)
        _redraw(context, ts, mask=False)


class OP_OT_catalog_effect(SearchPicker, SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.catalog_effect"
    bl_label = "Add Surface Effect"
    bl_options = {"REGISTER", "UNDO"}
    bl_property = "choice"
    choice: bpy.props.EnumProperty(name="Effect", items=effect_items)

    def run(self, context):
        from ..smart_materials.composer import EFFECTS, apply_effect, requirements
        from ..baking.bake_manager import refresh_stale_curvature
        ts = require_set(context)
        refreshed = refresh_stale_curvature(ts, requirements(EFFECTS[self.choice]), context)
        if refreshed:
            self.report({"INFO"}, "Rebaking the edge map in the background; the effect updates when it lands"
                        if refreshed == 'QUEUED' else "Edge map rebaked with smooth curvature before adding the effect")
        apply_effect(ts, self.choice)
        _redraw(context, ts, mask=False)


class OP_UL_texture_effects(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if item.kind == "ANCHOR":
            layout.label(text="", icon="BLANK1")
            layout.prop(item, "name", text="", emboss=False, icon="ANCHOR_CENTER")
            return
        layout.prop(item, "enabled", text="")
        layout.prop(item, "name", text="", emboss=False, icon="MODIFIER")


def draw_effect_settings(layout, effect, target="CHANNEL"):
    from ..evaluation.channel_eval import effect_ui_fields
    from ..evaluation.operation_contracts import SURFACE_KINDS
    layout.use_property_decorate = False
    if effect.kind in SURFACE_KINDS:
        # Seamless surface filters (0.9.4): UV Blur vs Surface Blur.
        row = layout.row(align=True)
        row.prop(effect, "space", expand=True)
        if effect.space == "SURFACE" and effect.kind in {"DIRECTIONAL_BLUR", "DIRECTIONAL_WARP"}:
            layout.prop(effect, "direction")
    from ..evaluation.operation_contracts import POINTWISE
    from ..core.project_manager import active_set
    ts = active_set(bpy.context)
    physical_fields = {}
    if effect.kind not in POINTWISE and effect.kind != "ANCHOR" and ts is not None:
        physical_fields = {"radius": ("physical_radius", "Radius"), "scale": ("feature_size", "Noise Feature Size")}
    for key, label in effect_ui_fields(effect.kind):
        if key == "angle" and effect.space == "SURFACE" and effect.kind in {"DIRECTIONAL_BLUR", "DIRECTIONAL_WARP"}:
            continue   # the world direction replaces the image-space angle
        if key in physical_fields:
            from .physical_scale import draw_size
            if key == "radius":
                draw_size(layout, ts, effect, "radius", *physical_fields[key])
            elif effect.units == "PHYSICAL":
                layout.prop(effect, physical_fields[key][0], text=physical_fields[key][1])
            else:
                layout.prop(effect, key, text=label)
            continue
        if key == "noise":
            layout.operator("open_painter.effect_noise", text=effect.noise.replace("_", " ").title(), icon="TEXTURE").target = target
        elif key == "image":
            layout.template_ID(effect, key, open="image.open")
        else:
            layout.prop(effect, key, text=label)
    layout.prop(effect, "strength", text="Filter Opacity", slider=True)


def draw_channel_effects(layout, context, layer):
    prop = _channel(context, layer)
    layout.separator()
    row = layout.row(align=True)
    row.label(text="FILTERS", icon="MODIFIER")
    op = row.operator("open_painter.choose_filter", text="Add Filter", icon="ADD")
    op.target, op.create = "CHANNEL", True
    row.operator("open_painter.anchor", text="Anchor", icon="ANCHOR_CENTER").action = "ADD_CHANNEL"
    if not prop.effects:
        return
    layout.template_list("OP_UL_texture_effects", "channel", prop, "effects", prop, "active_effect", rows=2)
    row = layout.row(align=True)
    for action, icon in (("UP", "TRIA_UP"), ("DOWN", "TRIA_DOWN"), ("DELETE", "TRASH")):
        row.operator("open_painter.channel_effect", text="", icon=icon).action = action
    effect = prop.effects[max(0, min(prop.active_effect, len(prop.effects) - 1))]
    if effect.kind == "ANCHOR":
        row.label(text="Anchor Point", icon="ANCHOR_CENTER")
        from .anchors import draw_anchor_settings
        draw_anchor_settings(layout, require_set(context), effect)
        layout.label(text="Publishes this channel after the filters above it", icon="INFO")
        return
    op = row.operator("open_painter.choose_filter", text=effect.kind.replace("_", " ").title())
    op.target, op.create = "CHANNEL", False
    body = layout.column()
    body.enabled = effect.enabled
    draw_effect_settings(body, effect)


CLASSES = (
    OP_OT_choose_procedural, OP_OT_clear_procedural, OP_OT_choose_filter,
    OP_OT_channel_effect, OP_OT_effect_noise, OP_OT_choose_baked_id, OP_OT_catalog_material, OP_OT_catalog_effect, OP_UL_texture_effects,
)
