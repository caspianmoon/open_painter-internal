"""Seamless surface filters (0.9.4): Surface-space effects follow the mesh across UV seams."""
import sys
from pathlib import Path
from time import monotonic

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
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
print('SURFACE_FILTERS_PASS', flush=True)
open_painter.unregister()
