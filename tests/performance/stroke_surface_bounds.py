"""CPU work per GPU dab on a dense planar mesh; no GPU context is used.

Blender --background --factory-startup --python this_file -- --samples 25
The immutable surface raster/texture setup is outside the measured dab path.
"""
import argparse
import json
from pathlib import Path
import statistics
import sys
from time import perf_counter

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from open_painter.painting.gpu_strokes import Surface
from open_painter.geometry.triangle_bounds import TriangleBounds

parser = argparse.ArgumentParser()
parser.add_argument('--samples', type=int, default=25)
parser.add_argument('--report', type=Path, default=ROOT / '.blender-test/stroke-surface-bounds.json')
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
if args.samples < 1:
    parser.error('--samples must be positive')

nx, ny = 500, 200
x, y = np.meshgrid(np.linspace(0, 1, nx + 1, dtype=np.float32),
                   np.linspace(0, 1, ny + 1, dtype=np.float32))
positions = np.stack((x, y, np.zeros_like(x)), axis=-1).reshape(-1, 3)
lower = (np.arange(ny)[:, None] * (nx + 1) + np.arange(nx)).ravel()
triangles = np.stack((np.stack((lower, lower + 1, lower + nx + 2), axis=1),
                      np.stack((lower, lower + nx + 2, lower + nx + 1), axis=1)), axis=1).reshape(-1, 3)
entry = dict(positions=positions, tri_vertices=triangles, tri_loops=triangles, uv=positions[:, :2],
             all_tri_vertices=triangles, all_tri_polygons=np.repeat(np.arange(nx * ny), 2),
             tri_indices=np.arange(len(triangles)), mirrored=False)
# Construct just the CPU portion of the immutable surface; texture uploads and
# the one-time UV raster are deliberately excluded from interactive CPU timing.
surface = Surface.__new__(Surface)
surface.entries, surface.n, surface.count = [entry], 2048, len(triangles)
started = perf_counter()
corners = positions[triangles]
surface.triangle_bounds = [(corners.min(axis=1), corners.max(axis=1))]
surface.triangle_indexes = [TriangleBounds(*surface.triangle_bounds[0])]
surface.sorted_triangle_indices = [True]
del corners
report = dict(triangles=surface.count, resolution=surface.n, bounds_bytes=surface.bounds_bytes,
              bounds_setup_ms=(perf_counter() - started) * 1000)
sample = np.array([.5, .5, 0, 0, 0, 1, 1, .03, 0, 0, 1, 0, 0], np.float32)
anchor = np.array([.5, .5, 0, (ny // 2) * nx + nx // 2], np.float32)
for mode in ('DEPTH', 'SURFACE'):
    settings = dict(reach=mode, depth_limit=1.0, planar=None)
    surface.eligible(sample, settings, anchor)  # warm immutable reach topology
    times = []
    for _ in range(args.samples):
        started = perf_counter()
        allowed, rect = surface.eligible(sample, settings, anchor)
        times.append((perf_counter() - started) * 1000)
    report[mode] = dict(median_ms=statistics.median(times), max_ms=max(times), samples=len(times),
                        rect=rect, eligible_triangles=int(allowed.sum()))
args.report.parent.mkdir(parents=True, exist_ok=True)
args.report.write_text(json.dumps(report, indent=2))
print(json.dumps(report), flush=True)
