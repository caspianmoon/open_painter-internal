"""Full-size scalar GPU/CPU parity; launch Blender with a real GPU window.

OP_SCALAR_N defaults to 2048. OP_SCALAR_CPU_ROOT may point at an archived
correctness baseline. GPU completion is fenced by one pixel before the separate
full readback used only for validation. This is not a viewport latency test.
"""
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import traceback

import bpy
import gpu
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from open_painter.evaluation.live_procedurals import ScalarDevice, UnsupportedSource, supports
from open_painter.evaluation.metrics import Metrics
from open_painter.texture_engine import procedurals

REPORT = Path(os.environ.get('OP_SCALAR_REPORT', '/private/tmp/op-live-procedurals.json'))
CPU_ROOT = Path(os.environ.get('OP_SCALAR_CPU_ROOT', str(ROOT)))
if CPU_ROOT != ROOT:
    spec = importlib.util.spec_from_file_location('open_painter.texture_engine.scalar_cpu_reference',
        CPU_ROOT / 'src/open_painter/texture_engine/procedurals.py')
    procedurals = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(procedurals)

CASES = [
    ('BRUSHED_METAL_LINES', 80., 1237960897),
    ('CONCRETE_PORES', 52., 1238065626),
    ('ORANGE_PEEL', 112., 1238170355),
    ('SCRATCHES', 78., 1238275084),
    ('GRUNGE_MEDIUM', 30., 1238379813),
    ('FBM', 30., 1238065626),
    ('FBM', 48., 1238170355),
    ('FBM', 30., 1238379813),
    ('VORONOI', 48., 1238170355),
    ('PERLIN', 112., 1),
]


def cpu_source(kind, n, scale, seed, **params):
    result = np.empty((n,n), np.float32)
    axis = (np.arange(n, dtype=np.float32)+.5)/n
    # Match the evaluator's canonical strip arithmetic, including the zero
    # rotation / unit-scale UV mapping at the authored texture resolution.
    for y in range(0,n,16):
        u,v = np.meshgrid(axis, axis[y:y+16])
        result[y:y+16] = procedurals.evaluate(kind,u,v,scale=scale,seed=seed,**params)
    return result


def run():
    assert not bpy.app.background, 'A real Blender GPU window is required'
    n = int(os.environ.get('OP_SCALAR_N', '2048'))
    record = {'blender': bpy.app.version_string, 'backend': gpu.platform.backend_type_get(),
              'n': n, 'cpu_root': str(CPU_ROOT), 'checks': [], 'failures': []}
    compilation_start = time.perf_counter()
    device = ScalarDevice()
    record['device_initialization_s'] = time.perf_counter() - compilation_start
    try:
        cases = [(kind,n,scale,seed,{}) for kind,scale,seed in CASES]
        for size,scale,seed in ((32,.125,0),(128,7.125,2147483000),(257,255.875,17)):
            cases.extend((kind,size,scale,seed,{}) for kind in (
                'BRUSHED_METAL_LINES','CONCRETE_PORES','ORANGE_PEEL','SCRATCHES','GRUNGE_MEDIUM','VORONOI'))
        cases.extend([
            ('FBM',257,256.,11,{'octaves':8}),
            ('FBM',33,.001,123,{'octaves':1}),
            ('SCRATCHES',256,78.,71,{'width':.02,'density':.9}),
            ('SCRATCHES',257,123.,87,{'width':1.,'density':0.}),
            ('SCRATCHES',128,.7,19,{'width':.03,'density':1.}),
            ('CONCRETE_PORES',256,52.,29,{'radius':.01,'density':1.}),
            ('CONCRETE_PORES',257,111.,39,{'radius':1.,'density':.17}),
            ('CONCRETE_PORES',64,.0125,101,{'radius':.175,'density':0.}),
            ('VORONOI',512,47.9375,99373,{}),
            ('VORONOI',512,256.,2147483000,{}),
        ])
        for kind, size, scale, seed, params in cases:
            print('SCALAR',kind,size,scale,seed,params,flush=True)
            t0=time.perf_counter()
            expected = cpu_source(kind,size,scale,seed,**params)
            cpu_s=time.perf_counter()-t0
            with Metrics() as metrics:
                t0=time.perf_counter()
                target=device.generate(kind,size,scale=scale,seed=seed,**params)
                submitted_s=time.perf_counter()-t0
                framebuffer=gpu.types.GPUFrameBuffer(color_slots=target)
                with framebuffer.bind():
                    framebuffer.read_color(0,0,1,1,1,0,'FLOAT')
                complete_s=time.perf_counter()-t0
            actual=np.array(target.read(),np.float32).reshape(size,size)
            difference=np.abs(actual-expected)
            permitted=3e-5+2e-5*np.abs(expected)
            # Nearest-cell identity is discontinuous. A changed cell winner is
            # an actual different source, not floating-point noise to hide.
            failed=(actual!=expected) if kind=='VORONOI' else difference>permitted
            check={'kind':kind,'n':size,'scale':scale,'seed':seed,'parameters':params,'cpu_s':cpu_s,
                   'gpu_submit_s':submitted_s,'gpu_complete_s':complete_s,
                   'max_error':float(difference.max()),'mean_error':float(difference.mean()),
                   'mismatched_pixels':int(np.count_nonzero(failed)),
                   'metrics':metrics.report()}
            if np.any(failed):
                iy,ix=np.unravel_index(np.argmax(difference),difference.shape)
                check['largest_difference']={'xy':[int(ix),int(iy)],'cpu':float(expected[iy,ix]),'gpu':float(actual[iy,ix])}
                record['failures'].append(kind+':'+str(seed))
            record['checks'].append(check)
            print(json.dumps({k:v for k,v in check.items() if k!='metrics'}),flush=True)
        for arguments in ({'kind':'WOOD_GRAIN'}, {'angle':.1}, {'persistence':.3},
                          {'scale':300.}, {'scale':0.}, {'scale':float('nan')}, {'seed':-1},
                          {'seed':2147483647}, {'seed':1.25}, {'octaves':9}, {'octaves':0},
                          {'anisotropy':16}, {'width':.019}, {'width':1.1},
                          {'density':float('nan')}, {'radius':.009}, {'radius':1.1},
                          {'n':0}, {'n':8193}, {'n':32.5}, {'warp':.2}):
            request=dict(kind='FBM',n=n,scale=8.,seed=1)
            request.update(arguments)
            assert not supports(**request),request
            try:
                device.generate(**request)
            except UnsupportedSource:
                pass
            else:
                raise AssertionError('Unsupported input was silently evaluated: '+str(request))
        state=lambda:(gpu.state.viewport_get(),gpu.state.scissor_get(),gpu.state.blend_get(),
                      gpu.state.depth_test_get(),gpu.state.depth_mask_get())
        original=state()
        try:
            gpu.state.viewport_set(1,2,11,13)
            gpu.state.scissor_set(3,4,7,9)
            gpu.state.blend_set('ALPHA')
            gpu.state.depth_test_set('LESS_EQUAL')
            gpu.state.depth_mask_set(True)
            before=state()
            target=device.generate('FBM',32)
            assert state()==before,'GPU state was not restored'
            window=device.window
            device.window=-1
            try:
                device.generate('FBM',32)
            except RuntimeError:
                pass
            else:
                raise AssertionError('A different GPU window was silently accepted')
            finally:
                device.window=window
            assert state()==before,'GPU context failure modified state'
        finally:
            viewport,scissor,blend,depth,depth_mask=original
            gpu.state.viewport_set(*viewport)
            gpu.state.scissor_set(*scissor)
            gpu.state.blend_set(blend)
            gpu.state.depth_test_set(depth)
            gpu.state.depth_mask_set(depth_mask)
        device.close()
        device.close()
        try:
            device.generate('FBM',32)
        except RuntimeError:
            pass
        else:
            raise AssertionError('A closed scalar device was silently reused')
        # Recreate without a synchronizing readback: resource lifecycle must
        # release the old program safely even with queued GPU work.
        device=ScalarDevice()
        target=device.generate('FBM',32)
        actual=np.array(target.read(),np.float32).reshape(32,32)
        np.testing.assert_allclose(actual,cpu_source('FBM',32,8.,1),atol=3e-5,rtol=2e-5)
        record['state_context_close_checks']='PASS'
        record['status']='PASS' if not record['failures'] else 'FAIL'
    finally:
        device.close()
    REPORT.write_text(json.dumps(record,indent=2))
    print('SCALAR REPORT',str(REPORT),record['status'],flush=True)
    if record['failures']:
        raise AssertionError('Scalar GPU parity failures: '+str(record['failures']))


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
