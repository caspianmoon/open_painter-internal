"""Gate 1 component probe. Run with stock Blender, isolated from user files.

python scripts/probe_native_presentation.py --blender /path/to/blender \
    --lib /absolute/path/to/libopenpainter --output /tmp/native-probe.json

No new GUI window. This is a background numerical/capability experiment,
NOT evidence of in-viewport visibility, presentation latency or Gate 1 pass.
The target Windows GUI workload must still be qualified independently.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent))

from blender_test_profile import assert_blender_profile, isolated_environment
from collect_gate0_baseline import artifact_digests

ROOT = Path(__file__).resolve().parents[1]


def worker(args):
    import bpy
    import gpu
    import numpy as np
    profile = assert_blender_profile(bpy, args.profile)
    sys.path.insert(0, str(ROOT / 'src'))
    import open_painter
    from open_painter.constants import CHANNELS
    from open_painter.evaluation import cache
    from open_painter.evaluation.engine import runtime
    from open_painter.native_bridge import NativeCore, load_library
    from open_painter.native_bridge.presentation import HostGPUProbe, build_plan

    report = {'status': 'RUNNING', 'gate1_pass': False, 'route': 'C',
              'measurement': 'background host GPU numerical probe; no viewport/display evidence',
              'profile': profile, 'size': args.size,
              'blender': bpy.app.version_string, 'build_hash': bpy.app.build_hash.decode(),
              'artifacts': artifact_digests(ROOT, args.lib), 'trace': [], 'checks': [],
              'native_gpu_backend': False, 'visual_evidence': None,
              'real_brush_stroke': False, 'diagnostic_shader_dab_only': True}
    render_engine = bpy.context.scene.render.engine
    selected_object = bpy.context.active_object
    images = []
    registered = False
    try:
        runtime.ensure_gpu()
        report.update(backend=gpu.platform.backend_type_get(), renderer=gpu.platform.renderer_get(),
                      vendor=gpu.platform.vendor_get(), driver=gpu.platform.version_get())
        open_painter.register()
        registered = True
        from open_painter.native_bridge import lifecycle
        report['addon_native'] = lifecycle.diagnostics()
        if not report['addon_native']['native_loaded']:
            raise RuntimeError('Native diagnostic library did not load through add-on registration')
        if report['addon_native']['selected'] != 'legacy':
            raise AssertionError('An unqualified backend was selected')
        if bpy.app.timers.is_registered(cache.tick):
            bpy.app.timers.unregister(cache.tick)
        obj = bpy.context.active_object
        if obj is None or obj.type != 'MESH' or not obj.data.uv_layers.active:
            raise RuntimeError('A selected mesh with active UVs is required')
        report['object'] = {'name': obj.name, 'vertices': len(obj.data.vertices),
                            'uv_corners': len(obj.data.uv_layers.active.data),
                            'uv_map': obj.data.uv_layers.active.name}
        lib, _ = load_library(args.lib)
        with NativeCore(lib) as core:
            doc = core.document_create(1)
            tx = core.begin(doc)
            core.layer_add(doc, tx, 1)
            core.layer_add(doc, tx, 2)
            # Distinct values in every channel reveal stale or mixed channel writes.
            for ch in range(8):
                core.set_color(doc, tx, 1, ch, (.1 + ch * .02, .2, .3, 1.))
                core.set_color(doc, tx, 2, ch, (.8 - ch * .02, .6, .4, 1.))
            core.commit(doc, tx)
            probe = HostGPUProbe(core, args.size)
            for channel in CHANNELS:
                im = bpy.data.images.new('OP native probe ' + channel, args.size, args.size,
                                         alpha=True, float_buffer=True)
                images.append(im)
                im.colorspace_settings.name = 'Non-Color'
            diagnostic_bytes = 0
            for event_index, (opacity, brush) in enumerate((
                    (.3, (0., 0., 0., 0.)), (.9, (0., 0., 0., 0.)),
                    (.9, (.375, .625, .12, .8)))):
                tx = core.begin(doc)
                core.set_opacity(doc, tx, 2, opacity)
                core.commit(doc, tx)
                probe.publish(doc, images, brush)
                report['trace'] = probe.trace
                plan = build_plan(core, doc)
                yy, xx = np.mgrid[:args.size, :args.size].astype(np.float32)
                uvx, uvy = (xx + .5) / args.size, (yy + .5) / args.size
                alpha = plan.opacity * (.25 + .75 * uvx)
                if brush[2]:
                    t = np.clip(np.sqrt((uvx-brush[0])**2 + (uvy-brush[1])**2) / brush[2], 0, 1)
                    dab = (1-t*t*(3-2*t)) * brush[3]
                else:
                    dab = np.zeros_like(alpha)
                for channel, im in enumerate(images):
                    base, coat = np.array(plan.base[channel]), np.array(plan.coat[channel])
                    expected = base + alpha[..., None] * (coat - base)
                    expected[..., :3] += dab[..., None] * (coat[:3] - expected[..., :3])
                    expected[..., 3] = 1
                    texture = gpu.texture.from_image(im)
                    level, size = 0, args.size
                    # Deliberate diagnostic readbacks; never included as interactive work.
                    with probe.device.state():
                        while True:
                            # Sample the requested mip in a shader. A framebuffer read
                            # can use the base image level on some host backends.
                            actual = probe.read_mip(texture, level, size)
                            diagnostic_bytes += actual.nbytes
                            report['diagnostic_readback_bytes'] = diagnostic_bytes
                            error = float(np.max(np.abs(actual - expected)))
                            report['checks'].append({'event': event_index + 1, 'channel': CHANNELS[channel],
                                                      'mip': level, 'max_abs_error': error})
                            if error > 1e-3:
                                raise AssertionError(f'{CHANNELS[channel]} mip {level}: error {error}')
                            if size == 1:
                                break
                            expected = expected.reshape(size//2, 2, size//2, 2, 4).mean(axis=(1, 3))
                            size //= 2
                            level += 1
            report['trace'] = probe.trace
            report['diagnostic_readback_bytes'] = diagnostic_bytes
            report['status'] = 'COMPONENT_PASS'
            report['max_abs_error'] = max(row['max_abs_error'] for row in report['checks'])
            report['scene_engine_unchanged'] = bpy.context.scene.render.engine == render_engine
            report['selected_object_unchanged'] = bpy.context.active_object == obj
    except Exception as exc:
        report['status'] = 'UNSUPPORTED' if report.get('backend') == 'VULKAN' else 'FAIL'
        report['error'] = str(exc)
        report['traceback'] = traceback.format_exc()
    finally:
        for im in images:
            bpy.data.images.remove(im)
        if registered:
            open_painter.unregister()
            report['native_closed_on_unregister'] = not lifecycle.diagnostics()['native_loaded']
        report['scene_engine_unchanged'] = bpy.context.scene.render.engine == render_engine
        report['selected_object_unchanged'] = bpy.context.active_object == selected_object
        args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print('NATIVE_PRESENTATION_PROBE', report['status'], flush=True)
    if report['status'] == 'FAIL':
        raise RuntimeError(report['error'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blender', type=Path)
    parser.add_argument('--lib', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--size', type=int, choices=(512, 1024, 2048), default=2048)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--profile', type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else None)
    args.lib, args.output = args.lib.resolve(), args.output.resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.worker:
        worker(args)
        return
    if args.blender is None:
        parser.error('--blender is required')
    with tempfile.TemporaryDirectory(prefix='op-native-probe-') as profile:
        cmd = [str(args.blender), '--background', '--factory-startup', '--python-exit-code', '1',
               '--python', str(Path(__file__).resolve()), '--', '--worker', '--profile', profile,
               '--lib', str(args.lib), '--output', str(args.output), '--size', str(args.size)]
        environment = isolated_environment(profile)
        environment['OP_LIBOPENPAINTER_PATH'] = str(args.lib)
        with args.output.with_suffix('.log').open('w', encoding='utf-8') as log:
            result = subprocess.run(cmd, env=environment, stdout=log,
                                    stderr=subprocess.STDOUT, timeout=240,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if result.returncode:
        raise SystemExit(f'Probe failed ({result.returncode}); see {args.output.with_suffix(".log")}')
    print(args.output)


if __name__ == '__main__':
    main()
