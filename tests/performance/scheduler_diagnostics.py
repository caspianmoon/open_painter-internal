"""CPU cost of cached-layer diagnostic/status polling; no GPU evaluation.

Run with --background --factory-startup --python this_file, then optional
-- --layers 100 --samples 40 --report PATH. The fixture uses real Blender RNA
and Graph.cache_states, with one shared image for metadata inspection only.
It does not measure compositing, per-layer image memory, or viewport FPS.
"""
import argparse
import json
from pathlib import Path
import statistics
import sys
from time import perf_counter
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache, dependencies, shader_preview
from open_painter.evaluation.shader_graph import Graph
from open_painter.ui import status
from open_painter.utils.ids import new_id

parser = argparse.ArgumentParser()
parser.add_argument('--layers', type=int, default=100)
parser.add_argument('--samples', type=int, default=40)
parser.add_argument('--report', type=Path, default=ROOT / '.blender-test' / 'scheduler-diagnostics.json')
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
if args.layers < 1 or args.samples < 1:
    parser.error('--layers and --samples must be positive')

open_painter.register()
scene = bpy.context.scene
ts = sets.create(scene, 'Cached-layer idle CPU fixture', 512)
bindings = []
shared_image = bpy.data.images.new('Shared diagnostic cache pixels', width=512, height=512, float_buffer=True)
for index in range(args.layers):
    layer = ts.layers.add()
    layer.id, layer.name = new_id(), f'Cached layer {index}'
    for channel in ('BASE_COLOR', 'ROUGHNESS', 'METALLIC', 'NORMAL'):
        row = layer.cache.add()
        row.channel, row.image = channel, shared_image
        bindings.append(SimpleNamespace(layer_id=layer.id, channel=channel, stale=False))
graph = Graph(scene, ts, None)
graph.cache_bindings = bindings
shader_preview._sessions[cache.key_for(scene, ts)] = dict(
    graph=graph, attached=True, serial=ts.edit_serial, image_epoch=dependencies.image_epoch(), dirty=False)


def measure(callback):
    callback()  # warm imports and bookkeeping before recording
    times = []
    for _ in range(args.samples):
        started = perf_counter()
        callback()
        times.append((perf_counter() - started) * 1000)
    return dict(median_ms=statistics.median(times), max_ms=max(times), samples=len(times))


result = dict(blender=bpy.app.version_string, layers=args.layers, cached_channels=len(bindings),
              full_diagnostics=measure(lambda: shader_preview.diagnostics(scene, ts)),
              status=measure(lambda: shader_preview.status(scene, ts)),
              ui_status=measure(lambda: status.of(bpy.context, ts)),
              scheduler_idle=measure(cache.tick))
args.report.parent.mkdir(parents=True, exist_ok=True)
args.report.write_text(json.dumps(result, indent=2))
print(json.dumps(result), flush=True)
shader_preview._sessions.clear()
open_painter.unregister()
