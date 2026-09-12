# Validation

Validated on Blender 5.2.1 LTS, macOS Apple Silicon, using the Blender application installed locally.

## Automated acceptance

`python3 scripts/run_tests.py` runs each phase in a fresh background Blender process, then the
pure NumPy/format unit tests. All pass. Covered behavior:

- Repeated registration/unregistration, persistent UUIDs and object/material assignments.
- Ten-layer stacks, reorder, folder parenting, cycle rejection, descendant duplication, save/reload.
- Twenty blend choices, independent channel opacity, pixel values, per-channel and per-texture-set dirty scope, user-node preservation.
- Painted masks, invert, packed-image reload, real Cycles high/low Normal/AO bakes, derived curvature,
  explicit map registration and restoration of render, selection and material state.
- Edge Wear, Dirt and tile-stable procedural noise; actual baked-map generator composition.
- Smart-material folder and parameter round trips, bundled EXR assets, three starter materials.
- Native brush activation, lazy paint images, raster-change detection and roughness output updates.
- UE5 file naming and ORM bytes, BaseColor sRGB, Unity metallic/smoothness, resolution override,
  PNG/TIFF 8/16-bit independent RGBA (including zero alpha), collision protection.
- Triplanar fills, unassigned texture-set duplication, lifecycle handler cleanup.

## Real UI checks

`tests/integration/gui_workflow.py` runs in a separate GUI Blender process, activates a native
texture-paint brush, issues a real viewport stroke, verifies painted pixels and final roughness,
and saves the resulting `.blend`. This verifies the brush engine, beyond simulated raster writes.

`tests/integration/dock_workflow.py` uses Blender's `--enable-event-simulate` facility to click the
Substance-style editor dock's Fill/Delete buttons, select a layer, drag its opacity, switch between 2D and
3D, and capture `docs/workspace.png`. Its saved demo retains the editor in the selected area. Original Layout
preservation is asserted. GPU draw callbacks and operator errors are checked in the process log.

## Packaging

The archive is checked using Blender's `--command extension validate` command.
`tests/integration/install_extension.py` installs the ZIP into a project-local isolated repository,
enables/disables it repeatedly under its real `bl_ext` namespace, then creates and evaluates a material.
No existing user extension or preference file is replaced by these tests.

## Performance

A 2K stack of 20 constant fill layers with six channels enabled measured approximately **0.67 seconds**
for a full rebuild and **0.05 seconds** for a roughness-only edit on this machine. This measures the
constant-fill fast path; raster masks, image textures and triplanar geometry require more work.
The benchmark is a diagnostic, not a timing assertion. 4K/8K, other operating systems, arbitrary
production geometry and sustained tablet painting have not been comprehensively tested.

Transient logs, generated test files and tool caches are under `.blender-test/` and ignored by Git.


`tests/integration/editor_selector.py` verifies the registered Open Painter editor subtype, captures
the native editor-type enum popup, activates it in the current area, asserts that workspace count and
screen layout stay unchanged, and tests closing back to the standard viewport. It also checks that
native sidebars are hidden while the native editor-type selector remains available.

`tests/integration/native_ui.py` verifies live theme adaptation, preservation of viewport
shading and overlays, actual mouse drags with Escape cancellation and Shift precision,
and a right-click opening the native numeric-property popup. It uses factory preferences
and does not save them to the user profile.

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

`phase13.py` verifies automatic material assignment, all six connected images and their initial
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
