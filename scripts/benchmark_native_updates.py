"""Compare a released native ZIP against the candidate using completed GPU updates.

This complements the viewport benchmark; it does not measure input-to-screen latency.
Run with Blender's bundled Python (NumPy required).
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import tempfile
from zipfile import ZipFile

import numpy as np

from blender_test_profile import isolated_environment
from collect_gate0_baseline import artifact_digests

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / 'tests/integration/native_update_latency.py'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-package', type=Path, required=True)
    parser.add_argument('--lib', type=Path, required=True)
    parser.add_argument('--blender', default='/Applications/Blender.app/Contents/MacOS/Blender')
    parser.add_argument('--pairs', type=int, default=3)
    parser.add_argument('--samples', type=int, default=100)
    args = parser.parse_args()
    if args.pairs < 1 or args.samples < 1:
        parser.error('Positive pair and sample counts required')
    out = ROOT / '.blender-test/native/completed-updates' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    out.mkdir(parents=True)
    artifacts = artifact_digests(ROOT, args.lib)
    data = dict(status='RUNNING', artifacts=artifacts, pairs=[],
                baseline_package_sha256=hashlib.sha256(args.baseline_package.read_bytes()).hexdigest(),
                worker_sha256=hashlib.sha256(WORKER.read_bytes()).hexdigest(),
                runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                scope='Twenty warm edits, then identical fixed trajectories in fresh background processes. Every update waits for GPU completion. Not viewport latency.')
    def save():
        (out / 'results.json').write_text(json.dumps(data, indent=2) + '\n', encoding="utf-8")
    print('UPDATE_BENCHMARK_OUTPUT', out, flush=True)
    with tempfile.TemporaryDirectory(prefix='op-old-release-') as baseline_dir:
        baseline = Path(baseline_dir) / 'open_painter'
        with ZipFile(args.baseline_package) as archive:
            for name in archive.namelist():
                assert not Path(name).is_absolute() and '..' not in Path(name).parts
            archive.extractall(baseline)
        old_lib, = baseline.glob('native_bridge/bin/*/libopenpainter.dylib')
        def run(pair, mode):
            path = out / f'pair-{pair}-{mode}.json'
            with tempfile.TemporaryDirectory(prefix='op-update-profile-') as profile:
                env = isolated_environment(profile)
                env.update(OP_NATIVE_ENGINE='1', OP_UPDATE_REPORT=str(path), OP_UPDATE_SAMPLES=str(args.samples),
                           OP_LIBOPENPAINTER_PATH=str(old_lib if mode == 'baseline' else args.lib.resolve()),
                           OP_UPDATE_SOURCE=str(baseline if mode == 'baseline' else ROOT / 'src/open_painter'))
                load = os.getloadavg()
                with path.with_suffix('.log').open('w') as log:
                    result = subprocess.run([args.blender, '--background', '--factory-startup', '--python-exit-code', '1',
                        '--python', str(WORKER)], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=300)
                assert result.returncode == 0, path.with_suffix('.log')
            row = json.loads(path.read_text(encoding="utf-8"))
            assert row['status'] == 'PASS'
            if mode == 'candidate':
                assert row['counters'].get('engine_texture_allocations', 0) == 0, row['counters']
            row.update(report=path.name, system_load_start=load, system_load_end=os.getloadavg())
            print('UPDATE_RUN', pair, mode, row['median_ms'], row['p95_ms'], flush=True)
            return row
        try:
            save()
            for pair in range(args.pairs):
                order = ('baseline', 'candidate') if pair % 2 == 0 else ('candidate', 'baseline')
                row = dict(pair=pair, order=order)
                for mode in order:
                    row[mode] = run(pair, mode)
                for field in ('blender', 'build', 'backend'):
                    assert row['baseline'][field] == row['candidate'][field]
                with np.load(row['baseline']['pixels']) as a, np.load(row['candidate']['pixels']) as b:
                    assert set(a.files) == set(b.files) and len(a.files) == 8
                    errors = {key: float(np.max(np.abs(a[key] - b[key]))) for key in a.files}
                    assert all(np.isfinite(error) and error == 0 for error in errors.values()), errors
                row['full_pixel_max_errors'] = errors
                data['pairs'].append(row)
                data['median_paired_speedup'] = statistics.median(p['baseline']['median_ms'] / p['candidate']['median_ms'] for p in data['pairs'])
                save()
            assert artifact_digests(ROOT, args.lib) == artifacts
            assert hashlib.sha256(WORKER.read_bytes()).hexdigest() == data['worker_sha256']
            data['status'] = 'PASS'
        except BaseException as error:
            data.update(status='FAIL', error=str(error))
            raise
        finally:
            save()
    print('UPDATE_BENCHMARK_PASS', out, data['median_paired_speedup'], flush=True)


if __name__ == '__main__':
    main()
