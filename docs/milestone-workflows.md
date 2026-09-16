# New milestone workflows

Use the existing Open Painter editor and native Painter panels. These changes do
not replace the editor or the material library.

- **Physical scale (0.9.5):** Texture Set List shows *Texel … · UV tile …*; switch a
  procedural, mask or filter to *Physical* and type a size in metres; the lock button converts a
  whole set so its sizes travel with the material. [Details](physical-scale.md).
- **Surface filters (0.9.4):** every blur, dilation, warp or directional effect shows a *UV /
  Surface* switch; Surface (default) follows the mesh across seams, and directional effects take a
  world direction. [Details](surface-filters.md).
- **Mask groups and custom graphs (0.9.3):** mask menu → *Add Mask Group* / *Add Custom
  Graph*; with a member active, *Add* joins its group; the list buttons move an entry into the
  group above, out of it, and move groups as blocks. [Details](mask-architecture.md).
- **Mesh masks (0.9.2):** Layers → mask menu → *Add Mesh Mask*, then *Select By* Object,
  Collection, Material, Vertex Group, Face Set, Face Selection, UV Island, Normal Direction or
  Geometry Attribute. A Collection mask follows the collection: add an object to it and the
  material updates. [Details](mesh-masks.md).
- **Bake Doctor (0.9.1):** Bake Mesh Maps → *Bake Check* runs before every bake; click a
  finding to select the offending faces, vertices, edges or objects. *Bake Corrections* →
  choose a correction, press *Bake Correction Brush*, paint the problem area, set the value
  (ctrl-scroll steps by 0.001); the painted region rebakes when the stroke ends.
  [Details](bake-doctor.md).
- **Layer performance (0.9.0):** Layers tool strip → hourglass (*Profile Layers*) fills cost
  badges in the rows; the row toggle next to the eye bypasses a layer in the preview only;
  Properties → *Performance* sets a layer or isolated folder to Cached or Frozen and refreshes
  or clears its image; Texture Set List → *Freeze While Painting* (on by default) defers
  expensive recomputation until the stroke ends. [Details](layer-performance.md).
- **Interactive preview:** Texture Set settings → Preview Quality. Full remains
  the default. 512/1024 are disposable previews; saving, rendering and exporting
  flush authored-resolution outputs.
- **Scene fields (live since 0.5.0):** select a mask, choose Scene as its source,
  pick a Preset or a kind (orientation, height, ground, proximity to chosen or every
  other mesh, exposure, live convexity/concavity/edge exposure, attributes). Live
  fields re-analyse in the background when anything they read moves or changes —
  the status line shows *Analysing…* and the mask updates when it lands; Manual
  Update computes on demand; Freeze stores a portable image. Edge Wear and Chips
  can take their edges from live geometry instead of baked maps (Edges From).
  [Details](scene-fields.md).
- **References:** choose Reference as a mask source and select the source layer,
  channel/mask and stage. Cycles reject before output publication. Publish a mask
  when a stable texture is needed by a geometry effect.
- **Open Graph (0.4.0):** the node icon in the Layers tool strip splits the Painter
  area into a live node view of the set. Drag from a layer's *Output*, a folder's
  *Composite* or any anchor socket into a *Content*, *Mask* or *Micro* input to set
  that reference; cycles are refused with a status message. Clicking a node selects
  the layer. [Details](open-graph.md).
- **Published outputs (0.4.0):** the layer properties' *Published Outputs* box
  publishes the layer's blended output (*Layer Output*) or, on folders, the child
  composite (*Folder Composite*) as anchors any Published Input mask, fill Content
  or Micro Detail can pick; composite consumers choose which channel they read.
- **Material modules (0.4.1):** folder properties → *Module* → Save Folder as
  Module writes a versioned `.openpaintmaterial` to the library; the package icon in
  the Layers strip inserts instances (library modules and the built-in Damage);
  Update / Update All merge a newer version into instances keeping your edits and
  listing conflicts; Detach opts a folder out. Catalog materials are module instances
  of their recipe and update the same way. [Details](material-modules.md).
- **Anchor points:** Add Mask Effect → Add Anchor Point places a row that publishes
  the mask coverage above it; FILTERS → Anchor publishes a channel after the
  filters above it. Pick an anchor on a Published Input mask, on a fill channel's
  Content Anchor, or in a generator's Micro Details (Micro Height / Micro Normal
  give painted relief edge wear and dirt). Any acyclic reference is allowed;
  selecting an anchor lists its consumers, consumers locate their anchor, and
  deleting an anchor reports the references it broke. [Details](anchor-points.md).
- **Geometry Effects:** select an assigned source mesh and the layer whose mask
  defines density (for moss, the Moss texture effect's mask). Rust Flakes, Moss,
  Snow, Splinters and Peel Fragments create separate owned helpers with variant
  prototypes and their own materials; Regenerate, Freeze and Remove operate on
  the selected helper. Weld Curve turns an editable curve into a rippled bead.
  See [geometry-effects.md](geometry-effects.md).
- **Material → Geometry (0.7.0):** Geometry Effects → Displace from Height, Extract
  Curves (then Stitches / Seam Bead on the curve), Bolts / Scales / Rocks / Bark scatter,
  and Bake Back to Normal / AO with keep / hide / remove. See [geometry-effects.md](geometry-effects.md).
- **Age / Environment State:** tag participating fill layers with their semantic
  effect role, then apply Clean/Used/Damaged or custom age/usage/maintenance.
  Paint strokes and untagged layers are preserved. Use separate texture sets for
  separate per-object variations; shared outputs remain shared.
- **Vector Brush (0.6.0):** paint layer → Vector Brush → Paint Vector Strokes; drag on
  any assigned surface, across texture sets. Strokes stay editable in the layer's
  Vector Strokes list and re-render at any Output Resolution. [Details](vector-strokes.md).
- **Material Stamp (4 Channels):** choose radius, hardness and channel values,
  then click an assigned visible surface. Escape cancels placement; normal
  navigation passes through. This is a discrete stamp, with one undo/redo step.
- **Target renderer (0.5.1):** the toolbar's Target menu (Blender, Unreal 5, Unity
  HDRP/URP, Godot 4, Custom) sets every set's export conventions; Engine Preview shows
  the asset as the engine will compute it from the exported files; Export → <Target>
  writes textures, manifest and mesh in one step. [Details](target-renderer.md).
- **Production Export:** choose the texture export directory and engine profile.
  Evaluated Bundle writes a new scene, textures and manifest without overwriting
  an existing bundle. Generated geometry is explicitly included or omitted.
  Measure Density reports the active set. Select source props before Stage Atlas;
  it creates evaluated copies and new UVs at the explicitly chosen resolution.
- **Inspect Bake Setup:** Bake Mesh Maps → Inspect Bake Setup checks
  assignments, scale, cages and the evaluated render UVs of every object in the
  set for degenerate faces, faces outside the 0–1 tile and exact UV overlaps.
  Partial overlaps are warnings; stacked or mirrored islands are informational
  because symmetric assets stack them deliberately. With Bake from High Poly on,
  it also samples cage rays and reports the missed fraction as an estimate.
  Select Problem Faces selects
  the reported faces on their base meshes when no modifier changes the face
  count. Nothing else is modified. See [bake diagnostics](bake-diagnostics.md).
- **Damaged Bevel / Rounded Edges:** bake Bevel Mask and Bevel Normal (Bake Mesh
  Maps → Bevel Maps, radius = band width), then Add Surface Effect. Works in
  EEVEE because the Cycles Bevel/AO nodes are baked, not evaluated per frame.
- **Edge map set, Edge Size and Edge Width:** the edge map bakes as four reaches
  from one Curvature Radius (default 2 cm): thin (×0.5), normal (×1), wide (×2) and
  extreme (×4), all sharing the crease lines. Each Edge Wear or Chips mask has a live
  Edge Size slider (0 thin … 3 extreme) that blends between the maps and an Edge Width
  slider that narrows the chosen band; neither needs a rebake. Rebake Edge Maps on
  existing projects (a single or crease-only map reads as stale); adding an
  edge-driven material, effect or smart mask queues that rebake in the background
  with progress in the status line, and the effect updates when the maps land.
  Smooth bevels and tessellated rims drive Edge Wear and Chips in every material.
- **Mean Curvature / Selected Faces maps:** Bake Mesh Maps → Target Mesh Utility
  Maps. Mean Curvature is signed and smooth (0.5 flat, convex above, concave below)
  and shares Curvature Radius. Selected Faces snapshots the current face selection and
  Reselect Baked Faces restores it. Both are Baked mask sources.
- **AO Distance and 2x Supersample:** in the bake settings; the default distance
  matches earlier bakes and supersampling is refused above 2 GiB of bake memory.
- **Offline Analysis:** use explicit contact frame ranges and a Contact field,
  choose a rest mesh for strain, mark selected cut faces, inspect the resource
  inventory, or stage a mesh LOD. Source meshes remain available for comparison.

The [implementation status](implementation-status.md) lists limitations and
acceptance evidence. UDIM authoring is not exposed: per-tile pixel access exists
only through tile files on this build ([gate rerun](udim-tile-access.md)), and no
two-tile acceptance has run.
The GPU image-chain experiment is disabled by default. Neither entry is presented
as production-ready functionality.
