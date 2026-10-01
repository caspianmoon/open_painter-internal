"""Independent no-edit recapture of pinned authored diagnostic states.

Reopens each source twice and forces actual CPU channel/effect-mask evaluation.
No provider, artistic edits, saved-source writes or benchmark grades.
"""
import argparse
import json
import os
import shutil
from pathlib import Path
import sys

import bpy
import numpy as np

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--product-root',type=Path,required=True)
p.add_argument('--base-attempt',type=Path,required=True)
p.add_argument('--boots-attempt',type=Path)
p.add_argument('--output',type=Path,required=True)
p.add_argument('--mask-tolerance',type=float,default=1e-6)
p.add_argument('--channel-tolerance',type=float,default=1e-6)
p.add_argument('--case',choices=('T01','T02','T03','T04','T05'))
a=p.parse_args(sys.argv[sys.argv.index('--')+1:])
a.output=a.output.resolve()
a.base_attempt=a.base_attempt.resolve()
if a.boots_attempt:a.boots_attempt=a.boots_attempt.resolve()
product=a.product_root.resolve()
sys.path[:0]=[str(product/'src'),str(HERE)]
import open_painter
from open_painter.evaluation import cache, raster_cache
from capture_evidence import capture_channels, config_for, scene_record, sha256, write_json
from runner import product_fingerprint
from revision_measurements import coverage


def differences(left,right,valid=None):
    if left.shape!=right.shape:
        raise ValueError('Recapture changed array dimensions')
    delta=np.abs(left.astype(np.float64)-right.astype(np.float64))
    if valid is not None:delta=delta[valid]
    if not delta.size or not np.isfinite(delta).all():
        raise ValueError('Recapture delta is empty or nonfinite')
    return dict(maximum=float(delta.max()),mean=float(delta.mean()),values=int(delta.size))


def main():
    if a.output.exists():raise ValueError('Refuse to overwrite a no-edit diagnostic attempt')
    a.output.mkdir(parents=True)
    pin=product_fingerprint(product)
    if not Path(open_painter.__file__).resolve().is_relative_to(product/'src'):
        raise ValueError('Loaded addon does not match requested product')
    result=dict(track='DETERMINISTIC_AUTHORED_ENGINE_NO_EDIT_RECAPTURE',provider_calls=0,
        product=pin,product_root=str(product),loaded_module=open_painter.__file__,
        status='RUNNING',reviewer='PENDING',quality='NOT_SCORED',
        method='Two independent source-file reloads; forced native CPU channel evaluation and actual effect-mask capture, with no authored edits.',
        frozen_tolerances=dict(mask_absolute_tolerance=a.mask_tolerance,channel_absolute_tolerance=a.channel_tolerance),
        implementation={path.name:sha256(path) for path in (Path(__file__),HERE/'capture_evidence.py',HERE/'revision_measurements.py')},
        technical_claim='Numeric no-edit repeatability only; not an artistic or autonomous-workflow pass.',cases={},reports=[])
    write_json(a.output/'repeatability.json',result)
    implementation=a.output/'implementation';implementation.mkdir()
    for name,digest in result['implementation'].items():
        shutil.copy2(HERE/name,implementation/name)
        if sha256(implementation/name)!=digest:raise ValueError('Implementation changed during snapshot')
    open_painter.register()
    if bpy.app.timers.is_registered(cache.tick):bpy.app.timers.unregister(cache.tick)
    for case in ([a.case] if a.case else ('T01','T02','T03','T04','T05')):
        base=a.boots_attempt if case=='T03' and a.boots_attempt else a.base_attempt
        source=base/case/'after_create.blend'
        authored=json.loads((base/case/'calibration.json').read_text(encoding="utf-8"))
        if authored['source_build']['source_sha256']!=pin['source_sha256']:
            raise ValueError('Saved calibration is from another product build')
        before=sha256(source)
        out=a.output/case;out.mkdir()
        records=[]
        for index in (1,2):
            bpy.ops.wm.open_mainfile(filepath=str(source))
            if bpy.app.timers.is_registered(cache.tick):bpy.app.timers.unregister(cache.tick)
            raster_cache.clear_shared()
            scene=bpy.context.scene;config=config_for(scene)
            protected=scene_record(scene,config['target_set_ids'])
            captured=capture_channels(bpy.context,out,'recapture_'+str(index),config)
            if captured['status']!='CAPTURED':raise ValueError(captured)
            if scene_record(scene,config['target_set_ids'])!=protected:
                raise ValueError('No-edit recapture changed protected geometry or environment')
            write_json(out/('recapture_'+str(index)+'.json'),captured)
            records.append(captured)
        left,right=records
        case_result=dict(case_id=case,source_blend=str(source),source_sha256=before,source_unchanged=sha256(source)==before,
            capture_reports=[dict(path=str(out/('recapture_'+str(index)+'.json')),sha256=sha256(out/('recapture_'+str(index)+'.json'))) for index in (1,2)],
            actual_recaptures=2,texture_sets={},maximum_channel_delta=0.,maximum_mask_delta=0.)
        if left['sets'].keys()!=right['sets'].keys():raise ValueError('Recapture changed texture-set membership')
        for identifier,first in left['sets'].items():
            second=right['sets'][identifier]
            valid=np.load(first['valid_samples']['path'],allow_pickle=False)
            if not np.array_equal(valid,np.load(second['valid_samples']['path'],allow_pickle=False)):
                raise ValueError('Recapture changed valid surface pixels')
            entry=dict(valid_texels=int(valid.sum()),channels={},effect_masks={},surface_samples_equal=True)
            with np.load(first['surface_samples']['path'],allow_pickle=False) as loaded:
                samples={key:loaded[key] for key in loaded.files}
            with np.load(second['surface_samples']['path'],allow_pickle=False) as loaded:
                if set(samples)!=set(loaded.files) or any(not np.array_equal(samples[key],loaded[key]) for key in loaded.files):
                    raise ValueError('Recapture changed geometric area samples')
            entry['surface_samples']=dict(count=len(samples['weight']),valid=int(samples['valid'].sum()),
                area=float(samples['weight'].sum()),artifacts=[first['surface_samples'],second['surface_samples']])
            for name,channel in first['channels'].items():
                x=np.load(channel['path'],allow_pickle=False);y=np.load(second['channels'][name]['path'],allow_pickle=False)
                measured=differences(x,y,valid)
                measured.update(all_texels=differences(x,y),within_tolerance=measured['maximum']<=a.channel_tolerance,
                    artifacts=[dict(path=channel['path'],sha256=channel['sha256']),dict(path=second['channels'][name]['path'],sha256=second['channels'][name]['sha256'])])
                entry['channels'][name]=measured
                case_result['maximum_channel_delta']=max(case_result['maximum_channel_delta'],measured['maximum'])
            if first['effect_masks'].keys()!=second['effect_masks'].keys():raise ValueError('Recapture changed effect membership')
            for identifier_mask,mask in first['effect_masks'].items():
                other=second['effect_masks'][identifier_mask]
                x=np.load(mask['path'],allow_pickle=False);y=np.load(other['path'],allow_pickle=False)
                measured=differences(x,y)
                measured.update(name=mask['layer_name'],within_tolerance=measured['maximum']<=a.mask_tolerance,
                    source_signature_equal=mask['source_signature']==other['source_signature'],
                    layer_opacity_equal=mask['layer_opacity']==other['layer_opacity'],
                    first_surface_coverage=coverage(x,samples),second_surface_coverage=coverage(y,samples),
                    artifacts=[dict(path=mask['path'],sha256=mask['sha256']),dict(path=other['path'],sha256=other['sha256'])])
                control,other_control=mask.get('effect_control',{}),other.get('effect_control',{})
                if control.get('source_field'):
                    source_field,other_field=control['source_field'],other_control['source_field']
                    source_difference=differences(np.load(source_field['path'],allow_pickle=False),np.load(other_field['path'],allow_pickle=False))
                    source_difference.update(representation=control.get('representation'),
                        source_signature_equal=control.get('source_signature')==other_control.get('source_signature'),
                        opacity_equal=control.get('opacity')==other_control.get('opacity'),
                        artifacts=[source_field,other_field])
                    measured['source_field']=source_difference
                    case_result['maximum_mask_delta']=max(case_result['maximum_mask_delta'],source_difference['maximum'])
                entry['effect_masks'][identifier_mask]=measured
                case_result['maximum_mask_delta']=max(case_result['maximum_mask_delta'],measured['maximum'])
            case_result['texture_sets'][identifier]=entry
        case_result['status']='PASS' if (case_result['source_unchanged']
            and case_result['maximum_channel_delta']<=a.channel_tolerance and case_result['maximum_mask_delta']<=a.mask_tolerance
            and all(item['source_signature_equal'] and item['layer_opacity_equal']
                and ('source_field' not in item or (item['source_field']['source_signature_equal'] and item['source_field']['opacity_equal']))
                for entry in case_result['texture_sets'].values() for item in entry['effect_masks'].values())) else 'FAILED'
        result['cases'][case]=case_result
        write_json(out/'comparison.json',case_result)
        result['reports'].append(dict(case_id=case,path=str(out/'comparison.json'),sha256=sha256(out/'comparison.json')))
        write_json(a.output/'repeatability.json',result)
        print('NO_EDIT_RECAPTURE',case,case_result['status'],case_result['maximum_channel_delta'],case_result['maximum_mask_delta'],flush=True)
    if product_fingerprint(product)!=pin:raise ValueError('Product changed during no-edit recapture')
    if any(sha256(HERE/name)!=digest for name,digest in result['implementation'].items()):
        raise ValueError('Measurement implementation changed during no-edit recapture')
    result['status']='PASS' if all(item['status']=='PASS' for item in result['cases'].values()) else 'FAILED'
    write_json(a.output/'repeatability.json',result)
    if result['status']=='FAILED':raise ValueError('No-edit recapture failed declared tolerance checks')


main()
