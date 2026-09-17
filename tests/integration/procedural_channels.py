"""Texture procedural channels, shared scalar cache, native effects and UV projection."""

import sys
from pathlib import Path
from unittest.mock import patch

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.core import image_manager as images, layer_stack as stack, texture_set_manager as sets
from open_painter.evaluation.channel_eval import source, apply_effect, effect_parameters, effect_ui_fields
from open_painter.evaluation.mask_eval import evaluate_masks
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.texture_engine import procedurals, filters

open_painter.register()
ts = sets.create(bpy.context.scene, 'Procedural channels', 512)
ts.preview_backend = 'CPU'  # Allocate outputs explicitly for raster invalidation checks.
LayerEvaluator(bpy.context.scene).evaluate_texture_set(ts.id)
layer = stack.add(ts, 'FILL')
base = stack.channel_for(layer, 'BASE_COLOR')
rough = stack.channel_for(layer, 'ROUGHNESS')
height = stack.channel_for(layer, 'HEIGHT')
normal = stack.channel_for(layer, 'NORMAL')
for channel in (base, rough, height, normal):
    channel.procedural = 'FBM'
    channel.procedural_scale = 4.37
    channel.procedural_seed = 19
    channel.procedural_detail = .7
base.color = (.2, .4, .6, .8)
rough.value = .35
height.procedural_height = -.4
normal.color = (.5, .5, 1, 1)
n = 128
y, x = np.indices((n, n), dtype=np.float32)
u, v = (x + .5) / n, (y + .5) / n
expected = procedurals.evaluate('FBM', u, v, seed=19, scale=4.37)
shared = {}
with patch.object(procedurals, 'evaluate', wraps=procedurals.evaluate) as evaluate:
    rgba = source(base, 'FILL', n, 0, 32, {}, ts, shared)
    base_calls = evaluate.call_count
    roughness = source(rough, 'FILL', n, 0, 32, {}, ts, shared)
    assert base_calls > 0 and evaluate.call_count == base_calls, 'The same procedural was recalculated for another channel'
np.testing.assert_allclose(rgba[..., :3], np.array(base.color[:3])[None, None, :] * (1 + (expected[:32] - .5) * .7)[..., None], atol=1e-7)
np.testing.assert_allclose(rgba[..., 3], .8, atol=1e-7)
np.testing.assert_allclose(roughness[..., 0], .35 + (expected[:32] - .5) * .7 * .35, atol=1e-7)
negative = source(height, 'FILL', n, 0, n, {}, ts, shared)
np.testing.assert_allclose(negative[..., 0], .5 + (expected - .5) * -.4, atol=1e-7)
assert height.procedural_height < 0, 'Negative scratch/dent height was clamped away'
tangent = source(normal, 'FILL', n, 0, n, {}, ts, shared)
np.testing.assert_allclose(np.linalg.norm(tangent[..., :3] * 2 - 1, axis=-1), 1, atol=2e-7)
tiled_normals = np.concatenate([source(normal, 'FILL', n, low, min(low + 32, n), {}, ts, {})
                                for low in range(0, n, 32)])
np.testing.assert_array_equal(tangent, tiled_normals)
whole = source(base, 'FILL', n, 0, n, {}, ts, {})
tiled = np.concatenate([source(base, 'FILL', n, low, min(low + 32, n), {}, ts, {}) for low in range(0, n, 32)])
np.testing.assert_array_equal(whole, tiled)

# Bounded Gaussian filtering has no row-edge artifacts, uses a finite halo,
# and cannot modify layer coverage.
effect = base.effects.add()
effect.kind = 'GAUSSIAN_BLUR'
effect.radius = 3
effect.strength = .65
effect.space = 'UV'   # the bounded-halo contract is UV space (Surface evaluates whole images)
for output in ts.outputs:
    output.dirty = False
effect.radius = 3
assert {output.channel for output in ts.outputs if output.dirty} == {'BASE_COLOR'}, 'Channel filter invalidated unrelated outputs'
channel_cache, geometry_cache = {}, {}
with patch.object(filters, 'apply', wraps=filters.apply) as apply:
    filtered = np.concatenate([source(base, 'FILL', n, low, min(low + 32, n), channel_cache, ts, geometry_cache) for low in range(0, n, 32)])
    # Aligned 64-row strips (halo 9): two strips serve the four requests.
    assert apply.call_count == 2, apply.call_count
    assert all(call.args[0].shape[0] <= 64 + 18 for call in apply.call_args_list), 'Unbounded filter temporary'
reference = whole.copy()
reference[..., :3] += (filters.apply(whole[..., :3], 'GAUSSIAN_BLUR', radius=3) - whole[..., :3]) * .65
np.testing.assert_allclose(filtered, reference, atol=1e-7)
np.testing.assert_allclose(filtered[..., 3], .8, atol=1e-7)
# The compositor's 16-row sweep re-filtered (16 + 2·halo) rows per tile (13x the
# image at radius 32): strips are filtered once each and only the current one
# stays in the evaluation's cache.
from open_painter.evaluation.channel_eval import band_rows
from open_painter.evaluation.operation_contracts import stack_footprint
assert band_rows(96, 4096) == 192 and band_rows(192, 4096) == 384 and band_rows(9, 4096) == 64
effect.radius = 8
wide_halo = stack_footprint([effect])
assert wide_halo == 24 and band_rows(wide_halo, n) == 64
sweep_cache = {}
with patch.object(filters, 'apply', wraps=filters.apply) as apply:
    swept = np.concatenate([source(base, 'FILL', n, low, min(low + 16, n), sweep_cache, ts, {}) for low in range(0, n, 16)])
    rows_filtered = sum(call.args[0].shape[0] for call in apply.call_args_list)
    assert apply.call_count == 2 and rows_filtered <= n + 2 * wide_halo * 2, (rows_filtered, apply.call_count)
strips = [key for key in sweep_cache if isinstance(key, tuple) and key[0] == 'filtered_channel']
assert len(strips) == 1, strips
wide = whole.copy()
wide[..., :3] += (filters.apply(whole[..., :3], 'GAUSSIAN_BLUR', radius=8) - whole[..., :3]) * .65
np.testing.assert_allclose(swept, wide, atol=1e-6)
effect.radius = 3
invert = base.effects.add()
invert.kind = 'INVERT'
inverted = source(base, 'FILL', n, 0, n, {}, ts, {})
np.testing.assert_allclose(inverted[..., :3], 1 - filtered[..., :3], atol=1e-7)
np.testing.assert_allclose(inverted[..., 3], .8, atol=1e-7)
invert.strength = 0
np.testing.assert_allclose(source(base, 'FILL', n, 0, n, {}, ts, {}), filtered, atol=1e-7)
effect.enabled = False
invert.strength = 1
pointwise_cache = {}
pointwise = source(base, 'FILL', n, 0, 32, pointwise_cache, ts, {})
np.testing.assert_allclose(pointwise[..., :3], 1 - whole[:32, ..., :3], atol=1e-7)
assert not any(isinstance(key, tuple) and key[0] == 'filtered_channel' for key in pointwise_cache)
effect.enabled = True

# Parameter conventions are visible in native controls and reused by mask effects.
effect.kind = 'DIRECTIONAL_WARP'
effect.angle = np.pi / 2
effect.radius = 6
effect.amount = -.5
effect.strength = .2
params = effect_parameters(effect)
assert abs(params['angle'] - 90) < 1e-5 and params['strength'] == -3
assert dict(effect_ui_fields('HSL'))['hue'] == 'Hue Shift'
effect.kind = 'GRADIENT_MAP'
effect.color = (1, 0, 0)
effect.color2 = (0, 0, 1)
effect.strength = 1
mapped = apply_effect(np.array([[0, .5, 1]], np.float32), effect)
np.testing.assert_allclose(mapped, [[[1, 0, 0], [.5, 0, .5], [0, 0, 1]]])

# Render UV remains authoritative for world-projected procedural fills. The two
# coincident quads have disjoint UV islands and must receive identical detail.
mesh = bpy.data.meshes.new('Coincident quads')
mesh.from_pydata([(0,0,0),(1,0,0),(1,1,0),(0,1,0)] * 2, [], [(0,1,2,3),(4,5,6,7)])
obj = bpy.data.objects.new('Procedural projection', mesh)
bpy.context.collection.objects.link(obj)
render_uv = mesh.uv_layers.new(name='Render UV')
for item, uv in zip(render_uv.data, [(0,0),(.5,0),(.5,1),(0,1),(.5,0),(1,0),(1,1),(.5,1)]):
    item.uv = uv
mesh.uv_layers.new(name='Editing UV')
mesh.uv_layers.active_index = 1
for item in mesh.uv_layers.active.data:
    item.uv = (0,0)
mesh.uv_layers['Render UV'].active_render = True
sets.assign(bpy.context.scene, ts, obj)
base.effects.clear()
base.projection = 'TRIPLANAR'
projected = source(base, 'FILL', n, 0, n, {}, ts, {})
np.testing.assert_allclose(projected[:, :n//2], projected[:, n//2:], atol=1e-6)
assert projected[..., 0].std() > .01
base.projection = 'UV'
uv_detail = source(base, 'FILL', n, 0, n, {}, ts, {})
assert np.mean(abs(uv_detail[:, :n//2] - uv_detail[:, n//2:])) > .005
procedural_mask = layer.masks.add()
procedural_mask.source = 'PROCEDURAL'
procedural_mask.procedural = 'FBM'
procedural_mask.noise_scale = 4.37
procedural_mask.seed = 19
procedural_mask.projection = 'TRIPLANAR'
projected_mask = evaluate_masks(ts, layer, n, 0, n, {}, {})
np.testing.assert_allclose(projected_mask[:, :n//2], projected_mask[:, n//2:], atol=1e-6)
assert projected_mask.std() > .01
procedural_mask.projection = 'UV'
uv_mask = evaluate_masks(ts, layer, n, 0, n, {}, {})
assert np.mean(abs(uv_mask[:, :n//2] - uv_mask[:, n//2:])) > .02
layer.masks.clear()

# Explicit paint/image masks must keep their raster source even though the mask
# model also stores a default name for its independent PROCEDURAL source mode.
mask = layer.masks.add()
mask.source = 'FILL'
mask.image = images.create('Mask image remains image', 32)
data = np.full((32, 32, 4), .37, np.float32)
data[..., 3] = 1
images.write(mask.image, data)
np.testing.assert_allclose(source(mask, 'FILL', n, 0, n, {}, ts, {})[..., 0], .37, atol=1e-7)
for output in ts.outputs:
    output.dirty = False
mask.effect.kind = 'GAMMA'
assert all(output.dirty for output in ts.outputs), 'Mask effect must invalidate every affected material channel'
open_painter.unregister()
print('PROCEDURAL_CHANNELS_PASS', flush=True)
