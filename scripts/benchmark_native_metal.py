"""Alternate legacy/C in isolated real Blender windows; compare full output pixels.

Requires NumPy (Blender's bundled Python works). This measures scripted RNA
receipt to viewport draw, never display scanout. Five pairs of 60s by default.
No implementation flags or machine power preferences are tuned between modes.
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

import numpy as np

from blender_test_profile import isolated_environment
from collect_gate0_baseline import artifact_digests

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / 'tests/integration/native_sustained_ui.py'


def summarize(pairs):
    fields = {'feedback_hz': lambda s: s['feedback_hz'],
              'callback_p95_ms': lambda s: s['callback_ms']['p95'],
              'callback_p99_ms': lambda s: s['callback_ms']['p99'],
              'evaluation_p95_ms': lambda s: s['evaluation_ms']['p95'],
              'receipt_to_draw_p95_ms': lambda s: s['receipt_to_draw_ms']['p95'],
              'receipt_to_draw_p99_ms': lambda s: s['receipt_to_draw_ms']['p99'],
              'displayed_state_age_p95_ms': lambda s: s['displayed_state_age_ms']['p95'],
              'ideal_tracking_error_p95': lambda s: s['ideal_tracking_error']['p95'],
              'received_commands': lambda s: s['commands'],
              'superseded_fraction': lambda s: s['superseded'] / s['commands']}
    result = {}
    for field, get in fields.items():
        legacy = [get(p['legacy']['summary']) for p in pairs]
        native = [get(p['native']['summary']) for p in pairs]
        relative = [100 * (c / p - 1) for p, c in zip(legacy, native)]
        deltas = [c - p for p, c in zip(legacy, native)]
        result[field] = dict(legacy_median=statistics.median(legacy), native_median=statistics.median(native),
                             paired_native_minus_legacy=deltas, paired_percent_changes=relative,
                             median_paired_percent_change=statistics.median(relative),
                             native_better_pairs=(None if field == 'received_commands' else
                               sum(d > 0 if field == 'feedback_hz' else d < 0 for d in deltas)))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blender', default='/Applications/Blender.app/Contents/MacOS/Blender')
    parser.add_argument('--lib', type=Path, required=True)
    parser.add_argument('--pairs', type=int, default=5)
    parser.add_argument('--seconds', type=float, default=60)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--require-no-warm-allocations', action='store_true',
                        help='Fail if the optimized native executor allocates a texture during observation')
    args = parser.parse_args()
    if args.pairs < 1 or args.seconds <= 0:
        parser.error('Positive pair count and duration required')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    out = args.output or ROOT / '.blender-test/native/paired-metal' / run_id
    out.mkdir(parents=True, exist_ok=False)
    initial = artifact_digests(ROOT, args.lib)
    environment = {}
    for name, cmd in {'os': ['sw_vers'], 'hardware': ['/usr/sbin/sysctl', '-n', 'machdep.cpu.brand_string'],
                      'power': ['pmset', '-g', 'custom'], 'power_source': ['pmset', '-g', 'batt']}.items():
        response = subprocess.run(cmd, capture_output=True, text=True)
        environment[name] = dict(exit=response.returncode, stdout=response.stdout.strip(), stderr=response.stderr.strip())
    result = dict(status='RUNNING', run_id=run_id, artifacts=initial, environment=environment, pairs=[],
                  worker_sha256=hashlib.sha256(WORKER.read_bytes()).hexdigest(),
                  runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  baseline='Current release with OP_NATIVE_ENGINE=0; not a historical release checkout',
                  protocol=f'Fresh process/profile per run; {args.pairs} AB/BA pairs; same 2K eight-channel material, viewport, 60Hz ideal opacity trajectory; no readback during timing',
                  require_no_warm_allocations=args.require_no_warm_allocations,
                  seconds=args.seconds, pair_count=args.pairs, warm_edit_seconds=15)
    print('BENCHMARK_OUTPUT', str(out), flush=True)

    def save():
        (out/'results.json').write_text(json.dumps(result, indent=2)+'\n', encoding="utf-8")

    def run(pair, mode):
        path = out / f'pair-{pair}-{mode}.json'
        load_start = os.getloadavg()
        with tempfile.TemporaryDirectory(prefix='op-paired-metal-') as profile:
            env = isolated_environment(profile)
            env.pop('OP_ENGINE_PIXEL_PUBLISH', None)
            env.update(OP_LIBOPENPAINTER_PATH=str(args.lib.resolve()),
                       OP_NATIVE_ENGINE='1' if mode == 'native' else '0',
                       OP_SUSTAINED_BACKEND='c17-host-metal' if mode == 'native' else 'legacy',
                       OP_SUSTAINED_SECONDS=str(args.seconds), OP_SUSTAINED_RUNS='1',
                       OP_SUSTAINED_REPORT=str(path.resolve()), OP_SUSTAINED_PIXELS='1',
                       OP_SUSTAINED_WARM_EDITS='15')
            cmd = [args.blender, '--factory-startup', '--python-exit-code', '1',
                   '--enable-event-simulate', '--python', str(WORKER), '--', '-ApplePersistenceIgnoreState', 'YES']
            with path.with_suffix('.log').open('w') as log:
                completed = subprocess.run(cmd, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                                           timeout=args.seconds + 150)
            assert completed.returncode == 0, f'{mode} failed: {path.with_suffix(".log")}'
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data['status'] == 'PASS', data.get('error')
        assert data['execution_backend'] == env['OP_SUSTAINED_BACKEND'], data['execution_backend']
        assert data['backend'] == 'METAL' and len(data['runs']) == 1
        row = data['runs'][0]
        assert row['summary']['duration_s'] >= args.seconds
        assert row['summary']['displayed_revisions'] > 0
        assert row['warm_evaluations'] >= data['minimum_warm_evaluations']
        counts = data['instrumentation']['counters']
        assert not counts.get('engine_program_compiles', 0) and not counts.get('engine_graph_builds', 0)
        if mode == 'native' and args.require_no_warm_allocations:
            assert not counts.get('engine_texture_allocations', 0), counts
        for channel in ('engine_image_pixel_writes', 'engine_texture_readbacks'):
            assert not counts.get(channel, 0), channel
        print('PAIRED_RUN', pair, mode, row['summary'], flush=True)
        return dict(report=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    system_load_start=load_start, system_load_end=os.getloadavg(),
                    summary=row['summary'], memory_start=row['memory_start'], memory_end=row['memory_end'],
                    counters=counts, viewport=data['viewport'], gpu=[data['vendor'],data['renderer'],data['driver']],
                    blender=[data['blender'],data['build']], pixels=data['parity']['pixels'])

    try:
        save()
        for number in range(args.pairs):
            order = ('legacy','native') if number % 2 == 0 else ('native','legacy')
            pair = dict(pair=number, order=order)
            for mode in order:
                pair[mode] = run(number, mode)
            for field in ('viewport','gpu','blender'):
                assert pair['legacy'][field] == pair['native'][field], field
            errors = {}
            with np.load(pair['legacy']['pixels']) as legacy, np.load(pair['native']['pixels']) as native:
                assert set(legacy.files) == set(native.files) and len(legacy.files) == 8
                for channel in legacy.files:
                    a, b = legacy[channel], native[channel]
                    assert a.shape == b.shape and a.shape[:2] == (2048, 2048), (channel, a.shape, b.shape)
                    assert np.isfinite(a).all() and np.isfinite(b).all(), channel
                    errors[channel] = float(np.max(np.abs(a-b)))
                    assert errors[channel] <= 1e-3, (channel,errors[channel])
            pair['full_pixel_max_errors'] = errors
            # Pixel arrays were only a correctness oracle; retain their hashes,
            # comparisons and traces rather than gigabytes of duplicate maps.
            for mode in order:
                path = Path(pair[mode].pop('pixels'))
                pair[mode]['pixels_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            result['pairs'].append(pair)
            result['summary'] = summarize(result['pairs'])
            save()
            print('PAIR_PASS', number, errors, flush=True)
        assert artifact_digests(ROOT,args.lib) == initial, 'Implementation changed during measurements'
        assert hashlib.sha256(WORKER.read_bytes()).hexdigest() == result['worker_sha256']
        result['status'] = 'PASS'
    except Exception as error:
        result.update(status='FAIL', error=str(error))
        raise
    finally:
        save()
    print('PAIRED_BENCHMARK_PASS', str(out/'results.json'), flush=True)


if __name__ == '__main__':
    main()
