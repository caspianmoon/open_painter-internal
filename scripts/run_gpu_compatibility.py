"""Run simulated backend contracts and real GPU parity, recording their distinct scope.

With no Vulkan device, run on Metal or OpenGL: the pixel-publication suite and a
second engine-parity run exercise Vulkan's publication route on that real GPU.
This does not emulate a Vulkan driver or claim hardware certification.
"""
import argparse
import json
import os
import subprocess
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blender', default='/Applications/Blender.app/Contents/MacOS/Blender')
    parser.add_argument('--backend', choices=('metal', 'opengl', 'vulkan'),
                        help='Require this actual backend; a fallback fails the run')
    parser.add_argument('--output', type=Path, default=ROOT / '.blender-test/backend-validation')
    parser.add_argument('--sampler-limit', type=int, default=16,
                        help='Cap engine samplers per pass to exercise constrained GPUs (default: 16)')
    args = parser.parse_args()
    if args.sampler_limit < 2:
        parser.error('--sampler-limit must be at least 2')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, BLENDER_USER_RESOURCES=str(output / 'user'))
    env.pop('OP_ENGINE_PIXEL_PUBLISH', None)
    reports = {'OP_ENGINE_BACKEND_REPORT': 'backend-contract.json',
               'OP_ENGINE_PARITY_REPORT': 'engine-parity.json',
               'OP_ENGINE_PROCEDURALS_REPORT': 'engine-procedurals.json'}
    runs = [('simulated-contracts', None, False),
            ('backend-contract', 'engine_backend_contract.py', False),
            ('procedurals', 'engine_procedurals.py', False),
            ('engine-parity', 'engine_parity.py', False),
            ('pixel-parity', 'engine_parity.py', True),
            ('pixel-publication', 'engine_pixel_publish.py', True),
            ('engine-stages', 'engine_stages.py', False),
            ('material-nodes', 'material_nodes.py', False)]
    results = []
    for name, script, pixels in runs:
        folder = output / name
        folder.mkdir(exist_ok=True)
        metadata = folder / 'backend.json'
        metadata.unlink(missing_ok=True)
        case_env = dict(env, **{key: str(folder / filename) for key, filename in reports.items()})
        if pixels:
            case_env['OP_ENGINE_PIXEL_PUBLISH'] = '1'
        command = [args.blender, '--background', '--factory-startup']
        if args.backend:
            command += ['--gpu-backend', args.backend]
        command += ['--python-exit-code', '1']
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
                f'Path({str(metadata)!r}).write_text(json.dumps(dict(backend=actual, '
                'renderer=gpu.platform.renderer_get(), blender=bpy.app.version_string, '
                'sampler_limit=limit))); '
                f'assert {args.backend is None!r} or actual == {str(args.backend).upper()!r}, actual; '
                f'runpy.run_path({str(ROOT / "tests/integration" / script)!r}, run_name="__main__")')
        command += ['--python-expr', expression]
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
            row.update(json.loads(metadata.read_text()))
        results.append(row)
        report = dict(requested_backend=args.backend, tests=results,
                      status=('FAIL' if any(row['status'] == 'FAIL' for row in results) else
                              'PASS' if len(results) == len(runs) else 'RUNNING'),
                      limitation='Simulated contracts and forced publication do not validate a Vulkan/OpenGL driver.')
        (output / 'report.json').write_text(json.dumps(report, indent=2))
        print(f'{name}: {row["status"]} ({row["seconds"]}s)', flush=True)
    return 0 if all(row['status'] == 'PASS' for row in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
