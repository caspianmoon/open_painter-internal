"""Profile one cold catalog recipe in a fresh Blender process.

OP_SHADER_PROFILE_MATERIAL selects a recipe. No rendering, input generation or
pending boundary stepping occurs. Composition and node construction are measured
separately; counts before/after cleanup reveal leaked graph datablocks.
"""
import cProfile
import io
import json
import os
from pathlib import Path
import pstats
import sys
from time import perf_counter

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import open_painter
from open_painter.core import image_manager as images, texture_set_manager as sets
from open_painter.evaluation import cache, shader_preview
from open_painter.evaluation.shader_graph import Graph
from open_painter.evaluation.shader_math import Math, is_socket
from open_painter.constants import OWNER
from open_painter.smart_materials import composer


def counts(ts):
    return dict(texture_sets=len(bpy.context.scene.open_painter.texture_sets),
                node_groups=len(bpy.data.node_groups), images=len(bpy.data.images),
                material_nodes=len(ts.material.node_tree.nodes))


def measure(label, function):
    profile = cProfile.Profile()
    started = perf_counter()
    profile.enable()
    result = function()
    profile.disable()
    elapsed = perf_counter() - started
    report = dict(wall_s=elapsed)
    for order in ("cumulative", "tottime"):
        stream = io.StringIO()
        pstats.Stats(profile, stream=stream).strip_dirs().sort_stats(order).print_stats(24)
        report[order] = stream.getvalue()
    print("SHADER_COLD_PROFILE", label, round(elapsed, 6), flush=True)
    print(report["cumulative"], flush=True)
    return result, report


def run():
    name = os.environ.get("OP_SHADER_PROFILE_MATERIAL", "Abandoned Machinery")
    open_painter.register()
    if bpy.app.timers.is_registered(cache.tick):
        bpy.app.timers.unregister(cache.tick)
    scene = bpy.context.scene
    ts = sets.create(scene, "Cold shader profile", 512)
    ts.preview_backend = "CPU"
    shader_preview.clear()
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_plane_add(size=2)
    sets.assign(scene, ts, bpy.context.object)
    bpy.context.view_layer.update()
    n = 32
    u, v = np.meshgrid((np.arange(n, dtype=np.float32)+.5)/n,
                       (np.arange(n, dtype=np.float32)+.5)/n)
    for kind in ("CURVATURE", "AO", "POSITION", "WORLD_NORMAL"):
        entry = ts.baked_maps.add()
        entry.kind = kind
        entry.image = images.create("Profile " + kind, n)
        values = np.ones((n, n, 4), np.float32)
        values[..., 0] = .1 + .8*u
        values[..., 1] = .1 + .8*v
        values[..., 2] = v
        images.write(entry.image, values, pack=False)
    report = {"material": name, "blender": bpy.app.version_string,
              "authored_resolution": int(ts.resolution), "before": counts(ts)}
    _, report["composer"] = measure("composer", lambda: composer.apply(ts, name))
    report["after_composer"] = counts(ts)
    if os.environ.get("OP_SHADER_RUNTIME_TIMING"):
        # Use the real backend-switch/property callbacks, without cProfile or
        # monkeypatching. These times measure Python/RNA submission, not frames.
        started = perf_counter()
        ts.preview_backend = "LIVE"
        graph = shader_preview.ensure(scene, ts)
        report["cold_callback_ms"] = (perf_counter()-started)*1000
        report["graph_nodes"] = graph.node_count
        started = perf_counter()
        while graph.pending_resources:
            graph.step_resources(.02)
        report["source_preparation_ms"] = (perf_counter()-started)*1000
        mask = next(mask for layer in ts.layers for mask in layer.masks
                    if mask.enabled and mask.source == "GENERATOR" and mask.white > .5)
        before_nodes = graph.node_identities()
        times = []
        from open_painter.evaluation.metrics import Metrics
        with Metrics() as measured:
            for index in range(121):
                value = .05 + .3 * (index % 17) / 16
                started = perf_counter()
                mask.black = value
                times.append((perf_counter()-started)*1000)
        assert graph.node_identities() == before_nodes
        report["hot_callback"] = dict(parameter=mask.name + ".black", events=len(times),
                                      median_ms=float(np.median(times)), p95_ms=float(np.percentile(times, 95)),
                                      max_ms=max(times), samples_ms=times,
                                      metrics=measured.report(), diagnostics=shader_preview.diagnostics(scene, ts))
        report["timing_scope"] = "Background Python/RNA submission; no render/frame-latency measurement. Other validation processes may contend."
        destination = ROOT / ".blender-test" / ("shader-runtime-" + name.lower().replace(" ", "_") + ".json")
        destination.write_text(json.dumps(report, indent=2))
        print("SHADER_RUNTIME", json.dumps({key:report[key] for key in
              ("cold_callback_ms", "source_preparation_ms", "graph_nodes", "timing_scope")}), flush=True)
        print("SHADER_RUNTIME_HOT", json.dumps({key:value for key,value in report["hot_callback"].items()
              if key not in {"samples_ms", "diagnostics"}}), flush=True)
        return
    node_cost = dict(new_s=0., ownership_s=0., calls=0)
    original_node = Math.node
    unique_names = bool(os.environ.get("OP_SHADER_UNIQUE_NAMES"))

    def timed_node(self, kind):
        started = perf_counter()
        node = self._target().nodes.new(kind)
        middle = perf_counter()
        if unique_names:
            node.name = f"OP_{len(self.tree.nodes):06d}"
        node[OWNER] = True
        finished = perf_counter()
        node_cost["new_s"] += middle-started
        node_cost["ownership_s"] += finished-middle
        node_cost["calls"] += 1
        return node

    Math.node = timed_node
    original_link = Math.link
    pending_links = []
    defer_links = bool(os.environ.get("OP_SHADER_DEFER_LINKS"))
    if defer_links:
        def deferred_link(self, value, target):
            if is_socket(value):
                pending_links.append((self.tree, value, target))
                return target
            return original_link(self, value, target)
        Math.link = deferred_link
    graph = Graph(scene, ts, ts.material)
    try:
        _, report["build"] = measure("build", graph.build)
        if defer_links:
            def flush():
                for tree, source, target in pending_links:
                    tree.links.new(source, target, verify_limits=False)
            _, report["flush_links"] = measure("flush_links", flush)
            report["deferred_links"] = len(pending_links)
    finally:
        Math.node = original_node
        Math.link = original_link
    report["node_cost"] = node_cost
    print("SHADER_COLD_NODE_COST", json.dumps(node_cost), flush=True)
    report["graph_nodes"] = graph.node_count
    report["resource_boundaries"] = len(graph.resource_bindings)
    report["after_build"] = counts(ts)
    _, report["close"] = measure("close", graph.close)
    report["after_close"] = counts(ts)
    destination = ROOT / ".blender-test" / ("shader-profile-" + name.lower().replace(" ", "_")
                  + ("-deferred" if defer_links else "") + ("-unique-names" if unique_names else "") + ".json")
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(report, indent=2))
    print("SHADER_COLD_COUNTS", json.dumps({key: report[key] for key in
          ("before", "after_composer", "after_build", "after_close", "graph_nodes", "resource_boundaries")}), flush=True)


run()
