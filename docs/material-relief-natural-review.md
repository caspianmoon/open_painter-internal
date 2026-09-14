# Natural and hero material relief review

This revision uses new AI-generated inputs throughout the library. Five serve the natural materials: `timber_relief_v2`, `charred_timber_v2`, `leather_relief_v2`, `denim_twill_v2`, and `cement_relief_v2`. Natural and hero recipes also use the new shared abrasion, paint-loss and rust inputs. Original built-in `image_gen` output pixels are preserved at their actual 1254 × 1254 resolution; prompts requested 2048 × 2048. These are authored scalar inputs, not measured scans or guaranteed seamless maps. [Natural prompts and hashes](research/natural-relief-prompts.json) preserve the exact generation record.

The 18 recipes below have stronger material-specific height detail. Raised coating films and recessed losses share their masks across color and height. Isotropic stone, hide and chipped paint use triplanar projection. Directional timber and cloth follow editable UVs, including the latitude/longitude preview sphere's visible pole convergence.

## Intended final behavior

These descriptions state recipe intent. They are not claims that every final render has been reviewed.

| Material | Intended behavior |
|---|---|
| Fresh Oak | Open vessels and bending fibers carry shallow directional relief. |
| Old Worn Wood | Deeper open pores, rubbed edges and restrained abrasion distinguish aged timber. |
| Painted Wood | Raised blue paint retains wood grain and reveals lower timber through irregular chips. |
| Weathered Outdoor Wood | Bleached fiber crowns stand above open dry vessel beds. |
| Burnt Wood | A dedicated carbonized source creates interrupted longitudinal fissures and crumbling ridges. |
| Oiled Dark Wood | Polished fiber crowns retain lower vessel pores and subtle finish abrasion. |
| Rough Concrete | Generated cement grit and open air pockets provide granular relief. |
| Old Dirty Concrete | Deeper spalls remain visible beneath stains and recess dirt. |
| Painted Concrete | Raised wall paint follows cement texture above exposed rough substrate. |
| Polished Marble | Mineral veins stay nearly level beneath subtle finish abrasion. |
| Granite | Shared mineral masks coordinate feldspar, mica and quartz with slight grain relief. |
| Weathered Stone | Eroded cement-like geometry sits beneath mineral coloring and localized moss. |
| Worn Leather | Rounded grain and compressed creases interrupt leather highlights. |
| Cracked Old Leather | Larger dry grain and lower crease beds distinguish aged hide. |
| Denim Fabric | Dedicated diagonal twill carries fine rounded yarn relief. |
| Canvas Fabric | Plain weave retains smaller raised yarns and recessed crossings. |
| Sci-Fi Panel Surface | Raised coating exposes lower alloy, with recessed service abrasion. |
| Post-Apocalyptic Scrap Metal | Pitted steel and granular rust sit beneath distinct yellow and blue coating levels. |

## Inspected focused previews

Seven representative materials were visually inspected using Blender 5.2.1 LTS, a curved preview mesh, 2048px composed textures, 768px renders and 32 Cycles samples. Height and combined-normal map statistics were checked, and each recorded recipe matches the current catalog. The material source hashes are recorded in the corresponding `render-context.json`.

| Material | Reviewed snapshot | Actual observation |
|---|---|---|
| Fresh Oak | `.blender-test/material-relief/natural` | Vessel pores are visibly recessed; directional grain converges at the sphere pole. |
| Rough Concrete | `.blender-test/material-relief/natural-refined` | Granular roughness and small pits are visible; the cement remains mostly uniform in color. |
| Cracked Old Leather | `.blender-test/material-relief/natural-refined` | Rounded grain and broader irregular creases respond to lighting; no broad polygon crack plates. |
| Denim Fabric | `.blender-test/material-relief/natural-refined` | Fine diagonal yarn ridges remain visible after reducing the initial oversized relief. |
| Canvas Fabric | `.blender-test/material-relief/natural-refined` | Plain weave relief is finer after reducing height and increasing repeat scale. |
| Burnt Wood | `.blender-test/material-relief/natural-refined` | Deep longitudinal fissures separate carbonized ridges; UV pole convergence remains visible. |
| Post-Apocalyptic Scrap Metal | `.blender-test/material-relief/natural` | Crisp yellow/blue chips expose granular corrosion without the previous zebra-like pole bands. |

These seven final focused snapshots have nonconstant height ranges of 0.0481–0.2691 and combined-normal XY RMS values of 0.1452–0.3210. Those values establish usable relief, not physical calibration or artistic quality by themselves. [Focused review details](research/natural-relief-review.md) retain the tuning and render record.

## Final matched gallery review

All 18 natural and hero entries were visually inspected from the completed 512px final run using the same curved mesh, 2048px composed textures, 32 samples, camera and lighting. Two weak distinctions were corrected and rerendered at those settings: Old Dirty Concrete initially looked too clean, and Weathered Stone initially looked too similar to fine cement. The accepted replacements are in `.blender-test/material-relief/natural-final-concrete` and `.blender-test/material-relief/natural-final`, respectively.

| Material | Observed final behavior |
|---|---|
| Fresh Oak | Open vessel pores and shallow bending fibers remain visible. |
| Old Worn Wood | Darker, deeper vessel relief distinguishes it from fresh oak. |
| Painted Wood | Crisp blue coating losses expose tan timber grain without zebra-like damage bands. |
| Weathered Outdoor Wood | Bleached gray fibers and open grain read differently from fresh timber. |
| Burnt Wood | Fractured carbonized ridges have deep interrupted longitudinal fissures. |
| Oiled Dark Wood | Strong polished/oiled highlights are visibly broken by lower vessel pores. |
| Rough Concrete | Fine cement grit and small pits remain visible with mostly uniform cement coloring. |
| Old Dirty Concrete | The accepted correction has broad darker mineral grime while preserving cement pores. |
| Painted Concrete | Crisp off-white film losses reveal lower gray cement. |
| Polished Marble | Fine mineral veins stay nearly level beneath smooth polished reflections. |
| Granite | Mineral speckle and subtle grain relief remain distinct. |
| Weathered Stone | The accepted correction has larger eroded features and stronger mineral variation than fine cement. |
| Worn Leather | Fine grain interrupts highlights; groove wear is restrained. |
| Cracked Old Leather | Larger irregular creases and dry grain distinguish it from smoother worn leather. |
| Denim Fabric | Fine diagonal yarn ridges remain visible at the reduced relief strength. |
| Canvas Fabric | Fine plain weave remains visible at the reduced yarn height. |
| Sci-Fi Panel Surface | Fine coating grain is restrained; exposed alloy forms a bright rim at the actual groove edge. |
| Post-Apocalyptic Scrap Metal | Yellow paint, sparse blue repair remnants and granular corrosion share clear chip boundaries. |

Directional timber and cloth still show the sphere's UV pole convergence. Replacement renders match the geometry, camera, lighting and AO/curvature/position bake hashes; raw UV and normal hashes vary across fresh Blender sessions, so the record does not claim every baked byte is identical. [The final inspection record](research/natural-final-review.json) contains all 18 observations, current recipe hashes, source hashes, settings and height/normal statistics.
