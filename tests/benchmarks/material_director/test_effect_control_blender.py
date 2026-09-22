"""Actual native inverse-coating measurement control; no provider or artistic grade."""
import argparse
import json
import os
from pathlib import Path
import sys

import bpy
import numpy as np

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
sys.path[:0]=[str(HERE),str(ROOT/'src')]
import open_painter
from open_painter.authoring import document
from capture_evidence import capture_channels,sha256,write_json
from revision_measurements import compare,measurement_policy

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--fixture',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args(sys.argv[sys.argv.index('--')+1:])
a.output.mkdir(parents=True,exist_ok=False)
original_hash=sha256(a.fixture)
open_painter.register()
bpy.context.preferences.view.show_splash=False
bpy.ops.wm.open_mainfile(filepath=str(a.fixture.resolve()),use_scripts=False)
scene=bpy.context.scene
ts,layer,mask=next((ts,layer,mask) for ts in scene.open_painter.texture_sets for layer in ts.layers for mask in layer.masks
    if getattr(layer,'surface_role','')=='COATING' and getattr(mask,'surface_role','')=='PAINT_LOSS')
original=document.encode(document.snapshot(ts),digest=True)
strength=mask.strength
policy=measurement_policy()
config=dict(protocol_version=3,case_id='T01',capture_channels=True,channels=['BASE_COLOR','ROUGHNESS','METALLIC'],
    measurement_policy=policy,capture_guard_set_channels=False,target_set_ids=[ts.id])
report=dict(status='RUNNING',provider_calls=0,quality='NOT_SCORED',fixture_sha256=original_hash,
    kind='ACTUAL_NATIVE_PAINT_LOSS_MEASUREMENT_CONTROL')
try:
    mask.strength=1
    first=capture_channels(bpy.context,a.output,'strength_1',config)
    mask.strength=.5
    last=capture_channels(bpy.context,a.output,'strength_half',config)
    assert first['status']==last['status']=='CAPTURED',(first,last)
    old=first['sets'][ts.id]['effect_masks'][layer.id]
    new=last['sets'][ts.id]['effect_masks'][layer.id]
    x,y=old['effect_control'],new['effect_control']
    assert x['status']==y['status']=='MEASURED',(x,y)
    assert x['representation']==y['representation']=='PAINT_LOSS_INVERTED_SINGLE_ROOT'
    before=np.load(x['source_field']['path'],allow_pickle=False)
    after=np.load(y['source_field']['path'],allow_pickle=False)
    assert np.ptp(before)>1e-7,'Control must evaluate a nonconstant real paint-loss field'
    with np.load(last['sets'][ts.id]['surface_samples']['path'],allow_pickle=False) as loaded:
        samples={key:loaded[key] for key in loaded.files}
    observation=compare(before,after,samples,tolerance=policy['mask_absolute_tolerance'],threshold=policy['mask_threshold'],
        invalid_area_tolerance=policy['invalid_uv_area_max_fraction'])
    assert observation['mask_equal'],observation
    assert x['source_signature']==y['source_signature'],'Changing only declared strength altered source identity'
    assert y['opacity']/x['opacity']==.5
    assert old['layer_opacity']==new['layer_opacity'],'Control accidentally changed ordinary layer opacity'
    assert not np.array_equal(np.load(old['path']),np.load(new['path'])),'Inverse coating mask should change visually'
    report.update(status='PASS',source_comparison=observation,effect_opacity_ratio=.5,
        coating_layer_opacity_ratio=new['layer_opacity']/old['layer_opacity'],source_signature_equal=True,
        before=first,after=last,semantic='PAINT_LOSS',opacity_control_path=y['opacity_control_path'])
except Exception as exc:
    report.update(status='FAILED',error=repr(exc))
    import traceback;traceback.print_exc()
finally:
    mask.strength=strength
    report['material_restored']=document.encode(document.snapshot(ts),digest=True)==original
    report['source_file_unchanged']=sha256(a.fixture)==original_hash
    if not report['material_restored'] or not report['source_file_unchanged']:
        report['status']='FAILED'
    write_json(a.output/'verification.json',report)
    print('NATIVE_EFFECT_CONTROL',report['status'],flush=True)
    bpy.ops.wm.quit_blender()
