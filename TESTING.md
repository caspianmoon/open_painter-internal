# Validation

Current project acceptance uses the artist-supplied `open painter test.blend`,
retaining its 2048 resolution and complete model. `tests/performance/shader_user_project.py`
measures the existing controls; `tests/integration/shader_project_ui.py` drives
actual native mask sliders and captures the model's viewport; and
`scripts/validate_project_persistence.py` checks source/mesh preservation and
explicit export. `tests/integration/install_user_project.py` validates the
installed extension namespace using that same asset. These scripts use copies.
The authoring trace, save/reopen/export, and actual-project held Cavity Dirt
strength check have passed. Eight reversals through an isolated native RNA
sidebar changed the captured material before release, with zero raster work or
texture writes. This checks the real property callback and viewport, not product
panel navigation. See the [actual-project report](docs/shader-preview-architecture.md#validation-on-the-supplied-project)
for its capture overhead, sparse Chips diagnosis, and scope. Archive installation
is validated separately; viewport results do not cover installation.
The small numerical fixtures below are development regressions; they do not
establish responsiveness on the user's asset and are not the remaining acceptance path.

The 0.2.7 native shader tests are `shader_nodes.py` (blend/filter GPU math),
`shader_sources.py` (source/projection parity and 50 catalog compilations),
`shader_runtime.py` (real callbacks/lifecycle), `shader_boundaries.py` (isolated
fallback dependencies), and `shader_preview.py` (rendered edits and persistence).
The regular background runner includes them. `--gui-only` includes an actual held
mask slider test and the explicit 2D snapshot/paint-source workflow. Failed GUI
reports fail the runner; callback timing alone cannot pass visual acceptance.

Developer diagnostics and operation limits are documented in
[shader-preview architecture](docs/shader-preview-architecture.md). The older
raster scheduler tests explicitly select CPU Reference where their assertions
require automatic channel-image generation.


Validated on Blender 5.2.1 LTS, macOS Apple Silicon, using the Blender application installed locally.

The historical 0.2.4 latency follow-up included 52 background integration scripts, 42 Blender unit
tests and 37 independent NumPy tests. The real-window `--gpu-only` runner covers
183 compositing checks, 91 folder-cache checks, 38 procedural configurations,
11 full-resolution mask cases plus unsupported-domain preflight checks, and the
live painting/save/export workflow. `--gui-only` also checks the native bake queue, alongside the three existing UI workflows.
See the [historical latency report](docs/live-preview-latency.md) for those
run results, exact commands, raw evidence and limits. GPU tests require an actual
window and do not pass by skipping. All tests use isolated Blender resources.

## Automated acceptance

`python3 scripts/run_tests.py` runs phase tests and the current integration suites in fresh background
Blender processes, then runs the unit suite with Blender's bundled Python and NumPy. Test resources
are isolated under `.blender-test/user`. Current suites include utility maps, the 50-material catalog,
procedural channels, texture operations, native controls and persistent raster caching. Historical
phase coverage is listed below. Covered behavior:

- Repeated registration/unregistration, persistent UUIDs and object/material assignments.
- Ten-layer stacks, reorder, folder parenting, cycle rejection, descendant duplication, save/reload.
- Extended color/normal blend choices, eight channels, independent channel opacity, pixel values, per-channel and per-texture-set dirty scope, user-node preservation.
- Painted masks, invert, packed-image reload, real Cycles high/low Normal/AO bakes, geometric curvature and utility maps,
  explicit map registration and restoration of render, selection and material state.
- Edge Wear, Dirt and tile-stable procedural noise; actual baked-map generator composition.
- Smart-material folder and parameter round trips, bundled EXR assets, legacy starter materials and 50 authored texture-layer recipes.
- Native brush activation, lazy paint images, raster-change detection and roughness output updates.
- UE5 file naming and ORM bytes, BaseColor sRGB, Unity metallic/smoothness, resolution override,
  PNG/TIFF 8/16-bit independent RGBA (including zero alpha), collision protection.
- Triplanar fills, unassigned texture-set duplication, lifecycle handler cleanup.

## Real UI checks

`tests/integration/gui_workflow.py` runs in a separate GUI Blender process, activates a native
texture-paint brush, issues a real viewport stroke, verifies painted pixels and final roughness,
and saves the resulting `.blend`. This verifies the brush engine, beyond simulated raster writes.

`python3 scripts/run_tests.py --gui-only` runs `native_ui.py` and `texture_operations_ui.py`: actual native Panel draw callbacks,
AssetShelf mouse activation, one mask per click, native T/N controls and 3D/2D switching. It captures
`docs/native-painter.png`; the texture-operation suite checks native channel controls, mask filters
and search selection. Historical GPU dock tests below no longer describe the active interface.
`native_registration.py` verifies register/unregister cycles without changing surrounding areas.

`bake_queue_ui.py` invokes the registered bake operator in a real window and receives
native TIMER events. It bakes Position and World Normal at 512, checks unrelated timer
routing, sends native Esc between maps, and injects a second-map failure. It verifies
completed images, provenance, queue cleanup and unchanged author scene resources.
The four `test_bake_queue.py` unit cases cover failed timer/handler initialization,
generator-close failure and already-removed timers. See the
[bake hotfix validation](docs/bake-queue-fix.md) for the reproduced error and exact runs.

## Packaging

The archive is checked using Blender's `--command extension validate` command.
`tests/integration/install_extension.py` installs the ZIP into a project-local isolated repository,
enables/disables it repeatedly under its real `bl_ext` namespace, then creates and evaluates a material.
No existing user extension or preference file is replaced by these tests.

## Performance

The earlier CPU-only 2K stack of 20 constant fill layers with six channels enabled measured approximately **0.67 seconds**
for a full rebuild and **0.05 seconds** for a roughness-only edit on this machine. This measures the
constant-fill fast path; raster masks, image textures and triplanar geometry require more work.
The benchmark is a diagnostic, not a timing assertion. 4K/8K, other operating systems, arbitrary
production geometry and sustained tablet painting have not been comprehensively tested.

Transient logs, generated test files and tool caches are under `.blender-test/` and ignored by Git.


## Historical acceptance records

The versioned sections below describe earlier implementations and measurements. The active runner
uses the native UI suites listed above; archived GPU-dock and close-button workflows are not current
acceptance criteria. Old six-channel timing results should not be interpreted as eight-channel
procedural-material performance results.

## 0.1.1 workflow regressions

`phase11.py` checks switching layer/channel/mask targets, flushing fresh strokes on export,
restoring material-slot face masks across save/reload, packed alpha assignment, missing-output
reconstruction, missing-source reporting and invalid-folder rejection.

`paint_workflow.py` issues actual Blender strokes to prove that unassigned material-slot faces
stay untouched, assigned faces accept paint, a black imported alpha blocks the brush, clearing
it restores painting, and switching layers leaves the old layer image unchanged. It also checks
that the complete brush controls are reachable and that finishing 2D painting enters view mode.


## PDF parity increment (0.1.2)

`phase12.py` checks independent channel blends/opacity, dirty-output routing, nested isolated and
passthrough folders, fractional alpha, group masks, empty groups, nearest and non-repeating fills,
triplanar hardness, save/reload, duplication and portable smart-material roundtrip. The numeric tests
in `test_compositing.py` use independent expected values, zero/one endpoints and UV border fixtures.

`compositing_ui.py` clicks the channel-blending button in a real Blender window and verifies native
channel and folder popup drawing. It captures `docs/channel_blending.png` for visual inspection.

`python3 scripts/check_parity.py` validates the PDF inventory and documentation synchronization.
`python3 scripts/check_parity.py --require-parity` must currently exit 1: full feature parity has
not been achieved. This is a requirements gate, not a substitute for running tests or comparing
renders against Substance Painter.


## Workspace repair regressions (0.1.3)

`phase13.py` verifies automatic material assignment, all eight connected images and their initial
linear pixels, unchanged shader-link identities after repair, group order/collapse/parenting/cycle
rejection, preset output values, persistence, deletion and one-channel-per-timer evaluation.

`repair_ui.py` uses real Ctrl-click/Ctrl+G, collapse buttons, layer drag/drop and T/N events, waits
through the maintenance timer, then paints through real mouse input. It verifies automatic stroke
composition and zero repeated idle canvas scans. Warm Python dock drawing measured approximately
1.8 ms in that test; this is not a total viewport frame-time or an 8K painting performance claim.
The screenshot is `docs/repaired_workspace.png`.

## Panel lifecycle repair (0.1.4)

`panel_lifecycle_ui.py` runs real T/N events with no texture set and the pointer over the left
asset dock. It deliberately exposes native regions and seeds stale input-handler tracking,
then checks recovery, editor reopening, maximize/unmaximize, 2D/3D transitions, and standard
Blender T/N behavior after closing Painter. It also checks that exactly one dock handler runs.

## Theme styling (0.1.5)

`theme_ui.py` renders Blender Dark, Blender Light and custom shaded widgets without saving preferences. Screenshots in `docs/theme_dark.png` and `docs/theme_light.png` are captured after a window redraw and swap. Inspect these alongside the native Properties editor; RNA color assertions alone do not prove rendered appearance.

## Interactive evaluation (0.1.6)

`phase14.py` verifies empty-layer invalidation, stale-job cancellation, enabled-channel scope,
premultiplied paint math, untouched rows, and equality of regional/cooperative and full outputs.
`realtime_ui.py` uses a 2K cube and real held/moving brush events, checks that empty layer creation
leaves image revisions unchanged, and compares the resulting paint preview with a full evaluation.
The final isolated run measured about 122 ms from movement to output revision and a maximum 34 ms
evaluation callback. This measures preview publication, not display frame time or a guaranteed frame
rate. Complex masks/projections and 4K/8K performance remain unverified.

## Reference layout and effects (0.1.7)

`reference_layout_ui.py` checks default native 3D/2D splitting, image assignment, dock ownership, actual settings-tab clicks, shared T/N and joining back to one area. `phase15.py` checks numerical content/mask effects, bitmap masks, clipboard identities, linked projection, portable import and saved-project reload. `test_effects.py` checks independent Levels/filter math, component selection, opacity, tile equivalence and unchanged source buffers. Screenshots supplement these tests; they do not certify complete visual or feature parity.

## Registration lifecycle repair

`registration_lifecycle.py` checks duplicate registration, repeated teardown, partial-failure rollback, recovery and unchanged area layout during registration. `reference_layout_ui.py` verifies the preserved dock layout, real settings-tab clicks, T/N and paired-view save/reload. These checks validate the existing subtype adapter, not creation of a native custom SpaceType.


## M0/M1 reproducible validation

The current task adds scene-scoped scheduling, geometry/source invalidation and
failed-job regressions to the active runner, along with the regional paint scan
regression. The GUI runner also executes the actual native brush-engine stroke
fixture, migrated to the current editor API. Candidate packaging can be tested
without replacing a published ZIP:

```sh
python3 scripts/build_extension.py --output .blender-test/candidate.zip
python3 scripts/run_tests.py --package .blender-test/candidate.zip
python3 scripts/run_tests.py --gui-only
```

The runner restores the bundled example files and tracked UI screenshot after
runs. `scripts/run_pure_tests.py` runs 23 bpy-independent tests with a NumPy Python;
the complete unit suite still runs inside Blender. CI definitions are in
`.github/workflows/tests.yml`; background Blender CI requires an explicitly
provisioned runner and dispatch. GUI/Metal validation is a separate local run.

See [implementation status](docs/implementation-status.md) for exact commands,
measured before/after results, capability reports, the historical-test audit and
unresolved limits. `scripts/benchmark_textures.py` runs fresh isolated CPU
fixtures at 512/2048. It reports image transfers and process RSS; it does not
measure displayed frames or silently use lower-resolution previews.
