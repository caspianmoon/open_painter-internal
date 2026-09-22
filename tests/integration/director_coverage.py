"""Actual native-mask evaluation and geometric area reduction, without a provider."""
from pathlib import Path
import sys

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.authoring import coverage, document, transactions
from open_painter.core import image_manager, layer_stack, texture_set_manager as sets
from open_painter.evaluation import cache

open_painter.register()
if bpy.app.timers.is_registered(cache.tick): bpy.app.timers.unregister(cache.tick)
scene = bpy.data.scenes.new('Area coverage regression')
bpy.context.window.scene = scene
mesh = bpy.data.meshes.new('Unequal area equal UV charts')
mesh.from_pydata([(0,0,0),(2,0,0),(2,2,0),(0,2,0),(3,0,0),(3.5,0,0),(3.5,.5,0),(3,.5,0)], [], [(0,1,2,3),(4,5,6,7)])
uv = mesh.uv_layers.new()
for face in mesh.polygons:
    lo, hi = ((0., .5) if face.index == 0 else (.5, 1.))
    for loop, xy in zip(face.loop_indices, ((lo,0),(hi,0),(hi,1),(lo,1))): uv.data[loop].uv = xy
obj = bpy.data.objects.new('Surface area fixture', mesh); scene.collection.objects.link(obj)
ts = sets.create(scene, 'Coverage', 512); sets.assign(scene,ts,obj)
layer = layer_stack.add(ts, 'FOLDER', 'Editable moss distribution')
image = image_manager.create('Persistent coverage source',512)
pixels = np.ones((512,512,4), np.float32)
pixels[:,:,:3] = ((np.arange(512,dtype=np.float32)+.5)/512)[None,:,None]
image_manager.write(image,pixels)
mask = layer.masks.add(); mask.id='source-mask'; mask.source='FILL'; mask.image=image
bpy.context.view_layer.update()
initial = document.snapshot(ts)
report = coverage.inspect(ts, layer.id)
assert abs(report['coverage'] - .25/4.25) < .003, report
assert report['invalid_uv_surface_fraction'] == 0 and report['opacity_excluded']
solved = coverage.reduction(ts, layer.id, .6)
assert abs(solved['measurements']['actual_ratio'] - .6) <= .06, solved
control = layer.masks.add(); layer_stack.restore(solved['mask'], control)
actual = coverage.inspect(ts,layer.id)
assert abs(actual['coverage'] / report['coverage'] - .6) <= .06
assert mask.id=='source-mask' and mask.image==image and layer.opacity==1
# CPU native stack reproduces the solver's evaluated output exactly.
assert actual['evaluated_mask_sha256'] == solved['measurements']['after_mask_sha256']
actual_buffer = coverage._buffer(ts, layer)
base_buffer = coverage._buffer(ts, layer, omit=control.id)
expected_buffer = np.clip((base_buffer - control.effect.black) / (1. - control.effect.black), 0, 1)
np.testing.assert_allclose(actual_buffer, expected_buffer, atol=1e-6, rtol=0)
second = coverage.reduction(ts,layer.id,.6)
assert second['mask']['id'] == control.id
assert second['mask']['effect']['black'] >= control.effect.black
assert second['measurements']['new_support_pixels']==0
# Ordinary document restoration reverses coverage with original source identity.
layer_stack.restore(initial['layers'][0],layer)
assert coverage.inspect(ts,layer.id)['evaluated_mask_sha256'] == report['evaluated_mask_sha256']
# Public typed recipe + constrained patch follows the same measured native path.
proposal = dict(version=1,summary='Production surface coverage integration',commands=[
    dict(op='create_surface',id='new:moss',name='Coverage surface',family='MOSS'),
    dict(op='add_mask',id='new:placement',layer_id='new:moss',name='Placement',source='NOISE',
         values=dict(seed=7,noise_scale=8,black=.1,white=.9))])
created = transactions.apply(ts,proposal,operation_id='coverage-create',expected_digest=document.digest(ts))
root_id = created['aliases']['new:moss']
root = next(item for item in ts.layers if item.id == root_id)
native_source = layer_stack.snapshot(root.masks[0])
initial_coverage = coverage.inspect(ts,root_id)
patch = dict(version=1,summary='Reduce only occupied area',commands=[dict(op='patch_surface',
    surface_id=root_id,mode='COVERAGE_ONLY',values=dict(coverage_ratio=.6))])
transactions.apply(ts,patch,operation_id='coverage-patch',expected_digest=document.digest(ts))
root = next(item for item in ts.layers if item.id == root_id)
assert layer_stack.snapshot(root.masks[0]) == native_source and root.opacity == 1
assert abs(coverage.inspect(ts,root_id)['coverage'] / initial_coverage['coverage'] - .6) <= .06
transactions.undo(ts)
assert coverage.inspect(ts,root_id)['evaluated_mask_sha256'] == initial_coverage['evaluated_mask_sha256']
# Reacquire RNA handles after the transaction reconciled their owning lists.
layer = ts.layers[0]
# Invalid UV triangles remain in denominator; automatic reduction refuses them.
uv.data[0].uv.x = -1
bpy.context.view_layer.update()
invalid = coverage.inspect(ts,layer.id)
assert invalid['invalid_uv_surface_fraction'] > .4
try: coverage.reduction(ts,layer.id,.6)
except ValueError as exc: assert 'invalid render UVs' in str(exc)
else: raise AssertionError('invalid UV area was silently discarded')
uv.data[0].uv.x = 0
# A binary full mask cannot deliver an area decrease by opacity or threshold.
image_manager.write(image,np.ones_like(pixels))
try: coverage.reduction(ts,layer.id,.6)
except ValueError as exc: assert 'cannot attain' in str(exc)
else: raise AssertionError('binary mask claimed an impossible geometric reduction')
image_manager.write(image,np.zeros_like(pixels))
try: coverage.reduction(ts,layer.id,.6)
except ValueError as exc: assert 'near zero' in str(exc)
else: raise AssertionError('near-zero source produced meaningless ratio')
# A stale live field would normally publish images during CPU evaluation.
# Inspection/compilation must reject it without changing any artist state or IDs.
stale = layer.masks.add(); stale.id='stale-field'; stale.source='SCENE'
stale.influence.kind='UP'; stale.influence.mode='LIVE'
before = document.canonical(document.snapshot(ts))
images_before = {item.as_pointer() for item in bpy.data.images}
try: coverage.inspect(ts,layer.id)
except ValueError as exc: assert 'analysis to finish' in str(exc)
else: raise AssertionError('stale live field was evaluated on the artist document')
assert document.canonical(document.snapshot(ts)) == before
assert {item.as_pointer() for item in bpy.data.images} == images_before
print('DIRECTOR_COVERAGE_AREA_NATIVE_SOLVER_OK', flush=True)
