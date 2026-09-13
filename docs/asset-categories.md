# Asset categories — 0.2.1

The Painter toolbar now exposes seven labeled native Blender asset-type buttons in both 3D and 2D. They filter the bottom shelf immediately. **All Types** shows their union. The **All** tab beside the shelf's hamburger menu is Blender's separate catalog selector; it does not change the Painter asset type. The same type selector is available under **Asset Library → Show**.

| Category | Contents and click behavior |
| --- | --- |
| Materials | Single-fill PBR materials such as Copper, Brushed Steel and Porcelain |
| Smart Materials | The 50 composed material recipes and the original grouped material presets |
| Smart Masks | Apply a mesh-aware preset to the selected layer's mask; required mesh maps are checked first |
| Filters | Add the selected filter to the active channel, or to the mask stack when mask properties are selected |
| Brushes | Activate a native texture-paint brush on the selected paint layer or painted mask |
| Alphas | Select an image-backed brush alpha, or No Alpha; Import Alpha packs an image into the project |
| Textures | Assign a procedural or imported image to a fill channel or mask; Import Texture packs a reusable image without changing existing source images |

Use the shelf's existing search field to search within the selected type. The refresh button discovers newly added local images/alphas, updates imported-image thumbnails and removes entries whose sources were deleted. Renaming a source reuses its existing shelf handle. Browsing categories does not invalidate the material texture stack.

Filter previews show an example input on the left and its filtered output on the right. Procedural previews use the actual texture evaluator. Brush thumbnails are stroke diagrams; imported images and alphas use their source pixels. These previews are prepared on library actions, never inside a draw or asset-poll callback.

Blender's built-in shelf catalog header is implemented in C and draws only its first registered header. Category buttons therefore use supported Python tool-header hooks instead of replacing that header or intercepting input. See Blender's [asset shelf implementation](https://github.com/blender/blender/blob/main/source/blender/editors/asset/intern/asset_shelf.cc) and [header layout implementation](https://github.com/blender/blender/blob/main/source/blender/editors/screen/area.cc). Unregistering Open Painter removes its hooks from both editors.

`tests/integration/asset_categories.py` verifies disjoint category membership, channel/mask activation, brushes/alphas, rename/delete behavior, browsing without texture invalidation, and repeated registration. The native UI acceptance test also checks category controls in both editors, actual shelf clicks and T/N behavior.
