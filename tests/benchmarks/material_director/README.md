# Material Director benchmark

The existing runner now supports `five_asset_eevee_v3`. Read [test_plan_v3.md](test_plan_v3.md) and the exact [cases](test_cases_v3.json). V2 fixtures/results stay unchanged; [v2/](v2/) supplies the explicit historical manifest/scoring adapter.

## Live diagnostic smoke

```sh
python3 scripts/benchmark_material_director.py smoke \
  --fixtures .blender-test/material-director-benchmark/fixtures-eevee-v3 \
  --output .blender-test/material-director-benchmark/remedy-v3-smoke-01 \
  --model gpt-6-astra --codex /absolute/path/to/codex --use-codex-login
```

This freezes a private `_runtime` copy of the actual production and harness bytes, then runs five fresh H sessions through ordinary Director. It writes a diagnostic manifest and `smoke_index.json`. Inspect all real results, including failures; populate `inspection_notes` and change the index to `INSPECTED`. This is operational inspection, never an independent artistic grade. Optional `--limits caps.json` accepts the same wall limits later frozen for the campaign.

`smoke --jobs 2 --concurrency-evidence PROOF.json` uses bounded diagnostic waves after an actual two-process isolation proof. Source snapshots remain private; Blender profiles, temporary files, provider caches, and output directories are per process.

Before each wave, the launcher checks the existing access token without changing credentials. Missing/expiring login pauses scheduling with `AUTH_REFRESH_REQUIRED` in `execution_control.json` and exit code 3. No attempt marker or AI failure is created for those sessions. Renew the normal managed Codex login outside the benchmark, then repeat the campaign `run` command. A partial diagnostic smoke resumes with the same arguments plus `--resume`, requiring the same immutable snapshot, fixtures and model; completed attempts are retained. A smoke stopped before its snapshot can repeat its original command. The harness never refreshes the real account or copies its refresh token.

## Paired campaign

Create an unmodified baseline checkout/archive at the reviewed commit, then:

```sh
python3 scripts/benchmark_material_director.py config --baseline-checkout BASELINE --output config.json
python3 scripts/benchmark_material_director.py freeze --config config.json --fixtures FIXTURES --output COMPARISON --correction-ablation
python3 scripts/benchmark_material_director.py run --comparison COMPARISON --fixtures FIXTURES --baseline-checkout BASELINE --improved-checkout IMPROVED --codex CODEX --use-codex-login
```

Resolve actual model/runtime/Blender/library/build pins and cite the inspected five-case smoke index before freezing. The default schedule has 60 sessions; `--correction-ablation` adds 30 in a separate randomized phase. Ablation never enters primary P/H means. Execute from the frozen runtime copy if development continues elsewhere. Source, fixture and model drift are rejected; finished attempts cannot be rerolled into the same ID. Account credentials are temporary access-only copies; the user's refresh token is never copied or changed.

For two-process execution, first run `tests/benchmarks/material_director/probe_concurrency.py` with two distinct `--fixture` files and a fresh `--output` directory. It captures native UI plus Eevee frames with zero provider calls and requires actual pixel inspection before a PASS proof. Freeze using `--jobs 2 --concurrency-evidence PROOF.json`, then run with `--jobs 2`. The first 60 sessions execute in adjacent baseline/improved pairs; the next 30 are correction-off diagnostics. RSS and preview wall time disclose shared-device contention; neither is a hardware GPU counter or uncontended smoke timing.

Human reference/reviewer may be unavailable by explicit user instruction. Keep reviewer PENDING, scores NOT_SCORED, deterministic calibration separately labeled, and report all planned/attempted runs plus the assessable-appearance subset.

Actual five-case no-edit recapture is an engineering prerequisite for freezing. Put its path/file checksum and embedded report/canonical digest in `evidence.no_edit_calibration`; the freezer verifies all five source files were unchanged, exactly two recaptures occurred, deltas fit the frozen tolerances, and the tested product/evaluator bytes match. Human artistic review remains separate.

The config-only helper derives final pins from the inspected smoke's immutable runtime and validates the authored calibration and technical repeatability proof:

```sh
python3 scripts/prepare_material_director_campaign.py \
  --smoke SMOKE --baseline-checkout BASELINE --fixtures FIXTURES \
  --calibration-selection frozen_selection.json --no-edit repeatability.json \
  --concurrency-proof concurrency_probe.json --imagegen-evidence PRIOR_RESULT_WITH_GENERATED_IMAGE \
  --comparison-id remedy-v3-paired-01 --comparison-output COMPARISON --output final-config.json
```

It prints the existing runner's freeze/run commands for 60 primary and 30 correction-off sessions. It cannot launch providers or mark the smoke inspected. The image-generation evidence option is optional when the smoke actually generated an image; a previously recorded successful generation is explicitly disclosed as historical availability evidence. A smoke against a different improved product, provider runtime, renderer or environment cannot authorize the final comparison.

Effect records distinguish whole layer masks from their declared controls. A sole native inverted paint-loss root exposes its pre-strength source and mask-strength opacity; a composite or zero-strength inverse is `NOT_MEASURED`. Generic baseline masks retain valid before-layer-opacity evidence without guessing their semantic role from names. Mask equality remains measurable for tiny effects even when an occupied-area ratio is undefined. Areas use four fixed interior samples per triangle in world units squared; thin-feature accuracy and a universal physical-height frame are not established.

## Recovery and portable delivery

```sh
python3 scripts/benchmark_material_director.py recover-evidence --session SESSION --state final --output NEW_RECOVERY
python3 scripts/benchmark_material_director_gallery.py COMPARISON --format portable --output review.html
python3 scripts/benchmark_material_director_gallery.py COMPARISON --format portable-cases --output portable-cases
python3 scripts/benchmark_material_director_gallery.py COMPARISON --format zip --output /outside/comparison/evidence.zip
```

Recovery is offline and uses the saved state's checksum; it never runs an instruction or changes the original record. Optional `--separate-blends` creates a declared companion ZIP. `--format html` produces relative links for the complete directory. `report` and `review-pack` retain the existing scoring and anonymous human-review workflow. The optional macOS `scripts/validate_material_review_offline.swift` opens the portable file in fresh WebKit and decodes every embedded image.

Full-campaign embedded HTML can exceed 1 GiB. `portable-cases` writes five standalone files, each containing its own images, and a small linked index. Each case file can be copied alone into an empty directory; the index requires its five sibling files. `--case T01` filters an individual HTML export. The full-comparison HTML and full evidence ZIP remain available.

PNG validation always checks chunk checksums, dimensions, the complete decompressed stream, filter bytes and actual pixel decoding. An identifiable installed libpng 1.6 library accelerates full decoding; its version, resolved file and checksum are frozen in `environment.png_decoder` and verified again before execution. No package is installed. The dependency-free decoder remains available when that library is absent. Raw scanlines plus RGBA output have a 256 MiB per-image allocation bound, and the decoded-metadata cache holds at most 256 entries. Indexed images retain explicit palette-index validation.

`environment.png_decoder` identifies the **host launcher's evidence-normalization decoder**, recorded after Blender exits. Blender's bundled Python may use the pure-Python fallback while the host uses libpng; those runtimes do not need the same decoder. The observer does not write this host pin. The freezer checks the completed smoke's normalized host record against the host used for the campaign.

## Checks

```sh
python3 -m unittest discover -s tests/unit -p 'test_material_director_benchmark*.py'
python3 tests/unit/test_director_quality_gate_regressions.py --repo .
```

`test_revision_measurements.py` uses Blender's bundled Python/NumPy. `test_v3_evidence_blender.py` opens the frozen vase in an ordinary visible Eevee process, confirms opacity/mask separation, captures added circumference views and verifies infrastructure restoration. These are deterministic controls, not live AI artistic evidence.
