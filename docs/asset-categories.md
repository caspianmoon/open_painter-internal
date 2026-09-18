# Asset categories

## Since 0.11.0: catalog tabs, select then apply

The shelf library assigns every built-in entry (and every local handle) to an asset catalog per
category — **Materials · Smart Materials · Smart Masks · Filters · Brushes · Alphas · Textures ·
Geometry** — so the shelf's own header can show them as tabs beside its search field. Blender
exposes no API to enable catalog tabs, so the artist ticks the categories once in the shelf's ☰
catalog menu; the choice persists in the preferences (`STORE_ENABLED_CATALOGS_IN_PREFERENCES`).
The former seven-button tool-header strip is gone: the tool header keeps one **Assets** menu
(Import Smart Material / Texture / Alpha, Save Set as Smart Material, Save Folder as Module, Delete
Local Material, Refresh Library) and one type dropdown (`asset_category`, also in the *Assets*
sidebar panel › *Library*), which filters the shelf together with the tabs.

A click on a shelf asset **selects** it: the *Assets* panel shows its name, type, the mesh maps it
still needs and the exact target (*Adds an editable group to Body*, *Adds to the mask stack of
Blue paint coat*, *Applies to Base › Roughness*, *Adds pieces to Cube*), with **Apply** and, for
smart masks, **Replace Masks**. A second click on the same asset, the Apply button in the tool
header or the shelf's right-click menu applies it. Browsing never changes the material; one
application is one undo step. Replacing masks is its own named action — no modifier key.

Application rules: a material or smart material adds a named, editable group to the active set
(saved `.openpaintmaterial` documents too); a smart mask joins the selected layer's or folder's mask
stack; a filter, procedural or texture goes to the inspected channel of a fill layer, or to the mask
stack when the mask is selected; a geometry preset opens its dialog for the selected object; brushes
and alphas set the tool. `tests/integration/asset_workflow.py` covers these rules.

## 0.2.1

The Painter toolbar exposed seven labeled native Blender asset-type buttons in both 3D and 2D. They filtered the bottom shelf immediately. **All Types** showed their union. The same type selector was available under **Asset Library → Show**.

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
