# Bake queue timer hotfix, 0.2.4

The UI's **Bake Selected Maps** operator read `event.timer`. Blender's public
`Event` has no such field, so the first native TIMER event raised the reported
`AttributeError` before any map was baked. Direct generator/backend tests did
not exercise that UI event path.

The change is incremental on `task/bake-queue-timer-fix`, from
`02c88157681ea532a6a692fb35e3b5b4111775ba`; the repository and remote were checked
before editing. `origin/main` remained at
`15ef25ec532f466eff18a7265fa7d7ef5019e8ef`. No checkout reset or artist project
modification was performed.

## Implementation

The queue now checks `event.type`, then consumes a change in its owned
`Timer.time_duration` before resuming one map. Unrelated window timers cannot
accelerate the queue. Timer creation and handler registration are covered by
initialization error cleanup. Cleanup detaches handles first and removes the
timer even if generator close raises; repeated cleanup is harmless.

The installed template at
`/Applications/Blender.app/Contents/Resources/5.2/scripts/templates_py/Operator/modal_timer.py`
dispatches using `event.type`. The official [Event and Timer RNA definitions](https://raw.githubusercontent.com/blender/blender/main/source/blender/makesrna/intern/rna_wm.cc)
expose the timer duration on Timer, not Event. The official [window timer processing code](https://raw.githubusercontent.com/blender/blender/main/source/blender/windowmanager/intern/wm_window.cc)
updates that duration when the owned timer becomes due. The online source is
Blender main, not the installed binary; the actual installed API was exercised
by the regression below.

Map algorithms, shader/material behavior, native UI, CPU evaluation, live GPU
preview and version-1/version-2 material formats are unchanged. Each map still
bakes synchronously; Esc is handled between maps. This hotfix does not implement
within-map cancellation or a baking performance backend. Cross-platform and
other Blender versions were not tested. No performance benchmark was rerun and
historical timing reports are not new measurements.

## Fresh reproduction and regression

Environment: macOS Apple Silicon, Apple M3 Pro; Blender **5.2.1 LTS**, build
**9e2066aef7ef**. Both runs used factory startup and isolated user resources.
The actual native Event object supplied no `timer` property in either run.
Authored test resolution was explicitly **512 x 512**, with float image pixel
checks; it is a new small queue regression, not the 2K performance fixture.

Before: [captured report](validation/bake-queue-before.json), **FAIL**, exact
reported AttributeError, zero map calls. The Blender process exits normally
because the harness captures the exception; report status is the acceptance
criterion. After: [captured report](validation/bake-queue-after.json), **PASS**:

- Position and World Normal produce finite, nonconstant images with provenance.
- A separate 10 ms window timer cannot run maps before the queue's 100 ms timer;
  repeated events with the same owned duration cannot run duplicate work.
- Native Esc after Position keeps that completed map and skips World Normal.
- Injecting failure in the second map retains Position and releases the queue.
- No leaked temporary objects, meshes, images, materials, scenes or collections;
  original selection, transforms, render engine and material slots remain.

Exact commands, run from the repository root:

```sh
env BLENDER_USER_RESOURCES=/private/tmp/op-bake-queue-before-user OP_BAKE_QUEUE_REPORT=/private/tmp/op-bake-queue-before.json /Applications/Blender.app/Contents/MacOS/Blender --factory-startup --enable-event-simulate --python tests/integration/bake_queue_ui.py -- -ApplePersistenceIgnoreState YES
env BLENDER_USER_RESOURCES=/private/tmp/op-bake-queue-after-user OP_BAKE_QUEUE_REPORT=/private/tmp/op-bake-queue-after.json /Applications/Blender.app/Contents/MacOS/Blender --factory-startup --enable-event-simulate --python tests/integration/bake_queue_ui.py -- -ApplePersistenceIgnoreState YES
/Applications/Blender.app/Contents/Resources/5.2/python/bin/python3.13 -m unittest discover -s tests/unit -p test_bake_queue.py -v
```

The four cleanup unit tests passed. They inject timer creation failure, handler
registration failure, generator-close failure and already-removed timers.
These lifecycle fakes complement the real native-event regression; they do not
substitute for it. The first after-fix harness run exposed a test-observer issue:
Blender may mutate Event contents during error reporting. Caching event fields
before dispatch fixed that assertion; the rerun above passed all three cases.

## Release validation

All checks below were actually run on the installed Blender binary, with isolated
user resources. Their exact commands and results:

```sh
python3 scripts/run_tests.py > /private/tmp/op-bake-queue-background.log 2>&1
python3 scripts/run_tests.py --gui-only > /private/tmp/op-bake-queue-gui.log 2>&1
python3 scripts/build_extension.py
env BLENDER_USER_RESOURCES=/private/tmp/op-bake-package-user /Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup --command extension validate dist/open_painter-0.2.4.zip > /private/tmp/op-bake-package-validation.log 2>&1
env OPEN_PAINTER_TEST_ZIP=/Volumes/SSD/projects/open_painter/dist/open_painter-0.2.4.zip BLENDER_USER_RESOURCES=/private/tmp/op-bake-package-user /Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup --python-exit-code 1 --python tests/integration/install_extension.py > /private/tmp/op-bake-package-install.log 2>&1
```

- **PASS**, exit 0: 52 background integration scripts and 46 unit tests. Includes
  actual Cycles bake isolation/pairing, diagnostics, geometric curvature,
  utility maps, materials, saving and export.
- **PASS**, exit 0: four native GUI workflows (registration/layout, texture
  controls, new bake queue, and actual paint stroke with save). The texture UI
  process still prints a Blender shutdown warning for five unfreed blocks,
  totaling 0.000481 MB; assertions and exit status pass. This hotfix does not
  diagnose that separate shutdown warning.
- **PASS**, exit 0: extension manifest/archive validation and isolated ZIP install,
  real `bl_ext` namespace, repeated enable/disable and material evaluation.

The real-window GPU math/performance suites were not rerun for this event-only
fix. Existing performance reports remain historical. The bake regression proves
the native operator path and the backend suite proves existing map algorithms;
it does not certify every map/production asset combination through the UI.

## Changed files

- `src/open_painter/baking/jobs.py`: timer routing and cleanup.
- `tests/integration/bake_queue_ui.py`, `tests/unit/test_bake_queue.py`:
  native queue and lifecycle regressions.
- `scripts/run_tests.py`: require the fresh bake queue report in GUI runs.
- `blender_manifest.toml`, `pyproject.toml`, `src/open_painter/constants.py`,
  `scripts/launch_blender.py`, `README.md`: 0.2.4 release identity.
- `CHANGELOG.md`, `TESTING.md`, `docs/implementation-status.md`, this report,
  `docs/validation/bake-queue-before.json`, `docs/validation/bake-queue-after.json`:
  cause, actual validation and limits.
- `scripts/build_extension.py`, `dist/open_painter-0.2.4.zip`: include the hotfix
  and its report in the installable extension; preserve the previous archive.
