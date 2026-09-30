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
from open_painter.baking.maps import mean_curvature
from open_painter.baking.maps.curvature import EDGE_MAP_KINDS, bake_curvature_set, edge_map_set_complete, METHOD
from open_painter.core import image_manager as images, texture_set_manager as sets
from open_painter.generators.base import edge_curvature

open_painter.register()
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
# Hard edges: the Cycles Bevel reaches measure how far the normal turns within their radius,
# so a rim rounded wider than the thinnest reach (the geometric method's old fixture, a
# 12 cm five-segment bevel) reads faint at every reach. A right-angle edge reads strongly
# at each, the band widening with the reach.
bpy.ops.mesh.primitive_cube_add(size=2)
cube = bpy.context.object
bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.uv.smart_project(angle_limit=1.15, island_margin=.02)
bpy.ops.object.mode_set(mode='OBJECT')
ts = sets.create(scene, 'Edge map set', 512)
sets.assign(scene, ts, cube)
ts.bake.device = 'CPU'
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
# An Edge Size edit re-rasters an Edge Wear mask through the shared, edit-surviving
# CPU cache (0.9.7): the generator signature carries edge_size.
from open_painter.core import layer_stack as stack
from open_painter.evaluation.mask_eval import evaluate_masks
from open_painter.evaluation.raster_cache import for_texture_set
from open_painter.utils.ids import new_id
worn_id = stack.add(ts, 'FILL', 'Wear').id
worn_of = lambda: next(l for l in ts.layers if l.id == worn_id)
wear = worn_of().masks.add()
wear.id, wear.source, wear.generator = new_id(), 'GENERATOR', 'EDGE_WEAR'
wear.amount, wear.noise_strength, wear.edge_size = 1., 0., 1.
normal = evaluate_masks(ts, worn_of(), 512, 0, 512, {}, for_texture_set(scene, ts))
worn_of().masks[0].edge_size = 3.
extreme = evaluate_masks(ts, worn_of(), 512, 0, 512, {}, for_texture_set(scene, ts))
fresh = evaluate_masks(ts, worn_of(), 512, 0, 512, {}, {})
np.testing.assert_array_equal(extreme, fresh)
assert float(extreme.mean()) > float(normal.mean()), (float(normal.mean()), float(extreme.mean()))
stack.remove(ts, next(i for i, l in enumerate(ts.layers) if l.id == worn_id))
# Without companions the generator falls back to the single map.
ts.baked_maps.remove(next(i for i, m in enumerate(ts.baked_maps) if m.kind == 'CURVATURE_WIDE'))
np.testing.assert_array_equal(edge_curvature(ts, SimpleNamespace(edge_size=2.5), mesh_map), mesh_map('CURVATURE')[..., 0])

# A rebake releases the maps it supersedes (0.9.7): they used to survive on
# their fake user and were packed into the .blend on every save. Only the fake
# user goes, not the datablock (an undo of the rebake finds its pixels): a map
# nothing binds is purged on the next save, one a node still binds goes once
# unbound; a user-supplied image is left alone.
from open_painter.constants import OWNER
superseded = {m.kind: m.image for m in ts.baked_maps if m.kind in EDGE_MAP_KINDS}
material = bpy.data.materials.new('binds the old map')
material.use_nodes = True
node = material.node_tree.nodes.new('ShaderNodeTexImage')
node.image = superseded['CURVATURE_THIN']
picked = bpy.data.images.new('artist curvature', 8, 8)
picked.use_fake_user = True
register_map(ts, 'CURVATURE_XWIDE', picked)
released = superseded['CURVATURE_XWIDE']
assert released.users == 0 and not released.use_fake_user, (released.users, released.use_fake_user)
register_maps(ts, bake_curvature_set(ts))
assert edge_map_set_complete(ts)
released = superseded['CURVATURE']
assert released.users == 0 and not released.use_fake_user, 'the superseded map kept its fake user'
bound = superseded['CURVATURE_THIN']
assert bound.users == 1 and not bound.use_fake_user, (bound.users, bound.use_fake_user)
node.image = None
assert bound.users == 0
assert picked.name in bpy.data.images and picked.use_fake_user and not picked.get(OWNER)
assert all(m.image.users == 2 and m.image.use_fake_user for m in ts.baked_maps if m.kind in EDGE_MAP_KINDS)
bpy.data.materials.remove(material)
bpy.data.images.remove(picked)

# A repeated geometric bake reproduces each reach.
shared = bake_curvature_set(ts)
for kind, image in shared.items():
    assert abs(float(images.pixels(image)[..., 0].mean()) - means[kind]) < 1e-4, kind
    bpy.data.images.remove(image)

# The pair matrix of a cell (its texels against the nearby samples) is sliced
# (0.9.7): unbounded, a dense 4k asset at the XWIDE reach allocated gigabytes
# per octant and the modal froze for the whole cell. Slices sum to the same map.
from open_painter.geometry.snapshots import targets
largest = [0]
original_einsum = np.einsum
def measuring_einsum(subscripts, *operands, **kwargs):
    if subscripts == 'ijk,ijk->ij':
        largest[0] = max(largest[0], operands[0].shape[0] * operands[0].shape[1])
    return original_einsum(subscripts, *operands, **kwargs)
with targets(ts) as meshes:
    reference = mean_curvature.convex_map(meshes, 128, 1.0)
    mean_curvature.TEXEL_CHUNK, mean_curvature.SAMPLE_CHUNK, np.einsum = 32, 64, measuring_einsum
    try:
        sliced = mean_curvature.convex_map(meshes, 128, 1.0)
    finally:
        mean_curvature.TEXEL_CHUNK, mean_curvature.SAMPLE_CHUNK, np.einsum = 1024, 4096, original_einsum
    assert 0 < largest[0] <= 32 * 64, largest[0]
    np.testing.assert_allclose(sliced, reference, atol=1e-5)
# A zero-scaled axis (a plate flattened with S Z 0) is a singular matrix: face
# turning takes the safe inverse like every other normal transform instead of
# raising 'Singular matrix' from the bake and the live field.
from open_painter.geometry import live_curvature
cube.scale = (1., 1., 0.)
bpy.context.view_layer.update()
with targets(ts) as meshes:
    flat = mean_curvature.convex_map(meshes, 128, .05)
    assert np.isfinite(flat).all()
    density, used = live_curvature.vertex_density(meshes[0], .05, np.radians(30))
    assert np.isfinite(density).all() and used.any()
cube.scale = (1., 1., 1.)
bpy.context.view_layer.update()
print('EDGE_MAP_SET_PASS', flush=True)
open_painter.unregister()
