# Deep stacks: suffix collapse and incremental evaluation (ABI 14)

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
folder's own chain, then collapses everything above the folder. Since `bfdeb2b`
that rest of the chain renders as one fused pass (`evaluation/engine/fusion.py`):
the members' own programs run in registers, rounded between them as their
half-float stores would round, so the result is bit-identical. Since `29e5226`
fusion also takes folder steps and the affine units no run folds; a fused pass
records its units as rendered with their local stamps (`op_graph_mark_rendered`,
ABI 14), as executing them would, so they stay run members. See the Windows plan,
"Drags render only what changes; fused chains".

Since 2026-10-10 a folder step fuses with the chain inside its folder: its group input
comes from a register like a backdrop (`fusion._link`), so an edit inside a smart material
renders the material's changed siblings and its folder step as one pass.

With images written in place, a channel's final renders inside its publication pass:
the collapsed run it tops applies its transform at the texel publication reads (the fused
chain that is the run's input evaluating there too), and a fused chain ending at it
evaluates there. The final's own pass, its texture and the
publication's read of it are saved, bit for bit (Windows plan, "The final renders in its
publication pass").

### Unchanged inputs stay resident

A result that a changed unit reads but that does not change itself is read again
on the next tick of the same drag: a smart material's inside under its folder
step, the layers below an edit, the fields they read. Since `bfdeb2b` such
results are kept (`Engine._update_frontier`) instead of being released after use
and rendered again on every tick; idle arming keeps the ones an edit of the
selected layer will read (`_prearm_reads`, `03a0174`). A mask edit mid-stack of
the 40-layer stack went from 58 units per tick to the 17 that change.

### Memory

Transforms are recomputable caches under a per-set budget (1/32 of physical
memory, 128–512 MiB). At display precision they are stored in half floats;
Height and precise sets keep full float. Arming holds up to six full-float
scratch textures of the channel's layout while it runs.

## Detail fields

A fill channel modulated by a triplanar detail image samples that image three times
per texel (one projection per axis). Compiled into the channel, those samples ran in
every pass of the channel (each edit of its colour, opacity, blend or masks) and in
every channel of the layer. `shader_graph.DETAIL_FIELDS` compiles the detail as a unit
of its own, `('FIELD', layer id, channel)`, exporting one full-float value. The
channel's content imports it. A FIELD's signature is what `detail` compiles from: the
projection and the sampler state. The image and the projection's numbers are bindings.

- **Which details.** Triplanar ones. Another projection is one sample, cheaper than a
  pass of its own. Normal's detail is a source boundary, and Material Mapping v1 and
  procedural details are compiled in place, as before.
- **Copies.** A layer's channels usually share the detail and its projection, so their
  fields hold the same texels. `Engine._copy_field` fills a field from another field
  that holds the same content: the same shader, the same parameter bits (float32, so
  0.0 and -0.0 differ) and the same image and surface identities. It copies texel for
  texel. Program stamps cannot decide this: a native program's stamp counts its own
  uniform revisions, which differ between units holding equal values.
  - A copied field is not retained. It is released once the units that read it have
    drawn (`_drop_copy`, also at the native batch's release of it), and it is copied
    again when one of them renders again.
  - The field it copied stays the holder. A holder rendered with other content, filled
    by a copy or retired is forgotten (`_forget_field`).
- **Exactness.** Published channels are bit-identical with fields off and with copies
  off (`engine_detail_fields.py`, and every channel of the 40-layer deep stack on both
  Windows backends).

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

### The product's sidebar

Every RNA edit also sends a window notifier, so each frame of a drag redraws the
sidebar. In the product's editor area (`editor.mount`, the Painter tab), the
layer list repeated two whole-stack jobs on every redraw:
- Its filter walked the hierarchy.
- The row badges rebuilt every layer's profile row, including the cache state of
  each layer from a full diagnostics read.

At 200 layers that was 3.6 ms per redraw, profiled.

- **List filter.** It is now memoised per structure and folder expansion.
  Expanding a folder has no callback, so the key reads each folder's flag.
- **Badges.** They read the recorded profile table only, and only once Profile
  Layers has run. Their key is the table and the stack's structure.
- **Cache icons.** Only a visible cached row asks for its state, from cache
  states read once per redraw.

The area's asset shelf is laid out again on each redraw too, and Blender polls
every asset through the shelf's Python `asset_poll`. For each library asset the
poll built its `full_library_path` and read its tags; that was a quarter of the
main thread. Both answers are fixed for an asset until the shelf or the library
refreshes, so `shelf_library.library_facts` remembers them per asset.

The product's editor area over 200 fills, 1K, with the same drag. Before is
`9fd1db2`:

| Build | Revisions/s | Tick p50 | Change → draw p50 | p95 |
| --- | ---: | ---: | ---: | ---: |
| Before | 23.2 | 15.1 ms | 33.6 ms | 37.2 ms |
| Memos, indexes, list filter and badges | 39.4 | 3.1 ms | 19.1 ms | 20.7 ms |
| And remembered shelf facts | 44.9 | 3.2 ms | 14.5 ms | 16.2 ms |

At 44.9 revisions/s, every value change the window delivered was drawn (45.0/s,
60 draws/s).

The same editor over 204 catalog smart-material layers:

| Build | Revisions/s | Tick p50 | Change → draw p50 |
| --- | ---: | ---: | ---: |
| Before (`9fd1db2`) | 25.8 | 6.1 ms | 28.4 ms |
| After | 34.0 | 4.4 ms | 21.3 ms |

Here too every delivered change was drawn. The window delivered only 34 changes
per second because the rest of its frame is longer; see below.

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

After the reader index, list filter and shelf facts, the same stack in the
product's editor splits the main thread as follows:

| Work | Share |
| --- | ---: |
| Viewport, sidebar and shelf drawing and layout | 43% |
| Depsgraph: copying the Scene | 18% |
| The scheduler tick | 16% |
| Waiting for the next Metal drawable | 14% |
| The slider's RNA update callback | 3% |

Within the drawing share:
- The sidebar's layout is 9%.
- The shelf is 11%. Blender calls the Python `asset_poll` once per asset, and it
  re-uploads each preview's pixels every time it draws.

The Scene copy is Blender's, and it grows with the stack. After every edit of a
Python-defined property owned by the Scene, `rna_property_update` tags the
Scene for copy-on-evaluation. The next depsgraph evaluation then duplicates and
frees the Scene's system properties, which hold the whole Open Painter project.
That was about 5 ms per edit at 204 layers. Python properties cannot opt out
(`PROP_NO_DEG_UPDATE` is not exposed). Only storage that the depsgraph never
evaluates would avoid it: a datablock no scene references, or an ID type without
copy-on-evaluation.

## Layer stores

Each texture set's layer stack now lives in a hidden Palette datablock
(`core/layer_store.py`), not on the Scene. Blender never copies a Palette for
evaluation. Every other part of a set stays on the Scene, so keyframes on set
settings keep their paths; an animated age is the supported case.

- **Access.** `OP_TextureSet.layers` is a Python property that reads the store.
  Code that reads or writes `ts.layers` is unchanged. The store's `layers` is an
  RNA collection, which list templates take as their data. The set remains the
  owner of `active_layer`.
- **Naming.** Stores are named `.OP Layers · <set id>`. Blender hides names that
  start with a dot from its datablock lists. New palettes get a fake user, which
  the store drops: the set's pointer is its only user, so a removed set's store
  goes with it.
- **The Scene of a struct.** A layer's structs have the store as their `id_data`.
  `layer_store.scene_of` and `project_of` map them back to their Scene, for
  property callbacks, the owner index and the preview's owner lookup.
  `scene_path` spells their RNA paths from the Scene, as before, because shader
  bindings, cache provenance and master controls key on those paths.
- **Old files.** A file written before the store keeps the stack on the set, as
  raw properties. On load, `ensure_all` moves it into a new store by a raw
  property copy: 2,000 layers with nested masks and object pointers take 8 ms.
- **Copied scenes.** A Full or Linked Copy of a Scene, or a Material Director
  candidate, points at the original's stores. Each scheduler tick checks for
  missing or shared stores. The set that held a store keeps it, and the others
  get copies, so the projects stay independent. A set whose store changed drops
  its preview sessions.
- **Snapshots.** Immutable evaluation snapshots of a set (`evaluation/snapshots`)
  take the stack explicitly, since it is no longer among the set's RNA
  properties.

With the stack in the store, an edit of a layer property plus the depsgraph
update takes 0.06 ms at 312 layers (`tests/integration/layer_store.py`). The
synthetic benchmark measured 19.9 ms with 150,000 properties on the Scene.

The drag in the product's editor over 204 smart-material layers (1K), as in "The
product's sidebar":

| Build | Revisions/s | Change → draw p50 | p95 | Depsgraph share of main thread |
| --- | ---: | ---: | ---: | ---: |
| `9fd1db2` | 25.8 | 28.4 ms | 30.5 ms | — |
| Before stores (`6d05fd1`) | 34.0 | 21.3 ms | 31.2 ms | 18% |
| Layer stores | 40.5 | 15.6 ms | 20.8 ms | 0.1% |

Every delivered change was drawn. The main thread is now mostly Blender's
drawing, about half, including 18% waiting for the next drawable. The scheduler
tick is 20%, the sidebar and shelf layout 16%, and the slider's callback 4%.

Layer properties can no longer be keyframed: a Palette holds no animation data.
Blender refuses the keyframe with a message. Before, the keyframe landed on the
Scene, but the engine never followed layer animation.

## Structural edits

A structural edit rebuilds the engine graph and adopts every unit whose structure
did not change. Examples are a blend mode, a mask source, or adding, removing or
moving a layer. Profiling the rebuild at 101 layers found four costs that grew
with the stack.

- **Reach walk.** `compiler._reverse_map` copied its recursion path at every
  step of chains as deep as the stack, which made it quadratic. It also read
  every node of every unit tree on each build. It is now a memoised walk, with
  each unit's dependency roles cached with its tree.
- **Repeated plan.** The property callback's structure check planned the stack,
  and the rebuild on the next tick planned it again. The rebuild now reuses the
  check's plan while the structure token is unchanged.
- **Chain scans.** `compiler._chain` walked the stack per folder and channel. It
  now reads the plan's children index.
- **Moved layers.** Inserting, removing or moving a layer, or the layers array
  growing, moves the struct of every layer above it. Each unit captured its
  layer's struct, so every unit of those layers was recompiled: about 370 of 877
  for an insert in the middle of 101 layers.

  Only the layer group itself moves; its channels, masks and effects live in
  arrays of their own. So an unchanged unit whose layer is the only owner that
  moved is re-pointed and adopted (`Graph._follow_moved_layer`). What it captured
  of the old struct is replaced by address: binding owners and their keys,
  resource and image closures (cells, defaults, nested closures) and static
  owner sets. If anything still holds the old address, the unit is recompiled
  as before.

  `tests/integration/moved_layer_adoption.py` inserts, removes and moves layers
  with CPU boundaries and text masks, edits the moved layers, and compares the
  result with a rebuild from scratch. With the re-pointing disabled, it fails
  (Base Color off by 0.057).
- **Selective restamp.** After a rebuild that keeps the native topology, only
  units with a new program, new constants, changed images or written uniforms
  are restamped. `OP_VERIFY_INCREMENTAL` checks that against a full restamp.

101 layers, 1K, real time from the edit until the set is current (medians of
three):

| Edit | Before | After |
| --- | ---: | ---: |
| Blend mode of a middle layer | 133 ms | 93 ms |
| Adding a fill in the middle | 315–432 ms | 148 ms |
| Removing it | 268–395 ms | 98 ms |
| Moving a layer one step | 104 ms | 95 ms |
| Removing the top layer | 134 ms | 93 ms |
| The first smart-material apply | 1.6–2.0 s | 0.95 s |

### Patching the graph in place

At 200 layers the graph holds about 1,750 units, and a blend mode or a mask edit
changes 4 to 20 of them. A rebuild still compiled a new graph and adopted every
unchanged unit into it one by one, the engine replaced its whole state table and
the native graph described every unit again.

- **`EngineGraph.patch`.** The session's own graph recompiles only its stale
  units: a changed signature, owners that moved (a collection of the layer was
  reallocated), a Mask Graph item that went away, or an importer whose producer's
  exports changed shape. Retired units hand their bindings back
  (`_unbind_units`); a compile that fails puts everything back and the rebuild
  runs as before. Inputs a structural edit can re-identify (an anchor another
  layer's CPU input reads, a cached subtree) are reconciled as adoption did;
  uniforms and image fields are read again only when edits were dropped while
  the rebuild was pending. The patch declines, changing nothing, when layers
  were added, removed or reordered, or the set's globals, resolution or baked
  maps changed.
- **Engine.** `Engine.adopt` of its own graph frees only the replaced states,
  their filter results and publication variants, keeps the inline set's identity
  and the positions (they follow the chains alone), and tells the native graph
  which units it replaced.
- **Native graph.** `NativeGraph._redescribe` describes and indexes only the
  replaced programs, the units their inlined producers compile into, and what a
  new topology changed (see "Stable native indices" below).

### Incremental structure checks

The callback's structure check planned all 200 layers and signed all 1,750
units. Now each graph keeps the plan its structure was made from and the layers
structurally edited since (`touch_layers`, fed by `notify`). The next plan
carries over every answer of the layers no edit can have reached (`Graph._reach`):

- the edited layer and its descendants (skip, cache and backdrop contexts come
  from above), and its ancestors on their own (a folder's wiring and cache state
  see their subtree);
- layers reading it or read by it, both through the keys their units imported
  and through what authoring names (`_layer_links`: anchor ids, reference
  layers, composite anchors), resolved or not. A reader of a disabled channel's
  anchor imports nothing, yet re-enabling the channel changes it;
- every reader of an OUTPUT or GROUP anchor: what it resolves to depends on the
  target's siblings;
- anything a re-planned layer reads now, until nothing more is reached.

A layer whose collection sizes differ from the previous plan is edited too (a
script edited a collection without a callback). Any layer order change, an
invalidated set or an edit whose layer is unknown plans every layer.
`OP_VERIFY_STRUCTURE=1` compares every incremental plan with a full one
(wiring, order, keys, contexts, every signature). It also runs the reference
check a patch skipped, because no re-planned layer takes part in references.
It found the disabled-anchor case above.

`tests/integration/structural_patch.py` applies fifteen structural edits,
checks that each patched the session's graph, and compares the result with a
rebuild from scratch. Every incremental restamp is verified too.

Blend mode of a middle layer, 200 layers at 1K, idle arming on as in the product
(`OP_PREARM=1`):

| Stage | Before | After |
| --- | ---: | ---: |
| Callback structure check | 37–47 ms | 5–8 ms |
| Graph rebuild or patch | 65–95 ms | about 8 ms |
| Engine adopt | 3–4 ms | 1–4 ms |
| Native prepare | 24–42 ms | 9–19 ms (translating the 4 new programs; a blend variant not compiled yet adds its Metal compile) |
| Total | 224–286 ms | 29–35 ms with the variant compiled, up to 57 ms without |

### Adding, removing and moving layers

A layer added, removed or moved shifts the layer structs above it in the stack's
array, and a growing array can move all of them. Each unit captured its layer's
struct, so the patch used to decline and the graph was rebuilt.

- **Plan.** Layers added, removed, or whose order among the others changed are
  edited layers for the incremental plan (`_reordered`). A change touching more
  than a quarter of the stack plans every layer.
- **Patch.** Each plan records where every layer's struct is. Units of a layer
  that moved since the previous plan are unbound, followed to the new address
  (`_follow_moved_layer`, as adoption does) and bound again; the patch only
  declines when the set or its baked maps moved. The reverse map is assembled
  again only for units that came, went, were replaced or reach other channels
  (`_reverse_changed`). CPU inputs are reconciled only when an edited or moved
  layer owns them.
- **Callbacks.** `layer_stack.add` keeps its callbacks quiet while the new layer is
  set up (they looked up their owner and notified the session about 25 times,
  and each missed owner rebuilt the project's owner index) and notifies the
  session once. An owner the index misses is found through its RNA path
  (`owners._by_path`) and only its layer is indexed again. `layer_stack.remove`,
  `move`, `remove_tree`, `move_above`, `place` and `parent` invalidate with the
  layers they changed. The session counts a serial bump as known only when a
  callback or such an invalidation reported it; any other bump plans every layer.
- **Native graph.** A new topology clears C's own stamps. Units whose program,
  constants, images and uniforms are unchanged resend their previous stamp
  instead of recomputing it.

`OP_VERIFY_STRUCTURE=1` also compares every patched dependency map with one
mapped from scratch. `structural_patch.py` inserts, removes and moves layers, and
adds layers until the array grows.

Further trims on the same path:

- A unit of a layer that only moved takes its new owner tuple from its old one
  (only the layer's address differs). Only the bindings that named the struct
  are registered again, in place.
- The native graph keeps each unit's program, imports and index facts (inlined
  producers, filters, surface reads, image nodes) while its program is the same.
  A new topology then describes only the units a patch replaced or whose inlined
  producers changed. `OP_VERIFY_INCREMENTAL=1` compares reused descriptions with
  `_state`.
- Plan memos are kept per layer, and a new plan shares the dicts of layers no edit
  reached instead of copying some 6,000 entries. The engine graph's structural
  signature is a dict. An incremental plan signs only the reached layers' keys;
  the rest are the settled structure's (verified).
- Edits dropped while a patch is pending record their owners. The patch reads
  those owners' bindings again (a moved layer's at its new address) instead of
  every binding. An edit whose owner is unknown still reads everything.
- A smart material's replay names the layers it created (`replay(created_only=True)`),
  so the structure check after it plans those alone.

**Armed runs across a new topology (ABI 13).** A suffix run member must have been
rendered with its current local stamp (`rendered[v] == local[v]`). `op_graph_replace`
used to free that record with the composition, and `op_graph_set_composition` kept
it only for nodes at the same index. Every layer added, removed or moved (and every
smart material) therefore lost all armed runs, and the layers above the edit
rendered one by one: adding a fill inside the bottom material of a small stack
rendered 88 units, 84 of them unchanged. Now `op_graph_replace` keeps the previous
composition, and `op_graph_set_composition_from` takes each node's previous index
(the native graph passes them). A node whose backdrop, child composite and
affinity map onto the same nodes keeps what C knew, so the run armed for an edit
is planned under the same key: 37 units rendered, three channels collapsed. At
200 layers, moving a layer went from 87 to 63 ms and applying a smart material
from 196 to 130 ms. `test_suffix.c` checks that the key survives an insertion.

200 layers at 1K, idle arming on:

| Edit | Before | After |
| --- | ---: | ---: |
| Adding a fill on top | 306 ms | 44 ms |
| Adding a fill in the middle | 325 ms | 57 ms |
| Removing the top layer | 220 ms | 39 ms |
| Removing a middle layer | 237 ms | 61 ms |
| Moving a middle layer | 222 ms | 38 ms |
| Applying a smart material (after the first) | 365 ms | 112 ms |

**Stable native indices.** The native graph numbered units in key order, so a unit
added or removed shifted the index of about half of them. Every topology change
then rebuilt every row of C's topology, every composition entry, every unit's
stamp slot and the whole image index, and walked every unit's description: 12 to
16 ms at 200 layers.

- **Indices stay.** A unit keeps its index while a channel reaches it; a new unit
  takes a free slot, and the arrays grow with headroom (`HEADROOM`, or an eighth).
  Rows, composition and own stamps are kept as index-space arrays. C still takes
  the whole topology, built from the rows at C speed (`array` and `itertools`).
  `op_graph_set_composition_from` then maps each kept unit to its own index and a
  reassigned slot to none.
- **Only what changed is described.** `_redescribe` describes the units a patch
  replaced, the units whose composition moved, and the consumers and former owners
  of producers whose inlining flipped. Reads are reference counted: a unit read for
  the first time is described, and a unit no channel reaches any more is released.
  A unit that was replaced, yet is still read, falls back to a complete
  description. Kept units resend their own stamps in one buffer.
- **Checks.** `OP_VERIFY_INCREMENTAL=1` compares each incremental description with
  a complete walk: units, reads, composition, programs, rows, reference counts and
  free slots. `tests/unit/test_native_graph_description.py` (Blender's Python)
  drives `prepare` through 660 steps of random structural edits with a fake engine that
  inlines as the real one does. It compares stamps, plans, rows and composition
  by unit key with a graph described from scratch, over both the binding and the
  ctypes route. `structural_patch.py` now requires every patched edit, layers
  added, removed and moved included, to avoid a complete description.

Three whole-stack passes of the patch also became cheaper:

- The chains come from the plan's wiring (`compiler._chains`), not from
  `_included` and `_wired` per layer and channel.
- Reach is a channel bitmask, with one shared frozenset per mask. Consumers and
  importers are kept up to date by adding and removing the units that changed.
- `native_bridge.stack.positions` packs the layer/folder nodes into an `array` and
  reads C's plan back through a memoryview instead of ctypes structs (7.6 to
  1.1 ms for 1,600 steps). `tests/unit/test_native_stack_plan.py` compares it with
  the host walk.

200 layers at 1K, idle arming on, medians of five:

| Edit | Before | After |
| --- | ---: | ---: |
| Adding a fill on top | 44–48 ms | 51 ms |
| Adding a fill in the middle | 57–58 ms | 44 ms |
| Removing the top layer | 39 ms | 30 ms |
| Removing a middle layer | 52–61 ms | 39 ms |
| Moving a middle layer | 38–50 ms | 34 ms |
| Blend mode of a middle layer | 34 ms | 28 ms |
| Applying a smart material (after the first) | 112–122 ms | 102 ms |

Native prepare went from 12–16 ms to 4–7 ms for these edits. Adding on top did not
improve: its first sample still pays a Metal compile.

The benchmark selects, and idle-arms, the layer each edit starts from: the one an
insertion goes above, the one removed or moved. In the UI that is the selected
layer, which idle arming prepares. A patch after a named invalidation (the stack's
remove, move, place) reads values again only for edits dropped while it was
pending: a catch-up update with no owner used to make it read all ~6,600 bindings.

### Making a patch proportional to its edit

Section timings (`OP_DEEP_PHASES=1 OP_DEEP_SECTIONS=1`, which wraps the patch and
native planning sections with wall-clock timers) showed about a dozen passes over
every unit left in each structural edit. Most of them now visit only what the edit
changed:

- **Adoption.** `EngineGraph.unit_changes` logs the units each patch added,
  replaced or removed. `Engine.adopt` of the patched graph retires and creates
  states for those alone, and updates the inline set from the producers they import
  now or imported before. `OP_VERIFY_STRUCTURE` compares the result with a full
  adoption.
- **Moved layers.** Binding keys name a layer by id (`_owner_token`) instead of by
  address. A layer whose struct moved therefore moves all its bindings in one step:
  a new owner struct and one `owner_bindings` entry. A unit decides once, when it
  compiles, whether any of its closures hold its layer's struct (`captures_layer`;
  image getters close over their channel, mask or effect). A unit whose closures
  don't only takes its new owner tuple. `OP_VERIFY_STRUCTURE` checks every binding
  is listed under its owner's current address.
- **Paths.** The path index spells a layer's structs from a shared index cell, so a
  moved layer updates its cell and its own address instead of indexing its ~30
  structs again. `graph_path_index.py` checks the spelled paths after an insert and
  a move.
- **Batches.** `authoring_batch` records the owners of the edits it coalesces. A
  smart material's replay made the patch read all ~7,000 bindings and 745 image
  fields again; it now reads the replay's owners, in one narrowed update rather
  than one update per owner. The patch no longer validates effects twice.
- **Liveness.** Validation walked every unit from each visible channel after any
  edit that changed what reads what. A patch now seeds the walk with the units that
  came, went or were replaced and the producers they import or imported
  (`_live_after_patch`). The cache is keyed by the hidden layers, so adding a layer
  no longer invalidates it.
- **Image groups.** Headless nodes stamp when they are assigned an image or a GPU
  resource. The native image groups hold while none of the indexed nodes changed, so
  a reindex reads only the entries new to it. Units compiled meanwhile no longer
  force a full rescan. `OP_VERIFY_INCREMENTAL` compares the groups with a complete
  scan.
- **Translation.** A program's translator no longer indexes every unit's tree (the
  index is kept on the graph while its units stand). Native IR nodes are packed
  with `struct` instead of ctypes objects.

200 layers at 1K, idle arming on, medians of five. These runs shared the machine
with a busy virtual machine, so they swing by up to a third:

| Edit | After stable indices | Now |
| --- | ---: | ---: |
| Adding a fill on top | 51 ms | 30–41 ms |
| Adding a fill in the middle | 44 ms | 30–36 ms |
| Removing the top layer | 30 ms | 26–27 ms |
| Removing a middle layer | 39 ms | 25–34 ms |
| Moving a middle layer | 34 ms | 24–26 ms |
| Blend mode of a middle layer | 28 ms | 26–30 ms (a first Metal compile of a blend variant adds 10–15 ms) |
| Applying a smart material (after the first) | 102 ms | 83–93 ms |

### Garbage collection

A 200-layer project holds about 627,000 Python objects the cyclic garbage
collector tracks. About 500,000 of them belong to the headless node trees of the
compiled units (nodes, sockets, links and their lists). CPython runs a full
collection once a quarter as many objects as the last one kept have survived
since. A structural edit allocates enough for that to happen every few edits, and
the collection walks all 627,000 objects: 311 ms in the middle of whichever edit
tipped it. Profiles charged it to whatever function was allocating at the time
(the reference graph, 87 ms "self time" that is 4.5 ms in isolation).

`evaluation/gc_policy.py` keeps it off the edit path:

- **Settle.** 0.3 s after an evaluation that followed a graph build, the idle
  tick collects what is not frozen and freezes what survived (`gc.freeze`). That
  is only what was allocated since the last settle, so it costs a few
  milliseconds. Later collections walk only newer objects.
- **Frozen objects still die.** Reference counting frees a frozen object as soon
  as nothing uses it. Across 24 structural edits at 200 layers the frozen count
  stayed flat (about 624,000), so replaced graphs are not kept alive.
- **Sweep.** A reference cycle among frozen objects waits for `sweep`. A file
  load and an undo run it (every session is dropped then). An idle spell of 30 s
  runs it once the frozen set has grown by a quarter, and by at least 100,000
  objects, since the last sweep.

`native_deep_stack.py` settles between edits the way the idle timer does, and
with `OP_DEEP_PHASES=1` reports collection time per generation. At 200 layers,
the 311 ms full collections are gone; younger collections still cost 5–10 ms per
structural edit.

### Painting and dragging inside a deep stack

`native_deep_stack.py` has a `paint_middle` case. It adds a paint layer in the
middle of the stack and feeds brush inputs as `paint_freeze.py` does, timing each
one from the pixels landing until the set is current. The pixel write itself is
Blender's brush in the product and is not timed.

- **Paint inputs kept the structure memos.** `channel_paint.mark_changed` moved the
  edit serial without recording a numeric edit. Every per-structure answer was
  therefore computed again on each input: the reader layers `dependent_channels`
  walks the whole stack for, `uses_geometry`, the geometry key.
  `geometry_uses_memo.py` now paints during its drag.
- **Same image, no reassignment.** `Graph.update` assigns a painted image to its
  node again whenever the image's identity changes. A headless node given the same
  datablock again (same address and `session_uid`) no longer counts as an
  assignment, so the native image groups hold and an evaluation reads only the
  distinct images' identities.
- **A folder's child composite is part of its spine.** `op_graph_spine` kept the
  backdrops of the edited node and of its enclosing folders. A folder's own edit
  (its opacity or blend) also composes over its child composite. Nothing kept that
  composite, so every tick of a folder-opacity drag rendered all of the folder's
  children again.

200 layers at 1K:

| Interaction | Before | After |
| --- | ---: | ---: |
| A brush input on a paint layer mid-stack | 23.4 ms (p95 31) | 7–13 ms (p95 12–19) |
| Bottom folder opacity drag | 15.2 ms, 44 units | 6.9 ms, 16 units |
| Top folder opacity drag | 7.2 ms, 20 units | 5.8 ms, 8 units |
| Middle leaf opacity drag | 9.5 ms | 7.3–7.7 ms |

A smart material's apply also stopped writing the set's surface parameters when they
are unchanged; an unchanged height distance still walked every channel. A replay
that only adds layers resolves the physical units of those layers alone.

### Undo

A memfile undo reallocates every struct of the restored layer store: layers,
channels, effects, masks. The Palette and Scene IDs keep their addresses, but
Blender invalidates the Python wrappers of every ID, images in headless nodes
included. The undo handler therefore drops every session and the next tick builds
the graph again. Other undo steps (a brush stroke's image step, sculpt, edit mode) restore
pixels or a mesh in place and keep the Main, which a memfile step replaces: the handler
keeps the sessions then (`cache._restored_datablocks`), and the restored pixels render like
a stroke. A memfile undo that kept the stack's shape (`undo_follow`: the set's stored
properties compared before and after it; only numeric values, or other values of layers whose
collections hold the same items in place) keeps its graph too: the units follow the restored
structs (`EngineGraph.follow_restored`), the changed layers are patched and every binding is
read again. At 200 layers the rebuild took 2.9–3.3 s:

- **Collector.** The rebuild's ~600,000 new objects set off full collections over
  a growing heap (0.6–2 s). `gc_policy.paused()` disables automatic collection
  while a graph is built from scratch or first evaluated, and freezes what survives
  at once. On undo and load, `release(defer=True)` marks a sweep for the next
  pause, because the dropped graph is still held for the rebuild to adopt its
  leaves and a collection then walked it and freed nothing.
- **Results.** The engine an undo drops is kept for its session's rebuild. Adopting
  a rebuilt graph hands each new unit state its predecessor's textures and stamp,
  so the native graph renders a unit again only where its content stamp differs.
- **Programs.** A native program's stamp counts its uniform revisions, which restart
  with every new program, so a carried stamp is only trusted with its own program.
  A rebuilt unit takes its predecessor's program, uniform bank and shader, rebound
  to its own tree's nodes and sockets (`_rebound`, `_same_shape`, `_rebind`). This
  happens when the unit and every producer compiled into it sign the same, their
  trees have the same shape and its images sign the same. A state given a newly
  translated program keeps its targets but not their stamp.
  `OP_VERIFY_INCREMENTAL` compares every rebound program with a fresh
  translation, and `shader_undo.py` asserts programs were rebound and that the
  published state is exactly the restored one.

200 layers at 1K (`undo_numeric`, `undo_structural` in `native_deep_stack.py`):

| Step | Undo |
| --- | ---: |
| Before | 2.9–3.3 s |
| Collector out of the rebuild | 1.6–2.2 s |
| Results carried | 1.2–1.6 s |
| Programs rebound | 0.8–1.1 s |

That left compiling every unit's tree again, about 0.45 s.

- **Units.** The graph an undo retires now gives its units to the rebuild,
  re-pointed to the restored structs. The memfile freed the old structs, so
  nothing reads them. The graph's struct index (`_path`, kept current by every
  patch) is plain data: each address as a layer id and a path within the layer
  (`undo_index`). The rebuild resolves those paths in the restored set
  (`_undo_structs`).
- **Adoption.** A unit is adopted as after a structural patch: its signature
  must match, and so must its imports and exports. In addition, its owners,
  re-pointed, must be exactly the structs the restored stack gives its key; a
  channel whose index an undo shifted is therefore compiled again.
  `_follow_undo` re-points bindings, aliases, binding keys, compiled effects and
  every struct an image getter captures. It also re-points every struct a node
  attribute holds, such as a spatial filter's effect; compiling records those
  nodes in `rna_nodes`. A unit is compiled instead when any closure or node
  attribute still holds something else (`_holds_foreign`). Image nodes take
  their datablocks back by session id.
- **Excluded.** A unit holding a resource or cache binding, a Mask Graph, a
  decal object, a custom node group or a GPU resource is compiled again.
- **Values.** The build reads every binding again, as after a patch, so a
  value the undo restored reaches its socket. An adopted unit keeps its engine
  state (program, uniform bank, results), so nothing is carried or rebound.
- **Checks.** `OP_VERIFY_UNDO=1` compares every adopted unit with a fresh
  compile: nodes, sockets, links, bindings, image entries, imports, exports,
  flags and owners. Node attributes holding RNA are compared by address.
  `OP_UNDO_ADOPTION=0` compiles everything, as before. `shader_undo.py`
  asserts adoption and that the published state is exactly the restored one.
  It also blurs a layer and checks that every struct an adopted node holds
  belongs to the restored set, and that edits of the effect still apply.

- **Native graph.** The native graph now outlives an undo too
  (`survives_undo`). The undo handler closed every native object as a
  generation barrier for restored documents. A native graph holds only plain
  unit descriptions, though, and its own stamps are what tell the carried
  engine which units the undo changed. Without them every unit was restamped,
  and about 1,000 rendered again in order. A load still closes it.
- **Image epochs.** Image epochs also survive an undo or redo
  (`dependencies.clear(undo=True)`). A memfile undo leaves images at their
  addresses with their pixel caches, which Blender preserves even for re-read
  datablocks. An image undo tags every image it restores, so the depsgraph
  moves its epoch. Restarting the epochs only made every unit sampling a detail
  image render again. An image outside the depsgraph never had an epoch to
  restart.
  - A memfile undo still reported the images whose tags were pending when its
    target step was pushed (the detail images a smart material loaded, say):
    a re-sync with no geometry, shading or transform flag, which moved their
    epochs all the same. Since `869a2ce` only a flagged update moves an epoch.
    An image undo's `DEG_id_tag_update(&image->id, 0)` sets all three, as
    `update_tag()` and a colour space change do. On the Windows laptop (40
    layers at 1K) the first undo went from 284–316 to 218–222 ms and the second
    from 131–154 to 90–92 ms (`windows-plan.md`).
- **Collapse.** The undo now reaches the engine as the units it changed, 4 for
  a numeric undo of one leaf. Suffix collapse applies as it does to any edit.
  After the first undo, an undo renders 28 units.

| Step | Undo |
| --- | ---: |
| Programs rebound (compile everything) | 0.73–1.1 s |
| Units adopted across the undo | 0.28–0.42 s |
| Native graph and image epochs kept (collapse applies) | 0.22–0.25 s |

The first undo after a build still restamps about 300 units. Of the rest, the
build (plan, adoption, value reads) takes about 135 ms and Blender's memfile
restore about 50 ms. The first build of a file went from 4.7 to 3.3 s.

## CPU kernels

These run wherever the library loads (`lifecycle.cpu_library`), with NEON on Arm and SSE4.2
on x86-64. `platforms.md` covers Windows, Linux and Intel Macs. Profiles of the structural
paths show arithmetic in C taking microseconds. The time
there is Python bookkeeping, which assembly cannot speed up. The CPU kernels that do
spend their time computing were reworked, and each keeps its previous version as a
reference its C test must match exactly.

- **Brush coverage (`op_brush_coverage`).** This kernel builds vector-stroke layer
  rasters, paints without the GPU path, and runs smudge and resolved dabs. It
  tested every texel inside a stroke's bounds against all of its up to 256
  stamps. Stamps are now listed, in stamp order, in the cells of a uniform grid
  their reach overlaps. The reach is `radius * (1 + depth limit * |axis|)`, plus
  rounding margins, and cells are found by a monotonic map. A pair the grid skips
  is therefore one that contributed exactly nothing, and FLOW multiplies in the
  same order.

  On arm64, four texels in one cell go through the cell's stamps as NEON float32x4
  lanes. The lanes perform the scalar operations in the same order: no fused
  multiply-add (the library builds with `-ffp-contract=off`), IEEE division and
  square root, and minNum/maxNum for fminf/fmaxf.
  `test_brush_coverage` requires bit-identical output over about 500M random
  texel-stamp pairs, half of them in raster order. A fused dot product fails it.
  For 1M texels × 256 stamps: 384 ms before, 35 ms with the grid, 16 ms with NEON.
  `-DOP_SCALAR_COVERAGE` builds the scalar path.
- **Export padding (`op_padding_donors`).** The horizontal pass stops once dx²
  exceeds the best distance found; a candidate at an equal distance is still
  compared, so island and index ties break as before. It also starts at the
  nearest column with any donor in reach. The vertical pass runs its per-column
  scan row by row, in memory order. `test_padding` requires identical donors and
  distances over 120 random layouts. 4K with 24 islands: 710 → 140 ms.
- **UV rasterize (`op_uv_rasterize`).** Bakes, mesh masks, vector strokes and texel
  density rasterize here. Unique output used to sort every sample by texel and then
  triangle (a qsort over up to 16M samples at 4K). Samples are now bucketed by row,
  stably, and within a row the last sample of each column wins, which gives the same
  samples in the same order. On arm64 the barycentric test takes four texels a step
  (`-DOP_SCALAR_RASTER` builds the scalar path). `test_rasterize` keeps the previous
  function and requires identical samples. 100K triangles, unique: 2K 391 → 52 ms,
  4K 1584 → 235 ms.
- **Export pixels (`op_quantize`, `op_normal_encode`, `op_normal_relief`).**
  These replace NumPy's clip, sRGB curve, round and cast for every written
  channel. They also replace the normal map's decode, normalize and encode
  (padding, and `surface_normal.combined` without relief) and the whole relief
  path of `combined`. That path covers chart-aware height derivatives, slopes
  through the UV metric, the Bump-style tilt of the authored normal, and the
  final normalize and encode. Each kernel takes one pass with no temporaries,
  writing a separate output so no input is copied first. Quantize uses NEON,
  eight values a step. Normal encoding takes four RGBA pixels a step, split into
  channels with `vld4q`. Relief takes four interior texels a step, with
  neighbours from unaligned loads and each scalar branch a lane select. The NumPy
  code stays as the fallback, and `tests/unit/test_native_pixels.py` requires
  identical bits from both. Its inputs include NaN, infinities, the sRGB knee,
  rounding ties, empty texels, chart borders and height cliffs. Relief at 2K:
  about 450 ms in NumPy, 88 ms scalar, 25 ms NEON.

  The sRGB curve avoids powf in the vector path. x^(1/2.4) is a table entry for
  x's exponent times a degree-7 polynomial in its mantissa, within 4 × 2⁻²⁴ of the
  exact power. A code is rint(s) for s = (1.055 p − 0.055) × maximum. Allowing two
  ulps of error in the platform's powf, the approximate s is within 0.045 of the
  exact one at 16 bits and within 1.7e-4 at 8 bits. A lane closer than that to a
  rounding tie (k + 0.5) is recomputed with powf, so every code matches powf's.
  That is 9.8% of 16-bit values and 0.04% of 8-bit ones. `test_pixels` compares
  every float in [2⁻⁹, 1] at both depths. A guard ten times smaller fails it (the
  measured deviation is 0.012–0.02 at 16 bits and 4–6e-5 at 8). 2K RGBA, sRGB:
  80 → 14 ms at 8 bits, 81 → 23 ms at 16. 2K Unreal export: 967 → 499 ms.
- **PNG compression.** On painted content zlib dominated the export: a 2K
  Unreal export of noisy maps spent 4.6 of 6.9 s in `compress`, at about 10
  MB/s. The scanlines are now deflated in row-aligned segments of about 1 MiB,
  each on its own thread, as pigz does. Each segment starts from the previous
  segment's last 32 KiB as its dictionary and ends with a sync flush, so the
  segments join into one zlib stream. Adler-32 is combined from the segments'
  checksums. The pixels are identical, sizes stay within 0.1% of one serial
  stream, and the boundaries depend only on the row size, so repeated exports
  give the same bytes. `tests/unit/test_png_segments.py` decodes the files with
  plain zlib at both depths, with segments down to one row. Dropping the
  dictionary or breaking the checksum fails it.

  Rows are filtered (None, Sub, Up, Average, Paeth) by `op_png_filter`. It
  picks, per row, the filter with the smallest sum of |signed byte|, as libpng
  does by default. Encoding filters read only raw neighbours, so NEON computes
  all five candidates and their sums sixteen bytes a step; Paeth uses `vabdq`
  and a widened third distance. Filters lose on noise-like masks, where deflate
  finds repeats in the raw rows (libpng's heuristic made those files up to 9%
  larger). So each segment compresses a 64 KiB sample of its rows both ways and
  is filtered only if that wins by 1%. Earlier segments' rows enter a dictionary
  as those segments wrote them. Over 36 material textures the files are 6.4%
  smaller (up to 15% on wood and brushed metal), and none grew. A NumPy filter
  gives the same bytes without the library, and the tests compare the two.
  Hashing went the same way:
  Blender's bundled `hashlib.sha256` runs at about 87 MB/s, and the export
  transaction hashes every staged file. `op_sha256` uses the arm64 SHA2
  instructions (SHA256H/H2/SU0/SU1) at about 1.7 GB/s, with identical digests.
  `utils.digest.sha256` picks it once the native engine is active, and the
  transaction and the authoring code's whole-image digests use that helper.
  `test_sha256` checks the FIPS 180-4 examples and a separate implementation over
  every length to 1100 bytes and random splits. Noisy 2K export: 5.9 → 1.1–1.3 s;
  flat fills: 499 → 233 ms.

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
- **Cycles bakes.** On Apple GPUs newer than M1, Cycles doubles its path states
  twice when the GPU's working set has room. On the 18 GB Mac a cube's bake then
  took more than 6 GiB, and nine bake suites passed the 4 GiB cap. The test
  environment (`blender_test_profile.py`) sets `CYCLES_METAL_GROW_PERCENT=0`, the
  count M1 Macs always use. `phase05.py`, `geometric_curvature.py`,
  `damaged_bevel.py`, `auto_edge_rebake.py`, `bake_back.py`, `bake_corrections.py`
  and `mask_groups.py` now peak at 2.1–2.8 GiB.
- **Heavy suites** (18 GB Mac, 2026-10-11):
  - In background: `export_padding.py` peaks at 4.7 GiB, `resolution_switch.py` at
    4.0–4.2 GiB, and `native_publication_4k.py` needs more than 7 GiB.
  - In a window: `director_candidates.py` 4.7 GiB, `shader_preview_ui.py` 4.5 and
    `edge_map_refresh_ui.py` 4.0.
  - `material_painting_ui.py` and `quality_baseline_paint_ui.py` build three sets
    at 2K with 25 layers, and they pass 8 GiB once all three sets compile. That is
    about 2,500 bytes a pixel: engine textures and retained results, Blender's image
    buffers and their GPU copies. The gate (`run_tests.py`) therefore paints them at 1K,
    where they peak at 3.9–4.5 GiB; `OP_MATERIAL_UI_RESOLUTION=2048` and
    `OP_QUALITY_PAINT_RESOLUTION=2048` restore 2K on a machine with the memory.
  - `artist_file_edits.py`, `artist_file_workflow.py` and
    `artist_file_multiset.py` pass 5–6 GiB with the local artist file present.

Engine budgets now scale with physical memory. Managed textures get a quarter of
it (1.5–4 GiB). Retained results and the free pool each get 1/32 (128–512 MiB).
Machines with 16 GB or more keep the previous limits.

## Open work

- **CPU boundaries.** Generator masks run in the units' GPU programs; since 0.14
  so do the Drips streak field and Runoff trails (both catalog fields of the baked
  position, with GLSL ports of the integer hash). A first smart-material apply at
  200 layers went from 955 ms over 43 ticks to 480 ms in one tick. Metric material
  mapping with images or procedural detail, mesh masks, scene fields, anchored
  micro details and the legacy Noise source still rasterize on the CPU at full
  resolution.
- **Isolated folders.** Chains inside isolated folders are not collapsed: they
  composite with straight alpha over a transparent start, which is not affine in the
  backdrop. Editing a layer inside a smart material re-renders the siblings above it
  (4 of a middle leaf's 7 units per channel), since `bfdeb2b` as one fused pass per
  channel. Premultiplied compositing inside isolated folders would make them
  collapsible: one pass for a run of any length.
- **Opening a deep file.** The first build of the 200-layer stack takes about 3.3 s
  at 1K: compiling 1,728 units, decoding the detail-source images (about 0.9 s),
  translating programs and the first render.
- **Undo.** About 0.22–0.25 s at 200 layers (see Undo), most of it the build. Units with resource or cache
  bindings, Mask Graphs, decals or custom node groups are still compiled again.
- **Layer order.** A layer inserted mid-stack still updates the owner tuple of
  every unit of the layers above it (about 1,000 at 200 layers), though its bindings
  move once per layer. C still takes the whole topology on every change (built at C
  speed).
- **Whole-stack passes left in a patched edit.** Each costs 1 to 3 ms at 200
  layers:
  - the plan (about 3.5 ms in the callback's structure check): memo copies, the key
    closure and each layer's collection fingerprint;
  - the reach walk in `_reverse_changed`;
  - the chains and positions;
  - collecting live effects;
  - the report's warnings.
- **Smart materials.** Translating the new units' programs (about 0.3 ms each, the
  IR walk) and compiling their trees still dominate a repeated apply. Units with
  equal signatures produce identical IR, which a program cache could reuse.

