#!/usr/bin/env python3
"""Derive a reviewable campaign configuration from inspected, immutable evidence.

This writes configuration only. The existing benchmark runner owns freezing,
scheduling, authentication, Blender and provider execution.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import sys


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--smoke', type=Path, required=True)
    parser.add_argument('--baseline-checkout', type=Path, required=True)
    parser.add_argument('--fixtures', type=Path, required=True)
    parser.add_argument('--no-edit', type=Path, required=True)
    parser.add_argument('--calibration-selection', type=Path, required=True)
    parser.add_argument('--concurrency-proof', type=Path, required=True)
    parser.add_argument('--imagegen-evidence', type=Path,
                        help='Actual result.json with a generated image, if the smoke did not request one')
    parser.add_argument('--comparison-id', required=True)
    parser.add_argument('--comparison-output', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    smoke = args.smoke.resolve()
    runtime = smoke / '_runtime'
    if not (runtime / '.benchmark_product_pin.json').is_file():
        parser.error('Use the immutable runtime of the completed five-case smoke')
    sys.path.insert(0, str(runtime / 'tests/benchmarks'))
    from material_director import manifest, runner
    from material_director.png_validation import decoder_backend

    config = manifest.configuration_template()
    config.update(comparison_id=args.comparison_id, correction_ablation=True)
    config['build'] = {k:v for k,v in runner.source_fingerprint(runtime).items() if k != 'files'}
    config['builds'] = dict(baseline=runner.product_fingerprint(args.baseline_checkout),
                            improved=runner.product_fingerprint(runtime))
    index_path = smoke / 'smoke_index.json'
    index = read(index_path)
    if index.get('status') != 'INSPECTED' or len(index.get('cases', [])) != 5:
        parser.error('Inspect the actual five-case smoke and record its notes before preparing the final configuration')
    results = []
    for item in index['cases']:
        path = Path(item['result_path'])
        path = path if path.is_absolute() else smoke / path
        results.append((path, read(path)))
    observed = results[0][1]
    provider = observed['provider']
    config['provider'].update(runtime_version=provider['runtime_version'],
        model_id=provider['returned_model_id'], returned_model_id=provider['returned_model_id'])
    environment = observed['environment']
    # Blender checks the exact version string; its build hash is separate metadata.
    config['environment'].update(hardware=environment['hardware'], blender_version=environment['blender_version'],
        blender_build_hash=environment.get('blender_build_hash'), render_backend=environment['render_engine'],
        evaluator_backend=environment['evaluation'], material_library_sha256=runner.library_fingerprint(runtime),
        png_decoder=decoder_backend())
    config['limits'] = read(smoke / 'manifest.json')['configuration']['limits']
    config['pilot'] = dict(status='PASS', evidence=str(index_path),
        meaning='Five actual provider cases inspected; this operational gate is not an artistic pass')

    availability = results + ([(args.imagegen_evidence.resolve(), read(args.imagegen_evidence))]
                              if args.imagegen_evidence else [])
    generation_proof = None
    for path, result in availability:
        for event in result.get('actual_generated_inputs', []):
            image = path.parent / event.get('path', '')
            if image.is_file() and runner.sha256(image) == event.get('sha256'):
                generation_proof = dict(result_path=str(path), result_sha256=runner.sha256(path),
                    image_path=str(image), image_sha256=event['sha256'], observed_provider=result.get('provider'),
                    basis='Observed prior generated image availability; generation is optional in hybrid mode')
                break
        if generation_proof:
            break
    if not generation_proof:
        parser.error('Supply actual image-generation evidence; absence of a generation request does not prove unavailability')
    config['provider'].update(image_generation_available=True, image_generation_evidence=generation_proof)

    proof_path = args.concurrency_proof.resolve()
    proof = read(proof_path)
    config['execution'].update(jobs=2, policy='PAIRED_WAVES', concurrency_evidence=str(proof_path),
        concurrency_evidence_sha256=runner.sha256(proof_path), concurrency_proof=proof,
        concurrency_proof_sha256=manifest.digest(proof))
    selection_path = args.calibration_selection.resolve()
    selection = read(selection_path)
    if selection.get('product') != config['builds']['improved']:
        parser.error('Authored calibration selection must match the exact improved product')
    if set(selection.get('cases', {})) != {f'T{i:02d}' for i in range(1,6)}:
        parser.error('Select exactly five authored calibration cases')
    if selection.get('status') not in {'PASS', 'READY'}:
        parser.error('Authored calibration selection is not complete')
    config['calibration'].update(status='CAPTURED_NOT_SCORED',
        evidence=[dict(path=str(selection_path), sha256=runner.sha256(selection_path), selection=selection)],
        disclosure='Deterministic authored controls, not human reference ratings; never provider inputs')
    repeat_path = args.no_edit.resolve()
    repeat = read(repeat_path)
    config['evidence']['no_edit_calibration'] = dict(status='PASS', evidence=str(repeat_path),
        sha256=runner.sha256(repeat_path), report=repeat, report_sha256=manifest.digest(repeat))
    config['evidence']['semantic_annotations'] = dict(status='PENDING',
        disclosure='Generic layer masks do not establish independent semantic labels')
    for name in ('capture_evidence.py', 'revision_measurements.py'):
        if repeat.get('implementation', {}).get(name) != runner.sha256(runtime / 'tests/benchmarks/material_director' / name):
            parser.error('Repeatability proof used a different evidence implementation: ' + name)
    manifest.validate_configuration(config)
    runner._verify_pilot(config)
    fixtures = runner.fixture_records(args.fixtures)
    if any(record.get('status') != 'READY' for record in fixtures.values()):
        parser.error('All neutral fixtures must have READY reports')
    if fixtures != read(smoke / 'manifest.json')['fixtures']:
        parser.error('Final fixtures must be the exact fixtures used by the smoke')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(config, stream, indent=2, allow_nan=False)
        stream.write('\n')
    runner_path = runtime / 'tests/benchmarks/material_director/runner.py'
    commands = [
        ['python3', str(runner_path), 'freeze', '--config', str(args.output.resolve()), '--fixtures', str(args.fixtures.resolve()),
         '--output', str(args.comparison_output.resolve())],
        ['python3', str(runner_path), 'run', '--comparison', str(args.comparison_output.resolve()),
         '--fixtures', str(args.fixtures.resolve()), '--baseline-checkout', str(args.baseline_checkout.resolve()),
         '--improved-checkout', str(runtime), '--jobs', '2', '--use-codex-login'],
    ]
    print(json.dumps(dict(configuration=str(args.output.resolve()), core_sessions=60, correction_ablation_sessions=30,
        commands=[shlex.join(command) for command in commands],
        note='Add --codex PATH if Codex is not on PATH. Run again to resume genuinely unattempted jobs only.'), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
