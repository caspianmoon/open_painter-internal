# Deep stacks: suffix collapse and incremental evaluation (ABI 13)

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
- **Native graph.** While the topology holds, `NativeGraph._reprogram` describes
  and indexes only the replaced programs and the units their inlined producers
  compile into.

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

The benchmark selects, and idle-arms, the layer each edit starts from: the one an
insertion goes above, the one removed or moved. In the UI that is the selected
layer, which idle arming prepares. A patch after a named invalidation (the stack's
remove, move, place) reads values again only for edits dropped while it was
pending: a catch-up update with no owner used to make it read all ~6,600 bindings.

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

- **CPU boundaries.** Generator masks run in the units' GPU programs; since 0.14
  so do the Drips streak field and Runoff trails (both catalog fields of the baked
  position, with GLSL ports of the integer hash). A first smart-material apply at
  200 layers went from 955 ms over 43 ticks to 480 ms in one tick. Metric material
  mapping with images or procedural detail, mesh masks, scene fields, anchored
  micro details and the legacy Noise source still rasterize on the CPU at full
  resolution.
- **Isolated folders.** Chains inside isolated folders are not collapsed.
- **Layer order.** A layer inserted mid-stack still visits every unit of the
  layers above it (about 1,000 at 200 layers) to follow it to its new address,
  and a new topology still replaces the native graph's C topology and walks every
  unit's description. Units that address their layer by id, and a C graph that
  inserts and removes nodes, would make these proportional to the edit.
- **Whole-stack passes left in a patched edit.** The plan still carries every
  memo into a new plan and sorts every signature. The layer callback walks all
  readers again after a structural edit (`references._reader_layers`), and the
  native graph rescans every image node after compiling new units. Each costs 1
  to 3 ms at 200 layers.

