# Gate 0 baseline — September 29, 2026

Pinned source: `5a552060988d0b43a7ef05f6e43da2aec481b323`.
Work branch: `feat/native-c-engine` (already selected at implementation start).
The checkout contained a partial uncommitted core and counters; this work
preserves and extends them. The embedded core uses `libopenpainter/`, because
`native/` belongs to the standalone application and remains unchanged.

**Target Windows reproduction unavailable.** This host is macOS arm64 with
stock Blender 5.2.1 (`9e2066aef7ef`). No Windows speedup, input-to-visible
latency, GPU/vendor qualification or successful native migration is claimed.
The original baseline runs registered source in isolated temporary profiles.
The subsequent Metal candidate package and clean-install evidence are recorded
in release-evidence.md. Tests do not modify the user's installed add-on.

## Target-machine fields still required

Installed add-on path and ZIP/package hash; Blender version/build hash and
bundled Python; OS/build; all GPU adapters and the adapter actually used;
driver and active backend; refresh rate; power mode; viewport dimensions;
2048 texture resolution and exact layer/source configuration. Do not infer
Vulkan from Windows or the active adapter from laptop branding.

## Implemented measurement work

- Retains `plain` and `masked` benchmark identities and timing definitions.
- Adds `nonuniform_image_fill_v1` and `paint_layer_v1` (cube, actual UV map,
  saved pressure-varying samples through the existing stroke raster).
- Per-event sequence, callback start/end/duration and accepted recipe serial.
  The callback timestamp is harness receipt, not OS input arrival.
- Explicit `image.pixels.foreach_set` counts and bytes at every engine write
  site, including uniform initialization, Vulkan publication and CPU sync.
- Python, whole native adapter, C source, native binary (when supplied),
  shader/compiler bundle, fixture source and build configuration digests.
- Native Route C traces identify the submitted generation/revision/channel
  set. GPU-ready, draw and actual display timestamps remain null/unknown.

The existing benchmark still measures callback/engine work and a diagnostic
completion read. It is not viewport FPS or display latency. Its full-resolution
validation reads are separate from interactive transfer counters. New fixtures
assert spatial variation of BASE_COLOR, not merely different RGB components.

Unimplemented observability includes complete production event correlation,
geometry rebuild reasons, GPU timestamps, native queue/supersession tracking,
GPU retirement, viewport-bound revision IDs and actual display evidence.

## Reproduction commands

```sh
python3 scripts/collect_gate0_baseline.py \
  --blender /path/to/blender --native-library /path/to/libopenpainter \
  --build-cache /path/to/CMakeCache.txt --output /tmp/gate0-environment.json
# Optional --package /path/to/installed.zip records the exact package hash.
python3 scripts/benchmark_windows_viewport.py --blender /path/to/blender \
  --backend auto --fixture image --sizes 512 1024 2048 \
  --repeat 6 --idle-repeat 100 --output /tmp/gate0-image.json
```

The local image/paint runs use two repeats at 512 to validate fixtures. They
are not the five sustained 60-second interaction runs required for performance
qualification. They ran concurrently; their timings must not support a speedup
comparison. Raw evidence is under `docs/native/evidence/`.

## Delivery evidence

This page preserves the initial baseline and its measurement limitations.
The final macOS Metal delivery is 0.14.0 / ABI 10. Current native ownership,
resource limits, safety checks, real-window input tests and sustained viewport
measurements are recorded in [release evidence](release-evidence.md) and the
[ownership contract](migration-status.md). Windows target capture and Vulkan
qualification belong to the deferred other-machine work.
