"""The export view (UI plan phase 5): one resolved plan for the preview and the export, an
explicit scope that never widens, project defaults and set overrides shown together and
kept, and a view that changes no saved setting when it opens.
"""
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests/integration"))
import open_painter
import ui_fixture
from ui_layout_recorder import Recorder
from open_painter.authoring import document
from open_painter.export import plan as planning
from open_painter.ui import editor, export_view

open_painter.register()


def export_settings(project):
    return [(ts.name, {k: getattr(ts.export, k) for k in ("directory", "preset", "prefix", "suffix", "file_format",
                                                         "bit_depth", "resolution", "follow_target")})
            for ts in project.texture_sets] + [("project", dict(target=project.target, directory=project.export_directory,
                                                                mesh=project.export_mesh))]


def refused(message):
    """Background Blender raises for a reported error: the refusal and its wording."""
    try:
        result = bpy.ops.open_painter.export_plan()
    except RuntimeError as exc:
        assert message in str(exc), str(exc)
        return True
    assert result == {"CANCELLED"} and message in bpy.context.scene.open_painter.status
    return True


def run(context):
    scene = context.scene
    project = scene.open_painter
    ids = ui_fixture.build(scene)          # Body follows Unreal; Panel keeps UNITY / TIFF 16 / P_ / its own folder
    editor.mount(context.window, context.area)
    body = next(ts for ts in project.texture_sets if ts.id == ids["body"])
    panel_set = next(ts for ts in project.texture_sets if ts.id == ids["panel"])
    view = export_view.view_settings(context)
    with TemporaryDirectory(prefix="op-export-view-") as tmp:
        root = Path(tmp)
        project.export_directory = str(root / "target")
        panel_set.export.directory = str(root / "panel-own")
        saved = export_settings(project)
        before = [document.digest(ts) for ts in project.texture_sets]

        # --- the target package: textures with target naming, a manifest per set, the mesh ---
        view.contents, view.scope = "TARGET", "ACTIVE"
        plan = export_view.current_plan(context)
        assert plan.ready and [item.name for item in plan.sets] == ["Body"] and plan.objects == ["Cube"]
        names = [output.name for output in plan.sets[0].outputs]
        assert names == ["T_Body_BC.png", "T_Body_N.png", "T_Body_ORM.png"], names
        assert plan.sets[0].manifest.name == "Body_manifest.json" and plan.mesh.name == "Scene.fbx"
        assert plan.sets[0].normal == "DIRECTX" and plan.sets[0].follow_target
        orm = plan.sets[0].outputs[2]
        assert orm.packing == "R=AO G=Roughness B=Metallic A=One" and orm.color_space == "Non-Color", orm.packing
        assert plan.sets[0].outputs[0].color_space == "sRGB" and plan.sets[0].size == 512
        # Drawing the whole view mutates nothing.
        layout = Recorder()
        export_view.OP_PT_export_view.draw(SimpleNamespace(layout=layout), context)
        text = layout.text
        for title in ("Contents", "Scope", "Destination Preset", "Folder", "Output Preview", "Prepare Export"):
            assert title in text, title
        assert "T_Body_ORM.png" in text and "Body_manifest.json" in text and "Scene.fbx" in text
        assert "Export 5 Files" in text and "Stage Mesh LOD" in text
        assert export_settings(project) == saved and [document.digest(ts) for ts in project.texture_sets] == before
        print("EXPORT_VIEW plan and preview: PASS", flush=True)

        # --- the export writes exactly the previewed files; overrides survive ---
        assert bpy.ops.open_painter.export_plan() == {"FINISHED"}, project.status
        written = sorted(p.name for p in (root / "target").iterdir())
        assert written == sorted(p.name for p in plan.files), (written, [p.name for p in plan.files])
        info = json.loads((root / "target" / "Body_manifest.json").read_text())
        assert info["normal_map"]["convention"] == "DIRECTX" and len(info["files"]) == 3
        assert export_settings(project) == saved, "the export borrows target settings and puts the set's own back"
        # Existing files are conflicts; the export refuses until replacing is allowed.
        plan = export_view.current_plan(context)
        assert len(plan.conflicts) == 5 and all(output.exists for output in plan.sets[0].outputs)
        assert refused("already exist")
        view.overwrite = True
        assert bpy.ops.open_painter.export_plan() == {"FINISHED"}, project.status
        view.overwrite = False
        print("EXPORT_VIEW files match the preview: PASS", flush=True)

        # --- scope: selected objects' sets, never widened ---
        for obj in scene.objects:
            obj.select_set(False)
        view.scope = "SELECTED"
        plan = export_view.current_plan(context)
        assert not plan.sets and plan.problems == ["Nothing is selected: select the objects whose sets should export"]
        assert refused("Nothing is selected")
        scene.objects["Light"].select_set(True)
        plan = export_view.current_plan(context)
        assert not plan.sets and plan.problems == ["No selected object belongs to a texture set"], plan.problems
        scene.objects["Light"].select_set(False)
        scene.objects["Panel"].select_set(True)
        plan = export_view.current_plan(context)
        assert [item.name for item in plan.sets] == ["Panel"] and plan.objects == ["Panel"]
        # Panel keeps its own settings on the target route: the overrides are named, not dropped.
        item = plan.sets[0]
        assert not item.follow_target and item.preset == "UNREAL" and item.file_format == "PNG"
        assert item.overrides == ["preset UNITY (set) → UNREAL (target)", "format TIFF (set) → PNG (target)",
                                  "bit depth 16 (set) → 8 (target)"], item.overrides
        assert [o.name for o in item.outputs] == ["T_P_Panel_BC.png", "T_P_Panel_N.png", "T_P_Panel_ORM.png"]
        assert plan.directory == root / "target" and any("keeps its own folder" in note for note in plan.notes)
        view.folder_source = "SET"
        plan = export_view.current_plan(context)
        assert plan.directory == root / "panel-own", "the set's saved folder is a choice, never discarded"
        view.folder_source = "PROJECT"
        view.scope = "ALL"
        plan = export_view.current_plan(context)
        assert [item.name for item in plan.sets] == ["Body", "Panel"] and plan.objects == ["Cube", "Panel"]
        print("EXPORT_VIEW scope: PASS", flush=True)

        # --- the set's own texture export uses its own preset, naming and folder ---
        view.contents, view.scope = "TEXTURES", "SELECTED"
        plan = export_view.current_plan(context)
        item = plan.sets[0]
        assert item.naming == "SET" and item.preset == "UNITY" and item.file_format == "TIFF" and item.bit_depth == "16"
        assert [o.name for o in item.outputs] == ["P_Panel_BaseColor.tif", "P_Panel_Normal.tif",
                                                  "P_Panel_MetallicSmoothness.tif", "P_Panel_Occlusion.tif"], \
            [o.name for o in item.outputs]
        assert item.manifest is None and plan.mesh is None and plan.directory == root / "panel-own"
        assert bpy.ops.open_painter.export_plan() == {"FINISHED"}, project.status
        assert sorted(p.name for p in (root / "panel-own").iterdir()) == sorted(o.name for o in item.outputs)
        assert export_settings(project) == saved
        # Bundle contents are named, not folded into the texture export.
        view.contents = "BUNDLE"
        plan = export_view.current_plan(context)
        assert plan.bundle == root / "panel-own" / "Panel_bundle" and any("evaluated scene" in n for n in plan.notes)
        print("EXPORT_VIEW own settings and bundle: PASS", flush=True)

        # --- overrides survive save and reopen ---
        blend = root / "roundtrip.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(blend))
        bpy.ops.wm.open_mainfile(filepath=str(blend))
        project = bpy.context.scene.open_painter
        panel_set = next(ts for ts in project.texture_sets if ts.name == "Panel")
        assert (panel_set.export.preset, panel_set.export.file_format, panel_set.export.bit_depth, panel_set.export.prefix,
                panel_set.export.follow_target) == ("UNITY", "TIFF", "16", "P_", False)
        assert project.target == "UNREAL" and project.export_directory == str(root / "target")
        assert panel_set.export.directory == str(root / "panel-own")
        print("EXPORT_VIEW_PASS", flush=True)


area = next(a for a in bpy.context.screen.areas if a.type == "VIEW_3D")
region = next(r for r in area.regions if r.type == "WINDOW")
with bpy.context.temp_override(area=area, region=region):
    run(bpy.context)
