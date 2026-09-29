# Metal release evidence — Open Painter 0.14.0 / ABI 10

The native implementation now owns shader IR/compiler and parameter banks,
layer/folder and dependency plans, checkpoint selection, resource policies,
brush/geometry kernels, filter plans and immutable material revisions. The
compiled CPython adapter submits draws through Blender's public GPU API.
ABI 9 adds native nested-mask traversal, fold order, continuation references
and isolated filter-prefix planning. ABI 8 supplies the 30 pointwise effects
and mask levels, inversion, strength and folding. These build on ABI 7 channel acceptance,
ABI 6 UV topology, ABI 5 blend/batch kernels and ABI 4 surface/anchor and
publication transaction ownership.

The macOS arm64 delivery follows the plan's embedded host-device route.
The [ownership contract](migration-status.md) records the native core and retained
Blender/compatibility adapters. Windows/Vulkan is deferred to the other machine
as requested. The unrelated standalone application is unchanged.

ABI 10 adds global managed-texture admission, idle-result reclamation, bounded
shared shader reuse/reflection and host metadata allocation rollback. Metal
fault tests preserve all eight accepted outputs and retry successfully.

## Executed checks

Host: macOS arm64, Apple M3 Pro, Apple clang 17 release build and Apple clang 21 sanitizer build; stock Blender **5.2.1 LTS**,
build **9e2066aef7ef**, Metal. All Blender runs use isolated profiles.

| Check | Result and scope |
| --- | --- |
| Release CMake/CTest | 25/25 PASS, assertions enabled |
| AddressSanitizer + UndefinedBehaviorSanitizer | 25/25 PASS (combined instrumentation) |
| Python regression suite | 391 tests, OK, five skips |
| Native differential geometry | Five tests: owned bounds, UV overlap/clipping, non-manifold/degenerate adjacency, tangent/interpolation and exact padding donors |
| Native surface/anchor comparisons | Three tests: owned geometry, stale-anchor validation, nearest-target ties and 256-sample chunk boundaries |
| Native seam comparisons | Two tests: rotated edges, unequal UV density, overlapping bands, coverage, island ties and malformed input |
| Native UV topology | Six tests: randomized meshes/material subsets, non-manifold edges, stable island IDs, 5,184-face grid, int64 vertex pairs, owned buffers and sparse island bounds |
| Native differential brush | Four tests: coverage/pressure/flow/alpha/eligibility, distance/windows, surface walk and resampling/bounds |
| Compiled binding validation | Six tests, including old-ABI capsule rejection, graph submission/cleanup/reentry, native publication exception/reentry/closed-owner checks and 400 deterministic mutated IR inputs, malformed buffers and atomic graph rejection |
| Metal integration | 26/26 PASS, including 21 background suites, slider/brush GUI suites, fresh reopen, five sustained viewport runs and clean install |
| Native mask planning | 1,280 randomized range/flag/continuation comparisons through compiled and ctypes bindings; 65,536-level C hierarchy; six Metal cases against CPU and legacy GPU, max channel error 4.89e-4; no numeric-edit replan/rebuild/compile |
| Native effect/mask parity | 90 effect cases and 30 mask cases × 1,024 pixels; effect maximum absolute error 5.96e-7 against legacy GPU and 5.37e-7 against NumPy; eight live edits with no graph rebuild or shader compilation |
| Native blend parity | 28 modes × 2 alpha contexts × 1,024 pixels; maximum absolute error 5.96e-8 against legacy GPU and 5.37e-7 against NumPy |
| Native channel acceptance | Render/commit failure at every callback, copied requests, exact constants, target/configuration changes, subset publication, reentry rejection, host metadata and retirement failure recovery; C, compiled/ctypes and Metal checks |
| Native batch execution | 65,536-node chain; shared/repeated inputs; failure at every acquire/render/release; reentry rejection; zero-byte retained-cache Metal parity |
| Static analyzer | 25 C translation units including the compiled binding, no diagnostics |
| Extension validator | PASS |
| Evaluated native geometry | Actual beveled mesh, mirrored/nonuniform transforms, tangents, adjacency, padding, UV labels/borders/seam sides, nearest flags and island bounds match the reference |
| Workflow compatibility | Fourteen suites PASS: evaluated projection, multiple sets, surface bounds, UDIM workflow/material, aging, Material Director, material surface parity, surface filters, mask groups, live masks, mask graphs, material recipe masks and projected masks |
| Resource pressure | Global admission before GPU allocation; idle-result reclamation; active shader drawing after eviction; three host registry allocation failures roll back C leases |

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

Release: `dist/open_painter-0.14.0-macos-arm64-native.zip`.
It bundles the ABI 10 dylib and CPython 3.11 limited-ABI module with hash validation.
Users need no compiler, pip installation or environment variable. Neither this
package nor any test add-on was installed in the artist's ordinary profile.
The dylib and module depend externally only on macOS libSystem; the module
resolves Python through the host interpreter.

The functional suite began before correcting `constants.VERSION` to match the
0.14.0 manifest. Artifact collection verifies that this single constant is the
entire Python source difference. The rebuilt final ZIP is installed and
exercised in a separate clean profile; the workflow suite also runs on final
source. Original results and the installation recheck are both retained, and the
combined report identifies the source run for each check.

ABI 10 evidence uses the `metal-v10-` prefix. Earlier ABI 2 through ABI 9 evidence remains intact
and is not relabeled as a test of the new binary. Each integration run also keeps
its own immutable directory under `.blender-test/native/metal-integration/runs/`.
The latest report is copied to the parent for compatibility with existing tools.

- [Artifact identities](evidence/metal-v10-artifacts.json)
- [Native mask hierarchy and visible reorder parity](evidence/metal-v10-native_masks.py.log)
- [Live-mask GUI result](evidence/metal-v10-workflow-live-masks.json)
- [Native effect and mask parity](evidence/metal-v10-native_effects.py.log)
- [Native blend parity log](evidence/metal-v10-native_blend.py.log)
- [Release integration outcomes](evidence/metal-v10-results.json)
- [Native publication and recovery](evidence/metal-v10-native-host-metal.json)
- [Slider report](evidence/metal-v10-native-slider-ui.json)
- [Brush report](evidence/metal-v10-native-brush-ui.json)
- [Workflow outcomes](evidence/metal-v10-workflows.json)
- [Python checks](evidence/metal-v10-pure-tests.log)
- [Final installed-package check](evidence/metal-v10-install-results.json)
- [Hardware, power mode and compilers](evidence/metal-v10-environment.json)
- [ASan/UBSan checks](evidence/metal-v10-asan.log)
- [Static analysis](evidence/metal-v10-analysis.log)
- [Sustained viewport trace](evidence/metal-v10-native-sustained-ui.json)

## Reproduction

```sh
python3 scripts/build_native.py --build-dir /tmp/openpainter-native-release \
  --package dist/open_painter-0.14.0-macos-arm64-native.zip
python3 scripts/run_native_metal_tests.py \
  --lib /tmp/openpainter-native-release/libopenpainter.dylib \
  --package dist/open_painter-0.14.0-macos-arm64-native.zip --gui --sustained
OP_TEST_NATIVE_LIBRARY=/tmp/openpainter-native-release/libopenpainter.dylib \
  /Applications/Blender.app/Contents/Resources/5.2/python/bin/python3.13 \
  scripts/run_pure_tests.py
```

`--gui` requires a logged-in desktop. The harness opens an isolated stock Blender
test window; the add-on itself creates no extra painting window. The runner
checks JSON assertions as well as process exit codes.

## Sanitizer toolchain

The selected Xcode Clang 17 ASan runtime stalled before `main`, including in a
minimal empty program. The installed Command Line Tools Clang 21 runtime runs
successfully with the same explicit macOS SDK. No global toolchain selection
was changed. The final portable core passes all 25 tests under combined ASan
and UBSan instrumentation:

```sh
cmake -S libopenpainter -B /tmp/openpainter-native-asan-final \
  -DCMAKE_C_COMPILER=/Library/Developer/CommandLineTools/usr/bin/clang \
  -DCMAKE_OSX_SYSROOT=/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk \
  -DOP_SANITIZERS=ON -DOP_SANITIZER_SET=address,undefined -DCMAKE_BUILD_TYPE=Debug
cmake --build /tmp/openpainter-native-asan-final --parallel
ctest --test-dir /tmp/openpainter-native-asan-final --output-on-failure
```

## Sustained viewport measurements

`native_sustained_ui.py` runs five warmed 60-second sequences of scripted RNA
opacity edits against a nonuniform 2048 material with all eight channels enabled
in a real rendered EEVEE viewport. It records callback receipt, revision
acceptance and POST_PIXEL draw separately. Every command is classified as drawn,
accepted but not drawn, or superseded. Missed ideal timer slots are recorded
separately from delivered commands. Screenshots occur outside timed runs.

These are receipt-to-draw measurements, not OS input latency, GPU completion or
scanout. The separate native widget and brush tests provide actual input and
completed framebuffer capture evidence. This is a repeated native stability
measurement, without a paired speedup claim against the legacy implementation.

| Run | Received / drawn / superseded | New revisions/s | Callback p95 / p99 ms | Receipt-to-draw p95 / p99 ms | Drawn-state age p95 ms |
| --- | --- | --- | --- | --- | --- |
| 1 | 1781 / 776 / 1005 | 12.93 | 1.10 / 1.80 | 52.12 / 65.47 | 136.99 |
| 2 | 1725 / 751 / 974 | 12.51 | 1.22 / 1.84 | 50.99 / 59.26 | 147.21 |
| 3 | 1678 / 730 / 948 | 12.15 | 1.38 / 2.50 | 58.52 / 77.91 | 154.10 |
| 4 | 1663 / 709 / 954 | 11.80 | 1.42 / 2.46 | 64.17 / 80.77 | 154.53 |
| 5 | 1776 / 778 / 998 | 12.96 | 1.21 / 2.14 | 53.46 / 63.74 | 136.06 |

The machine was on AC power with Low Power Mode enabled; its settings were not
changed. Latency percentiles above apply to drawn commands; superseded commands
remain in the trace and count column. No accepted-but-undrawn commands were
observed in these runs. This workload **does not meet** the proposed p95 ≤33 ms
or 60 Hz material-feedback targets. POST_PIXEL cadence is not display scanout.

All five runs passed stability assertions: zero warm shader compilations,
topology rebuilds, geometry extraction or CPU image writes. Managed storage
reached 512 MiB leased plus approximately 480 MiB pooled after the first run
and stayed at exactly the same totals for runs 2–5. This is a managed-storage
plateau, not a total renderer/driver-memory measurement. The counters also
record allocation churn; no allocation-free steady-state claim is made.

## Paired performance follow-up

A subsequent [five-pair legacy/C comparison](paired-performance.md) used the
unchanged final ABI 10 implementation. C was slower in four of five pairs for
viewport feedback and receipt-to-draw latency, and in all five for evaluation
call time. This release establishes integration and recovery behavior; it does
not establish a performance improvement on the tested Metal workload. The paired
report retains all runs, full-map parity and source/binary identities.

## Qualification boundaries

The tested platform is the stock Blender build and M3 Pro listed above. Physical
device loss, other GPUs/builds and a separate clean machine without developer
tools are unrun. Context/submission errors are explicit; image eviction and
recoverable publication/allocation failures are tested. No arbitrary-hardware
latency or universal 60 Hz material-feedback claim is made.

Workflow fixtures cover dense evaluated geometry, multiple texture sets, UDIMs,
seams and projection. They do not establish an unlimited combined workload size.
The global managed budget excludes Blender renderer/image/driver memory. Source
snapshots and explicit save/export readback remain distinct from ordinary
GPU-only publication. See [bridge decision](bridge-decision.md) for synchronization
and recovery limits.
