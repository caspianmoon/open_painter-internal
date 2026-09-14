# Current revision

The notes below describe the earlier authoring pass. The replacement uses
newly generated relief sources and coating masks; see
[the current review](material-surface-review.html) and
[the implementation record](material-surface-quality.md).

# Individual material review: wood, stone, leather, fabric and panels

Entries 33–50 were inspected individually on the same curved mesh as entries
1–32. The first comparison uses 1024² authored textures, 512² renders and 16
Cycles samples; the final library check uses 2048² textures and 32 samples.
The combined validation report records the completed runs and remaining limits.

| # | Material | Observed defect | Individual correction |
|---|---|---|---|
| 33 | Fresh Oak | Separate pore and fiber noise made soft unrelated stains over weak grain. | Select vessel pores and roughness directly from the same wood image as the base grain; use shallow relief and restrained color variation. |
| 34 | Old Worn Wood | Broad dents and many pale cuts obscured the timber direction. | Coherent longitudinal pores, sparse recessed abrasion, smaller isolated dents, roughness-only edge polishing and localized dirt. |
| 35 | Painted Wood | Uniform paint hid the timber; scattered ticks did not read as chipped coating. | Retain wood-grain relief under the paint, use ragged chipped boundaries and separately controlled edge loss, with single-mask shallow scoring. |
| 36 | Weathered Outdoor Wood | Large dark blobs and polygon cracks broke the grain direction. | Gray longitudinal fibers, stretched interrupted splits, restrained runoff and darker weathered timber color. |
| 37 | Burnt Wood | Brown clouds, huge cracks and regular ash dots did not look carbonized. | Dark carbonized timber bed, fine aligned grain, interrupted charcoal fissures, restrained heat-browned areas and sparse top-facing ash. |
| 38 | Oiled Dark Wood | Unrelated dark pores and large fingerprints looked painted on. | Reuse grain for vessel pores; keep handling marks in roughness, reduce print size/strength and preserve shallow oiled grain. |
| 39 | Rough Concrete | Broad soft aggregate and unrelated round dots looked synthetic. | Fine cement grain and actual pore-image coverage; darkest image pits define air pockets instead of regular procedural dots. |
| 40 | Old Dirty Concrete | Oversized cracks and broad gray stains obscured the concrete. | Shallow correlated pores, interrupted fine fissures, restrained runoff and dirt concentrated in baked cavities. |
| 41 | Painted Concrete | Soft exposed patches lacked actual delamination boundaries. | Sharp irregular peeling reveals textured cement; use fine recessed cracks and shallow paint texture instead of swollen relief. |
| 42 | Polished Marble | Unrelated warm masks made cloudy patches; too much relief disturbed polish. | Mineral wisps share the vein image, near-flat height preserves polished reflection, and micro-abrasion changes roughness only. |
| 43 | Granite | Independent feldspar, mica and quartz noise overlapped incoherently. | Select all mineral populations from one shared mineral image with independent levels and roughness; reduce relief. |
| 44 | Weathered Stone | Broad cellular facets looked like inflated polygon plates. | Fine mineral substrate and real pore coverage, shallow erosion, localized moisture and fine cavity moss. |
| 45 | Worn Leather | Giant cell cracks and unrelated dark blobs overwhelmed the fine leather grain. | Remove the macro fracture web; creases share the leather image, shallow grain remains visible, rub marks are subtle and high-point polish changes roughness. |
| 46 | Cracked Old Leather | Huge polygon plates and brown camouflage replaced hide structure. | Fine grain plus sparse open branching fissures with constant recessed depth; select fading from grain and keep dark stains in cavities. |
| 47 | Denim Fabric | Bright random dash strokes covered the fabric, while independent pale threads broke the weave. | Pale threads share the woven source, abrasion is sparse, fiber relief is shallow and fading follows high points. |
| 48 | Canvas Fabric | Broad stains and unrelated fiber relief muddied the coarse weave. | Raised threads share the weave, stains remain low strength, and frayed color follows thread coverage at mesh edges. |
| 49 | Sci-Fi Panel Surface | Dense white scratches, random patchwork and a continuous bright outline dominated panels. | Fine alloy/coating grain, recessed service marks, subtle recess variation and restrained broken exposed edges. |
| 50 | Post-Apocalyptic Scrap Metal | Oversized cracks, giant paint blobs and exposed metallic speckling overwhelmed the layers. | Dark oxidized substrate, granular rust, two smaller sharply fractured paint generations, localized gouges, runoff, dirt and dust. |

These are revised reusable texture recipes, not measured scans. Pattern scale
remains editable in UV space: arbitrary meshes have different texel densities
and cannot share one physically calibrated pore size without scale information.
Existing saved instances retain their authored layers. Reapply the library
preset to use the new defaults; installing an update does not overwrite artwork.

The workflow follows Adobe's separation of [grayscale masks and effects](https://experienceleague.adobe.com/en/docs/substance-3d-painter/using/interface/layer-stack/masking-and-effects)
and [mesh-aware wear](https://experienceleague.adobe.com/en/docs/substance-3d-painter/using/effects/generators/metal-edge-wear).
Algorithms and source assets remain original to Open Painter; no Adobe library
assets are bundled.
