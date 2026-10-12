# User guide

A tour of the main workflow, the export presets and how projects are stored. Feature pages are linked from the [README](../README.md).

## First use

1. Select your low-poly mesh. It needs a UV map in the 0–1 tile. A new texture set immediately creates and assigns a Principled material with all eight initialized texture images connected.
2. Click **+** in the **Texture Set** panel. A selected UV-unwrapped mesh is assigned automatically; the panel
   names the assigned objects, and **Texture Set Settings › Assign Selected** links additional meshes. For a mesh
   with multiple materials, choose the intended active material slot before assigning. Repeat for additional objects/slots.
3. Use the resolution dropdown in the Texture Set panel to choose resolution (default 2048).
4. Click **Bake Mesh Maps** in the tool header to open the **Bake** tab (Setup, Maps, Results). Under Setup, select
   high-poly objects and click **Add Selected**, then set the ray distance. Maps shows every map's real state
   (Missing, Up to Date, Out of Date, Baking, Failed or Not Checked); **Select** picks the maps the material reads,
   **Bake Selected Maps** bakes them. **Bake from High Poly** is off by default; enable it for projection from the selected sources. Previous maps stay until a
   replacement lands, and **Stop After This Map** ends the queue honestly.
5. Use **Add Layer** (Paint, Fill, Folder, Decal, Smart Material, Module), **Add Mask**, **Add Effect** and **More**
   above the layer list. Select layer content by clicking its name; the mask icon beside it selects its mask stack.
6. Click **Add Baked Normal and AO Layer** under Bake › Results to add those mesh maps to the material.
7. Select a Paint layer. **Paint Material** in the **Inspector** paints the enabled PBR channels
   (Color, Roughness, Metallic, Height, …) with one continuous stroke that shows on the model while
   you drag; the destination line says which set, layer and channels receive it, and the stroke stays
   editable as data. Esc discards a stroke in progress and exits when idle. For single-channel pixel
   painting choose the paint destination and click **Paint** (Blender’s brush selector and native
   brush settings); the composite refreshes after the stroke ends. Selecting a fill, folder or
   non-painted mask ends the session; **Finish** also ends painting.
8. Click a material thumbnail in the bottom native Asset Shelf to select it: the **Assets** panel names it,
   lists what it needs and the exact target, and its **Apply** button (also in the tool header) applies it.
   Clicking the same thumbnail again applies too. The type filter beside the **Assets** menu in the tool header narrows the shelf
   (**Materials**, **Smart Materials**, **Smart Masks**, **Filters**, **Brushes**, **Alphas**, **Textures**,
   **Geometry Effects**); search and thumbnail size use Blender’s shelf controls. The panel's **Library** sub-panel
   holds portable smart-material import, save and library commands.
9. Click **Export** in the tool header to open the **Export** tab: choose Contents, Scope, a destination preset
   and folder, read the Output Preview, then export.

Layers are displayed top-first and evaluated bottom-first. Press a row to select its content and
drag it up or down to move the layer (a folder moves with its contents); move the mouse sideways
while dragging to change how deeply the layer nests, and press Esc to put it back. The arrow
buttons under the list move one step at a time, and the Parent button moves the selected layer into
a chosen folder. Each row shows the blend mode and opacity of the channel selected above the list;
double-click a name to rename it. Delete removes a folder and its descendants. Undo restores them.
Folder duplication copies children and their paint images. The native panel uses single selection.

**T** toggles Blender’s native left toolbar. **N** toggles the right sidebar containing Painter’s
Texture Set, Layers, Inspector and Assets panels, and the Bake and Export tabs. These are Blender’s standard
shortcuts, with no custom modal interception. **More › Open Inspector** shows the full inspector in a Properties
area beside the viewport. Toggle **Asset Shelf** through Blender’s View menu. Resize the sidebar and shelf
using their native borders. No surrounding editor areas are rearranged.

The mask menu offers white/black painted masks, projected fill masks, generators, baked maps and
anchor points; anchors in mask or filter stacks feed Published Input masks, fill channel content and
generator micro details ([anchor points](anchor-points.md)).
Select **Assets → Smart Masks**, select the target layer, select a preset and click **Apply**. Application
adds an editable effect on top of the existing masks; **Replace Masks with This** in the asset's right-click
menu (and the Assets panel) is the separate, visible replacement. Required mesh maps are checked before
modifying the stack. The 18 bundled presets include edge, cavity, dust, rust, scratch and drip masks.

A mask on a **folder affects every child**, including the metal substrate in Painted Steel. For
chipped paint over metal, select **Paint coat** before applying a smart mask. The Inspector identifies
the target and exposes generator, invert, levels, strength and **Normal / Multiply / Add** combination.
New smart-mask effects use Normal; saved effects keep their previous combination.
Edge Wear uses baked geometric crease coverage; noise only breaks up that coverage. After upgrading from an older bake, click **Rebake Edge Map** in Edge Wear properties. **Minimum Edge Angle** determines which convex target-mesh creases qualify; **Edge Width (px)** determines their texture width. Re-bake after changing either; generator intensity does not change edge classification. **Bake Mask Stack to Texture** converts the selected layer’s mask effects into one packed, editable grayscale UV texture. The material node graph only displays channel textures; it does not implement edge-wear or grunge effects. This is an editable
mask-effect workflow, not complete Substance Painter generator parity.

The Inspector shows the selected item's own controls first — a smart material's master controls (every built-in material has 4–8 authored ones, see [master controls](master-controls.md)), a fill's channel content with native color/image and UV/triplanar transform controls, a paint layer's destination, a mask's settings — and publishing, module, cache and profiling controls after them. Layers has its own scrollable list; opacity is displayed as a percentage. The modeling toolbar starts hidden outside painting. The selected layer's blend and
opacity for the display channel sit in the row above the list (every row shows them with the **Blend and Opacity in Layer Rows** preference), independently of the paint destination. **Use Layer Blend**
keeps the layer-wide setting. Blender draws the controls, handles numeric editing and scrolling,
and applies its current theme and interface scale.

Brush Properties exposes color or scalar value, size, strength, spacing, erase and alpha controls.
Scroll the Inspector to reach all controls. **Import Alpha** loads a grayscale image and packs
it into the blend. Alpha thumbnails show the imported image; **Clear Alpha** removes the brush mask.
Bundled brushes are copied to local brush assets so imported textures work and persist without
modifying Blender's bundled library. For painted masks, **Black · Hide** and **White · Reveal**
set the brush value directly.

The Inspector's mask section provides mask selection/reorder/delete, enable/invert, strength, levels,
and generator controls, with the selected mask's settings in their own sub-panel; **View Mask** shows the
mask alone in the viewport and **Back to Material** returns. **Texture Set Settings** exposes renaming,
evaluation, assignment, repair and validation. **Assets › Library** holds smart-material
apply/import/save/delete actions.

3D painting uses a temporary face-selection mask to protect material slots outside the current
texture set. The previous face selection and mask setting are restored when painting ends, including
after save/reload. 2D painting edits the selected layer image directly; finishing changes the 2D editor
to view mode so it cannot paint into the evaluated output. Export and save flush pending paint changes.

## Export conventions

| Preset | Files / encoding |
| --- | --- |
| Blender | BaseColor, Roughness, Metallic, Normal, Height, AO, Emission, Opacity |
| Unreal Engine 5 | BaseColor, DirectX Normal (green inverted), ORM: R=AO, G=Roughness, B=Metallic |
| Unity Standard Metallic | BaseColor, OpenGL Normal, MetallicSmoothness: R=Metallic, G=B=0, A=1−Roughness; separate Occlusion |
| Custom | One RGBA image; assign scalar channels, smoothness, zero or one independently |

BaseColor and Emission are encoded as sRGB; other maps contain linear data. PNG/TIFF support 8 or 16 bits.
Export resolution overrides resample the evaluated textures over the texels the geometry covers.
Every map is extended into its UV gutters from the nearest valid texel (**UV Padding**: Automatic
4–64 px by resolution, Custom, Fill Empty Space, or None; validity comes from the export geometry,
never from pixel values), and the Export view reports islands whose gutters meet. Files use
`{prefix}{texture_set}_{channel}{suffix}.{ext}` with filesystem-safe names. An export is published
as one transaction: existing files are only replaced when you allow it, and then all of a request's
maps, manifests and mesh land together or the originals are restored (a journal beside the outputs
recovers an interrupted export). In Unreal/Unity import packed maps as linear data and normal maps
using the corresponding engine's normal-map import setting ([engine notes](../engine_validation/README.md)).

## Persistence and safety

Project state lives in the scene. Managed images are packed on save, so the `.blend` is portable
and its material preview remains available with the extension disabled. Original materials and
unrelated user nodes are retained. Removing a texture set restores its assigned slots where possible;
it does not delete images, materials, meshes, or unrelated nodes. Texture-set copies are unassigned
until you explicitly assign new mesh slots. Use Blender's orphan cleanup when you want to reclaim
unused managed datablocks. Undo covers project edits; exported and library files are filesystem operations.
