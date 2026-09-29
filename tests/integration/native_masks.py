"""Production nested masks, prefix references and edits use native C planning."""
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

import bpy
import gpu
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tests/integration'))
import open_painter
from open_painter.core import image_manager, layer_stack as stack, mask_stack, material_manager
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import shader_preview
from open_painter.evaluation.shader_graph import Graph
from open_painter.evaluation.engine import runtime
from open_painter.evaluation.engine.compiler import EngineGraph
from open_painter.evaluation.shader_effects import UnsupportedEffect
from open_painter.native_bridge import masks as native_masks
from open_painter.utils.ids import new_id
from time import perf_counter


def ready(scene, ts):
    deadline = perf_counter() + 90
    while perf_counter() < deadline:
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert not info['error'], info
        if shader_preview.current(scene, ts): return
    raise AssertionError(info)


def engine_channels(ts):
    return {out.channel: np.array(gpu.texture.from_image(out.image).read(), np.float32)
            .reshape(512, 512, 4)[..., :3].copy() for out in ts.outputs}


def compare(label, scene, ts):
    from open_painter.evaluation.evaluator import LayerEvaluator
    from open_painter.export import padding
    actual = engine_channels(ts)
    covered = padding.ownership(ts, 512).covered
    evaluator, errors = LayerEvaluator(scene), {}
    for channel, pixels in actual.items():
        result = evaluator.evaluate_channel(ts.id, channel)
        image = result.image if hasattr(result, 'image') else result
        expected = np.clip(image_manager.pixels(image)[..., :3], 0, 1)
        errors[channel] = float(np.abs(pixels-expected)[covered].max())
    assert max(errors.values()) <= 2e-3, (label, errors)
    entry = shader_preview._sessions[(scene.as_pointer(), ts.id)]
    entry['engine'].published.clear()
    entry['dirty'] = True
    return errors

open_painter.register()
gpu.init()
runtime.PRECISE = True
try:
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_plane_add(size=2)
    scene = bpy.context.scene
    ts = sets.create(scene, 'Native mask plan', 512)
    sets.assign(scene, ts, bpy.context.object)
    base = stack.add(ts, 'FILL', 'Base')
    stack.channel_for(base, 'BASE_COLOR').color = (.1,.2,.3,1.)
    coat = stack.add(ts, 'FILL', 'Grouped coat')
    stack.channel_for(coat, 'BASE_COLOR').color = (.9,.6,.3,1.)
    layer_id = coat.id
    axes = np.meshgrid((np.arange(512,dtype=np.float32)+.5)/512,
                       (np.arange(512,dtype=np.float32)+.5)/512)
    images = []
    for index, axis in enumerate(axes):
        image = image_manager.create('Mask gradient '+str(index), 512)
        pixels = np.ones((512,512,4),np.float32)
        pixels[...,:3] = axis[...,None]
        image_manager.write(image,pixels,pack=False)
        images.append(image)
    def layer(): return next(l for l in ts.layers if l.id==layer_id)
    def get(identifier): return next(m for m in layer().masks if m.id==identifier)
    def add(source, parent='', **fields):
        mask=layer().masks.add()
        mask.id,mask.name,mask.source,mask.parent_id=new_id(),source,source,parent
        for key,value in fields.items(): setattr(mask,key,value)
        return mask.id
    prefix=add('FILL',image=images[0],strength=.4)
    outer=add('GROUP',operation='MULTIPLY',strength=.7,black=.1,white=.95)
    add('FILL',outer,image=images[0])
    nested=add('GROUP',outer,operation='MULTIPLY',strength=.8)
    add('FILL',nested,image=images[1])
    levels=add('FILTER',nested)
    get(levels).effect.kind='LEVELS'
    get(levels).effect.black=.12
    get(levels).effect.white=.87
    blur=add('FILTER',nested)
    get(blur).effect.kind='BLUR'
    get(blur).effect.radius=2.
    add('GROUP',outer,operation='MIN',strength=.3)  # empty group's value is one
    disabled=add('GROUP',enabled=False)
    add('FILL',disabled,image=images[1],invert=True)
    add('ANCHOR')
    tail=add('FILL',image=images[1],operation='ADD',strength=.2)
    # A separate mask consumes a stage inside the nested group, and another
    # consumes a top-level stage that can be reused as a continuation.
    follower=stack.add(ts,'FILL','Published mask consumer')
    for target in (levels,prefix):
        mask=follower.masks.add()
        mask.id,mask.source=new_id(),'REFERENCE'
        mask.reference_layer=layer_id
        mask.reference_channel='MASK'
        mask.reference_mask=target
        mask.operation='MULTIPLY'
    reference_id=follower.id
    material_manager.repair(ts)
    errors={}
    plan_calls=0
    original=native_masks.plan
    def observed(*args,**kwargs):
        global plan_calls
        plan_calls+=1
        return original(*args,**kwargs)
    def check(label):
        with patch.object(native_masks,'plan',observed), \
             patch.object(Graph,'_mask_walk',side_effect=AssertionError('legacy mask walk used')), \
             patch.object(Graph,'_mask_chain',side_effect=AssertionError('legacy mask chain used')):
            ready(scene,ts)
        errors[label]=max(compare(label,scene,ts).values())
        ready(scene,ts)
    check('nested_prefixes')
    assert plan_calls>0
    engine=shader_preview.session_engine(scene,ts)
    rebuilds=engine._native_graph.rebuilds
    compiles=runtime.device().compiles
    count=plan_calls
    before_numeric = engine_channels(ts)['BASE_COLOR']
    get(outer).strength=.36
    get(levels).effect.gamma=.7
    check('numeric')
    assert float(abs(engine_channels(ts)['BASE_COLOR'] - before_numeric).max()) > .01
    assert plan_calls==count and runtime.device().compiles==compiles
    assert engine._native_graph.rebuilds==rebuilds
    get(outer).enabled=False
    check('disabled_group')
    get(outer).enabled=True
    follower=next(l for l in ts.layers if l.id==reference_id)
    follower.masks[0].reference_stage='PRE_FILTER'
    check('pre_filter_prefix')
    before_reorder = engine_channels(ts)['BASE_COLOR']
    # Move past the anchor and disabled subtree, then past the active group.
    # Crossing only a skipped entry would leave the effective order unchanged.
    while next(i for i,m in enumerate(layer().masks) if m.id==tail) > next(i for i,m in enumerate(layer().masks) if m.id==outer):
        mask_stack.move(layer(),next(i for i,m in enumerate(layer().masks) if m.id==tail),-1)
    stack.invalidate(ts)
    check('reordered_group')
    assert float(abs(engine_channels(ts)['BASE_COLOR'] - before_reorder).max()) > .01
    # Force this one spatial mask through its existing CPU compatibility
    # boundary: it must receive the nested run, excluding the outer prefix.
    shader_preview.clear()
    with patch.object(EngineGraph,'spatial_effect',side_effect=UnsupportedEffect('CPU prefix fixture')):
        check('cpu_filter_prefix')
    actual=engine_channels(ts)
    try:
        os.environ['OP_NATIVE_ENGINE']='0'
        shader_preview.clear()
        ready(scene,ts)
        for channel,value in engine_channels(ts).items():
            np.testing.assert_allclose(value,actual[channel],atol=2e-3,rtol=0)
    finally:
        os.environ.pop('OP_NATIVE_ENGINE',None)
    print('NATIVE_MASKS_PASS',json.dumps(dict(native_plan_calls=plan_calls,cases=errors,
          numeric_rebuilds=0,numeric_compiles=0,legacy_gpu_parity=True)),flush=True)
finally:
    open_painter.unregister()
