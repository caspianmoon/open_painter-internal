"""Two visible Blender processes, no provider: prove process-local UI/Eevee capture.

This writes REQUIRES_IMAGE_INSPECTION, never an automatic visual-isolation PASS.
Inspect both native screenshots and fixed renders before recording a PASS proof.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import time

from runtime import run_blender, sha256, write_json
from png_validation import decode_png

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blender', default='/Applications/Blender.app/Contents/MacOS/Blender')
    parser.add_argument('--fixture', type=Path, action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if len(args.fixture) != 2 or len({p.resolve() for p in args.fixture}) != 2:
        parser.error('Exactly two distinct prepared fixture files are required')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    started = time.time()

    def capture(index, fixture):
        destination = output / ('process-' + str(index))
        destination.mkdir()
        with tempfile.TemporaryDirectory(prefix='director-concurrency-' + str(index) + '-') as profile:
            env = dict(os.environ, BLENDER_USER_RESOURCES=profile, TMPDIR=profile, CODEX_HOME=str(Path(profile) / 'no-provider'))
            Path(env['CODEX_HOME']).mkdir()
            for key in ('OPENAI_API_KEY', 'OP_MD_BENCHMARK_AUTH', 'OP_MD_BENCHMARK_CODEX', 'OP_MD_PRODUCT_ROOT'):
                env.pop(key, None)
            command = [args.blender, '--factory-startup', '--disable-autoexec', '--offline-mode',
                '--python-exit-code', '1', '--python', str(HERE / 'test_ui_evidence_blender.py'), '--',
                '--fixture', str(fixture.resolve()), '--output', str(destination), '--helper', '--render-proof',
                '--probe-id', str(index), '--concurrency-barrier', str(output / 'barrier')]
            code = run_blender(command, env=env, timeout=240, log=destination / 'blender.log')
        path = destination / 'ui_probe.json'
        report = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {'status':'FAILED', 'error':'No terminal probe record'}
        report.update(exit_code=code, output=str(destination), fixture=str(fixture.resolve()))
        for image in destination.rglob('*.png'):
            report.setdefault('verified_images', []).append(dict(path=str(image.relative_to(output)),
                sha256=sha256(image), decoded=decode_png(image.read_bytes())))
        return report

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(capture, i + 1, fixture) for i, fixture in enumerate(args.fixture)]
        reports = [future.result() for future in futures]
    overlap = max(0, min(r.get('finished_at', started) for r in reports) - max(r.get('started_at', started) for r in reports))
    valid = overlap > 0 and len({r.get('pid') for r in reports}) == 2 and all(
        r.get('status') == 'CAPTURED_REQUIRES_IMAGE_INSPECTION' and r.get('exit_code') == 0 and
        r.get('fixed_rig_after', {}).get('status') == 'PASS' and r.get('provider_calls') == 0 and
        r.get('renders', {}).get('engine') == 'BLENDER_EEVEE' for r in reports)
    proof = dict(kind='DETERMINISTIC_CONCURRENCY_PROBE', status='REQUIRES_IMAGE_INSPECTION' if valid else 'FAILED',
        processes=2, isolated_capture=None, provider_calls=0, started_at=started, finished_at=time.time(),
        process_overlap_seconds=overlap, peak_process_rss_sum_upper_bound_bytes=sum(r.get('peak_rss_bytes', 0) for r in reports),
        memory_basis='Sum of per-process high-water RSS; peaks need not coincide; shared GPU allocation is not measured',
        timing_basis='Shared-device process wall time; includes contention and is not uncontended GPU performance',
        reports=reports, image_inspection=None)
    write_json(output / 'concurrency_probe.json', proof)
    print(output / 'concurrency_probe.json')
    return 0 if valid else 1


if __name__ == '__main__':
    raise SystemExit(main())
