"""Mask filter neighborhoods, all eight channels, generators and portable pixels."""
import sys
from pathlib import Path
import bpy
import numpy as np
root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root/'src'))
import open_painter
from open_painter.core import texture_set_manager as sets, layer_stack as stack, image_manager as images
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.mask_eval import evaluate_masks
from open_painter.evaluation.channel_eval import apply_effect
from open_painter.smart_materials import serializer,loader
from open_painter.constants import CHANNELS
from open_painter.utils.ids import new_id
open_painter.register()
scene=bpy.context.scene
ts=sets.create(scene,'Texture operations',512)
sets.assign(scene,ts,bpy.context.object)
layer=stack.add(ts,'FILL')
mask=layer.masks.add();mask.id=new_id();mask.source='PROCEDURAL';mask.procedural='CHECKER';mask.noise_scale=8
filter_mask=layer.masks.add();filter_mask.id=new_id();filter_mask.source='FILTER'
filter_mask.effect.kind='GAUSSIAN_BLUR';filter_mask.effect.radius=3
filter_mask.effect.space='UV'   # the bounded-halo contract is UV space (Surface evaluates whole images)
whole=evaluate_masks(ts,layer,512,0,512,{},{})
shared={}
tiled=np.concatenate([evaluate_masks(ts,layer,512,y,min(512,y+32),{},shared) for y in range(0,512,32)])
np.testing.assert_array_equal(whole,tiled)
mask_blocks = [k for k in shared if isinstance(k,tuple) and k[0]=='mask_stack']
# Aligned 64-row strips retain only their own rows: a sweep keeps n / 64 entries
# (one image in total), not an overlapping halo strip per request.
assert len(mask_blocks) == 8, len(mask_blocks)
assert all(k[-1] - k[-2] == 64 for k in mask_blocks), 'Blur cached a full-image mask'
assert sum(shared[k].shape[0] for k in mask_blocks) == 512
sweep_cache = {}
swept = np.concatenate([evaluate_masks(ts,layer,512,y,y+16,sweep_cache,shared) for y in range(0,512,16)])
np.testing.assert_array_equal(whole, swept)
assert len([k for k in shared if isinstance(k,tuple) and k[0]=='mask_stack']) == 8, 'the 16-row sweep added strips'
assert len([k for k in sweep_cache if isinstance(k,tuple) and k[0]=='mask_stack']) == 1, 'the job retained every strip'
assert whole.min()<.01 and whole.max()>.99 and ((whole>.1)&(whole<.9)).any()
filter_mask.effect.kind='INVERT'
actual=evaluate_masks(ts,layer,512,0,512,{},{})
filter_mask.enabled=False
original=evaluate_masks(ts,layer,512,0,512,{},{})
np.testing.assert_allclose(actual,1-original,atol=1e-6)
filter_mask.enabled=True
filter_mask.effect.kind='GRADIENT_MAP';filter_mask.effect.color=(1,0,0);filter_mask.effect.color2=(0,1,0)
gray=evaluate_masks(ts,layer,512,0,512,{},{})
assert gray.ndim==2
np.testing.assert_allclose(gray, .2126*(1-original)+.7152*original,atol=1e-6)
layer.masks.clear()
for channel in layer.channels:
    channel.enabled=True
stack.channel_for(layer,'EMISSION').color=(.2,.1,.05,1)
stack.channel_for(layer,'OPACITY').value=.35
ts.anisotropy=.4;ts.coat_weight=.6
LayerEvaluator(scene).evaluate_texture_set(ts.id)
assert {o.channel for o in ts.outputs}==set(CHANNELS) and len(CHANNELS)==8
np.testing.assert_allclose(images.pixels(images.output(ts,'OPACITY').image)[0,0,0],.35,atol=1e-6)
np.testing.assert_allclose(images.pixels(images.output(ts,'EMISSION').image)[0,0,:3],[.2,.1,.05],atol=1e-6)
bsdf=next(n for n in ts.material.node_tree.nodes if n.get('role')=='Surface')
assert bsdf.inputs['Alpha'].is_linked and bsdf.inputs['Emission Color'].is_linked
assert bsdf.inputs['Tangent'].is_linked
path=root/'.blender-test/texture-operations.openpaintmaterial';path.unlink(missing_ok=True)
serializer.save(ts,path)
copy=sets.create(scene,'Imported operations',512)
loader.apply(copy,path)
assert abs(copy.anisotropy-.4)<1e-6 and abs(copy.coat_weight-.6)<1e-6
LayerEvaluator(scene).evaluate_texture_set(copy.id)
for channel in CHANNELS:
    np.testing.assert_allclose(images.pixels(images.output(ts,channel).image),images.pixels(images.output(copy,channel).image),atol=1e-6)
# Generator with large seed must have local breakup, without turning into strips.
layer=copy.layers[0]
m=layer.masks.add();m.id=new_id();m.source='GENERATOR';m.generator='NOISE';m.seed=2_000_000_000;m.noise_scale=20
noise=evaluate_masks(copy,layer,512,0,512,{},{})
assert np.std(np.diff(noise,axis=0))>.0001 and np.std(np.diff(noise,axis=1))>.0001
open_painter.unregister()
print('TEXTURE_OPERATIONS_PASS: filter neighborhoods and order, mask luminance, eight texture channels, scalar surface persistence, large seeds')
