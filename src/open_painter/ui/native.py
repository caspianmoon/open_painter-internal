"""Painter arrangement using Blender panels, RNA widgets and its native asset shelf."""

from pathlib import Path
from textwrap import wrap

import bpy
from bpy_extras.io_utils import ImportHelper

from ..core.layer_stack import active, channel_for, visible_rows
from ..core.project_manager import active_set, require_set
from . import editor
from .dock import assets, state
from .operators import SafeOperator
from . import library_categories as categories

ASSET_KEY = "open_painter_catalog_entry"


def catalog():
    from ..smart_masks.library import PRESETS
    from ..smart_materials.catalog import CATALOG

    return assets.MATERIALS + [
        (name, "SMART_RECIPE", name, data["layers"][0]["color"], data["layers"][0]["roughness"], "")
        for name, data in CATALOG.items()
    ] + [
        (p[0], "SMART_MASK", p[0], (0.5, 0.5, 0.5), 0.5, "") for p in PRESETS
    ] + categories.extras()


def prepare_assets(*, refresh_sources=False):
    # Images are asset handles only; activation always routes through Painter.
    from ..smart_materials.catalog import CATALOG
    from ..constants import VERSION
    handles = {image.get(ASSET_KEY): image for image in bpy.data.images if ASSET_KEY in image}
    entries = catalog()
    for entry in entries:
        entry_key = categories.key(entry)
        category = categories.KIND_CATEGORY[entry[1]]
        path = (
            Path(__file__).resolve().parents[1]
            / "assets/previews"
            / (entry[0].replace(" ", "_") + ".png")
        )
        if category not in {"MATERIALS", "SMART_MATERIALS", "MASKS"}:
            path = Path("/__open_painter_no_preview__")
        source = (bpy.data.images.get(entry[2]) if entry[1] == "TEXTURE" else
                  bpy.data.textures.get(entry[2]) if entry[1] == "ALPHA" and entry[2] else None)
        image = assets.preview(entry[0]) if path.exists() else handles.get(entry_key)
        if image is None and source:
            image = next((handle for handle in handles.values()
                          if handle.get(categories.SOURCE_KEY) == source
                          and handle.get(categories.ASSET_CATEGORY_KEY) == category), None)
        if not image:
            # Library entries remain usable while a rendered preview is absent.
            # This is explicitly a color swatch, never a claimed material render.
            image = bpy.data.images.new(entry[0], width=32, height=32)
            image.generated_color = (*entry[3], 1)
            image.use_fake_user = True
            image["open_painter_preview_kind"] = "COLOR_SWATCH"
        image[ASSET_KEY] = entry_key
        image[categories.ASSET_CATEGORY_KEY] = category
        if source:
            image[categories.SOURCE_KEY] = source
        old_handle = handles.get(entry_key)
        if old_handle and old_handle != image and old_handle.asset_data:
            old_handle.asset_clear()
            del old_handle[ASSET_KEY]
        image.name = entry[0] if source is None else entry[0] + " · Painter Asset"
        if not image.asset_data:
            image.asset_mark()
        description = (
            "Smart mask: apply to the selected layer or folder"
            if entry[1] == "SMART_MASK"
            else CATALOG[entry[0]]["description"] if entry[1] == "SMART_RECIPE"
            else "Apply to the selected channel or mask" if entry[1] in {"FILTER", "PROCEDURAL", "TEXTURE"}
            else "Texture painting brush" if entry[1] == "BRUSH"
            else "Brush alpha" if entry[1] == "ALPHA"
            else "Open Painter material"
        )
        if (path.exists() or category not in {"MATERIALS", "SMART_MATERIALS", "MASKS"}) and "open_painter_preview_kind" in image:
            del image["open_painter_preview_kind"]
        image.asset_data.description = description + (
            " (Color swatch preview)" if image.get("open_painter_preview_kind") == "COLOR_SWATCH" else ""
        )
        if not path.exists() and ((source and refresh_sources) or image.get("open_painter_preview_version") != VERSION or image.preview is None or not image.preview.image_size[0]):
            import numpy as np
            source_image = source.image if isinstance(source, bpy.types.Texture) else source
            if source_image:
                source_path = bpy.path.abspath(source_image.filepath)
                if source_path and not source_image.is_dirty and Path(source_path).is_file():
                    # Use Blender's native thumbnail loader for file textures;
                    # avoid copying their full raster into Python just to browse.
                    with bpy.context.temp_override(id=image):
                        bpy.ops.ed.lib_id_load_custom_preview(filepath=source_path)
                    image["open_painter_preview_version"] = VERSION
                    continue
                from ..core.image_manager import pixels
                rgba = pixels(source_image)
                ys = np.linspace(0, rgba.shape[0] - 1, 48).astype(int)
                xs = np.linspace(0, rgba.shape[1] - 1, 48).astype(int)
                rgba = rgba[ys[:, None], xs]
            else:
                rgba = categories.make_preview(entry)
            preview = image.preview_ensure()
            preview.image_size = (rgba.shape[1], rgba.shape[0])
            preview.image_pixels_float = rgba.ravel()
            image["open_painter_preview_version"] = VERSION
        if path.exists() and (image.get("open_painter_preview_version") != VERSION or image.preview is None or not image.preview.image_size[0]):
            with bpy.context.temp_override(id=image):
                bpy.ops.ed.lib_id_load_custom_preview(filepath=str(path))
            image["open_painter_preview_version"] = VERSION
    valid = {categories.key(entry) for entry in entries}
    for handle in handles.values():
        if handle.get(ASSET_KEY) not in valid and handle.asset_data:
            handle.asset_clear()


class OP_OT_native_asset(SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.native_asset"
    bl_label = "Apply Painter Asset"
    bl_options = {"UNDO"}
    asset_library_type: bpy.props.EnumProperty(
        items=[
            ("LOCAL", "Local", "", 1),
            ("ALL", "All", "", 2),
            ("CUSTOM", "Custom", "", 100),
            ("ESSENTIALS", "Essentials", "", 3),
        ],
        default="LOCAL",
    )
    asset_library_identifier: bpy.props.StringProperty()
    relative_asset_identifier: bpy.props.StringProperty()
    replace_masks: bpy.props.BoolProperty(options={"HIDDEN"})

    def invoke(self, context, event):
        # Native shelf activation can fire on both press and release.
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            return {"CANCELLED"}
        self.replace_masks = event.ctrl or event.oskey
        return self.execute(context)

    # Blender's shelf invokes this with the active AssetRepresentation in context.
    def run(self, context):
        handle = context.asset.local_id if context.asset else None
        if handle is None and self.asset_library_type == "LOCAL":
            handle = bpy.data.images.get(self.relative_asset_identifier.split("/", 1)[-1])
        if not handle or ASSET_KEY not in handle:
            raise ValueError("Select an Open Painter asset.")
        entry = next((e for e in catalog() if categories.key(e) == handle[ASSET_KEY]), None)
        if entry is None:
            raise ValueError("This asset is no longer available. Refresh the library.")
        region = next(r for r in context.area.regions if r.type == "WINDOW")
        with context.temp_override(region=region):
            self.apply_entry(context, entry, handle.get(categories.SOURCE_KEY))
        context.area.tag_redraw()

    def apply_entry(self, context, entry, source):
        if categories.activate(context, entry, source):
            return
        if entry[1] == "SMART_MASK":
            from ..smart_masks.library import apply, PRESETS
            from ..generators.base import required_maps
            from ..baking.bake_manager import refresh_stale_curvature
            ts = require_set(context)
            preset = next((p for p in PRESETS if p[0] == entry[0]), None)
            refreshed = preset and refresh_stale_curvature(ts, required_maps(preset[1]), context)
            if refreshed:
                self.report({"INFO"}, "Rebaking the edge map in the background; the mask updates when it lands"
                            if refreshed == 'QUEUED' else "Edge map rebaked with smooth curvature before adding the mask")
            apply(ts, entry[0], replace=self.replace_masks)
        elif entry[1] == "SMART_RECIPE":
            from ..smart_materials.composer import apply, requirements
            from ..baking.bake_manager import refresh_stale_curvature
            ts = require_set(context)
            refreshed = refresh_stale_curvature(ts, requirements(entry[0]), context)
            if refreshed:
                self.report({"INFO"}, "Rebaking the edge map in the background; the material updates when it lands"
                            if refreshed == 'QUEUED' else "Edge map rebaked with smooth curvature before adding the material")
            apply(ts, entry[0])
        else:
            assets.apply(context, entry)
        ui = state.get(context.area)
        ui.mask_selected = entry[1] == "SMART_MASK"
        ui.selected_layers = {active(require_set(context)).id}
        context.area.tag_redraw()


class VIEW3D_AST_open_painter(bpy.types.AssetShelf):
    bl_space_type = "VIEW_3D"
    bl_options = {"DEFAULT_VISIBLE", "NO_ASSET_DRAG"}
    bl_default_preview_size = 80
    bl_activate_operator = "OPEN_PAINTER_OT_native_asset"

    @classmethod
    def poll(cls, context):
        return editor.is_area(context.area)

    @classmethod
    def asset_poll(cls, asset):
        handle = asset.local_id
        if not handle or ASSET_KEY not in handle:
            return False
        category = bpy.context.scene.open_painter.asset_category
        return category == "ALL" or handle.get(categories.ASSET_CATEGORY_KEY) == category


def asset_type_buttons(self, context):
    if not editor.is_area(context.area):
        return
    row = self.layout.row(align=True)
    row.label(text="Assets:")
    for identifier, label, _description, _value in categories.CATEGORY_ITEMS:
        row.prop_enum(context.scene.open_painter, "asset_category", identifier, text=label)
    row.operator("open_painter.refresh_assets", text="", icon="FILE_REFRESH")
    category = context.scene.open_painter.asset_category
    if category == "ALPHAS":
        row.operator("open_painter.import_alpha", text="Import Alpha", icon="IMPORT")
    elif category == "TEXTURES":
        row.operator("open_painter.import_texture", text="Import Texture", icon="IMPORT")
    self.layout.separator()


class OP_OT_refresh_assets(SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.refresh_assets"
    bl_label = "Refresh Painter Assets"

    def run(self, context):
        categories.refresh(None, context, refresh_sources=True)


class OP_OT_import_texture(SafeOperator, bpy.types.Operator, ImportHelper):
    bl_idname = "open_painter.import_texture"
    bl_label = "Import Texture to Painter Library"
    bl_options = {"UNDO"}
    filter_glob: bpy.props.StringProperty(default="*.png;*.jpg;*.jpeg;*.tif;*.tiff;*.exr;*.bmp", options={"HIDDEN"})

    def run(self, context):
        image = bpy.data.images.load(self.filepath, check_existing=False)
        try:
            if not image.size[0]:
                raise ValueError("The texture image could not be read.")
            image.pack()
            image.use_fake_user = True
            image["open_painter_library_texture"] = True
        except Exception:
            bpy.data.images.remove(image)
            raise
        context.scene.open_painter.asset_category = "TEXTURES"
        self.report({"INFO"}, "Texture imported. Click its thumbnail to assign it to the selected fill or mask.")


class OP_OT_native_view(SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.native_view"
    bl_label = "Painter View"
    view: bpy.props.EnumProperty(items=[("VIEW_3D", "3D", ""), ("IMAGE_EDITOR", "2D", "")])

    def run(self, context):
        area = context.area
        editor.set_view(area, self.view)
        area.type = self.view
        space = area.spaces.active
        if self.view == "IMAGE_EDITOR":
            ts = active_set(context)
            project = context.scene.open_painter
            space.ui_mode = "PAINT" if project.paint_image else "VIEW"
            from ..evaluation.composite_snapshot import for_view
            space.image = for_view(context.scene, ts, project.blend_channel)
        else:
            space.shading.type = "MATERIAL"
        for prop in (
            "show_region_ui",
            "show_region_toolbar",
            "show_region_tool_header",
            "show_region_asset_shelf",
        ):
            if hasattr(space, prop):
                setattr(space, prop, True)
        if self.view == "VIEW_3D" and context.mode == "OBJECT":
            space.show_region_toolbar = False
        request_focus(area)
        editor.persist(context.screen)
        area.tag_redraw()


class OP_OT_native_snapshot(SafeOperator, bpy.types.Operator):
    bl_idname = 'open_painter.native_snapshot'
    bl_label = 'Refresh 2D Composite Snapshot'
    bl_description = 'Evaluate the selected channel once for the 2D view; shader edits do not refresh this snapshot'

    @classmethod
    def poll(cls, context):
        return (context.area and context.area.type == 'IMAGE_EDITOR' and active_set(context)
                and not context.scene.open_painter.paint_image)

    def run(self, context):
        project = context.scene.open_painter
        if project.paint_image:
            raise ValueError('Finish painting before viewing a composite snapshot')
        from ..evaluation.composite_snapshot import request
        context.space_data.image = request(context.scene, require_set(context), project.blend_channel)
        context.space_data.ui_mode = 'VIEW'
        context.area.tag_redraw()


class OP_OT_native_select(SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.native_select"
    bl_label = "Select Layer Content or Mask"
    layer_id: bpy.props.StringProperty()
    mask: bpy.props.BoolProperty()

    def run(self, context):
        ts = require_set(context)
        ts.active_layer = next(i for i, layer in enumerate(ts.layers) if layer.id == self.layer_id)
        ui = state.get(context.area)
        ui.mask_selected = self.mask
        ui.selected_layers = {self.layer_id}
        from ..painting.paint_session import synchronize

        synchronize(context, mask=self.mask)
        context.area.tag_redraw()


class PainterPanel:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Painter"

    @classmethod
    def poll(cls, context):
        return editor.is_area(context.area)


class OP_PT_native_sets(PainterPanel, bpy.types.Panel):
    bl_label = "Texture Set List"
    bl_order = 0

    def draw(self, context):
        layout = self.layout
        project = context.scene.open_painter
        layout.template_list(
            "OP_UL_texture_sets",
            "native",
            project,
            "texture_sets",
            project,
            "active_texture_set",
            rows=1,
            maxrows=2,
            sort_lock=True,
        )
        row = layout.row(align=True)
        for action, icon in [("ADD", "ADD"), ("ASSIGN", "LINKED"), ("DUPLICATE", "DUPLICATE")]:
            row.operator("open_painter.texture_set", text="", icon=icon).action = action
        ts = active_set(context)
        if ts:
            row.prop(ts, "resolution", text="")
            layout.prop(ts, 'preview_backend')
            if ts.preview_backend == 'CPU':
                layout.prop(ts, 'preview_quality')
            if ts.preview_backend == 'LIVE':
                from ..evaluation.shader_preview import status
                layout.label(text=status(context.scene, ts), icon='INFO')
                if context.area.type == 'IMAGE_EDITOR':
                    from ..evaluation.composite_snapshot import MARKER, stale
                    image = context.space_data.image
                    if project.paint_image:
                        layout.label(text='Paint source canvas', icon='BRUSH_DATA')
                    elif image and image.get(MARKER):
                        channel = image.get('channel', '').replace('_', ' ').title()
                        suffix = ' · out of date' if stale(ts, image) else ''
                        layout.label(text=channel + ' Snapshot' + suffix,
                                     icon='TIME' if stale(ts, image) else 'IMAGE_DATA')
                    elif image:
                        layout.label(text='Source image', icon='IMAGE_DATA')
                    layout.prop(project, 'blend_channel', text='Composite channel')
                    layout.operator('open_painter.native_snapshot', text='Refresh Composite Snapshot',
                                    icon='FILE_REFRESH')
            if ts.preview_backend == 'CPU' and ts.preview_quality != 'FULL' and ts.dirty:
                layout.label(text=f'Preview {min(int(ts.preview_quality), int(ts.resolution))} · final {ts.resolution} pending', icon='TIME')


_row_depths = {}


def draw_generator_controls(layout, ts, layer):
    """The controls an artist reaches for right after adding an effect: the
    generator mask's amount, edge width and contrast, without first selecting
    the mask. The full mask stack stays behind the Edit button."""
    generators = [mask for mask in layer.masks if mask.enabled and mask.source in {"GENERATOR", "NOISE"}]
    if not generators:
        return
    from ..baking.bake_manager import stale_curvature
    from ..baking.jobs import OP_OT_edge_map_refresh
    for mask in generators[:2]:
        box = layout.box()
        title = "Noise" if mask.source == "NOISE" else mask.generator.replace("_", " ").title()
        row = box.row(align=True)
        row.label(text=f"{title} Mask", icon="MOD_MASK")
        op = row.operator("open_painter.native_select", text="Edit Mask", icon="PREFERENCES")
        op.layer_id, op.mask = layer.id, True
        box.prop(mask, "amount", slider=True)
        if mask.source == "GENERATOR" and mask.generator in {"EDGE_WEAR", "CHIPS"}:
            radius = ts.bake.mean_curvature_distance
            box.prop(mask, "edge_width", text=f"Edge Width (of {radius * 100:.1f} cm)", slider=True)
            if OP_OT_edge_map_refresh.running:
                box.label(text="Rebaking the edge map in the background…", icon="TIME")
            elif stale_curvature(ts) is not None:
                box.label(text="Crease-only edge map: rebake for smooth edges", icon="ERROR")
                box.operator("open_painter.edge_map_refresh", text="Rebake Edge Map", icon="IMAGE_DATA")
            elif not any(m.kind == "CURVATURE" and m.image for m in ts.baked_maps):
                box.label(text="Bake a Curvature map for edge wear", icon="ERROR")
        box.prop(mask, "contrast", slider=True)


class OP_UL_native_layers(bpy.types.UIList):
    def filter_items(self, context, data, propname):
        rows = visible_rows(data)
        # filter_items runs once per redraw before the items draw; keep the
        # depths so draw_item does not walk the hierarchy again per row.
        _row_depths[data.as_pointer()] = dict(rows)
        visible = {index for index, _ in rows}
        ordered = [index for index, _ in rows] + [
            i for i in range(len(data.layers)) if i not in visible
        ]
        ranks = {index: rank for rank, index in enumerate(ordered)}
        return (
            [self.bitflag_filter_item if i in visible else 0 for i in range(len(data.layers))],
            [ranks[i] for i in range(len(data.layers))],
        )

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layer = item
        channel = channel_for(layer, context.scene.open_painter.blend_channel)
        depths = _row_depths.get(data.as_pointer())
        if depths is None:
            depths = _row_depths[data.as_pointer()] = dict(visible_rows(data))
        depth = depths.get(index, 0)
        split = layout.split(factor=0.42, align=True)
        row = split.row(align=True)
        row.prop(
            layer, "visible", text="", icon="HIDE_OFF" if layer.visible else "HIDE_ON", emboss=False
        )
        for _ in range(depth):
            row.separator(factor=0.5)
        if layer.type == "FOLDER":
            row.prop(
                layer,
                "expanded",
                text="",
                icon="TRIA_DOWN" if layer.expanded else "TRIA_RIGHT",
                emboss=False,
            )
        op = row.operator(
            "open_painter.native_select",
            text=layer.name,
            icon="FILE_FOLDER" if layer.type == "FOLDER" else "MATERIAL",
            emboss=False,
        )
        op.layer_id, op.mask = layer.id, False
        if layer.masks:
            op = row.operator(
                "open_painter.native_select",
                text="",
                icon="MOD_MASK",
                depress=index == data.active_layer and state.get(context.area).mask_selected,
            )
            op.layer_id, op.mask = layer.id, True
        if any(m.source == "ANCHOR" for m in layer.masks) or any(
                e.kind == "ANCHOR" for p in layer.channels for e in p.effects):
            row.label(text="", icon="ANCHOR_CENTER")
        controls = split.split(factor=0.5, align=True)
        blend_control = controls.row(align=True)
        opacity_control = controls.row(align=True)
        if layer.type == "FOLDER" and layer.group_mode == "PASSTHROUGH":
            blend_control.prop_menu_enum(layer, "group_mode", text="Pass")
        elif channel:
            blend = layer.blend_mode if channel.blend_mode == "INHERIT" else channel.blend_mode
            blend_control.prop_menu_enum(channel, "blend_mode", text=blend.replace("_", " ").title())
        if channel:
            opacity_control.prop(channel, "opacity_percent", text="", slider=False)


class OP_PT_native_layers(PainterPanel, bpy.types.Panel):
    bl_label = "Layers"
    bl_order = 1

    def draw(self, context):
        layout = self.layout
        ts = active_set(context)
        if not ts:
            layout.label(text="Create a texture set to start painting.")
            return
        row = layout.row(align=True)
        row.prop(context.scene.open_painter, "blend_channel", text="")
        row = layout.row(align=True)
        for action, label, icon in [
            ("PAINT", "Paint", "BRUSH_DATA"),
            ("FILL", "Fill", "SHADING_RENDERED"),
            ("FOLDER", "Folder", "FILE_FOLDER"),
        ]:
            row.operator("open_painter.layer", text=label, icon=icon).action = action
        row.menu("OP_MT_native_masks", text="", icon="MOD_MASK")
        row.operator("open_painter.layer", text="", icon="TRASH").action = "DELETE"
        row = layout.row(align=True)
        row.operator("open_painter.catalog_material", text="Material", icon="MATERIAL")
        row.operator("open_painter.catalog_effect", text="Effect", icon="MODIFIER")
        layout.template_list(
            "OP_UL_native_layers",
            "stack",
            ts,
            "layers",
            ts,
            "active_layer",
            rows=3,
            maxrows=3,
            sort_lock=True,
        )
        row = layout.row(align=True)
        for action, label, icon in [
            ("UP", "", "TRIA_UP"),
            ("DOWN", "", "TRIA_DOWN"),
            ("DUPLICATE", "Duplicate", "DUPLICATE"),
            ("PARENT", "Parent", "FILE_FOLDER"),
        ]:
            row.operator("open_painter.layer", text=label, icon=icon).action = action


class OP_MT_native_masks(bpy.types.Menu):
    bl_label = "Add Mask"

    def draw(self, context):
        for source, label in [
            ("PAINT", "Add White Mask"),
            ("BLACK", "Add Black Mask"),
            ("FILL", "Add Fill Mask"),
            ("GENERATOR", "Add Generator"),
            ("BAKED", "Add Mesh Map Mask"),
            ('SCENE', 'Add Scene Field'),
            ('REFERENCE', 'Add Published Input'),
        ]:
            op = self.layout.operator("open_painter.mask", text=label)
            op.action, op.source = "ADD", "PAINT" if source == "BLACK" else source
            op.black = source == "BLACK"
        self.layout.separator()
        op = self.layout.operator("open_painter.choose_procedural", text="Add Procedural Fill", icon="TEXTURE")
        op.target, op.create = "MASK", True
        op = self.layout.operator("open_painter.choose_filter", text="Add Filter", icon="MODIFIER")
        op.target, op.create = "MASK", True
        self.layout.operator("open_painter.anchor", text="Add Anchor Point", icon="ANCHOR_CENTER").action = "ADD_MASK"
        self.layout.separator()
        self.layout.prop(context.scene.open_painter, "asset_category", text="Library")


class OP_PT_native_properties(PainterPanel, bpy.types.Panel):
    bl_label = "Properties"
    bl_order = 2

    def draw(self, context):
        layout = self.layout
        status = context.scene.open_painter.status.strip()
        if status:
            box = layout.box()
            scale = max(.5, context.preferences.system.ui_scale)
            width = max(20, min(70, int(context.region.width / (7.5 * scale)) - 5))
            for index, line in enumerate(wrap(status, width=width)):
                box.label(text=line, icon="INFO" if index == 0 else "BLANK1")
        ts = active_set(context)
        layer = active(ts) if ts else None
        if not layer:
            return
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(layer, "name", text="Name")
        if state.get(context.area).mask_selected and layer.masks:
            layout.label(text="Mask on " + layer.name, icon="MOD_MASK")
            from .properties_panel import OP_PT_masks

            OP_PT_masks.draw(self, context)
            return
        layout.prop(layer, "opacity", slider=True)
        if layer.type != "FOLDER":
            layout.prop(layer, "blend_mode", text="Layer Blend")
        draw_generator_controls(layout, ts, layer)
        if layer.type == "FOLDER":
            layout.prop(layer, "group_mode", text="Blend")
            layout.label(text="Folder masks affect all child layers.")
            return
        if layer.type == "PAINT":
            from ..painting.brush_bridge import draw

            layout.prop(context.scene.open_painter, "paint_channel")
            layout.operator("open_painter.paint", text="Paint", icon="BRUSH_DATA")
            layout.operator('open_painter.material_stamp',text='Material Stamp (4 Channels)',icon='BRUSH_DATA')
            draw(layout, context)
            from .texture_operations import draw_channel_effects
            draw_channel_effects(layout, context, layer)
            return
        layout.separator()
        layout.label(text="MATERIAL", icon="MATERIAL")
        layout.prop(layer,'effect_semantic')
        layout.operator('open_painter.material_state',text='Age / Environment State')
        row = layout.row(align=True)
        row.prop(context.scene.open_painter, "properties_channel", text="Channel")
        channel = channel_for(layer, context.scene.open_painter.properties_channel)
        if channel:
            layout.prop(channel, "enabled", text="Enabled")
            body = layout.column()
            body.enabled = channel.enabled
            from .anchors import draw_anchor_field
            draw_anchor_field(body, ts, "CHANNEL", channel.anchor, text="Content Anchor")
            if channel.anchor:
                body.label(text="Anchor replaces the color and image; detail and filters still apply", icon="INFO")
            body.prop(
                channel,
                "color" if channel.channel in {"BASE_COLOR", "NORMAL", "EMISSION"} else "value",
                text="Color" if channel.channel in {"BASE_COLOR", "NORMAL", "EMISSION"} else "Value",
            )
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
                for key in ("procedural_scale", "procedural_seed"):
                    body.prop(channel, key)
            if channel.detail_image or channel.procedural:
                if channel.channel not in {"HEIGHT", "NORMAL"}:
                    body.prop(channel, "procedural_detail")
                if channel.channel in {"HEIGHT", "NORMAL"}:
                    body.prop(channel, "procedural_height")
            if channel.image or channel.detail_image or channel.procedural:
                body.separator()
                body.label(text="FILL", icon="TEXTURE")
                for key in ("projection", "filtering", "tiling"):
                    body.prop(channel, key)
                body.separator()
                body.label(text="UV TRANSFORM")
                for key in ("scale", "rotation", "offset"):
                    body.prop(channel, key)
                if channel.projection == "TRIPLANAR":
                    body.prop(channel, "triplanar_hardness")
            from .texture_operations import draw_channel_effects
            draw_channel_effects(body, context, layer)
        layout.separator()
        panel_header, panel = layout.panel("painter_material_surface", default_closed=True)
        panel_header.label(text="Surface Response")
        if panel:
            for key in ("anisotropy", "coat_weight", "coat_roughness", "height_distance"):
                panel.prop(ts, key)


class OP_PT_native_library(PainterPanel, bpy.types.Panel):
    bl_label = "Asset Library"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 5

    def draw(self, context):
        self.layout.prop(context.scene.open_painter, "asset_category", text="Show")
        from .assets_panel import OP_PT_assets

        OP_PT_assets.draw(self, context)


def toolbar(self, context):
    if not editor.is_area(context.area):
        return
    row = self.layout.row(align=True)
    row.operator("open_painter.paint", text="Paint", icon="BRUSH_DATA")
    row.operator("open_painter.paint", text="Finish").stop = True
    row.popover(panel="OP_PT_native_bake", text="Bake Mesh Maps")
    row.popover(panel="OP_PT_native_export", text="Export Textures")


class OP_PT_native_bake(PainterPanel, bpy.types.Panel):
    bl_label = "Bake Mesh Maps"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 3

    def draw(self, context):
        from .baking_panel import OP_PT_bake

        OP_PT_bake.draw(self, context)


class OP_PT_native_export(PainterPanel, bpy.types.Panel):
    bl_label = "Export Textures"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 4

    def draw(self, context):
        from .export_panel import OP_PT_export

        OP_PT_export.draw(self, context)


CLASSES = (
    OP_UL_native_layers,
    OP_OT_native_view,
    OP_OT_native_snapshot,
    OP_OT_native_asset,
    OP_OT_refresh_assets,
    OP_OT_import_texture,
    VIEW3D_AST_open_painter,
    OP_OT_native_select,
    OP_PT_native_sets,
    OP_PT_native_layers,
    OP_MT_native_masks,
    OP_PT_native_properties,
    OP_PT_native_library,
    OP_PT_native_bake,
    OP_PT_native_export,
)


_focus_pending = set()


def request_focus(area):
    _focus_pending.add(area.as_pointer())


def focus_area(window, area):
    if area.as_pointer() not in _focus_pending:
        return
    region = next((r for r in area.regions if r.type == "UI" and r.width > 10), None)
    if not region:
        return
    try:
        with bpy.context.temp_override(window=window, area=area, region=region):
            # Categories are writable only after Blender has built this region.
            region.active_panel_category = "Painter"
        shelf_region = next((r for r in area.regions if r.type == "ASSET_SHELF"), region)
        with bpy.context.temp_override(window=window, area=area, region=shelf_region):
            shelf = bpy.context.asset_shelf
            if shelf:
                shelf.asset_library_reference = "LOCAL"
                shelf.show_names = True
        region.tag_redraw()
        area.tag_redraw()
        _focus_pending.discard(area.as_pointer())
    except (AttributeError, TypeError, ValueError):
        pass


# The same native controls also serve the 2D painting editor.
IMAGE_CLASSES = tuple(
    type(
        cls.__name__.replace("native_", "image_"),
        (PainterPanel, bpy.types.Panel),
        {
            "__module__": __name__,
            "bl_space_type": "IMAGE_EDITOR",
            "bl_label": cls.bl_label,
            "bl_order": cls.bl_order,
            "bl_idname": cls.__name__.replace("native_", "image_"),
            "draw": cls.draw,
        },
    )
    for cls in (
        OP_PT_native_sets,
        OP_PT_native_layers,
        OP_PT_native_properties,
        OP_PT_native_bake,
        OP_PT_native_export,
        OP_PT_native_library,
    )
)


class IMAGE_AST_open_painter(bpy.types.AssetShelf):
    bl_space_type = "IMAGE_EDITOR"
    bl_options = {"DEFAULT_VISIBLE", "NO_ASSET_DRAG"}
    bl_default_preview_size = 80
    bl_activate_operator = "OPEN_PAINTER_OT_native_asset"

    @classmethod
    def poll(cls, context):
        return editor.is_area(context.area)

    @classmethod
    def asset_poll(cls, asset):
        return VIEW3D_AST_open_painter.asset_poll(asset)


CLASSES += IMAGE_CLASSES + (IMAGE_AST_open_painter,)
