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
  defines density. Rust Flakes/Moss create separate owned helpers. Regenerate,
  Freeze and Remove operate on the selected helper. Weld Curve uses an editable
  curve. Snow, splinters and peeling fragments are experimental prototypes.
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
- **Offline Analysis:** use explicit contact frame ranges and a Contact field,
  choose a rest mesh for strain, mark selected cut faces, inspect the resource
  inventory, or stage a mesh LOD. Source meshes remain available for comparison.

The [implementation status](implementation-status.md) lists limitations and
acceptance evidence. UDIM authoring is not exposed: the tile-access probe failed.
The GPU image-chain experiment is disabled by default. Neither entry is presented
as production-ready functionality.
