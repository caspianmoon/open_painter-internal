"""Material modules: save a folder as a versioned module, insert instances, update them."""
from pathlib import Path

import bpy
from bpy_extras.io_utils import ExportHelper

from ..core.layer_stack import active
from ..core.project_manager import require_set
from ..smart_materials import library, loader, modules
from .operators import SafeOperator
from .texture_operations import _redraw

_items = []


def _root(context, ts=None):
    ts = ts or require_set(context)
    layer = active(ts)
    if layer is None or layer.type != "FOLDER":
        raise ValueError("Select a folder: modules are folder subtrees.")
    return ts, layer


def module_items(self, context):
    global _items
    from ..smart_materials.composer import MODULES
    _items = [("catalog:" + name, f'{name} v{data.get("version", 1)} (catalog)', "Built-in module")
              for name, data in sorted(MODULES.items())]
    _items += [(str(entry["path"]), f'{entry["name"]} v{entry["version"]}', str(entry["path"]))
               for entry in sorted(modules.library_index(context).values(), key=lambda e: e["name"].lower())]
    return _items or [("NONE", "No modules in the library", "")]


class OP_OT_module_save(SafeOperator, bpy.types.Operator, ExportHelper):
    bl_idname = "open_painter.module_save"
    bl_label = "Save Folder as Module"
    bl_description = ("Save the selected folder and its contents as a reusable, versioned module; "
                      "saving a module folder again writes the next version")
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ".openpaintmaterial"
    filter_glob: bpy.props.StringProperty(default="*.openpaintmaterial", options={"HIDDEN"})
    module_name: bpy.props.StringProperty(name="Module Name")
    description: bpy.props.StringProperty(name="Description")

    def invoke(self, context, event):
        ts, root = _root(context)
        self.module_name = root.module_name or root.name
        version = root.module_version + 1 if root.module_id else 1
        self.filepath = str(library.directory(context) / f"{self.module_name} v{version}.openpaintmaterial")
        return ExportHelper.invoke(self, context, event)

    def run(self, context):
        ts, root = _root(context)
        path, block = modules.save_module(context, ts, root, self.filepath, self.module_name, self.description)
        self.report({"INFO"}, f'Saved {block["name"]} v{block["version"]} to {path.name}')
        _redraw(context, ts)


class OP_OT_module_insert(SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.module_insert"
    bl_label = "Insert Module"
    bl_description = "Insert an instance of a library module above the selected layer"
    bl_options = {"REGISTER", "UNDO"}
    bl_property = "module"
    module: bpy.props.EnumProperty(name="Module", items=module_items)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def run(self, context):
        if self.module == "NONE":
            raise ValueError("Save a folder as a module first.")
        ts = require_set(context)
        if self.module.startswith("catalog:"):
            from ..smart_materials import composer
            name = self.module[len("catalog:"):]
            roots = [composer.apply_document(ts, composer.MODULES[name], name)]
        else:
            selected = active(ts)
            parent_id = selected.parent_id if selected is not None else ""
            roots = loader.apply(ts, Path(self.module), parent_id=parent_id)
        _redraw(context, ts)
        self.report({"INFO"}, f"Inserted {roots[0].module_name} v{roots[0].module_version}" if roots else "Inserted")


class OP_OT_module_update(SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.module_update"
    bl_label = "Update Module Instances"
    bl_description = ("Update this instance (or every instance of its module in the set) to the newest "
                      "library version, keeping your edits; conflicts keep your values and are listed")
    bl_options = {"REGISTER", "UNDO"}
    # SKIP_SAVE: three buttons share this operator; a remembered Replace must not turn a later
    # plain Update into a wholesale replace that drops the artist's edits.
    all_instances: bpy.props.BoolProperty(name="All Instances", default=False, options={"SKIP_SAVE"})
    replace: bpy.props.BoolProperty(name="Replace (discard local edits)", default=False, options={"SKIP_SAVE"})

    def run(self, context):
        ts, root = _root(context)
        if not root.module_id:
            raise ValueError("This folder is not a module instance.")
        catalog = modules.catalog_name(root)
        if catalog:
            definition = modules.catalog_definition(catalog)
            if definition is None:
                raise ValueError(f"The catalog no longer defines {catalog}.")
            newest = int(definition.get("version", 1))
        else:
            entry = modules.library_index(context).get(root.module_id)
            if entry is None:
                raise ValueError("No library file defines this module; save it as a module first.")
            newest = entry["version"]
        targets = modules.instances(ts, root.module_id) if self.all_instances else [root]
        report, conflicts = [], []
        for instance in [layer.id for layer in targets]:
            layer = next(item for item in ts.layers if item.id == instance)
            if layer.module_version >= newest and not self.replace:
                report.append(f"{layer.name} is already v{layer.module_version}")
                continue
            if catalog:
                version, found = modules.update_catalog(ts, layer, replace=self.replace)
            else:
                version, found = modules.update(ts, layer, entry["path"], replace=self.replace)
            report.append(f"{layer.name} → v{version}")
            conflicts.extend(found)
        _redraw(context, ts)
        message = "; ".join(report)
        if conflicts:
            shown = ", ".join(path.replace("material.layers.", "") for path in conflicts[:6])
            message += f" · {len(conflicts)} conflict{'s' if len(conflicts) != 1 else ''} kept your values: {shown}"
            context.scene.open_painter.status = message
            self.report({"WARNING"}, message)
        else:
            context.scene.open_painter.status = message
            self.report({"INFO"}, message)


class OP_OT_module_detach(SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.module_detach"
    bl_label = "Detach from Module"
    bl_description = "Keep this folder's provenance but exclude it from module updates"
    bl_options = {"REGISTER", "UNDO"}

    def run(self, context):
        ts, root = _root(context)
        modules.detach(root)
        _redraw(context, ts)


def draw_module_box(layout, context, ts, layer):
    """Module identity, version, update/detach on a folder's properties."""
    if layer.type != "FOLDER":
        return
    box = layout.box()
    if not layer.module_id:
        box.label(text="Module", icon="PACKAGE")
        box.operator("open_painter.module_save", text="Save Folder as Module", icon="EXPORT")
        return
    index = modules.library_index(context)
    row = box.row(align=True)
    row.label(text=f"{layer.module_name or layer.name} v{layer.module_version}", icon="PACKAGE")
    if layer.module_detached:
        row.label(text="detached")
    elif modules.update_available(ts, layer, index):
        row.alert = True
        row.label(text="update available", icon="FILE_REFRESH")
    box.label(text=layer.module_source or "unsaved definition")
    row = box.row(align=True)
    row.enabled = not layer.module_detached
    op = row.operator("open_painter.module_update", text="Update", icon="FILE_REFRESH")
    op.all_instances, op.replace = False, False
    op = row.operator("open_painter.module_update", text="Update All")
    op.all_instances, op.replace = True, False
    row.operator("open_painter.module_save", text="Save Version", icon="EXPORT")
    row = box.row(align=True)
    row.operator("open_painter.module_detach", text="Detach", icon="UNLINKED")
    op = row.operator("open_painter.module_update", text="Replace with Library Version")
    op.all_instances, op.replace = False, True


CLASSES = (OP_OT_module_save, OP_OT_module_insert, OP_OT_module_update, OP_OT_module_detach)
