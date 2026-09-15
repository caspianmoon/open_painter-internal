"""The edge map bakes as a set of four reaches; Edge Size blends between them."""
from pathlib import Path
import sys
from types import SimpleNamespace

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.baking.bake_manager import register_maps, stale_curvature, register_map, refresh_stale_curvature
from open_painter.baking.maps.curvature import EDGE_MAP_KINDS, bake_curvature_set, edge_map_set_complete, METHOD
from open_painter.core import image_manager as images, texture_set_manager as sets
from open_painter.generators.base import edge_curvature

open_painter.register()
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add(size=2)
cube = bpy.context.object
bevel = cube.modifiers.new('rims', 'BEVEL')
bevel.width, bevel.segments = .12, 5
bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.uv.smart_project(angle_limit=1.15, island_margin=.02)
bpy.ops.object.mode_set(mode='OBJECT')
ts = sets.create(scene, 'Edge map set', 512)
sets.assign(scene, ts, cube)
ts.bake.mean_curvature_distance = .05
produced = bake_curvature_set(ts)
assert tuple(produced) == EDGE_MAP_KINDS, tuple(produced)
means = {}
for kind, image in produced.items():
    pixels = images.pixels(image)[..., 0]
    assert image.get('open_painter_curvature_method') == METHOD
    assert np.isfinite(pixels).all() and pixels.max() > .5, (kind, pixels.max())
    means[kind] = float(pixels.mean())
    print('EDGE_MAP', kind, 'radius', round(image['mean_curvature_distance'], 4), 'mean', round(means[kind], 4), flush=True)
assert means['CURVATURE_THIN'] < means['CURVATURE'] < means['CURVATURE_WIDE'] < means['CURVATURE_XWIDE'], means
assert abs(produced['CURVATURE']['mean_curvature_distance'] - .05) < 1e-6
assert abs(produced['CURVATURE_XWIDE']['mean_curvature_distance'] - .2) < 1e-6

# Only the normal map registered: stale until the companions exist; then complete.
register_map(ts, 'CURVATURE', produced['CURVATURE'])
assert not edge_map_set_complete(ts) and stale_curvature(ts) is not None
register_maps(ts, produced)
assert edge_map_set_complete(ts) and stale_curvature(ts) is None
assert refresh_stale_curvature(ts, {'CURVATURE'}) is False

# Edge Size 0/1/2/3 reproduce the maps (to float rounding); fractional sizes lie between neighbours.
cache = {}
def mesh_map(kind):
    from open_painter.evaluation.mask_eval import map_pixels
    return map_pixels(ts, kind, cache)
for size, kind in enumerate(EDGE_MAP_KINDS):
    value = edge_curvature(ts, SimpleNamespace(edge_size=float(size)), mesh_map)
    np.testing.assert_allclose(value, mesh_map(kind)[..., 0], atol=1e-6)
half = edge_curvature(ts, SimpleNamespace(edge_size=1.5), mesh_map)
lo, hi = mesh_map('CURVATURE')[..., 0], mesh_map('CURVATURE_WIDE')[..., 0]
assert np.all(half >= np.minimum(lo, hi) - 1e-6) and np.all(half <= np.maximum(lo, hi) + 1e-6)
assert means['CURVATURE'] < float(half.mean()) < means['CURVATURE_WIDE']
# Without companions the generator falls back to the single map.
ts.baked_maps.remove(next(i for i, m in enumerate(ts.baked_maps) if m.kind == 'CURVATURE_WIDE'))
np.testing.assert_array_equal(edge_curvature(ts, SimpleNamespace(edge_size=2.5), mesh_map), mesh_map('CURVATURE')[..., 0])
print('EDGE_MAP_SET_PASS', flush=True)
open_painter.unregister()
