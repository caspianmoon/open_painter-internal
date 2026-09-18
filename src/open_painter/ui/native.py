"""Painter arrangement using Blender panels, RNA widgets and its native asset shelf."""

from pathlib import Path
from textwrap import wrap

import bpy
from bpy.app.handlers import persistent
from bpy_extras.io_utils import ImportHelper

from ..constants import BLEND_ITEMS
from ..core import layer_stack
from ..core.layer_stack import active, channel_for, visible_rows
from ..core.project_manager import active_set, require_set
from . import editor, selection
from .dock import assets, state
from .operators import SafeOperator
from ..utils.logging import logger
from . import library_categories as categories

ASSET_KEY = "open_painter_catalog_entry"


def catalog(local=True):
    """Every shelf entry; ``local=False`` leaves out the open file's textures (and never reads ``bpy.data``)."""
    from ..smart_masks.library import PRESETS
    from ..smart_materials.catalog import CATALOG

    return assets.MATERIALS + [
        (name, "SMART_RECIPE", name, data["layers"][0]["color"], data["layers"][0]["roughness"], "")
        for name, data in CATALOG.items()
    ] + [
        (p[0], "SMART_MASK", p[0], (0.5, 0.5, 0.5), 0.5, "") for p in PRESETS
    ] + categories.extras(local)


def handles():
    return [image for image in bpy.data.images if ASSET_KEY in image]


@persistent
def unmark_handles(_):
    """Shelf handles are runtime UI state rebuilt on load: never write them
    into the artist's .blend. Without a fake user or asset mark they have no
    users, so the file writer skips them while they stay usable in session."""
    for image in handles():
        if image.asset_data:
            image.asset_clear()
        image.use_fake_user = False


@persistent
def remark_handles(_):
    if handles() and not bpy.app.background:
        prepare_assets()


def description_of(entry, CATALOG):
    return (
        "Smart mask: apply to the selected layer or folder"
        if entry[1] == "SMART_MASK"
        else CATALOG[entry[0]]["description"] if entry[1] == "SMART_RECIPE"
        else "Apply to the selected channel or mask" if entry[1] in {"FILTER", "PROCEDURAL", "TEXTURE"}
        else "Texture painting brush" if entry[1] == "BRUSH"
        else "Brush alpha" if entry[1] == "ALPHA"
        else "Open Painter material"
    )


def prepare_assets(*, refresh_sources=False):
    # Images are asset handles only; activation always routes through Painter.
    # Only entries that point at data of the open file (imported textures and
    # alphas) get a local handle: the built-in catalog is read from the shelf
    # library (shelf_library.py), so it never crowds the file's image lists.
    from ..smart_materials.catalog import CATALOG
    from ..constants import VERSION
    from . import shelf_library
    handles = {image.get(ASSET_KEY): image for image in bpy.data.images if ASSET_KEY in image}
    entries = catalog()
    for entry in entries:
        entry_key = categories.key(entry)
        category = categories.KIND_CATEGORY[entry[1]]
        if shelf_library.is_builtin(entry):
            stale = handles.pop(entry_key, None)
            if stale is not None:
                # A handle a previous version wrote into this file (or made in
                # this session): the library carries the entry now.
                if stale.asset_data:
                    stale.asset_clear()
                stale.use_fake_user = False
                if not stale.users:
                    bpy.data.images.remove(stale)
            continue
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
        description = description_of(entry, CATALOG)
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
    valid = {categories.key(entry) for entry in entries if not shelf_library.is_builtin(entry)}
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
        from . import shelf_library
        asset = context.asset
        handle = asset.local_id if asset else None
        entry_key, source = None, None
        if asset is not None and handle is None and shelf_library.is_library_asset(asset):
            entry_key = shelf_library.key_of(asset)
        elif handle is None and self.asset_library_type == "LOCAL":
            handle = bpy.data.images.get(self.relative_asset_identifier.split("/", 1)[-1])
        elif handle is None and self.asset_library_identifier == shelf_library.LIBRARY_NAME:
            # Activation without an asset in context (scripts, tests): the
            # identifier ends in the library image's name.
            entry_key = shelf_library.key_for_name(self.relative_asset_identifier.rsplit("/", 1)[-1])
        if handle is not None:
            if ASSET_KEY not in handle:
                raise ValueError("Select an Open Painter asset.")
            entry_key, source = handle[ASSET_KEY], handle.get(categories.SOURCE_KEY)
        if entry_key is None:
            raise ValueError("Select an Open Painter asset.")
        entry = next((e for e in catalog() if categories.key(e) == entry_key), None)
        if entry is None:
            raise ValueError("This asset is no longer available. Refresh the library.")
        region = next(r for r in context.area.regions if r.type == "WINDOW")
        with context.temp_override(region=region):
            self.apply_entry(context, entry, source)
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
        ts = require_set(context)
        layer = active(ts)
        if layer is None:
            context.area.tag_redraw()
            return
        if entry[1] in {"BRUSH", "ALPHA"}:
            # Brushes and alphas change the tool, not the stack: the selected
            # mask stays the paint target.
            kind = selection.ui_state(context).selection_kind
        else:
            kind = selection.MASK if entry[1] == "SMART_MASK" else selection.CONTENT
        selection.select(context, layer.id, kind, ts=ts, synchronize=False)


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
        from . import shelf_library
        category = bpy.context.scene.open_painter.asset_category
        handle = asset.local_id
        if handle is None:
            # The built-in catalog comes from the shelf library; every other
            # library the artist registered stays out of the Painter shelf.
            if not shelf_library.is_library_asset(asset):
                return False
            return category == "ALL" or shelf_library.category_of(asset) == category
        if ASSET_KEY not in handle:
            return False
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
        from . import shelf_library
        shelf_library.ensure()
        categories.refresh(None, context, refresh_sources=True)
        shelf_library.refresh_shelves()


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
            space.image = for_view(context.scene, ts, project.display_channel)
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
        context.space_data.image = request(context.scene, require_set(context), project.display_channel)
        context.space_data.ui_mode = 'VIEW'
        context.area.tag_redraw()


class OP_OT_native_select(SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.native_select"
    bl_label = "Select Layer Content or Mask"
    bl_description = "Select this layer's content, its mask stack or one of its masks as the editing target"
    layer_id: bpy.props.StringProperty()
    mask: bpy.props.BoolProperty()
    # SKIP_SAVE: buttons that leave these unset must not inherit a mask or filter from an earlier click.
    mask_id: bpy.props.StringProperty(options={"SKIP_SAVE"})
    channel: bpy.props.StringProperty(options={"SKIP_SAVE"})
    effect_id: bpy.props.StringProperty(options={"SKIP_SAVE"})

    def run(self, context):
        kind = selection.MASK if (self.mask or self.mask_id) else selection.CONTENT
        selection.select(context, self.layer_id, kind, mask_id=self.mask_id or None,
                         channel=self.channel or None, effect_id=self.effect_id or None,
                         ts=require_set(context))


_drag = {"active": False}


def layer_row_pressed(ts, context):
    """Start the modal reorder when a Painter layer row is pressed.

    Blender applies a list row on mouse press, before any drag can be seen;
    ``active_layer``'s update is that press. Python assignments arrive here
    too, but with another event state or no list under the mouse, and the
    operator declines them.
    """
    if context.area is not None and not _drag["active"]:
        # Whatever moved the index selected that layer: Delete, Duplicate,
        # keyboard navigation and the legacy list never press a native row,
        # and Group and Bypass Selected act on the selection.
        layer = active(ts)
        ui = state.get(context.area)
        if layer is None:
            ui.selected_layers = set()
        elif layer.id not in ui.selected_layers:
            ui.selected_layers = {layer.id}
    if _drag["active"] or context.window is None:
        return
    ui_list = getattr(context, "ui_list", None)
    if ui_list is None or ui_list.bl_idname != "OP_UL_native_layers":
        return
    try:
        bpy.ops.open_painter.native_layer_drag("INVOKE_DEFAULT")
    except RuntimeError:
        pass


def placement_text(ts, target):
    """Words for a drop candidate: before, after or inside which layer."""
    _depth, parent_id, kind, anchor_id = target
    names = {item.id: item.name for item in ts.layers}
    if kind == "FIRST_CHILD":
        return f"Into {names.get(anchor_id, 'folder')} (first)"
    if kind == "END":
        return "Bottom of the stack"
    where = "Above" if kind == "ABOVE" else "Below"
    inside = f" in {names[parent_id]}" if parent_id in names else ""
    return f"{where} {names.get(anchor_id, 'layer')}{inside}"


class OP_OT_native_layer_drag(bpy.types.Operator):
    """Drag a layer up or down the stack; move sideways to change its folder"""

    bl_idname = "open_painter.native_layer_drag"
    bl_label = "Move Layer"
    bl_options = {"UNDO", "INTERNAL"}

    @classmethod
    def poll(cls, context):
        return active_set(context) is not None

    def invoke(self, context, event):
        ts = active_set(context)
        layer = active(ts)
        if layer is None or event.type != "LEFTMOUSE" or event.value != "PRESS":
            return {"CANCELLED"}
        # The press selects the layer content, as the name button used to.
        selection.select(context, layer.id, selection.CONTENT, ts=ts)
        system = context.preferences.system
        # A list row is one widget unit tall; folder levels step sideways by one.
        self.unit = int(round(18 * system.ui_scale) + 2 * system.pixel_size)
        self.threshold = context.preferences.inputs.drag_threshold_mouse * system.ui_scale
        self.layer_id = layer.id
        self.origin = (event.mouse_x, event.mouse_y)
        self.targets = layer_stack.drop_targets(ts, layer.id)
        self.gap, self.depth = layer_stack.slot(ts, layer.id)
        self.saved = layer_stack.arrangement(ts)
        self.current = None
        self.dragging = False
        # Every row crossed is a placement, and the timer keeps running under a
        # modal: hold the shader session so the material rebuilds once, on the drop.
        from ..evaluation import shader_preview
        self.hold = shader_preview.hold(context.scene, ts)
        _drag["active"] = True
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        try:
            return self.track(context, event)
        except Exception:
            # A region that vanished mid-drag must not leave the stack held.
            logger.debug("Layer drag ended", exc_info=True)
            return self.finish(context, "CANCELLED")

    def track(self, context, event):
        ts = active_set(context)
        if ts is None or not any(item.id == self.layer_id for item in ts.layers):
            return self.finish(context, "CANCELLED")
        # Every event the modal still receives proves the gesture is alive; a
        # hold nobody renews expires (shader_preview.HOLD_SECONDS) so a modal
        # cancelled underneath us can never freeze the session for good.
        from ..evaluation import shader_preview
        shader_preview.renew_hold(self.hold)
        if event.type in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"}:
            dx, dy = event.mouse_x - self.origin[0], event.mouse_y - self.origin[1]
            if not self.dragging:
                if abs(dx) < self.threshold and abs(dy) < self.threshold:
                    return {"PASS_THROUGH"}
                self.dragging = True
                context.window.cursor_modal_set("HAND_CLOSED")
                context.workspace.status_text_set(
                    "Drag up or down to reorder, sideways to change folder, Esc to cancel")
            gap = min(len(self.targets) - 1, max(0, self.gap - round(dy / self.unit)))
            wanted = self.depth + round(dx / self.unit)
            target = min(self.targets[gap], key=lambda item: abs(item[0] - wanted))
            if target != self.current:
                layer_stack.place(ts, self.layer_id, target)
                self.current = target
                # The row already sits where it would drop; the status bar says it in words.
                context.workspace.status_text_set(placement_text(ts, target) + " · Esc cancels")
                context.region.tag_redraw()
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            return self.finish(context, "FINISHED" if self.dragging else "CANCELLED")
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS" and self.dragging:
            layer_stack.restore_arrangement(ts, self.saved)
            ts.active_layer = layer_stack.index_of(ts, self.layer_id)
            return self.finish(context, "CANCELLED")
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            # The release was lost to another window; do not hold the stack.
            return self.finish(context, "FINISHED" if self.dragging else "CANCELLED")
        return {"RUNNING_MODAL"} if self.dragging else {"PASS_THROUGH"}

    def finish(self, context, result):
        self.release(context)
        if self.dragging:
            if context.area is not None:
                context.area.tag_redraw()
            return {result}
        # A plain click: leave the release to the widgets underneath.
        return {result, "PASS_THROUGH"}

    def cancel(self, context):
        # The window manager ends a modal without a final event (its window
        # closed, a file loaded, the handler stack was cleared): ``modal`` never
        # runs again, so the drop the user never made is undone here and the
        # hold and the drag flag are released exactly as ``finish`` does them.
        try:
            ts = active_set(context)
            if self.dragging and ts is not None and any(item.id == self.layer_id for item in ts.layers):
                layer_stack.restore_arrangement(ts, self.saved)
                ts.active_layer = layer_stack.index_of(ts, self.layer_id)
        except Exception:
            logger.debug("Layer drag cancelled without its texture set", exc_info=True)
        self.release(context)

    def release(self, context):
        """Let go of everything the drag took: the flag, the shader hold, the cursor and the status text."""
        _drag["active"] = False
        from ..evaluation import shader_preview
        shader_preview.release_hold(self.hold)
        if self.dragging:
            try:
                context.window.cursor_modal_restore()
                context.workspace.status_text_set(None)
            except (AttributeError, RuntimeError):
                # A cancel arrives without the window that showed them.
                pass


class PainterPanel:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Painter"

    @classmethod
    def poll(cls, context):
        return editor.is_area(context.area)


def draw_2d_view(layout, context, ts):
    """What the 2D editor shows, named truthfully: the paint source, a mask snapshot, a
    composite snapshot (with its out-of-date state) or a plain source image."""
    from ..evaluation.composite_snapshot import MARKER, stale
    from . import mask_view
    project = context.scene.open_painter
    image = context.space_data.image
    box = layout.box()
    box.label(text="2D View", icon="IMAGE_DATA")
    view = mask_view.active(ts)
    if project.paint_image:
        box.label(text="Paint Source · the authored canvas, live", icon="BRUSH_DATA")
    elif view is not None and image is not None and image.name == view["image"]:
        out_of_date = mask_view.stale(ts, view)
        box.label(text="Mask Snapshot" + (" · out of date" if out_of_date else " · current"),
                  icon="TIME" if out_of_date else "MOD_MASK")
        row = box.row(align=True)
        row.operator("open_painter.view_mask", text="Refresh", icon="FILE_REFRESH").action = "REFRESH"
        row.operator("open_painter.view_mask", text="Back to Material", icon="MATERIAL").action = "BACK"
        return
    elif image and image.get(MARKER):
        channel = image.get("channel", "").replace("_", " ").title()
        out_of_date = stale(ts, image)
        box.label(text=f"{channel} Composite Snapshot" + (" · out of date" if out_of_date else " · current"),
                  icon="TIME" if out_of_date else "IMAGE_DATA")
    elif image:
        box.label(text="Source Image · not a composite", icon="IMAGE_DATA")
    else:
        box.label(text="No image shown yet", icon="INFO")
    box.prop(project, "display_channel", text="Composite Channel")
    box.operator("open_painter.native_snapshot", text="Refresh Composite Snapshot", icon="FILE_REFRESH")


class OP_PT_native_sets(PainterPanel, bpy.types.Panel):
    bl_label = "Texture Set"
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
        if not ts:
            return
        row.prop(ts, "resolution", text="")
        # The active target at a glance: what it is assigned to, then the settings behind one click.
        objects = sorted({ref.object.name for ref in ts.assignments if ref.object})
        compact = row_metrics(context)[1]
        shown = objects[:2 if compact else 3]
        summary = ", ".join(shown) + (f" +{len(objects) - len(shown)}" if len(objects) > len(shown) else "")
        layout.label(text=(f"Assigned to {summary}" if objects else "Not assigned to any object yet"),
                     icon="MESH_DATA" if objects else "ERROR")
        if ts.preview_backend == 'LIVE':
            from .performance import draw_freeze_status
            draw_freeze_status(layout, context, ts)
        if context.area.type == 'IMAGE_EDITOR':
            draw_2d_view(layout, context, ts)
        if ts.preview_backend == 'CPU' and ts.preview_quality != 'FULL' and ts.dirty:
            layout.label(text=f'Preview {min(int(ts.preview_quality), int(ts.resolution))} · final {ts.resolution} pending', icon='TIME')
        head, body = layout.panel("painter_set_settings", default_closed=True)
        head.label(text="Texture Set Settings", icon="PREFERENCES")
        if body is None:
            return
        body.use_property_split = True
        body.use_property_decorate = False
        body.prop(ts, "name", text="Name")
        body.prop(ts, 'preview_backend', text="Evaluation")
        if ts.preview_backend == 'CPU':
            body.prop(ts, 'preview_quality')
        from .physical_scale import draw_set_scale
        draw_set_scale(body, context, ts)
        body.prop(ts, "preview_ao")
        body.prop(ts, "height_distance")
        objects_row = body.row(align=True)
        objects_row.operator("open_painter.texture_set", text="Assign Selected", icon="LINKED").action = "ASSIGN"
        objects_row.operator("open_painter.texture_set", text="Remove Slot", icon="UNLINKED").action = "UNASSIGN"
        body.template_list("OP_UL_assignments", "native", ts, "assignments", ts, "active_assignment", rows=2)
        from .strokes_panel import pending_resample
        pending = pending_resample(ts)
        if pending:
            body.label(text=f"{pending} paint image{'s' if pending != 1 else ''} at another size", icon="INFO")
            body.operator("open_painter.resample_paint_images", icon="IMAGE_DATA")
        actions = body.row(align=True)
        actions.operator("open_painter.texture_set", text="Repair Material").action = "REPAIR"
        actions.operator("open_painter.texture_set", text="Validate", icon="CHECKMARK").action = "VALIDATE"
        actions.operator("open_painter.texture_set", text="", icon="TRASH").action = "DELETE"


_row_depths = {}
LAYER_ICONS = {"PAINT": "BRUSH_DATA", "FILL": "MATERIAL", "FOLDER": "FILE_FOLDER"}
BLEND_LABELS = {key: label for key, label, _ in BLEND_ITEMS}
# Four-letter blend names for rows in a narrow sidebar.
BLEND_SHORT = {
    "NORMAL": "Norm", "MULTIPLY": "Mult", "ADD": "Add", "SUBTRACT": "Sub", "OVERLAY": "Ovly",
    "DIVIDE": "Div", "DARKEN": "Dark", "LIGHTEN": "Lght", "DIFFERENCE": "Diff", "EXCLUSION": "Excl",
    "SCREEN": "Scrn", "LINEAR_BURN": "LBrn", "COLOR_BURN": "CBrn", "COLOR_DODGE": "CDdg",
    "SOFT_LIGHT": "SftL", "HARD_LIGHT": "HrdL", "VIVID_LIGHT": "VivL", "LINEAR_LIGHT": "LinL",
    "PIN_LIGHT": "PinL", "DISABLE": "Off", "HUE": "Hue", "SATURATION": "Sat", "COLOR": "Col",
    "LUMINOSITY": "Lum", "NORMAL_COMBINE": "NCmb", "RNM": "RNM", "NORMAL_ADD": "NAdd",
    "NORMAL_REPLACE": "NRpl",
}
OPACITY_WIDTH = 52


def preferences(context):
    addon = context.preferences.addons.get(__package__.rsplit(".", 1)[0])
    return addon.preferences if addon else None


def dense_rows(context):
    """Whether layer rows carry their own blend menu and opacity field (Preferences)."""
    prefs = preferences(context)
    return bool(prefs and prefs.dense_layer_rows)


def row_metrics(context):
    """Unscaled row width, whether rows are compact, and the blend menu width.

    A row is the sidebar minus its tabs, panel and list margins. Below 300 px
    the blend menu shows short names so the layer name keeps its room.
    """
    scale = max(.5, context.preferences.system.ui_scale)
    region = getattr(context, "region", None)
    width = (region.width if region is not None else 400) / scale - 80
    compact = width < 300
    return width, compact, 50 if compact else 78


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
            from ..baking.maps.curvature import edge_map_set_complete
            radius = ts.bake.mean_curvature_distance
            box.prop(mask, "edge_source", text="Edges")
            if mask.edge_source == "LIVE":
                field = mask.influence
                box.prop(field, "distance", text="Edge Reach")
                box.prop(field, "resolution", text="Analysis")
                box.prop(mask, "edge_width", text="Edge Width", slider=True)
                if field.error:
                    box.label(text=field.error, icon="TIME" if field.error.startswith("Analysing") else "ERROR")
                else:
                    box.label(text="Follows the evaluated mesh: bevels and edits update live", icon="INFO")
            elif edge_map_set_complete(ts):
                box.prop(mask, "edge_size", text=f"Edge Size ({radius * 50:.1f}–{radius * 400:.1f} cm)", slider=True)
            if mask.edge_source != "LIVE":
                box.prop(mask, "edge_width", text="Edge Width", slider=True)
            if mask.edge_source == "LIVE":
                pass
            elif OP_OT_edge_map_refresh.running:
                box.label(text="Rebaking the edge map in the background…", icon="TIME")
            elif stale_curvature(ts) is not None:
                box.label(text="Edge maps need a rebake (smooth edges, live Edge Size)", icon="ERROR")
                box.operator("open_painter.edge_map_refresh", text="Rebake Edge Maps", icon="IMAGE_DATA").set_id = ts.id
            elif not any(m.kind == "CURVATURE" and m.image for m in ts.baked_maps):
                box.label(text="Bake a Curvature map for edge wear", icon="ERROR")
        box.prop(mask, "contrast", slider=True)


_profile_cache = {}


def _profile_rows(scene, ts):
    """(rows by id, total) of the set's profile, rebuilt once per redraw pass."""
    from ..evaluation import profile
    key = (scene.as_pointer(), ts.id)
    entry = _profile_cache.get(key)
    serial = (ts.edit_serial, len(ts.layers), profile.table(scene, ts)['sampled_at'])
    if entry is None or entry[0] != serial:
        rows = {row['layer_id']: row for row in profile.rows(scene, ts)}
        entry = _profile_cache[key] = (serial, rows, sum(row['cost_ms'] for row in rows.values()))
    return entry[1], entry[2]


class OP_UL_native_layers(bpy.types.UIList):
    def filter_items(self, context, data, propname):
        rows = visible_rows(data)
        # filter_items runs once per redraw before the items draw; keep the
        # depths so draw_item does not walk the hierarchy again per row.
        _row_depths[data.as_pointer()] = dict(rows)
        _profile_cache.pop((context.scene.as_pointer(), data.id), None)
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
        # Everything left of the controls is the row itself: press to select,
        # drag to reorder, double-click the name to rename. Dense rows keep a
        # blend menu and opacity field of readable width; otherwise the name
        # takes the whole row and the selected layer's blend sits above the list.
        width, compact, blend_width = row_metrics(context)
        dense = dense_rows(context)
        controls_width = blend_width + OPACITY_WIDTH
        if dense:
            split = layout.split(factor=1 - controls_width / max(2 * controls_width, width))
            row = split.row(align=True)
        else:
            row = layout.row(align=True)
        row.prop(
            layer, "visible", text="", icon="HIDE_OFF" if layer.visible else "HIDE_ON", emboss=False
        )
        from .performance import badge, CACHE_ICONS
        scene = context.scene
        profile_rows = _profile_rows(scene, data)
        profile_row = profile_rows[0].get(layer.id)
        mark = badge(scene, data, layer, profile_rows[0], profile_rows[1])
        if layer.bypass or not compact or mark:
            # Preview-only bypass, distinct from the artistic eye.
            row.prop(layer, "bypass", text="",
                     icon="RESTRICT_VIEW_ON" if layer.bypass else "RESTRICT_VIEW_OFF", emboss=False)
        row.active = not layer.bypass
        if depth:
            row.separator(factor=2.5 * depth)
        if layer.type == "FOLDER":
            row.prop(
                layer,
                "expanded",
                text="",
                icon="TRIA_DOWN" if layer.expanded else "TRIA_RIGHT",
                emboss=False,
            )
        row.label(text="", icon=LAYER_ICONS[layer.type])
        selected = index == data.active_layer
        mask_selected = selected and selection.ui_state(context).selection_kind == selection.MASK
        if layer.masks:
            # Content versus mask is shown by the pressed state and the icon, never by colour alone.
            op = row.operator(
                "open_painter.native_select",
                text="",
                icon="CLIPUV_HLT" if mask_selected else "MOD_MASK",
                depress=mask_selected,
            )
            op.layer_id, op.mask = layer.id, True
        row.prop(layer, "name", text="", emboss=False)
        if layer.type == "FOLDER" and layer.module_id:
            row.label(text="" if compact else f"v{layer.module_version}",
                      icon="UNLINKED" if layer.module_detached else "PACKAGE")
        if any(m.source == "ANCHOR" for m in layer.masks) or any(
                e.kind == "ANCHOR" for p in layer.channels for e in p.effects):
            row.label(text="", icon="ANCHOR_CENTER")
        # The row's state is the one the profile table already holds; asking
        # the shader session again per cached row scans the stack per binding.
        icon = (CACHE_ICONS.get(profile_row["state"], "FILE_CACHE")
                if profile_row is not None and layer.cache_mode != "LIVE" else None)
        if icon:
            row.label(text="", icon=icon)
        if mark:
            row.label(text="" if compact else mark[0], icon=mark[1])
        if not dense:
            return
        controls = split.split(factor=blend_width / controls_width, align=True)
        if layer.type == "FOLDER" and layer.group_mode == "PASSTHROUGH":
            controls.prop_menu_enum(layer, "group_mode", text="Pass" if compact else "Pass Through")
        elif channel:
            blend = layer.blend_mode if channel.blend_mode == "INHERIT" else channel.blend_mode
            labels = BLEND_SHORT if compact else BLEND_LABELS
            controls.prop_menu_enum(channel, "blend_mode", text=labels.get(blend, blend.title()))
        else:
            controls.label(text="")
        if channel:
            controls.prop(channel, "opacity_percent", text="")
        else:
            controls.label(text="")


class OP_PT_native_layers(PainterPanel, bpy.types.Panel):
    bl_label = "Layers"
    bl_order = 1

    def draw(self, context):
        layout = self.layout
        ts = active_set(context)
        if not ts:
            layout.label(text="Create a texture set to start painting.")
            return
        width, compact, _ = row_metrics(context)
        # Labelled menus instead of an icon strip; Duplicate and Delete stay one click away.
        # A narrow sidebar wraps the strip onto two rows rather than shortening the labels.
        if compact:
            strip = layout.column(align=True)
            first, second = strip.row(align=True), strip.row(align=True)
        else:
            first = second = layout.row(align=True)
        first.menu("OP_MT_native_add_layer", text="Add Layer", icon="ADD")
        first.menu("OP_MT_native_masks", text="Add Mask", icon="MOD_MASK")
        second.menu("OP_MT_native_add_effect", text="Add Effect", icon="MODIFIER")
        second.menu("OP_MT_native_more", text="More", icon="DOWNARROW_HLT")
        second.operator("open_painter.layer", text="", icon="DUPLICATE").action = "DUPLICATE"
        second.operator("open_painter.layer", text="", icon="TRASH").action = "DELETE"
        draw_selected_row(layout, context, ts)
        layout.template_list(
            "OP_UL_native_layers",
            "stack",
            ts,
            "layers",
            ts,
            "active_layer",
            rows=4,
            maxrows=12,
            sort_lock=True,
        )
        # Keyboard-free alternatives to dragging: step through the visible hierarchy.
        footer = layout.row(align=True)
        footer.alignment = "LEFT"
        for action, icon in [
            ("UP", "TRIA_UP"),
            ("DOWN", "TRIA_DOWN"),
            ("PARENT", "FOLDER_REDIRECT"),
        ]:
            footer.operator("open_painter.layer", text="", icon=icon).action = action


def draw_selected_row(layout, context, ts):
    """Blend channel, blend mode and opacity of the selected layer, above the list.

    Layer opacity, channel opacity, an inherited blend and a folder's compositing are
    four different things; the row edits the selected layer's *channel* blend and
    channel opacity for the blend channel, as the dense rows do per layer.
    """
    project = context.scene.open_painter
    layer = active(ts)
    width, compact, _ = row_metrics(context)
    row = layout.row(align=True)
    row.prop(project, "blend_channel", text="")
    if layer is None:
        row.label(text="No layer selected")
        return
    if dense_rows(context):
        row.label(text=f"{layer.name}: blend per row", icon="INFO")
        return
    channel = channel_for(layer, project.blend_channel)
    if layer.type == "FOLDER" and layer.group_mode == "PASSTHROUGH":
        row.prop_menu_enum(layer, "group_mode", text="Pass Through")
    elif channel is not None:
        blend = layer.blend_mode if channel.blend_mode == "INHERIT" else channel.blend_mode
        row.prop_menu_enum(channel, "blend_mode", text=BLEND_LABELS.get(blend, blend.title())
                           + (" (layer)" if channel.blend_mode == "INHERIT" and not compact else ""))
    if channel is not None:
        row.prop(channel, "opacity_percent", text="")


class OP_MT_native_add_layer(bpy.types.Menu):
    bl_label = "Add Layer"

    def draw(self, context):
        layout = self.layout
        for action, label, icon in (("PAINT", "Paint Layer", LAYER_ICONS["PAINT"]),
                                    ("FILL", "Fill Layer", LAYER_ICONS["FILL"]),
                                    ("FOLDER", "Folder", "NEWFOLDER")):
            layout.operator("open_painter.layer", text=label, icon=icon).action = action
        layout.separator()
        layout.operator("open_painter.decal", text="Decal Layer…", icon="OUTLINER_OB_IMAGE").action = "ADD"
        layout.operator("open_painter.catalog_material", text="Smart Material…", icon="ASSET_MANAGER")
        layout.operator("open_painter.module_insert", text="Module Instance…", icon="PACKAGE")
        layout.separator()
        layout.operator("open_painter.layer", text="Group Selected", icon="FILE_FOLDER").action = "GROUP"


class OP_MT_native_add_effect(bpy.types.Menu):
    bl_label = "Add Effect"

    def draw(self, context):
        layout = self.layout
        layout.operator("open_painter.catalog_effect", text="Surface Effect Layer…", icon="MATERIAL")
        op = layout.operator("open_painter.choose_filter", text="Channel Filter…", icon="MODIFIER")
        op.target, op.create = "CHANNEL", True
        op = layout.operator("open_painter.choose_filter", text="Mask Filter…", icon="MOD_MASK")
        op.target, op.create = "MASK", True
        layout.separator()
        layout.operator("open_painter.anchor", text="Anchor Point (channel)", icon="ANCHOR_CENTER").action = "ADD_CHANNEL"
        layout.separator()
        layout.menu("OP_MT_geometry_effect_add", text="Geometry Effect", icon="MOD_PARTICLES")


class OP_MT_native_more(bpy.types.Menu):
    bl_label = "More"

    def draw(self, context):
        layout = self.layout
        layout.operator("open_painter.layer", text="Move Up", icon="TRIA_UP").action = "UP"
        layout.operator("open_painter.layer", text="Move Down", icon="TRIA_DOWN").action = "DOWN"
        layout.operator("open_painter.layer", text="Move into Folder…", icon="FOLDER_REDIRECT").action = "PARENT"
        layout.operator("open_painter.layer", text="Ungroup Folder", icon="X").action = "UNGROUP"
        layout.separator()
        layout.operator("open_painter.open_graph", text="Edit Nodes", icon="NODETREE")
        layout.operator("open_painter.open_inspector", text="Open Inspector", icon="PROPERTIES")
        layout.separator()
        layout.operator("open_painter.save_material", text="Save Set as Smart Material…", icon="FILE_TICK")
        layout.operator("open_painter.module_save", text="Save Folder as Module…", icon="PACKAGE")
        layout.separator()
        layout.operator("open_painter.profile_layers", text="Profile Layers", icon="SORTTIME").action = "PROFILE"
        layout.menu("OP_MT_native_performance", text="Performance", icon="TIME")


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
            ('MESH', 'Add Mesh Mask'),
            ('GROUP', 'Add Mask Group'),
            ('GRAPH', 'Add Custom Graph'),
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
    bl_label = "Inspector"
    bl_order = 2

    def draw_header(self, context):
        # The header names the selected thing, not just "Properties".
        target = selection.resolve(context)
        if target.layer is not None:
            self.layout.label(text=target.label, icon=target.icon)

    def draw(self, context):
        layout = self.layout
        status = context.scene.open_painter.status.strip()
        if status:
            box = layout.box()
            scale = max(.5, context.preferences.system.ui_scale)
            width = max(20, min(70, int(context.region.width / (7.5 * scale)) - 5))
            for index, line in enumerate(wrap(status, width=width)):
                box.label(text=line, icon="INFO" if index == 0 else "BLANK1")
        target = selection.resolve(context)
        compact = row_metrics(context)[1]
        selection.draw_breadcrumb(layout, target, compact=compact)
        if target.layer is not None:
            row = layout.row(align=True)
            row.use_property_split = False
            row.prop(target.layer, "name", text="")
        from . import inspector
        inspector.draw(layout, context, target, compact=compact)


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
    project = context.scene.open_painter
    row = self.layout.row(align=True)
    row.prop(project, "target", text="")
    row.prop(project, "engine_preview", text="", icon="SHADING_RENDERED", toggle=True)
    from ..targets import get
    row.operator("open_painter.export_target", text="Export → " + get(project.target).label, icon="EXPORT")


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
    OP_OT_native_layer_drag,
    OP_PT_native_sets,
    OP_PT_native_layers,
    OP_MT_native_masks,
    OP_MT_native_add_layer,
    OP_MT_native_add_effect,
    OP_MT_native_more,
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
                # Local handles (imported textures, alphas) plus the shelf library;
                # asset_poll keeps every other library out.
                shelf.asset_library_reference = "ALL"
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
            **({"draw_header": cls.draw_header} if "draw_header" in cls.__dict__ else {}),
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
