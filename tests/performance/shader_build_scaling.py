"""Cold shader-graph construction cost for catalog recipes in one Blender process.

Usage:
  blender --background --factory-startup --python-exit-code 1 \
      --python tests/performance/shader_build_scaling.py -- [--all] [--report PATH] \
      [--materials "Worn Painted Metal" "Post-Apocalyptic Scrap Metal"] [--threshold-ms N]

Each recipe is composed into a fresh 512 texture set with synthetic mesh maps,
then compiled with Graph.build(). The report records wall time, node and tree
counts and unique images; it never renders or steps source boundaries.
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
from open_painter.core import image_manager as images, texture_set_manager as sets
from open_painter.evaluation import cache, shader_preview
from open_painter.evaluation.shader_graph import Graph
from open_painter.smart_materials import composer
from open_painter.smart_materials.catalog import CATALOG

DEFAULT = ("Worn Painted Metal", "Post-Apocalyptic Scrap Metal")


def parse():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--materials", nargs="*", default=list(DEFAULT))
    parser.add_argument("--report", type=Path, default=ROOT / ".blender-test" / "shader-build-scaling.json")
    parser.add_argument("--threshold-ms", type=float, default=0.0, help="Fail when any build exceeds this")
    return parser.parse_args(argv)


def synthetic_maps(ts, n=32):
    u, v = np.meshgrid((np.arange(n, dtype=np.float32) + .5) / n, (np.arange(n, dtype=np.float32) + .5) / n)
    for kind in ("CURVATURE", "AO", "POSITION", "WORLD_NORMAL", "THICKNESS", "UV_BORDER"):
        entry = ts.baked_maps.add()
        entry.kind = kind
        entry.image = images.create("Scaling " + kind, n)
        values = np.ones((n, n, 4), np.float32)
        values[..., 0] = .1 + .8 * u
        values[..., 1] = .1 + .8 * v
        values[..., 2] = v
        images.write(entry.image, values, pack=False)


def graph_stats(graph):
    trees = list(graph.trees()) if hasattr(graph, "trees") else [graph.tree]
    nodes = sum(len(tree.nodes) for tree in trees)
    links = sum(len(tree.links) for tree in trees)
    unique = {node.image.as_pointer() for tree in trees for node in tree.nodes
              if node.bl_idname == "ShaderNodeTexImage" and node.image}
    largest = max((len(tree.nodes) for tree in trees), default=0)
    return dict(nodes=nodes, links=links, trees=len(trees), largest_tree=largest, unique_images=len(unique))


def measure(name, plane):
    scene = bpy.context.scene
    ts = sets.create(scene, "Scaling " + name, 512)
    ts.preview_backend = "CPU"
    shader_preview.clear()
    sets.assign(scene, ts, plane)
    synthetic_maps(ts)
    started = perf_counter()
    composer.apply(ts, name)
    compose_ms = (perf_counter() - started) * 1000
    graph = Graph(scene, ts, ts.material)
    started = perf_counter()
    graph.build()
    build_ms = (perf_counter() - started) * 1000
    stats = graph_stats(graph)
    extra = {key: graph._last.get(key) for key in ("units", "largest_unit", "max_textures_frag")
             if key in getattr(graph, "_last", {})}
    boundaries = len(graph.resource_bindings)
    graph.close()
    result = dict(name=name, layers=len(ts.layers), compose_ms=round(compose_ms, 1),
                  build_ms=round(build_ms, 1), resource_boundaries=boundaries, **stats, **extra)
    print("SHADER_BUILD", json.dumps(result), flush=True)
    return result


def run():
    args = parse()
    open_painter.register()
    if bpy.app.timers.is_registered(cache.tick):
        bpy.app.timers.unregister(cache.tick)
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_plane_add(size=2)
    plane = bpy.context.object
    names = list(CATALOG) if args.all else args.materials
    results = []
    for name in names:
        scene = bpy.context.scene
        ts_count = len(scene.open_painter.texture_sets)
        results.append(measure(name, plane))
        # Keep the scene small: remove the set we just measured.
        while len(scene.open_painter.texture_sets) > ts_count:
            sets.remove(scene, len(scene.open_painter.texture_sets) - 1)
    report = dict(blender=bpy.app.version_string, build=bpy.app.build_hash.decode() if isinstance(bpy.app.build_hash, bytes) else str(bpy.app.build_hash),
                  note="Construction wall time of Graph.build() only; no GPU compile, no boundary stepping.",
                  materials=results)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=1))
    worst = max(results, key=lambda r: r["build_ms"])
    print("SHADER_BUILD_SUMMARY", json.dumps(dict(count=len(results), worst=worst["name"], worst_ms=worst["build_ms"],
          total_ms=round(sum(r["build_ms"] for r in results), 1))), flush=True)
    if args.threshold_ms and worst["build_ms"] > args.threshold_ms:
        raise SystemExit(f"{worst['name']} built in {worst['build_ms']} ms > {args.threshold_ms} ms")


run()
