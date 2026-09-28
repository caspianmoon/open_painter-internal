"""CPU costs that can interrupt viewport interaction, with an isolated scene.

Run with --background --factory-startup --python-exit-code 1 --python this_file.
Arguments after --: --layers 100 --samples 20 --profile --report PATH.
No GPU context or author preferences are required; timings are not viewport FPS.
"""
import argparse
import cProfile
from contextlib import nullcontext
import io
import json
from pathlib import Path
import pstats
import sys
from time import perf_counter

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import layer_stack as stack, texture_set_manager as sets, image_manager as images
from open_painter.evaluation import cache
from open_painter.graph import mirror
from open_painter.ui.dock import lifecycle

parser = argparse.ArgumentParser()
parser.add_argument('--layers', type=int, default=100)
parser.add_argument('--samples', type=int, default=20)
parser.add_argument('--profile', action='store_true')
parser.add_argument('--report', type=Path, default=ROOT / '.blender-test' / 'viewport-interaction.json')
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
if args.layers < 2 or args.samples < 1:
    parser.error('--layers must be at least 2 and --samples must be positive')

open_painter.register()
scene = bpy.context.scene
ts = sets.create(scene, 'CPU idle profile', 512)
ts.preview_backend = 'CPU'
for index in range(args.layers):
    layer = stack.add(ts, 'FILL', f'Layer {index}')
    for _ in range(2):
        mask = layer.masks.add()
        mask.source, mask.procedural = 'PROCEDURAL', 'CHECKER'
ts.dirty = False
tree = mirror.ensure_tree(ts)
area = bpy.context.screen.areas[0]
area.type = 'NODE_EDITOR'
area.spaces.active.tree_type = 'OpenPainterGraph'
area.spaces.active.node_tree = tree
report = {'blender': bpy.app.version_string, 'layers': args.layers, 'masks_per_layer': 2,
          'profile_enabled': args.profile}


def measure(name, callback, count=None):
    times = []
    profile = cProfile.Profile() if args.profile else None
    with profile if profile is not None else nullcontext():
        for _ in range(count or args.samples):
            started = perf_counter()
            callback()
            times.append((perf_counter() - started) * 1000)
    result = dict(median_ms=float(np.median(times)), max_ms=max(times), count=len(times))
    if profile is not None:
        stream = io.StringIO()
        pstats.Stats(profile, stream=stream).sort_stats('cumtime').print_stats(18)
        result['profile'] = stream.getvalue()
    report[name] = result
    print(name, json.dumps({k: v for k, v in result.items() if k != 'profile'}), flush=True)


# Separate cold module loading from steady-state idle work.
cache.tick()
lifecycle.tick()
measure('scheduler_idle', cache.tick)
measure('graph_ui_idle', lifecycle.tick)
measure('graph_signature', lambda: mirror.signature(ts))


class NoopLayout:
    def __getattr__(self, name):
        return lambda *args, **kwargs: self


# Exercise the actual Python node callbacks and live RNA. Native UI layout and
# rendering costs are excluded, so this is callback CPU time rather than FPS.
nodes = [node for node in tree.nodes if hasattr(node, 'op_kind')]
layout = NoopLayout()
report['graph_draw_nodes'] = len(nodes)
report['graph_draw_note'] = 'Actual Python draw callbacks and RNA; no native UI layout or GPU rendering.'


def draw_nodes():
    for node in nodes:
        mirror.draw_node(node, bpy.context, layout)


draw_nodes()  # warm owner lookup before timing
measure('graph_draw_callbacks', draw_nodes)

for resolution in (2048, 4096):
    image = bpy.data.images.new(f'Canvas {resolution}', width=resolution, height=resolution, float_buffer=True)
    scratch = images.pixels(image)
    previous = scratch.copy()
    image.pixels.foreach_set(scratch.ravel())
    image.update()

    def scan():
        # The idle native-paint fallback reads the full canvas and compares
        # bounded row strips, even when the previous stroke is already caught.
        raster = images.pixels(image, scratch)
        changed = np.zeros(resolution, dtype=bool)
        for start in range(0, resolution, 256):
            stop = start + 256
            changed[start:stop] = np.any(raster[start:stop] != previous[start:stop], axis=(1, 2))
        return np.flatnonzero(changed)

    measure(f'idle_canvas_scan_{resolution}', scan, min(args.samples, 6))
    bpy.data.images.remove(image)

args.report.parent.mkdir(parents=True, exist_ok=True)
args.report.write_text(json.dumps(report, indent=2))
open_painter.unregister()
