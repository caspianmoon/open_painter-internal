"""Measure cold texture generation and a cached roughness edit in Blender.

Run in an isolated process (this script creates test scene data)::

    blender --background --factory-startup --python-exit-code 1 \
      --python scripts/benchmark_texture_material.py -- --resolution 2048

The fixture uses synthetic AO/curvature textures to measure composition rather
than mesh baking or viewport rendering. It evaluates the complete requested
resolution, with no downsampling. Wall time includes other host activity; CPU
time helps distinguish that from computation. This is not an interactive FPS
benchmark. The default report is .blender-test/texture-material-benchmark.json.
"""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
import time

try:
    import resource
except ImportError:  # Windows does not expose POSIX process resource counters.
    resource = None


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import bpy
import numpy as np

import open_painter
from open_painter.core import image_manager, layer_stack, texture_set_manager
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.metrics import Metrics
from open_painter.smart_materials import composer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resolution", type=int, default=2048,
                        choices=(512, 1024, 2048, 4096, 8192))
    parser.add_argument("--report", type=Path,
                        default=ROOT / ".blender-test/texture-material-benchmark.json")
    parser.add_argument("--legacy-detail", action="store_true",
                        help="Compare preserved procedural recipe without generated image detail")
    parser.add_argument("--recipes", type=Path,
                        help="Saved complete catalog snapshot for a reproducible previous-release comparison")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    open_painter.register()
    try:
        scene = bpy.context.scene
        ts = texture_set_manager.create(scene, "Texture benchmark", args.resolution)
        x, y = np.meshgrid((np.arange(32) + .5) / 32, (np.arange(32) + .5) / 32)
        for kind in ("AO", "CURVATURE"):
            mesh_map = ts.baked_maps.add()
            mesh_map.kind = kind
            mesh_map.image = image_manager.create("Benchmark " + kind, 32)
            data = np.ones((32, 32, 4), np.float32)
            field = .3 + .7 * y if kind == "AO" else np.clip((.15 - abs(x - .5)) * 8, 0, 1)
            data[..., :3] = field[..., None]
            image_manager.write(mesh_map.image, data)

        started = time.perf_counter()
        recipe = deepcopy(composer.CATALOG["Worn Painted Metal"])
        if args.recipes:
            recipe = json.loads(args.recipes.read_text(encoding="utf-8"))["Worn Painted Metal"]
        if args.legacy_detail:
            recipe = json.loads((ROOT / 'tests/performance/worn_v020.json').read_text(encoding="utf-8"))
        composer.apply_document(ts, recipe, "Worn Painted Metal")
        author_seconds = time.perf_counter() - started
        evaluator = LayerEvaluator(scene)
        initial_started = time.perf_counter()
        initial_cpu_started = time.process_time()
        channel_seconds, channel_cpu_seconds = {}, {}
        max_step, max_cpu_step = 0.0, 0.0
        for output in ts.outputs:
            started, cpu_started = time.perf_counter(), time.process_time()
            job = evaluator.channel_steps(ts.id, output.channel)
            while True:
                step, cpu_step = time.perf_counter(), time.process_time()
                done = False
                try:
                    next(job)
                except StopIteration:
                    done = True
                max_step = max(max_step, time.perf_counter() - step)
                max_cpu_step = max(max_cpu_step, time.process_time() - cpu_step)
                if done:
                    break
            channel_seconds[output.channel] = time.perf_counter() - started
            channel_cpu_seconds[output.channel] = time.process_time() - cpu_started
            print(output.channel, channel_seconds[output.channel], flush=True)
        initial_seconds = time.perf_counter() - initial_started
        initial_cpu_seconds = time.process_time() - initial_cpu_started
        cache_bytes = sum(value.nbytes for value in evaluator.geometry_cache.values()
                          if isinstance(value, np.ndarray))
        # Real UI edits start another evaluator job; the bounded shared cache must
        # survive destruction of this particular evaluator instance.
        del evaluator
        for output in ts.outputs:
            output.dirty = False
        coat = next(layer for layer in ts.layers if layer.name == "Blue paint coat")
        layer_stack.channel_for(coat, "ROUGHNESS").value += .03
        started, cpu_started = time.perf_counter(), time.process_time()
        with Metrics() as edit_metrics:
            LayerEvaluator(scene).evaluate_texture_set(ts.id)
        edit_seconds = time.perf_counter() - started
        edit_cpu_seconds = time.process_time() - cpu_started
        # Darwin reports bytes; Linux reports KiB.
        rss_scale = 2**20 if sys.platform == "darwin" else 2**10
        report = dict(
            blender_version=bpy.app.version_string,
            blender_build_hash=bpy.app.build_hash.decode(),
            detail_variant="preserved_procedural" if args.legacy_detail else "generated_scalar_images",
            recipe=recipe,
            resolution=args.resolution, layers=len(ts.layers), material="Worn Painted Metal",
            author_seconds=author_seconds, initial_seconds=initial_seconds,
            initial_cpu_seconds=initial_cpu_seconds, channel_seconds=channel_seconds,
            channel_cpu_seconds=channel_cpu_seconds, roughness_edit_seconds=edit_seconds,
            roughness_edit_cpu_seconds=edit_cpu_seconds,
            roughness_edit_metrics=edit_metrics.report(),
            max_cooperative_step_cpu_ms=max_cpu_step * 1000,
            max_cooperative_step_ms=max_step * 1000, scalar_cache_mib=cache_bytes / 2**20,
            max_rss_mib=(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / rss_scale
                         if resource else None),
            context="CPU texture composition with synthetic AO/curvature; excludes mesh baking and viewport rendering.",
        )
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)
        print("Report:", args.report, flush=True)
    finally:
        open_painter.unregister()


if __name__ == "__main__":
    main()
