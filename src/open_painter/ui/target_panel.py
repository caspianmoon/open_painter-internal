"""Target renderer: selector, engine preview and one-click export."""
import json
from pathlib import Path

import bpy

from ..core.project_manager import active_set, require_set
from ..targets import apply, filename, get, manifest, resolution
from .operators import SafeOperator


def _sets_for_export(context, project, everything):
    if everything:
        selected = {obj.as_pointer() for obj in context.selected_objects}
        chosen = [ts for ts in project.texture_sets
                  if any(ref.object and ref.object.as_pointer() in selected for ref in ts.assignments)]
        return chosen or list(project.texture_sets)
    return [require_set(context)]


class OP_OT_export_target(SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.export_target"
    bl_label = "Export to Target"
    bl_description = ("Export textures (and the mesh) for the project's target renderer in one step: "
                      "the target's packing, naming, normal convention and mesh format, plus a manifest")
    bl_options = {"REGISTER"}
    # SKIP_SAVE: the plain export buttons set nothing, and Blender would otherwise reuse the last
    # run's value, so one "Selected Objects" click would make every later plain click export
    # the selected objects' sets (or every set when nothing is selected).
    all_sets: bpy.props.BoolProperty(name="Sets of Selected Objects", default=False, options={"SKIP_SAVE"})

    def run(self, context):
        from ..export.exporter import export
        project = context.scene.open_painter
        target = get(project.target)
        if not project.export_directory:
            raise ValueError("Choose the project's export directory first (Export panel → Target).")
        directory = Path(bpy.path.abspath(project.export_directory))
        written = []
        objects = set()
        for ts in _sets_for_export(context, project, self.all_sets):
            settings = ts.export
            saved = {key: getattr(settings, key) for key in ("preset", "file_format", "bit_depth", "resolution")}
            try:
                apply(target, settings)
                n = resolution(target, ts, settings)
                if n != int(ts.resolution) or settings.resolution != "SET":
                    settings.resolution = str(n) if str(n) in {"512", "1024", "2048", "4096", "8192"} else "SET"
                extension = "png" if settings.file_format == "PNG" else "tif"
                naming = (lambda set_name, label, ext, prefix=settings.prefix, suffix=settings.suffix:
                          filename(target, set_name, label, ext, prefix, suffix)) if target.key != "CUSTOM" else None
                paths = export(context, ts, settings, naming=naming, directory=directory)
                from ..export.exporter import safe_name
                labels = [label for label, _ in _definitions(settings)]
                files = list(zip(labels, paths))
                written.extend(paths)
                if target.key != "CUSTOM":
                    info = directory / f"{safe_name(ts.name)}_manifest.json"
                    info.write_text(json.dumps(manifest(target, ts, files), indent=2))
                    written.append(info)
            finally:
                for key, value in saved.items():
                    setattr(settings, key, value)
            objects.update(ref.object for ref in ts.assignments if ref.object)
        if project.export_mesh and target.mesh_format and objects:
            written.append(_export_mesh(context, target, directory, objects))
        context.scene.open_painter.status = f"Exported {len(written)} files for {target.label} to {directory}"
        self.report({"INFO"}, context.scene.open_painter.status)


def _definitions(settings):
    from ..export.exporter import blender, custom, unity, unreal, godot, gltf, unity_urp, unity_hdrp
    if settings.preset == "CUSTOM":
        return custom.outputs(settings)
    return {"BLENDER": blender, "UNREAL": unreal, "UNITY": unity, "GODOT": godot, "GLTF": gltf,
            "UNITY_URP": unity_urp, "UNITY_HDRP": unity_hdrp}[settings.preset].outputs()


def _export_mesh(context, target, directory, objects):
    from ..export.exporter import safe_name
    name = safe_name(context.scene.name)
    previous = [obj for obj in context.selected_objects]
    active = context.view_layer.objects.active
    for obj in context.view_layer.objects:
        obj.select_set(obj in objects)
    try:
        if target.mesh_format == "FBX":
            path = directory / f"{name}.fbx"
            if path.exists():
                raise ValueError(f"{path.name} already exists in the export directory.")
            bpy.ops.export_scene.fbx(filepath=str(path), use_selection=True, object_types={"MESH"}, **target.mesh_options)
        else:
            path = directory / f"{name}.glb"
            if path.exists():
                raise ValueError(f"{path.name} already exists in the export directory.")
            bpy.ops.export_scene.gltf(filepath=str(path), use_selection=True, **target.mesh_options)
    finally:
        for obj in context.view_layer.objects:
            obj.select_set(obj in previous)
        context.view_layer.objects.active = active
    return path


def draw_target(layout, context):
    """Target selector, engine preview toggles and one-click export."""
    project = context.scene.open_painter
    target = get(project.target)
    box = layout.box()
    box.label(text="Target Renderer", icon="RESTRICT_RENDER_OFF")
    box.prop(project, "target", text="")
    row = box.row(align=True)
    row.prop(project, "engine_preview", toggle=True, icon="SHADING_RENDERED")
    if project.engine_preview:
        column = box.column(align=True)
        column.prop(project, "engine_preview_quantize")
        column.prop(project, "engine_preview_normal_reconstruct")
        column.prop(project, "engine_preview_ao_indirect")
    if target.key != "CUSTOM":
        box.label(text=f"{'DirectX' if target.normal == 'DIRECTX' else 'OpenGL'} normals · "
                       f"{'smoothness' if target.gloss == 'SMOOTHNESS' else 'roughness'} · AO {target.ao_usage.lower()}")
        box.label(text=f"Packing: {target.preset} · naming {target.naming}"
                       + (f" · mesh {target.mesh_format}" if target.mesh_format else ""))
    box.prop(project, "export_directory")
    if target.mesh_format:
        box.prop(project, "export_mesh")
    row = box.row(align=True)
    row.operator("open_painter.export_target", text=f"Export → {target.label}", icon="EXPORT")
    op = row.operator("open_painter.export_target", text="Selected Objects")
    op.all_sets = True


CLASSES = (OP_OT_export_target,)
