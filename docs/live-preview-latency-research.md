# Live preview latency: research and implementation decisions

Research date: 2026-09-13. This document explains the current optimization scope
and measurement methods. It does not present final before/after timings or
replace the recorded test results in [implementation status](implementation-status.md).

## Scope and source provenance

The work keeps the CPU evaluator as the authoritative texture producer and
retains the existing fixed display material, native UI and material recipes.
Live GPU rasters are disposable preview resources. Save, export, render and
explicit CPU evaluation must still complete the latest authored edits; live
publication does not clear authoritative output dirty flags. Default `FULL`
preview keeps authored resolution and float32 precision. Existing explicit
preview-quality choices remain user choices, not benchmark optimizations.

The installed test environment reports **Blender 5.2.1 LTS, build
`9e2066aef7ef`, Metal, Apple M3 Pro**. Exact-hash source URLs could not be
resolved, and a bounded local search found no matching C++ source checkout.
The Blender links below identify either current `main` or the immutable
`v5.0.0` tag. They establish implementation rationale, not exact-build identity.
Behavior and performance on the installed binary require actual isolated
Blender tests. No timing from an earlier experiment is a new measurement.

## What the renderer research establishes

**Offscreen completion and interactive completion are different measurements.**
The Python `GPUOffScreen.draw_view3d()` wrapper passes the image-render flag;
the draw context selects viewport rendering. EEVEE's viewport-image path
iterates sampling to its target, whereas the ordinary interactive path renders
a sample and schedules further refinement. Therefore an all-samples offscreen
result cannot be relabeled as latency to the first native viewport draw.
[Python wrapper, main](https://raw.githubusercontent.com/blender/blender/main/source/blender/python/gpu/gpu_py_offscreen.cc),
[draw context, main](https://raw.githubusercontent.com/blender/blender/main/source/blender/draw/intern/draw_context.cc),
[EEVEE instance, v5.0.0](https://raw.githubusercontent.com/blender/blender/v5.0.0/source/blender/draw/engines/eevee/eevee_instance.cc).

**Material invalidation has a real cost, but removing it is incorrect.**
Default `ID.update_tag()` invokes broad dependency invalidation; material
evaluation frees the GPU material descriptor list. This can require material
conversion/setup even with unchanged nodes. It does not prove recompilation
of GPU machine code, which may remain cached. EEVEE also checks dependency
graph updates to reset temporal accumulation; a raw texture write supplies no
such update. The existing publication path preserves the necessary tag and
dependency update rather than displaying stale accumulated shading.
[RNA ID API, main](https://raw.githubusercontent.com/blender/blender/main/source/blender/makesrna/intern/rna_ID.cc),
[dependency tagging, main](https://raw.githubusercontent.com/blender/blender/main/source/blender/depsgraph/intern/depsgraph_tag.cc),
[material evaluation, v5.0.0](https://raw.githubusercontent.com/blender/blender/v5.0.0/source/blender/blenkernel/intern/material.cc),
[EEVEE instance, main](https://raw.githubusercontent.com/blender/blender/main/source/blender/draw/engines/eevee/eevee_instance.cc).

**A POST_PIXEL callback's active framebuffer can be the overlay target.**
Blender binds its overlay framebuffer before post-view callbacks. Transparent
zeros from that target do not demonstrate that EEVEE rendered the edited
material. The native harness captures the color framebuffer in PRE_VIEW and
reads that same frame's target after rendering. Warm samples require finite,
nonzero, opaque shaded content at the framed model's center.
[Draw callbacks and engine order, main](https://raw.githubusercontent.com/blender/blender/main/source/blender/draw/intern/draw_context.cc).

## Optimization choices in this change

| Work avoided | Implementation and correctness boundary |
| --- | --- |
| Unused CPU cellular outputs | `_cells()` keeps the full 5×5 search and strict nearest-winner ordering, but computes only requested distances/identity. It hashes the selected identity once instead of each discarded candidate. |
| Unused CPU breakup noise | Generators evaluate breakup noise only when their selected operation consumes it. Scratch-only and direct mesh-map operations no longer pay for unused FBM. |
| Identity transforms and fills | Zero-angle CPU coordinates avoid unnecessary rotation temporaries. The live planner skips a constant Normal fill only when its RGB equals a known opaque backdrop. It makes no such inference inside an isolated folder, from a texture, or after an unknown source. |
| Cold scalar raster generation/upload | Restricted GPU scalar kernels produce cached R32F fields. Small CPU float32 coordinate tables retain the CPU octave rounding order without constructing an entire scalar image on the CPU. |
| Cold generator-mask raster generation/upload | Restricted GPU mask kernels sample CPU-decoded baked maps using explicit center-based bilinear interpolation, then apply the existing generator and stack operations. Fields and maps share dependency-keyed caches. |
| Repeated folder composition | An unmasked, full-opacity passthrough folder needs no intermediate copy/blend. A bounded cache can retain the completed children of the first root folder: passthrough starts from the fixed channel default, while isolated children start transparent. Changing only that folder's opacity or mask can reuse its children. |

The CPU changes are tested against frozen pre-change sources from
`df32483a158fb3852564654c6d10f5f2ff7673d4`, including catalog aliases, transformed
and wrapped coordinates, strip boundaries and cellular ties. Their acceptance
criterion is array equality, not a looser visual approximation. GPU source
and mask tests instead use declared float32 tolerances against the CPU
reference; they do not promise bitwise GPU/CPU equality. Unsupported domains
retain CPU evaluation.

The folder cache is capped at 256 MiB, uses scene/texture-set, resolution,
channel, folder identity, group mode and child signatures, and retains complete
results only. Changes to children or ownership invalidate reuse. Isolated
cached results retain premultiplied RGB and coverage. Later root folders and
nested folders are excluded. Eviction, disabled caching, save/restore, deletion
and failure paths must preserve output.

Implementation:
[CPU procedurals](../src/open_painter/texture_engine/procedurals.py),
[CPU generators](../src/open_painter/generators/base.py),
[live planner](../src/open_painter/evaluation/live_inputs.py),
[GPU scalars](../src/open_painter/evaluation/live_procedurals.py),
[GPU masks](../src/open_painter/evaluation/live_masks.py),
[GPU composition and folder cache](../src/open_painter/evaluation/live_gpu.py).

## Current GPU eligibility and fallback limits

These limits describe the new scalar/mask acceleration, not removal of
existing material or mask capabilities.

- **Backend:** new scalar and mask kernels currently require a Blender window
  and the validated Metal backend. Other contexts retain CPU sources. Live
  composition also requires a valid window context, sufficient configured
  resources, and a material not shared by different texture-set owners.
- **Scalar kinds:** Perlin, FBM, Grunge Medium, Brushed Metal Lines, Concrete
  Pores, Orange Peel, Scratches and Voronoi, including their existing aliases.
  The planner selects identity UV transforms and repeat wrapping on a
  power-of-two grid. Scale must be in `(0, 256]`; seeds must be nonnegative and
  at most `2^31 - 1 - 512`. Non-default rotation, persistence or anisotropy
  falls back. The standalone kernel also bounds width, radius and octaves.
- **Scalar consumers:** unfiltered fill channels without an input image,
  excluding Normal, may share scalar fields across color/scalar/height
  modulation. Normal generation, transformed/triplanar sources, sampled
  textures and effects continue through the CPU leaf path.
- **Mask kinds:** Edge Wear, Paint Chips, Dirt, Cavity Dirt, Rust and Scratches
  from `GENERATOR` sources. An entire enabled stack must qualify before any
  field/map callback or mask-target allocation. Paint, references, filters,
  other generators and transformed masks fall back as a whole.
- **Mask sampling and operations:** identity UVs, repeat and bilinear; full
  float32 map inputs; Normal, Multiply and Add stack operations; original
  first-mask, invert and strength semantics. Missing required maps fall back;
  optional AO remains part of edge-wear evaluation when available.
- **Mask parameter stability:** Levels require `white - black >= 0.25`;
  reversed/equal/narrow intervals fall back. Scratch amplification additionally
  requires `2 * amount * contrast / (white - black) <= 2`. This avoids
  amplifying small scalar differences into materially different masks. The
  committed Worn Painted Metal defaults meet these guards.
- **Precision and size:** scalar/mask targets are R32F and composed display
  targets RGBA32F, up to the checked grid limit of 8192, subject to resource
  budgets. A legal maximum is not a performance or memory guarantee. No
  automatic downsampling or half-precision substitution is used to qualify.

Fallback retains the existing CPU-generated leaf inside live composition
where possible. A whole live-preview failure returns to authoritative CPU
evaluation; it must not mark a failed or partial result current. GPU-only
preview pixels are never treated as saved project textures.

## Benchmark interpretation and readback scope

[The native harness](../tests/performance/live_native_frame.py) runs the same
file against each checkout through `OP_BENCH_REPO`, using that checkout's
fixture. It records source/harness hashes, actual region size and unchanged
EEVEE settings. Cold preparation, warm roughness edits, folder-opacity edits
and no-edit redraw controls are separate. At least five warm repeats are
required; cold setup precedes controls, then roughness, then opacity. Optional
`--procedural-edits` adds mask-strength and shared-noise-scale phases afterward.

The report's `evaluation_mode` distinguishes two methods:

- **Explicit:** consumes the live job directly and separately times dependency
  update. Preparation, dependency update and the native draw/fence wait are
  distinct intervals. Add-on timer/cooperative-yield delays are excluded.
- **Scheduled (`--scheduled`):** retains production `cache.tick`, which alone
  advances the live job. Preparation measures property mutation; the following
  wait includes scheduler delays, cooperative yields and native dependency
  processing. The separate dependency phase is null. A frame is accepted only
  when its draw began with the requested live revision current.

Both include GPU queue waits and fence overhead. Neither measures physical
presentation or OS input delivery, and the two modes must not be pooled under
one latency label. A first native frame is distinct from final TAA convergence;
the cold frame can still await asynchronous shader compilation. Nonzero center
color validates the target, not whole-image parity.

The native fence requests 3×3×4 float values: **144 bytes of result payload**.
Metal's framebuffer code forwards that rectangle. Its texture reader sizes
the destination from the requested extent and copies that region or performs
conversion over it, but calls `GPU_finish()` before and after the read.
Neither full-frame host transfer nor only-144-byte internal traffic follows
from that code; driver resolves and bus traffic are not measured.
[Metal framebuffer, main](https://raw.githubusercontent.com/blender/blender/main/source/blender/gpu/metal/mtl_framebuffer.mm),
[Metal texture read, main](https://raw.githubusercontent.com/blender/blender/main/source/blender/gpu/metal/mtl_texture.mm).

The Python framebuffer wrapper provides no attachment-texture getter or blit
method for the captured native framebuffer. The mask probe owns its output
texture and copies a dependent pixel into a reusable 1×1 target before readback;
the scalar probe requests one pixel directly from its output attachment. Both
perform full-resolution parity reads outside the completion interval. Managed
image-transfer counters do not measure every Blender/driver transfer or allocation.
[Python framebuffer methods, main](https://raw.githubusercontent.com/blender/blender/main/source/blender/python/gpu/gpu_py_framebuffer.cc).

## Rejected or deferred approaches

- **Removing material tags or tagging unrelated IDs:** risks stale temporal
  history or unrelated dependency work. `Material.update_tag(refresh={'DATA'})`
  is not a supported narrow material invalidation. Reusing correct cached
  inputs and completed children reduces actual work without this shortcut.
- **Replacing material tags with image updates:** `Image.update()` schedules
  CPU pixels for GPU upload and would overwrite disposable GPU-only content.
  `Image.update_tag()` can still propagate through nodes to material shading;
  it has no demonstrated invalidation-cost advantage here.
  [Image API, main](https://raw.githubusercontent.com/blender/blender/main/source/blender/makesrna/intern/rna_image_api.cc),
  [dependency relations, main](https://raw.githubusercontent.com/blender/blender/main/source/blender/depsgraph/intern/builder/deg_builder_relations.cc).
- **MRT mip generation:** a two-target RGBA32F prototype wrote an image mip and
  its next scratch input together. It passed the isolated pixel-parity probe,
  but the completed texture-job measurement showed only a small saving;
  this was deferred rather than treated as a major latency improvement.
  The first level kept separate passes to avoid sampling the same image being
  written. The [archived probe](performance/latency-mip-experiment.json)
  excludes cold preparation, EEVEE rendering and UI latency. Its result is
  not a final before/after benchmark for this change.
- **Fusing all channels with MRT:** this is a different, unvalidated proposal.
  Attachment count and per-pixel storage are separate constraints. The
  two-target mip probe did not fail a hardware limit and does not establish
  behavior for eight channel targets. Apple's limits vary by GPU family and
  explicit versus implicit image-block storage; there is no universal 64-byte
  limit to infer from the number of attachments. Sampling dependencies also
  restrict which passes can merge. The production path keeps validated
  single-target float32 passes; precision is not reduced to fit an MRT design.
  [Apple Metal feature tables](https://developer.apple.com/metal/capabilities/),
  [color-renderable format sizes](https://developer.apple.com/documentation/metal/understanding-color-renderable-pixel-format-sizes),
  [Apple's archived render-encoder guide](https://developer.apple.com/library/archive/documentation/3DDrawing/Conceptual/MTLBestPracticesGuide/RenderCommandEncoders.html).
- **Broad shader-graph replacement, lower precision or authored resolution:**
  outside this incremental change. Display nodes, authored textures and
  existing material compatibility remain the baseline.

## Validation entry points

Focused checks are in
[CPU optimization parity](../tests/unit/test_procedural_optimization.py),
[identity planning](../tests/integration/live_identity_planning.py),
[GPU scalar parity](../tests/integration/live_procedurals.py),
[GPU mask parity and decline guards](../tests/integration/live_masks.py),
[folder-cache parity and lifecycle](../tests/integration/live_gpu_checkpoints.py)
and [live-preview integration](../tests/integration/live_preview.py).
Their presence is not a claim that they ran. Completion reports must supply
the actual commands, environment, source revision and recorded outcomes, and
label native-frame and all-samples offscreen results separately.
