# Material Director five-asset Eevee v3

Authority: `Open_Painter_Material_Director_Capability_Remedy.md`, with the user's later instruction permitting pending human review and a separate deterministic calibration track. Exact prompts and numeric tolerances are in `test_cases_v3.json`. V2 records and policy remain archived unchanged under `v2/`.

## Frozen comparison

- Five cases × baseline/improved × native/hybrid × three independent repeats = 60 sessions.
- Optional controlled correction ablation adds 30 improved sessions with correction disabled through the ordinary production setting.
- Adjacent baseline/improved pairs balance run order. Fresh fixture and conversation per session; every attempt is retained.
- Concurrency is preregistered at one or two processes. Two-process execution requires a passed actual native-UI/Eevee isolation probe, uses paired waves for the primary 60, and records separate profile/temp/cache paths, peak process RSS and contended preview wall time. The 30 correction-off ablations form a separate randomized phase and never enter primary P/H aggregates.
- Baseline production is commit `2a9ff0d37daf3997b5b5817bc2744d358d8c1b0e`; the shared harness does not backport authoring capabilities.
- Eevee only, existing 1024-square studio. Standard views are retained; boots/rocks add undersides and vase adds quarter-turn circumference views. No rig reconstruction or favorable per-model relighting.
- Nominal fixture physical sizes are declared engineering test inputs, not measured source dimensions. Calibration authoring never enters AI context.
- Per instruction: at most three candidate previews, 180 seconds of candidate work, and four generated images total. Default wall caps: 600 seconds single-set turn, 900 seconds multi-set turn, 1800 seconds session.
- Ordinary product deadlines are preserved in both products: SET native 180 seconds, SET hybrid 900 seconds, SCENE 1800 seconds. The lower harness/product cap applies. Default effective caps therefore differ by mode (SET P 180, SET H 600, SCENE 900); baseline/improved budgets match within each mode/scope. No claim of equal P/H budgets is valid.

## Outcome and evidence

Execution, evidence and quality are separate typed states. Failed/no-commit creation captures an unchanged/failure state where Blender remains healthy and skips dependent refinements. Recovery uses the saved state offline, never reconnects the provider, never changes material content and never upgrades an AI result.

The portable review embeds decoded PNGs and checksums. Full evidence ZIP inventory validates files/hashes and PNG decoding. Optional blend companion archives are explicitly declared. Missing evidence is visible; a failure-state image cannot establish an applied-stage success.

## Frozen development quality gate

`director_quality_v1`: final weighted score ≥80/100, appearance ≥4/5, no final dimension below3/5; each required stage reviewed, acceptance true, and appearance ≥3/5. Workflow PASS additionally requires all three deliveries, evidence, preservation and unassisted execution. Raw ratings/disagreement are retained. Absent ratings remain NOT_REVIEWED; no aesthetic score is invented for no output.

Human-authored reference: UNAVAILABLE. Reviewer: PENDING. The user explicitly permits the comparison to run before human grading. Deterministic calibration is an engineering track and cannot replace artistic review or prove improved realism.

## Measurements

Effect masks are captured before layer opacity. Revision evidence records source identity, decoded mask equality, opacity ratio, added/removed effects and triangle-area-weighted support. All assigned triangle area remains in the denominator; invalid UV area is disclosed and excessive invalid area invalidates a spatial claim. Four fixed interior samples per triangle avoid depending on UV pixel density. Moss occupancy threshold0.5, final/initial ratio0.60±0.06, minimum initial area0.01; band positional tolerance0.005 of upright height. Evaluator-only semantic annotation/review remains required for final location judgments.
