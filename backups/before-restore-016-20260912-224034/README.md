# Blender Open Painter

A Blender extension for non-destructive PBR texturing: texture sets, paint and fill layers,
folders, masks, mesh-map baking, procedural generators, reusable smart materials, and texture export.
Python and Blender's bundled NumPy are the only runtime requirements. GPL-3.0-or-later.

**Feature target:** the supplied Substance Painter documentation dated June 26, 2018.
Full parity is not achieved. The [page-referenced feature inventory](PARITY.md) records working
subsets, missing workflows, external dependencies and acceptance criteria.

## Install

Tested on **Blender 5.2.1 LTS, macOS Apple Silicon**. The manifest requires Blender 5.2 or newer;
other platforms and newer versions have not been tested.

1. Download or build [`dist/open_painter-0.1.7.zip`](dist/open_painter-0.1.7.zip).
2. In Blender, open **Edit → Preferences → Add-ons**, open the menu at the top right,
   choose **Install from Disk**, and select the ZIP. Enable **Open Painter** if needed.
3. Click the **Editor Type** selector at the upper-left corner of any Blender editor—the menu
   containing 3D Viewport, Image Editor, Shader Editor, and the other editors.
4. Choose **Open Painter**. It opens the Painter layout with Assets on the left, paired 3D/2D views
   in the center, and Texture Sets, Layers and Properties on the right. Restore paired views from maximized mode before combining them.
5. Use Blender’s editor selector to change editors. The Close-session controls have been removed.

The entry is a supported Python editor subtype adapter. Blender registers it through its NodeTree
subtype API; when selected, it mounts the native 3D/Image Editor painting backend in that area.
It is not a new compiled Blender SpaceType. The native editor selector remains available, and the
backend's selector highlight can read 3D Viewport or Image Editor while Open Painter is active.

The ZIP follows Blender's [extension packaging format](https://docs.blender.org/manual/en/latest/advanced/extensions/getting_started.html).
No online account, network connection, or separate Python installation is needed.

## First use

1. Select your low-poly mesh. It needs a UV map in the 0–1 tile. A new texture set immediately creates and assigns a Principled material with all six initialized texture images connected.
2. Click **+ New** in Texture Set List. A selected UV-unwrapped mesh is assigned automatically; use **Assign** for additional meshes. For a mesh with multiple materials,
   choose the intended active material slot before assigning. Repeat for additional objects/slots.
3. Click **Settings** in Texture Set List to choose resolution (default 2048).
4. Click **Bake Mesh Maps** in the top bar, select high-poly objects and click **Add Selected High Poly**.
   Set ray distance and bake Normal, AO, and Curvature. For self-bakes, disable **Bake from High Poly**.
5. Add a **Fill** layer. Set Base Color, Roughness and Metallic. Add another fill and a
   **Generator** mask; select **Edge Wear**. It uses the baked curvature map.
6. Click **Use Baked Normal + AO in Stack** to add those mesh maps to the material.
7. Add a **Paint** layer. Click **R** (Roughness) in Properties to target roughness, then **Paint** in the top bar. Choose a brush from **Assets → Brushes**; size, strength, and spacing are in Properties.
   The composite updates cooperatively during strokes. The brush follows paint-layer and channel selection; selecting a fill, folder or non-painted mask ends the session. Click **Finish** to leave painting.
8. Click a material thumbnail in **Assets** to apply it. **Save Material** saves the stack; **Import Assets** imports portable materials. Search and the Mat/Smart/Brushes/Alphas tabs filter the asset browser.
9. Click **Export Textures** in the top bar, choose a directory and **Unreal Engine 5**, then export.

Bottom layers are evaluated first; the UI displays the top layer at the top. **Group** or
**Ctrl+G** groups the selected layers in place. Ctrl-click adds/removes a selection; Shift-click
selects a range. Groups have collapse arrows and nested children. Drag layers onto a group to
move them inside, onto another layer to reorder, or onto the **Layers** header to move them out.
**Ctrl+Alt+G** ungroups the selected group. Delete removes the group and its descendants; Undo
restores them. New groups use passthrough composition. Group opacity and masks affect the result
once, and group duplication copies descendants and their paint images.

**T** toggles the Assets panel and **N** toggles Texture Sets/Layers/Properties. These keys act on
Open Painter's panels while the mouse is over its viewport; they do not toggle other editors.

Painted masks start white. Click a layer’s mask thumbnail to inspect it, then **Paint mask**, and paint black to hide content.
Multiple mask entries are evaluated in list order, with invert, levels, multiply/add and strength.
Fill textures support UV transforms and world-space triplanar sampling; use **Edit** in Properties
and assign an image to reveal projection, scale, rotation, offset, **Nearest/Bilinear** filtering,
**Repeat/None** wrapping and **Triplanar Hardness**. None makes pixels outside the original tile
transparent so lower layers show through. Scroll individual docks to reach more assets, layers, texture sets or properties.

**Channel Blending…** in Properties opens native controls for the chosen channel’s opacity and
blend mode. This selection is separate from the brush target. Overall layer opacity multiplies
channel opacity; **Use Layer Blend** preserves the layer-wide setting. Twenty blend choices
include Screen, Min/Max, Difference, Exclusion, Burn/Dodge and light modes. The complete PDF blend
set is not implemented yet. Existing projects keep their previous defaults; editing an old folder
initializes neutral per-channel settings. See [the controls](docs/channel_blending.png).

Docks follow your current Blender theme and interface font size. Hover over abbreviated channel
buttons for their full names. Numeric fields support dragging, **Shift** for precision and **Esc**
to cancel a drag. Double-click or right-click a numeric field to edit its value in a native Blender
property popup. Double-click a layer name or use **F2** over the right dock to rename it.
Blender viewport shading and overlay changes remain in effect while the editor is open.

Brush Properties exposes color or scalar value, size, strength, spacing, erase and alpha controls.
Scroll the Properties dock to reach all controls. **Import Alpha** loads a grayscale image and packs
it into the blend. Alpha thumbnails show the imported image; **Clear Alpha** removes the brush mask.
Bundled brushes are copied to local brush assets so imported textures work and persist without
modifying Blender's bundled library. For painted masks, **Black · Hide** and **White · Reveal**
set the brush value directly.

The mask Properties dock provides mask selection/reorder/delete, enable/invert, strength, levels,
and generator controls. **Settings** opens the full native mask properties. Texture Set **Settings**
also exposes renaming, assignment repair, validation and resolution. **Library** opens smart-material
apply/import/save/delete actions.

3D painting uses a temporary face-selection mask to protect material slots outside the current
texture set. The previous face selection and mask setting are restored when painting ends, including
after save/reload. 2D painting edits the selected layer image directly; finishing changes the 2D editor
to view mode so it cannot paint into the evaluated output. Export and save flush pending paint changes.

## Included example

Open [`examples/open_painter_demo.blend`](examples/open_painter_demo.blend) after enabling the extension.
It contains explicit HP/LP assignments, baked maps, Painted Steel layers and an actual viewport-painted
roughness image. A portable smart material and example Unreal textures are in `examples/`.
The high-poly source is hidden in the demo; baking temporarily makes it available and restores visibility.

The Assets shelf includes **Painted Steel, Dirty Plastic, Rough Wood, Brushed Steel, Copper,
Matte Rubber, Porcelain and Red Paint**. These original PBR presets contain coordinated color,
roughness and subtle height textures. Painted Steel and Dirty Plastic create editable groups.
They work without a bake; they do not reproduce mesh-aware edge wear or physical dust deposition.
Shelf thumbnails are Cycles renders of the actual presets, not illustrative swatches.

Older bundled portable smart-material files still demonstrate bake-driven generators; those
require their indicated mesh maps. Existing projects keep their authored layers when upgraded.

## Export conventions

| Preset | Files / encoding |
| --- | --- |
| Blender | BaseColor, Roughness, Metallic, Normal, Height, AO |
| Unreal Engine 5 | BaseColor, DirectX Normal (green inverted), ORM: R=AO, G=Roughness, B=Metallic |
| Unity Standard Metallic | BaseColor, OpenGL Normal, MetallicSmoothness: R=Metallic, G=B=0, A=1−Roughness; separate Occlusion |
| Custom | One RGBA image; assign scalar channels, smoothness, zero or one independently |

BaseColor is encoded as sRGB; other maps contain linear data. PNG/TIFF support 8 or 16 bits.
Export resolution overrides resample the evaluated textures. Files use
`{prefix}{texture_set}_{channel}{suffix}.{ext}` with filesystem-safe names. Existing files are
never overwritten; choose a new folder, prefix or suffix. In Unreal/Unity import packed maps as
linear data and normal maps using the corresponding engine's normal-map import setting.

## Persistence and safety

Project state lives in the scene. Managed images are packed on save, so the `.blend` is portable
and its material preview remains available with the extension disabled. Original materials and
unrelated user nodes are retained. Removing a texture set restores its assigned slots where possible;
it does not delete images, materials, meshes, or unrelated nodes. Texture-set copies are unassigned
until you explicitly assign new mesh slots. Use Blender's orphan cleanup when you want to reclaim
unused managed datablocks. Undo covers project edits; exported and library files are filesystem operations.

## Current limitations

- CPU evaluation; 2K is the practical default. Full six-channel rebuilds, texture-heavy stacks,
  triplanar rasterization and packing large projects can block Blender. Automatic updates yield between channels; an individual large channel can still take noticeable time. 4K/8K have high memory costs.
- Painting is one channel at a time. The native brush engine writes the layer image; the compositor
  updates the composite after stroke completion. Idle Open Painter sessions do not repeatedly hash the canvas. **Refresh** is available for externally changed textures.
- Tangent normal layers use normalized RGB blending, not reoriented normal-map blending.
- Curvature is an unsigned derivative approximation from tangent normals; UV seams can appear as edges.
  Dust uses tangent-normal Z, not world-up. Position Gradient, UDIM authoring and world normals are deferred.
- Triplanar uses the base mesh's UVs, world positions and interpolated vertex normals. Apply topology-changing
  modifiers first; after moving geometry, use Refresh. UVs outside the first tile and overlapping islands
  are not a supported authoring workflow.
- Automatic cages use a manual distance. Custom cage support is limited to one low-poly mesh with matching topology.
  Baking is synchronous and uses Cycles CPU for predictable behavior.
- Shared mesh datablocks require an explicit single-user copy. Material slot insertion/reordering after
  assignment can invalidate references; use Validate Project and reassign the affected slots.
- The selected editor area uses GPU-drawn docks around Blender’s native painting viewport. Dock controls call real project operators; detailed bake/export/color controls use native popovers. It follows Substance Painter’s standard layout but does not reproduce every Adobe feature. The original N-panel interface remains available in other workspaces. Groups support collapse, multi-selection and drag/drop. Detailed properties use native Blender popups.
- Smart-material import is limited to 512 MiB and 256 layers. External paint textures are bundled as EXR;
  missing generator maps must be baked in the destination texture set.

## Development

```sh
python3 scripts/build_extension.py
python3 scripts/run_tests.py --blender /path/to/blender
python3 scripts/run_tests.py --blender /path/to/blender --gui
```

See [ARCHITECTURE.md](ARCHITECTURE.md), [CONTRIBUTING.md](CONTRIBUTING.md),
[SMART_MATERIAL_FORMAT.md](SMART_MATERIAL_FORMAT.md), and [TESTING.md](TESTING.md).

## Screenshots

![Open Painter with Blender theme controls](docs/workspace.png)

![Mask stack and generator controls](docs/mask_controls.png)

See [CHANGELOG.md](CHANGELOG.md) for changes in 0.1.7.


