"""Real frozen-fixture v3 evidence controls; no provider or artistic grade."""
import json
import os
from pathlib import Path
import sys
import time

import bpy
import numpy as np

HERE=Path(__file__).resolve().parent
sys.path[:0]=[str(HERE),str(HERE.parents[2]/'src')]
import open_painter
from open_painter.authoring import document
from open_painter.core import layer_stack
from open_painter.evaluation import shader_preview
from capture_evidence import capture_channels, render_views, scene_record, sha256, write_json

open_painter.register()
fixture=HERE.parents[2]/'.blender-test/material-director-benchmark/fixtures-eevee-v3/A04_vase_neutral.blend'
original_hash=sha256(fixture)
bpy.ops.wm.open_mainfile(filepath=str(fixture),use_scripts=False)
scene=bpy.context.scene
ts=scene.open_painter.texture_sets[0]
layer=layer_stack.add(ts,'FILL','Synthetic evidence opacity control')
mask=layer.masks.add();mask.id='synthetic-mask';mask.name='Synthetic fixed noise';mask.source='NOISE';mask.seed=19
layer.opacity=.8
output=Path(os.environ.get('OP_MD_V3_EVIDENCE_OUTPUT',str(HERE.parents[2]/'.blender-test/material-director-benchmark/remedy-v3-harness-smoke')))
output.mkdir(parents=True,exist_ok=True)
config=dict(protocol_version=3,case_id='T04',primary_evidence=True,capture_channels=True,channels=['ROUGHNESS'])
started=time.monotonic();ready=0


def tick():
    global ready
    try:
        shader_preview.tick(scene,ts)
        ready=ready+1 if shader_preview.current(scene,ts) else 0
        if ready<5:
            assert time.monotonic()-started<120,'Preview failed to settle'
            return .1
        original_scene=scene_record(scene,[ts.id])
        a=capture_channels(bpy.context,output,'mask_before',config)
        assert a['status']=='CAPTURED',a
        layer.opacity=.4
        b=capture_channels(bpy.context,output,'mask_after',config)
        assert b['status']=='CAPTURED',b
        first=a['sets'][ts.id]['effect_masks'][layer.id]
        last=b['sets'][ts.id]['effect_masks'][layer.id]
        assert np.array_equal(np.load(first['path']),np.load(last['path'])),'Opacity altered evaluated source mask'
        assert first['source_signature']==last['source_signature'],'Source identity moved'
        assert abs(last['layer_opacity']/first['layer_opacity']-.5)<1e-6
        assert len(np.unique(np.load(first['path'])))>1,'Test source mask was constant'
        views=render_views(bpy.context,output,'after_create',config)
        assert views['status']=='CAPTURED',views
        assert {v['pose'] for v in views['views']}=={'front','back','close','circumference_90','circumference_270'}
        assert scene_record(scene,[ts.id])==original_scene,'Evidence views altered infrastructure/geometry'
        assert sha256(fixture)==original_hash,'Source fixture was modified'
        write_json(output/'verification.json',dict(status='PASS',kind='DETERMINISTIC_EVIDENCE_TEST',provider_used=False,
            artistic_score=None,fixture_sha256=original_hash,engine=scene.render.engine,views=views,
            source_mask_equal=True,opacity_ratio=last['layer_opacity']/first['layer_opacity'],rig_restored=True))
        print('V3_EVIDENCE_PASS',str(output),flush=True)
    except Exception as exc:
        write_json(output/'verification.json',dict(status='FAIL',error=repr(exc)))
        import traceback;traceback.print_exc()
    bpy.ops.wm.quit_blender()
    return None

bpy.app.timers.register(tick,first_interval=.5)
