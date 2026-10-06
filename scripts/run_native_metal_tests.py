"""Run C host-Metal regressions in isolated stock Blender profiles.

GUI mode requires a logged-in macOS desktop. Captures are diagnostic evidence,
not scanout latency. No preferences or add-ons in the artist profile are changed.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import shutil
import tempfile

from blender_test_profile import isolated_environment
from collect_gate0_baseline import artifact_digests

ROOT = Path(__file__).resolve().parents[1]
# Suites that legitimately need more than the default cap (4K publication targets).
MEMORY_CAPS_GB = {'native_publication_4k.py': 7.0, 'resolution_switch.py': 6.0}
TESTS = ('native_host_metal.py', 'native_publication_4k.py', 'native_publication_precision.py', 'native_blend.py', 'native_effects.py', 'native_masks.py', 'native_geometry_projection.py', 'native_suffix.py', 'native_fusion.py', 'engine_kept_reads.py', 'engine_param_free.py', 'graph_path_index.py', 'engine_parity.py', 'engine_procedurals.py',
         'gpu_strokes.py', 'engine_filters.py', 'engine_state_cache.py', 'shader_undo.py',
         'shader_lifecycle_edges.py', 'shader_adoption.py', 'engine_memory_soak.py',
         'save_during_evaluation.py', 'resolution_switch.py', 'stroke_parity.py',
         'engine_pixel_publish.py', 'engine_uniform_publish.py',
         'engine_constant_publish.py', 'engine_fused_publish.py')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blender', default='/Applications/Blender.app/Contents/MacOS/Blender')
    parser.add_argument('--lib', type=Path, required=True)
    parser.add_argument('--package', type=Path)
    parser.add_argument('--gui', action='store_true')
    parser.add_argument('--sustained', action='store_true', help='Five 60-second real viewport RNA edit runs')
    parser.add_argument('--tests', nargs='+', default=TESTS)
    parser.add_argument('--gpu-backend', choices=('metal', 'opengl', 'vulkan'),
                        help="Blender's --gpu-backend (default: the factory setting); backends other "
                             "than Metal also need OP_NATIVE_HOST_BACKENDS")
    parser.add_argument('--cap-gb', type=float, default=4.0,
                        help='Kill a test whose Blender memory footprint passes this (scripts/run_guarded.py)')
    args = parser.parse_args()
    latest = ROOT / '.blender-test/native/metal-integration'
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    out = latest / 'runs' / run_id
    out.mkdir(parents=True, exist_ok=True)
    report = dict(status='RUNNING', run_id=run_id,
                  artifacts=artifact_digests(ROOT, args.lib), tests=[])
    if args.package:
        report['package_sha256'] = hashlib.sha256(args.package.read_bytes()).hexdigest()

    def run(name, *, gui=False, extra=None, package=False):
        with tempfile.TemporaryDirectory(prefix='op-metal-') as profile:
            env = isolated_environment(profile)
            env['OP_EXPECT_NATIVE'] = '1'
            env['OP_NATIVE_HOST_REPORT'] = str(out / 'native-host-metal.json')
            env['OP_VECTOR_BRUSH_UI_REPORT'] = str(out / 'native-brush-ui.json')
            if package:
                env.pop('OP_LIBOPENPAINTER_PATH', None)
                env['OPEN_PAINTER_TEST_ZIP'] = str(args.package.resolve())
                env['OP_INSTALL_TEST_REPOSITORY'] = str(Path(profile) / 'extensions ü' / 'repository')
            else:
                env['OP_LIBOPENPAINTER_PATH'] = str(args.lib.resolve())
            env.pop('OP_NATIVE_ENGINE', None)
            env.update(extra or {})
            # Every Blender runs under the memory guard: a runaway test must not take the machine down.
            cap = max(args.cap_gb, MEMORY_CAPS_GB.get(name, 0.0))
            cmd = [sys.executable, str(ROOT / 'scripts/run_guarded.py'), '--cap-gb', str(cap),
                   '--start-gb', '3', '--wait-seconds', '120', '--',
                   args.blender, '--factory-startup', '--python-exit-code', '1']
            if args.gpu_backend:
                cmd += ['--gpu-backend', args.gpu_backend]
            cmd += ['--enable-event-simulate'] if gui else ['--background']
            cmd += ['--python', str(ROOT / 'tests/integration' / name)]
            if gui:
                cmd += ['--', '-ApplePersistenceIgnoreState', 'YES']
            with (out / (name + '.log')).open('w') as log:
                result = subprocess.run(cmd, cwd=ROOT, env=env, stdout=log,
                                        stderr=subprocess.STDOUT, timeout=120 + (480 if name == 'native_sustained_ui.py' else 300))
            item = dict(test=name, exit=result.returncode)
            report['tests'].append(item)
            print(item, flush=True)
            (out / 'results.json').write_text(json.dumps(report, indent=2) + '\n', encoding="utf-8")
            if result.returncode:
                raise RuntimeError(f'{name} failed; see {out / (name + ".log")}')

    try:
        for name in args.tests:
            if name == 'live_masks.py':
                # Timer-driven GUI tests can exit successfully without running
                # at all in background mode. Require their fresh result report.
                target = out / 'live-masks.json'
                run(name, gui=True, extra={'OP_MASK_REPORT': str(target), 'OP_MASK_N': '512'})
                data = json.loads(target.read_text(encoding="utf-8"))
                assert data['status'] == 'PASS', data
            else:
                run(name)
        if args.gui:
            for name, variable, filename in (
                ('shader_preview_ui.py', 'OP_SHADER_UI_REPORT', 'native-slider-ui.json'),
                ('vector_brush_ui.py', 'OP_VECTOR_BRUSH_UI_REPORT', 'native-brush-ui.json')):
                target = out / filename
                target.unlink(missing_ok=True)
                run(name, gui=True, extra={variable: str(target), 'OP_VECTOR_BRUSH_UI_SIZE': '2048'})
                data = json.loads(target.read_text(encoding="utf-8"))
                assert data.get('result', data.get('status')) == 'PASS', data
                assert data['execution_backend'].startswith('c17-host-'), data   # the test checks its backend
            # A real drag over 200 layers: every value change is evaluated, and every evaluation drawn.
            target = out / 'native-drag-cadence.json'
            target.unlink(missing_ok=True)
            run('native_drag_cadence.py', gui=True, extra={'OP_DRAG_REPORT': str(target), 'OP_DRAG_RES': '1024',
                                                           'OP_DRAG_LAYERS': '200', 'OP_DRAG_SECONDS': '8'})
            data = json.loads(target.read_text(encoding="utf-8"))
            assert data['status'] == 'PASS', data.get('error')
            summary = data['summary']
            assert summary['undrawn_evaluations'] == 0, summary
            assert summary['evaluations_per_s'] >= .95 * summary['value_changes_per_s'], summary
            run('native_paint_reopen.py')
        if args.sustained:
            target = out / 'native-sustained-ui.json'
            run('native_sustained_ui.py', gui=True, extra={'OP_SUSTAINED_REPORT': str(target)})
            data = json.loads(target.read_text(encoding="utf-8"))
            assert data['status'] == 'PASS', data.get('error')
        if args.package:
            run('install_extension.py', package=True)
        report['status'] = 'PASS'
    except Exception as exc:
        report.update(status='FAIL', error=str(exc))
        raise
    finally:
        (out / 'results.json').write_text(json.dumps(report, indent=2) + '\n', encoding="utf-8")
        # Preserve each run's evidence; compatibility consumers use the latest copy.
        for path in out.iterdir():
            if path.is_file():
                shutil.copy2(path, latest / path.name)


if __name__ == '__main__':
    main()
