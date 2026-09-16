"""Layer performance (0.9.0) benchmark: stroke-time cost with the freeze gate on and off,
and the compile/settle/refresh cost of a heavy folder live versus cached.

Usage: blender --background --factory-startup --python-exit-code 1 \
    --python tests/performance/layer_performance.py -- [--resolution 2048] [--inputs 60] [--report PATH]

Strokes are emulated (pixel writes + the scheduler tick with the brush check
patched), so the figures describe the add-on's per-tick work, not a viewport frame.
"""
import argparse
import json
import sys
from pathlib import Path
from time import monotonic, perf_counter
from unittest.mock import patch

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import open_painter
from open_painter.core import image_manager as images, layer_stack as stack, material_manager
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache, layer_cache, metrics, profile, shader_preview
from open_painter.painting import paint_session
from open_painter.painting.channel_paint import target as paint_target
from open_painter.utils.ids import new_id


def parse():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--resolution", type=int, default=2048)
    parser.add_argument("--inputs", type=int, default=60)
    parser.add_argument("--report", type=Path, default=ROOT / ".blender-test" / "layer-performance.json")
    return parser.parse_args(argv)


def settle(scene, ts):
    started = perf_counter()
    while True:
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert not info["error"], info
        if shader_preview.current(scene, ts):
            return (perf_counter() - started) * 1000, info


def percentiles(values):
    values = sorted(values)
    if not values:
        return {}
    pick = lambda q: values[min(len(values) - 1, int(len(values) * q))]
    return {"p50_ms": round(pick(.5), 3), "p95_ms": round(pick(.95), 3), "max_ms": round(values[-1], 3),
            "count": len(values)}


def run():
    args = parse()
    open_painter.register()
    if bpy.app.timers.is_registered(cache.tick):
        bpy.app.timers.unregister(cache.tick)
    scene = bpy.context.scene
    project = scene.open_painter
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_plane_add(size=2)
    ts = sets.create(scene, "Layer performance", args.resolution)
    sets.assign(scene, ts, bpy.context.object)
    report = {"blender": bpy.app.version_string, "resolution": args.resolution, "inputs": args.inputs}

    # Twelve fills; two carry spatial blurs (CPU boundaries) inside a folder;
    # a painted layer with a blur on top is the stroke target.
    folder = stack.add(ts, "FOLDER", "Heavy folder")
    folder_id = folder.id
    rng = np.random.default_rng(3)
    for index in range(12):
        fill = stack.add(ts, "FILL", f"Fill {index}")
        fill.parent_id = folder_id
        stack.channel_for(fill, "BASE_COLOR").color = (*rng.random(3), .8)
        stack.channel_for(fill, "ROUGHNESS").value = float(rng.random())
        mask = fill.masks.add()
        mask.id, mask.source, mask.procedural, mask.noise_scale = new_id(), "PROCEDURAL", "CHECKER", 2. + index
        if index in {3, 8}:
            blur = stack.channel_for(fill, "BASE_COLOR").effects.add()
            blur.kind, blur.radius = "GAUSSIAN_BLUR", 3.
    painted = stack.add(ts, "PAINT", "Painted roughness")
    painted_id = painted.id
    image = paint_target(ts, next(l for l in ts.layers if l.id == painted_id), "ROUGHNESS")
    blur = stack.channel_for(next(l for l in ts.layers if l.id == painted_id), "ROUGHNESS").effects.add()
    blur.kind, blur.radius = "GAUSSIAN_BLUR", 2.
    material_manager.repair(ts)
    live_settle_ms, info = settle(scene, ts)
    shader_preview.flush(scene, ts)
    report["layers"] = len(ts.layers)
    report["live"] = {"build_ms": round(info.get("build_ms", 0), 1), "settle_ms": round(live_settle_ms, 1),
                      "nodes": info.get("shader_nodes"), "units": info.get("units")}

    # ---- stroke: gate on / off --------------------------------------------
    project.paint_image = image
    project.paint_set_id, project.paint_layer_id = ts.id, painted_id
    project.paint_channel = project.paint_target_channel = "ROUGHNESS"
    paint_session.reset()
    paint_session.detect(scene, force=True)
    pointer = scene.as_pointer()
    n = image.size[0]
    scratch = images.pixels(image)

    def stroke(gate):
        project.freeze_while_painting = gate
        ticks = []
        with metrics.Metrics() as observed, patch.object(cache, "_modal_painting", lambda s: True):
            for step in range(args.inputs):
                y, x = (step * 23) % (n - 64), (step * 41) % (n - 64)
                scratch[y:y + 64, x:x + 64, :3] = (step % 7) / 7
                image.pixels.foreach_set(scratch.ravel())
                image.update()
                cache._live_checks.pop(pointer, None)
                started = perf_counter()
                cache.tick()
                ticks.append((perf_counter() - started) * 1000)
        counters = observed.report()["counters"]
        cache._paint_input[pointer] = (True, monotonic() - .2)
        cache._live_checks.pop(pointer, None)
        started = perf_counter()
        cache.tick()
        release_ms, _ = settle(scene, ts)
        release_ms += (perf_counter() - started) * 1000
        return {**percentiles(ticks), "resource_requests": counters.get("shader_resource_requests", 0),
                "deferrals": counters.get("shader_paint_deferrals", 0), "catch_up_ms": round(release_ms, 1)}

    report["stroke"] = {"gate_on": stroke(True), "gate_off": stroke(False)}
    project.freeze_while_painting = True
    project.paint_image = None
    project.paint_set_id = project.paint_layer_id = project.paint_target_channel = ""
    cache._paint_input.pop(pointer, None)

    # ---- heavy folder: live versus cached ---------------------------------
    next(l for l in ts.layers if l.id == folder_id).cache_mode = "CACHED"
    started = perf_counter()
    cached_settle_ms, info = settle(scene, ts)
    refresh_started = perf_counter()
    layer_cache.flush(scene, ts)
    refresh_ms = (perf_counter() - refresh_started) * 1000
    settle(scene, ts)
    report["cached_folder"] = {"build_ms": round(info.get("build_ms", 0), 1), "settle_ms": round(cached_settle_ms, 1),
                               "refresh_ms": round(refresh_ms, 1), "nodes": info.get("shader_nodes"),
                               "units": info.get("units")}
    # A slider inside the cached folder: the shader binds nothing new until the
    # debounced refresh; measure the callback alone.
    fill = next(l for l in ts.layers if l.name == "Fill 3")
    started = perf_counter()
    fill.opacity = .5
    report["cached_folder"]["inner_edit_callback_ms"] = round((perf_counter() - started) * 1000, 2)
    layer_cache.flush(scene, ts)
    settle(scene, ts)

    # ---- bypass and profile ------------------------------------------------
    next(l for l in ts.layers if l.id == folder_id).cache_mode = "LIVE"
    settle(scene, ts)
    started = perf_counter()
    next(l for l in ts.layers if l.id == folder_id).bypass = True
    bypass_settle_ms, info = settle(scene, ts)
    report["bypassed_folder"] = {"settle_ms": round(bypass_settle_ms, 1), "nodes": info.get("shader_nodes"),
                                 "units": info.get("units")}
    next(l for l in ts.layers if l.id == folder_id).bypass = False
    settle(scene, ts)
    started = perf_counter()
    rows = profile.run(scene, ts, 512)
    report["profile"] = {"pass_ms": round((perf_counter() - started) * 1000, 1),
                         "total_ms": round(profile.total(scene, ts), 1),
                         "heaviest": sorted(({"name": r["name"], "cost_ms": round(r["cost_ms"], 1)} for r in rows),
                                            key=lambda r: -r["cost_ms"])[:5]}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print("LAYER_PERFORMANCE", json.dumps(report), flush=True)
    open_painter.unregister()


run()
