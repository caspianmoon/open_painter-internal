# Material Director use-case benchmark

This implements the supplied [test plan](test_plan.md), using its [exact case manifest](test_cases.json). It exercises public CC0 assets through the live Material Director UI/provider path. Fixture preparation and evidence capture are evaluator operations; they do not author the Director's proposal or replace its generated images.

**Implementing the runner does not mean the matrix has passed or even run.** The report includes every scheduled outcome. Appearance, placement, independent editability and the baseline comparison require evidence and independent review; neither a passing Python test nor a populated layer stack supplies that judgment.

## Run sequence

Use a separate output directory for each frozen comparison. Live provider calls are explicit, use an isolated access-only copy of the current Codex login, and remain outside routine unattended CI.

1. Download, verify and prepare the neutral fixtures using [ASSETS.md](ASSETS.md). Original textures and masks are removed. Preserve actual source material boundaries; do not add semantic masks to rescue primary runs. Freeze evaluator-only placement annotations before viewing AI results.
2. Write an environment configuration template:

   ```sh
   python3 scripts/benchmark_material_director.py config --output .blender-test/benchmark-config.json
   ```

3. Run a **non-scored** pilot to verify the model identifier, runtime, sharing, availability, camera/evidence setup and operational caps:

   ```sh
   python3 scripts/benchmark_material_director.py pilot \
     --fixtures .blender-test/material-director-benchmark/fixtures \
     --output .blender-test/material-director-benchmark/pilot \
     --case MD-01 --mode H --model YOUR_EXACT_MODEL_ID --use-codex-login
   ```

   `--blender PATH` and `--codex PATH` select executables. `--quick` is a non-scored pilot diagnostic with reduced evidence; it cannot establish full-resolution benchmark quality or rescue a failed core session. Verify the normal evidence workload and predeclare caps before freezing a scored comparison. The defaults are 600 seconds per single-asset turn, 900 seconds per multi-set/scene turn and 1,800 seconds per session.

4. Complete the configuration with the observed provider/model identifiers, Blender version, hardware, renderer/evaluator, library SHA-256 and hash of actual executed source bytes, including dirty edits. Record uncontrolled provider randomness as `uncontrolled`. Choose the **quality** or **efficiency** value claim now. Supply the completed non-scored pilot evidence and pin each neutral fixture plus `R01_fabric_reference.png`.

   ```sh
   python3 scripts/benchmark_material_director.py freeze \
     --config .blender-test/benchmark-config.json \
     --fixtures .blender-test/material-director-benchmark/fixtures \
     --output .blender-test/material-director-benchmark/comparison-01
   ```

5. Run the scheduled live matrix, optionally in bounded batches:

   ```sh
   python3 scripts/benchmark_material_director.py run \
     --comparison .blender-test/material-director-benchmark/comparison-01 \
     --fixtures .blender-test/material-director-benchmark/fixtures \
     --use-codex-login --kind all --max-sessions 2
   ```

   `--kind core|robustness|all` selects the evidence category. A finished attempt is not rerolled in place. Preserve unavailable/provider failures. Diagnose or fix on a separately named build, then create a new frozen comparison. Never pool assisted targeting runs with the primary matrix.

6. Prepare matched controls with normal Open Painter controls. For each case, complete `controls/MD-XX-preset.json` and its actual output, operator time, elapsed time and independent ratings. The artist control has its own predeclared active-work budget, default 20 minutes. If no qualified artist is available, leave that control unavailable and limit conclusions to the preset comparison. A downloaded original material is never ground truth.
7. Export anonymous review packets and collect independent judgments:

   ```sh
   python3 scripts/benchmark_material_director.py review-pack \
     --comparison .blender-test/material-director-benchmark/comparison-01
   ```

   Give reviewers `review_packets/` without the comparison manifest, run logs or `review_key.private.json`. Packet order is randomized and opaque IDs conceal P/H and repeat. Each packet retains identical fixed camera labels and image checksums. Merge returned forms into `reviews/REVIEW-XXXX.json`; retain separate reviewer identities and ratings. Do not replace the human assessment with the generating AI's opinion. A normal or close render can establish appearance, but cannot prove a control is editable or that offline/export checks passed.
8. Build a report at any point, including an incomplete or blocked comparison:

   ```sh
   python3 scripts/benchmark_material_director.py report \
     --comparison .blender-test/material-director-benchmark/comparison-01
   ```

## Frozen experiment

The schedule contains **48 core sessions**: eight cases × P/H × three fresh repeats, each with the exact Create prompt plus the common instruction and two exact Refine prompts. Cases are shuffled within each mode; mode order is P/H, H/P, P/H across repeats. The schedule separately includes **16 robustness scenarios**, eight per mode. Repeat 1 has a 4K check for each case/mode without further AI generation: those 16 checks do not increase the generative sample count.

The manifest records the deterministic schedule and its seed, source specification, all frozen settings and fixture digests. It is hash-verified before reading or recording results. Existing comparisons cannot be overwritten. Results retain an append-only revision history; a completed attempt cannot be replaced by a better attempt. Source/model/library/environment drift requires a new comparison.

P disables Generate Surface Textures; H enables it. H does not imply images were generated or used: the report separately counts generated and assigned inputs. If generation is unavailable, that capability remains `BLOCKED`; a procedural fallback cannot be advertised as demonstrated image generation.

## Review and scoring contract

Each reviewer entry identifies independence and expertise:

```json
{
  "id": "reviewer-01",
  "independent": true,
  "is_generating_ai": false,
  "role": "material_artist"
}
```

Use `game_asset_usability` for that reviewer role; other roles are permitted and disclosed. Prefer three independent reviewers. A single human is allowed but the report states the limitation. Do not invent reviewers to fill the template.

Each stage's `ratings` list contains independent entries with the six dimension values, each 0–5, and evidence paths. Missing/invalid ratings remain `NOT_SCORED`. The median per dimension determines the weighted score.

| Dimension key | Weight |
|---|---:|
| `material_appearance` | 25 |
| `brief_compliance` | 20 |
| `spatial_placement` | 20 |
| `editable_structure` | 15 |
| `refinement_fidelity` | 10 |
| `handoff_integrity` | 10 |

Score anchors: 0 absent/unusable; 1 fundamentally wrong; 2 major rework; 3 noticeable cleanup; 4 useful with minor cleanup; 5 satisfies the brief and inspections. First-pass, first-refinement and final-stage ratings remain separate.

The five case acceptance checks use `AC-01` through `AC-05`, each `{ "passed": true|false|null, "evidence": [...] }`. A check with no evidence is incomplete even if somebody entered `true`. The result's workflow checks follow the same shape:

- `input_integrity`: clean frozen source, input sharing, target and map readiness.
- `preservation`: geometric/UV/scene and protected material semantic/buffer checks.
- `editability_probe`: case-specific ordinary-control probes, followed by checkpoint restoration.
- `undo`: second refinement undone to first-refinement semantic state and decoded image pixels, for every affected set.
- `save_reopen_offline`: saved final reopened with the provider disconnected and without relying on a temporary generated-image cache.
- `export_reimport`: exported used channels rebuilt in a plain PBR shader and inspected under both fixed light arrangements.

A legitimate no-op can satisfy a refinement. For example, MD-01 says not to invent rust when no rust exists. The evaluator/operator must confirm it using `no_op_confirmed: true` plus `no_op_evidence` on the stage, or fill the review stage's `no_op_acceptance` evidence check. A visible complete material is still required. Provider prose alone cannot establish a successful no-op; Create always requires an applied result.

Final acceptance requires **at least 80/100**, every dimension **at least 3/5**, all five case and required workflow checks passing, every instruction delivered or a confirmed legitimate refinement no-op, and no hard failure. Crash, unauthorized edit, lost artist work, corrupted save, missing material after a claimed apply and falsely claimed apply are hard failures. An explicitly reported provider outage is availability failure, with no fabricated zero-quality score.

## Mode-level gates

Report P and H separately. Each mode requires at least 20/24 core passes and at least 2/3 in **every** material class. No destructive or cross-target failure may occur. A failed robustness scenario blocks the dependable-workflow claim; incomplete safety evidence keeps it pending.

The predeclared value gate requires six of eight matched case comparisons:

- **Quality:** mean final score across all three repeats is at least 10 points above the preset control.
- **Efficiency:** mean final quality is no worse than five points below the control, with at least 50% less measured active operator time. Elapsed provider time cannot substitute for operator time.

All three scores and the matching frozen fixture/configuration are required for a control comparison. A missing baseline stays `NOT_SCORED`, and the beta value gate stays pending. The report always uses all 24 scheduled sessions per mode in the delivery denominator, including blocked, unavailable and unrun records. It additionally reports visual quality conditional on completed outputs, individual scores, median/range, every failure, actual generated-input use, timings and unknown cost without averaging away a weak mode.

## Evidence boundaries

Source-region limitations are part of the result: a single texture set is not necessarily a semantic region. Scene context, map inspection or a plausible prompt response cannot prove correct placement. Evaluator-only annotations may measure placement but must never enter AI context. Clearly mark unsupported checks and untested engines. A clean Blender PBR reconstruction does not establish Unreal or Unity compatibility.

The [report module](report.py) lists all 48 core and 16 robustness outcomes. The anonymous view exporter checks image hashes before copying; it does not synthesize missing views. The [unit tests](../../unit/test_material_director_benchmark.py) verify scheduling, freeze boundaries, no-op handling and scoring arithmetic. They are a separate evidence category from actual live model results.

```sh
python3 -m unittest discover -s tests/unit -p test_material_director_benchmark.py
```

## Python APIs

Add `tests/benchmarks` to `sys.path`, then import `material_director`:

- `manifest.load_cases()`, `prompts(case)`, `build_schedule(seed)`.
- `manifest.configuration_template()`, `freeze_manifest(directory, configuration, fixtures, seed)`.
- `manifest.load_manifest(directory)`, `verify_environment(manifest, observed)`, `record_result(directory, run_id, result)`.
- `scoring.score_session(result, case, review)`, `summarize(manifest, results, baselines, reviews)`.
- `report.write_report(directory)`, `export_review_packets(directory)`.

Fixture pins are keyed by the exact `.blend` basenames from the case manifest plus `R01_fabric_reference.png`, each with a `sha256` field. Scoring accepts stage mappings or ordered stage lists with unique `name` fields. Importing these modules does not launch Blender, use the network or access a provider account.
