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
open_painter.unregister()
print('TYPED_BUFFERS_PASS')
