"""Local infrastructure smoke; synthetic fixtures are NOT scored AI evidence."""
import json
import os
from pathlib import Path
import sys
import tempfile

import bpy
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[2] / "src")]
import open_painter
from open_painter.authoring import document, scene as scene_transactions
from open_painter.core import texture_set_manager as sets
from capture_evidence import (capture_checkpoint, compare_buffers, compare_checkpoints, material_state,
                              capture_channels, render_views, fixed_rig_check, PRIMARY_VIEWS)
from verify_final import finalize_session, undo_check, export_final, reconstruct, verify_offline

open_painter.register()
scene = bpy.context.scene
for obj in list(scene.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
ids = []
for index in range(2):
    bpy.ops.mesh.primitive_cube_add(location=(index * 3, 0, 0))
    obj = bpy.context.object
    ts = sets.create(scene, f"Evidence set {index}", 512)
    sets.assign(scene, ts, obj)
    ids.append(ts.id)
config = dict(target_set_ids=ids, render_views=False, channels=["BASE_COLOR", "ROUGHNESS"], repeat=2)
scene.render.engine = "BLENDER_EEVEE"
scene.render.resolution_x = scene.render.resolution_y = 32
scene.render.resolution_percentage = 100
bpy.ops.object.camera_add(location=(6, -9, 5))
camera = bpy.context.object
camera.name = "BENCH_CAM_three_quarter"
from mathutils import Vector
camera.rotation_euler = (Vector((1.5, 0, 0)) - camera.location).to_track_quat("-Z", "Y").to_euler()
scene.camera = camera
config["camera_names"] = [camera.name]
config["relight_collection_names"] = []
for index in range(2):
    collection = bpy.data.collections.new(f"BENCH_LIGHT_{index}")
    scene.collection.children.link(collection)
    collection.hide_render = bool(index)
    light = bpy.data.lights.new(f"Light {index}", "AREA")
    light.energy = 1000
    obj = bpy.data.objects.new(light.name, light)
    collection.objects.link(obj)
    obj.location = (index * 6 - 3, -4, 6)
    obj.rotation_euler = (-obj.location).to_track_quat("-Z", "Y").to_euler()
    config["relight_collection_names"].append(collection.name)
scene["md_benchmark_config"] = json.dumps(config)


def apply(identifier, value, create=False):
    targets = []
    for ts in scene.open_painter.texture_sets:
        layer_id = "new:base" if create else ts.layers[0].id
        commands = ([dict(op="add_layer", id=layer_id, name="Base", kind="FILL")] if create else [])
        commands += [dict(op="set_channel", layer_id=layer_id, channel="ROUGHNESS", values=dict(value=value))]
        targets.append(dict(texture_set_id=ts.id, proposal=dict(version=1, summary=identifier, commands=commands)))
    scene_transactions.apply(scene, dict(version=1, summary=identifier, targets=targets), operation_id=identifier,
        digests={ts.id: document.digest(ts) for ts in scene.open_painter.texture_sets})


with tempfile.TemporaryDirectory(prefix="md-benchmark-evidence-") as folder:
    apply("create", .5, True)
    if "--nonfinite-only" in sys.argv:
        from unittest.mock import patch
        from open_painter.core import image_manager
        original_pixels = image_manager.pixels

        def nonfinite_pixels(image):
            values = original_pixels(image).copy()
            values[0, 0, 0] = np.nan
            values[0, 0, 1] = np.inf
            return values

        with patch.object(image_manager, "pixels", side_effect=nonfinite_pixels):
            failed = capture_channels(bpy.context, folder, "nonfinite", config=dict(config, channels=["BASE_COLOR"]))
        assert failed["status"] == "FAILED" and len(failed["errors"]) == len(ids), failed
        for entry in failed["sets"].values():
            assert entry["status"] == "FAILED"
            record = entry["channels"]["BASE_COLOR"]
            assert record["finite"] is False and record["nonfinite_samples"] == 2, record
            assert record["preview"] is None and record["preview_status"] == "NOT_WRITTEN_NONFINITE"
            captured = np.load(record["path"], allow_pickle=False)
            assert np.isnan(captured[0, 0, 0]) and np.isposinf(captured[0, 0, 1])
        print("BENCHMARK_NONFINITE_EVIDENCE_PASS", flush=True)
        raise SystemExit(0)
    before = capture_checkpoint(bpy.context, folder, "after_create", config=config)
    assert before["channels"]["status"] == "CAPTURED", before["channels"]
    apply("refine1", .3)
    first = capture_checkpoint(bpy.context, folder, "after_refine_1", config=config)
    apply("refine2", .7)
    final = capture_checkpoint(bpy.context, folder, "after_refine_2", config=config)
    assert compare_checkpoints(first, final)["status"] == "FAIL"
    assert compare_checkpoints(first, final, protected_set_ids=[])["status"] == "PASS"
    finalized = finalize_session(bpy.context, folder, config=config)
    assert finalized["status"] == "SAVED"
    undone = undo_check(bpy.context, folder, config)
    assert undone["status"] == "PASS", undone
    assert undone["grouped"] and len(undone["affected_sets"]) == 2
    isolated = Path(folder) / "empty-provider-home"
    isolated.mkdir()
    previous_home = os.environ.get("CODEX_HOME")
    os.environ["CODEX_HOME"] = str(isolated)
    try:
        offline = verify_offline(folder, render=False, export=False, check_4k=False)
        assert offline["provider_isolation"]["status"] == "PASS", offline
        assert offline["persistence"]["status"] == "PASS", offline
        assert offline["undo"]["status"] == "PASS", offline
    finally:
        if previous_home is None:
            os.environ.pop("CODEX_HOME", None)
        else:
            os.environ["CODEX_HOME"] = previous_home
    bpy.ops.wm.open_mainfile(filepath=str(Path(folder) / "final.blend"), load_ui=False, use_scripts=False)
    reopened = capture_checkpoint(bpy.context, folder, "reopened", config=config)
    assert compare_checkpoints(final, reopened, exact_channels=True)["status"] == "PASS"
    exported = export_final(bpy.context, folder, config, resolution=512)
    assert exported["status"] == "CAPTURED", exported
    reconstructed = reconstruct(bpy.context, folder, exported, dict(config, render_views=True))
    assert reconstructed["status"] == "CAPTURED", reconstructed
    assert reconstructed["views"]["status"] == "CAPTURED", reconstructed["views"]
    assert reconstructed["relight"]["status"] == "CAPTURED", reconstructed["relight"]
    bpy.ops.wm.open_mainfile(filepath=reconstructed["path"], load_ui=False, use_scripts=False)
    assert len([obj for obj in bpy.context.scene.objects if obj.type == "MESH"]) == 2
    assert len(bpy.context.scene.open_painter.texture_sets) == 0
    assert all(not mat.get("open_painter_managed") for obj in bpy.context.scene.objects if obj.type == "MESH" for mat in obj.data.materials)
    assert all(node.image.packed_file for mat in bpy.data.materials if mat.use_nodes for node in mat.node_tree.nodes if node.type == "TEX_IMAGE")
    a = np.zeros((10, 10, 4), np.float32)
    b = a.copy(); b[0, 0] = 1
    assert compare_buffers(a, b)["status"] == "FAIL"
    valid = np.ones((10, 10), bool); valid[0, 0] = False
    assert compare_buffers(a, b, valid=valid)["status"] == "PASS"
    assert compare_buffers(a, b, valid=np.zeros((10, 10), bool))["status"] == "NOT_TESTED"
print("BENCHMARK_EVIDENCE_SMOKE_PASS", flush=True)

# Primary protocol: one camera, frozen Eevee rig, exact ten evidence filenames.
scene = bpy.data.scenes.new("Primary evidence smoke")
bpy.context.window.scene = scene
bpy.ops.mesh.primitive_cube_add(location=(0, 0, 1))
obj = bpy.context.object
ts = sets.create(scene, "Primary neutral", 512)
sets.assign(scene, ts, obj)
scene.render.engine = "BLENDER_EEVEE"
scene.render.resolution_x = scene.render.resolution_y = 1024
scene.render.resolution_percentage = 100
scene.render.use_compositing = scene.render.use_sequencer = False
if hasattr(scene, "eevee") and hasattr(scene.eevee, "taa_render_samples"):
    scene.eevee.taa_render_samples = 8
bpy.ops.object.camera_add(location=(3, -5, 3))
camera = bpy.context.object
camera.name = "BENCHMARK_CAMERA"
camera.data.type = "ORTHO"
camera.data.ortho_scale = 4
scene.camera = camera
poses = {}
for name, location, scale in (("front", (3, -5, 3), 4), ("back", (-3, 5, 3), 4), ("close", (3, -5, 3), 2.5)):
    camera.location = location
    camera.rotation_euler = (Vector((0, 0, 1)) - camera.location).to_track_quat("-Z", "Y").to_euler()
    bpy.context.view_layer.update()
    poses[name] = dict(matrix_world=[list(row) for row in camera.matrix_world], type="ORTHO", ortho_scale=scale, lens=50)
from mathutils import Matrix
camera.matrix_world = Matrix(poses["front"]["matrix_world"])
for name, location in (("Key", (3, -4, 5)), ("Fill", (-3, -2, 3)), ("Rim", (0, 4, 5))):
    light = bpy.data.lights.new(name, "AREA")
    light.energy, light.shape, light.size = 500, "DISK", 3
    light_obj = bpy.data.objects.new(name, light)
    scene.collection.objects.link(light_obj)
    light_obj.location = location
    light_obj.rotation_euler = (Vector((0, 0, 1)) - light_obj.location).to_track_quat("-Z", "Y").to_euler()
config = dict(primary_evidence=True, camera_name=camera.name, camera_names=[camera.name], camera_poses=poses,
              input_camera_pose="front", render_engine="BLENDER_EEVEE", render_size=[1024, 1024],
              fixed_light_names=["Key", "Fill", "Rim"], target_set_ids=[ts.id])
bpy.context.view_layer.update()
assert fixed_rig_check(scene, config)["status"] == "PASS"
baseline = material_state(scene, config)
with tempfile.TemporaryDirectory(prefix="md-primary-evidence-") as folder:
    for label in PRIMARY_VIEWS:
        result = render_views(bpy.context, folder, label, config)
        assert result["status"] == "CAPTURED", result
        assert len(result["views"]) == (1 if label == "before" else 3)
        assert result["input_pose_restored"]
        actual = material_state(scene, config)
        if actual != baseline:
            print("PRIMARY_CAPTURE_MUTATION", document.changes(baseline, actual), flush=True)
        assert actual == baseline
    assert sorted(path.name for path in Path(folder).glob("*.png")) == sorted(
        filename for plan in PRIMARY_VIEWS.values() for _, filename in plan)
scene.render.resolution_x = 512
assert fixed_rig_check(scene, config)["status"] == "FAIL"
scene.render.resolution_x = 1024
camera.location.x += 1
assert fixed_rig_check(scene, config)["status"] == "FAIL"
print("BENCHMARK_PRIMARY_EEVEE_EVIDENCE_PASS", flush=True)
