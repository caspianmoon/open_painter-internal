# The Open Painter engine

Open Painter evaluates a texture set's layer stack itself, on the GPU, into the set's
channel images. Materials read those images through one node, **Open Painter Texture
Set** (Shader Editor ▸ Add ▸ Input). No Blender shader node tree is built for
evaluation, and no edit — a slider, a new mask, a reordered layer — changes a material.

## Why

Until this change the whole stack was compiled into one Blender material: every layer,
mask and filter became Math, Mix and Image nodes inside groups that Blender inlines into a
single fragment shader. Measured on an M3 Pro (Blender 5.2.1, 24 fill layers + Worn
Painted Metal, 5 643 material nodes in 241 node groups):

| Action | Material graph | Open Painter engine |
|---|---|---|
| Material | 5 643 nodes, 241 groups | 3 nodes (Texture Set node, Principled BSDF, Material Output) |
| First build of the set (512 px) | 16.1 s | 8.6 s (programs cached afterwards) |
| Add a mask from the Open Graph | 440–510 ms rebuild, 0.85–1.0 s UI freeze, full material recompile | 25–50 ms rebuild, one small program compiled once |
| One slider step (2K, 3 059 nodes) | ~80 ms: Blender re-derives the whole material | uniform update + GPU passes of the layers above |
| Noise-scale drag on a procedural mask (2K) | CPU raster at full resolution, seconds to settle | GPU, uniforms only |
| First build at 2K (8 layers + Worn Painted Metal) | 41.7 s (1 640 CPU raster strips) | 2.1 s |
| Three Noise masks from the Open Graph (2K, 8 layers) | CPU value-noise rasters (the legacy Noise source, measured without the material recompile): 0.9 s over 40 ticks, one 0.6 s tick | 0.22 s in one tick (first compile of the program included) |
| One paint tick on a 28-layer stack (2K, one channel) | — | 50–60 ms (canvas at the bottom), 30–38 ms (at the top), with a full readback |
| Undo, Worn Painted Metal (512) | persisted-tree rehydration | 0.25 s (compile 35 ms, render 190 ms) |

Every structural edit recompiled the entire material, every numeric edit made Blender
re-derive it (~25–30 µs per node), and legacy noise fields were NumPy rasters at full
resolution — work a fast GPU cannot help with.

## How it works

```
Layers panel / Open Graph ──edits──▶ layer stack (unchanged RNA)
                                         │
      evaluation/engine/compiler.py      │  the existing stack compiler (shader_graph.Graph)
      evaluation/engine/headless.py      │  building in-memory node trees, not Blender ones
                                         ▼
      evaluation/engine/glsl.py          one small GPU program per unit
      evaluation/engine/procedural_glsl.py   (catalog procedurals as GLSL)
                                         ▼
      evaluation/engine/runtime.py       texture-space passes, change stamps, caching,
                                         seam padding, publication with mip chain
                                         ▼
      ts.outputs[channel].image  ──▶  Open Painter Texture Set node  ──▶  Principled BSDF
```

**Compiler.** `EngineGraph` is the stack compiler that used to emit the material graph,
running on `headless` node objects that expose the part of the bpy node API it uses. Its
semantics are unchanged: units (a layer channel's blend, a channel's content, a mask
prefix), signatures and adoption of unchanged units on structural edits, parameter,
image, resource and cache bindings. `chains` records, per channel, the CHANNEL units bottom
to top with folders and their children — what the top-level material tree used to wire. A
Custom Graph mask's [Mask Graph](mask-graphs.md) is a unit of its own (`GRAPH`), compiled
from Open Painter's node types.

**Programs.** `glsl.translate` turns a unit into a fragment program that runs once per
texel of the set's texture space (`uv` at the texel centre; world position, normal and
tangent frame come from the rasters `painting.projection` computes for the CPU reference).
Node semantics are ports of Blender 5.2's GLSL (Math, Vector Math, implicit socket
conversions, Image Texture alpha handling); image sampling follows the CPU reference
(bilinear at texel centres, no mipmaps), so the engine matches the exported textures, not a
view-dependent material. Parameter values live in a uniform buffer and images are sampler
bindings, so a program depends only on structure: units with the same structure share one
compiled program, and a numeric edit never compiles anything. A content or mask unit read
by a single unit is compiled into that unit (no pass, no texture of its own). The consumer's
program is keyed by the trees compiled into it, so a mask or content unit rebuilt on its
own, for example when a mask's kind changes, recompiles the consumer that holds it. A Mask
Graph is never compiled into its consumer: it stays a pass of its own.

**Procedurals.** The procedural catalog (71 sources) and the weathering fields run in the
programs (`procedural_glsl`): the 32-bit lattice hash is reproduced bit for bit, seeds are
bound as two exact 16-bit halves (catalog materials use seeds around 1.2·10⁹, beyond float
precision), and each source follows its NumPy definition operation by operation. Only the
last bits of transcendental functions differ (`tests/integration/engine_procedurals.py`:
most sources within 10⁻⁵, the worst 5·10⁻⁴).

**Evaluation.** Each unit has a stamp: its program, parameter values, bound image
identities (including CPU leaf readiness and painted-canvas revisions) and the stamps of
what it reads. A channel whose stamp is unchanged is not rendered. A changed channel
renders its chain; retained content and mask results with matching stamps are reused, and
the result just below the lowest changed layer is kept as a checkpoint so repeated edits of
one layer start there. Intermediates are half floats except Height (bump detail is finer
than a half-float step).

**Publication.** The finished channel is padded across UV seams with the exporter's donor
map and written, with its mip chain, straight into the image's GPU texture (Metal and
OpenGL).

On Vulkan, Blender 5.2 creates image textures for sampling and storage, not as render targets,
and the Python API can neither write their mip levels nor regenerate them. So the result goes
through the image's pixel buffer (`runtime.PIXEL_PUBLISH`). Blender then rebuilds the image's
texture and mip chain on its next draw. Several things keep that affordable:

- **One read-back, no Python copy.** The finished channel is read back once into a buffer
  reused across publications (a 2K channel is 64 MB) and handed to Blender directly: no
  second target, no allocation, no copy in Python.
- **A time budget per evaluation** (`PIXEL_BUDGET_MS`, 20 ms). At least one channel is
  published, the channels waiting longest go first, and the rest are held for the next ticks.
  During a stroke or a slider drag the UI keeps drawing while channels catch up. The set is
  not current until every channel is out. Render, export and bakes flush every channel at once.
- **No second read-back.** Channels published this way already have their pixels, so a Cycles
  viewport, a save or a bake reads nothing back.
- **Compile once per machine.** Programs are generated deterministically, so Vulkan's on-disk
  SPIR-V cache compiles each one once per machine.

`tests/integration/engine_pixel_publish.py` runs this path on any backend. EEVEE and Workbench sample the GPU textures directly;
Cycles reads pixels on the CPU, so the results are copied into the images' pixel buffers
before renders and bakes and, while a 3D view renders with Cycles, as they are published.
The channel images of an engine-evaluated set are not packed on save: they are re-rendered
after the file opens (a CPU-mode set's outputs are its result and are packed).

**Node.** `core/texture_set_node.py` registers `OPShaderNodeTextureSet`, a
`ShaderNodeCustomGroup` (the one way an add-on can register a shader node type). One hidden
group per texture set holds the image samplers. The outputs are:

- Base Color, with AO in the preview;
- Metallic and Roughness;
- Normal: the normal map with the height as bump, ready for a BSDF;
- Height, Emission, AO and Alpha;
- the raw Normal Map colour and the UV Tangent.

A set's managed material is exactly that node, a Principled BSDF and a Material Output,
wired directly. Engine Preview's adapters, View Mask's snapshot and a CPU-reference display's
images live inside the node's group too, so no mode adds a node to the material.
`material_manager.repair` brings materials of earlier layouts back to the three nodes.

## What still runs on the CPU

- **CPU boundaries the engine inherits**: scene fields, mesh masks, vector strokes, micro
  details, runoff and drips generators, the legacy *Noise* mask source (a sine hash no GPU
  reproduces to the bit; the Open Graph's Noise masks use the catalog's value noise),
  metric material mapping, and spatial filters such as blur. They are cached by identity as before and sampled by the programs.
- **Custom Graph masks from earlier versions** that still point at a Blender shader group
  are evaluated by the CPU reference (a Cycles bake of the group). New Custom Graph masks
  use [Mask Graphs](mask-graphs.md), which the engine runs on the GPU.
- **Export** still evaluates with the CPU reference, which the engine matches
  (`tests/integration/engine_parity.py`).

## Measuring it

The engine times every update by stage, so each stage can be optimised on its own:

| Stage | What it measures |
| --- | --- |
| build | Units compiled into node trees (Python), after structural edits only; the report counts the units rebuilt and kept |
| update | Numeric edits written as uniforms |
| CPU inputs | Isolated rasters, geometry textures and the seam-padding table |
| translate | Node trees turned into GLSL (Python) |
| compile | GLSL compiled by the GPU driver |
| render | The units' passes, by kind; CPU submission time unless profiled |
| publish | Results written into the images, as framebuffer passes, or pixel read-back on Vulkan and for Cycles |

*Performance ▸ Profile Engine* renders every pass once more with each pass timed on the
GPU: five draws less one, each followed by a wait, so the wait's own cost cancels. It then
copies a plain-text report to the clipboard with:

- the machine: Blender, OS, GPU backend, vendor, renderer and driver;
- the last update's stages, with statistics over the recent updates;
- the heaviest passes, each labelled with its layer, channel, mask stack or Mask Graph;
- the cost per layer.

*Copy Engine Report* copies the same report without profiling. The code lives in
`evaluation/engine/stages.py`, and `tests/integration/engine_stages.py` covers it.

## Precision and parity

`tests/integration/engine_parity.py` evaluates fixture sets through the real session path
and compares every channel image with `LayerEvaluator` on the texels the set's UVs cover:
with full-float intermediates the difference is within 5·10⁻⁴ (the images are half floats
except Height), at display precision within 10⁻³.

## Limits

- Programs compile on first use (≈70–100 ms each on Metal); units with the same structure
  share one program, and Blender caches compiled programs.
- The engine needs a GPU: background Blender initialises the gpu module (`gpu.init`).
- Memory at 2K: the channel images (half float, Height full float) plus retained
  intermediates (bounded per set) — see `runtime.RETAIN_BYTES`.
- A file opened without Open Painter shows an undefined node where the texture set node was,
  and its channel images are blank until Open Painter renders them: share the textures with
  an export.
- Painting re-renders the layers above the painted one on every engine tick (from the kept
  result below it); on deep stacks at 4K that is the dominant cost of a stroke.
