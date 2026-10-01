"""Render the Damaged Bevel and Rounded Edges effects in EEVEE and Cycles.

The reference script's Bevel/AO shader nodes only evaluate in Cycles. This
validation bakes the Bevel Mask and Bevel Normal maps, applies the effects to a
painted cube and renders the same view with EEVEE and with Cycles, before and
after the effects, then reports how much each render changed along the edges.
It needs a window (EEVEE renders on the GPU)::

    BLENDER_USER_RESOURCES=/private/tmp/op-bevel-user blender --factory-startup \
      --python scripts/validate_damaged_bevel_eevee.py -- --quit -ApplePersistenceIgnoreState YES
"""
import json
import os
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import open_painter
from open_painter.core import image_manager as images, texture_set_manager as sets, layer_stack as stack
from open_painter.baking.bake_manager import bake_selected
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.smart_materials import composer

OUT = Path(os.environ.get("OP_BEVEL_REPORT_DIR", ROOT / ".blender-test" / "damaged-bevel"))
OUT.mkdir(parents=True, exist_ok=True)
open_painter.register()
scene = bpy.context.scene
cube = bpy.context.object
cube.data.materials.clear()
ts = sets.create(scene, "Bevel demo", 1024)
sets.assign(scene, ts, cube)
# Painted base: a blue coat over steel, so chips expose the substrate colour too.
composer.apply_document(ts, {"layers": [
    composer.layer("Steel", (.56, .57, .58), .38, 1),
    composer.layer("Blue coat", (.06, .2, .45), .42, 0, height_value=.56),
]}, "Demo base")
ts.bake.normal = ts.bake.ao = ts.bake.curvature = False
ts.bake.selected_to_active = False
ts.bake.bevel_normal = ts.bake.bevel_mask = True
ts.bake.bevel_radius, ts.bake.bevel_samples, ts.bake.samples = .12, 16, 16
bake_selected(bpy.context, ts)

cam_data = bpy.data.cameras.new("cam"); cam = bpy.data.objects.new("cam", cam_data)
scene.collection.objects.link(cam); scene.camera = cam
cam.location = Vector((1.9, -1.6, 1.75)); cam.rotation_euler = (Vector((.55, -.35, .7)) - cam.location).to_track_quat("-Z", "Y").to_euler()
cam_data.lens = 70
sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN")); scene.collection.objects.link(sun)
sun.data.energy = 4; sun.rotation_euler = (0.8, 0.2, 0.9)
scene.world = scene.world or bpy.data.worlds.new("w"); scene.world.color = (.3, .3, .3)
scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 640, 480, 100
scene.render.image_settings.file_format = "PNG"


def render(engine, name):
    scene.render.engine = engine
    if engine == "CYCLES":
        scene.cycles.device, scene.cycles.samples = "CPU", 32
    else:
        scene.eevee.taa_render_samples = 16
    scene.render.filepath = str(OUT / name)
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(str(OUT / name), check_existing=False)
    try:
        px = np.empty(image.size[0] * image.size[1] * 4, np.float32)
        image.pixels.foreach_get(px)
        return px.reshape(image.size[1], image.size[0], 4)[..., :3]
    finally:
        bpy.data.images.remove(image)


def evaluate():
    LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)


report = {"blender": bpy.app.version_string, "build": bpy.app.build_hash.decode(), "renders": {}}
evaluate()
before = {engine: render(engine, f"before-{engine.lower()}.png") for engine in ("BLENDER_EEVEE", "CYCLES")}
composer.apply_effect(ts, "Rounded Edges")
composer.apply_effect(ts, "Damaged Bevel")
evaluate()
after = {engine: render(engine, f"after-{engine.lower()}.png") for engine in ("BLENDER_EEVEE", "CYCLES")}
for engine in before:
    diff = np.abs(after[engine] - before[engine]).mean(axis=-1)
    changed = diff > .04
    report["renders"][engine] = dict(mean_abs_difference=float(diff.mean()), changed_pixels=float(changed.mean()),
                                     max_difference=float(diff.max()))
# The two engines must agree on where the effect is, not merely that something changed.
eevee_mask = np.abs(after["BLENDER_EEVEE"] - before["BLENDER_EEVEE"]).mean(axis=-1) > .04
cycles_mask = np.abs(after["CYCLES"] - before["CYCLES"]).mean(axis=-1) > .04
overlap = (eevee_mask & cycles_mask).sum() / max((eevee_mask | cycles_mask).sum(), 1)
report["engine_agreement_jaccard"] = float(overlap)
report["pass"] = bool(report["renders"]["BLENDER_EEVEE"]["changed_pixels"] > .01 and overlap > .4)
(OUT / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print("DAMAGED_BEVEL_EEVEE", json.dumps(report), flush=True)
if "--quit" in sys.argv:
    bpy.ops.wm.quit_blender()
