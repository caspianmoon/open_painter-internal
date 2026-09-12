# Native UI and mask effects — 0.1.13

The active interface uses Blender Panel, UILayout, UIList and AssetShelf APIs. There is no GPU dock draw handler or modal dock input operator. The existing editor-subtype adapter is retained; no compiled SpaceType or workspace replacement is introduced.

Assets sit below the viewport. Texture Set List, Layers and Properties use the native right sidebar. T/N retain native toolbar/sidebar behavior. Both 3D and 2D show Painter controls. Imported/saved smart materials remain accessible from Asset Library. Native layer selection is single-select; activation is click-based rather than Substance drag/drop.

## Mask behavior

Adobe documents smart masks as mask-effect presets, accumulation when adding multiple presets, and Ctrl-drop replacement: [Smart Materials and Masks](https://experienceleague.adobe.com/en/docs/substance-3d-painter/using/features/smart-materials-and-masks). Painter implements click/Ctrl-or-Cmd-click for the corresponding add/replace actions in Blender's shelf. New effects have an editable Normal combination, while previous effects retain Multiply/Add. This default is an implementation choice, not a claim about every Adobe preset.

The reported material disappearance happens immediately on applying a mask. A mask targeting the Painted Steel folder also masks the substrate; that can expose the default background material. The native interface names the target and distinguishes layer content from masks. Select Paint coat to keep the substrate visible. This does not establish that every reported gray-material case has the same cause. Curvature remains the documented tangent-normal approximation; this release does not replace the baker or claim full Adobe parity.

## API verification

[Blender AssetShelf API](https://docs.blender.org/api/5.0/bpy.types.AssetShelf.html) and the installed Blender 5.2.1 RNA/source were used to verify registration and activation. Asset-library enum values are explicit. The activation operator resolves local asset identifiers when WINDOW context has no active asset. Preview Images are catalog handles; activation routes through the material/mask operators, never through direct image assignment. Registered built-in panels are not patched. Category focus runs only after Blender has created the sidebar categories.

## Validation

Blender 5.2.1 LTS, macOS Apple Silicon:
- native_ui.py: native Panel drawing, no GPU overlay handlers/modal interceptor, one real shelf click adds one generator, T/N hide native regions, 3D→2D→3D retains layers/masks.
- smart_masks.py: map preflight, distinct outputs, Normal and Multiply composition, explicit replacement and save/reopen persistence.
- projected_masks.py: triplanar continuity and render UV selection.
- phase13.py: materials and grouping behavior.
- phase14.py: incremental evaluation equals full evaluation.

No new performance benchmark or guaranteed real-time claim is made. Screenshot: ../native-painter.png.
