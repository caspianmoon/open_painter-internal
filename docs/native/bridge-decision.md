# Metal integration decision — ABI 11

The implementation uses Blender's existing Metal device and public GPU API.
C owns shader generation, typed program/parameter storage, dependency and layer
plans, batch execution/dependency holds, channel selection and acceptance, all blend math, pointwise effects and mask folding, brush/geometry kernels, UV topology and seam connectivity,
resource policies and immutable material revisions.
A compiled CPython limited-ABI adapter submits GPU draws on Blender's context
thread. EEVEE samples the ordinary output images on the original object.

This continues the plan's host-device integration route. It does not create a
second device, import a foreign Metal texture, replace the renderer or open a
painting window. No Blender headers, RNA layouts, unexported symbols or private
GPU addresses are used. The portable library builds without Python or Blender.
See the exact [ownership map](migration-status.md).

## Host contract

- Blender creates and owns its graphics context, queue and public GPU objects.
- The C pool assigns opaque resource tokens; Python holds the corresponding
  public texture objects until native retirement policy releases them.
- The compiled adapter validates framebuffer dimensions and feedback hazards,
  binds public shaders/uniforms/samplers, draws, and exits the framebuffer context
  even on failure. The outer host state guard restores viewport/scissor/blend/depth.
- CPython buffers are copied before compilation and raster work release the GIL.
  Graphics submission stays on the owning context thread with the GIL held.
- Normal Metal updates perform no full-frame readback or image-pixel rewrite.
  Explicit export/render/save synchronization and compatibility source inputs
  remain separate boundaries with separate accounting.
- RGBA16F display storage is used where appropriate; height and precise work
  retain full float storage. Scalar intermediates retain their packed layouts.

## Publication and recovery

Changed channel sources are pinned until publication finishes when they fit the
retained-result allowance; larger source sets are staged sequentially. All allocations,
shader compilation and attachment validation precede visible writes. The C
transaction driver orders preflight, batched submission and native commit without
yielding; Python callbacks bind the public host resources. Base finishing writes
image and recovery storage together; mip generation groups up to three channels
per draw. Accepted mip chains are retained rather than regenerated on each edit.
A recoverable submission failure restores
every mip of every touched image from GPU snapshots (continuing restoration
after an individual failure) and leaves the accepted revision unchanged.
The regression injects failure after a visibly changed first channel and checks
all eight outputs before retrying.

Blender image eviction is detected through public GPU wrapper identity. The
accepted native resources restore the recreated image textures and all mip
levels before a viewport draw or during the normal session tick. The regression
frees all eight image textures and verifies every recovered mip.

This is ordered host-queue consumption, not an external semaphore contract or a
CPU GPU-completion fence. A device/context failure that also prevents rollback
is explicitly reported; broad device-loss recovery remains unqualified.

Load, undo and redo close native generations before rebinding restored recipes.
Unregister closes native plans/resources and removes the viewport recovery hook.
Libraries stay mapped; no manual unload races are introduced. The existing
durable stroke recipe supports immediate save and fresh-process reconstruction.

## Selection and qualification

The bundled dylib and compiled binding are SHA-256/ABI checked before use.
Metal selects `c17-host-metal`; `OP_NATIVE_ENGINE=0` selects the diagnostic legacy
path. Invalid/missing bundles preserve the existing evaluator. Windows/Vulkan
work is deferred as requested. The standalone `native/` application is unchanged.

The tested host is stock Blender 5.2.1 LTS (`9e2066aef7ef`), Apple M3 Pro, macOS
arm64. `fully_migrated` remains false while the documented host orchestration and
compatibility semantics remain. See [release evidence](release-evidence.md).

## Mip probe history

The original failed probe is preserved in `evidence/macos-metal-probe-2048.json`.
Its explicit-LOD `texelFetch` returned level-zero values on the tested Metal build.
A controlled texture with distinct solid colors at each mip reproduced that
sampler behavior. Normalized `textureLod` sampling fixed the diagnostic.

The corrected 2048 probe passed all eight channels and twelve mip levels with
maximum absolute error 0.00048828125. It is a component test, separate from the
real-window slider/brush evidence, and is not a scanout latency measurement.
