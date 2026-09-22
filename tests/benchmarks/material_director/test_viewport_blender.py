"""Visible, no-provider camera-context probe on an immutable benchmark fixture.

blender --factory-startup --disable-autoexec --python test_viewport_blender.py -- \
  --fixture path/to/A04_vase_neutral.blend --output /tmp/viewport-probe

This checks the exact production viewport.capture path. The probe only changes
viewport shading/UI state in memory and never saves or authors the fixture.
"""
import argparse
import base64
import json
from pathlib import Path
import struct
import sys
import time
import traceback
import zlib

import bpy
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(HERE), str(ROOT / "src")]
import open_painter
from open_painter.ai import viewport
from open_painter.evaluation import shader_preview
from capture_evidence import material_state, sha256, write_json, fixed_rig_check

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--fixture", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
args.output.mkdir(parents=True, exist_ok=True)
source_hash = sha256(args.fixture)
open_painter.register()
bpy.ops.wm.open_mainfile(filepath=str(args.fixture), load_ui=False, use_scripts=False)
start = time.monotonic()
state = dict(ready_frames=0, captured=False, stage="setup", attempts=[],
             fixture_sha256=source_hash, provider_calls=0, shading="MATERIAL", scene_lights=True, scene_world=True)
scene = None


def setup():
    global window, area, region, scene, config, project, target, source_state
    window = bpy.context.window or next(iter(bpy.context.window_manager.windows), None)
    if window is None:
        return
    area = next(item for item in window.screen.areas if item.type == "VIEW_3D")
    region = next(item for item in area.regions if item.type == "WINDOW")
    scene = window.scene
    config = json.loads(scene["md_benchmark_config"])
    project = scene.open_painter
    project.active_texture_set = next(i for i, ts in enumerate(project.texture_sets) if ts.id == config["target_set_ids"][0])
    target = project.texture_sets[project.active_texture_set]
    source_state = material_state(scene, config)
    state["config"] = config
    if config.get("primary_evidence"):
        state["fixed_rig_before"] = fixed_rig_check(scene, config)
        if state["fixed_rig_before"]["status"] != "PASS":
            raise ValueError("Frozen primary Eevee rig validation failed: " + repr(state["fixed_rig_before"]))
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.open_painter.open_editor()
        scene.camera = scene.objects[config["input_camera_name"]]
        space = area.spaces.active
        space.shading.type = "MATERIAL"
        space.shading.use_scene_world = True
        space.shading.use_scene_lights = True
        space.region_3d.view_perspective = "CAMERA"
        space.region_3d.view_camera_zoom = 0
        space.region_3d.update()
        shader_preview.tick(scene, target, force_repair=True)
        area.tag_redraw()
    state["stage"] = "wait"


def decoded(png):
    offset, compressed = 8, bytearray()
    while offset < len(png):
        length = struct.unpack(">I", png[offset:offset + 4])[0]
        kind, data = png[offset + 4:offset + 8], png[offset + 8:offset + 8 + length]
        if kind == b"IHDR":
            width, height, depth, color, *_ = struct.unpack(">IIBBBBB", data)
            assert depth == 8 and color == 6
        elif kind == b"IDAT":
            compressed.extend(data)
        offset += length + 12
    rows = np.frombuffer(zlib.decompress(compressed), np.uint8).reshape(height, width * 4 + 1)
    assert not rows[:, 0].any(), "Production PNG encoder unexpectedly uses row filters"
    return rows[:, 1:].reshape(height, width, 4)


def capture(label, isolate):
    data_url = viewport.capture(bpy.context, target, max_side=768, isolate=isolate)
    png = base64.b64decode(data_url.split(",", 1)[1])
    path = args.output / (label + ".png")
    path.write_bytes(png)
    pixels = decoded(png)
    rgb = pixels[..., :3]
    entry = dict(label=label, path=str(path), sha256=sha256(path), size=list(pixels.shape[:2]),
                 rgb_min=int(rgb.min()), rgb_max=int(rgb.max()), rgb_mean=float(rgb.mean()),
                 rgb_std=float(rgb.std()), nonblack_fraction=float(np.any(rgb > 3, axis=-1).mean()),
                 isolate=isolate, seconds=time.monotonic() - start)
    state["attempts"].append(entry)
    print("VIEWPORT_CAPTURE", json.dumps(entry), flush=True)


def finish(error=None):
    state["error"] = error
    state["fixture_file_unchanged"] = sha256(args.fixture) == source_hash
    state["authoring_and_geometry_unchanged"] = material_state(scene, config) == source_state if scene else None
    if scene and config.get("primary_evidence"):
        state["fixed_rig_after"] = fixed_rig_check(scene, config)
    state["status"] = "FAILED" if error or not state["fixture_file_unchanged"] or not state["authoring_and_geometry_unchanged"] else "CAPTURED_REQUIRES_IMAGE_INSPECTION"
    write_json(args.output / "viewport_probe.json", state)
    print("VIEWPORT_PROBE", state["status"], flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    try:
        if time.monotonic() - start > 90:
            raise TimeoutError("Viewport probe exceeded its 90 second cap")
        if state["stage"] == "setup":
            setup()
            return .25
        with bpy.context.temp_override(window=window, area=area, region=region):
            shader_preview.tick(scene, target)
            info = shader_preview.diagnostics(scene, target)
            if info.get("error"):
                raise RuntimeError(info["error"])
            state["ready_frames"] = state["ready_frames"] + 1 if shader_preview.current(scene, target) else 0
            if state["ready_frames"] >= 5 and time.monotonic() - start >= 5:
                if state["stage"] == "wait":
                    capture("material-scene-lights-world", False)
                    state["stage"], state["first_capture_time"] = "isolated", time.monotonic()
                elif state["stage"] == "isolated" and time.monotonic() - state["first_capture_time"] >= 3:
                    capture("material-scene-lights-world-isolated", True)
                    bpy.ops.screen.screenshot(filepath=str(args.output / "visible-viewport.png"))
                    finish()
                    return None
            area.tag_redraw()
    except Exception:
        finish(traceback.format_exc())
        return None
    return .25


bpy.app.timers.register(tick, first_interval=1)
