"""Deterministic authored engine diagnostics, separate from live Director results.

Fixture copies receive disclosed nominal-size assumptions. Authored calibration
uses public commands and is never an artist reference or provider input.
"""
from pathlib import Path
import argparse
import copy
import json
import os
import sys
import time

import bpy

ROOT = Path(__file__).resolve().parents[3]
PRODUCT_ROOT = Path(os.environ.get('OP_MD_PRODUCT_ROOT', ROOT)).resolve()
sys.path[:0] = [str(PRODUCT_ROOT/'src'),str(Path(__file__).parent)]
import open_painter
from open_painter.authoring import context as authoring, document, physical_space, regions, transactions
from open_painter.evaluation import cache, shader_preview
from open_painter.geometry.provenance import status as map_status
from capture_evidence import sha256, write_json, fixed_rig_check, config_for, scene_record

CASES = {
 'T01': ('A01_toolbox_neutral', .55),
 'T02': ('A02_chair_neutral', .90),
 'T03': ('A05_boots_neutral', .40),
 'T04': ('A04_vase_neutral', .40),
 'T05': ('A06_rocks_neutral', 2.0),
}
LABEL = 'DETERMINISTIC_AUTHORED_ENGINE_CALIBRATION'


def source_build():
    from runner import product_fingerprint
    loaded=Path(open_painter.__file__).resolve()
    if not loaded.is_relative_to((PRODUCT_ROOT/'src').resolve()):
        raise ValueError('Calibration imported Open Painter outside the requested product checkout')
    fingerprint=product_fingerprint(PRODUCT_ROOT)
    expected=os.environ.get('OP_MD_CALIBRATION_PRODUCT_SHA256')
    if expected and fingerprint['source_sha256']!=expected:
        raise ValueError('Calibration product bytes differ from the requested checkout pin')
    return dict(product_root=str(PRODUCT_ROOT),loaded_module=str(loaded),**fingerprint,
        status='PINNED_PRODUCT_SNAPSHOT' if (PRODUCT_ROOT/'.benchmark_product_pin.json').is_file() else 'UNFROZEN_DEVELOPMENT_DIAGNOSTIC',
        authoring_script_sha256=sha256(Path(__file__)),
        note='Match source_sha256 to the compared improved product before claiming same-build calibration.')


def prepare(fixtures, output, cases):
    output.mkdir(parents=True,exist_ok=True)
    for case in cases:
        stem, nominal = CASES[case]
        source, target = fixtures/(stem+'.blend'), output/(stem+'.blend')
        if target.exists():
            raise ValueError(f'Refuse to overwrite prepared fixture: {target}')
        source_hash = sha256(source)
        metadata = json.loads(source.with_suffix('.json').read_text(encoding="utf-8"))
        if metadata['status'] != 'READY' or metadata['sha256'] != source_hash:
            raise ValueError('Source fixture is not the unchanged READY fixture')
        bpy.ops.wm.open_mainfile(filepath=str(source))
        scene = bpy.context.scene
        frozen = config_for(scene)
        if fixed_rig_check(scene,frozen)['status'] != 'PASS':
            raise ValueError('Source fixed Eevee rig did not validate')
        before = scene_record(scene,frozen['target_set_ids'])
        declarations = {}
        for ts in scene.open_painter.texture_sets:
            declarations[ts.id] = physical_space.declare(ts,nominal/2.,
                f'Benchmark v3 nominal size declaration: longest fixture volume {nominal:g} m; '
                'explicit diagnostic assumption, not source metrology. Original normalized volume is 2 world units.')
        frozen['protocol_version'] = 3
        frozen['case_id'] = case
        scene['md_benchmark_config'] = json.dumps(frozen,sort_keys=True)
        after = scene_record(scene,frozen['target_set_ids'])
        if before != after:
            raise ValueError('Size declaration unexpectedly changed protected scene infrastructure or geometry')
        maps = {ts.id:{m.kind:map_status(ts,m) for m in ts.baked_maps} for ts in scene.open_painter.texture_sets}
        if any(value!='Ready' for values in maps.values() for value in values.values()):
            raise ValueError(f'Size declaration changed bake readiness: {maps}')
        bpy.ops.wm.save_as_mainfile(filepath=str(target))
        metadata.update(protocol='five_asset_eevee_v3',schema_version=3,status='READY',sha256=sha256(target),
            source_v2_fixture_sha256=source_hash,source_v2_fixture=str(source),
            physical_space=declarations,physical_size_assumption=dict(kind='NOMINAL_DIAGNOSTIC_DECLARATION',
                longest_dimension_m=nominal,source_measurement_verified=False),
            protected_scene_unchanged=True,bake_status_after_declaration=maps)
        write_json(target.with_suffix('.json'),metadata)
        assert sha256(source)==source_hash
        print('V3_FIXTURE_READY',case,str(target),flush=True)


def inventory(fixtures, output, cases):
    output.mkdir(parents=True,exist_ok=True)
    for case in cases:
        stem,_ = CASES[case]
        bpy.ops.wm.open_mainfile(filepath=str(fixtures/(stem+'.blend')))
        result = {}
        for ts in bpy.context.scene.open_painter.texture_sets:
            bindings = authoring.resolve_bindings(authoring.bindings(bpy.context,ts))
            result[ts.id] = dict(name=ts.name, objects={key:obj.name_full for key,obj in bindings.items()},
                physical_space=physical_space.capture(ts),regions=regions.capture(ts,bindings))
        write_json(output/(case+'_regions.json'),result)


def surface(alias, family, name, *, region_id=None, **values):
    result = dict(op="create_surface", id="new:"+alias, name=name, family=family, values=values)
    if region_id: result["region_id"] = region_id
    return result


def mask(alias, layer, name, source, **values):
    return dict(op="add_mask",id="new:"+alias,layer_id="new:"+layer,name=name,source=source,values=values)


def height_mask(alias, layer, lo, hi):
    return [mask(alias,layer,"Declared relative height", "SCENE",black=lo,white=hi,invert=True),
            dict(op="set_field",layer_id="new:"+layer,mask_id="new:"+alias,
                 values=dict(kind="WORLD_HEIGHT",bounds=[0,2],direction=[0,0,1],resolution="512",falloff="LINEAR"))]


def authored_commands(case, ts, objects):
    # These are intentionally explicit authored diagnostic choices for these
    # fixtures. This module is never imported by production or provider context.
    result, assumptions = [], []
    if case == "T01":
        result.append(surface("paint","PAINTED_METAL","Petrol painted steel",color=[.025,.115,.15,1],wear_coverage=.12,paint_loss_opacity=.8))
        catalog = regions.capture(ts,objects)
        brass_regions = []
        for index,entry in enumerate(item for item in catalog if item['kind']=='ASSIGNMENT'):
            obj = objects[entry['object_id']]
            if obj.name.endswith("_handle"):
                result.append(surface("wood","WOOD","Hardwood handle",region_id=entry['id'],color=[.12,.047,.014,1],period_m=.003,frame="COMPONENT"))
            elif "handle_latch" in obj.name:
                brass_regions.append(entry['id'])
        result.append(surface("brass","BARE_METAL","Shared brass latch finish",color=[.55,.32,.075,1],roughness_range=[.28,.38]))
        for index, identifier in enumerate(brass_regions):
            result.append(dict(op='add_region_mask',id='new:brasspart'+str(index),layer_id='new:brass',name='Brass part '+str(index+1),region_id=identifier))
            if index:
                result.append(dict(op='set_mask',layer_id='new:brass',mask_id='new:brasspart'+str(index),values=dict(operation='MAX')))
        result.extend([surface("grime","MUD","Protected recess grime",color=[.018,.012,.007,1],roughness_range=[.72,.8],height_m=.00001,opacity=.12),
            mask("recesses","grime","AO recess placement","BAKED",map_type="AO",invert=True,black=.03,white=.85)])
        assumptions.append("Calibration chooses the fixture's explicitly separated handle/latch objects by authored names; no labels or commands enter Director inputs.")
    elif case == "T02":
        slot = ts.assignments[0].slot_index
        if slot == 0:
            finish=dict(color=[.095,.037,.011,1],period_m=.003,frame="WORLD",height_m=.000012)
            result.append(surface("wood","WOOD","Fore-aft walnut rails and arms",axis_u=[1,0,0],axis_v=[0,1,0],**finish))
            assignment=next(item for item in regions.capture(ts,objects) if item['kind']=='ASSIGNMENT' and item['slot_index']==0)
            upright_regions=[]
            for lower,upper in (([-1,-1.1,0],[1,-.70,1.55]),([-1,.45,0],[1,1.1,1.75])):
                entry=regions.query(ts,objects,dict(object_id=assignment['object_id'],slot_index=0,space='WORLD',
                    bounds_min=lower,bounds_max=upper,selection='CENTROID'),objects.region_registry)
                upright_regions.append(entry['id'])
            result.append(surface("uprights","WOOD","Query-selected walnut uprights",axis_u=[1,0,0],axis_v=[0,.12,.9927738917],**finish))
            for index,identifier in enumerate(upright_regions):
                result.append(dict(op='add_region_mask',id='new:upright'+str(index),layer_id='new:uprights',
                    name='Authored geometric upright '+str(index+1),region_id=identifier))
                if index:
                    result.append(dict(op='set_mask',layer_id='new:uprights',mask_id='new:upright'+str(index),values=dict(operation='MAX')))
            crossbars=[entry for entry in regions.capture(ts,objects) if entry['kind']=='CONNECTED_COMPONENT'
                and entry['bounds_world']['maximum'][0]-entry['bounds_world']['minimum'][0]>1.2
                and entry['bounds_world']['maximum'][1]-entry['bounds_world']['minimum'][1]<.1]
            if len(crossbars)!=1:
                raise ValueError('Calibration crossbar geometry differs from the disclosed frozen fixture')
            result.append(surface("crossbar","WOOD","Crosswise walnut stretcher",region_id=crossbars[0]['id'],
                axis_u=[0,1,0],axis_v=[1,0,0],**finish))
            assumptions.append("Three authored WORLD direction groups fit the unchanged native texture-input budget: fore/aft rails and arms, two bounded upright regions, and the exact crosswise stretcher component. Uprights share a disclosed slight rearward tilt; whole-face query boundaries at curved junctions are an approximation, not artist-verified per-member segmentation. These private calibration choices are never supplied to Director.")
        else:
            result.append(surface("fabric","WOVEN_FABRIC","Burnt orange woven upholstery",color=[.44,.105,.022,1],period_m=.001,height_m=.000025))
        assumptions.append("Nominal 1 mm weave may be under-resolved in the existing 1024 atlas; resolution is not silently increased.")
    elif case == "T03":
        result.extend([surface("rubber","RUBBER","Mustard rubber",color=[.56,.31,.023,1],roughness_range=[.48,.56]),
            surface("sole","RUBBER","Matte black sole",color=[.009,.009,.008,1],roughness_range=[.7,.8])])
        result.extend(height_mask("soleheight","sole",.075,.10))
        result.append(surface("mud","MUD","Independent wet mud",color=[.032,.016,.007,1],roughness_range=[.12,.22],period_m=.005,height_m=.0002))
        result.extend(height_mask("mudheight","mud",.14,.27))
        result.append(mask("mudvariation","mud","World-space mud breakup","PROCEDURAL",procedural="FBM",operation="MULTIPLY",projection="TRIPLANAR",
            scale=[1,1],noise_scale=18,seed=17,black=.22,white=.65))
        assumptions.append("Sole coverage is an explicit face-independent height approximation, not verified semantic sole segmentation. Mud placement is a native editable height/noise stack.")
        assumptions.append("Breakup uses ordinary PROCEDURAL/FBM with native TRIPLANAR world projection. The legacy NOISE source ignores projection; the earlier request to change it was a no-op, preserved separately. This authored calibration does not repair an AI result. Wet/dry variants retain this same mask and seed.")
    elif case == "T04":
        result.extend([surface("glaze","CERAMIC_GLAZE","Quiet ivory glaze",color=[.8,.75,.64,1],roughness_range=[.14,.18],height_m=.000001),
            dict(op="create_bands",id="new:bands",name="Two cobalt bands",centers=[.7,.78],width=.015,color=[.008,.035,.32,1],roughness=.2),
            dict(op="create_bands",id="new:rim",name="Narrow metallic gold mouth",centers=[.995],width=.01,color=[.65,.38,.07,1],metallic=1,roughness=.23)])
    else:
        result.extend([surface("stone","STONE","Pale sandstone",color=[.58,.49,.34,1],period_m=.003,height_m=.00008),
            surface("damp","MUD","Recess dampness",color=[.09,.066,.034,1],roughness_range=[.28,.39],height_m=.000015,opacity=.27),
            mask("dampcavity","damp","Deep ambient cavities","BAKED",map_type="AO",invert=True,black=.03,white=.7),
            surface("moss","MOSS","Editable upward moss",color=[.12,.19,.022,1],period_m=.002,height_m=.00015),
            mask("mossup","moss","Upward eligibility","SCENE",black=.32,white=.8),
            dict(op="set_field",layer_id="new:moss",mask_id="new:mossup",values=dict(kind="UP",direction=[0,0,1],resolution="512")),
            mask("mossbreakup","moss","Fixed distribution breakup","NOISE",operation="MULTIPLY",noise_scale=16,seed=29,black=.15,white=.7),
            mask("mossshelter","moss","Shelter modulation","BAKED",operation="MULTIPLY",map_type="AO",invert=True,strength=.3)])
        assumptions.append("Upward eligibility plus AO shelter modulation is a declared geometric diagnostic, not an artist-confirmed ecological ground truth.")
    return result, assumptions


def settle(scene):
    from open_painter.evaluation import field_jobs
    started = time.monotonic()
    while time.monotonic()-started < 180:
        field_jobs.tick(scene,budget=.05)
        complete = True
        for ts in scene.open_painter.texture_sets:
            shader_preview.tick(scene,ts)
            info = shader_preview.diagnostics(scene,ts)
            if info.get('error'): raise RuntimeError(info['error'])
            complete &= shader_preview.current(scene,ts)
        if complete: return
    raise TimeoutError('Native shader preview did not settle')


def checkpoint(scene, out, label, config):
    import numpy as np
    from capture_evidence import capture_channels, render_views, material_state
    settle(scene)
    views = render_views(bpy.context,out,label,config)
    if views['status']!='CAPTURED': raise RuntimeError(views)
    channels = capture_channels(bpy.context,out,label,config)
    if channels['status']!='CAPTURED': raise RuntimeError(channels)
    stats = {}
    for identifier,entry in channels['sets'].items():
        valid = np.load(entry['valid_samples']['path'])
        stats[identifier]={}
        for name,record in entry['channels'].items():
            values=np.load(record['path'])[valid]
            stats[identifier][name]=dict(finite=bool(np.isfinite(values).all()),samples=len(values),
                minimum=float(values.min()),maximum=float(values.max()),mean=float(values.mean()),
                percentile01=float(np.quantile(values,.01)),percentile99=float(np.quantile(values,.99)))
    # CPU evidence may republish raster outputs; restore the ordinary live graph
    # before saving the editable material and recording its backend.
    settle(scene)
    write_json(out/(label+'_state.json'),material_state(scene,config))
    write_json(out/(label+'_channels.json'),channels)
    write_json(out/(label+'_channel_statistics.json'),stats)
    write_json(out/(label+'_views.json'),views)
    bpy.ops.file.pack_all()
    bpy.ops.wm.save_as_mainfile(filepath=str(out/(label+'.blend')))
    return dict(views=views,channels=channels,channel_statistics=stats,saved_blend=str(out/(label+'.blend')))


def wet_dry_diagnostic(out, first, second):
    """Measure the actual saved captures; no inferred equality from controls."""
    import numpy as np
    report=dict(track=LABEL,quality='NOT_SCORED',reviewer='PENDING',provider_calls=0,texture_sets={})
    for identifier,left in first['channels']['sets'].items():
        right=second['channels']['sets'][identifier]
        valid=np.load(left['valid_samples']['path'],allow_pickle=False)
        if not np.array_equal(valid,np.load(right['valid_samples']['path'],allow_pickle=False)):
            raise ValueError('Wet/dry calibration changed valid surface samples')
        entry=dict(masks={},channels={});mud=None
        for mask_id,mask in left['effect_masks'].items():
            other=right['effect_masks'][mask_id]
            old=np.load(mask['path'],allow_pickle=False);new=np.load(other['path'],allow_pickle=False)
            observed=dict(name=mask['layer_name'],actual_buffers_identical=bool(np.array_equal(old,new)),
                maximum_delta=float(np.abs(old-new).max()),source_signatures_equal=mask['source_signature']==other['source_signature'],
                opacity_equal=mask['layer_opacity']==other['layer_opacity'])
            entry['masks'][mask_id]=observed
            if not observed['actual_buffers_identical'] or not observed['source_signatures_equal'] or not observed['opacity_equal']:
                raise ValueError('Wet/dry calibration changed an existing placement mask')
            if mask['layer_name']=='Independent wet mud':mud=old
        if mud is None:raise ValueError('Wet/dry calibration lacks its independent mud mask')
        outside,inside=valid&(mud<=1e-6),valid&(mud>.5)
        entry.update(valid_outside_mud_samples=int(outside.sum()),valid_occupied_mud_samples=int(inside.sum()))
        for channel,record in left['channels'].items():
            old=np.load(record['path'],allow_pickle=False)[...,:3]
            new=np.load(right['channels'][channel]['path'],allow_pickle=False)[...,:3]
            maximum=float(np.abs(old[outside]-new[outside]).max())
            entry['channels'][channel]=dict(outside_mud_maximum_delta=maximum,
                occupied_before_mean=float(old[inside].mean()),occupied_after_mean=float(new[inside].mean()),
                occupied_maximum_delta=float(np.abs(old[inside]-new[inside]).max()))
            if maximum>1e-6:raise ValueError('Wet/dry revision changed values outside mud: '+channel)
        report['texture_sets'][identifier]=entry
    report['blend_sha256']={Path(stage['saved_blend']).name:sha256(Path(stage['saved_blend'])) for stage in (first,second)}
    write_json(out/'wet_to_dry_invariants.json',report)
    return report


def author(fixtures, output, cases):
    from capture_evidence import material_state
    failures=[]
    for case in cases:
        stem,_=CASES[case]
        fixture=fixtures/(stem+'.blend'); out=output/case
        if out.exists(): raise ValueError(f'Refuse to overwrite calibration attempt: {out}')
        out.mkdir(parents=True)
        record=dict(track=LABEL,case_id=case,status='RUNNING',quality='NOT_SCORED',reviewer='PENDING',
            authored_by='Deterministic public-authoring script; not a human-authored reference',
            source_build=source_build(),
            provider_calls=0,source_fixture=str(fixture),fixture_sha256=sha256(fixture),
            excluded_from_ai_inputs=True,excluded_from_live_benchmark_results=True,
            assumptions=[],receipts=[],checkpoints={})
        write_json(out/'calibration.json',record)
        try:
            bpy.ops.wm.open_mainfile(filepath=str(fixture))
            scene=bpy.context.scene; config=config_for(scene)
            assert config.get('protocol_version')==3
            assert fixed_rig_check(scene,config)['status']=='PASS'
            protected=scene_record(scene,config['target_set_ids'])
            record['physical_space']={ts.id:physical_space.capture(ts) for ts in scene.open_painter.texture_sets}
            record['resolution_diagnostics']={}
            for ts in scene.open_painter.texture_sets:
                saved=authoring.bindings(bpy.context,ts)
                objects=regions.bindings(authoring.resolve_bindings(saved),{})
                commands,assumptions=authored_commands(case,ts,objects)
                write_json(out/(ts.id+'_authored_regions.json'),[entry for entry in regions.capture(ts,objects) if entry['kind']=='SPATIAL_QUERY'])
                proposal=dict(version=1,summary='Explicit deterministic engine calibration, not an AI result',commands=commands)
                write_json(out/(ts.id+'_authored_commands.json'),proposal)
                receipt=transactions.apply(ts,proposal,operation_id='calibration-'+case+'-'+ts.id,
                    expected_digest=document.digest(ts),objects=objects)
                record['receipts'].append(receipt['id'])
                record['assumptions'].extend(assumptions)
                record['resolution_diagnostics'][ts.id]=physical_space.resolution_diagnostic(ts,.001)
            record['checkpoints']['authored']=checkpoint(scene,out,'after_create',config)
            if case=='T03':
                ts=scene.open_painter.texture_sets[0]
                root=next(layer for layer in ts.layers if layer.type=='FOLDER' and layer.name=='Independent wet mud')
                proposal=dict(version=1,summary='Deterministic wet-to-dry appearance calibration',commands=[dict(
                    op='patch_surface',surface_id=root.id,mode='APPEARANCE_ONLY',
                    values=dict(color=[.2,.12,.045,1],roughness_range=[.78,.9]))])
                receipt=transactions.apply(ts,proposal,operation_id='calibration-'+case+'-dry',expected_digest=document.digest(ts))
                record['receipts'].append(receipt['id'])
                record['checkpoints']['dry_mud']=checkpoint(scene,out,'after_refine_1',config)
                record['wet_to_dry_invariants']=wet_dry_diagnostic(out,record['checkpoints']['authored'],record['checkpoints']['dry_mud'])
            assert protected==scene_record(scene,config['target_set_ids']), 'Calibration changed protected scene'
            assert sha256(fixture)==record['fixture_sha256']
            assert source_build()==record['source_build'], 'Product or calibration script changed during calibration'
            record['status']='AUTHORED_AND_CAPTURED'
            record['protected_scene_unchanged']=True
        except Exception as exc:
            import traceback
            record.update(status='FAILED',error=f'{type(exc).__name__}: {exc}',traceback=traceback.format_exc())
            failures.append(case)
            try: bpy.ops.wm.save_as_mainfile(filepath=str(out/'failure.blend'))
            except Exception: pass
        write_json(out/'calibration.json',record)
        print('CALIBRATION_RESULT',case,record['status'],record.get('error',''),flush=True)
    if failures: raise RuntimeError('Calibration failures: '+', '.join(failures))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('mode',choices=('prepare','inventory','author'))
    p.add_argument('--fixtures',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--case',choices=tuple(CASES))
    a = p.parse_args(sys.argv[sys.argv.index('--')+1:])
    open_painter.register()
    if bpy.app.timers.is_registered(cache.tick): bpy.app.timers.unregister(cache.tick)
    cases = [a.case] if a.case else list(CASES)
    if a.mode=='prepare': prepare(a.fixtures,a.output,cases)
    elif a.mode=='inventory': inventory(a.fixtures,a.output,cases)
    else: author(a.fixtures,a.output,cases)

if __name__=='__main__': main()
