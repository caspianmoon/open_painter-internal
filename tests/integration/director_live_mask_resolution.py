"""Short authoring regression; no rendering, inference or benchmark run."""
import sys
from pathlib import Path
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.authoring import commands
from open_painter.core import texture_set_manager as sets

open_painter.register()
bpy.ops.mesh.primitive_cube_add()
ts = sets.create(bpy.context.scene, 'Mask resolution regression', 1024)
sets.assign(bpy.context.scene, ts, bpy.context.object, 0)
base = [dict(op='add_layer', id='new:wear', name='Wear', kind='FILL'),
        dict(op='add_mask', id='new:edge', layer_id='new:wear', name='Edge', source='GENERATOR',
             values=dict(generator='EDGE_WEAR', edge_source='LIVE'))]

def compile(items, images=None):
    return commands.compile_proposal(ts, dict(version=1, summary='Regression', commands=items),
                                     'resolution-regression', images=images)['after']

assert compile(base)['layers'][-1]['masks'][0]['influence']['resolution'] == '512'
for resolution in ('128', '512'):
    after = compile(base + [
        dict(op='set_field', layer_id='new:wear', mask_id='new:edge', values=dict(resolution=resolution)),
        dict(op='set_mask', layer_id='new:wear', mask_id='new:edge', values=dict(amount=.1))])
    assert after['layers'][-1]['masks'][0]['influence']['resolution'] == resolution

identifier = 'generated:' + 'a' * 64
after = compile([
    dict(op='create_surface', id='new:metal', name='Metal', family='BARE_METAL'),
    dict(op='assign_generated_surface', layer_id='new:metal:finish', image_id=identifier,
         name='Source', purpose='No unrelated relief', roughness_range=[.4, .6], height_amplitude=0)
], {identifier: {}})
height = next(p for layer in after['layers'] if layer['type'] == 'FILL'
              for p in layer['channels'] if p['channel'] == 'HEIGHT')
assert not height['enabled'] and not height['procedural']
print('DIRECTOR_MASK_RESOLUTION_AND_ZERO_RELIEF_OK', flush=True)

# Director must share the manual UI's neutral brightness initialization.
base_filter = [dict(op='add_layer', id='new:metal', name='Metal', kind='FILL'),
    dict(op='add_filter', id='new:contrast', layer_id='new:metal', channel='BASE_COLOR',
         kind='BRIGHTNESS_CONTRAST', values=dict(contrast=.75))]
effect = compile(base_filter)['layers'][-1]['channels'][0]['effects'][0]
assert effect['amount'] == 0 and effect['contrast'] == .75
from open_painter.texture_engine.filters import apply
import numpy as np
np.testing.assert_allclose(apply(np.array([[.1]], np.float32), 'BRIGHTNESS_CONTRAST',
                               amount=effect['amount'], contrast=effect['contrast']), [[.2]], atol=1e-6)
edited = compile(base_filter + [dict(op='set_filter', layer_id='new:metal', channel='BASE_COLOR',
                                    effect_id='new:contrast', values=dict(amount=.12))])
assert edited['layers'][-1]['channels'][0]['effects'][0]['amount'] == .12
print('DIRECTOR_NEUTRAL_BRIGHTNESS_OK', flush=True)
