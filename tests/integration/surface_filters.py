"""Seamless surface filters (0.9.4): Surface-space effects follow the mesh across UV seams."""
import sys
from pathlib import Path
from time import monotonic

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
# Exercise the retained CPU-boundary fallback; GPU filters have dedicated parity coverage.
from unittest.mock import patch
patch('open_painter.evaluation.engine.filters.supported', return_value=False).start()
from open_painter.core import image_manager as images, layer_stack as stack, material_manager
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache, channel_eval, shader_preview
from open_painter.evaluation.mask_eval import evaluate_masks
from open_painter.evaluation.operation_contracts import stack_footprint
from open_painter.geometry import seams
from open_painter.utils.ids import new_id


def ready(scene, ts):
    deadline = monotonic() + 90
    while monotonic() < deadline:
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert not info['error'], info
        if shader_preview.current(scene, ts):
            return info
    raise AssertionError(shader_preview.diagnostics(scene, ts))


def make(name, cube=True):
    if cube:
        bpy.ops.mesh.primitive_cube_add(size=2)
    else:
        bpy.ops.mesh.primitive_plane_add(size=2)
    obj = bpy.context.object
    obj.name = name
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    if cube:
        bpy.ops.mesh.subdivide(number_cuts=2)
        bpy.ops.uv.smart_project(island_margin=.04)
    bpy.ops.object.mode_set(mode='OBJECT')
    return obj


def channel_raster(ts, layer, n, geometry):
    from open_painter.evaluation.snapshots import capture
    prop = stack.channel_for(capture(layer), 'BASE_COLOR')
    return channel_eval.source(prop, 'FILL', n, 0, n, {}, ts, geometry)[..., 0].copy()


def near_seam(seam, width):
    """Covered texels within ``width`` texels of an uncovered one."""
    outside = ~seam.covered
    grown = outside.copy()
    for axis in (0, 1):
        for step in range(1, width + 1):
            grown |= np.roll(outside, step, axis=axis) | np.roll(outside, -step, axis=axis)
    return seam.covered & grown


open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
cube = make('Cube')
ts = sets.create(scene, 'Surface filters', 512)
sets.assign(scene, ts, cube)
n = 512
# A field that is continuous across seams by construction: vertex-group weights
# (x + 1) / 2, interpolated over the surface and rasterized through the UVs.
group = cube.vertex_groups.new(name='Grad')
for vertex in cube.data.vertices:
    group.add([vertex.index], (vertex.co.x + 1) / 2, 'REPLACE')
layer = stack.add(ts, 'FILL', 'Grain')
layer_id = layer.id
probe = layer.masks.add()
probe.id, probe.source = new_id(), 'MESH'
probe.mesh.kind, probe.mesh.attribute = 'VERTEX_GROUP', 'Grad'
from open_painter.evaluation import mesh_masks
field = mesh_masks.raster(ts, probe, n)
layer.masks.clear()
gradient = images.create('Gradient', n)
data = np.ones((n, n, 4), np.float32)
data[..., 0] = data[..., 1] = data[..., 2] = field
images.write(gradient, data, pack=False)
prop = stack.channel_for(layer, 'BASE_COLOR')
prop.image, prop.projection = gradient, 'UV'
effect = prop.effects.add()
effect.kind, effect.radius = 'GAUSSIAN_BLUR', 5.
layer_of = lambda: next(l for l in ts.layers if l.id == layer_id)
effect_of = lambda: stack.channel_for(layer_of(), 'BASE_COLOR').effects[0]

seam = seams.map_for(ts, n)
assert seam.seam_edges > 0 and len(seam.rows) > 1000 and seam.covered.any(), (seam.seam_edges, len(seam.rows))
assert not seam.covered[seam.rows, seam.cols].any(), 'padding never targets covered texels'
assert seam.halo >= 40
# The padding band reads from inside islands.
inside_sources = seam.covered[np.clip(np.round(seam.source_y).astype(int), 0, n - 1),
                              np.clip(np.round(seam.source_x).astype(int), 0, n - 1)]
assert inside_sources.mean() > .9, inside_sources.mean()
print('SURFACE_FILTERS_MAP', seam.seam_edges, len(seam.rows), flush=True)


# 0.9.7: the band is rastered many seam sides per grid and reduced per (island, texel)
# in dense arenas as it goes. It must equal the per-edge reference (one grid per seam
# side, the closest seam winning) up to texels on a band boundary, whatever the
# chunk and arena budgets.
def reference_bands(seam, halo):
    n = seam.n
    gy, gx = np.mgrid[0:n, 0:n]
    closest = {}
    for s0, s1, centroid, o0, o1, _, island in zip(*seam.sides):
        edge, other = (s1 - s0).astype(np.float64), (o1 - o0).astype(np.float64)
        length = float(np.hypot(*edge))
        if length < 1e-6 or float(np.hypot(*other)) < 1e-6:
            continue
        tangent = edge / length
        normal = np.array((-tangent[1], tangent[0]))
        if np.dot(centroid - s0, normal) > 0:
            normal = -normal
        px, py = gx + .5 - s0[0], gy + .5 - s0[1]
        along, depth = px * tangent[0] + py * tangent[1], px * normal[0] + py * normal[1]
        inside = (depth > 0) & (depth <= halo) & (along >= -halo) & (along <= length + halo) & ~seam.covered
        distance = np.hypot(depth, np.maximum(np.maximum(-along, along - length), 0))
        for r, c, d in zip(gy[inside], gx[inside], distance[inside]):
            key = (int(island), int(r), int(c))
            closest[key] = min(closest.get(key, np.inf), float(d))
    return closest


def entries(seam):
    return {(int(i), int(r), int(c)): float(d) for i, r, c, d in zip(seam.island, seam.rows, seam.cols, seam.depth)}


reference = reference_bands(seam, seams.DEFAULT_HALO)
built = entries(seam)
assert len(built) == len(seam.rows), 'one entry per (island, texel)'
boundary = set(reference) ^ set(built)
assert len(boundary) <= len(reference) * 1e-3, (len(boundary), len(reference))
assert max(abs(reference[key] - built[key]) for key in set(reference) & set(built)) < 1e-3
budgets = seams.ARENA_TEXELS, seams.BAND_TEXELS
seams.ARENA_TEXELS, seams.BAND_TEXELS = 1 << 12, 1 << 14
try:
    chunked = seams.build(ts, n)
finally:
    seams.ARENA_TEXELS, seams.BAND_TEXELS = budgets
order = np.lexsort((seam.cols, seam.rows, seam.island))
chunked_order = np.lexsort((chunked.cols, chunked.rows, chunked.island))
for name in ('rows', 'cols', 'source_x', 'source_y', 'depth', 'normal', 'island', '_nearest'):
    np.testing.assert_array_equal(getattr(seam, name)[order], getattr(chunked, name)[chunked_order], name)
assert chunked.bounds == seam.bounds
# Bounds cover exactly the islands' covered texels.
for island, (r0, r1, c0, c1) in seam.bounds.items():
    where = np.nonzero(seam.island_of == island)
    assert (r0, r1, c0, c1) == (where[0].min(), where[0].max(), where[1].min(), where[1].max())
assert set(seam.bounds) == set(np.unique(seam.island_of[seam.island_of >= 0]).tolist())
# Moving the asset keeps its seam map (only orientation and size feed the Jacobians);
# rotating it rebuilds, and a preview-resolution map never evicts a full one.
cube.location.x += 1.
bpy.context.view_layer.update()
assert seams.map_for(ts, n) is seam, 'a move rebuilt the seam map'
cube.rotation_euler.z = .5
bpy.context.view_layer.update()
rotated = seams.map_for(ts, n)
assert rotated is not seam and not np.allclose(rotated.jacobians, seam.jacobians)
cube.location.x -= 1.
cube.rotation_euler.z = 0.
bpy.context.view_layer.update()
assert seams.map_for(ts, n) is seam
small = seams.map_for(ts, 64)
assert small is not seam and seams.map_for(ts, n) is seam and seams.map_for(ts, 64) is small
print('SURFACE_FILTERS_BUILD_OK', flush=True)
# Per-island lookups slice the island-grouped band (0.9.7) instead of scanning
# the whole band once per island; bounds come from one pass over the coverage.
assert len(seam.bounds) > 1 and set(seam.spans) <= set(seam.bounds)
assert sum(e - s for s, e in seam.spans.values()) == len(seam.island)
for island in seam.spans:
    entries = seam.entries(island)
    assert len(entries) == int((seam.island == island).sum()) and (seam.island[entries] == island).all(), island
for island, bounds in seam.bounds.items():
    where = np.nonzero(seam.island_of == island)
    assert bounds == (int(where[0].min()), int(where[0].max()), int(where[1].min()), int(where[1].max()))
from open_painter.texture_engine import filters
whole_padded = seam.pad(field, 12)
for island in seam.bounds:
    crop, (r0, r1, c0, c1) = seam.island_crop(whole_padded, island, 12)
    expected = whole_padded[r0:r1, c0:c1].copy()
    pick = (seam.island == island) & (seam.depth <= 12)
    rows, cols = seam.rows[pick], seam.cols[pick]
    within = (rows >= r0) & (rows < r1) & (cols >= c0) & (cols < c1)
    expected[rows[within] - r0, cols[within] - c0] = filters.sample(
        whole_padded, seam.source_x[pick][within], seam.source_y[pick][within])
    np.testing.assert_array_equal(crop, expected)
print('SURFACE_FILTERS_ISLANDS_OK', len(seam.bounds), flush=True)

# A smooth 3D-continuous field blurred in Surface space stays close to itself at seams;
# a UV blur mixes the margin in and departs from it.
from open_painter.evaluation.raster_cache import for_texture_set
geometry = for_texture_set(scene, ts)
effect_of().enabled = False
source = channel_raster(ts, layer_of(), n, geometry)
assert np.abs(source - field)[seam.covered].max() < 1e-4
effect_of().enabled = True
effect_of().space = 'UV'
assert stack_footprint([effect_of()]) is not None
uv_blur = channel_raster(ts, layer_of(), n, geometry)
effect_of().space = 'SURFACE'
assert stack_footprint([effect_of()]) is None, 'surface effects need the whole image'
surface_blur = channel_raster(ts, layer_of(), n, geometry)
band = near_seam(seam, 4)
interior = seam.covered & ~near_seam(seam, 20)
uv_error = float(np.abs(uv_blur - source)[band].mean())
surface_error = float(np.abs(surface_blur - source)[band].mean())
interior_error = float(np.abs(surface_blur - source)[interior].mean())
print('SURFACE_FILTERS_SEAM', dict(uv=uv_error, surface=surface_error, interior=interior_error), flush=True)
assert surface_error < uv_error * .25, (uv_error, surface_error)
assert surface_error < .03, surface_error
assert np.allclose(surface_blur[interior], uv_blur[interior], atol=1e-3), 'away from seams both agree'

# Morphology and warps take the same path; directional effects follow a world direction.
for kind in ('DILATE', 'WARP', 'DIRECTIONAL_BLUR', 'SHARPEN'):
    effect_of().kind = kind
    effect_of().radius = 4.
    effect_of().space = 'UV'
    a = channel_raster(ts, layer_of(), n, geometry)
    effect_of().space = 'SURFACE'
    b = channel_raster(ts, layer_of(), n, geometry)
    assert np.isfinite(b).all() and b.shape == (n, n)
    assert not np.allclose(a[band], b[band]), f'{kind}: surface space must differ at seams'
effect_of().kind = 'DIRECTIONAL_BLUR'
effect_of().direction = (1., 0., 0.)
field = seam.uv_direction((1., 0., 0.))
top = (seam.triangle >= 0) & seam.covered
lengths = np.linalg.norm(field[top], axis=1)
assert (lengths > .99).mean() > .5 and (lengths < 1e-6).mean() > .05, 'faces facing ±X have no in-plane X direction'
effect_of().direction = (0., 0., -1.)
down = channel_raster(ts, layer_of(), n, geometry)
effect_of().direction = (1., 0., 0.)
along_x = channel_raster(ts, layer_of(), n, geometry)
assert not np.allclose(down, along_x)
print('SURFACE_FILTERS_KINDS_OK', flush=True)

# Mask filters run seam-aware through the whole-image path.
effect_of().enabled = False
mask = layer_of().masks.add()
mask.id, mask.source = new_id(), 'MESH'
mask.mesh.kind, mask.mesh.attribute = 'VERTEX_GROUP', 'Grad'
blur = layer_of().masks.add()
blur.id, blur.source = new_id(), 'FILTER'
blur.effect.kind, blur.effect.radius, blur.effect.space = 'GAUSSIAN_BLUR', 5., 'UV'
plain = evaluate_masks(ts, layer_of(), n, 0, n, {}, {})
uv_mask = plain.copy()
blur.effect.space = 'SURFACE'
surface_mask = evaluate_masks(ts, layer_of(), n, 0, n, {}, {})
blur.enabled = False
raw_mask = evaluate_masks(ts, layer_of(), n, 0, n, {}, {})
blur.enabled = True
assert np.abs(surface_mask - raw_mask)[band].mean() < np.abs(uv_mask - raw_mask)[band].mean() * .5
layer_of().masks.clear()
print('SURFACE_FILTERS_MASK_OK', flush=True)

# The shader's isolated input takes the same seam-aware path as the CPU reference.
effect_of().enabled = True
effect_of().kind, effect_of().space, effect_of().radius = 'GAUSSIAN_BLUR', 'SURFACE', 5.
material_manager.repair(ts)
ready(scene, ts)
shader_preview.flush(scene, ts)
graph = shader_preview.ensure(scene, ts)
node = next(node for node in graph.nodes() if node.get('op_boundary', '').startswith('["channel_prefix"'))
boundary = images.pixels(node.image)[..., 0]
expected = channel_raster(ts, layer_of(), n, for_texture_set(scene, ts))
assert np.abs(boundary - expected)[seam.covered].max() < 1e-4, float(np.abs(boundary - expected)[seam.covered].max())
print('SURFACE_FILTERS_SHADER_OK', flush=True)

# A single-island plane has no seams: Surface and UV spaces agree exactly.
bpy.ops.object.select_all(action='DESELECT')
plane = make('Plane', cube=False)
flat = sets.create(scene, 'Flat', 512)
sets.assign(scene, flat, plane)
flat_layer = stack.add(flat, 'FILL', 'Grain')
flat_prop = stack.channel_for(flat_layer, 'BASE_COLOR')
flat_prop.procedural, flat_prop.projection, flat_prop.procedural_scale = 'FBM', 'UV', 6.
flat_effect = flat_prop.effects.add()
flat_effect.kind, flat_effect.radius, flat_effect.space = 'GAUSSIAN_BLUR', 4., 'UV'
flat_geometry = for_texture_set(scene, flat)
a = channel_raster(flat, flat.layers[0], n, flat_geometry)
flat_effect.space = 'SURFACE'
b = channel_raster(flat, flat.layers[0], n, for_texture_set(scene, flat))
assert seams.map_for(flat, n).seam_edges == 0
assert np.allclose(a, b, atol=1e-5), float(np.abs(a - b).max())
assert seams.map_for(flat, 64).seam_edges == 0 and seams.map_for(flat, n) is not seam
assert seams.map_for(next(t for t in scene.open_painter.texture_sets if t.name == 'Surface filters'), n) is seam, \
    'two sets at two resolutions evicted a full-resolution map'

# A Surface-space boundary is identified by the mesh it pads across (0.9.7): a
# re-unwrap re-rasters it, and two sets with the same mask stack never share one.
from open_painter.evaluation import dependencies
ts = next(item for item in scene.open_painter.texture_sets if item.name == 'Surface filters')
ready(scene, ts)
graph = shader_preview.ensure(scene, ts)
binding = next(b for b in graph.resource_bindings if b.identifier.startswith('RasterChannelPrefix:'))
before = binding.signature
for loop in cube.data.uv_layers.active.data:
    loop.uv = (loop.uv.x * .9 + .05, loop.uv.y * .9 + .05)
cube.data.update()
dependencies._mesh_epochs[cube.data.as_pointer()] = dependencies.mesh_epoch(cube.data) + 1
dependencies._candidates.add(scene.as_pointer())
dependencies.refresh(scene)
ready(scene, ts)
shader_preview.flush(scene, ts)
assert binding.signature != before, 'the island padding follows the old UV layout'
node = next(node for node in graph.nodes() if node.get('op_boundary', '').startswith('["channel_prefix"'))
boundary = images.pixels(node.image)[..., 0]
moved = seams.map_for(ts, n)
expected = channel_raster(ts, layer_of(), n, for_texture_set(scene, ts))
assert np.abs(boundary - expected)[moved.covered].max() < 1e-4, float(np.abs(boundary - expected)[moved.covered].max())

# Wide blurs convolve by FFT (Gaussian) and running sums (box) rather than one
# full pass per tap (0.9.7): the boundary still matches the CPU reference at the
# widest radii on the UV strip path and the whole-image Surface path.
for kind, space, radius in (('GAUSSIAN_BLUR', 'UV', 40.), ('GAUSSIAN_BLUR', 'SURFACE', 64.),
                            ('BLUR', 'UV', 64.), ('BLUR', 'SURFACE', 40.)):
    effect_of().kind, effect_of().space, effect_of().radius = kind, space, radius
    started = monotonic()
    ready(scene, ts)
    shader_preview.flush(scene, ts)
    graph = shader_preview.ensure(scene, ts)   # a kind change rebuilds the tree
    node = next(node for node in graph.nodes() if node.get('op_boundary', '').startswith('["channel_prefix"'))
    boundary = images.pixels(node.image)[..., 0]
    expected = channel_raster(ts, layer_of(), n, for_texture_set(scene, ts))
    error = float(np.abs(boundary - expected)[moved.covered].max())
    assert error < 1e-4, (kind, space, radius, error)
    print('SURFACE_FILTERS_WIDE', kind, space, radius, dict(seconds=round(monotonic() - started, 2)), flush=True)
effect_of().kind, effect_of().space, effect_of().radius = 'GAUSSIAN_BLUR', 'SURFACE', 5.
for holder in (ts, flat):
    masked = stack.add(holder, 'FILL', 'Shared stack')
    stack.channel_for(masked, 'BASE_COLOR').color = (.9, .2, .1, 1.)
    noise = masked.masks.add()
    noise.id, noise.source, noise.procedural, noise.noise_scale = new_id(), 'PROCEDURAL', 'FBM', 5.
    soft = masked.masks.add()
    soft.id, soft.source = new_id(), 'FILTER'
    soft.effect.kind, soft.effect.radius, soft.effect.space = 'GAUSSIAN_BLUR', 4., 'SURFACE'
    material_manager.repair(holder)
signatures = []
for holder in (ts, flat):
    ready(scene, holder)
    shader_preview.flush(scene, holder)
    live = shader_preview.ensure(scene, holder)
    signatures.append(next(b for b in live.resource_bindings if b.identifier.startswith('RasterMaskPrefix:')).signature)
assert signatures[0] != signatures[1], 'one per-island raster served two meshes'
print('SURFACE_FILTERS_IDENTITY_OK', flush=True)

# Edits that keep a whole-image mask stack on its (0, n) cache range still
# re-raster it through the edit-surviving CPU cache (0.9.7): a filter mask's
# space, direction and, in Surface space, the mesh it pads across are keyed.
stale_id = stack.add(ts, 'FILL', 'Stale stack').id
stale_of = lambda: next(l for l in ts.layers if l.id == stale_id)
noise = stale_of().masks.add()
noise.id, noise.source, noise.procedural, noise.noise_scale = new_id(), 'PROCEDURAL', 'FBM', 5.
soft = stale_of().masks.add()
soft.id, soft.source = new_id(), 'FILTER'
soft.effect.kind, soft.effect.radius, soft.effect.space = 'GAUSSIAN_BLUR', 4., 'SURFACE'
filter_of = lambda: stale_of().masks[1].effect
shared = lambda: evaluate_masks(ts, stale_of(), n, 0, n, {}, for_texture_set(scene, ts)).copy()
fresh = lambda: evaluate_masks(ts, stale_of(), n, 0, n, {}, {})
padded = shared()
np.testing.assert_array_equal(shared(), padded)
for loop in cube.data.uv_layers.active.data:
    loop.uv = (loop.uv.x * .9 + .05, loop.uv.y * .9 + .05)
cube.data.update()
dependencies._mesh_epochs[cube.data.as_pointer()] = dependencies.mesh_epoch(cube.data) + 1
dependencies._candidates.add(scene.as_pointer())
dependencies.refresh(scene)
repadded = shared()
np.testing.assert_array_equal(repadded, fresh())
assert not np.allclose(padded, repadded), 'the mask kept the padding of the old UV layout'
filter_of().kind = 'DIRECTIONAL_BLUR'
filter_of().direction = (0., 0., -1.)
down = shared()
filter_of().direction = (1., 0., 0.)
along_x = shared()
np.testing.assert_array_equal(along_x, fresh())
assert not np.allclose(down, along_x), 'the mask kept the old blur direction'
filter_of().kind = 'WARP'
surface_warp = shared()
filter_of().space = 'UV'
uv_warp = shared()
np.testing.assert_array_equal(uv_warp, fresh())
assert not np.allclose(surface_warp, uv_warp), 'the mask kept the Surface-space warp'
filter_of().kind, filter_of().space = 'GAUSSIAN_BLUR', 'SURFACE'
# A whole-image stack the shared allowance refuses (or evicts) is rastered once
# per job, not once per 16-row strip.
from open_painter.evaluation import memory
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.metrics import Metrics
masked_layers = sum(1 for l in ts.layers if l.visible and l.masks)
allowance = memory.shared.allowance
memory.shared.configure(0)
try:
    with Metrics() as observation:
        LayerEvaluator(scene).evaluate_channel(ts.id, 'BASE_COLOR')
finally:
    memory.shared.configure(allowance)
calls = observation.phases['masking']['calls']
assert calls == masked_layers, (calls, masked_layers)
print('SURFACE_FILTERS_CACHE_OK', dict(masking_calls=calls, layers=masked_layers), flush=True)

# A dense, many-island mesh builds its map in seconds within a bounded memory (the
# per-edge build took 43 s and 3.9 GB here): the bands never pile up before the
# per-(island, texel) reduction, and the island bounds come from one pass rather than
# one scan per island.
import tracemalloc
bpy.ops.object.select_all(action='DESELECT')
bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=5)
dense = bpy.context.object
bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.uv.smart_project(angle_limit=np.radians(5), island_margin=.02)
bpy.ops.object.mode_set(mode='OBJECT')
dense_set = sets.create(scene, 'Dense', 1024)
sets.assign(scene, dense_set, dense)
tracemalloc.start()
started = monotonic()
dense_map = seams.build(dense_set, 1024)
elapsed = monotonic() - started
peak = tracemalloc.get_traced_memory()[1]
tracemalloc.stop()
held = sum(getattr(dense_map, name).nbytes for name in ('covered', 'rows', 'cols', 'source_x', 'source_y', 'depth',
                                                        'normal', 'island', 'island_of', 'triangle', 'jacobians'))
print('SURFACE_FILTERS_DENSE', dict(seam_edges=dense_map.seam_edges, islands=len(dense_map.bounds),
                                    entries=len(dense_map.rows), seconds=round(elapsed, 2),
                                    map_mib=held // 2 ** 20, peak_mib=peak // 2 ** 20), flush=True)
assert dense_map.seam_edges > 2000 and len(dense_map.bounds) > 500, (dense_map.seam_edges, len(dense_map.bounds))
assert not dense_map.covered[dense_map.rows, dense_map.cols].any()
assert len(dense_map.bounds) == len(np.unique(dense_map.island_of[dense_map.island_of >= 0]))
assert elapsed < 12, elapsed
assert peak < 2 * held + (256 << 20), (peak, held)
print('SURFACE_FILTERS_PASS', flush=True)
open_painter.unregister()
