# Metal editing performance — 0.14.1 / ABI 11

This release removes redundant publication passes and warm texture allocation
from the native Metal path. Output remains full resolution; no viewport samples,
material channels, precision settings or machine power preferences are reduced.

## Implementation

- Keep the accepted revision's complete mip chain for rollback and image eviction
  recovery. Do not regenerate it on every edit.
- Reuse one unreferenced chain per channel, protected by the existing 4 GiB device
  admission limit. Reclaim these spare buffers under memory pressure.
- Pin final channel sources until publication completes and retain them in the
  existing 512 MiB result cache. This avoids cycling simultaneous channel results
  through a free pool designed for sequential draws. When the worst-case source
  set exceeds that allowance (4K+ at the default budget), stage bases sequentially
  and release each source before rendering the next channel instead.
- Finish directly into the Blender image and recovery texture in one draw.
  Generate and publish each subsequent mip for up to three channels in one draw,
  using Blender's six public framebuffer attachment slots.
- The C batched transaction prepares every channel before writing and marks all
  channels touched before calling the host once. Any partial submission failure
  restores every mip of every prepared channel before rejecting the revision.
- Precompile all three batch widths at initial publication so changing the dirty
  channel subset does not compile shaders during numeric editing.
- Cold snapshots match the actual image precision and copy each existing mip
  with explicit LOD sampling. They preserve arbitrary prior mip contents without
  widening every channel to RGBA32F.
- Coalesce pending preview timer wakes. Repeated RNA events preserve the already
  scheduled callback, and the executing timer is never unregistered by its own
  work.

The eight-channel 2048-square opacity fixture now submits **57 GPU passes** per
evaluation, compared with **296** in 0.14.0 and **112** in the legacy executor.
It allocates **zero managed GPU textures after warmup**, compared with **125 per
eight-channel publication** in 0.14.0. These counts describe this fixture, not
every possible material.

The 4K fixture uses 68 passes and completes repeated eight-channel edits under
the unchanged 4 GiB managed budget. Its regression compares sampled output patches
after restoring an edited value; full-map parity is measured by the 2K runner.
4K timing is not represented as a real-window responsiveness measurement.

The change targets GPU submission and resource lifetime. Handwritten assembly
would not eliminate these passes, allocations or timer queue operations.

## Validation protocol

Use the same paired runner and full-output comparison as the
[0.14.0 investigation](paired-performance.md). The current legacy comparator
also includes the timer coalescing fix. Historical 0.14.0 measurements remain
separate and must not be described as contemporaneous matched timings.

```sh
/Applications/Blender.app/Contents/Resources/5.2/python/bin/python3.13 \
  scripts/benchmark_native_metal.py \
  --lib /tmp/openpainter-native-perf/libopenpainter.dylib \
  --pairs 5 --seconds 60 --require-no-warm-allocations
```

The benchmark counts newly drawn material revisions, preserves superseded inputs
and stalled runs, compares every pixel in all eight final maps, and verifies
unchanged source and binary hashes. Receipt-to-draw ends at Blender's POST_PIXEL
callback; it does not measure hardware scanout or OS mouse delivery. Timing
excludes screenshots and full-map readbacks.

Warmup starts after the first successful evaluation and requires at least 16
accepted evaluations, as well as the 15-second interval, for both executors.
Each trace records the actual warm evaluation count. The earlier wall-clock-only
warmup could expire during the first viewport build on a busy desktop; the new
allocation assertion rejected that attempt (57 one-time native allocations).
That failed experiment is preserved separately and is not a completed paired
performance result.

Regression coverage includes exact base/mip rollback after partial publication,
image eviction, repeated allocation-free warm edits, subset edits, memory pressure,
zero retained-cache budget, timer coalescing, shader/recipe parity, undo, painting,
save/reload and resolution changes. C tests inject failure at each preparation
and partial batch-write point. The compiled binding tests exhaustive rollback
and propagation of the original Python exception.

## Real viewport results

Five alternating 60-second pairs, with every stalled run retained:

| Pair | Legacy drawn revisions/s | Native drawn revisions/s | Legacy draw p95 (ms) | Native draw p95 (ms) |
| --- | ---: | ---: | ---: | ---: |
| 0 | 11.50 | 11.37 | 41.13 | 31.46 |
| 1 | 9.99 | 13.83 | 49.51 | 59.20 |
| 2 | 18.74 | 0.19 | 47.83 | 1345.90 |
| 3 | 13.37 | 11.36 | 35.58 | 41.13 |
| 4 | 11.62 | 10.75 | 36.65 | 69.38 |

Native improved drawn-revision throughput in only one of five pairs. The median
paired throughput change was **−7.53%**; the median paired draw-p95 change was
**+19.59%**. This run does **not** establish faster viewport interaction than the
legacy executor, nor an instant/60 Hz experience. All eight final 2048² output
maps were bit-identical in every pair. Every native run used 57 passes and zero
warm managed texture allocations.

During pair 2, read-only macOS inspection reported 18 GiB physical memory,
27,505.12 MiB swap in use, and 11% memory-pressure free. Other editor, browser,
build and local-model workloads were running. The severe native stall remains
in the report; system pressure is a confound, not a measured explanation of a
specific fraction of latency. No unrelated processes or power settings were
changed. Per-process system-load observations accompany the raw runs.

At 2K, native managed textures settled at approximately **1,264 MiB** (1,184 MiB
leased plus 80 MiB pooled), versus **992 MiB** in the old C release and **480 MiB**
in the legacy path. Retaining reusable revisions increases persistent memory in
exchange for eliminating repeated allocation. These figures exclude Blender's
own images, viewport resources and driver memory. Admission can reclaim spare
chains and result caches; it cannot control unrelated applications' memory.

The viewport run froze its source before one final correction restoring the
explicit full-float intermediate override. Its default half-float/HEIGHT formats
and pass counts are unchanged. The exact candidate source and the small
[final-source patch](evidence/metal-v11/viewport-to-final.patch) are preserved;
its hashes are not relabeled as the final package. The direct release comparison
below and the full-precision regression use the final production source.

## Direct comparison with the previous C release

Three alternating pairs compare the packaged 0.14.0 C backend with final 0.14.1
source in fresh background Blender processes. Each process performs 20 warm
edits, then the same 100-value opacity trajectory on eight nonuniform 2048² maps.
Each timed update includes RNA assignment, material evaluation, and the existing
one-pixel GPU read fence. The fence is benchmark instrumentation; production
interaction does not gain a readback. Full output readbacks and compression occur
outside timing. This measures completed material updates, excluding viewport
rendering, event delivery and display scanout.

| Pair / execution order | 0.14.0 median (ms) | 0.14.1 median (ms) | Speedup | 0.14.0 p95 (ms) | 0.14.1 p95 (ms) |
| --- | ---: | ---: | ---: | ---: | ---: |
| 0 / old → new | 28.70 | 5.37 | 5.34× | 47.47 | 8.87 |
| 1 / new → old | 29.70 | 6.38 | 4.65× | 55.99 | 13.81 |
| 2 / old → new | 31.85 | 7.29 | 4.37× | 50.99 | 14.52 |

The median paired speedup is **4.65×**. All three pairs have bit-identical full
output maps. The old backend allocates 12,500 managed textures per 100 edits;
the new one allocates zero. Neither recompiles shaders nor writes image pixels
through Python during the timed edits. Managed memory is stable from the start
to the end of each timed run. This is evidence of a faster C material-update path
on this fixture, not a general speedup for every operation or proof of instant
viewport response.

```sh
/Applications/Blender.app/Contents/Resources/5.2/python/bin/python3.13 \
  scripts/benchmark_native_updates.py \
  --baseline-package dist/open_painter-0.14.0-macos-arm64-native.zip \
  --lib /tmp/openpainter-native-perf/libopenpainter.dylib \
  --pairs 3 --samples 100
```

## Evidence and package

- [Direct comparison and all samples](evidence/metal-v11/completed-updates/results.json).
- [Viewport comparison, including the stalled run](evidence/metal-v11/results.json).
- [Qualification, source/binary identities and package checksum](evidence/metal-v11/qualification.json).
- [Compressed viewport trace checksums](evidence/metal-v11/traces.json).

Validation includes 394 Python tests (five expected skips), 25 release C tests,
25 C tests under AddressSanitizer/UndefinedBehaviorSanitizer, and the Metal
integration suites listed with their exact source identities in qualification.
The explicit full-precision regression compares every mip of all eight channels
against the legacy executor. The final ZIP is checked byte-for-byte against
production Python/native artifacts and installed into an isolated Blender
extension repository; the artist profile is not changed.

Installable artifact: `dist/open_painter-0.14.1-macos-arm64-native.zip`.
Vulkan and Windows remain outside this machine's qualification scope.
