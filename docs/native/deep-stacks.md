# Deep stacks: suffix collapse and incremental evaluation (ABI 12)

The native Metal path made each GPU pass cheaper, but two costs still grew with
the stack.

- **The layers above an edit.** Editing a layer re-rendered every layer above it
  on every tick.
- **Host work per tick.** Each tick re-derived state for every unit of the stack,
  and each property callback scanned the whole stack.

A 201-layer stack of smart materials at 2K (36 folders, 141 masks) spent
200–330 ms per slider tick. This page describes what replaced that work and what
it measures now.

## Affine suffix collapse

For some blend modes, a layer composed over an opaque backdrop is a per-texel
affine function of that backdrop: `out = A * x + B`, per component. These modes
are NORMAL, DISABLE, MULTIPLY, ADD, SUBTRACT, EXCLUSION, SCREEN and HARD_LIGHT.
A run of such layers composes into one affine map. While only the layers below a
run change, one pass reproduces the run's result from its input.

### What C owns

- **Classification.** `op_ir_backdrop_affine` classifies a unit's IR by
  following every path from the backdrop fetch to the output. Only linear
  conversions, component access and opaque-backdrop blends in the modes above
  are allowed on those paths. It returns, for each output component, which
  backdrop component that component reads. `test_affine` checks numerically that
  the whitelist is exactly affine and that every excluded separable mode is not.
- **Composition and local stamps.** `op_graph_set_composition` records each
  node's backdrop edge, its grouped edge and its class. Evaluation then also
  derives each node's local stamp: everything the node reads except its
  backdrop. Execution records the local stamp each node was rendered with.
- **Run planning.** `op_graph_suffix_runs` walks the stale composition tree
  below a channel's root. It reports maximal chains of affine nodes that are
  stale only because their backdrop changed, meaning their local stamps equal
  the ones they were rendered with. Each run's key is derived from its members'
  local stamps.

### What the host does

`evaluation/engine/suffix.py` arms a run when the same run key repeats on
consecutive evaluations. A one-off edit renders sequentially as before; a drag
or stroke arms on its second tick. Only evaluations that changed content plan
runs, so a forced re-render without edits stays sequential and bit-identical.

Arming evaluates each member's own compiled program twice, with constant
backdrops of 0 and 1. That gives the slope and offset of each member without
generating new shaders. A native kernel (`suffix.accumulate`) composes these
bottom-up in full float. Each later tick then materializes the run's top unit
with one `suffix.apply` pass and gives it its desired stamp. The ordinary
executor then treats the top unit as already rendered.

### Exactness

`tests/integration/native_suffix.py` drags layers at five depths of a mixed
stack. The stack includes smart-material folders, a passthrough folder,
non-affine barrier modes, procedural and filter masks, and every channel.

- **Full float:** collapsed results match sequential rendering to 1.8e-7.
- **Display precision:** the collapse is at least as close to the full-float
  result as sequential rendering is, because it skips the per-layer half-float
  stores. For example, BASE_COLOR is off by 4.9e-4 collapsed versus 1.4e-3
  sequential.

### Where it does not apply

These units render normally and split runs:

- non-affine blend modes;
- passthrough folder steps, which mix their backdrop with their children;
- units with a second output (published group composites);
- units whose spatial filter reads the backdrop;
- the non-opaque chains inside isolated folders.

Editing inside a smart-material folder therefore re-renders the rest of that
folder's own chain, then collapses everything above the folder.

### Memory

Transforms are recomputable caches under a per-set budget (1/32 of physical
memory, 128–512 MiB). At display precision they are stored in half floats;
Height and precise sets keep full float. Arming holds up to six full-float
scratch textures of the channel's layout while it runs.

## Incremental host evaluation

- **Touched units.** `EngineGraph.update()` records the units whose uniforms it
  wrote; bindings carry their unit.
- **Restamping only dirty units.** `NativeGraph.prepare` restamps only:
  - the touched units, including the graph node that compiles an inlined
    producer;
  - units whose sampled images changed identity (each image is inspected once
    per evaluation);
  - units reading object matrices or spatial filters;
  - surface readers, when the surface key changes.
- **Verification.** `OP_VERIFY_INCREMENTAL=1` compares every incremental
  evaluation with a complete restamp.
- **Stamp mirrors.** Unit states mirror their stamp and residency into
  native-order arrays through per-topology slots. `render`, `runs` and
  `checkpoint` pass those arrays to C without a Python walk.
- **Owner lookup.** Property callbacks find the edited struct's set and layer
  through `core/owners.py`: an address index holding positions and ids, verified
  on every hit.
- **Live-effect validation.** Validation reuses the live effects per compiled
  structure and visibility signature.
- **Smart materials.** Applying a smart material runs inside
  `layer_stack.replay`. Its hundreds of property writes skip the per-callback
  walks, and the set is invalidated and notified once.
- **Graph builds.** A build asks RNA for struct paths through one index built
  per build (`shader_graph.path_index`). `path_from_id` searches collections
  linearly, which made builds quadratic in the stack size.

## Measured results

Apple M3 Pro, 18 GB, Blender 5.2.1, background Blender with the native library.
Warm medians of 12–16 edits. Each timing covers the RNA edit, one session tick
and a one-pixel GPU fence. Viewport drawing, event delivery and scanout are
excluded. The baseline is `a04b994`, measured the same way without profiling.

201 layers, 2K, `tests/integration/native_deep_stack.py`:

| Edit | Baseline | Suffix collapse | + incremental host |
| --- | ---: | ---: | ---: |
| Top folder opacity | 46 ms | 50 ms | 11.6 ms |
| Bottom folder opacity (1,359 → 85 passes) | 203 ms | 71 ms | 15.8 ms |
| Middle leaf opacity | 262 ms | 76 ms | 15.2 ms |
| Bottom leaf roughness | 115 ms | 56 ms | 11.5 ms |
| Middle mask amount | 329 ms | 54 ms | 16.3 ms |

The first tick of a drag renders sequentially and the second arms. The p95 of
those runs (43–45 ms) comes from these ticks.

101 layers, 1K, from each structural edit until the set is current:

- Adding a top fill: 277 ms.
- Changing a middle layer's blend mode: 222 ms.
- Applying a smart material: 1.35 s → 0.53 s with the replay transaction.
- Authoring the 101-layer stack: 9.5 s → 3.0 s.
- Cold build: 2.4–3.0 s.

The 2K structural and cold figures above were not re-measured after the path
index: see below.

## Test and benchmark memory

A 200-layer 2K benchmark run exhausted the development machine. Background
Blender never runs the window manager's frame loop. On Metal, that loop calls
`GPU_render_step` and drains the autorelease pool, which is what releases GPU
objects the engine has freed. A long background run therefore retains every
texture it ever used.

- **Guard.** Every runner (`run_tests.py`, `run_native_metal_tests.py`,
  `run_selected_tests.py`) launches Blender through `scripts/run_guarded.py`. It
  checks available memory before starting and serializes runs. It kills the run
  when the process tree's physical footprint (GPU memory included) passes the
  cap, or when available system memory falls below a floor.
- **Frame emulation.** `native_deep_stack.py` emulates the frame boundary around
  each edit. This is test instrumentation only; the add-on never calls those
  functions.
- **Heavy suites.** `native_publication_4k.py` needs more than 7 GiB and
  `resolution_switch.py` more than 4 GiB. They were not run for this change on
  the 18 GB machine while other work was using its memory.

Engine budgets now scale with physical memory. Managed textures get a quarter of
it (1.5–4 GiB). Retained results and the free pool each get 1/32 (128–512 MiB).
Machines with 16 GB or more keep the previous limits.

## Open work

- **CPU boundaries.** Metric material mapping with images or procedural detail,
  mesh masks and generators still rasterize on the CPU at full resolution.
- **Isolated folders.** Chains inside isolated folders are not collapsed.
- **Structural edits.** Structural edits and cold builds still rebuild more
  host state than they change.
