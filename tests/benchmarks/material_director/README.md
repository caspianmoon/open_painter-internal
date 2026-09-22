# Material Director: five-model Eevee benchmark

This implements the user's [revised protocol](test_plan.md). The primary benchmark is **five live sessions**, one each for toolbox, chair, boots, vase and rocks. Native image generation is enabled (H mode). Each session uses the exact Create prompt and two exact Refine prompts from [test_cases.json](test_cases.json).

The harness prepares clean geometry, fixes Eevee infrastructure and captures evidence. The tested Codex model only textures through Material Director. Humans judge blinded material results. No points are awarded for importing, rendering, fixing lights or preparing the scene.

## Primary workflow

1. Download and sterilize assets using [ASSETS.md](ASSETS.md). Use the new fixed Eevee fixtures, with fresh 1024 maps and no original appearance data. Case IDs:

   | Case | Asset | Fixture |
   |---|---|---|
   | T01 | Metal toolbox | `A01_toolbox_neutral.blend` |
   | T02 | Chair | `A02_chair_neutral.blend` |
   | T03 | Rubber boots | `A05_boots_neutral.blend` |
   | T04 | Ceramic vase | `A04_vase_neutral.blend` |
   | T05 | Rock set | `A06_rocks_neutral.blend` |

2. Run a non-scored pilot with the actual Codex login:

   ```sh
   python3 scripts/benchmark_material_director.py pilot \
     --fixtures .blender-test/material-director-benchmark/fixtures \
     --output .blender-test/material-director-benchmark/pilot-t04 \
     --case T04 --mode H --model YOUR_EXACT_MODEL_ID --use-codex-login
   ```

   Add `--blender PATH` and `--codex PATH` when needed. `--quick` is only a plumbing diagnostic and cannot qualify the full-evidence pilot. Default caps are 600 seconds for a single-set turn, 900 for a multi-set turn and 1,800 for a complete session. Freeze confirmed limits before scored runs.

3. Generate and complete the configuration using observed pilot metadata:

   ```sh
   python3 scripts/benchmark_material_director.py config --output .blender-test/benchmark-config.json
   ```

   Pin the actual selected and returned model, runtime, Blender, hardware, evaluator, library bytes and executed source bytes. The renderer must be `BLENDER_EEVEE`; render size is 1024 × 1024 and material-map resolution is 1024. The default matrix is `modes: ["H"]`, `repeat_count: 1`. Declare pilot evidence as its actual `result.json`.

4. Freeze a new comparison directory, then run the five sessions:

   ```sh
   python3 scripts/benchmark_material_director.py freeze \
     --config .blender-test/benchmark-config.json \
     --fixtures .blender-test/material-director-benchmark/fixtures \
     --output .blender-test/material-director-benchmark/primary-01

   python3 scripts/benchmark_material_director.py run \
     --comparison .blender-test/material-director-benchmark/primary-01 \
     --fixtures .blender-test/material-director-benchmark/fixtures \
     --use-codex-login
   ```

   `--max-sessions N` permits bounded batches. Finished or unavailable attempts are retained rather than rerolled. A changed build, fixture, library, runtime or environment requires a new comparison. Login copies and Blender profiles are isolated; the user's normal provider cache is never removed or refreshed.

5. Export blinded evidence for independent human review:

   ```sh
   python3 scripts/benchmark_material_director.py review-pack --comparison .blender-test/material-director-benchmark/primary-01
   python3 scripts/benchmark_material_director.py review-import \
     --comparison .blender-test/material-director-benchmark/primary-01 \
     --review returned-review.json
   python3 scripts/benchmark_material_director.py report --comparison .blender-test/material-director-benchmark/primary-01
   ```

   Share `review_packets/` without the manifest, traces or `review_key.private.json`. Review import merges independent ratings and retains their history. Missing review evidence remains `NOT_SCORED`; a passing Python test, generated PNG or applied layer stack is not an aesthetic judgment.

## Fixed evidence

Every primary session uses the same Eevee renderer, camera poses, lights, World, exposure and render settings. Model normalization happens during fixture preparation. The Director receives texturing-relevant context: actual model and isolated-target views, clean geometry/UV summaries, texture sets, dimensions and fresh AO, curvature, world-normal, position and thickness maps. No primary case includes a reference image.

Actual outgoing image bytes and settings are audited, including viewport sanity, hashes, map families/provenance and native-generation mode. Sharing flags alone do not establish that images reached the provider. Scene-material inspection replies are audited too.

The deterministic evaluator saves:

```text
00_before.png
01_create_front.png
02_create_back.png
03_create_close.png
04_refine1_front.png
05_refine1_back.png
06_refine1_close.png
07_refine2_front.png
08_refine2_back.png
09_refine2_close.png
```

Material checkpoints, generated-input ownership, input manifests, tool traces, receipts, channel buffers, screenshots and a final `.blend` accompany those images. Geometry, ordinary guard materials, protected artist layers and fixed Eevee infrastructure are checked for unauthorized changes.

## Scoring

Reviewers rate each dimension from 0–5. The median independent rating per dimension contributes `rating / 5 × weight` points. Keep Create, first revision and final ratings separate.

| Dimension | Weight |
|---|---:|
| Material appearance | 25 |
| Semantic/spatial placement | 25 |
| Prompt compliance | 20 |
| Editability | 15 |
| Revision fidelity | 15 |

The revised user protocol declares **no numerical pass threshold**. Weighted scores and evidenced requirement decisions remain separate; the old 80-point and 20/24 gates do not apply.

Each case has five acceptance checks covering its actual brief and revisions. Primary workflow checks are input integrity, evidence delivery, preservation and independent editability. Evidence delivery verifies the ten render hashes, evaluated channel files, native UI captures, apply receipts and saved/packed final blend. Missing evidence blocks the prerequisite without inventing an artistic score. Checks require `passed: true|false|null` and evidence paths. Captured controls/rendered images alone cannot prove that unrelated components remain unchanged. Human review may complete a pending check, but cannot overwrite a measured failure.

Reviewer records identify `id`, `independent: true`, `is_generating_ai: false` and expertise, such as `material_artist` or `game_asset_usability`. Prefer three reviewers where practical; a single reviewer is disclosed rather than represented as consensus. The generating model cannot grade its own output.

### Artist corrections

Record every additional human correction beyond the three predefined test prompts:

- **0:** autonomous success only if the actual texturing requirements pass.
- **1:** minor assistance.
- **2:** significant assistance.
- **3 or more:** failed autonomous workflow.

`artist_intervention_count` and correction records are separate from active operator time. Missing counts remain unknown; automation does not establish zero human effort. A reviewer may record externally observed corrections with evidence. Failed requirements remain failures even when correction count is zero.

The report retains every scheduled attempt, including blocked/unavailable outcomes, and reports conditional visual quality only for delivered outputs. Unknown scores, operator time and monetary cost never become invented zeroes. Actual generated-image assignments are reported separately from the H toggle.

## Optional extensions

These are outside the five-session primary benchmark:

- `freeze --controlled-matrix`: five cases × P/H × three repeats, **30 sessions**. Cases are randomized within mode and mode order alternates across repeats.
- `pilot --extended-checks` and `freeze --extended-checks`: offline reopen, undo, export/reconstruction and repeat-1 4K checks. They reuse the final stack and do not add generative samples.
- `freeze --include-robustness`: the archived eight safety scenarios for each selected mode.
- `freeze --evidence-config FILE`: evaluator-only region annotations and measurements prepared with [annotations.py](annotations.py). They never enter Director context.
- `supplements.baselines: true`: manual preset/qualified-artist control templates. Missing controls are not invented or credited as executed baselines.

Missing optional handoff, 4K or robustness evidence does not fail a primary material result. Blender-only evidence makes no game-engine compatibility claim.

## Versioning and verification

The original eight-case specification, scoring and reader remain in [legacy/](legacy/README.md). Existing version-1 frozen comparisons stay readable and are never silently converted. New comparisons use protocol `five_asset_eevee_v2` and manifest version 2.

Focused scheduling, scoring, review and CLI boundary tests use no provider:

```sh
python3 -m unittest discover -s tests/unit -p 'test_material_director_benchmark*.py'
```

Those tests validate the harness contract. Live five-model results and independent visual judgments remain separate evidence categories.
