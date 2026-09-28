# GPU painting and authoring upgrade

The approved six-stage implementation is present and the stage acceptance tests pass. CPU evaluation remains the export reference.
Vulkan and OpenGL API simulations are not driver certification; real GPU tests here
use Blender 5.2.1 on Metal, including forced pixel publication.

## Stage validation

1. Central texture sizes and separate retained, pooled, active and undo accounting.
   Six baseline cases cover 1K/2K/4K and one/four channels, cold and warm P50/P95,
   transfers and allocations. Raw reports are in `.blender-test/authoring-upgrade`.
2. GPU stroke geometry, region accumulation, direct resource bindings, RG scalar
   layouts, incremental replay, full-resolution gesture adoption and evictable GPU
   region snapshots. Forty-eight kernel comparisons and append/undo parity passed.
   Overlapping UVs retain CPU rasterization. Color accumulation retains full float
   precision before compact engine intermediates; Height uses RG32F.
3. All catalog neighborhood effects use engine passes where supported. Seventy-two
   UV comparisons and seventeen seam-aware comparisons passed. Prefixes and uniform
   parameters remain cached. Unsupported combinations use the CPU reference.
4. Object-local XYZ symmetry, multichannel smudging and BLF text sources are present.
   Four-channel smudge parity, symmetry-plane deduplication, CPU-only text rendering,
   missing-font preservation, complete gestures and saved-file replay passed.
5. New UDIM groups share an authoritative recipe through a central owner resolver.
   Tile-local RNA projections preserve images, bakes, resolution and export naming.
   Linking rejects mismatched recipes; existing files remain independent. Editing
   through a member, local pixels, structural edits and unlinking passed.
6. Brush Graphs freeze validated programs at gesture start and save resolved values.
   Pressure, tilt, position, normal, time, distance and sample index drive math,
   vector operations, seeded randomness and brush/material outputs. Alpha multiplies
   coverage; the existing image alpha brush remains available. Seeded particles use
   120 Hz collision steps and persist contacts as ordinary samples. Frozen graph and
   contact determinism, invalid graph rejection and CPU replay passed.

Smart materials now write version 5 and read versions 1–4. Text masks and native
Mask Graphs, including text nodes, round-trip with generated image assets. Stroke
payloads have explicit versioning and shape/finite-value validation. Graph-driven
resolved dabs and reflected planar strokes currently use the exact CPU source path;
ordinary painting, erasing and smudging have GPU paths.

## Measurements and coverage

The current backend matrix passed strict Vulkan/OpenGL API simulations, native GPU
contracts, procedural parity, complete stack parity, forced pixel parity and
publication, engine staging, GPU strokes, neighborhood filters, authoring sources,
and the material-node interface. The pure suite passed 407 tests (two skipped).
Actual Vulkan/OpenGL driver behavior and speed remain unmeasured.

The broad GUI suite exposed temporary-scene texture-tree aliasing and recycled scene
addresses retaining a stale engine. Scene-specific texture trees and scene-session
identity checks now protect the artist's material and disposable review scenes.
Dedicated candidate inspection and repeated live-mask inspection tests passed.

The complete background run and corrected-test reruns cover 162 scripts, including
loading a 0.12.2 reference file and comparing its appearance and export settings.
Thirty-three GUI workflows and six additional real-window GPU suites passed. The
final backend matrix contains fourteen native/forced-publication/simulation cases.
The initial broad run identified legacy CPU-boundary assumptions; those tests now
explicitly exercise CPU fallbacks while new tests cover owned GPU resources.

GPU handles and undo acceleration remain transient; persisted stroke data and CPU
export do not depend on those caches. Unlinking a UDIM group also copies editable
stroke histories, and duplicated strokes retain resolved graph/smudge payloads.

## Measured comparison

Blender 5.2.1, Metal, saved pre-change source `dbf3ef0`, one cold and five warm runs
per case in separate processes. Each timed GPU result is synchronized by reading
one pixel from that actual output. Earlier scratch-texture synchronization results
were discarded. P95 below is the observed percentile of five samples, not a
production tail-latency guarantee. Painting includes a bounded preview plus
publication at the document resolution.

| Resolution | Channels | Paint P50 before → after, ms | Paint P95 before → after, ms | Gaussian CPU → GPU P50, ms |
|---|---:|---:|---:|---:|

| 1024 | 1 | 21.67 → 4.53 | 23.33 → 4.95 | 17.46 → 5.26 |
| 1024 | 4 | 22.02 → 9.02 | 23.26 → 9.55 | 62.53 → 8.76 |
| 2048 | 1 | 25.34 → 6.60 | 32.13 → 7.04 | 72.85 → 6.29 |
| 2048 | 4 | 41.13 → 16.33 | 41.64 → 20.42 | 293.91 → 26.24 |
| 4096 | 1 | 39.15 → 12.69 | 44.75 → 14.58 | 299.46 → 22.89 |
| 4096 | 4 | 61.44 → 46.86 | 65.21 → 56.72 | 1210.46 → 94.40 |

Gaussian uses radius 4; the CPU baseline processes scalar arrays, while GPU passes
process RGBA. CPU export formulas remain unchanged. Scalar engine stages now use
four bytes per texel instead of eight, and Height uses eight instead of sixteen;
mask storage stays four bytes. Committed color stroke accumulation retains full
float precision. Retained/undo caches share the existing 512 MiB per-set budget,
and the separate global free-texture pool is bounded to 512 MiB with LRU reuse.

Cold preview setup is slower in most cases because it builds the GPU surface
lookup: approximately 0.2–0.7 seconds here. That geometry is reused across gestures.
Bounded previews of existing GPU stroke resources now avoid full-image readback;
parity tests check this with the actual readback counter. Full-resolution eligible
commits are adopted, and other commits append only their new records.

Four-channel 4K publication remains slower under the bounded pool (about 47 ms
versus 37 ms); warm painting still improves there, approximately 61 → 47 ms.
Opacity-edit undo timings show no consistent speedup. GPU stroke-undo snapshots
are validated separately by cache hits, zero stroke replay, eviction recovery and
injected allocation failures; no universal undo speedup is claimed.

The repository record `docs/validation/gpu-authoring-upgrade.json` contains all
cold/warm samples, publication and undo timings, explicit transfers, allocations,
evictions and memory categories. Counters exclude Blender-internal transfers,
driver overhead and the diagnostic one-pixel synchronization reads. Raw logs are
in `.blender-test/authoring-upgrade`. Actual Vulkan/OpenGL performance is unmeasured.
