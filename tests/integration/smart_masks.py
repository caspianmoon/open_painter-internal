import sys
from pathlib import Path
import bpy
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.core import texture_set_manager as sets, layer_stack as stack, image_manager as images
from open_painter.smart_masks.library import PRESETS, apply
from open_painter.evaluation.mask_eval import evaluate_masks
from open_painter.ui.dock.interaction import slider_owner
open_painter.register()
ts = sets.create(bpy.context.scene, 'Masks', 512)
layer = stack.add(ts, 'FILL')
try:
    apply(ts, 'Cavity Dirt')
    raise AssertionError('Missing maps were accepted')
except ValueError as e:
    assert 'AO' in str(e) and 'CURVATURE' in str(e)
assert not layer.masks
for kind in ['AO', 'CURVATURE']:
    m = ts.baked_maps.add(); m.kind = kind; m.image = images.create(kind, 32)
    data = np.ones((32,32,4), np.float32)
    data[...,:3] = np.linspace(0,1,32)[None,:,None]
    images.write(m.image, data)
outputs = []
for preset in PRESETS:
    layer.masks.clear(); mask = apply(ts, preset[0])
    value = evaluate_masks(ts, layer, 512, 0, 512, {})
    assert np.isfinite(value).all() and value.std() > .01
    outputs.append(value)
    assert mask.name == preset[0] and len(ts.layers) == 1
assert all(not np.array_equal(a,b) for i,a in enumerate(outputs) for b in outputs[i+1:])
identifier = layer.id
stack.add(ts, 'FILL', 'Another layer')
bpy.context.scene.open_painter.blend_channel = 'METALLIC'
bpy.context.scene.open_painter.paint_channel = 'ROUGHNESS'
owner, key = slider_owner(bpy.context, ('ROW_CHANNEL', identifier))
setattr(owner, key, .35)
assert abs(stack.channel_for(ts.layers[0], 'METALLIC').opacity-.35)<1e-6
assert stack.channel_for(ts.layers[1], 'METALLIC').opacity == 1
assert bpy.context.scene.open_painter.paint_channel == 'ROUGHNESS'
open_painter.unregister()
print('SMART_MASKS_AND_INLINE_OPACITY_PASS', flush=True)
