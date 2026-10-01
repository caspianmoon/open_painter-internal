"""Run simulated backend contracts and real GPU parity, recording their distinct scope.

With no Vulkan device, run on Metal or OpenGL: the pixel-publication suite and a
second engine-parity run exercise Vulkan's publication route on that real GPU.
This does not emulate a Vulkan driver or claim hardware certification.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_test_profile import isolated_environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blender', default='/Applications/Blender.app/Contents/MacOS/Blender')
    parser.add_argument('--backend', choices=('metal', 'opengl', 'vulkan'),
                        help='Require this actual backend; a fallback fails the run')
    parser.add_argument('--output', type=Path, default=ROOT / '.blender-test/backend-validation')
    parser.add_argument('--sampler-limit', type=int, default=16,
                        help='Cap engine samplers per pass to exercise constrained GPUs (default: 16)')
    parser.add_argument('--tests', nargs='+', metavar='NAME',
                        help='Run only these named suites (for example backend-contract pixel-publication)')
    parser.add_argument('--cap-gb', type=float, default=4.0,
                        help='Kill a Blender process whose memory footprint passes this (scripts/run_guarded.py)')
    args = parser.parse_args()
    if args.sampler_limit < 2:
        parser.error('--sampler-limit must be at least 2')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.pop('OP_ENGINE_PIXEL_PUBLISH', None)
    reports = {'OP_ENGINE_BACKEND_REPORT': 'backend-contract.json',
               'OP_ENGINE_PARITY_REPORT': 'engine-parity.json',
               'OP_ENGINE_PROCEDURALS_REPORT': 'engine-procedurals.json'}
    runs = [('simulated-contracts', None, False),
            ('backend-contract', 'engine_backend_contract.py', False),
            ('compact-readback', 'engine_compact_readback.py', False),
            ('preview-readback', 'engine_preview_readback.py', False),
            ('state-cache', 'engine_state_cache.py', False),
            ('memory-soak', 'engine_memory_soak.py', False),
            ('procedurals', 'engine_procedurals.py', False),
            ('engine-parity', 'engine_parity.py', False),
            ('pixel-parity', 'engine_parity.py', True),
            ('pixel-publication', 'engine_pixel_publish.py', True),
            ('uniform-publication', 'engine_uniform_publish.py', False),
            ('fused-publication', 'engine_fused_publish.py', False),
            ('constant-publication', 'engine_constant_publish.py', False),
            ('engine-stages', 'engine_stages.py', False),
            ('gpu-strokes', 'gpu_strokes.py', False),
            ('pixel-strokes', 'gpu_strokes.py', True),
            ('authoring-workflow', 'authoring_workflow.py', False),
            ('pixel-authoring-workflow', 'authoring_workflow.py', True),
            ('gpu-filters', 'engine_filters.py', False),
            ('authoring-sources', 'authoring_sources.py', False),
            ('material-nodes', 'material_nodes.py', False)]
    if args.tests:
        unknown = set(args.tests) - {name for name, _, _ in runs}
        if unknown:
            parser.error('Unknown suites: ' + ', '.join(sorted(unknown)))
        runs = [case for case in runs if case[0] in args.tests]
    results = []
    for name, script, pixels in runs:
        folder = output / name
        folder.mkdir(exist_ok=True)
        metadata = folder / 'backend.json'
        metadata.unlink(missing_ok=True)
        profile = folder / 'user'
        case_env = isolated_environment(profile, env)
        case_env.update({key: str(folder / filename) for key, filename in reports.items()})
        if pixels:
            case_env['OP_ENGINE_PIXEL_PUBLISH'] = '1'
        # Every Blender launch goes through the memory guard, one at a time.
        command = [sys.executable, str(ROOT / 'scripts/run_guarded.py'), '--cap-gb', str(args.cap_gb),
                   '--start-gb', '3', '--wait-seconds', '120', '--', args.blender, '--background', '--factory-startup']
        if args.backend:
            command += ['--gpu-backend', args.backend]
        command += ['--python-exit-code', '1']
        # Verify Blender's resolved profile before any GPU setup or test code.
        guard = (
            'import bpy,json,sys; from pathlib import Path; '
            f'sys.path.insert(0, {str(Path(__file__).resolve().parent)!r}); '
            'from blender_test_profile import assert_blender_profile; '
            f'profile=assert_blender_profile(bpy, {str(profile)!r}); '
            f'Path({str(metadata)!r}).write_text(json.dumps(dict(profile_isolation=profile))); ')
        if script is None:
            expression = (
                "import unittest; suite=unittest.defaultTestLoader.discover('tests/unit', "
                "pattern='test_gpu_backend_contract.py'); "
                "result=unittest.TextTestRunner(verbosity=2).run(suite); assert result.wasSuccessful()")
        else:
            # Verify the actual backend in each fresh process, before running its test.
            expression = (
                'import bpy,gpu,json,runpy,sys; from pathlib import Path; '
                'from unittest.mock import patch; gpu.init(); '
                'actual=gpu.platform.backend_type_get(); '
                f'sys.path.insert(0, {str(ROOT / "src")!r}); '
                f'limit=min(gpu.capabilities.max_textures_frag_get(), {args.sampler_limit}); '
                'cap=patch.object(gpu.capabilities,"max_textures_frag_get",return_value=limit); cap.start(); '
                f'Path({str(metadata)!r}).write_text(json.dumps(dict(profile_isolation=profile, backend=actual, '
                'renderer=gpu.platform.renderer_get(), blender=bpy.app.version_string, '
                'sampler_limit=limit))); '
                f'assert {args.backend is None!r} or actual == {str(args.backend).upper()!r}, actual; '
                f'runpy.run_path({str(ROOT / "tests/integration" / script)!r}, run_name="__main__")')
        command += ['--python-expr', guard + expression]
        print(f'Running {name}', flush=True)
        start = perf_counter()
        with (folder / 'run.log').open('w') as log:
            try:
                completed = subprocess.run(command, cwd=ROOT, env=case_env, stdout=log,
                                           stderr=subprocess.STDOUT, timeout=600)
                code = completed.returncode
            except subprocess.TimeoutExpired:
                code = 'timeout'
        row = dict(name=name, status='PASS' if code == 0 else 'FAIL', returncode=code,
                   seconds=round(perf_counter() - start, 2),
                   scope='simulated API contracts' if script is None else
                         'forced pixel publication on actual GPU' if pixels else 'actual GPU',
                   log=str(folder / 'run.log'))
        if metadata.exists():
            row.update(json.loads(metadata.read_text(encoding="utf-8")))
        results.append(row)
        report = dict(requested_backend=args.backend, selected_tests=[case[0] for case in runs], tests=results,
                      status=('FAIL' if any(row['status'] == 'FAIL' for row in results) else
                              'PASS' if len(results) == len(runs) else 'RUNNING'),
                      limitation='Simulated contracts and forced publication do not validate a Vulkan/OpenGL driver.')
        (output / 'report.json').write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f'{name}: {row["status"]} ({row["seconds"]}s)', flush=True)
    return 0 if all(row['status'] == 'PASS' for row in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
