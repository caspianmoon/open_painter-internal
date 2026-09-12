# Smart masks and layer controls — 0.1.12

Reference: [Adobe Smart Materials and Masks](https://experienceleague.adobe.com/en/docs/substance-3d-painter/using/features/smart-materials-and-masks). Smart masks are editable mask-effect presets driven by mesh maps, distinct from smart materials.

Assets > Masks contains six original presets: Edge Clean, Edge Broken, Edge Broad, Cavity Dirt, Dirt Heavy, Dirt Fine. These append a generator to the selected layer or folder’s mask stack. Existing effects remain and combine using the existing mask rules. Edge presets require Curvature; dirt presets require AO and Curvature. Missing maps produce an actionable Bake Mesh Maps message before mutation. These are Open Painter presets, not Adobe’s proprietary asset collection. The current curvature baker remains a normal-image approximation.

Layer rows expose a compact blend selector and a 0–100 opacity display that can be dragged, cancelled with Escape, or edited numerically by double-click. Edits address the row’s stable layer ID and the selected stack channel, without changing the brush channel. The main dock no longer sends these actions to the generic Channel Blend popup.

Tests: smart_masks.py verifies distinct evaluated output, missing-map atomicity and correct opacity targeting. shelf_ui.py checks the actual drawn smart-mask assets, row controls and T/N. render_mask_previews.py bakes a corrugated high-poly sphere onto a UV sphere and renders the evaluated masks for thumbnails.
