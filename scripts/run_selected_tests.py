"""Run chosen background integration tests, each in a fresh isolated Blender under the memory guard.

    python3 scripts/run_selected_tests.py catalog_materials.py material_aging.py ...

Every test runs to completion even after a failure; the exit code is nonzero if
any failed. Logs land in .blender-test/selected/<run>/<test>.log. The native
library comes from OP_LIBOPENPAINTER_PATH when set, as for the other runners.
"""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import tempfile

from blender_test_profile import isolated_environment

ROOT = Path(__file__).resolve().parents[1]
# Suites that need more than the default cap; run_tests.py runs every suite at 4 GB. On Windows
# utility_maps.py commits 3.2 GiB (its 2048 border bake).
MEMORY_CAPS_GB = {'utility_maps.py': 4.0}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('tests', nargs='+')
    parser.add_argument('--blender', default='/Applications/Blender.app/Contents/MacOS/Blender')
    parser.add_argument('--cap-gb', type=float, default=3.0)
    parser.add_argument('--start-gb', type=float, default=2.0, help='Available memory needed to start each test')
    parser.add_argument('--timeout', type=float, default=900)
    parser.add_argument('--gpu-backend', choices=('metal', 'opengl', 'vulkan'),
                        help="Blender's --gpu-backend (default: the factory setting)")
    args = parser.parse_args()
    out = ROOT / '.blender-test/selected' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
    out.mkdir(parents=True, exist_ok=True)
    failed = []
    for name in args.tests:
        with tempfile.TemporaryDirectory(prefix='op-selected-') as profile:
            env = isolated_environment(profile)
            cap = max(args.cap_gb, MEMORY_CAPS_GB.get(name, 0.0))
            command = [sys.executable, str(ROOT / 'scripts/run_guarded.py'), '--cap-gb', str(cap),
                       '--start-gb', str(args.start_gb), '--wait-seconds', '120', '--', args.blender, '--background',
                       '--factory-startup', *(['--gpu-backend', args.gpu_backend] if args.gpu_backend else []),
                       '--python-exit-code', '1', '--python', str(ROOT / 'tests/integration' / name)]
            log = out / (name + '.log')
            with log.open('w') as handle:
                try:
                    code = subprocess.run(command, cwd=ROOT, env=env, stdout=handle, stderr=subprocess.STDOUT,
                                          timeout=args.timeout).returncode
                except subprocess.TimeoutExpired:
                    code = 'timeout'
        print(f'{name}: {"PASS" if code == 0 else f"FAIL ({code})"}', flush=True)
        if code != 0:
            failed.append(name)
    print('SELECTED_TESTS', 'FAIL' if failed else 'PASS', out, *failed, flush=True)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
