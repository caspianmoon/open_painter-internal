"""GPU/CPU mask parity with real Worn records and exact baked-map sampling.

Run in an isolated Blender GUI. OP_MASK_N defaults to full 2048; the test never
changes an authored texture set's size. OP_MASK_REPORT chooses the JSON report.
No production planner uses this prototype until these checks pass.
"""

import json
import os
from pathlib import Path
import sys
import time
import traceback
from types import SimpleNamespace

import bpy
import gpu
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tests' / 'performance'))
import open_painter
from fixtures import create as fixture
from open_painter.core import image_manager
from open_painter.evaluation import cache
from open_painter.evaluation.live_gpu import Device
from open_painter.evaluation.live_masks import MaskDevice, UnsupportedMask, supports, validate
from open_painter.evaluation.live_procedurals import ScalarDevice
from open_painter.evaluation.mask_eval import _evaluate_masks
from open_painter.evaluation.metrics import Metrics

REPORT = Path(os.environ.get('OP_MASK_REPORT', '/private/tmp/op-live-masks.json'))
N = int(os.environ.get('OP_MASK_N', '2048'))
FIELDS = ('source','generator','enabled','operation','invert','black','white','strength',
          'amount','contrast','noise_scale','noise_strength','seed','projection','filtering',
          'tiling','scale','rotation','offset')


def copy_mask(mask, **changes):
    values = {name: getattr(mask,name) for name in FIELDS}
    values.update(changes)
    return SimpleNamespace(**values)


def cpu(ts, layer, data):
    result = np.empty((N,N),np.float32)
    images = {entry.image.as_pointer():data[entry.kind]
              for entry in ts.baked_maps if entry.image}
    for y in range(0,N,32):
        result[y:y+32] = _evaluate_masks(ts,layer,N,y,min(y+32,N),images,{})
    return result


def run():
    assert not bpy.app.background, 'This test requires a Blender GPU window'
    open_painter.register()
    if bpy.app.timers.is_registered(cache.tick):
        bpy.app.timers.unregister(cache.tick)
    scalar = masks = fence_device = None
    fence_target = fence_framebuffer = None
    fields = {}
    record = dict(status='RUNNING',n=N,blender=bpy.app.version_string,
                  build=bpy.app.build_hash.decode(),gpu_backend=gpu.platform.backend_type_get(),
                  checks=[],unsupported_checks=0,failures=[],
                  fence='Dependent GPU copy to reusable 1x1 RGBA32F texture, then readback',
                  fence_requested_payload_bytes=16,
                  timing_limit='Completion includes GPU queue synchronization and the fence copy/read; '
                               'full-resolution parity readback is outside the timed interval.')
    try:
        ts = fixture(bpy.context.scene,'worn',N)[0]
        data = {entry.kind:image_manager.pixels(entry.image) for entry in ts.baked_maps}
        authored = {entry.kind:entry.image.as_pointer() for entry in ts.baked_maps}
        actual = {mask.generator:copy_mask(mask) for layer in ts.layers for mask in layer.masks}
        record['worn_masks'] = [{name:list(getattr(mask,name)) if name in {'scale','offset'}
                                else getattr(mask,name) for name in FIELDS}
                               for mask in actual.values()]
        cases = [(kind,ts,SimpleNamespace(name=kind,masks=[mask]),data)
                 for kind,mask in actual.items()]
        edge = copy_mask(actual['CHIPS'],generator='EDGE_WEAR',invert=False)
        dirt = copy_mask(actual['CAVITY_DIRT'],generator='DIRT',amount=.63,contrast=3.2)
        cases += [('EDGE_WEAR',ts,SimpleNamespace(name='Edge',masks=[edge]),data),
                  ('DIRT',ts,SimpleNamespace(name='Dirt',masks=[dirt]),data)]
        cases.append(('sequential_levels_invert_strength',ts,SimpleNamespace(name='Stack',masks=[
            copy_mask(edge,operation='ADD',strength=.42,black=.08,white=.89),
            copy_mask(dirt,operation='MULTIPLY',strength=.61,invert=True),
            copy_mask(actual['RUST'],operation='ADD',strength=.27,black=.12,white=.91),
            copy_mask(actual['SCRATCHES'],operation='NORMAL',strength=.31),
        ]),data))
        cases.append(('disabled_unsupported_ignored',ts,SimpleNamespace(name='Disabled',masks=[
            copy_mask(edge),copy_mask(dirt,source='FILTER',enabled=False)]),data))
        flat = {kind:values.copy() for kind,values in data.items()}
        flat['CURVATURE'][...,:3] = 0
        cases.append(('flat_curvature_high_intensity',ts,SimpleNamespace(name='Flat',masks=[
            copy_mask(edge,amount=1.,contrast=20.,noise_strength=1.)]),flat))
        no_ao = SimpleNamespace(baked_maps=[entry for entry in ts.baked_maps if entry.kind!='AO'])
        cases.append(('optional_ao_absent',no_ao,SimpleNamespace(name='No AO',masks=[edge]),data))
        # Non-square maps exercise independent X/Y sizes and UV repeat borders.
        uneven = {kind:values[:19,:23].copy() for kind,values in data.items()}
        cases.append(('uneven_map_manual_bilinear',ts,SimpleNamespace(name='Uneven',masks=[dirt]),uneven))

        scalar, masks = ScalarDevice(), MaskDevice()
        # Keep fence resources and its shader compilation outside mask timings.
        # Reading an isolated tiny attachment avoids conflating mask work with
        # any full-size attachment readback/conversion cost in the driver.
        fence_device = Device()
        fence_target = gpu.types.GPUTexture((1,1),format='RGBA32F')
        fence_framebuffer = gpu.types.GPUFrameBuffer(color_slots=fence_target)
        with fence_device._state():
            fence_device._pass(fence_target,1,opcode=0)
        with fence_framebuffer.bind():
            fence_framebuffer.read_color(0,0,1,1,4,0,'FLOAT')

        def field(kind,n,**parameters):
            key = kind,n,parameters['scale'],parameters['seed']
            if key not in fields:
                fields[key] = scalar.generate(kind,n,**parameters)
            return fields[key]

        for label,owner,layer,maps_data in cases:
            print('GPU_MASK',label,flush=True)
            assert supports(owner,layer,N), 'The supported fixture was declined: '+label
            started=time.perf_counter()
            expected=cpu(owner,layer,maps_data)
            cpu_s=time.perf_counter()-started
            maps={}
            def mesh_map(kind):
                if kind not in maps:
                    data=np.ascontiguousarray(maps_data[kind],np.float32)
                    maps[kind]=gpu.types.GPUTexture((data.shape[1],data.shape[0]),format='RGBA32F',
                        data=gpu.types.Buffer('FLOAT',data.size,data.ravel()))
                return maps[kind]
            with Metrics() as observed:
                started=time.perf_counter()
                texture=masks.generate(owner,layer,N,field=field,mesh_map=mesh_map)
                submit_s=time.perf_counter()-started
                with fence_device._state():
                    fence_device._pass(fence_target,1,opcode=4,current=texture)
                with fence_framebuffer.bind():
                    fence_framebuffer.read_color(0,0,1,1,4,0,'FLOAT')
                complete_s=time.perf_counter()-started
            actual_pixels=np.array(texture.read(),np.float32).reshape(N,N)
            difference=np.abs(actual_pixels-expected)
            failed=difference>(3e-5+2e-5*np.abs(expected))
            check=dict(name=label,cpu_s=cpu_s,gpu_submit_s=submit_s,gpu_complete_s=complete_s,
                       max_error=float(difference.max()),mean_error=float(difference.mean()),
                       mismatched_pixels=int(np.count_nonzero(failed)),metrics=observed.report())
            if label=='flat_curvature_high_intensity':
                assert np.count_nonzero(actual_pixels)==0, 'Noise created wear on flat curvature'
            if np.any(failed):
                iy,ix=np.unravel_index(np.argmax(difference),difference.shape)
                check['largest_difference']=dict(x=int(ix),y=int(iy),
                    cpu=float(expected[iy,ix]),gpu=float(actual_pixels[iy,ix]))
                record['failures'].append(label)
            record['checks'].append(check)
            print(json.dumps({k:v for k,v in check.items() if k!='metrics'}),flush=True)
        # Full-stack preflight must reject even a later unsupported entry before
        # invoking a caller's map or field loader (and before target allocation).
        for changes in ({'source':'FILTER'},{'source':'REFERENCE'},{'source':'PAINT'},
                        {'projection':'TRIPLANAR'},{'scale':(2.,1.)},{'rotation':.1},
                        {'offset':(.1,0.)},{'noise_scale':300.},
                        {'black':.8,'white':.2}, {'black':.5,'white':.51},
                        {'black':.6,'white':.6},
                        {'generator':'SCRATCHES','amount':1.,'contrast':3.},
                        {'detail_height_anchor':'anchor'},{'detail_normal_anchor':'anchor'},
                        {'generator':'SCRATCHES','amount':.3,'contrast':3.,'black':.25,'white':.5}):
            layer=SimpleNamespace(name='Declined',masks=[edge,copy_mask(dirt,**changes)])
            assert not supports(ts,layer,N), 'Unsupported parameters passed pure preflight'
            def forbidden(*args,**kwargs):
                raise AssertionError('Unsupported stack requested GPU input before preflight completed')
            with Metrics() as observed:
                try:
                    masks.generate(ts,layer,N,field=forbidden,mesh_map=forbidden)
                except (UnsupportedMask,ValueError):
                    pass
                else:
                    raise AssertionError('Unsupported mask stack was silently accepted')
            assert observed.report()['counters'].get('live_gpu_allocations',0)==0
            record['unsupported_checks']+=1
        # A non-null but unreadable image is still present to the CPU evaluator.
        # In particular, dropping an empty optional AO would silently change edge
        # wear. Reject all such stacks before requesting fields/maps or targets.
        empty_image=SimpleNamespace(size=(0,0))
        edge_layer=SimpleNamespace(name='Unloaded map',masks=[edge])
        for kind in ('AO','CURVATURE'):
            for duplicate in (False,True):
                entries=[SimpleNamespace(kind=kind,image=empty_image)]
                entries.extend(item for item in ts.baked_maps if duplicate or item.kind!=kind)
                owner=SimpleNamespace(baked_maps=entries)
                assert not supports(owner,edge_layer,N), 'An unreadable referenced map was treated as absent'
                with Metrics() as observed:
                    try:
                        masks.generate(owner,edge_layer,N,field=forbidden,mesh_map=forbidden)
                    except UnsupportedMask as error:
                        assert 'no loaded pixels' in str(error)
                    else:
                        raise AssertionError('An unreadable referenced map bypassed CPU fallback')
                assert observed.report()['counters'].get('live_gpu_allocations',0)==0
                record['unsupported_checks']+=1
        unrelated=SimpleNamespace(baked_maps=[*ts.baked_maps,
            SimpleNamespace(kind='THICKNESS',image=empty_image)])
        assert supports(unrelated,edge_layer,N), 'An unused map blocked an eligible mask'
        assert {entry.kind:entry.image.as_pointer() for entry in ts.baked_maps}==authored
        record['status']='FAIL' if record['failures'] else 'PASS'
        REPORT.write_text(json.dumps(record,indent=2)+'\n')
        if record['failures']:
            raise AssertionError('GPU mask parity failures: '+str(record['failures']))
    finally:
        if fence_device is not None:
            fence_device.close()
        fence_framebuffer = fence_target = None
        if masks is not None:
            masks.close()
        if scalar is not None:
            scalar.close()
        fields.clear()
        open_painter.unregister()


def main():
    try:
        run()
    except Exception:
        detail=traceback.format_exc()
        print(detail,flush=True)
        record = json.loads(REPORT.read_text()) if REPORT.exists() else {}
        record.update(status='ERROR', traceback=detail)
        REPORT.write_text(json.dumps(record, indent=2))
    finally:
        bpy.ops.wm.quit_blender()


bpy.app.timers.register(main,first_interval=1.)
