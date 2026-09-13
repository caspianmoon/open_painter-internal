# Live procedural texture preview

This describes the initial live backend at `df32483`. See the newer
[latency follow-up](live-preview-latency.md) for current measurements and GPU
source/mask acceleration. Timings below are historical, not rerun results. The
512² offscreen benchmark completes all viewport samples; it does not measure
the first native interactive frame.

Open Painter now separates its live display from final texture output. The
editable layer stack remains the source of truth. Color, roughness, opacity and
blend edits update a GPU texture compositor instead of composing and uploading
entire output images through Python. The existing display material remains 14
nodes on the measured fixture; no procedural material shader stack is generated.

## What actually caused the delay

Ordinary edits already used `image_manager.write(..., pack=False)`. They did not
save textures to disk. They still ran CPU composition and a full RGBA pixel
transfer. The prior isolated measurement of commit
`00ccf773bae22ee5f82cf787de4c7dc0ca5bca9e` reproduced the committed Worn Painted
Metal 2048² fixture: 17.748 seconds cold and 182.981 ms for a warm roughness edit,
including a 64 MiB output write. Packing was zero in both workloads. This was one
sample recorded for that earlier change, not a new measurement here, and excludes a rendered viewport.
Raw evidence: [CPU baseline](performance/live-cpu-before-00ccf77.json).

## Implementation

- `live_inputs.py` captures an immutable recipe and computes dependency
  signatures before resolving textures. Unchanged channels do no leaf work,
  including when their disposable inputs have been evicted.
- Constant fills and procedural color/roughness/height modulation use shader
  parameters. Exact CPU procedural fields are shared by channels. Masks,
  transformed texture sources, normal procedurals and filters retain their
  existing numerical implementations as cached texture inputs. A change to one
  input regenerates that input rather than every final channel image.
- `live_gpu.py` composes float32 texture inputs using all 28 existing blend enum
  values, per-channel opacity, premultiplied painting, isolated folders and
  passthrough folders. One shader program serves subsequent edits. Intermediate
  textures are reused by nesting depth; outputs are not retained per blend node.
- `live_display.py` publishes through RGBA32F textures shared with disposable
  Blender Images. Mipmaps are updated on the GPU. EEVEE and the 2D Image Editor
  see these images, while native paint canvases remain editable source images.
  Warm edits perform neither a GPU readback nor an `Image.pixels` write.
- Native float paint can use Blender's updated GPU canvas directly. Its live
  stroke notification skips full-image CPU scans when no source filters require
  those pixels. Complex painted masks/filtered paint keep the existing detector.
- The scheduler distinguishes **LIVE** from authoritative **READY**. It does not
  start a full CPU evaluation merely because the mouse stopped moving. Output
  dirty flags stay set until explicitly evaluated.

The native Texture Set panel exposes **Evaluation: Live GPU / CPU Reference**.
Live GPU is the default when a window context and compatible resources are
available. The existing **Preview** setting remains explicit: Full preserves
authored resolution; 512/1024 use disposable previews without resizing authored
textures or reducing export precision. Headless evaluation remains CPU.

## Persistence and recovery

Save, render, explicit Refresh and export evaluate the latest complete recipe
through the original CPU float32 reference. Final images are packed for save;
disposable GPU views never become the project's saved texture data. Export
reads completed CPU output, including edits made after the last live update.
Version-1 and version-2 `.openpaintmaterial` compatibility is unchanged.

Runtime identities include scene and texture-set ID. Deleted sets, load,
undo/redo and unregister release disposable state. A shared material with
different scene/set owners uses CPU fallback. GPU failures preserve the last
completed live view while CPU recovery works; they do not mark pending output
clean. A subsequent edit or retry can attempt live evaluation again. Device
shutdown unbinds the shader before releasing it, avoiding a verified native
Blender crash on later GPU allocation.

## Measurements and verification

The [completed-frame report](performance/live-viewport-2k.json) measures the
committed Worn Painted Metal recipe on an assigned cube: eight 2048² channels,
float32 authoring and display textures, a 512² EEVEE viewport, Blender 5.2.1 LTS
`9e2066aef7ef`, Metal, Apple M3 Pro / 18 GiB. Each measured edit ends with an
actual framebuffer readback fence. CPU was measured first, GPU second, with
three warm edits in each phase. Another Blender process was present and its
activity was not controlled; this is not a broad cross-machine performance claim.

| Work | CPU reference | Live GPU |
| --- | ---: | ---: |
| Warm edit preparation/submission, median | 200.90 ms | 5.97 ms |
| Warm edit through completed EEVEE framebuffer, median | 296.89 ms | 138.14 ms |
| Cold preparation through completed framebuffer | 20.60 s | 16.81 s |
| Managed output transfer per warm roughness edit | 64 MiB | 0 |

Completed-frame improvement was **2.15×**. The 5.97 ms number is add-on work,
not total frame latency. The test does not measure physical display presentation
or tablet-to-screen latency. The result does **not** establish 60 FPS.

[GPU math regressions](performance/live-gpu-math.json) compare all blend modes,
groups and normal math against the CPU reference; maximum absolute error was
`2.563e-6`. Mip contents were independently sampled with `textureLod`, because
the tested Metal API misreported nonzero mip levels through framebuffer reads
and `texelFetch`. [Display capability evidence](performance/live-display-capability.json)
and [whole-recipe workflow validation](performance/live-preview-validation.json)
record the actual checks. The separate
[2K eight-channel parity run](performance/live-parity-2k.json) passed with maximum
absolute error `1.193e-7`. The [validation inventory](performance/live-preview-validation-inventory.json)
lists exact commands, results and changed files. GPU tests refuse headless execution.

Reproduction commands, from the repository root:

```sh
python3 scripts/build_extension.py --output /private/tmp/open-painter-live-preview.zip
python3 scripts/run_tests.py --blender /Applications/Blender.app/Contents/MacOS/Blender --package /private/tmp/open-painter-live-preview.zip
python3 scripts/run_tests.py --blender /Applications/Blender.app/Contents/MacOS/Blender --gui-only
python3 scripts/run_tests.py --blender /Applications/Blender.app/Contents/MacOS/Blender --gpu-only
/Applications/Blender.app/Contents/Resources/5.2/python/bin/python3.13 scripts/run_pure_tests.py
BLENDER_USER_RESOURCES=/private/tmp/op-live-viewport-user /Applications/Blender.app/Contents/MacOS/Blender --factory-startup --python tests/performance/live_viewport.py -- --report /private/tmp/op-live-viewport.json -ApplePersistenceIgnoreState YES
```

The test runner validates fresh result files and restores its generated example
fixtures. Run the GUI and background runners sequentially. The benchmark report
contains the exact command used, source digest, raw samples and timestamps.

## Limits

- Cold source generation and changes to complex generators/filters still use
  CPU computations. Initial material preparation is not instantaneous. Reference
  masks currently use conservative authoring-revision dependencies.
- Display Images also own unused CPU buffers. Live GPU leaf caching is bounded
  separately at 256 MiB. Preferences provide a default 4096 MiB preflight
  allowance for additional live displays, composition scratch and inputs across
  sets. Oversized work falls back to CPU with a reason; it is never silently
  downsampled. This is not a total-process memory cap and excludes authored
  images, CPU evaluator workspaces and driver allocations.
- Full 8K live workloads exceed the default allowance. Select an explicit
  smaller Preview or raise the resource allowance only when appropriate for the
  machine. 4K/8K performance, physical tablets, and other GPU/OS combinations
  were not certified by this run.
- This change keeps existing noises, mask algorithms, materials and texture
  format semantics. It does not substitute Blender shader noises for authored
  texture effects or claim pixel identity with Adobe's proprietary evaluator.
