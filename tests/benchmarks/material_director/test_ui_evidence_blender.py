"""No-provider proof of the normal companion inspector, without resizing a view.

Run in visible Blender with --fixture final.blend --output DIRECTORY after --.
Only UI selection changes in memory. The source .blend is never saved.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import resource
import traceback

import bpy

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(HERE), str(ROOT / "src")]
import open_painter
from open_painter.evaluation import shader_preview
from open_painter.ui import companion, selection
from open_painter.ui.dock import state as dock_state
from capture_evidence import fixed_rig_check, material_state, sha256, write_json

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--fixture", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--helper", action="store_true", help="Exercise the shared bounded top-to-bottom capture helper")
parser.add_argument("--concurrency-barrier", type=Path, help="Both no-provider processes must reach their local window before capture")
parser.add_argument("--probe-id", default="single")
parser.add_argument("--render-proof", action="store_true")
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
args.output.mkdir(parents=True, exist_ok=True)
source_sha = sha256(args.fixture)
open_painter.register()
bpy.context.preferences.view.show_splash = False
bpy.ops.wm.open_mainfile(filepath=str(args.fixture), load_ui=False, use_scripts=False)
started = time.monotonic()
report = dict(provider_calls=0, stage="setup", ready_frames=0, fixture_sha256=source_sha,
              pid=os.getpid(), started_at=time.time(), probe_id=args.probe_id)
scene = None


def finish(error=None):
    report["error"] = error
    if scene is not None:
        for ts in scene.open_painter.texture_sets:
            ts.active_layer = selected_layers[ts.id]
            for layer in ts.layers:
                layer.active_mask = selected_masks[layer.id]
        scene.open_painter.active_texture_set = selected_set
        dock_state.get(area).selection_kind = selected_kind
        report["state_unchanged"] = material_state(scene, config) == before
        report["fixed_rig_after"] = fixed_rig_check(scene, config)
        report["viewport_size_after"] = [area.width, area.height, region.width, region.height]
        report["viewport_not_resized"] = report["viewport_size_before"] == report["viewport_size_after"]
    report["fixture_file_unchanged"] = sha256(args.fixture) == source_sha
    report["finished_at"] = time.time()
    report["elapsed_seconds"] = time.monotonic() - started
    report["peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)
    report["status"] = "FAILED" if error or not all(report.get(k) for k in (
        "state_unchanged", "viewport_not_resized", "fixture_file_unchanged")) else "CAPTURED_REQUIRES_IMAGE_INSPECTION"
    write_json(args.output / "ui_probe.json", report)
    print("UI_EVIDENCE_PROBE", report["status"], flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    global window, area, region, scene, config, target, before
    global selected_set, selected_layers, selected_masks, selected_kind
    try:
        if time.monotonic() - started > 180:
            raise TimeoutError("Companion inspector probe exceeded 180 seconds")
        if report["stage"] == "setup":
            window = bpy.context.window or next(iter(bpy.context.window_manager.windows))
            area = next(a for a in window.screen.areas if a.type == "VIEW_3D")
            region = next(r for r in area.regions if r.type == "WINDOW")
            scene = window.scene
            config = json.loads(scene["md_benchmark_config"])
            project = scene.open_painter
            selected_set = project.active_texture_set
            selected_layers = {ts.id: ts.active_layer for ts in project.texture_sets}
            selected_masks = {layer.id: layer.active_mask for ts in project.texture_sets for layer in ts.layers}
            selected_kind = dock_state.get(area).selection_kind
            before = material_state(scene, config)
            report["fixed_rig_before"] = fixed_rig_check(scene, config)
            assert report["fixed_rig_before"]["status"] == "PASS", report["fixed_rig_before"]
            project.active_texture_set = next(i for i, ts in enumerate(project.texture_sets)
                                              if ts.id == config["target_set_ids"][0])
            target = project.texture_sets[project.active_texture_set]
            with bpy.context.temp_override(window=window, area=area, region=region):
                assert bpy.ops.open_painter.open_editor() == {"FINISHED"}
                space = area.spaces.active
                space.shading.type = "MATERIAL"
                space.shading.use_scene_world = True
                space.shading.use_scene_lights = True
                space.overlay.show_overlays = False
                space.region_3d.view_perspective = "CAMERA"
                space.region_3d.view_camera_zoom = 0
                space.region_3d.update()
                report["viewport_size_before"] = [area.width, area.height, region.width, region.height]
                assert bpy.ops.open_painter.open_inspector(placement="EXISTING") == {"FINISHED"}
                masks = [(layer, mask) for layer in target.layers for mask in layer.masks]
                layer, mask = next(((layer, mask) for layer, mask in masks if mask.source == "SCENE"), masks[0])
                resolved = selection.select_mask(bpy.context, layer.id, mask.id, ts=target, synchronize=False)
                report["selected"] = dict(texture_set_id=target.id, layer_id=layer.id,
                    layer_name=layer.name, mask_id=mask.id, mask_name=mask.name,
                    mask_source=mask.source, resolved_kind=resolved.kind)
                inspector = companion.companion_for(window.screen)
                report["inspector"] = dict(area_type=inspector.type, context=inspector.spaces.active.context)
                shader_preview.tick(scene, target, force_repair=True)
            report["stage"] = "settle"
            if args.concurrency_barrier:
                args.concurrency_barrier.mkdir(parents=True, exist_ok=True)
                (args.concurrency_barrier / (args.probe_id + ".ready")).write_text(str(os.getpid()), encoding="utf-8")
            return .25
        with bpy.context.temp_override(window=window, area=area, region=region):
            shader_preview.tick(scene, target)
            info = shader_preview.diagnostics(scene, target)
            if info.get("error"):
                raise RuntimeError(info["error"])
            report["ready_frames"] = report["ready_frames"] + 1 if shader_preview.current(scene, target) else 0
            selection.redraw(bpy.context)
            if report["stage"] == "settle" and report["ready_frames"] >= 5 and time.monotonic() - started >= 5:
                if args.concurrency_barrier and len(list(args.concurrency_barrier.glob("*.ready"))) != 2:
                    return .1
                report["capture_started_at"] = time.time()
                if args.helper:
                    from ui_evidence import capture_inspector_pages
                    report["paged_inspector"] = capture_inspector_pages(bpy.context, area, args.output, "scene-mask-inspector")
                    assert report["paged_inspector"]["status"] == "CAPTURED", report["paged_inspector"]
                    report["ui_capture_seconds"] = time.time() - report["capture_started_at"]
                    if args.render_proof:
                        from capture_evidence import render_views
                        render_started = time.monotonic()
                        report["renders"] = render_views(bpy.context, args.output, "concurrency_probe", dict(config, protocol_version=2))
                        report["eevee_capture_seconds"] = time.monotonic() - render_started
                        assert report["renders"]["status"] == "CAPTURED", report["renders"]
                    finish()
                    return None
                bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
                path = args.output / "scene-mask-native-inspector.png"
                bpy.ops.screen.screenshot(filepath=str(path))
                report["screenshot"] = dict(path=str(path), sha256=sha256(path))
                inspector = companion.companion_for(window.screen)
                inspector_region = next(r for r in inspector.regions if r.type == "WINDOW")
                with bpy.context.temp_override(window=window, area=inspector, region=inspector_region):
                    bpy.ops.view2d.scroll_down(page=True)
                report["stage"] = "scrolled"
                report["ready_frames"] = 0
                return .25
            if report["stage"] == "scrolled" and report["ready_frames"] >= 5:
                bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
                path = args.output / "scene-mask-native-inspector-controls.png"
                bpy.ops.screen.screenshot(filepath=str(path))
                report["controls_screenshot"] = dict(path=str(path), sha256=sha256(path))
                finish()
                return None
    except Exception:
        finish(traceback.format_exc())
        return None
    return .25


bpy.app.timers.register(tick, first_interval=1)
