# Smart material catalog

Open Painter includes 50 authored smart material compositions with editable layers. Recipes are original data stored in `src/open_painter/smart_materials/catalog.py`. They compose texture fills, texture procedurals, and grayscale masks; they do not build shader effect graphs. The output material displays the resulting Base Color, Roughness, Metallic and Height textures.

Version 0.2.6 adds 13 generated wear/relief inputs and revises all 50 materials,
including image-based scratches, chipped coatings, welds, blisters and rubber
fissures. Height now contributes to exported normals. See the
[current material review and validation](material-surface-quality.md).

The earlier version 0.2.5 added 15 generated scalar surface-detail sources across all 50
materials. They supply fine grain, pores, mineral structures and handling marks;
color, roughness, height and mesh-aware masks remain editable. Existing material
instances in saved projects are preserved; apply an updated library recipe to
use its new inputs. See [generated material quality and validation](generated-material-quality.md).

A material is inserted as an isolated folder above the selected layer. Its children are stored from substrate to topmost effect, and the library selects the folder after insertion. Coating-loss masks belong to coating layers: hiding paint exposes the underlying metal or timber. A mask on the whole folder deliberately affects its substrate too.

Heavy Chipped Paint, Abandoned Machinery, Painted Concrete and Post-Apocalyptic Scrap Metal combine edge loss with a second editable, high-contrast breakup mask on the paint layer. This exposes substrate across damaged flat areas without making the edge-wear generator itself invent geometric edges there. Mask operations remain visible and editable in the layer's mask stack.

Missing mesh maps are checked before adding anything. Baking them is an explicit operation. A failed composition restores the existing layers, selection, output dirty flags and display surface settings. Recipes do not substitute noise for missing geometry information.

## Material recipes

The map requirements below include every mesh-aware effect in the recipe. Materials marked “None” use surface texture patterns and can be applied before mesh-map baking. Each mask amount, procedural scale, channel value, channel enable switch and layer remains editable.

| Material | Layers | Required maps | Authored surface and effects |
| --- | ---: | --- | --- |
| Clean Brushed Steel | 2 | None | Fine parallel brushing with sparse cross scratches and adjustable roughness. |
| Worn Brushed Steel | 5 | AO, Curvature | Brushed grain, exposed polished edges, handling marks and recess grime. |
| Machined Aluminum | 2 | None | Bright aluminum with fine cutter passes and a configurable anisotropic response. |
| Scratched Aluminum | 4 | Curvature | Random intersecting scratches over machined aluminum, with impact pits and polished creases. |
| Cast Iron | 4 | AO, Curvature | Coarse casting grain and tiny pits under a dark oxide skin, with exposed metal on creases. |
| Raw Industrial Steel | 4 | AO | Rolling marks, patchy mill scale and slight brown manufacturing discoloration. |
| Polished Chrome | 4 | None | Near mirror chromium with sparse fine scratches, soft wipe marks and fingerprints. |
| Old Brass | 5 | AO, Curvature | Warm brass with dull tarnish, scratches and deep dark deposits in recesses. |
| Antique Bronze | 5 | AO, Curvature | Aged bronze, brown tarnish and green patina restricted to sheltered cavities. |
| Oxidized Copper | 5 | AO, Position | Bare copper beneath granular green oxidation, darker oxide and rain washed streaks. |
| Worn Painted Metal | 5 | AO, Curvature | Steel below orange peel paint, edge chips, fine scratches and cavity dirt. |
| Heavy Chipped Paint | 6 | AO, Curvature | Broad paint loss reveals pitted steel and corrosion beneath ragged surviving flakes. |
| Factory Powder-Coated Metal | 3 | Curvature | Fine textured powder coating, restrained handling scratches and slight crease exposure. |
| Military Painted Steel | 6 | AO, Curvature, World Normal | Matte olive coating, rubbed metal creases, dry scratches, earth and corrosion. |
| Old Enamel Metal | 5 | AO, Curvature | Faded cream enamel with crazing, dark cracks, worn edges and thin surface scratches. |
| Rusting Painted Steel | 6 | AO, Curvature, Position | Layered red paint over corroding steel, exposed chips, rust blooms and downward leakage. |
| Automotive Painted Metal | 5 | None | Primer, saturated base coat, clearcoat response and subtle wash swirls with sparse scoring. |
| Burned Painted Metal | 5 | Curvature, World Normal | Blistered paint with charred zones, exposed heat-darkened steel and powdery soot. |
| Heavy Rust | 5 | AO | Thick black oxide below broken rust scales, orange corrosion and pitted cavity deposits. |
| Surface Rust | 3 | AO | Thin irregular orange oxidation on otherwise intact rolled steel. |
| Wet Rust | 4 | AO | Dark saturated rust with rough dry scales, low roughness moisture and glossy crevices. |
| Galvanized Steel | 3 | None | Faceted zinc spangle texture, thin white oxidation and fine manufacturing scratches. |
| Welded Steel | 5 | AO | Industrial steel with local weld-like bands, heat tint, oxidized seams and soot. |
| Heat-Treated Metal | 5 | None | Authored gold, brown, violet and blue temper bands over brushed metal. |
| Greasy Machinery | 6 | AO, Position | Machined steel with dark cavity grease, thin oil, scratches and oil leakage. |
| Abandoned Machinery | 7 | AO, Curvature, Position, World Normal | Peeling industrial paint over rust, with oil leakage, dense cavity grime and top dust. |
| Injection-Molded Plastic | 3 | None | Fine satin plastic grain, broad mold variation and small molding imperfections. |
| Worn Hard Plastic | 5 | AO, Curvature | Hard plastic with fine grain, polished creases, pale scoring, oily handling marks and dirt. |
| Soft-Touch Plastic | 3 | None | Dark matte rubberized coating with velvety grain, rubbed areas and absorbed handling oils. |
| Sun-Damaged Plastic | 4 | World Normal | Patchy bleached plastic with dry cracking, chalky grain and exposure-driven fading. |
| Dirty Rubber | 4 | AO, Curvature | Matte rubber grain, shallow scoring, rubbed ridges and embedded earth in cavities. |
| Cracked Old Rubber | 4 | AO | Faded dry rubber with branching cracks, fine grain and dusty crevices. |
| Fresh Oak | 3 | None | Warm natural oak with long grain, darker vessel pores and restrained growth variation. |
| Old Worn Wood | 6 | AO, Curvature | Dark aged timber grain, deep pores, dents, scratches and rubbed edges. |
| Painted Wood | 5 | AO, Curvature | Blue paint following timber grain, with chips exposing warm wood and darker recess dirt. |
| Weathered Outdoor Wood | 5 | AO, Position, World Normal | Gray bleached timber, raised fibers, long cracks, moisture staining and crevice dirt. |
| Burnt Wood | 4 | World Normal | Warm timber through heat-darkened transitions into cracked charcoal and fine ash. |
| Oiled Dark Wood | 4 | None | Deep brown polished timber, dark pores, rubbed oil finish and sparse fine handling scratches. |
| Rough Concrete | 4 | None | Cement with multiscale aggregate, fine pores and isolated deep air pockets. |
| Old Dirty Concrete | 5 | AO, Position | Rough old concrete with aggregate, dry cracks, water runoff and dark accumulated grime. |
| Painted Concrete | 5 | AO, Curvature | Off-white wall paint over coarse concrete with hairline cracks, peeling and grime. |
| Polished Marble | 3 | None | Pale polished stone with warped dark veins, fine mineral variation and tiny polish scratches. |
| Granite | 4 | None | Interlocking mineral grains, pale feldspar, dark mica and isolated quartz with editable roughness. |
| Weathered Stone | 5 | AO, Position | Eroded mineral surface with pitting, dark cavities, runoff and an independently removable moss layer. |
| Worn Leather | 5 | AO, Curvature | Warm leather grain with creases, fine scoring, dark recesses and polished raised edges. |
| Cracked Old Leather | 5 | AO, Curvature | Dry fissured leather with faded grain, deep stains and severe pale edge abrasion. |
| Denim Fabric | 5 | AO, Curvature | Indigo directional weave, pale cross threads, worn raised areas and embedded dirt. |
| Canvas Fabric | 5 | AO, Curvature | Coarse neutral woven canvas with fiber roughness, stained folds and fraying exposed edges. |
| Sci-Fi Panel Surface | 6 | AO, Curvature | Coated panel substrate with contrasting repairs, edge wear, scratches and recess grime; add decals above the folder. |
| Post-Apocalyptic Scrap Metal | 8 | AO, Curvature, Position, World Normal | Pitted steel, deep rust and two mismatched paint generations under repairs, gouges, oil and dust. |

## Reusable effects

Sixteen effects insert one independently masked fill layer: Rust, Dirt, Dust, Edge Wear, Chipping, Scratches, Grease, Water Stains, Edge Polish, Oil, Fingerprints, Copper Patina, Soot, Wet Surface, Rust Streaks and Moss. When a folder is selected, the new effect is placed above its children. Otherwise it is placed above the selected layer in the same parent folder.

Scratches changes Roughness and Height. Edge Polish, Fingerprints and Wet Surface change Roughness. These channel-only effects preserve the color and metallic response below them. Rust, dirt, coatings and patina carry their own dielectric Metallic values rather than merely tinting bare metal.

## Authoring conventions

Colors in the recipes are linear RGB. Raw-metal colors describe reflectance; paint, rust, dirt and wood colors describe diffuse albedo. Height is a signed modulation around neutral 0.5; negative amplitudes make scratch/pit signals depressions. Surface color and roughness use restrained correlated variation. Seeds are stable for a material, layer and chosen seed, including after saving and reopening.

Recipe `surface` parameters set scalar anisotropy or clearcoat response on the texture-set output material. They are explicit display parameters; an exported Base Color/Roughness/Metallic/Height map set does not itself encode anisotropy or a separate clearcoat lobe. Brushing and machining marks are still evaluated into textures.

Welded Steel and Heat-Treated Metal provide authored procedural placement of bead-like marks and temper-color regions. They do not infer actual weld paths or heat exposure from a model. Sci-Fi Panel Surface provides repair-coating variation and accepts additional painted or imported decal layers; it does not invent model-specific panel IDs. Weathered Stone's moss is a separate layer that can be hidden or deleted. These distinctions let artists choose placement deliberately.

## PBR references

The metal constants for aluminum, chrome, copper and zinc use approximate linear Rec.709 F0 values from [Adobe's metal material reference table](https://experienceleague.adobe.com/en/docs/substance-3d/general-knowledge/asm/metal-material-references). Steel, brass and bronze are authored alloy estimates rather than claims of measured samples.

The separation of conductive metal from dielectric corrosion, paint and dirt follows [Adobe's PBR Guide, Part 2](https://www.adobe.com/learn/substance-3d-designer/web/the-pbr-guide-part-2). Base Color contains material reflectance rather than baked scene lighting; roughness and mask images are linear data. Layer colors, damage amounts and scales are Open Painter authoring choices, not copied Substance assets.

Scalar surface controls follow [Blender's Principled BSDF documentation](https://docs.blender.org/manual/en/latest/render/shader_nodes/shader/principled.html). Clearcoat adds an additional specular response; it is distinct from lowering the base roughness.

## Validation

`tests/integration/catalog_materials.py` checks required-map preflight, rollback after a partial composition, all 50 editable hierarchies, independent pixel fingerprints, signed height values, effect channel isolation, folder effect order and save/reopen texture equality. Its synthetic mesh-map inputs test composition; geometric baker accuracy is tested separately. Static catalog auditing also checks unique recipe structures, finite values, supported procedural identifiers and unmasked substrates.

`scripts/render_catalog_previews.py` renders the composed output textures at 256 pixels on a beveled, UV-unwrapped sample with a real recessed cavity. Mesh maps are baked at 1024 pixels. This fixture makes both convex wear and concave grime/patina visible; thumbnail review supplements numerical validation. Pass `-- --material 'Material Name'` to render one entry.
