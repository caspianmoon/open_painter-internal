# UDIM texture sets

Open Painter supports a texture set per occupied UDIM tile. Each tile has its own
resolution, images, mesh maps and texture outputs. New groups share their layer and mask recipe. The original UV layout
stays in UDIM space; you do not need to move islands back to 0–1.

## Create the sets

1. In Object Mode, select your unwrapped mesh objects. Their mesh data must be
   local and single-user, with no existing Open Painter texture-set assignments.
2. Keep every UV face inside a single tile. Vertices on tile borders are valid.
   The supported addresses are 1001–2000: ten columns, starting at UV (0, 0).
   Gaps and additional rows are supported. Multiple selected meshes must use the
   same render UV map name.
3. In **Texture Sets**, choose **Create UDIM Sets**, enter an export name and
   choose the resolution per tile. The button is also in the Open Painter sidebar.
4. Select the tile in the texture-set list and add your layers or materials.

Faces are grouped by their UV tile, regardless of their original material names.
Open Painter retains the original material slots and routes faces through new
managed slots. Removing a tile set restores its faces to their original slots
when those slots still exist. Invalid UV layouts are rejected before creation.

Existing painted sets are not automatically split or migrated. Keep a saved copy
of those projects; this action starts with unassigned meshes. Do not reorder the
managed material slots or move faces between tiles after assignment. Validate
Project reports missing UV maps and faces that moved to another tile.

## Paint, bake and save

Both **Paint Material** and the native single-channel brush work on the selected
tile. Enable **Paint Across Sets** to paint across tile boundaries. Editing the shared
recipe through any tile updates the group in one undo transaction; stroke permissions
still restrict each gesture to the tiles it was allowed to touch.

Existing files retain independent stacks. **Link Layer Stacks** explicitly links
matching recipes and reports conflicts before changing anything. **Make Independent**
detaches the group, preserving each tile's pixels and giving its editable strokes
independent copies.

Native painting temporarily selects a derived UV map with local tile coordinates.
**Finish** restores the original active map, including after saving and reopening
an active paint session. The source UV coordinates and render UV map remain intact.
Native painting needs room for that extra UV map in Blender's mesh UV layers.

Bake maps for each tile using the usual bake controls. CPU evaluation, Shader
Native preview, Cycles bake staging, curvature, correction brushes and regional
rebaking use the tile's local coordinates. Ordinary `.blend` saves preserve the
tile metadata, shared recipe and managed images. Images and evaluation caches use memory per
tile; choose resolution with the total number of tiles in mind.

## Export

Export with **All Texture Sets** or **Selected Objects' Texture Sets** to include
the tiles together. For example, the name `Robot` produces:

```text
Robot_BaseColor.1001.png
Robot_BaseColor.1002.png
Robot_Roughness.1001.png
Robot_Roughness.1002.png
```

The tile number is always immediately before the file extension. Engine presets
keep their channel naming and packing. **Export Name** in each tile's settings
controls the common stem; use the same stem for tiles belonging to one texture.
Target and bundle manifests include the tile number and group identity. A
receiving application must support UDIM textures to interpret the numbered files;
the exporter does not add UDIM capability to a game engine or mesh format.

The atlas operation can flatten tile sets into separate cells on a copied mesh,
leaving the original UDIM UVs intact. Direct import of an external `<UDIM>` image
sequence into one shared layer is not provided: assign external images to their
corresponding tile's layer.

## Verification

The acceptance scripts are `tests/integration/udim_workflow.py`,
`tests/integration/udim_material.py` and `tests/integration/udim_paint_ui.py`.
They check distinct pixels and numbered exports, Cycles baking and shader
readback on tile 1002, native and multi-channel strokes across tiles, save/reopen,
restored UV selection, atlas coordinates, displacement, sparse rows, multiple
objects, invalid layouts and creation rollback. The GUI script requires a real
Blender window and `--enable-event-simulate`.
