"""Opt-in window-context experiment; CPU fallback is tested headlessly."""
import json,sys,traceback
from pathlib import Path
import bpy
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from open_painter.core import image_manager as images
from open_painter.evaluation.backends.gpu_experimental import Backend
from open_painter.evaluation.backends.cpu import execute


def run():
    rng=np.random.default_rng(410)
    source=rng.random((512,512,4),dtype=np.float32)
    mask=rng.random((512,512,4),dtype=np.float32)
    operations=[dict(kind='FILL',color=(.1,.2,.3,1)),dict(kind='OVER',opacity=.7),
                dict(kind='LEVELS',black=.03,white=.9,gamma=1.3),dict(kind='GAUSSIAN_BLUR',radius=2)]
    input_image=images.create('GPU input',512); mask_image=images.create('GPU mask',512); target=images.create('GPU result',512)
    images.write(input_image,source,pack=False); images.write(mask_image,mask,pack=False)
    expected=execute(source,mask,operations)
    reference=Backend()
    _,cpu_report=reference.execute(input_image,mask_image,operations,target)
    backend=Backend(enabled=True)
    try:
        cold,cold_report=backend.execute(input_image,mask_image,operations,target)
        warm,warm_report=backend.execute(input_image,mask_image,operations,target)
        np.testing.assert_allclose(cold,expected,atol=2e-6,rtol=1e-6)
        np.testing.assert_allclose(warm,expected,atol=2e-6,rtol=1e-6)
        np.testing.assert_allclose(images.pixels(target),expected,atol=2e-6,rtol=1e-6)
        if not bpy.app.background:
            assert warm_report['backend']!='CPU'
        report=dict(blender=bpy.app.version_string,build=bpy.app.build_hash.decode(),background=bpy.app.background,
                    size=512,format='RGBA32F',operations=operations,cpu=cpu_report,cold=cold_report,warm=warm_report,
                    max_absolute_error=float(np.max(np.abs(expected-warm))),
                    promotion='DISABLED: isolated image chain, no verified interactive whole-material advantage')
        path=Path('/private/tmp/op-gpu-parity-headless.json' if bpy.app.background else '/private/tmp/op-gpu-parity-metal.json')
        path.write_text(json.dumps(report,indent=2))
        print('GPU_PARITY_PASS',json.dumps(report),flush=True)
    finally:
        backend.close(); reference.close()
        for image in (input_image,mask_image,target): bpy.data.images.remove(image)


if bpy.app.background:
    run()
else:
    def deferred():
        try: run()
        except Exception:
            traceback.print_exc()
        finally: bpy.ops.wm.quit_blender()
    bpy.app.timers.register(deferred,first_interval=2)
