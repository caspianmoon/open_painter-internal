from pathlib import Path

import bpy
from bpy_extras.io_utils import ExportHelper, ImportHelper

from ..core.project_manager import require_set
from ..smart_materials import library, loader, serializer
from .operators import SafeOperator

_items = []


def materials(self, context):
    global _items
    _items = [(str(p), p.stem.replace("_", " "), str(p)) for p in library.entries(context)]
    return _items or [("NONE", "No materials", "")]


class OP_OT_save_material(SafeOperator, bpy.types.Operator, ExportHelper):
    bl_idname = "open_painter.save_material"
    bl_label = "Save Stack as Smart Material"
    filename_ext = ".openpaintmaterial"
    filter_glob: bpy.props.StringProperty(default="*.openpaintmaterial", options={"HIDDEN"})

    def run(self, context):
        serializer.save(require_set(context), self.filepath)
        self.report({"INFO"}, "Smart material saved with bundled textures.")


def apply_document(context, ts, path):
    """Apply a library document as one named, editable group (a module document is already one)."""
    from ..core import layer_stack
    from ..smart_materials import portable
    from ..baking.bake_manager import refresh_stale_curvature
    data = portable.header(path)
    portable.preflight(ts, data)
    refresh_stale_curvature(ts, portable.requirements(data), context)
    roots = loader.apply(ts, path)
    ids = [layer.id for layer in roots]
    if len(ids) == 1 and next(layer for layer in ts.layers if layer.id == ids[0]).type == "FOLDER":
        folder = next(layer for layer in ts.layers if layer.id == ids[0])
    elif ids:
        folder = layer_stack.group(ts, set(ids), name=Path(path).stem.replace("_", " "))
        folder.group_mode = "ISOLATED"
    else:
        return None
    ts.active_layer = layer_stack.index_of(ts, folder.id)
    from . import selection
    if context.area is not None:
        selection.select(context, folder.id, selection.CONTENT, ts=ts, synchronize=False)
    return folder


class OP_OT_import_material(SafeOperator, bpy.types.Operator, ImportHelper):
    bl_idname = "open_painter.import_material"
    bl_label = "Import and Apply Smart Material"
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ".openpaintmaterial"
    filter_glob: bpy.props.StringProperty(default="*.openpaintmaterial", options={"HIDDEN"})

    def run(self, context):
        apply_document(context, require_set(context), self.filepath)
        folder = library.directory(context)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / Path(self.filepath).name
        if not target.exists():
            target.write_bytes(Path(self.filepath).read_bytes())


class OP_OT_library(SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.library"
    bl_label = "Smart Material Library"
    bl_options = {"REGISTER", "UNDO"}
    material: bpy.props.EnumProperty(name="Material", items=materials)
    action: bpy.props.EnumProperty(
        items=[("APPLY", "Apply", ""), ("DELETE", "Delete Local Material", "")]
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        self.layout.prop(self, "material")
        if self.action == "DELETE":
            self.layout.label(text="Deletes the selected local library file.", icon="ERROR")

    def run(self, context):
        path = Path(self.material)
        if self.action == "APPLY":
            apply_document(context, require_set(context), path)
        else:
            if path.resolve().parent != library.directory(context).resolve():
                raise ValueError("Built-in materials cannot be deleted.")
            path.unlink()


class OP_PT_assets(bpy.types.Panel):
    bl_order = 6
    bl_label = "Assets · Smart Materials"
    bl_parent_id = "OP_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Open Painter"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        layout.operator("open_painter.catalog_material", text="Add Smart Material", icon="ASSET_MANAGER")
        layout.operator("open_painter.catalog_effect", text="Add Surface Effect", icon="MODIFIER")
        layout.separator()
        layout.operator(
            "open_painter.library", text="Apply Saved Material", icon="ASSET_MANAGER"
        ).action = "APPLY"
        layout.operator("open_painter.save_material", icon="FILE_TICK")
        layout.operator("open_painter.import_material", icon="IMPORT")
        layout.operator(
            "open_painter.library", text="Delete Local Material", icon="TRASH"
        ).action = "DELETE"
