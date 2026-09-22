"""Actual read-only Director tools settle disposable SCENE/live-edge fields.

Run visibly: production tool routing requires a 3D View, and scratch readiness
compiles the actual Eevee material. This test makes no provider calls.
"""
import hashlib
from pathlib import Path
import sys
import time
import traceback

import bpy
import numpy as np

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
import open_painter
from open_painter.ai import candidates, session
from open_painter.authoring import coverage, document
from open_painter.authoring.schema import AuthoringError
from open_painter.core import image_manager, layer_stack, texture_set_manager as sets
from open_painter.evaluation import cache


def resources():
    return {kind:{value.as_pointer() for value in getattr(bpy.data,kind)} for kind in candidates._STORES}


def image_pixels():
    return {image.as_pointer():hashlib.sha256(np.asarray(image_manager.pixels(image)).tobytes()).hexdigest()
            for image in bpy.data.images if image.has_data}


def run():
    scene=bpy.data.scenes.new('Read-only live field coverage')
    bpy.context.window.scene=scene;scene.render.engine='BLENDER_EEVEE'
    bpy.ops.mesh.primitive_cube_add()
    obj=bpy.context.object
    ts=sets.create(scene,'Artist live masks',512);sets.assign(scene,ts,obj)
    scene.open_painter.active_texture_set=0
    scene_layer=layer_stack.add(ts,'FILL','Upward surface')
    scene_mask=scene_layer.masks.add();scene_mask.id='scene-up';scene_mask.source='SCENE'
    scene_mask.influence.kind='UP';scene_mask.influence.mode='LIVE';scene_mask.influence.resolution='64'
    edge_layer=layer_stack.add(ts,'FILL','Exposed edges')
    edge_mask=edge_layer.masks.add();edge_mask.id='edge-live';edge_mask.source='GENERATOR';edge_mask.generator='EDGE_WEAR'
    edge_mask.edge_source='LIVE';edge_mask.influence.resolution='64';edge_mask.influence.distance=.1
    edge_mask.amount=.6;edge_mask.contrast=2.;edge_mask.noise_strength=0.
    bpy.context.view_layer.update()
    area=next(area for area in bpy.context.screen.areas if area.type=='VIEW_3D')
    region=next(region for region in area.regions if region.type=='WINDOW')
    with bpy.context.temp_override(area=area,region=region):
        candidates._ready(scene,time.monotonic()+60)
        assert scene_mask.influence.image and edge_mask.influence.image
        source_coverage={layer.id:coverage.inspect(ts,layer.id) for layer in (scene_layer,edge_layer)}
        session.request=session.capture(bpy.context,mode='EXPLAIN',scope='SET',prompt='Measure the two existing placement masks')
        captured=session.request
        original=document.canonical(document.snapshot(ts));before_resources=resources();before_pixels=image_pixels()
        geometry=(obj.data.as_pointer(),obj.material_slots[0].material.as_pointer(),tuple(tuple(v.co) for v in obj.data.vertices),
                  tuple(tuple(value.uv) for value in obj.data.uv_layers.active.data),obj.select_get(),obj.hide_get())
        # Reproduce the old error on an unready scratch: cleared derived images
        # make a direct read fail even when the artist fields are already ready.
        with candidates.scratch(bpy.context,[],{}) as temporary:
            copied=next(item for item in temporary.open_painter.texture_sets if item.id==ts.id)
            # Shader callbacks can finish a small field during construction.
            # Explicitly clear only the disposable derived field to exercise
            # the missing-input guard deterministically, as on larger scenes.
            copied.layers[0].masks[0].influence.image=None
            try: coverage.inspect(copied,scene_layer.id)
            except AuthoringError as exc: assert 'analysis to finish' in str(exc)
            else: raise AssertionError('Unready scratch unexpectedly bypassed the field guard')
        assert resources()==before_resources and image_pixels()==before_pixels
        for layer in (scene_layer,edge_layer):
            report=session.tool_call(dict(request_id=captured['id'],tool='inspect_mask_coverage',
                arguments=dict(texture_set_id=ts.id,layer_id=layer.id)))
            assert 0 < report['coverage'] < 1 and report['surface_area_world']==24., report
            assert report['evaluated_mask_sha256']==source_coverage[layer.id]['evaluated_mask_sha256']
            assert abs(report['coverage']-source_coverage[layer.id]['coverage'])<1e-8
            assert document.canonical(document.snapshot(ts))==original
            assert resources()==before_resources and image_pixels()==before_pixels, 'inspection changed artist images or leaked scratch IDs'
        channels=session.tool_call(dict(request_id=captured['id'],tool='inspect_material_channels',arguments=dict(texture_set_id=ts.id)))
        assert channels['channels'][ts.id]['channels']['ROUGHNESS']['max']>=0
        assert channels['_images'] and document.canonical(document.snapshot(ts))==original
        assert resources()==before_resources and image_pixels()==before_pixels
        assert geometry==(obj.data.as_pointer(),obj.material_slots[0].material.as_pointer(),tuple(tuple(v.co) for v in obj.data.vertices),
                          tuple(tuple(value.uv) for value in obj.data.uv_layers.active.data),obj.select_get(),obj.hide_get())
        assert captured['mode']=='EXPLAIN' and captured['draft'] is None and not ts.director_history.strip('[]')
        session.release(captured);session.request=None
        print('DIRECTOR_COVERAGE_SCRATCH_SCENE_LIVE_EDGE_TOOLS_OK',flush=True)


open_painter.register()
if bpy.app.timers.is_registered(cache.tick):bpy.app.timers.unregister(cache.tick)
def execute():
    try:run()
    except BaseException:
        traceback.print_exc()
        import os
        os._exit(1)
    bpy.ops.wm.quit_blender()
bpy.app.timers.register(execute,first_interval=2.)
