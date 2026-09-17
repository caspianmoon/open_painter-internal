"""Disposable previews cannot resize paint, clean final output or enter saves."""
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
import bpy
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
import open_painter
from open_painter.core import texture_set_manager as sets, layer_stack as stack, image_manager as images
from open_painter.evaluation import cache, preview_policy
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.snapshots import capture
open_painter.register()
scene=bpy.context.scene
ts=sets.create(scene,'Preview authority',1024)
ts.preview_backend='CPU'  # Disposable raster proxies belong to the explicit CPU mode.
layer=stack.add(ts,'FILL')
rough=stack.channel_for(layer,'ROUGHNESS'); rough.value=.25
LayerEvaluator(scene).evaluate_texture_set(ts.id)
out=images.output(ts,'ROUGHNESS'); pointer=out.image.as_pointer(); revision=out.revision
paint=images.create('Authored paint',1024,(.4,.4,.4,1))
rough.image=paint
rough.effects.add().kind='GAUSSIAN_BLUR'
rough.effects[0].radius=12
frozen=capture(ts,.5)
assert stack.channel_for(frozen.layers[0],'ROUGHNESS').effects[0].radius == 6
assert rough.effects[0].radius == 12
rough.effects.clear(); rough.image=None; rough.value=.75
ts.preview_quality='512'
job=LayerEvaluator(scene).channel_steps(ts.id,'ROUGHNESS',preview_resolution=512)
for _ in job: pass
assert preview_policy.current(scene,ts,'ROUGHNESS')
assert out.revision == revision and out.dirty
assert tuple(out.image.size)==(1024,1024) and tuple(paint.size)==(1024,1024)
np.testing.assert_allclose(images.pixels(out.image)[...,0],.25,atol=1e-7)
proxy=preview_policy._proxies[(*cache.key_for(scene,ts),'ROUGHNESS')][2]
np.testing.assert_allclose(images.pixels(proxy)[...,0],.75,atol=1e-7)
# Serial-keyed anchor memos of earlier edits must not accumulate in the preview cache.
from open_painter.evaluation import anchors
layer.publish_output=True
copy=stack.add(ts,'FILL','Copy'); stack.channel_for(copy,'ROUGHNESS').anchor=anchors.output_id(layer.id)
def preview_memo():
    for _ in LayerEvaluator(scene).channel_steps(ts.id,'ROUGHNESS',preview_resolution=512): pass
    return preview_policy._resources[cache.key_for(scene,ts)].signatures
counts=set()
for step in range(4):
    rough.value=.5+.05*step
    memo=preview_memo()
    assert all(key[-1]==ts.edit_serial for key in memo), sorted(memo)
    counts.add(len(memo))
assert len(counts)==1 and counts!={0}, counts
stack.remove(ts,next(i for i,l in enumerate(ts.layers) if l.id==copy.id))
ts.layers[0].publish_output=False
# A later edit cancels an older proxy before publishing it.
job=LayerEvaluator(scene).channel_steps(ts.id,'ROUGHNESS',preview_resolution=512)
next(job); rough.value=.83
for _ in job: pass
assert not preview_policy.current(scene,ts,'ROUGHNESS')
# Exercise the actual scheduler through previews and final publication.
cache._pending[cache.key_for(scene,ts)]=(ts.edit_serial,monotonic()-1)
for _ in range(10000):
    cache.tick()
    if not ts.dirty: break
assert not ts.dirty
assert out.image.as_pointer()==pointer and out.revision>revision
np.testing.assert_allclose(images.pixels(out.image)[...,0],.83,atol=1e-7)
# Save while another interactive revision is pending; reload only full outputs.
rough.value=.91
for _ in LayerEvaluator(scene).channel_steps(ts.id,'ROUGHNESS',preview_resolution=512): pass
with TemporaryDirectory(prefix='op-interactive-save-') as directory:
    identifier=ts.id
    bpy.ops.wm.save_as_mainfile(filepath=directory+'/final.blend')
    assert not preview_policy._proxies
    assert not any(i.get('open_painter_disposable_preview') for i in bpy.data.images)
    bpy.ops.wm.open_mainfile(filepath=directory+'/final.blend')
    ts=next(t for t in bpy.context.scene.open_painter.texture_sets if t.id==identifier)
    assert all(tuple(o.image.size)==(1024,1024) for o in ts.outputs)
    np.testing.assert_allclose(images.pixels(images.output(ts,'ROUGHNESS').image)[...,0],.91,atol=1e-7)
# Deleting a set must release its disposable images and geometry cache.
scene=bpy.context.scene
ts.preview_quality='512'
for _ in LayerEvaluator(scene).channel_steps(ts.id,'ROUGHNESS',preview_resolution=512): pass
assert preview_policy._proxies
sets.remove(scene,next(i for i,t in enumerate(scene.open_painter.texture_sets) if t.id==ts.id))
cache.tick()
assert not preview_policy._proxies and not preview_policy._resources
assert not any(i.get('open_painter_disposable_preview') for i in bpy.data.images)
open_painter.unregister()
print('INTERACTIVE_PREVIEW_PASS',flush=True)
