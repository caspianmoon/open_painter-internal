# Windows plan: state and next steps

This page hands the Windows work over from the macOS sessions. They left the work at commit
`fb335e1` on `feat/native-c-engine` (2026-10-01). It is written for a Claude Code
session on the user's Windows laptop. Read it before changing anything; it
replaces the context those sessions kept on the Mac.

## Rules the user set (non-negotiable)

1. **Zero failing tests.** Every red test is yours to triage and fix, including ones that
   were already red before your change. Fix the product, or update a test whose expectation
   is outdated after checking that each difference is intended. Never report "known
   failures" and move on.
2. **Memory-safe Blender runs.**
   - A 200-layer 2K benchmark once drove a Mac to 75 GB and crashed it.
   - Never run a large Blender benchmark (100+ layers, 2K+, structural loops) without the
     user's explicit approval for that run. Start at 40 layers and 1K.
   - Never run two Blender processes at once.
   - Launch Blender through the memory guard once it works on Windows (step 4).
   - The laptop's GPU has 4 GB of VRAM, so keep GPU fixtures small too.
3. **Native kernels are exact.**
   - Every C kernel must give bit-identical results to the Python (NumPy) code it replaces.
   - Don't let the compiler contract a multiply and an add. GCC/Clang get
     `-ffp-contract=off`; MSVC gets `/fp:precise`.
   - Every kernel keeps a scalar path (`-DOP_SCALAR_<kernel>`).
   - Every vector path (NEON, SSE4.2) has a test comparing it with the scalar path or the
     NumPy reference.
   - When you change a kernel, change it on both NEON and SSE and run `test_pixels_paths`.
   - Mutation-check new tests: change the arithmetic deliberately and make sure the test fails.
4. **Product rules.**
   - The managed material is exactly three nodes: Texture Set, Principled BSDF and
     Material Output.
   - There is exactly one node editor type, Open Painter Graph.
5. **Commits.** Commit as you go, with descriptive messages: what changed, why, and how it
   was verified. Work on a branch of your own (below). Ask the user before pushing.

## Progress on the Windows laptop

Branch `feat/native-windows`, 2026-10-01 to 10-02. Each commit message records its
evidence.

- **Machine:** Windows 11 22631; i7-8750H (SSE4.2, AVX2/FMA3); 12 GB RAM, about 5 GiB
  available with the desktop running.
- **GPU and Blender:** GTX 1050, 4 GiB, driver 560.70; Blender 5.2.2 LTS (`d13f752e3b9c`).
- **Toolchain:** VS 2022 Build Tools 17.14 (MSVC 14.44) with the Windows 11 SDK 10.0.26100;
  python.org CPython 3.11 for the binding headers.

| Step | State | Result |
| --- | --- | --- |
| 1. MSVC build | Done | 33/33 in the Release and scalar builds, no `/W4` warnings. `test_pixels` takes 41 s here, so pass `--test-timeout 120` to `build_native.py`. |
| 2. Unit tests | Done | 530 tests, no failures. 3 skips: no system libpng (2), POSIX shebang layout. |
| 3. Baseline matrix | Done | Vulkan 21/21 (`.blender-test/windows-vulkan/validation-native-branch`). |
| 4. Guard | Done | Job Object; `tests/unit/test_run_guarded.py`. |
| 5. Suites | Done | All pass without and with the library, on OpenGL and on Vulkan. `native_geometry_projection.py` needs the library. |
| 6. Binding | Done | In Blender, `abi()` is 13 and `compiled_binding: true`. |
| 7. Package | Done | `open_painter-windows-x64.zip`: validates, installs into a non-ASCII path on both backends with no environment variables, passes the two-phase clean install (6 GB cap), and the step 5 suites pass against its binaries. |
| 8. Native engine | Done | Vulkan, and on Windows OpenGL, are default host backends, with collapse, idle arming and fused finals ported (OpenGL since `6ae821b`: "Undo and publication in place"). |
| 9. Windows on Arm | Not applicable | This laptop is x64. |
| 10. Full gate | Done, one suite over the cap | At `ee80d04` (2026-10-06): background (182 suites and the 0.12.2 contract), GPU and GUI pass on OpenGL and Vulkan, except `quality_baseline_paint_ui.py` at 2K, killed at the approved 6 GB cap (6.05 and 6.6 GiB). Four Blender unit tests failed on a test's fake (fixed in `a5f8b16`; all 569 then pass). At `869a2ce`: 180 suites, 542 unit tests, the same cap (6.30 and 6.09 GiB; below). |

### Findings that change how to work here

- **Text encoding.** A plain `python.exe` reads and writes text as cp1252 on Windows.
  Blender's Python runs in UTF-8 mode, so add-on code is fine, but tests and scripts must
  pass `encoding="utf-8"`; they all do now.
- **Guard measure.** On Windows the guard caps the job's commit charge. GPU drivers commit
  system memory alongside video memory, so GUI runs commit 4–5 GiB with a 1.2–1.8 GiB
  working set. The summary reports `working_set_peak_gib`, `processes_peak` and peak
  dedicated GPU memory.
- **Approved caps.** The user approved these caps on this laptop:
  - GUI runs: 6 GB;
  - `resolution_switch.py` and `native_publication_4k.py`: 4 GB;
  - `utility_maps.py` under the OpenGL native scheduler: 5 GB. It commits 3.2 GiB on
    OpenGL otherwise, and `run_selected_tests.py` gives it 4 GB.
- **Bevel UVs are not bit-stable.** Re-evaluating a bevelled mesh can move a few corner
  UVs by one ulp, even with one thread. Tests that compare two evaluations at about 1e-6
  must read one snapshot (see `native_geometry_projection.py`, `evaluated_projection.py`).
- **Budgeted publication.** On Vulkan one tick publishes channels within a time budget,
  not all of them. A test may assert "current after one tick" only where
  `runtime.device().pixel_publish` is false.
- **GPU allocations at exit.** Some Vulkan runs print `VMA: UNFREED ALLOCATION` at exit, for
  example `layer_store.py`, `structural_patch.py` and `moved_layer_adoption.py`, with the
  legacy scheduler too. Each was a run that ended without `open_painter.unregister()`, which
  releases the device (`forget_device`) and unfreezes the collector (`gc_policy.release`).
  The likely cause: frozen reference cycles that hold GPU textures outlive Blender's GPU
  shutdown. A user who quits Blender sees the same, because Blender does not unregister
  add-ons on quit. No test fails, and it is cosmetic at exit.
- **A Blender preview-loading crash.** In the first OpenGL GUI gate, `authoring_ui.py`
  passed and then hung at exit, after Blender printed `Code marked as unreachable` from
  `IMB_thumb_path_unlock`. Reproduced twice in three runs:
  - one exit crashed (code 11);
  - one hung. Blender's crash log, with every thread's stack, shows an access violation in
    `blender::PreviewLoadJob::run_fn` (the job that loads asset previews for the shelf),
    reading address 0x20.
  - The job holds a pointer into a `PreviewImage` that was freed while it was loading. The
    path unlock then reads garbage, and the job crashes, or Blender's crash handler hangs.

  It did not recur in 27 later runs, including runs with the asset shelf hidden, with
  library refreshes or new images forced during loading, and after evicting the file
  cache. Python cannot see the job, so a test cannot wait for it. The trigger is not
  identified. The guard's `--timeout-seconds` makes such a hang fail its suite instead of
  stalling the gate. To catch the next occurrence, fault a thread in the hung `blender.exe`
  (CreateRemoteThread at an unmapped address): Blender's crash handler then writes every
  thread's stack to `%TEMP%\blender.crash.txt`.

### Publication on Vulkan (2026-10-02)

Steps 8 and 10 ran with Vulkan publishing through pixels. Each channel was read back,
written into `Image.pixels`, and re-uploaded by Blender with its mips on the next draw. The
reason given in `4575759`, that image textures cannot be render targets on Vulkan, came
from a simulated GPU on a machine without Vulkan (`dbf3ef0`'s strict fake GPU). On the GTX
1050 with Blender 5.2.2, an image's own GPU texture (RGBA16F, SRGB8_A8) takes clears, draws
and mip-level attachments, and keeps them across redraws. Two probes showed this: a draw
into half of the texture, read back, and levels 0 and 1 written separately, then sampled
with `textureLod`.

**In a window, every backend renders into the images** (`85cba57`): one pass finishes,
pads and writes level 0, and one pass per level writes the mips. Deep stack, 40 layers at
1K, Vulkan, medians of two alternating rounds, in ms:

| Edit | Through pixels | Into the images |
| --- | ---: | ---: |
| Bottom folder opacity | 121–145 | 12.5 |
| Top folder opacity | 53 | 10–16 |
| Middle leaf opacity | 57–64 | 10.7 |
| Add fill (top) | 102–125 | 17 |
| Move layer | 45–49 | 16 |
| Visibility | 38–43 | 10 |
| Remove layer | 33–35 | 10 |
| Blend mode | 47–57 | 20–23 |
| Mask black | 58–62 | 28 |
| Paint tick | 16 | 12.5 |
| Apply smart material | 170–197 | 121–124 |

Three memory effects then pushed background suites past the 4 GB cap (`resolution_switch`
4.05 GiB against 2.11, `material_painting` 4.12 against 3.32, `material_director` killed).
They were handled in `4b32d81`:

- **Native revisions** keep each channel's previous mip chain and its next one, and pin
  the final sources until the batch commits. They now run only where that fits the
  retained-result budget (`runtime.revisions_fit`): 1K everywhere, 2K from 16 GB of RAM,
  never 4K. Elsewhere each channel publishes straight into its image, as OpenGL's default
  scheduler always did.
- **Eviction.** Blender recreates an image's texture from its CPU pixels when it evicts it
  (Texture Time Out, `gl_free`), and those pixels lag behind a GPU publication. A revision
  restored its channels. Channels published in place had no recovery, on OpenGL's default
  scheduler too. `InPlacePresentation` holds the wrapper of the texture each channel was
  published into, which keeps that texture alive, and copies every mip into the recreated
  one before the next viewport draw, with no new evaluation (`engine_eviction.py`).
- **Deferred destruction.** Blender's Vulkan backend destroys released GPU resources (a
  resize's old textures, upload staging) only after a wait or a frame end, and a
  background session has no frames. Publishing into the images never waits, so each 1K
  resolution switch kept about 0.5 GB. `Device.settle()` waits once after an evaluation
  that submitted passes, in background sessions. A 1K switch loop then peaks at 1.71 GiB
  instead of 3.39, flat across switches.
- **Background Vulkan sessions publish through pixels** (`runtime.BACKGROUND_PIXELS`).
  Vulkan image textures count toward the process commit charge on Windows (about 29 MiB
  per 1K image here). A background session never draws them, and every consumer (export,
  bake, save) reads the CPU pixels. This is the route the background suites took before
  `85cba57`. In a window Vulkan keeps the faster route. Benchmarks that run in background
  measure the window route with `OP_ENGINE_PIXEL_PUBLISH=0`.

Peaks after the change (guard, 4 GB cap):

| Suite | Vulkan | OpenGL |
| --- | ---: | ---: |
| `resolution_switch.py` | 2.12 GiB | 2.87 GiB |
| `material_painting.py` | 3.30 GiB | 3.73 GiB |
| `material_director.py` | 2.42 GiB | 2.58 GiB (was 3.98–4.03; its tests now release their scenes) |
| `engine_eviction.py` | 0.77 GiB | 0.96 GiB |

`native_publication_4k.py` still cannot run here. Rendering into the images, it was killed
at 4.0 GiB during its first evaluation: the eight 4K images' CPU pixels alone are 2 GiB.

### Undo and publication in place (2026-10-02, evening)

Profiles of the deep stack (40 layers at 1K, Vulkan, window route) found three costs in an
undo and in a full republish. None of the fixes changes a published pixel.

- **Every undo rebuilt the output textures.** Each publication called
  `image.update_tag()`, so that EEVEE restarts its temporal history. Blender's undo
  therefore counted every output image as changed since its step. An undo read the image
  again, which drops its GPU texture, and the next use rebuilt it from CPU pixels that lag
  behind the GPU result: 7–10 ms per 1K channel, about four times that at 2K. The engine
  then published over it anyway. A plain-Blender probe shows the same for any image tagged
  between two undo steps, and for a Non-Color image whose colour space was set then.
  - A publication now tags the set's material instead, as `live_display` already did.
    EEVEE's history restarts the same way; `shader_preview_ui.py` and
    `shader_snapshot_ui.py` pass on both backends.
  - `OP_TAG_IMAGES=1` restores the image tags. Undo, steady state: 204–215 ms with image
    tags, 159–163 ms with the material tag.
- **Publication in place dropped every final result.** A revision commit hands each
  channel's final result to the bounded result cache, so the next edit that composes over
  it reuses it (a layer added on top, an undo). A channel published in place released it,
  and that edit rendered the stack below again: adding a fill on top took 80 ms instead of
  20 (597 native submissions instead of 4). `_keep_final` keeps it in the same cache, whose
  budget is enforced once the evaluation has published.
- **Publication in place leased its mip scratch every time.** Each lease and return is a
  call into the native registry, which calls back into Python: about 1 ms per published
  channel at 1K.
  - The device now keeps one scratch texture per lane, level and format up to 2K
    (`Device.mip_scratch`, budget `MIP_SCRATCH_BYTES`).
  - The engine whose mips are queued owns it until they are drawn. Another engine leases
    its own meanwhile, and nothing is dropped from under a queue.
  - The mips of three channels share a draw, as in a revision commit (`MIP_LANES`).

With those three fixes, publication in place matches revisions on warm edits, beats them
after an undo and needs about 0.45 GiB less. Vulkan and OpenGL therefore publish in place:
`runtime.REVISION_BACKENDS` is Metal, where revisions were qualified. Vulkan, 40 layers at
1K, two alternating rounds, in ms:

| Edit | Revisions | In place |
| --- | ---: | ---: |
| Top folder opacity | 10.5–10.6 | 10.5–10.8 |
| Bottom folder opacity | 13.0–13.1 | 12.8–13.4 |
| Middle leaf opacity | 11.0–11.1 | 11.1–11.3 |
| Bottom leaf roughness | 6.7–7.3 | 6.4–6.5 |
| Middle mask black | 28.3–28.4 | 28.5 |
| Visibility | 10.9–11.2 | 10.5 |
| Undo (numeric) | 162–197 | 120 |
| Undo (structural) | 144–158 | 113–125 |
| Peak commit | 3.17 GiB | 2.72 GiB |

Undo on this route was 174–190 ms in the morning (`85cba57`) and is now 84–125 ms. The
first undo after a build took 280–320 ms, and 218–222 ms since `869a2ce` (see "What
remains").

Also measured:
- **The Python scheduler** (OpenGL's default before `6ae821b`) draws through the binding's C
  function now (`BINDING_DRAWS`). These are the same calls, and the A/B showed no measurable
  change.
- **The 4K export** pads each map with `op_padding_extend`: 44 ms in place against 184 ms
  for the NumPy gather, and 310 MiB less transient memory. `export_padding.py` still peaks
  at 3.91–3.92 GiB, so its peak lies elsewhere.

What remains, by measured share (as of 2026-10-02; later sections update the first items):
- **Smart-material apply, about 110 ms.** Compiling the new units' node trees takes
  2.7 ms per unit (Python node, link and parameter construction). The IR walk takes
  12 ms, and the composer's module snapshot 8–10 ms. On 2026-10-06 (profiled, per apply)
  it was still compiling about 35 new units (including the first decode of a new bundled
  PNG), the IR walk and the composer: Python work in proportion to the units added.
- **Undo, 84–92 ms in steady state** (a probe of every texture lease and of each stamp
  that changes, Vulkan, window route). Blender's memfile restore takes 14 ms, our undo
  handler 2.3 ms, the rebuild 45–52 ms (compiling with adoption, the plan, value reads) and
  the evaluation 10–13 ms. Nothing is allocated: the earlier churn of about 120 textures
  per undo was the revisions' staged mip chains. Since `ee80d04` an undo that kept the
  stack's shape follows the graph instead of a rebuild: 58–61 ms on the benchmark against
  80–84 (2026-10-06, "Undo that keeps the graph").
- **The first undo after a build, 280–320 ms, now 218–222 ms** (`869a2ce`). The memfile
  restore re-tags the images whose tags were still pending when its target step was pushed
  (Blender's `direct_link_id_restore_recalc`): here the detail images a smart material
  loaded, whose colour space the loader sets. Those updates carry no geometry, shading or
  transform flag, and Blender keeps the images' pixels, but `on_depsgraph` moved their
  epochs: the 66 units sampling them rendered again, and the second undo rendered 92 units
  with collapse unarmed.
  - An image update now moves the epoch only with one of those flags. Blender's image
    (paint) undo restores its tiles and then calls `DEG_id_tag_update(&image->id, 0)`, the
    tag `update_tag()` also makes, which sets all three (measured); a colour space change
    sets them too. `pixels.foreach_set`, `update()` and `reload()` report no depsgraph
    update at all, and Open Painter's own strokes move `open_painter_pixel_revision`.
  - The second undo now takes 90–92 ms (was 131–154). What remains of the first is
    Blender rebuilding the GPU textures of the images it read again (about 60 ms): the
    benchmark's setup tags are pending at its first step. A window session's operators
    leave such tags too: an apply loads images and sets their colour space just before
    Blender pushes its step (corrected below, "Pending depsgraph updates", 2026-10-03).
  - The same tags made the benchmark's cold build render those units twice (146 ms):
    background Blender evaluates them only when the engine reads geometry, mid-tick. A
    window evaluates them only after the timers, so its tick sampled them first too (same
    section).
- **Painting at 2K and above.** A brush input took 12.8–13.6 ms at 1K (40 layers, both
  backends). Half of that was the benchmark's whole-canvas upload, which Blender's brush
  never pays: 4.0–4.3 ms since `0f3d519` (2026-10-03, below). The rest is mostly GPU work
  over whole textures: the painted layer's channels, the
  collapsed suffix above them and the publication. At 2K that work is four times larger.
  Rendering only the stroke's bounds through the stack (region-limited evaluation) is the
  main remaining lever. It changes how results are cached (a result updated inside a
  rectangle rather than replaced), so it is a design change, not a tuning one.
- **A mask edit mid-stack, 26–33 ms, now 12–13 ms** (`bfdeb2b`). It rendered 58 units per
  tick, 41 of them unchanged: results released after each tick and rendered again (see
  "Drags render only what changes", 2026-10-06). It now renders the 17 that change: the
  layers above it whose masks read the composite height below them ("Dirt in folds",
  "Cavity deposits") and the folder steps over them.
- **Cold build, 1.5–1.8 s.** About 0.45 s of it is decoding the thirteen bundled detail
  PNGs that the stack uses (1254 × 1254, about 37 ms each). They are noisy, so they hardly
  compress: an uncompressed lossless copy would load almost free at a similar size. That
  would change the provenance-tracked asset files, so it is the user's call.
- **`native_publication_4k.py`** was killed at 6.61 GiB (OpenGL, native) and 6.22 GiB
  (Vulkan, window route) under a 6 GB cap, so it cannot run on this laptop. Native
  scheduling is a default on both backends anyway (below).
- **The 2048 production-prop GUI suites** (`material_painting_ui.py`,
  `quality_baseline_paint_ui.py`) pass the 6 GB cap on both backends during the first
  evaluation of three sets of 25 layers: 6.38 GiB on OpenGL and 6.02 on Vulkan for the
  baseline (step 10). The engine's own textures are a small share of that peak.

**OpenGL on Windows schedules natively** (`6ae821b`), as Vulkan does. OpenGL is Blender
5.2's factory backend there, and its Python scheduler rendered every layer above an edit
again. The user had kept the native one opt-in until `native_publication_4k.py` had run, and
then left the call to me, on condition that nothing changes for an artist but speed.

- **Why it is on.** That suite cannot fit this laptop on either backend: it was killed at
  6.61 GiB under a 6 GB cap. Vulkan already schedules natively without it.
- **The evidence.** The same native suites pass on OpenGL (24 of 24, the 4K one aside), and
  the pixels are identical. The deep stack's memory is close to Vulkan's default (2.61
  against 2.72 GiB); in a window OpenGL's commit charge runs about 0.25 GiB above its Python
  scheduler's at 1K (step 10).
- **Where it stays off.** Elsewhere OpenGL is not qualified (Linux, other GPUs), so it keeps
  its scheduler there. `OP_NATIVE_HOST_BACKENDS=OPENGL` still opts it in.

Deep stack, OpenGL, 40 layers at 1K, two alternating rounds, in ms:

| Edit | Python scheduler | Native |
| --- | ---: | ---: |
| Top folder opacity | 28.6–29.0 | 9.8 |
| Bottom folder opacity | 78.2–78.4 | 11.9–12.4 |
| Middle leaf opacity | 69.2–69.6 | 10.2–10.8 |
| Bottom leaf roughness | 29.0–29.4 | 7.2–7.7 |
| Middle mask black | 73.0–74.7 | 26.6–32.6 |
| Move layer | 73.3–75.4 | 17.0–17.4 |
| Blend mode | 78.7–79.9 | 19.9–20.3 |
| Visibility | 69.0 | 10.5–10.8 |
| Paint tick | 26.5–26.7 | 12.8–13.6 |
| Apply smart material | 185 | 116–121 |
| Peak commit | 1.99 GiB | 2.61 GiB |

The evening's other changes also apply to the Python scheduler wherever it still runs:
- undo went from 311–377 ms to 120–127 ms;
- adding a fill on top went from 68–69 ms to 28–30 ms;
- removing a layer went from 64 ms to 23–25 ms.

### Pending depsgraph updates, paint undo, detail fields and the node sync (2026-10-03)

**Pending depsgraph updates are reported before the engine samples.** An image the stack
starts to reference (a smart material's detail, a picked texture) joins the depsgraph on its
next evaluation. That evaluation reports it as updated (a new node, and the colour space the
loader sets), which moves its epoch. Blender runs it at the end of the event loop's
iteration, after the timers. So the tick that followed an apply sampled the new images first,
and the next tick rendered every unit sampling them again.
- The scheduler's tick now evaluates pending updates first (`dependencies.report_updates`).
  With nothing pending it costs nothing, and it leaves a running render or bake job alone.
- An add-on operator that pushes an undo step does the same before Blender pushes it
  (`SafeOperator`). Otherwise the step stores the tags still pending, and an undo to it sets
  them again.
- A window probe on 45 layers at 1K. Simulated key presses run an apply, an opacity edit
  and Ctrl Z, as a click would. Three applies each load one new detail image:

| | Before | After |
| --- | ---: | ---: |
| Apply: evaluations, Vulkan | 2 each | 1 each |
| Apply: last result, Vulkan | 143–145 ms | 91–136 ms |
| Undo after it: until current, Vulkan | 187–240 ms | 119–152 ms |
| Apply: evaluations, OpenGL | 2 each | 1 each |
| Undo after it: until current, OpenGL | 256–286 ms | 154–244 ms |

  The undo times are from Blender's `undo_post` (its memfile restore, 30–50 ms, comes
  before). Before, the second evaluation after an undo rendered 224–296 channel units; with
  the tick's report alone, the undo took 141–173 ms on Vulkan. Vulkan ran three probes
  each way; two samples where the desktop stalled (a 138 ms restore, a 542 ms apply) are
  left out.
- `native_deep_stack.py` ticks as the scheduler does (`tick`), so its cold build no longer
  renders those units twice either.

**An undone brush stroke keeps the session.** Blender's brush pushes an image undo step per
stroke. Undoing one restores the canvas's pixels in place, but the undo handler started every
session over as after a memfile undo, so each Ctrl Z while painting rebuilt the whole graph:
33–42 ms of a 59–78 ms undo on 40 layers at 1K (a window probe, Vulkan).
- `cache.on_undo` now starts the sessions over only when the step restored datablocks. A
  memfile step reads the file into a new Main, so `bpy.data.as_pointer()` changes between
  `undo_pre` and `undo_post`. An image, sculpt or edit-mode step keeps it, along with every
  struct and Python wrapper the sessions hold. The pixels it restored report their depsgraph
  update like a stroke, and the session renders what reads them. A set evaluated on the CPU
  still starts over after any undo.
- An undo or redo of a stroke now reaches current in 13–19 ms on Vulkan (its evaluation
  1.3–1.5 ms) and 17–25 ms on OpenGL (4.3–5.6 ms; a first one took 93 ms), and publishes
  exactly the result from before the stroke. `paint_undo_ui.py` checks both backends; the undo
  that crosses back past the first stroke restores a memfile step and still starts over.

**The paint benchmark paints as Blender's brush does** (`0f3d519`). Its brush inputs were CPU
pixel writes (`Image.pixels.foreach_set` and `update()`), which make the next texture fetch
upload the whole canvas: 7.5 ms on Vulkan and 11 ms on OpenGL at 1K, half of the 13 ms it
reported per input. Blender's brush updates only the tiles it paints. A dab is now drawn into
the canvas texture; `OP_DEEP_PAINT_UPLOAD=1` restores the CPU writes. With the node sync
below, a brush input takes 4.0–4.2 ms on Vulkan (5.3 before) and 4.2–4.3 ms on OpenGL (6.7
before), 40 layers at 1K.

**The texture set node syncs in one pass over its tree** (`0f3d519`). Every evaluation syncs
it. It looked each role's node up with a scan of the tree, and checked each connection
through `socket.links`, which Blender computes by walking every link of the tree: about 1.5 ms
of a 1K paint tick. An index of the tree's nodes and links is now read once per sync.

**A fill channel's triplanar image detail is a unit of its own** (a FIELD,
`shader_graph.DETAIL_FIELDS`). A full render of the deep stack spent about 39 of its 87 ms of GPU
time (Vulkan, 40 layers at 1K) in the triplanar detail of channel units, about 1.2 ms each:
three projected image samples per texel, repeated in every pass of the channel and in every
channel of the layer. The detail is now rendered once into a full-float texture and kept
while the channel's colour, opacity, blend or masks change.
- A layer's channels sample the same detail with the same projection, so their fields hold
  the same texels. The engine renders the first and fills the others by copying its texture
  (`runtime.FIELD_COPIES`). A copy is released as soon as the channel reading it has drawn.
  Fields are matched by shader, parameter bits and image identities, never by their
  programs' uniform revisions, which differ between equal units.
- Only triplanar details become fields: another projection is one sample, cheaper than a
  pass of its own. Normal (a source boundary), Material Mapping v1 and procedural details are
  compiled as before.
- The published channels are bit-identical with fields on and off, and with copies on and
  off: every channel of the deep stack on both backends, and `engine_detail_fields.py`.
- The deep stack has 32 fields (40 layers at 1K; two alternating rounds each way, in ms;
  the benchmark now ticks as the scheduler does, so its numbers include the depsgraph's
  evaluation, unlike the tables above). Peak commit is unchanged, 2.5–2.7 GiB.

| Edit | Vulkan, fields | Vulkan, none | OpenGL, fields | OpenGL, none |
| --- | ---: | ---: | ---: | ---: |
| Middle mask black | 22.9–25.5 | 29.4–31.6 | 25.9–26.3 | 27.2–33.0 |
| Bottom leaf roughness | 6.2–6.6 | 7.4–7.5 | 6.6–7.0 | 7.8–8.0 |
| Bottom folder opacity | 13.8 | 14.8–15.0 | 13.6–13.7 | 13.0–14.0 |
| Add a fill mid-stack | 20.6–21.7 | 23.5–23.7 | 20.3–20.8 | 19.9–21.0 |
| Apply smart material | 112 | 120–125 | 120–121 | 106–116 |
| Undo (structural) | 109–110 | 104 | 114–124 | 105–114 |

  A full render's GPU time went from 87 to 62 ms on Vulkan: 8 fields rendered and 24
  copied (the profile does not time the copies). Edits that keep a layer's
  detail gain most. A rebuild handles 32 more units, which costs a few milliseconds where
  every unit is checked again (undo) and, on OpenGL, in an apply; other cases are within
  noise.

### Undo that keeps the graph, per-pass costs and resolution scaling (2026-10-06)

**A memfile undo keeps the graph when the stack kept its shape.** A memfile undo reallocates
every struct of the layer store, so each session was built again, adopting its units one by
one. On the deep stack that build was about half of an undo: the plan, every unit's
structural signature and owners, the reach map and the native description. Most undos keep
the stack's shape: they restore an opacity, a colour, a mask's level or a visibility, or a
blend mode, an effect's kind, a channel switched on.
- `cache.on_undo_pre` snapshots each settled engine set's stored properties: its layer store
  and its own. `cache.on_undo` snapshots them again after the restore, and
  `evaluation/undo_follow.py` compares the two.
  - A field counts as numeric by its declaration (a `numeric` update callback, in nested
    groups and collections too). Bookkeeping the compiler never reads may differ.
  - Other differences are allowed in layers whose collections still hold the same items in
    place. Items named by id (masks, anchors) or by channel must keep their place; a channel's
    effects are compiled by position.
  - Anything else rebuilds as before: a layer, mask or effect added, removed or moved, the
    set's own structure, or a datablock that went with the undo.
- `EngineGraph.follow_restored` re-points the retired graph's units to the restored structs
  (`_follow_undo`) and rebuilds the tables keyed by struct address. The layers that changed
  structurally are then patched as after a structural edit (`patch`), and every binding is
  read again. The graph keeps its engine. It refuses (and the set rebuilds) when a unit holds a
  CPU boundary, a cache, a Mask Graph or a datablock no path maps, when the graph has stubs,
  or when structural edits are pending.
- A snapshot costs about 2.5 ms on 44 layers: `to_dict`, then a walk over the fields that can
  hold a datablock. It is skipped in texture paint mode, where undos are a stroke's image
  steps (`1a43b39`).
- `undo_follow.py` checks it on both backends. Undos and redos of numeric values, a blend mode,
  an effect's kind and a channel switch are followed, keep the graph, equal a fresh compile
  (`verify_undo`) and publish bit for bit the state they returned to. Adding a mask or a layer
  rebuilds, and so does the second of two undos with no tick between them.
  `OP_FOLLOW_NUMERIC_UNDO=0` rebuilds every time.
- A first version kept `_live_effects`'s cache of effect structs and crashed in
  `validate_parameters`. The follow now drops every cache that holds structs.

| Deep stack, Vulkan, 40 layers at 1K, ms | Follow | Rebuild |
| --- | ---: | ---: |
| Numeric undo (background) | 58–59 | 80–82 |
| Structural undo (blend mode, background) | 60–62 | 83–84 |
| Numeric undo in a window, from the restore to current | 73–84 | 104–133 |

**Descriptors and colour spaces** (`8a1dcfe`). The engine's graph wrote the JSON descriptors a
material's persisted trees carry for rehydration, which the engine never reads. Every
evaluation read each sampled image's colour space through RNA (8–22 µs each); it is now read
from the Image struct's bytes, checked against RNA once per image. Paint 5.4–5.9 → 4.7–5.2 ms,
adding a fill about 1.5 ms less, structural undo 105 → 97 ms.

**Where a drag's GPU time goes.** Each pass timed between two GPU waits (an upper bound),
Vulkan, 40 layers at 1K, per evaluation:

| Case | Units | Publication | Mips |
| --- | ---: | ---: | ---: |
| Top folder opacity (8 channels) | 8 units, 4–7 ms | 8 passes, 2.7–3.6 ms | 30 draws, 1.3–4.1 ms |
| Mask edit mid-stack (4 channels) | 57 units, 18–20 ms | 4 passes, 2.9 ms | 20 draws, 2.4 ms |
| Paint input | 1 pass, 0.3–0.4 ms | 1 pass, 0.3–0.4 ms | 10 draws, 0.1–1.0 ms |

- **Publication.** It writes the image (a 32-bit float texture, 16 bytes a texel) and the copy
  its mips reduce, so it is bandwidth-bound: about 0.34 ms a channel at 1K, twice a unit
  pass. A half-float image would round normals and mip levels, so it stays.
- **Mips.** A mip draw costs about 35 µs of submission; small levels are all overhead. Four
  channels to a draw would halve a four-channel drag's draws, but Blender's framebuffer takes
  six attachments, which three lanes already use (`MIP_LANES`).
- **Units.** Every unit a drag renders is a real change. A mask edit mid-stack renders 57,
  because the masks above it read the composite height below them.

**How each case scales with resolution.** The deep stack at 512 and at 1K, split into a fixed
part and a part per texel (2K and 4K extrapolated; measuring them needs the user's approval):

| Case, ms | 512 | 1K | 2K | 4K |
| --- | ---: | ---: | ---: | ---: |
| Paint input | 4.1 | 4.9 | about 8 | about 20 |
| Leaf opacity drag | 8.0 | 11.6 | about 26 | about 83 |
| Bottom folder opacity drag | 9.3 | 14.4 | about 35 | about 116 |
| Mask edit mid-stack | 15.8 | 25.7 | about 65 | about 225 |
| Memfile undo, smart-material apply | 93–101 | 97–122 | fixed CPU | fixed CPU |

### Drags render only what changes; fused chains (2026-10-06)

**Kept reads** (`bfdeb2b`). On every tick of a drag the same units change, and they read
the same unchanged results. Examples are the inside of a smart material above the edited
layer, which its folder step composes over again, the layers below, and the fields those
read. The scheduler released such a result once its consumer had drawn, so the next tick
rendered it again with everything it reads. A mask edit mid-stack rendered 58 units per
tick, 41 of them unchanged.
- Each render records what a changed unit read (`Engine._note_reads`). `_settle` keeps
  those results, and `_update_frontier` holds them until an evaluation changes something
  without reading them. They fit in what the retention budget (`RETAIN_BYTES`) has left.
- The same drag renders the 17 units that change. `engine_kept_reads.py` requires that
  from the third tick on nothing unchanged renders, and that results equal a from-scratch
  render bit for bit. `OP_KEEP_READS=0` turns the rule off.

**Chain fusion** (`bfdeb2b`, `engine/fusion.py`). Inside a smart material, layers
composite with straight alpha over a transparent start, which is not affine, so suffix
collapse cannot fold them. An edit there re-rendered the siblings above it one pass each.
A fused pass runs the members' own programs in registers instead.
- **Exactness.** Between members the value is rounded as the member's half-float store
  would round it. NVIDIA's stores truncate (toward zero) on both backends, and
  `unpackHalf2x16(packHalf2x16(x))` is folded away by the driver, so the rounding is
  emulated with integer operations. `store_rounding` picks the emulation (toward zero or
  to nearest even) that matches this device's stores bit for bit in every value class:
  ties, half denormals, overflow, infinities, float denormals. Without a match, fusion is
  off. Each fused shader is also checked once against sequential rendering.
- **Scheduling.** A chain's top takes its desired stamp before C plans the channel, as a
  collapsed run's does. The members keep no texture, and their stamps say they hold their
  content, as an evicted result's would. (A stale stamp made one of them the lowest
  changed unit, and the checkpoints kept the wrong results.)
- **Compilation.** A chain is planned once per drag. Its shader is compiled when the
  same edit renders it again, or while idle for the selected layer; never for an apply,
  an undo or a cold build. A first compile takes 200–330 ms on Vulkan and 85–105 ms on
  OpenGL. Blender's SPIR-V cache and NVIDIA's GL cache keep it across sessions
  (1.2–1.8 ms after).
- **Effect.** A four-unit chain takes 0.65 ms of GPU time instead of 1.2 ms at 1K, and
  0.33 instead of 0.55 at 512, so the saving grows with resolution. At 1K a drag is
  mostly CPU, which hides most of it.
- **Tests.** `native_fusion.py` drags seven places, at display and full precision, with
  and without collapse. Results equal the same drag rendered sequentially and a
  from-scratch render, bit for bit. Both emulations are checked on the GPU against
  independent numpy models. Mutations caught: unrounded members, the same without
  verification (913 texels differ), parameter offsets dropped.

**Prepared first ticks** (`03a0174`). While idle, `Engine.prearm` now also:
- keeps what an edit of the selected layer will read (`_prearm_reads`);
- verifies the layer's fused chains;
- warms the suffix apply pipeline per target format.
After it, a drag's first tick renders only what changes; `engine_kept_reads.py` requires
it.

| Deep stack, Vulkan, 40 layers at 1K, ms | Before | After |
| --- | ---: | ---: |
| Mask edit mid-stack | 22.5–23.6 | 12.2–12.5 |
| Bottom leaf roughness | 5.9–6.0 | 5.1–5.2 |
| Middle leaf opacity | 11.3–11.6 | 10.5–10.8 |
| First tick of a middle-leaf drag (after idle arming) | 15–16 | 11–14 |
| First tick of a mask drag (after idle arming) | 25–52 | 16–23 |
| Folder drags, apply, undo, paint, cold build | unchanged | unchanged |

The bottom folder's first tick renders the same 16 units as later ticks but takes about
27 ms against 13. The difference is GPU time, as after any idle period.

### Folder steps with empty channels; mesh signatures after an undo (2026-10-06)

**A folder's parameters skip its empty channels** (`66aee06`). A smart material's children
often paint only some channels; in the deep stack: base colour, roughness, metallic and
height. In the other channels the folder step's group input is the constant transparent
composite, so it blends with zero alpha over its backdrop. Its result is the backdrop's,
whatever its opacity and masks, but its content stamp counted those parameters. Dragging
a folder's opacity therefore rendered and published those channels again on every tick.
- `Engine._param_free` leaves the parameters out of such a unit's content stamp.
- Each program is checked once on the GPU first (`_check_param_free`): a corner is
  rendered with the unit's own parameters, all zeros, all ones and random values, and
  the four must agree bit for bit.
- Measured: top folder opacity 10.95 → 8.1 ms (8 → 4 units), bottom folder 13.35 → 9.55
  (16 → 8).
- `engine_param_free.py` compares every channel's published image with a from-scratch
  render, the channels not published again included.

**Mesh signatures** (`b21b104`, `53cf6d3`). Every memfile undo, redo and load clears the
mesh fingerprints (`dependencies.clear`). The next geometry key then hashed each assigned
mesh again: about 0.7 ms per 1,000 vertices (hashlib's SHA-256 at 82 MB/s here, over RNA
reads that go element by element). On the benchmark's 1.1K-vertex sphere that was 4 ms,
but a 130K-vertex mesh took about 350 ms on every undo.

The same bytes are now:
- read from Blender's attributes, derived exactly where nothing stores them, or copied
  directly from Blender's memory (`_direct`). A direct copy happens only when the
  elements' addresses form one array and three values match RNA, as for an Image's
  colour space;
- hashed with `utils.digest`'s SHA-256 (the native one, 260 MB/s here);
- kept per mesh session id after hashing, so an unchanged mesh keeps its digest after an
  undo, compared byte for byte.

Digests are unchanged. A cached digest also has to match the mesh's session id: a mesh
created where a freed one lived could get its digest.

| Mesh signature, ms | 32K vertices | 130K | 523K |
| --- | ---: | ---: | ---: |
| Before, after any undo | 85–115 | 342–371 | 1,432–1,441 |
| First hash now | 24 | 80 | 329 |
| After an undo of an unchanged mesh now | 4–5 | 12–15 | 47–56 |

The deep stack's numeric undo with a 130K-vertex mesh takes 62–67 ms; before, it spent
about 350 ms more in the signature alone. Drags and applies cost the same with 1.1K and
130K vertices.

### Speed: native kernels where the time went (2026-10-02)

Profiles of the deep stack (40 layers, 1K, Vulkan) and of single calls located the CPU time
that native code could take. Each kernel has SSE4.2 and NEON paths, a scalar path, tests
against the code it replaces and mutation checks. The NEON paths are also tested on x86-64
through SIMDe's emulated intrinsics (`docs/native/platforms.md`).

| What | Kernel | Before | After |
| --- | --- | ---: | ---: |
| Scalar channel readback (Vulkan, 1K) | `op_expand_compact`, R16F publication | 3.45 ms read | 0.98 ms read + 1.1 ms expand |
| Edits that publish scalar channels (Vulkan) | (same) | e.g. move layer 59 ms | 46 ms (10–24% across cases) |
| `projection.geometry()` at 1K | `op_uv_raster_interpolate` | 260 ms | 34 ms |
| Padding ownership at 1K | `op_uv_raster_ownership` | 110 ms | 8 ms |
| Export relief metric, tangent frames | `op_uv_raster_assign` | per-texel arrays | none |
| `export_padding.py` (4K export) | (all three rasters) | killed at 4.21 GiB | passes, 3.94 GiB |

What remains is not CPU arithmetic:
- GPU readback waits: each readback waits for the stack's GPU work, which takes 13 ms on
  the GTX 1050 during an undo.
- Blender's image upload: Blender rebuilds an image texture after its pixels change, 6.5 ms
  for a 1K float image.
- Python graph code: about 126 ms per undo under the profiler.

Hand-written assembly would not help: the compiler already emits the minimal instruction
sequence for these loops (see platforms.md).

### Step 8: the native scheduler on Vulkan and OpenGL

**Vulkan is a default host backend** (`lifecycle.host_backends()` was Metal and Vulkan;
OpenGL stayed opt-in until `6ae821b`, see "Undo and publication in place"). At this step
Vulkan published through pixels, on the belief that image textures cannot be render targets
(corrected since: see "Publication on Vulkan"; background Vulkan sessions still take this
route). Four native features were missing on that path and are now ported:

- **Suffix collapse:** it ran only on the native channel path, which pixel publication
  does not take.
- **Idle arming:** it is enabled once no channel waits for the publication budget.
- **Fused finals:** these failed for native programs, which start with helper functions.
- **Reentry guard:** `evaluate`, `close` and `adopt` refuse during an evaluation.

Pixel publication keeps its per-tick budget (about 13 ms per channel at 1K, 50 ms at 2K on
the GTX 1050), which keeps drags responsive. It has no revision transactions: channels can
land a tick apart, and a fault leaves the channels published before it. The native suites
check each publication model's own guarantees.

Medians from `tests/integration/native_deep_stack.py` (40 layers, 1024, `OP_PREARM=1`), two
alternating rounds, native scheduler against `OP_NATIVE_ENGINE=0`, in ms:

| Case | Vulkan native | Vulkan legacy | OpenGL native | OpenGL legacy |
| --- | ---: | ---: | ---: | ---: |
| Cold build (s) | 1.8 | 3.0–3.4 | 1.9–2.8 | 2.9–5.4 |
| Bottom leaf roughness | 11 | 36 | 7 | 28 |
| Middle leaf opacity | 63–67 | 129–132 | 11 | 69 |
| Middle mask black | 60–62 | 132–134 | 26–27 | 73 |
| Bottom folder opacity | 129–135 | 178–194 | 13 | 78 |
| Blend mode (structural) | 63–64 | 144–162 | 20–71 | 76–94 |
| Remove layer (structural) | 34–39 | 119–184 | 13–14 | 58–59 |
| Apply smart material | 206–319 | 328 | 114–124 | 190–192 |
| Undo (numeric) | 280–287 | 303–710 | 285–289 | 302–314 |

Undo medians are noisy on both schedulers: each series of three starts with a cold sample
of about 700 ms. The OpenGL columns were measured before the Vulkan port.

- **Vulkan, as the default:**
  - 24 of 25 native suites, every step 5 suite and the GPU matrix (at `--sampler-limit`
    16 and 8) pass.
  - `native_publication_4k.py` exceeds the approved 4 GB cap (4.46 GiB commit) and is not
    run on this laptop.
- **OpenGL** (`OP_NATIVE_HOST_BACKENDS=OPENGL`):
  - The same result, and the 4K suite is likewise not run.
  - The native scheduler doubles video memory (1.4 against 0.6 GiB).
  - The user kept OpenGL opt-in until that suite had run. It cannot run on this laptop (killed
    at 6.61 GiB under a 6 GB cap), and OpenGL became a default on Windows on the user's
    go-ahead to decide (`6ae821b`, "Undo and publication in place").

## Where things stand

### The native library

- `libopenpainter` is C17 with a C ABI. The current ABI is 13; the add-on manifest says
  0.14.1.
- The add-on loads it with ctypes. A release package also needs a compiled CPython binding
  (`_native.abi3.so` on macOS, `_native.pyd` on Windows) beside it, with SHA-256 hashes in
  a manifest. The loader rejects a bundled library without its binding.
- Development runs bypass the bundle: set `OP_LIBOPENPAINTER_PATH` (Blender) or
  `OP_TEST_NATIVE_LIBRARY` (unit tests) to a built library.

There are two gates (`src/open_painter/native_bridge/lifecycle.py`):

| Gate | What it enables | Where it runs today |
| --- | --- | --- |
| `cpu_library()` | CPU kernels: export pixels and padding, PNG filters, UV rasterize, brush coverage, surface reach, topology, seams, SHA-256 | Any platform and GPU backend once the library loads |
| `active_library()` / `execution_library(backend)` | The native GPU engine scheduler (incremental plans, suffix collapse, stamps that let undo re-render 28 units) | Metal and Vulkan. `OP_NATIVE_HOST_BACKENDS=OPENGL` opts OpenGL in, for qualification |

`src/core/simd.h` selects the vector paths:

| Target | Path | Selected by |
| --- | --- | --- |
| Windows x64, Linux x64, Intel Mac | SSE4.2 (Blender 4.2+ requires it) | `_M_X64` (MSVC, no flag needed); `__SSE4_2__` (CMake adds `-msse4.2` for GCC/Clang) |
| Windows on Arm | NEON, plus SHA2 | `_M_ARM64` (MSVC); clang-cl defines `__aarch64__` |
| macOS and Linux arm64 | NEON | `__aarch64__` |

### What has been verified

| Configuration | Result |
| --- | --- |
| macOS arm64 (Apple clang) | 33/33 C tests; unit tests; integration suites; Blender with Metal |
| x86-64 macOS build under Rosetta (SSE4.2, no AVX) | 33/33 C tests, including the exhaustive sRGB test |
| Linux arm64 and amd64, GCC 12 (`scripts/cross_test_native.py`) | 33/33, no warnings |
| Windows x64, MinGW-w64 GCC 12, tests under Wine | 33/33, no warnings |
| Windows x64, MSVC 14.44 (VS 2022 Build Tools), Release and scalar builds | 33/33, no warnings at `/W4` |
| Windows x64, unit tests with the MSVC library and its `_native.pyd` | 530 run, no failures |
| Windows x64, Blender 5.2.2, Vulkan and OpenGL | The step 5 suites with and without the library, the GPU matrix, the package install; see [progress](#progress-on-the-windows-laptop) |

Not verified anywhere yet:

- MSVC on ARM64 (step 9; the laptop is x64);
- the native GPU engine on OpenGL outside Windows (Linux, other GPUs): opt-in there;
- `native_publication_4k.py` on Windows (it needs more memory than this laptop allows).

The C code has no variable-length arrays (checked with `-Wvla`) and no GCC builtins. Two
global counters use `_InterlockedExchangeAdd64` under MSVC (`src/core/serial.h`), because
MSVC compiles `<stdatomic.h>` only behind `/experimental:c11atomics`.

### What the Mac work changed that runs on Windows too (Python, no library needed)

These are untested on Windows. Step 5 covers them.

- Undo reuses the units the undo left unchanged. It re-points them to the restored structs
  instead of compiling all of them again.
  - Code: `shader_graph.undo_index`, `_undo_structs`, `_follow_undo`.
  - Switches: `OP_VERIFY_UNDO=1` compares every reused unit with a fresh compile;
    `OP_UNDO_ADOPTION=0` turns reuse off.
- Image dependency epochs survive undo and redo (`dependencies.clear(undo=True)`).
- PNG export:
  - deflates 1 MiB segments in parallel threads, each segment primed with a dictionary;
  - applies row filters where a sample says they pay;
  - has a NumPy filter that gives the same bytes when the library is absent.
- Export also has native relief normals and SHA-256 hashing (the library fallbacks are
  NumPy and hashlib).

### Earlier Windows work in this repo (same laptop)

`docs/windows-gpu-performance.md` and `docs/windows-vulkan-round3.md`, `round4.md` and
`round5.md` cover earlier rounds:

- **Machine:** Windows 11 (22631), NVIDIA GTX 1050 with 4 GiB, driver 560.70, Blender
  5.2.2 LTS (`d13f752e3b9c`), Vulkan backend.
- **What changed:** they sped up the Python engine's Vulkan publication.
- **Tools:**
  - `scripts/benchmark_windows_viewport.py` for timings;
  - `scripts/run_gpu_compatibility.py` for the GPU regression matrix;
  - `scripts/collect_gate0_baseline.py` for the environment record.

  All three launch Blender themselves, so they work on Windows today.

Vulkan facts that matter (Blender 5.2):

- **Image textures:** an image's GPU texture takes clears, draws and mip-level attachments,
  so in a window the engine renders into it. Background sessions publish through pixel
  readback (`runtime.BACKGROUND_PIXELS`). `OP_ENGINE_PIXEL_PUBLISH=1` or `=0` forces
  either route on any backend.
- **Memory:** image textures count toward the process commit charge on Windows, and
  released GPU resources are destroyed only after a wait or a frame end
  (`Device.settle`).
- **Pixel writes:** `Image.pixels.foreach_set` frees the image's GPU textures.
- **Shader cache:** SPIR-V is cached on disk.
- **Publication budget:** about 20 ms per evaluation.
- **Suites to rerun** whenever publication changes: `engine_pixel_publish.py` and
  `resolution_switch.py`.

## Setup

```powershell
git clone -c core.autocrlf=false -c core.longpaths=true https://github.com/caspianmoon/open_painter.git
cd open_painter
git switch feat/native-c-engine
git switch -c feat/native-windows      # the Mac sessions keep working on feat/native-c-engine
$blender = 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe'
$py = 'C:\Program Files\Blender Foundation\Blender 5.2\5.2\python\bin\python.exe'   # has NumPy
```

- **Line endings:** keep `core.autocrlf=false`. The repo has no line-ending rules, and
  CRLF would change source digests and patch files. Git for Windows sets
  `core.autocrlf=true` system-wide, so an existing checkout has CRLF files: on a clean
  tree, `git config core.autocrlf false`, then `git rm -r --cached .` and
  `git reset --hard` rewrites them with LF.
- **Compiler:** install Visual Studio 2022 Build Tools with the Desktop C++ workload (MSVC
  v143, Windows SDK, CMake). Build from a *Developer PowerShell for VS 2022*, which puts
  `cl.exe` and `cmake` on PATH. On an ARM64 laptop, use the ARM64 native environment.
  Without the Windows SDK component, CMake fails at `LNK1181: cannot open input file
  'kernel32.lib'`; add `Microsoft.VisualStudio.Component.Windows11SDK.26100` from an
  elevated installer.
- **Binding (step 6):** a python.org CPython 3.11 or later x64 install provides the
  headers and `libs\python3.lib`; the module needs only the 3.11 limited API. Blender
  ships neither, but ships `python3.dll`. Ask the user before installing one.
- **Check the GPU backend** Blender uses (Preferences ▸ System, or
  `gpu.platform.backend_type_get()`). `--gpu-backend vulkan|opengl` forces one. Factory
  settings on this laptop select **OpenGL**, so pass `--gpu-backend vulkan` to
  `run_selected_tests.py`, `run_native_metal_tests.py` and `run_clean_install.py` (and
  `--backend vulkan` to `run_gpu_compatibility.py`) to test Vulkan.

## Steps

Do them in order. Each step says when it is done.

### 1. MSVC build and C tests

```powershell
& $py scripts/build_native.py
```

This configures `libopenpainter` with CMake (the Visual Studio generator), builds Release
and runs ctest. The library lands at `.blender-test\native\build\Release\openpainter.dll`.

- **Done when:** 33/33 C tests pass, with no MSVC warnings at `/W4`. Fix warnings rather
  than silencing them.
- **Likely trouble on ARM64:** NEON intrinsics MSVC may spell or support differently.
  Look at `vqtbl3q_u8` and the `uint8x16x3_t` initializer in `pixels.c`, the SHA2
  intrinsics in `sha256.c`, and `vrndnq_f32`/`vminnmq_f32`.
- **Timeouts:** a slow laptop may hit the 20 s per-test limit. Pass `--test-timeout 120`
  to `build_native.py` (it sets `-DOP_TEST_TIMEOUT`) rather than weakening a test. On the
  i7-8750H, `test_pixels` takes 41 s.
- Also run `& $py scripts/native_ctypes_spike.py --lib <dll>`, which CI ran on
  `windows-latest` (`.github/workflows/native-core.yml`; the Actions billing has been
  blocked, so CI has not been running).

### 2. Unit tests against the DLL

```powershell
$env:OP_TEST_NATIVE_LIBRARY = "$PWD\.blender-test\native\build\Release\openpainter.dll"
& $py -m unittest discover -s tests/unit -p "test_*.py"
```

- **Done when:** everything passes or skips for a stated platform reason. The Windows
  process-probe tests in `test_export_transaction.py` run here (the Mac skips them);
  they may need fixing.
- **Parity tests that matter:**
  - `test_native_pixels`: quantize, normal encoding, relief and PNG filters against
    NumPy;
  - `test_native_digest`: SHA-256 against hashlib;
  - `test_png_segments`: segmented, filtered PNG writing.

### 3. Re-establish the Windows baseline (no native library)

The branch has a lot of Python work since the last Windows round. Without
`OP_LIBOPENPAINTER_PATH`, the add-on runs its Python paths. Run the GPU matrix:

```powershell
& $py scripts/run_gpu_compatibility.py --blender $blender --backend vulkan --output .blender-test/windows-vulkan/validation-native-branch
```

- **Done when:** the matrix passes on Vulkan, or every failure is triaged and fixed (rule 1).
- The round documents above describe the earlier results to compare against.

### 4. Make the guarded test runners work on Windows

**Done.** `scripts/run_guarded.py` now runs on Windows with the same command line and
output. It starts the command suspended, puts it in a Job Object and resumes it, polls
the job's commit charge, and has the kernel limit the job to the cap plus 1 GiB. It also
reports the job's peak working set, process count and dedicated GPU memory.
`run_gpu_compatibility.py` and `run_clean_install.py` launch Blender through it too.
`--timeout-seconds` kills a run that is still going after that long, so a Blender that
hangs fails its suite instead of stalling the gate; `run_tests.py` passes 30 minutes
(`--timeout-minutes`).

The original instructions, kept for reference:

`scripts/run_guarded.py` was macOS-only: it used `libproc` for process footprints,
`vm_stat` for free memory and `fcntl` for its lock. `run_tests.py`,
`run_selected_tests.py` and `run_native_metal_tests.py` all launch Blender through it.

Port it, keeping its command line and its `GUARD_SUMMARY` / `GUARD_KILLED` output:

- **Free memory:** `GlobalMemoryStatusEx` (`ullAvailPhys`), via ctypes.
- **Footprint cap:** put Blender in a Job Object. Either have the kernel enforce
  `JOB_OBJECT_LIMIT_JOB_MEMORY`, or poll the job's memory usage and call
  `TerminateJobObject` at the cap. Add `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. Child
  processes join the job automatically.
- **VRAM:** a discrete GPU's dedicated memory is not in the job's commit charge, unlike the
  Mac footprint, which included GPU allocations. Say so in the summary.
- **Lock:** `msvcrt.locking` on a lock file, or an `O_EXCL` file.
- **Blender path:** every runner defaults `--blender` to the macOS path, so pass `--blender`.

Test the guard with a small Python child that allocates past a 1 GB cap. **Done when:**
`run_selected_tests.py --blender $blender shader_undo.py` runs and passes through the guard.

### 5. Blender suites on Windows, with and without the library

Run the suites below twice: once as they are, and once with
`$env:OP_LIBOPENPAINTER_PATH` set to the DLL, which turns the CPU kernels on. Add
`OP_VERIFY_STRUCTURE=1`, `OP_VERIFY_INCREMENTAL=1` and `OP_VERIFY_UNDO=1` for the undo and
patch suites.

- **Export:**
  - `production_export.py`, `export_target.py`, `export_view.py`;
  - `surface_relief_export.py`, `live_surface_relief.py`;
  - `export_safety.py`, `quality_baseline.py`.
- **Painting and geometry:**
  - `vector_strokes.py`, `stroke_parity.py`, `gpu_strokes.py`;
  - `paint_freeze.py`, `stroke_surface_bounds.py`;
  - `utility_maps.py`, `mesh_masks.py`, `evaluated_projection.py`;
  - `native_geometry_projection.py`, `decal_objects.py`;
  - `geometry_effects.py`, `geometry_sources.py`, `geometry_uses_memo.py`.
- **Undo and patches:**
  - `shader_undo.py`, `undo_leaf_lifetime.py`;
  - `layer_list_ops.py`, `layer_store.py`, `layer_cache.py`;
  - `structural_patch.py`, `moved_layer_adoption.py`, `shader_adoption.py`;
  - `surface_filters.py`, `engine_filters.py`.
- **Publication:** `engine_pixel_publish.py`, `resolution_switch.py`.

**Done when:** all pass in both runs. Windows-only failures are product bugs to fix, for
example a file still open while it is deleted or renamed in the export transaction.

Some suites assume Metal and belong to step 8: those in `run_native_metal_tests.TESTS`,
starting with `native_host_metal.py`, which asserts the execution backend
`c17-host-metal`.

### 6. The CPython binding (`_native.pyd`)

A release package needs it, and it is the faster GPU submission path for the native
engine.

- **CMake (`libopenpainter/CMakeLists.txt`):**
  - Replace the `WIN32` `FATAL_ERROR` in the `OP_PYTHON_INCLUDE` block.
  - Link `python3.lib` (the module uses `Py_LIMITED_API 0x030b0000`).
  - Set `SUFFIX ".pyd"`, keeping `PREFIX ""` and `OUTPUT_NAME "_native"`.
- **Check Blender's DLLs** in `5.2\python\bin`. A stable-ABI module imports
  `python3.dll`; if Blender does not ship it, link the versioned library instead and record
  why.
- **Build:** `& $py scripts/build_native.py --python-include <CPython 3.13>\include`.

**Done when:** in Blender, `load_library` loads the binding (`module.abi()` returns 13),
`_op_binding` is set, and the native diagnostics (`lifecycle.diagnostics()`) say
`compiled_binding: True`.

### 7. Package and install in an isolated profile

```powershell
& $py scripts/build_native.py --python-include <...> --package dist/open_painter-windows-x64.zip
```

`build_extension.py` bundles the library and binding under
`native_bridge/bin/windows-amd64/`, which is the name the loader looks for. It writes
their SHA-256 hashes into the manifest and sets the manifest's `platforms`.

Install only into an isolated profile, never the artist's real one:
`run_tests.py --package <zip>`, or `scripts/run_clean_install.py --package <zip> --blender
$blender`.

**Done when:** the clean install loads the bundled library and binding without
environment variables, and the suites in step 5 pass from the installed package.

### 8. Qualify the native GPU engine on Vulkan (then OpenGL)

Set `$env:OP_NATIVE_HOST_BACKENDS = 'VULKAN'`. `execution_library` then selects the native
scheduler on Vulkan, and the engine reports `c17-host-vulkan`.

1. **Generalize Metal-specific assertions:** the tests below expect `c17-host-metal`;
   make them expect `c17-host-<backend>`.
   - `native_host_metal.py`, `native_paint_reopen.py`, `native_update_latency.py`;
   - `native_sustained_ui.py`, `vector_brush_ui.py`.
2. **Run the suites:**
   - the suites in `run_native_metal_tests.TESTS` (pass `--blender`);
   - the undo and patch suites, with all three `OP_VERIFY_*` switches.
3. **Watch for:**
   - pixel publication: native revisions are skipped when `dev.pixel_publish` is set,
     which is always the case on Vulkan;
   - R/RG readback;
   - the binding's feedback-hazard checks on Vulkan;
   - the sampler limit (`run_gpu_compatibility.py --sampler-limit`).
4. **Measure** with the deep-stack benchmark, small first, comparing with
   `OP_NATIVE_ENGINE=0`. It needs `OP_DEEP_REPORT`, plus `OP_DEEP_LAYERS=40`,
   `OP_DEEP_RES=1024` and `OP_PREARM=1`. See how `benchmark_windows_viewport.py` launches
   Blender on Vulkan. Ask the user before going above 40 layers or 1K.

**Done when:** every engine suite passes on Vulkan and the benchmark shows the native
scheduler is not slower. Then:

- add `VULKAN` to the default `host_backends()`;
- record the evidence in `docs/native/supported-matrix.md` and `docs/native/platforms.md`;
- repeat the whole step for OpenGL. The engine already special-cases OpenGL uniforms
  (`_has_uniform` in `runtime.py`).

### 9. Windows on Arm (only on an ARM64 machine)

Repeat steps 1, 2 and 6 in the ARM64 native environment. The NEON paths, SHA2 included,
compile under `_M_ARM64`.

### 10. The full gate

`run_tests.py --keep-going`, then `--gui-only` and `--gpu-only`, one after another, never
overlapping. Background takes about 35 minutes on the Mac. **Done when:** zero failures.

**Result on the laptop (2026-10-02, `e2cea49` plus the GUI test fixes of `e7702ca`)**, with
the release library and the package built at that commit:

- **Background:** 178 suites, `compatibility_contract.py` against a file built and recorded
  from `v0.12.2`, and 536 unit tests in Blender. Everything passes on OpenGL and on Vulkan.
  - `export_padding.py` (4K export) now fits the 4 GB cap: 3.93 GiB.
  - Four artist-file checks skip: the artist's own `open painter test.blend` is not on
    this laptop.
- **GPU:** all six real-window suites pass on both backends, scalar and mask parity at the
  full 2048 included.
- **GUI:** every suite passes on both backends except two. `material_painting_ui.py` and
  `quality_baseline_paint_ui.py` build the production prop at 2048 (three sets of 25
  layers), and pass the approved 6 GB cap within 13 s on both backends (6.0 and 6.8 GiB,
  the GPU driver's commit included). As agreed, they are reported, not retried higher.
  `material_painting_ui.py` passes at 1K (`OP_MATERIAL_UI_RESOLUTION=1024`); the baseline
  recorder has no size switch, since its baseline is defined at 2048.
- **Four GUI suites needed Windows fixes** (`e7702ca`):
  - `edge_map_refresh_ui.py` used a rounded fixture that the Cycles Bevel method reads as
    faint, and its stall limit assumed a fast bake device;
  - `shader_preview_ui.py` and `shader_snapshot_ui.py` assumed framebuffer publication;
  - `launch_audit_ui.py` ran into Blender's `introspect()` failing on Windows paths.
- `authoring_ui.py`'s exit crash (findings above) did not recur.

**Again after the speed work (2026-10-02, `6ae821b`, OpenGL scheduling natively)**, with the
release library and the package rebuilt at that commit and `material_painting_ui.py` at 1K:

- **Background:** everything passes on both backends: 180 suites, the 0.12.2 contract and
  539 unit tests in Blender (58 skips: no native build supplied to the unit tests, no system
  libpng, POSIX launchers).
  - The heaviest runs against the 4 GB cap: `export_padding.py` 3.92 GiB on both;
    `material_painting.py` 3.85 GiB on OpenGL (3.73 with the Python scheduler) and 3.25 on
    Vulkan, whose background sessions publish through pixels; `resolution_switch.py` 3.02
    and 2.12 GiB. OpenGL's `material_painting.py` has the least margin.
- **GPU:** all six real-window suites pass on both backends.
- **GUI:** 37 of 38 suites pass on each backend, `material_painting_ui.py` at 1K included
  (4.78 GiB on OpenGL, 4.14 on Vulkan). `quality_baseline_paint_ui.py` builds the production
  prop at 2048 and was killed under the 6 GB cap, at 6.38 GiB on OpenGL (6.61 before) and
  6.02 GiB on Vulkan, 11–13 s into its first evaluation. The runner stops at the first
  failure, so the two window checks after it ran separately (`--gui-start-at` with a name
  no suite has): `gui_workflow.py` and `native_regions_ui.py` pass on both backends.
- **Where that memory goes** (`material_painting_ui.py` at 1K on OpenGL, sampled every
  0.5 s; peak 4.66 GiB): the engine's textures are about 0.5 GiB (386–433 MiB leased, of
  which 280 MiB are retained results, and 74 MiB of mip scratch), the images' CPU buffers
  0.4 GiB. The rest is Blender's: the commit charge rises from 2.8 to 4.0 GiB in the 0.6 s
  of the viewport's first Material Preview draw, while the engine's textures grow by 64 MiB.
  At 2K the 0.4 GiB OpenGL lacks would have to come out of the result budgets that make
  edits fast, so the suite stays reported, not tuned to the cap.

**Again at `869a2ce`** (image epochs move for content updates only; the package rebuilt at
that commit), with the same result on both backends: background (180 suites, the 0.12.2
contract, 542 unit tests with `test_image_epochs.py`), GPU and the window checks after the
GUI loop pass; GUI passes 37 of 38, `quality_baseline_paint_ui.py` again killed under the
6 GB cap (OpenGL 6.30 GiB, Vulkan 6.09 GiB). A false alarm stopped the first OpenGL
run after 154 suites, all passing (`master_controls.py` takes about 940 s on every backend:
its CPU reference evaluations, forced, of fifty-four materials); the run resumed at the
next suite (`--start-at`).

## Reference

### Environment switches

| Variable | Effect |
| --- | --- |
| `OP_LIBOPENPAINTER_PATH` | Blender loads this library instead of the bundle (no binding needed) |
| `OP_TEST_NATIVE_LIBRARY` | Library for the unit tests |
| `OP_NATIVE_ENGINE=0` | No native library use at all (CPU kernels and engine) |
| `OP_NATIVE_HOST_BACKENDS` | Extra GPU backends for the native engine scheduler (`OPENGL` outside Windows; Metal, Vulkan and Windows' OpenGL are defaults) |
| `OP_TAG_IMAGES=1` | A publication tags its images instead of the set's material (comparisons; undo then rebuilds the output textures) |
| `OP_VERIFY_STRUCTURE=1` | Compare every incremental plan with a full one |
| `OP_VERIFY_INCREMENTAL=1` | Compare incremental native descriptions and stamps, rebound programs and adoption with full ones |
| `OP_VERIFY_UNDO=1` | Compare every unit an undo reused with a fresh compile |
| `OP_UNDO_ADOPTION=0` | Compile every unit after an undo (the old path) |
| `OP_ENGINE_PIXEL_PUBLISH=1` / `=0` | Force publication through pixels / into the images' GPU textures, on any backend (background Vulkan defaults to pixels) |
| `OP_PREARM=1` | Idle suffix arming in background Blender (benchmarks) |
| `OP_DEEP_*` | Deep-stack benchmark options (see `tests/integration/native_deep_stack.py`) |
| CMake `-DOP_TEST_TIMEOUT=<s>` | Seconds per C test (default 20) |
| CMake `-DOP_SCALAR_<kernel>` | Build a kernel's scalar path (`PIXELS`, `COVERAGE`, `RASTER`, `SHA256`) |

### Test-runner facts

- Unit tests need NumPy, so use Blender's bundled Python.
- Run Blender suites one at a time.
- Suites that end with `bpy.app.timers.register(...)` (most `director_*.py`) do nothing
  in `--background` and pass vacuously. Run them in a real window, as `run_tests.py
  --gui-only` does.
- In background Blender, a failing `bpy.ops` call raises `RuntimeError`.
- RNA references to layers go stale after `stack.add()` and after undo. Re-resolve them by
  id.
- A suite can need things beyond the add-on before its failures mean anything:
  - the local artist file;
  - the Material Director's Codex CLI;
  - an isolated user profile (the runners create it with `scripts/blender_test_profile.py`).
- **Heavy on the Mac (memory):**
  - `native_publication_4k.py`, `resolution_switch.py`, `mask_groups.py`;
  - `artist_file_*.py` with the local artist file;
  - `auto_edge_rebake.py`, `bake_back.py`, `bake_corrections.py`, `geometric_curvature.py`
    (Cycles kernel compilation).

  Run them only with the user's go-ahead and a raised cap.

### Documents

- `docs/native/platforms.md`: the platform design behind this plan.
- `docs/native/deep-stacks.md`: how the engine works, the performance history and the
  open work. It covers undo, export kernels and PNG compression.
- `docs/native/supported-matrix.md`, `migration-status.md`, `bridge-decision.md`: what C owns
  and the qualification record. They still say ABI 11, but the library is ABI 13.
- `TESTING.md`, plus the Windows round documents above.

### Not for this machine unless the user asks

The remaining macOS-side performance work:

- opening a deep file (3.3 s);
- applying a smart material (~90 ms);
- the first undo after a build (~0.4 s);
- collapse inside isolated folders.

Kernel arithmetic changes belong on `feat/native-c-engine` with both vector paths updated.
