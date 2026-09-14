"""Real-window idle cost of the scheduler timer with one native texture set.

Usage: blender --factory-startup --python tests/performance/idle_timer.py -- [--seconds 5] [--report PATH]
Wraps cache.tick to count invocations and time spent, after the shader settles.
"""
import argparse
import json
import sys
from pathlib import Path
from time import perf_counter

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
import open_painter
from open_painter.core import image_manager as images, material_manager, texture_set_manager as sets
from open_painter.evaluation import cache, shader_preview
from open_painter.smart_materials import composer

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
parser = argparse.ArgumentParser()
parser.add_argument("--seconds", type=float, default=5.0)
parser.add_argument("--material", default="Worn Painted Metal")
parser.add_argument("--report", type=Path, default=ROOT / ".blender-test" / "idle-timer.json")
args = parser.parse_args(argv)

open_painter.register()
scene = bpy.context.scene
ts = sets.create(scene, "Idle timer", 512)
mesh = next((obj for obj in scene.objects if obj.type == "MESH"), None)
if mesh is not None:
    sets.assign(scene, ts, mesh)
n = 32
u, v = np.meshgrid((np.arange(n, dtype=np.float32) + .5) / n, (np.arange(n, dtype=np.float32) + .5) / n)
for kind in ("CURVATURE", "AO", "POSITION", "WORLD_NORMAL", "THICKNESS", "UV_BORDER"):
    entry = ts.baked_maps.add()
    entry.kind = kind
    entry.image = images.create("Idle " + kind, n)
    values = np.ones((n, n, 4), np.float32)
    values[..., 0], values[..., 1], values[..., 2] = .1 + .8 * u, .1 + .8 * v, v
    images.write(entry.image, values, pack=False)
try:
    composer.apply(ts, args.material)
    material_manager.repair(ts)
except Exception as error:
    print("IDLE_TIMER_SETUP_FAILED", error, flush=True)
    bpy.ops.wm.quit_blender()
    raise

stats = {"ticks": 0, "seconds": 0.0, "intervals": {}}
original = cache.tick


def counted():
    started = perf_counter()
    result = original()
    stats["ticks"] += 1
    stats["seconds"] += perf_counter() - started
    stats["intervals"][str(result)] = stats["intervals"].get(str(result), 0) + 1
    return result


def swap():
    if bpy.app.timers.is_registered(cache.tick):
        bpy.app.timers.unregister(cache.tick)
    bpy.app.timers.register(counted, first_interval=0.0, persistent=True)


phase = {"started": None, "deadline": perf_counter() + 60}


def measure():
    if phase["started"] is None:
        if not shader_preview.current(scene, ts):
            if perf_counter() > phase["deadline"]:
                print("IDLE_TIMER_TIMEOUT", json.dumps(shader_preview.diagnostics(scene, ts), default=str)[:800], flush=True)
                bpy.ops.wm.quit_blender()
                return None
            return 0.1
        swap()
        stats.update(ticks=0, seconds=0.0, intervals={})
        phase["started"] = perf_counter()
        return 0.1
    elapsed = perf_counter() - phase["started"]
    if elapsed < args.seconds:
        return 0.1
    report = {"material": args.material, "window_seconds": round(elapsed, 2), "ticks": stats["ticks"],
              "ticks_per_second": round(stats["ticks"] / elapsed, 1),
              "cpu_ms_per_second": round(stats["seconds"] / elapsed * 1000, 2),
              "mean_tick_ms": round(stats["seconds"] / max(stats["ticks"], 1) * 1000, 3),
              "intervals": stats["intervals"], "shader_state": shader_preview.diagnostics(scene, ts)["state"]}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=1))
    print("IDLE_TIMER", json.dumps(report), flush=True)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(measure, first_interval=0.5)
