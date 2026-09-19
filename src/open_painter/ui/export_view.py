"""The export view (UI plan phase 5): Contents → Scope → Destination Preset → Folder →
Output Preview, in the Painter sidebar's *Export* tab. The preview and the export button
read the same resolved plan (``export/plan.py``); opening the view changes no saved
setting — the view's own choices live on the window manager and are never saved.
"""
import bpy

from ..core.project_manager import active_set
from ..export import plan as planning
from . import editor

CATEGORY = "Export"


class OP_ExportView(bpy.types.PropertyGroup):
    contents: bpy.props.EnumProperty(name="Contents", items=planning.CONTENTS, default="TARGET", options={"SKIP_SAVE"})
    scope: bpy.props.EnumProperty(name="Scope", items=planning.SCOPES, default="ACTIVE", options={"SKIP_SAVE"})
    folder_source: bpy.props.EnumProperty(name="Folder", items=planning.FOLDERS, default="PROJECT", options={"SKIP_SAVE"},
                                          description="Which saved folder this export writes to when the set's own and the project's differ")
    overwrite: bpy.props.BoolProperty(name="Replace Existing Files", default=False, options={"SKIP_SAVE"},
                                      description="Replace files the preview marks as existing; otherwise the export refuses")


def view_settings(context):
    return context.window_manager.open_painter_export


def current_plan(context):
    view = view_settings(context)
    return planning.resolve(context, contents=view.contents, scope=view.scope, folder_source=view.folder_source)


def draw_contents(layout, context, view):
    layout.prop(view, "contents", expand=True)
    # Short enough for a 320 px sidebar: no essential word is lost to an ellipsis.
    layout.label(text={"TEXTURES": "Textures, the set's own preset and naming",
                       "TARGET": "Target textures + manifest + mesh",
                       "BUNDLE": "Staged scene + textures + manifest"}[view.contents], icon="INFO")


def draw_scope(layout, context, view, plan):
    layout.prop(view, "scope", expand=True)
    if plan.sets:
        names = ", ".join(item.name for item in plan.sets)
        layout.label(text=f"Sets: {names}", icon="TEXTURE")
        layout.label(text=("Objects: " + ", ".join(plan.objects)) if plan.objects else "Objects: none assigned",
                     icon="MESH_DATA" if plan.objects else "ERROR")
    for problem in plan.problems:
        layout.label(text=problem, icon="ERROR")


def draw_destination(layout, context, view, plan):
    project = context.scene.open_painter
    target = planning.target_registry.get(project.target)
    layout.use_property_split = True
    layout.use_property_decorate = False
    layout.prop(project, "target", text="Target")
    if target.key != "CUSTOM":
        layout.label(text=f"{'DirectX' if target.normal == 'DIRECTX' else 'OpenGL'} normals · "
                          f"{'smoothness' if target.gloss == 'SMOOTHNESS' else 'roughness'} · AO {target.ao_usage.lower()}"
                          + (f" · mesh {target.mesh_format}" if target.mesh_format else ""), icon="RESTRICT_RENDER_OFF")
    row = layout.row(align=True)
    row.prop(project, "engine_preview", toggle=True, icon="SHADING_RENDERED")
    if project.engine_preview:
        column = layout.column(align=True)
        column.prop(project, "engine_preview_quantize")
        column.prop(project, "engine_preview_normal_reconstruct")
        column.prop(project, "engine_preview_ao_indirect")
    if view.contents == "TARGET":
        layout.prop(project, "export_mesh")
    ts = active_set(context)
    if ts is not None:
        head, body = layout.panel("painter_export_set_settings", default_closed=view.contents == "TARGET")
        head.label(text=f"{ts.name} · own export settings", icon="PREFERENCES")
        if body is not None:
            settings = ts.export
            body.prop(settings, "follow_target")
            for key in ("preset", "resolution", "file_format", "bit_depth", "prefix", "suffix"):
                body.prop(settings, key)
            if settings.preset == "CUSTOM":
                for c in "rgba":
                    body.prop(settings, "custom_" + c)
            body.prop(settings, "padding_mode")
            if settings.padding_mode == "CUSTOM":
                body.prop(settings, "padding_pixels")
            from ..export import padding as padding_module
            size = next((item.size for item in plan.sets if item.set_id == ts.id), int(ts.resolution))
            body.label(text=padding_module.describe(settings, size) + f" at {size} px", icon="FULLSCREEN_EXIT")
            if settings.padding_migrated:
                body.label(text="Set to Automatic when this file was upgraded", icon="INFO")
            if settings.last_report:
                body.label(text="Last export: " + settings.last_report, icon="CHECKMARK" if "meet" not in settings.last_report else "ERROR")
    # Effective settings per set, with the set's own values named where they differ.
    for item in plan.sets:
        row = layout.row(align=True)
        row.label(text=f"{item.name}: {item.preset} · {item.file_format} {item.bit_depth}-bit · {item.size} px · "
                       f"{'DirectX' if item.normal == 'DIRECTX' else 'OpenGL'} normals"
                       + (" · follows target" if item.follow_target and view.contents == "TARGET" else ""),
                  icon="CHECKMARK")
        for note in item.overrides:
            layout.label(text=f"    override: {note}", icon="INFO")


def draw_folder(layout, context, view, plan):
    project = context.scene.open_painter
    ts = active_set(context)
    layout.use_property_split = True
    layout.use_property_decorate = False
    if view.contents == "TARGET":
        layout.prop(project, "export_directory", text="Project Folder")
    if ts is not None:
        layout.prop(ts.export, "directory", text=f"{ts.name} Folder")
    differing = [item for item in plan.sets if item.own_directory and project.export_directory
                 and bpy.path.abspath(item.own_directory) != bpy.path.abspath(project.export_directory)]
    if view.contents == "TARGET" and differing:
        layout.prop(view, "folder_source", text="Write To")
        layout.label(text=", ".join(item.name for item in differing) + " saved a different folder; both are kept",
                     icon="INFO")
    if plan.directory is not None:
        layout.label(text=f"Writes to {plan.directory}", icon="FILE_FOLDER")


def draw_preview(layout, context, view, plan):
    if not plan.sets:
        layout.label(text="Nothing to export in this scope", icon="INFO")
        return
    column = layout.column(align=True)
    for item in plan.sets:
        column.label(text=item.name, icon="TEXTURE")
        for output in item.outputs:
            row = column.row(align=True)
            row.label(text=f"{output.name} · {output.size} px · {output.file_format} {output.bit_depth}-bit",
                      icon="ERROR" if output.exists else "IMAGE_DATA")
            column.label(text=f"    {output.packing} · {output.color_space}", icon="BLANK1")
        if item.manifest is not None:
            column.label(text=f"{item.manifest.name} · target manifest", icon="ERROR" if item.manifest.exists() else "TEXT")
    if plan.mesh is not None:
        column.label(text=f"{plan.mesh.name} · mesh ({planning.target_registry.get(plan.target).mesh_format})",
                     icon="ERROR" if plan.mesh.exists() else "MESH_DATA")
    for note in plan.notes:
        column.label(text=note, icon="INFO")
    if plan.conflicts:
        layout.label(text=f"{len(plan.conflicts)} file(s) already exist" + ("; they will be replaced" if view.overwrite
                                                                            else "; the export refuses until replaced"),
                     icon="ERROR")
        layout.prop(view, "overwrite")


def draw_actions(layout, context, view, plan):
    row = layout.row(align=True)
    row.enabled = plan.ready
    count = len(plan.files) + (1 if plan.bundle else 0)
    row.operator("open_painter.export_plan", text=f"Export {count} File{'s' if count != 1 else ''}", icon="EXPORT")
    row.operator("open_painter.show_task", text="", icon="LOOP_BACK").task = "PAINTER"
    if not plan.ready and plan.problems:
        layout.label(text=plan.problems[0], icon="ERROR")


def draw_prepare(layout, context):
    layout.label(text="Optional staging before the main export", icon="INFO")
    row = layout.row(align=True)
    row.operator("open_painter.production_export", text="Measure Density").action = "MEASURE"
    row.operator("open_painter.production_export", text="Stage Atlas…").action = "ATLAS"
    layout.operator("open_painter.offline_analysis", text="Stage Mesh LOD…", icon="MOD_DECIM").action = "LOD"
    layout.label(text="LOD: a decimated evaluated copy for inspection; the original mesh stays", icon="BLANK1")


def draw_view(layout, context, *, compact=False):
    view = view_settings(context)
    plan = current_plan(context)
    draw_actions(layout, context, view, plan)
    for idname, title, icon, drawer, closed in (
        ("painter_export_contents", "Contents", "PACKAGE", lambda body: draw_contents(body, context, view), False),
        ("painter_export_scope", "Scope", "RESTRICT_SELECT_OFF", lambda body: draw_scope(body, context, view, plan), False),
        ("painter_export_destination", "Destination Preset", "RESTRICT_RENDER_OFF",
         lambda body: draw_destination(body, context, view, plan), compact),
        ("painter_export_folder", "Folder", "FILE_FOLDER", lambda body: draw_folder(body, context, view, plan), compact),
        ("painter_export_preview", "Output Preview", "IMAGE_DATA", lambda body: draw_preview(body, context, view, plan), False),
        ("painter_export_prepare", "Prepare Export", "MOD_BUILD", lambda body: draw_prepare(body, context), True),
    ):
        head, body = layout.panel(idname, default_closed=closed)
        head.label(text=title, icon=icon)
        if body is not None:
            drawer(body)


class OP_OT_export_plan(bpy.types.Operator):
    bl_idname = "open_painter.export_plan"
    bl_label = "Export"
    bl_description = "Write exactly the files the Output Preview lists"
    bl_options = {"REGISTER"}

    def execute(self, context):
        view = view_settings(context)
        plan = current_plan(context)
        try:
            written = planning.execute(context, plan, overwrite=view.overwrite)
        except Exception as exc:
            context.scene.open_painter.status = str(exc)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        message = f"Exported {len(written)} file(s) to {plan.directory} · {planning.summary(plan)}"
        if plan.outcome is not None and plan.outcome.cleanup_pending:
            message = f"{plan.outcome.message} · {message}"
            self.report({"WARNING"}, plan.outcome.message)
        context.scene.open_painter.status = message
        self.report({"INFO"}, message)
        return {"FINISHED"}


class ExportPanel:
    bl_region_type = "UI"
    bl_category = CATEGORY
    bl_label = "Export"
    bl_order = 0

    @classmethod
    def poll(cls, context):
        return editor.is_area(context.area)

    def draw(self, context):
        layout = self.layout
        ts = active_set(context)
        row = layout.row(align=True)
        row.label(text=f"Export · {ts.name}" if ts else "Export", icon="EXPORT")
        row.operator("open_painter.show_task", text="Back to Painting", icon="LOOP_BACK").task = "PAINTER"
        draw_view(layout, context)


class OP_PT_export_view(ExportPanel, bpy.types.Panel):
    bl_space_type = "VIEW_3D"


class OP_PT_image_export_view(ExportPanel, bpy.types.Panel):
    bl_space_type = "IMAGE_EDITOR"


def register():
    bpy.types.WindowManager.open_painter_export = bpy.props.PointerProperty(type=OP_ExportView)


def unregister():
    if hasattr(bpy.types.WindowManager, "open_painter_export"):
        del bpy.types.WindowManager.open_painter_export


CLASSES = (OP_ExportView, OP_OT_export_plan, OP_PT_export_view, OP_PT_image_export_view)
