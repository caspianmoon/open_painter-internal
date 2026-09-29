# Metal implementation evidence — ABI 9

The native implementation now owns shader IR/compiler and parameter banks,
layer/folder and dependency plans, checkpoint selection, resource policies,
brush/geometry kernels, filter plans and immutable material revisions. The
compiled CPython adapter submits draws through Blender's public GPU API.
ABI 9 adds native nested-mask traversal, fold order, continuation references
and isolated filter-prefix planning. ABI 8 supplies the 30 pointwise effects
and mask levels, inversion, strength and folding. These build on ABI 7 channel acceptance,
ABI 6 UV topology, ABI 5 blend/batch kernels and ABI 4 surface/anchor and
publication transaction ownership.

The complete original migration is **not yet claimed**: remaining Python
orchestration and compatibility boundaries are explicitly listed in
[migration status](migration-status.md). Windows/Vulkan is deferred to the
other machine as requested. The unrelated standalone application is unchanged.

## Executed checks

Host: macOS arm64, Apple M3 Pro, Apple clang 17; stock Blender **5.2.1 LTS**,
build **9e2066aef7ef**, Metal. All Blender runs use isolated profiles.

| Check | Result and scope |
| --- | --- |
| Release CMake/CTest | 25/25 PASS, assertions enabled |
| UndefinedBehaviorSanitizer | 25/25 PASS |
| Python regression suite | 388 tests, OK, five skips |
| Native differential geometry | Five tests: owned bounds, UV overlap/clipping, non-manifold/degenerate adjacency, tangent/interpolation and exact padding donors |
| Native surface/anchor comparisons | Three tests: owned geometry, stale-anchor validation, nearest-target ties and 256-sample chunk boundaries |
| Native seam comparisons | Two tests: rotated edges, unequal UV density, overlapping bands, coverage, island ties and malformed input |
| Native UV topology | Six tests: randomized meshes/material subsets, non-manifold edges, stable island IDs, 5,184-face grid, int64 vertex pairs, owned buffers and sparse island bounds |
| Native differential brush | Four tests: coverage/pressure/flow/alpha/eligibility, distance/windows, surface walk and resampling/bounds |
| Compiled binding validation | Six tests, including old-ABI capsule rejection, graph submission/cleanup/reentry, native publication exception/reentry/closed-owner checks and 400 deterministic mutated IR inputs, malformed buffers and atomic graph rejection |
| Metal integration | 25/25 PASS, including 21 background suites, two GUI suites, fresh reopen and clean install |
| Native mask planning | 1,280 randomized range/flag/continuation comparisons through compiled and ctypes bindings; 65,536-level C hierarchy; six Metal cases against CPU and legacy GPU, max channel error 4.89e-4; no numeric-edit replan/rebuild/compile |
| Native effect/mask parity | 90 effect cases and 30 mask cases × 1,024 pixels; effect maximum absolute error 5.96e-7 against legacy GPU and 5.37e-7 against NumPy; eight live edits with no graph rebuild or shader compilation |
| Native blend parity | 28 modes × 2 alpha contexts × 1,024 pixels; maximum absolute error 5.96e-8 against legacy GPU and 5.37e-7 against NumPy |
| Native channel acceptance | Render/commit failure at every callback, copied requests, exact constants, target/configuration changes, subset publication, reentry rejection, host metadata and retirement failure recovery; C, compiled/ctypes and Metal checks |
| Native batch execution | 65,536-node chain; shared/repeated inputs; failure at every acquire/render/release; reentry rejection; zero-byte retained-cache Metal parity |
| Static analyzer | 25 C translation units including the compiled binding, no diagnostics |
| Extension validator | PASS |
| Evaluated native geometry | Actual beveled mesh, mirrored/nonuniform transforms, tangents, adjacency, padding, UV labels/borders/seam sides, nearest flags and island bounds match the reference |
| Workflow compatibility | Fourteen suites PASS: evaluated projection, multiple sets, surface bounds, UDIM workflow/material, aging, Material Director, material surface parity, surface filters, mask groups, live masks, mask graphs, material recipe masks and projected masks |
| AddressSanitizer | Startup timeout unresolved; not counted as passing |

The native Metal test compares all eight outputs, checks warm topology reuse and
zero warm shader compilation, switches executors, injects a failure after the
first changed channel is published, verifies rollback and retries. It then frees
all eight host image textures and verifies exact recovery of every mip without
advancing the accepted revision. Native restore/unregister teardown is checked. The portable C publication driver
is fault-tested at every prepare/write step, retries after failure, and attempts
all remaining restores if one rollback fails. The compiled binding rejects
publication reentry and closing a revision stream inside a submission callback.
Batch execution now tracks dependency lifetimes in C; all acquired holds are
released on failure, and no recursive host rendering is allowed to hide a missing
dependency. The Metal regression enforces a zero-byte retained cache and checks
identical output after successive edits. A separate injected failure occurs after
a unit draw overwrites its allocation; restoring the previous parameter value
must recompute that unit, with no stale cache stamp or changed visible revision.

The real-window slider observes six changed completed viewport frames before
release at 2048. The brush test checks cancellation, full-resolution feedback,
35 durable samples, a visibly painted object and immediate Save As. A separate
process reopens and evaluates the saved source. Clean installation uses Blender's
real extension installer in a non-ASCII temporary path without a developer
library override, then exercises repeated enable/disable.

Four publication compatibility suites exercise forced pixel, uniform, constant
and fused paths on Metal; these do not qualify Vulkan hardware.

The live-mask compatibility prototype runs in a real GUI at 512 and must write
a fresh PASS report. Its old ABI 8 background invocation only exited normally;
that historical log does not prove the timer-driven test executed. The runner
now rejects a missing or failed report even when the Blender process exits zero.
Its 11 parity cases and 19 unsupported-input checks passed. This prototype is
separate from the new production native-mask test. A final targeted run strengthens
the production fixture to require visible output changes for numeric edits and
for a source moved across an active group, while retaining CPU/legacy GPU parity.

Workflow compatibility tests include deliberately CPU-based paths; their success
is not presented as proof those workflows are entirely native GPU operations.
The geometry/brush differential suites compare C against independent NumPy paths.

## Artifact and evidence

Candidate: `dist/open_painter-0.13.1-macos-arm64-native.zip`.
It bundles the ABI 9 dylib and CPython 3.11 limited-ABI module with hash validation.
Users need no compiler, pip installation or environment variable. Neither this
package nor any test add-on was installed in the artist's ordinary profile.
The dylib and module depend externally only on macOS libSystem; the module
resolves Python through the host interpreter.

ABI 9 evidence uses the `metal-v9-` prefix. Earlier ABI 2 through ABI 8 evidence remains intact
and is not relabeled as a test of the new binary. Each integration run also keeps
its own immutable directory under `.blender-test/native/metal-integration/runs/`.
The latest report is copied to the parent for compatibility with existing tools.

- [Artifact identities](evidence/metal-v9-artifacts.json)
- [Native mask hierarchy and visible reorder parity](evidence/metal-v9-mask-order.log)
- [Final mask recheck outcomes](evidence/metal-v9-mask-order-results.json)
- [Live-mask GUI result](evidence/metal-v9-workflow-live-masks.json)
- [Native effect and mask parity](evidence/metal-v9-native_effects.py.log)
- [Native blend parity log](evidence/metal-v9-native_blend.py.log)
- [Release integration outcomes](evidence/metal-v9-results.json)
- [Native publication and recovery](evidence/metal-v9-native-host-metal.json)
- [Slider report](evidence/metal-v9-native-slider-ui.json)
- [Brush report](evidence/metal-v9-native-brush-ui.json)
- [Workflow outcomes](evidence/metal-v9-workflows.json)
- [Python checks](evidence/metal-v9-pure-tests.log)
- [UBSan checks](evidence/metal-v9-ubsan.log)

## Reproduction

```sh
python3 scripts/build_native.py --build-dir /tmp/openpainter-native-release \
  --package dist/open_painter-0.13.1-macos-arm64-native.zip
python3 scripts/run_native_metal_tests.py \
  --lib /tmp/openpainter-native-release/libopenpainter.dylib \
  --package dist/open_painter-0.13.1-macos-arm64-native.zip --gui
OP_TEST_NATIVE_LIBRARY=/tmp/openpainter-native-release/libopenpainter.dylib \
  /Applications/Blender.app/Contents/Resources/5.2/python/bin/python3.13 \
  scripts/run_pure_tests.py
```

`--gui` requires a logged-in desktop. The harness opens an isolated stock Blender
test window; the add-on itself creates no extra painting window. The runner
checks JSON assertions as well as process exit codes.

## Remaining qualification

No five-by-60-second sustained latency qualification or p95/p99 display latency
claim is made. Completed framebuffer captures include screenshot/readback cost
and do not measure scanout. The earlier paired 2K component benchmark belongs
to ABI 2; it showed comparable costs, not an established speedup, and is not
performance evidence for subsequent ABIs.

Broader device-loss/allocation-failure qualification, dense-mesh and combined
multi-object/UDIM interactive stress, other Blender builds/GPUs, and clean
machines without development tools remain release gates. Source snapshots and
explicit save/export readback are distinct from ordinary GPU-only publication.
See [bridge decision](bridge-decision.md) for synchronization and recovery limits.
