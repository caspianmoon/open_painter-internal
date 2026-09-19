"""Worker for export_safety.py: builds the same fixtures and runs one real target export into
OP_WORKER_FOLDER with padding mode OP_WORKER_PADDING, under whatever OP_EXPORT_FAILPOINT or
OP_EXPORT_FAULT the caller set, so hard terminations hit the production commit path.

    blender --background --factory-startup --python tests/integration/export_safety_worker.py
"""
import os
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests/integration"))
import open_painter
import quality_fixtures as fixtures
from open_painter.export import plan as planning
from open_painter.ui import export_view

open_painter.register()
scene = bpy.context.scene
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
project = scene.open_painter
fixtures.seam_object(scene, resolution=512)
fixtures.channel_plate(scene, resolution=512)
for ts in project.texture_sets:
    ts.export.padding_mode = os.environ.get("OP_WORKER_PADDING", "AUTOMATIC")
view = export_view.view_settings(bpy.context)
view.contents, view.scope, view.folder_source = "TARGET", "ALL", "PROJECT"
project.target, project.export_mesh = "UNREAL", True
project.export_directory = os.environ["OP_WORKER_FOLDER"]
plan = planning.resolve(bpy.context, contents="TARGET", scope="ALL", folder_source="PROJECT")
written = planning.execute(bpy.context, plan, overwrite=os.environ.get("OP_WORKER_OVERWRITE", "1") == "1")
print("EXPORT_SAFETY_WORKER_DONE", len(written), plan.outcome.message, flush=True)
