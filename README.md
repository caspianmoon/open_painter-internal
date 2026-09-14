<!-- Active target: Blender add-on; native/ and prototypes/ are retained experiments. -->
# Blender Open Painter

A Blender extension for non-destructive PBR texturing: texture sets, paint and fill layers,
folders, masks, mesh-map baking, procedural generators, reusable smart materials, and texture export.
Python and Blender's bundled NumPy are the only runtime requirements. GPL-3.0-or-later.

Version **0.2.9** evaluates editable layers and masks through an internal Blender
material shader graph. Supported sliders update shader values immediately;
texture baking is reserved for export, explicit snapshots and isolated operations
that need pixels. Existing `LIVE` projects adopt this path without reapplying presets.
See the [architecture and diagnostics](docs/shader-preview-architecture.md).
The supplied 2048 project's authoring, save/reopen/export, and held Cavity Dirt
slider checks have passed. The visual check used an isolated native RNA sidebar;
it does not cover navigation through the product panels. See the linked report
for capture timing and remaining limits.

All 50 smart materials retain the generated wear masks and raised relief added in
0.2.6. See the [material review](docs/material-surface-review.html) and
[source/validation record](docs/material-surface-quality.md).

Exported Normal already includes Height. Use the separate Height map for
displacement, or omit it when using the combined normal; applying it through a
second bump node repeats the same relief.

**Feature target:** the supplied Substance Painter documentation dated June 26, 2018.
Full parity is not achieved. The [page-referenced feature inventory](PARITY.md) records working
subsets, missing workflows, external dependencies and acceptance criteria.

## Install

Tested on **Blender 5.2.1 LTS, macOS Apple Silicon**. The manifest requires Blender 5.2 or newer;
other platforms and newer versions have not been tested.

1. Download or build [`dist/open_painter-0.2.9.zip`](dist/open_painter-0.2.9.zip).
2. In Blender, open **Edit → Preferences → Add-ons**, open the menu at the top right,
   choose **Install from Disk**, and select the ZIP. Enable **Open Painter** if needed.
3. Click the **Editor Type** selector at the upper-left corner of any Blender editor—the menu
   containing 3D Viewport, Image Editor, Shader Editor, and the other editors.
4. Choose **Open Painter**. It opens in that same area with Assets along the bottom, the paint viewport
   in the center, and Texture Sets, Layers and Properties on the right. It does not create a workspace
   or rearrange your screen. Use Blender's usual **Ctrl+Space** to maximize the area if desired.
5. Use Blender’s Editor Type selector to switch to another editor.

The entry is a supported Python editor subtype adapter. Blender registers it through its NodeTree
subtype API; when selected, it mounts the native 3D/Image Editor painting backend in that area.
It is not a new compiled Blender SpaceType. The native editor selector remains available, and the
backend's selector highlight can read 3D Viewport or Image Editor while Open Painter is active.

The ZIP follows Blender's [extension packaging format](https://docs.blender.org/manual/en/latest/advanced/extensions/getting_started.html).
No online account, network connection, or separate Python installation is needed.

## Texture library

Version 0.2.0 adds all **50 requested smart materials**, **16 stackable effects**, **18 smart masks**,
**65 procedural sources** and **52 texture filters**. They form editable layer and mask recipes.
See the [v1 workflow and controls](docs/texture-workflow-v1.md) and the [material catalog with required maps](docs/material-catalog.md).

The native Painter toolbar has **Materials, Smart Materials, Smart Masks, Filters, Brushes, Alphas, and Textures** buttons that filter the bottom shelf. **All Types** clears that type filter. See [asset category controls](docs/asset-categories.md) for activation and import behavior.

## First use

1. Select your low-poly mesh. It needs a UV map in the 0–1 tile. A new texture set immediately creates and assigns a Principled material with all eight initialized texture images connected.
2. Click **+** in Texture Set List. A selected UV-unwrapped mesh is assigned automatically; use the link icon for additional meshes. For a mesh with multiple materials,
   choose the intended active material slot before assigning. Repeat for additional objects/slots.
3. Use the resolution dropdown in Texture Set List to choose resolution (default 2048).
4. Expand **Bake Mesh Maps** or open its top-bar popover. Select high-poly objects and click **Add Selected High Poly**.
   Set ray distance and bake Normal, AO, and Curvature. For self-bakes, disable **Bake from High Poly**.
5. Use the paint, fill and folder icons beside the stack channel selector to add layers.
   Select layer content by clicking its name; the adjacent mask icon selects its mask effects.
6. Click **Use Baked Normal + AO in Stack** to add those mesh maps to the material.
7. Select a Paint layer, choose its paint channel in Properties, then click **Paint**.
   Use Blender’s brush selector and native brush settings. The composite refreshes after the stroke ends.
   Selecting a fill, folder or non-painted mask ends the session; **Finish** also ends painting.
8. Click a material thumbnail in the bottom native Asset Shelf to apply it. The **Assets** selector in
   the tool header filters **Materials** and **Smart Masks**. Search and thumbnail size use Blender’s shelf controls.
   **Asset Library** in the sidebar contains portable smart-material import, save and library commands.
9. Expand **Export Textures**, choose a directory and an export preset, then export.

Layers are displayed top-first and evaluated bottom-first. Use disclosure arrows for folders and
native arrow buttons to reorder layers. The Parent button moves the selected layer into a folder;
Delete removes a folder and its descendants. Undo restores them. Folder duplication copies children
and their paint images. The native panel currently uses single selection and operator-based parenting.

**T** toggles Blender’s native left toolbar. **N** toggles the right sidebar containing Painter’s
Texture Set List, Layers and Properties. These are Blender’s standard shortcuts, with no custom
modal interception. Toggle **Asset Shelf** through Blender’s View menu. Resize the sidebar and shelf
using their native borders. No surrounding editor areas are rearranged.

The mask menu offers white/black painted masks, projected fill masks, generators, baked maps and
anchor points; anchors in mask or filter stacks feed Published Input masks, fill channel content and
generator micro details ([anchor points](docs/anchor-points.md)).
Select **Assets → Smart Masks**, select the target layer, then click a preset. Normal application
adds an editable effect; **Ctrl-click / Cmd-click** replaces the target’s effect stack. Required mesh
maps are checked before modifying the stack. The 18 bundled presets include edge, cavity, dust, rust, scratch and drip masks.

A mask on a **folder affects every child**, including the metal substrate in Painted Steel. For
chipped paint over metal, select **Paint coat** before applying a smart mask. Properties identifies
the target and exposes generator, invert, levels, strength and **Normal / Multiply / Add** combination.
New smart-mask effects use Normal; saved effects keep their previous combination.
Edge Wear uses baked geometric crease coverage; noise only breaks up that coverage. After upgrading from an older bake, click **Rebake Edge Map** in Edge Wear properties. **Minimum Edge Angle** determines which convex target-mesh creases qualify; **Edge Width (px)** determines their texture width. Re-bake after changing either; generator intensity does not change edge classification. **Bake Mask Stack to Texture** converts the selected layer’s mask effects into one packed, editable grayscale UV texture. The material node graph only displays channel textures; it does not implement edge-wear or grunge effects. This is an editable
mask-effect workflow, not complete Substance Painter generator parity.

Properties shows one selected material channel at a time, with native color/image and UV/triplanar transform controls. Layers has its own scrollable list; opacity is displayed as a percentage. The modeling toolbar starts hidden outside painting. Per-channel blend and
opacity are editable directly in layer rows, independently of the paint channel. **Use Layer Blend**
keeps the layer-wide setting. Blender draws the controls, handles numeric editing and scrolling,
and applies its current theme and interface scale.

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

Alongside the 50 new smart materials, the Assets shelf retains **Painted Steel, Dirty Plastic, Rough Wood, Brushed Steel, Copper,
Matte Rubber, Porcelain and Red Paint**. These original PBR presets contain coordinated color,
roughness and subtle height textures. Painted Steel and Dirty Plastic create editable groups.
They work without a bake; they do not reproduce mesh-aware edge wear or physical dust deposition.
Shelf thumbnails are Cycles renders of the actual presets, not illustrative swatches.

Older bundled portable smart-material files still demonstrate bake-driven generators; those
require their indicated mesh maps. Existing projects keep their authored layers when upgraded.

## Export conventions

| Preset | Files / encoding |
| --- | --- |
| Blender | BaseColor, Roughness, Metallic, Normal, Height, AO, Emission, Opacity |
| Unreal Engine 5 | BaseColor, DirectX Normal (green inverted), ORM: R=AO, G=Roughness, B=Metallic |
| Unity Standard Metallic | BaseColor, OpenGL Normal, MetallicSmoothness: R=Metallic, G=B=0, A=1−Roughness; separate Occlusion |
| Custom | One RGBA image; assign scalar channels, smoothness, zero or one independently |

BaseColor and Emission are encoded as sRGB; other maps contain linear data. PNG/TIFF support 8 or 16 bits.
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

- Shader Native is the default authoring mode. Supported numeric edits update existing bindings; legacy procedural fields and spatial filters can require isolated raster inputs. Initial graph construction and Blender's material redraw still have a cost. Native save preserves source images without baking output channels; export and explicit CPU Reference evaluate textures. See the [measured scope and remaining costs](docs/shader-preview-architecture.md); no universal viewport frame-time target is established.
- Painting is one channel at a time. The native brush engine writes the layer image, which the material shader samples. A native 2D composite is an explicit snapshot and requires an explicit refresh after edits. Finishing native painting keeps the authored source canvas visible. Idle Open Painter sessions do not repeatedly hash the canvas. **Refresh** is available for externally changed textures.
- Normal channels offer RNM, whiteout, additive and replacement blending. Select the intended normal blend mode explicitly; existing projects retain their saved mode.
- Curvature is a convex-crease map of the evaluated target mesh, filtered by Minimum Edge Angle. Smooth continuous curvature and high-poly-only normal detail are not transferred.
  Dust uses baked world-normal Z; moving or deforming geometry requires rebaking affected mesh maps. UDIM authoring remains unsupported.
- Triplanar uses the base mesh's world positions and interpolated vertex normals through the render UV layer. Apply topology-changing modifiers first; after moving geometry, use Refresh. UVs outside the first tile and overlapping islands
  are not a supported authoring workflow.
- Automatic cages use a manual distance. Custom cage support is limited to one low-poly mesh with matching topology.
  Baking is synchronous and uses Cycles CPU for predictable behavior.
- Shared mesh datablocks require an explicit single-user copy. Material slot insertion/reordering after
  assignment can invalidate references; use Validate Project and reassign the affected slots.
- Painter uses native Blender panels, properties, menus and an Asset Shelf, arranged around the native viewport. Asset activation is click-based; layer drag/drop and multi-selection are not implemented in this native interface. The original N-panel remains available outside Painter.
- Anchor points reference within one texture set and ignore projection and UV transforms; folder channels cannot be anchored. A missing anchor evaluates as neutral and is reported, not repaired.
- Smart-material import is limited to 512 MiB and 256 layers. External paint textures are bundled as EXR;
  missing generator maps must be baked in the destination texture set.

## Development

```sh
python3 scripts/build_extension.py
python3 scripts/run_tests.py --blender /path/to/blender
python3 scripts/run_tests.py --blender /path/to/blender --gui
python3 scripts/run_tests.py --blender /path/to/blender --gpu-only
```

See [ARCHITECTURE.md](ARCHITECTURE.md), [CONTRIBUTING.md](CONTRIBUTING.md),
[SMART_MATERIAL_FORMAT.md](SMART_MATERIAL_FORMAT.md), and [TESTING.md](TESTING.md).

## Screenshots

![Open Painter with native Blender controls](docs/native-painter.png)

![Mask stack and generator controls](docs/mask_controls.png)

See [CHANGELOG.md](CHANGELOG.md) for changes in 0.2.0.




## 0.1.9: bottom shelf, painting memory, and materials

The reference layout now places a compact tool strip on the left, contextual brush controls above the viewport, assets across the bottom, and texture sets/layers/properties on the right. Controls continue reading Blender's current theme and font settings. That custom overlay was replaced by native regions in 0.1.13; T now toggles the native toolbar.

The library bundles CC0 Poly Haven maps for Rough Wood, Fine Wood, and Weathered Paint. Base color uses sRGB; roughness and OpenGL normal maps use Non-Color. Applied maps are packed into the project. The other original presets have regenerated material previews; Copper and Dirty Plastic were adjusted. No material downloads occur during use. See [research and sources](docs/research/improvements-0.1.9.md).

Paint detection reuses raster buffers and bounds temporary comparison storage. Rectangle and tool geometry are cached between UI redraws. These changes reduce allocations; they do not eliminate Python's full image upload cost or establish a guaranteed painting frame rate.
