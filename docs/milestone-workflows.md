# New milestone workflows

Use the existing Open Painter editor and native Painter panels. These changes do
not replace the editor or the material library.

- **Interactive preview:** Texture Set settings → Preview Quality. Full remains
  the default. 512/1024 are disposable previews; saving, rendering and exporting
  flush authored-resolution outputs.
- **Scene fields:** select a mask, choose Scene as its source, choose the field
  and explicit influencer object/collection. Start with Distance or Contact and
  a nearby object. Manual Update computes on demand; Live reacts to dependencies;
  Freeze stores a portable image. Missing inputs preserve the last good result.
- **References:** choose Reference as a mask source and select the source layer,
  channel/mask and stage. Cycles reject before output publication. Publish a mask
  when a stable texture is needed by a geometry effect.
- **Geometry Effects:** select an assigned source mesh and the layer whose mask
  defines density (for moss, the Moss texture effect's mask). Rust Flakes, Moss,
  Snow, Splinters and Peel Fragments create separate owned helpers with variant
  prototypes and their own materials; Regenerate, Freeze and Remove operate on
  the selected helper. Weld Curve turns an editable curve into a rippled bead.
  See [geometry-effects.md](geometry-effects.md).
- **Age / Environment State:** tag participating fill layers with their semantic
  effect role, then apply Clean/Used/Damaged or custom age/usage/maintenance.
  Paint strokes and untagged layers are preserved. Use separate texture sets for
  separate per-object variations; shared outputs remain shared.
- **Material Stamp (4 Channels):** choose radius, hardness and channel values,
  then click an assigned visible surface. Escape cancels placement; normal
  navigation passes through. This is a discrete stamp, with one undo/redo step.
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
- **Curvature V2:** Rebake Edge Map on existing projects (the map reads "Stale ·
  crease-only"); Curvature Radius is the wear width (default 2 cm). Smooth bevels and
  tessellated rims now drive Edge Wear and Chips in every material.
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
