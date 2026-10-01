"""Native geometry kernels against the reference on evaluated, mirrored meshes."""
from contextlib import nullcontext
import sys
from pathlib import Path
from unittest.mock import patch

import bpy
import gpu
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.core import texture_set_manager as sets
from open_painter.painting import projection, surface_reach
from open_painter.geometry import seams, snapshots
from open_painter.geometry.snapshots import targets
from open_painter.export import padding
from open_painter.native_bridge import lifecycle


def compare(entries):
    """Every native kernel against its reference, all on one evaluated snapshot."""
    actual = (*projection.geometry(ts, 512, coverage=True), *projection.tangents(ts, 512))
    with patch.object(lifecycle, 'cpu_library', return_value=None):
        expected = (*projection.geometry(ts, 512, coverage=True), *projection.tangents(ts, 512))
    for a, b in zip(actual, expected):
        np.testing.assert_allclose(a, b, rtol=0, atol=3e-7)
    native_owned = padding.build_ownership(entries, 512)
    with patch.object(lifecycle, 'cpu_library', return_value=None):
        reference_owned = padding.build_ownership(entries, 512)
    np.testing.assert_array_equal(native_owned.covered, reference_owned.covered)
    np.testing.assert_array_equal(native_owned.island, reference_owned.island)
    from open_painter.baking.maps.utility import _islands
    for target in entries:
        labels, borders = _islands(target)
        with patch.object(lifecycle, 'cpu_library', return_value=None):
            expected_labels, expected_borders = _islands(target)
        np.testing.assert_array_equal(labels, expected_labels)
        assert list(borders) == list(expected_borders)
        for island in borders:
            np.testing.assert_array_equal(borders[island], expected_borders[island])
        actual = surface_reach.adjacency(target['all_tri_vertices'])
        with patch.object(lifecycle, 'cpu_library', return_value=None):
            expected = surface_reach.adjacency(target['all_tri_vertices'])
        for a, b in zip(actual, expected):
            np.testing.assert_array_equal(a, b)
    native_donors = padding.nearest_donors(native_owned.covered, native_owned.island, 4)
    with patch.object(lifecycle, 'cpu_library', return_value=None):
        reference_donors = padding.nearest_donors(reference_owned.covered, reference_owned.island, 4)
    for a, b in zip(native_donors, reference_donors):
        np.testing.assert_array_equal(a, b)
    native_seam = seams.build(ts, 128, halo=12)
    with patch.object(lifecycle, 'cpu_library', return_value=None):
        reference_seam = seams.build(ts, 128, halo=12)
    assert native_seam.bounds == reference_seam.bounds
    np.testing.assert_array_equal(native_seam._nearest, reference_seam._nearest)
    assert native_seam.seam_edges == reference_seam.seam_edges
    assert (native_seam.sides is None) == (reference_seam.sides is None)
    if native_seam.sides is not None:
        for actual, expected in zip(native_seam.sides, reference_seam.sides):
            np.testing.assert_array_equal(actual, expected)
    for field in ('covered', 'rows', 'cols', 'island', 'triangle', 'island_of'):
        np.testing.assert_array_equal(getattr(native_seam, field), getattr(reference_seam, field), err_msg=field)
    for field in ('source_x', 'source_y', 'depth', 'normal', 'jacobians'):
        np.testing.assert_allclose(getattr(native_seam, field), getattr(reference_seam, field), atol=3e-5, rtol=1e-6, err_msg=field)
    for axis in ((0, 0, 0), (0, 0, 1), (.3, .7, .4)):
        actual = native_seam.uv_direction(axis)
        with patch.object(lifecycle, 'cpu_library', return_value=None):
            expected = reference_seam.uv_direction(axis)
        np.testing.assert_allclose(actual, expected, atol=3e-6, rtol=0)


gpu.init()
open_painter.register()
try:
    # The CPU kernels, on any GPU backend: the library must be loaded (OP_LIBOPENPAINTER_PATH or
    # the bundle). Without it there is nothing to compare with the reference.
    assert lifecycle.cpu_library() is not None, lifecycle.diagnostics()
    scene, obj = bpy.context.scene, bpy.context.object
    ts = sets.create(scene, 'Native evaluated geometry', 512)
    sets.assign(scene, ts, obj)
    modifier = obj.modifiers.new('Native geometry bevel', 'BEVEL')
    modifier.width, modifier.segments = .18, 3
    for scale in ((1, 2, .7), (-1, 2, .7)):
        obj.scale, obj.location = scale, (2, -3, 1)
        bpy.context.view_layer.update()
        # Both paths read one evaluation. Blender's bevel can round a UV differently from one
        # evaluation to the next (one ulp at 20 of its 384 corners on Windows, even with one
        # thread), and between two evaluations that moves edge texels to a neighboring triangle.
        with targets(ts) as entries:
            entries = list(entries)
            with patch.object(snapshots, 'targets', lambda _ts: nullcontext(entries)):
                compare(entries)
    print('NATIVE_GEOMETRY_PROJECTION_PASS')
finally:
    open_painter.unregister()
