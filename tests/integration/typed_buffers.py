"""Optimized scalar composition equals the preserved RGB reference path."""
import sys
from pathlib import Path
from unittest.mock import patch
import bpy
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.core import texture_set_manager as sets, layer_stack as stack, image_manager as images
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation import buffers
from open_painter.smart_materials import composer
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'performance'))
from fixtures import synthetic_maps

open_painter.register()
scene = bpy.context.scene
ts = sets.create(scene, 'Typed buffers', 512)
synthetic_maps(ts)
composer.apply(ts, 'Worn Painted Metal')
evaluator = LayerEvaluator(scene)
for group_mode in ('ISOLATED', 'PASSTHROUGH'):
    for layer in ts.layers:
        if layer.type == 'FOLDER':
            layer.group_mode = group_mode
            layer.opacity = .63
    evaluator.evaluate_texture_set(ts.id, force=True)
    expected = {out.channel: images.pixels(out.image) for out in ts.outputs}
    with patch.object(buffers, 'scalar_compatible', return_value=False):
        evaluator.evaluate_texture_set(ts.id, force=True)
    for out in ts.outputs:
        np.testing.assert_array_equal(images.pixels(out.image), expected[out.channel])

# A Surface-space blur (the effect default) needs the whole image plus the seam
# map, so the scalar path declines it instead of expanding an unbounded footprint.
bpy.ops.mesh.primitive_cube_add(size=2)
cube = bpy.context.object
bpy.ops.object.mode_set(mode='EDIT'); bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.uv.smart_project(island_margin=.04); bpy.ops.object.mode_set(mode='OBJECT')
surface = sets.create(scene, 'Surface blur', 512)
sets.assign(scene, surface, cube)
grain = stack.add(surface, 'FILL', 'Grain')
rough = stack.channel_for(grain, 'ROUGHNESS')
rough.procedural = 'FBM'
blur = rough.effects.add(); blur.kind = 'GAUSSIAN_BLUR'; blur.radius = 3
assert blur.space == 'SURFACE'
props = {layer.id: stack.channel_for(layer, 'ROUGHNESS') for layer in surface.layers}
assert not buffers.scalar_compatible('ROUGHNESS', surface.layers, props)
evaluator.evaluate_texture_set(surface.id, force=True)
surface_rough = images.pixels(images.output(surface, 'ROUGHNESS').image)[..., 0]
assert surface_rough.std() > 1e-3 and 0 <= surface_rough.min() <= surface_rough.max() <= 1
# In UV space the footprint is bounded again, and the scalar path still matches the reference.
blur.space = 'UV'
assert buffers.scalar_compatible('ROUGHNESS', surface.layers, props)
evaluator.evaluate_texture_set(surface.id, force=True)
uv_rough = images.pixels(images.output(surface, 'ROUGHNESS').image)[..., 0].copy()
with patch.object(buffers, 'scalar_compatible', return_value=False):
    evaluator.evaluate_texture_set(surface.id, force=True)
np.testing.assert_allclose(images.pixels(images.output(surface, 'ROUGHNESS').image)[..., 0], uv_rough, atol=1e-6)
open_painter.unregister()
print('TYPED_BUFFERS_PASS')
