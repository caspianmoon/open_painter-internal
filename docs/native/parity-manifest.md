# Parity manifest — native C backend (P0 inventory)

Status: **inventory plus tested ABI 10 host-Metal integration**. Source of truth is the pinned implementation
(`5a55206`), not this document. Every row must name the test or fixture that
proves it; rows without evidence are marked `UNPROVEN`.

## 1. Channels (8 — `src/open_painter/constants.py:34-49`)

| Channel | Default | Evidence |
| --- | --- | --- |
| BASE_COLOR | (0.5, 0.5, 0.5, 1) | `tests/integration/engine_parity.py` — PASS with C IR compilation, planning and compiled host Metal submission |
| ROUGHNESS | (0.5, 0.5, 0.5, 1) | same as above |
| METALLIC | (0, 0, 0, 1) | same as above |
| NORMAL | (0.5, 0.5, 1, 1) | same as above |
| HEIGHT | (0.5, 0.5, 0.5, 1) | same as above |
| AO | (1, 1, 1, 1) | same as above |
| EMISSION | (0, 0, 0, 1) | same as above |
| OPACITY | (1, 1, 1, 1) | same as above; coverage alpha vs authored OPACITY separation per plan §8 |

Do not ship a four-channel subset and call it complete.

## 2. Blend modes (`BLEND_ITEMS`, `constants.py:5-32`)

28 entries; first five positions are stable for existing `.blend` projects:
`NORMAL, MULTIPLY, ADD, SUBTRACT, OVERLAY`, then `DIVIDE, DARKEN, LIGHTEN,
DIFFERENCE, EXCLUSION, SCREEN, LINEAR_BURN, COLOR_BURN, COLOR_DODGE,
SOFT_LIGHT, HARD_LIGHT, VIVID_LIGHT, LINEAR_LIGHT, PIN_LIGHT, DISABLE, HUE,
SATURATION, COLOR, LUMINOSITY, NORMAL_COMBINE, RNM, NORMAL_ADD,
NORMAL_REPLACE`.

Parity evidence: `tests/integration/native_blend.py`,
`tests/integration/engine_parity.py` and `PARITY.md`. The standalone C reference
slice covers a bounded fill subset. The integrated host executor uses typed
blend IR and C shader generation for all 28 modes; it does not use that CPU slice.

## 3. Layer / folder semantics (must preserve)

- Stable IDs, ordering, folder parenting, isolated + passthrough behavior,
  visibility, per-layer and per-channel opacity, blend overrides,
  source/effect ordering, masks, references, disabled-channel behavior.
- Procedural seed scheme + integer hashing must be reproduced bit for bit
  where fixtures require it (`procedural_glsl.py`; worst-case `5e-4` on
  transcendental tails per `docs/open-painter-engine.md`).
- Thresholds are starting points, not universal tolerances: `5e-4` precise
  path, `1e-3` display path, plus stricter coverage/ID, angular normal,
  seam, and height-detail checks in later gates.

## 4. Engine modules mapped for migration (verify symbols before patching)

| Existing area | Role in migration |
| --- | --- |
| `properties/layer.py` + property callbacks | submit narrow commands; retain UI + validation |
| `evaluation/engine/compiler.py`, `headless.py`, `glsl.py` | semantics source for portable IR; reuse fixtures + shader math |
| `evaluation/engine/runtime.py` (`_publish`, budget, fences) | orchestration to replace incrementally; publication boundary is the Gate 1 risk |
| `evaluation/shader_preview.py` | session lifetime / readiness → native revision state |
| `evaluation/cache.py` | short native submissions / completion polling; remove default full-output publication from editing |
| `core/texture_set_node.py` | EEVEE adapter if Route A succeeds; compatibility role otherwise |
| `painting/projection.py` + GPU paint modules | revisioned snapshots; brush/projection migration (P5) |
| `procedural_graph/` | translate graph definitions to portable IR; keep graph UI |
| `registration.py` | safe load/undo/save/shutdown lifecycle for native sessions |
| `scripts/benchmark_windows_viewport.py` | keep component benchmark; add native identities + GUI interaction protocol |

## 5. Test index (run before claiming parity)

- `tests/integration/engine_parity.py` — stack/blend/channel parity.
- `tests/integration/engine_procedurals.py` — 71 procedural sources.
- `tests/integration/engine_pixel_publish.py` — Vulkan pixel path on any backend.
- `tests/integration/engine_preview.py`, `engine_preview_readback.py` — preview currency.
- `tests/integration/engine_stages.py`, `engine_state_cache.py` — invalidation/caching.
- `tests/integration/engine_detail_fields.py` — triplanar detail fields and their copies,
  bit-identical to the detail compiled into every channel.
- `tests/integration/engine_uniform_publish.py`, `engine_constant_publish.py`,
  `engine_compact_readback.py`, `engine_fused_publish.py` — publication variants.
- `tests/integration/stroke_parity.py`, `gpu_parity.py`,
  `material_surface_parity.py` — paint/GPU/material equivalence.
- `scripts/check_parity.py`, `scripts/engine_validation.py` — harnesses.

Executed checks and their scope are recorded in `release-evidence.md`. An
existing test listed here is not automatically a completed qualification gate.

## Implemented ABI 10 ownership

The old opaque-fill C document API remains a bounded reference fixture. Production
materials use the C program, graph, stack, resource and revision APIs; they do not
use the fill fixture as a replacement material model.

| Feature family | Implementation and evidence |
| --- | --- |
| Stable IDs, transactions, generations, CPU leases | Bounded infrastructure; C lifetime/document tests |
| Snapshot parsing/restore | Internal v4 with length/checksum validation; existing `.blend` recipe remains project truth |
| All blends, masks, channel overrides | C owns all 28 blend formulas and alpha composition; masks/overrides enter through typed IR; `native_blend.py`, `engine_parity.py` |
| Pointwise effects and mask folding | All 30 effect kernels and typed mask levels/invert/blend operation in C; independent CPU/legacy GPU comparisons and live binding checks in `native_effects.py`; validation in `test_effects.c` |
| Nested mask groups and prefixes | C active-run traversal and postorder fold plan, filter-prefix isolation and continuation references; `test_masks.c`, `test_native_masks.py`, `native_masks.py` |
| Nested isolated/passthrough folders | C stack composition/backdrop plan; `test_stack.c`, `engine_parity.py` |
| Procedurals and references | 71-family C compiler, native DAG cycle rejection; `engine_procedurals.py`, `test_ir.c`, `test_graph.c` |
| Filters and padding | C fixed shaders, filter pass/weight plans, exact gutter donors; `engine_filters.py`, `test_filter.c`, seeded padding comparisons |
| Brush | C stamp uniforms, coverage/pressure/flow/alpha, resampling/bounds, anchor/target selection and GPU shaders; `test_native_paint.py`, `gpu_strokes.py`, `stroke_parity.py` |
| Geometry | C bounds index, owned surface snapshots, rasterization/interpolation, frames, adjacency, distance, surface walk, UV topology/borders/seam pairing, closest-seam bands/selection, island bounds and projection Jacobians; `test_native_geometry.py`, `test_native_topology.py`, `test_mesh.c`, `test_topology.c` |
| Batch execution | C owns pass submission and last-consumer holds; 65,536-node chain, failure at every callback and zero-budget Metal parity; `test_graph_execution.c`, `native_host_metal.py` |
| Channel selection | C accepted output descriptors and atomic changed-channel batches; `test_channels.c`, `test_native_channels.py`, `native_host_metal.py` |
| Resource retirement | C texture pool and retained/filter/history cache policy; pool/cache C tests and `engine_memory_soak.py` |
| Publication | Native immutable multichannel revisions, C-owned preflight/submission/rollback and eviction recovery of all mips; `native_host_metal.py` |
| Undo/save/load | Existing durable recipes plus native generation teardown; `shader_undo.py`, `save_during_evaluation.py`, `native_paint_reopen.py` |
| Workflow adapters | Evaluated projection, UDIM, multiple sets, aging, Material Director, material surface parity and surface-filter suites pass; some intentionally exercise CPU compatibility workflows |
| Interactive Metal | Real-window 2K slider and brush fixtures, compiled public GPU API binding, clean extension install |

See [migration status](migration-status.md) for the retained host ownership.
Presence in this inventory does not imply full native ownership of every host
adapter or qualification of untested devices. See the release report for sustained-run measurements.
Historical ABI 2 scheduler evidence remains separately identified.
