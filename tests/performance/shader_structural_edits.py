"""Structural-edit latency on a heavy catalog recipe: visibility, blend mode, add mask, undo.

Usage: blender --background --factory-startup --python-exit-code 1 \
    --python tests/performance/shader_structural_edits.py -- [--material NAME] [--report PATH]
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
from open_painter.core import image_manager as images, layer_stack as stack, material_manager
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache, shader_preview
from open_painter.smart_materials import composer
from open_painter.utils.ids import new_id


def parse():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--material", default="Post-Apocalyptic Scrap Metal")
    parser.add_argument("--report", type=Path, default=ROOT / ".blender-test" / "shader-structural-edits.json")
    return parser.parse_args(argv)


def settle(scene, ts):
    """Repair topology and finish resources the way the timer would; return ms."""
    started = perf_counter()
    while True:
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert not info["error"], info
        if shader_preview.current(scene, ts):
            return (perf_counter() - started) * 1000, info


def run():
    args = parse()
    open_painter.register()
    if bpy.app.timers.is_registered(cache.tick):
        bpy.app.timers.unregister(cache.tick)
    scene = bpy.context.scene
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_plane_add(size=2)
    ts = sets.create(scene, "Structural edits", 512)
    sets.assign(scene, ts, bpy.context.object)
    n = 32
    u, v = np.meshgrid((np.arange(n, dtype=np.float32) + .5) / n, (np.arange(n, dtype=np.float32) + .5) / n)
    for kind in ("CURVATURE", "AO", "POSITION", "WORLD_NORMAL", "THICKNESS", "UV_BORDER"):
        entry = ts.baked_maps.add()
        entry.kind = kind
        entry.image = images.create("Edits " + kind, n)
        values = np.ones((n, n, 4), np.float32)
        values[..., 0], values[..., 1], values[..., 2] = .1 + .8 * u, .1 + .8 * v, v
        images.write(entry.image, values, pack=False)
    composer.apply(ts, args.material)
    material_manager.repair(ts)
    cold_ms, info = settle(scene, ts)
    report = {"material": args.material, "layers": len(ts.layers), "cold_ms": round(cold_ms, 1),
              "nodes": info.get("shader_nodes"), "units": info.get("units"), "edits": []}
    graph = shader_preview.ensure(scene, ts)
    identities = graph.node_identities()
    layer = next(item for item in ts.layers if item.type != "FOLDER" and item.masks)
    layer_id = layer.id

    def record(label, action):
        started = perf_counter()
        action()
        callback_ms = (perf_counter() - started) * 1000
        settle_ms, info = settle(scene, ts)
        current = shader_preview.ensure(scene, ts)
        entry = {"edit": label, "callback_ms": round(callback_ms, 2), "settle_ms": round(settle_ms, 1),
                 "graph_builds": info["graph_builds"], "rebuilt_units": info.get("rebuilt_units"),
                 "reused_units": info.get("reused_units"), "same_graph": current is graph,
                 "identities_preserved": current.node_identities() == identities}
        report["edits"].append(entry)
        print("STRUCTURAL_EDIT", json.dumps(entry), flush=True)
        return current

    def set_visible(value):
        def action():
            next(item for item in ts.layers if item.id == layer_id).visible = value
        return action

    def set_blend(value):
        def action():
            next(item for item in ts.layers if item.id == layer_id).blend_mode = value
        return action

    def add_mask():
        target = next(item for item in ts.layers if item.id == layer_id)
        mask = target.masks.add()
        mask.id, mask.source, mask.procedural = new_id(), "PROCEDURAL", "CHECKER"
        target.opacity = target.opacity  # publish through the layer callback

    record("hide layer", set_visible(False))
    record("show layer", set_visible(True))
    graph = record("layer blend mode -> MULTIPLY", set_blend("MULTIPLY"))
    identities = graph.node_identities()
    graph = record("layer blend mode -> NORMAL", set_blend("NORMAL"))
    identities = graph.node_identities()
    graph = record("add procedural mask", add_mask)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=1))
    print("STRUCTURAL_EDITS_SUMMARY", json.dumps({k: report[k] for k in ("material", "cold_ms", "nodes", "units")}), flush=True)


run()
