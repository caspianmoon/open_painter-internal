"""CPU culling stays exact across surface reach, geometry snapshots and releases.

Only texture allocation is replaced: constructor rasterization, reach selection,
UV clipping and retained/active resource accounting run their actual code.
"""
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
from weakref import WeakSet

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tests/unit'))
from test_surface_reach import grid, sample, anchor, target_of
from open_painter.painting import gpu_sources, gpu_strokes, stroke_raster as raster
from open_painter.geometry.triangle_bounds import TriangleBounds
from open_painter.painting.surface_reach import Sheets


class CPUDevice:
    def __init__(self):
        self._leases = {}

    def upload(self, array):
        texture = object()
        self._leases[id(texture)] = array.nbytes
        return texture

    def discard(self, texture):
        self._leases.pop(id(texture))


def entries_for(dtype, grid_size=4):
    entries = []
    for index, z in enumerate((0, .02)):
        entry = target_of([grid(grid_size, grid_size, z=z)])
        entry['positions'] = entry['positions'].astype(dtype)
        triangles = entry['all_tri_vertices']
        entry.update(tri_vertices=triangles, tri_loops=triangles,
                     tri_indices=np.arange(len(triangles)),
                     world_normals=np.tile(np.array([0, 0, 1], dtype), (len(entry['positions']), 1)),
                     uv=entry['positions'][:, :2] * .4 + np.array([.05 + .5 * index, .05]))
        entries.append(entry)
    return entries


def exhaustive(surface, row, settings, hit):
    """Uncached triangle-by-triangle reference, including UV margin/clipping."""
    pseudo = SimpleNamespace(reach=settings['reach'], depth_limit=settings['depth_limit'], anchors='')
    sheets = raster.reach_for(surface.entries, pseudo, row[None], settings['planar'],
                              None if hit is None else hit[None])
    lo, hi = raster.bounds(row[None])
    allowed, windows = [], []
    for entry, sheet in zip(surface.entries, sheets):
        count = len(entry['tri_vertices'])
        indices = np.arange(count)
        test = raster.eligibility(sheet, entry, indices)
        eligible = np.ones(count, bool) if test is None else test(indices, 0, 1)[:, 0]
        allowed.extend(eligible)
        for index in np.flatnonzero(eligible):
            corners = entry['positions'][entry['tri_vertices'][index]]
            if np.all(corners.max(axis=0) >= lo) and np.all(corners.min(axis=0) <= hi):
                uv = entry['uv'][entry['tri_loops'][index]]
                windows.extend(raster.uv_window(corners[None], uv[None], lo, hi, margin=2 / surface.n))
    width = min(1024, max(1, surface.count))
    values = np.zeros(((max(1, surface.count) + width - 1) // width, width), np.float32)
    values.ravel()[:len(allowed)] = allowed
    rect = None
    if windows:
        windows = np.asarray(windows)
        low = np.clip(np.floor(windows[:, 0].min(axis=0) * surface.n).astype(int), 0, surface.n)
        high = np.clip(np.ceil(windows[:, 1].max(axis=0) * surface.n).astype(int), 0, surface.n)
        if np.all(high > low):
            rect = tuple(map(int, (*low, *(high - low))))
    return values, rect


device = CPUDevice()
with patch.object(gpu_strokes.runtime, 'device', return_value=device):
    for dtype in (np.float32, np.float64):
        surface = gpu_strokes.Surface(entries_for(dtype), 64)
        expected_bytes = surface.count * 2 * 3 * np.dtype(dtype).itemsize
        assert surface.bounds_bytes == expected_bytes
        for mode in ('DEPTH', 'SURFACE'):
            for centre, radius, planar, hit in (
                    ((.49, .52, 0), .2, None, anchor((.49, .52, 0), 9)),
                    ((.49, .52, .02), .2, None, anchor((.49, .52, .02), 9)),
                    ((0, 0, 0), .04, None, anchor((0, 0, 0), 0)),
                    ((1, 1, 0), .04, (0, 0, 1), None),
                    ((3, 3, 3), .01, None, None),
                    ((.5, .5, 0), 2., None, None)):
                row = sample(centre, radius=radius)
                settings = dict(reach=mode, depth_limit=.5, planar=planar)
                wanted, wanted_rect = exhaustive(surface, row, settings, hit)
                actual, actual_rect = surface.eligible(row, settings, hit)
                np.testing.assert_array_equal(actual, wanted)
                assert actual_rect == wanted_rect, (dtype, mode, centre, actual_rect, wanted_rect)
                if centre == (.49, .52, 0) and mode == 'SURFACE':
                    split = len(surface.entries[0]['tri_vertices'])
                    assert actual.ravel()[:split].any()
                    assert not actual.ravel()[split:surface.count].any(), 'rear sheet leaked into the dab'

        # A replaced geometry snapshot gets different bounds at the same size;
        # there is no object-identity/global memo that survives geometry changes.
        changed = entries_for(dtype)
        for entry in changed:
            entry['positions'][:, 0] += 3
        replacement = gpu_strokes.Surface(changed, 64)
        row = sample((.49, .52, 0), radius=.2)
        settings = dict(reach='DEPTH', depth_limit=1., planar=None)
        assert surface.eligible(row, settings, None)[1] is not None
        assert replacement.eligible(row, settings, None)[1] is None
        replacement.close()

        # A retained surface is shared across stores/canvases. Its CPU arrays
        # are counted once and survive until the final borrowing canvas closes.
        store = SimpleNamespace(surface=surface, output=None)
        retained = {(1, 'fixture', 'a'): store, (1, 'fixture', 'b'): store}
        previews = {(1, 'fixture', 64, 'geometry'): surface}
        canvases = WeakSet()
        first, second = gpu_strokes.Canvas.__new__(gpu_strokes.Canvas), gpu_strokes.Canvas.__new__(gpu_strokes.Canvas)
        for canvas in (first, second):
            canvas.surface, canvas.set_id, canvas.closed = surface, 'fixture', False
            canvas.owns_surface = False
            canvas.bases, canvas.outputs, canvas.borrowed = {}, {}, set()
            canvas.coverage = canvas.scratch = canvas.alpha = canvas.depth_eligible = None
            canvas.n = 64
            canvases.add(canvas)
        surface.pins = 2
        with patch.object(gpu_sources, '_stores', retained), patch.object(gpu_sources, '_preview_surfaces', previews), \
                patch.object(gpu_strokes, '_canvases', canvases):
            assert gpu_sources.retained_bytes('fixture') == 64 * 64 * 32 + expected_bytes
            assert gpu_strokes.active_bytes('fixture') == 0
            retained.clear()
            previews.clear()
            surface.close()
            assert surface.pending_close and surface.bounds_bytes == expected_bytes
            assert gpu_sources.retained_bytes('fixture') == 0
            assert gpu_strokes.active_bytes('fixture') == 64 * 64 * 32 + expected_bytes
            first.close()
            assert surface.bounds_bytes == expected_bytes
            np.testing.assert_array_equal(surface.eligible(row, settings, None)[0], np.ones((1, surface.count)))
            second.close()
            assert surface.bounds_bytes == 0 and surface.position is None and surface.normal is None
            assert gpu_strokes.active_bytes('fixture') == 0
        surface.close()  # release remains idempotent
        assert not device._leases

    # Dense snapshots build both material and full-object acceleration. Exact
    # masks and UV rectangles must match direct scans, including a stale hit
    # polygon and a brush broad enough to deliberately fall back to a scan.
    dense = gpu_strokes.Surface(entries_for(np.float32, 48), 64)
    material_bytes = sum(index.extra_bytes for index in dense.triangle_indexes)
    bounds_bytes = dense.count * 2 * 3 * np.dtype(np.float32).itemsize
    assert material_bytes > 0 and dense.bounds_bytes == bounds_bytes + material_bytes
    for mode in ('DEPTH', 'SURFACE'):
        for radius, polygon in ((.03, 48 * 24 + 24), (.2, 100000), (2., 0)):
            row = sample((.51, .51, 0), radius=radius)
            hit = anchor(row[:3], polygon)
            settings = dict(reach=mode, depth_limit=.5, planar=None)
            actual, rect = dense.eligible(row, settings, hit)
            with patch.object(TriangleBounds, 'query', TriangleBounds._scan):
                wanted, wanted_rect = dense.eligible(row, settings, hit)
            np.testing.assert_array_equal(actual, wanted)
            assert rect == wanted_rect
    reach_bytes = sum(entry['_reach']['bounds_index'].extra_bytes for entry in dense.entries)
    assert reach_bytes > 0 and dense.bounds_bytes == bounds_bytes + material_bytes + reach_bytes
    dense.pins = 1
    dense.close()
    assert dense.pending_close and dense.bounds_bytes > 0 and dense.entries
    dense.pins = 0
    dense.close()
    assert dense.bounds_bytes == 0 and not dense.triangle_indexes and not dense.entries
    dense.close()
    assert not device._leases

    # Sortedness is a property of each immutable material snapshot. Unsorted
    # and duplicate full-triangle IDs retain the generic lookup exactly.
    for kind in ('subset', 'reversed', 'duplicate'):
        entries = entries_for(np.float32)
        for entry in entries:
            if kind == 'subset':
                for key in ('tri_indices', 'tri_vertices', 'tri_loops'):
                    entry[key] = entry[key][::2]
            elif kind == 'reversed':
                for key in ('tri_indices', 'tri_vertices', 'tri_loops'):
                    entry[key] = entry[key][::-1]
            else:
                entry['tri_indices'][1] = entry['tri_indices'][0]
        changed = gpu_strokes.Surface(entries, 64)
        assert all(flag == (kind == 'subset') for flag in changed.sorted_triangle_indices)
        row = sample((.51, .51, 0), radius=.3)
        settings = dict(reach='SURFACE', depth_limit=.5, planar=None)
        hit = anchor(row[:3], 10)
        wanted, wanted_rect = exhaustive(changed, row, settings, hit)
        if kind == 'subset':
            actual, rect = changed.eligible(row, settings, hit)
        else:
            with patch.object(Sheets, 'mask_sorted', side_effect=AssertionError('Generic fallback required')):
                actual, rect = changed.eligible(row, settings, hit)
        np.testing.assert_array_equal(actual, wanted)
        assert rect == wanted_rect
        changed.close()
        assert not changed.sorted_triangle_indices and not device._leases
print('STROKE_SURFACE_BOUNDS_PASS', flush=True)
