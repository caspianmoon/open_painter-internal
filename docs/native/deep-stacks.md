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
  local stamps only, not from node indices. An armed transform therefore
  survives graph rebuilds that keep its members, such as a structural edit
  elsewhere in the stack.

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

### Checkpoints along the spine

An edit composes over results it does not change. These are the result just below
the edited layer and the result below each folder enclosing it (`op_graph_spine`).
For each channel, the host keeps all of them for the lowest changed layer
(`op_graph_lowest_changed`). Previously only the first was kept. Every tick of an
edit inside a folder then re-rendered the stack below that folder, or collapsed
it from the channel's constant start.

### Idle arming

The first ticks of a new drag used to pay the sequential render and then the
arming. While a set is idle (0.3 s after its last evaluation), the session tick
prepares an edit of the selected layer in advance:

- It renders and keeps the layer's spine.
- It arms the runs an edit of the layer would produce. `op_graph_edit_runs`
  predicts them exactly, with the same members and keys the planner reports once
  the edit is evaluated.
- It arms one run per idle tick.
- A run is never armed twice for the same stack state. If the budget evicts a
  sibling, idle arming stops instead of cycling.

Idle arming is on with a UI. In background Blender it is off unless `OP_PREARM=1`
is set, because tests drive ticks by hand.

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
- **Geometry readers.** Whether a set reads its assigned geometry
  (`dependencies.uses_geometry`), and the structural part of whether it reads
  placement, are memoised per set on its edit serial. Numeric callbacks of layers,
  masks, channels and effects map their serial bump back to the serial before it
  (`numeric_edit`), so a drag keeps both answers. The exceptions:
  - A decal layer's own numeric fields are its projection frame, so they are not
    carried.
  - A units switch can leave every derived value, and so the serial, unchanged;
    it bumps a per-set generation instead.
  - A Mask Graph's own nodes are read on each call.
  - While a replay is open, its edits have neither callbacks nor a serial, so
    both are computed afresh.
- **Reader index.** The same memo holds where the stack's live-field masks, mesh
  masks and decal layers sit. The scheduler's refresh and the geometry key read
  them through it instead of walking the stack. The refresh covers name mirrors,
  decal images and Shift+D copies of decal Empties. Each indexed item is checked
  again on use, since Blender nulls a deleted Empty without a callback. If the
  layer count moved under an unchanged serial, the stack is walked afresh.
- **Reference readers.** Every property callback asks which channels read the
  edited layer (`references.dependent_channels`). The layers holding a reference
  mask, an anchored channel or a generator's detail input are indexed per
  structure. A stack with none answers without building the anchor registry or
  walking the stack. That walk was 3 ms of every 3.7 ms callback at 124
  smart-material layers.
- **Sampled images.** Headless nodes count every image or GPU resource
  assignment. While that count stands still, the image scan reads each distinct
  image once, not each of the set's image nodes (about 690 in a 200-layer
  smart-material stack, over about 32 images).
- **Structure passes.** Each structure pass (a plan, a structural signature, a
  build) memoizes, on its plan:
  - the per-layer answers that only structural edits change: children,
    skipped, touched and the channel lookup;
  - unit signatures;
  - the set-wide owner addresses.

  A build's final signature reuses the plan it was built from.

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

In these runs, idle arming was off. The first tick of a drag rendered
sequentially and the second armed. The p95 of those runs (43–45 ms) comes from
these ticks.

First ticks of a new drag, 101 layers at 1K. Idle arming ran to completion before
each drag, as it does in the UI while the layer is selected:

| Edit | Without idle arming (1st / 2nd tick) | With idle arming (1st / 2nd tick) |
| --- | ---: | ---: |
| Top folder opacity | 44.6 / 10.1 ms | 9.5 / 8.0 ms |
| Bottom folder opacity | 43.5 / 66.9 ms | 12.0 / 9.6 ms |
| Middle leaf opacity | 39.6 / 62.9 ms | 10.6 / 10.4 ms |
| Bottom leaf roughness | 19.4 / 23.3 ms | 8.2 / 5.5 ms |
| Mask black level | 43.6 / 51.7 ms | 9.0 / 8.6 ms |

101 layers, 1K, from each structural edit until the set is current. The edited
layer is selected, as in the UI, and idle arming is on. These are medians of
three edits on a shared machine, so they vary by about ±50 ms between runs.

| Edit | Before | After |
| --- | ---: | ---: |
| Adding a top fill | 277 ms | 251 ms |
| Removing a layer | 160 ms | 144 ms |
| Changing a middle layer's blend mode | 222 ms | 182 ms |
| Toggling a middle layer's visibility | 42 ms | 11 ms |
| Applying a smart material | 1,350 ms | 241 ms |
| Authoring the 101-layer stack | 9.5 s | 3.0 s |

The smart-material apply is also noisier than the rest: its first application
steps CPU resources over about 50 ticks. The cold build takes 2.4–3.0 s.

The 2K structural and cold figures above were not re-measured after the path
index: see below.

## Real viewport cadence

`tests/integration/native_drag_cadence.py` drags the product's opacity slider in a
real Blender window (`--enable-event-simulate`). It queues mouse moves at 120 Hz;
Blender delivered about 45 value changes per second. It counts value changes,
evaluations and POST_PIXEL draws, and measures from the first value change a
revision includes to its first draw. That is event queue to POST_PIXEL, not OS
input or scanout.

An earlier timer-driven run (`native_sustained_ui.py`) showed 12–16 drawn
revisions per second. That was the timer's phase against the draw loop. In a real
drag, every value change is evaluated and drawn in the same frame.

1K, M3 Pro. Before is `9fd1db2`:

| Stack | Revisions/s | Tick p50 | Evaluation p50 | Change → draw p50 |
| --- | ---: | ---: | ---: | ---: |
| Fixture (3 layers), before | 44.0 | 3.1 ms | 2.2 ms | 9.0 ms |
| +100 fills, before | 41.7 | 9.0 ms | 5.2 ms | 16.0 ms |
| +200 fills, before | 30.2 | 15.2 ms | 8.4 ms | 23.8 ms |
| +200 fills, after | 44.9 | 3.9 ms | 2.7 ms | 12.1 ms |
| +200 fills and a triplanar layer, before | 35.8 | 9.4 ms | 4.1 ms | 18.2 ms |
| +200 fills and a triplanar layer, after | 44.7 | 4.3 ms | 2.7 ms | 12.5 ms |

A repeat of the 200-fill pair on the same machine state gave 30.7/s, 15.1 ms and
8.4 ms before, and 44.9/s, 3.9 ms and 2.7 ms after.

At 200 layers, most of each scheduler tick before the change was two Python walks
over every channel, effect and mask (`uses_geometry`):
- one from `dependencies.refresh`;
- one from the engine's surface key, for the padding donor.

When nothing on the set reads geometry, each walk runs all six of its checks to
the end. A set that does read geometry stops at the first check that finds a
reader, but then rebuilds its geometry key.

The scheduler asks for both on every tick. Publishing the channel images causes
shading updates of the material, mesh and object, and the depsgraph handler
counts those as relevant. That is deliberate: a material rename produces exactly
the same updates, and mesh masks of kind MATERIAL key on material names. The
per-set memo makes both calls, and the geometry key's rebuild, independent of the
number of layers.

### The rest of the frame

A whole-process sample of Blender during a drag under 204 smart-material layers
(before the reader index) splits the main thread like this:

| Work | Share |
| --- | ---: |
| Viewport and UI drawing | 23% |
| Waiting for the next Metal drawable | 16% |
| Depsgraph: copying the Scene into its evaluated copy | 19% |
| The scheduler tick (engine evaluation and publication) | 17% |
| The slider's RNA update callback | 18% |

The Scene copy is Blender's, and it grows with the stack. After every edit of a
Python-defined property owned by the Scene, `rna_property_update` tags the
Scene for copy-on-evaluation. The next depsgraph evaluation then duplicates and
frees the Scene's system properties, which hold the whole Open Painter project.
That was about 5 ms per edit at 204 layers. Python properties cannot opt out
(`PROP_NO_DEG_UPDATE` is not exposed). Only storage that the depsgraph never
evaluates would avoid it: a datablock no scene references, or an ID type without
copy-on-evaluation.

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
  `resolution_switch.py` more than 4 GiB. `mask_groups.py` and
  `artist_file_edits.py` also exceed 5 GiB; the latter only with the local
  artist file present. `auto_edge_rebake.py` passes 4.5 GiB within seconds of
  its first Cycles bake. All five exceed these limits at the pre-change commit
  too, so this is not a regression. They were not run for this change on the
  18 GB machine while other work was using its memory.

Engine budgets now scale with physical memory. Managed textures get a quarter of
it (1.5–4 GiB). Retained results and the free pool each get 1/32 (128–512 MiB).
Machines with 16 GB or more keep the previous limits.

## Open work

- **CPU boundaries.** Metric material mapping with images or procedural detail,
  mesh masks and generators still rasterize on the CPU at full resolution.
- **Isolated folders.** Chains inside isolated folders are not collapsed.
- **Structural edits.** Structural edits and cold builds still rebuild more
  host state than they change.
- **Scene copies.** Each edit re-copies the project's properties into the
  evaluated Scene (see "The rest of the frame"). Removing that means moving the
  project's storage off the Scene, a data-model migration.
