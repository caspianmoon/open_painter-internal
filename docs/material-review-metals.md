# Material review: metals, coatings, industrial surfaces and polymers

This records the earlier revision that was rejected for weak relief and poor
scratch/paint patterns. The replacement uses newly generated masks and stronger
height; see [the current review](material-surface-review.html) and
[the current implementation record](material-surface-quality.md).

This is the individual authoring review for catalog entries 1–32. The review used
the unchanged curved-object baseline renders in
`.blender-test/material-corrections/before/` at 1,024 authored texture resolution,
512 render pixels and the common lighting setup. It records observed defects and
the corresponding editable recipe corrections. Subsequent render validation is
recorded in the combined material-quality report; this file does not claim that
the before images validate the new recipes.

The correction follows the separation between grayscale layer coverage and
the channel values stored in the layer. Adobe documents grayscale masks and
stackable effects in [Masking and effects](https://experienceleague.adobe.com/en/docs/substance-3d-painter/using/interface/layer-stack/masking-and-effects).
Its [peeled-paint tutorial](https://www.adobe.com/learn/substance-3d-painter/web/substance-painter-peeled-paint-effect)
also separates paint coverage from roughness variation. We use that workflow
with original texture inputs and editable Open Painter layers; no Adobe assets
were copied.

The previous recipes frequently multiplied an independent scratch mask by a
second scratch texture in the layer. Their different seeds produced unrelated
relief and coverage, while all-channel layers recolored the marks. New cuts use
one coverage mask and one constant negative height offset. Fine handling marks
change roughness, while cuts that actually expose substrate retain its material
color and metalness. Coating, casting, corrosion and polymer relief is authored
individually, avoiding the previous large cellular displacement.

| # | Material | Observed defect | Individual correction |
|---|---|---|---|
| 1 | Clean Brushed Steel | Brushing relief was too strong for a clean fine finish. | Lower shallow brushed relief, retain directional roughness and anisotropy; hairlines affect only roughness and height. |
| 2 | Worn Brushed Steel | Dense pale ticks and broad oil patches obscured the brush direction. | Sparse tapered cuts, roughness-only fingerprints and polishing, granular cavity grime. |
| 3 | Machined Aluminum | Large concentric embossed bands dominated the metal. | Replace analytic ring displacement with fine machined texture, shallow relief and roughness-only cutter variation. |
| 4 | Scratched Aluminum | White dash strokes appeared painted on; dents were broad unrelated noise. | Single tapered cut mask, constant negative depth, roughness-only cuts and small isolated pore masks for dents. |
| 5 | Cast Iron | Casting was pale and smooth, with an overly bright continuous rim. | Dark oxide skin, finer shallow casting grain, more broken restrained exposed creases and grain-sized oxidation. |
| 6 | Raw Industrial Steel | Large light and dark camouflage blobs replaced mill scale. | Use casting-grain-correlated thin blue-black scale, shallow rolling cuts and sparse fine flash rust. |
| 7 | Polished Chrome | Wipe residue was conspicuous cloudy patches, fingerprints lacked ridge structure. | Roughness-only low-coverage residue and recognizable fingerprint masks; keep conductor color untouched. |
| 8 | Old Brass | Brown tarnish formed large high-contrast camouflage islands. | Tarnish coverage comes from the actual fine tarnish texture, with separate recess oxidation and restrained edge polish. |
| 9 | Antique Bronze | Pitting made the whole metal look inflated and rough; patina formed an even band. | Shallow casting grain, darker aged skin and patina broken by correlated rust-grain coverage inside cavities. |
| 10 | Oxidized Copper | Pink and turquoise clouds repeated over the entire surface. | Fine correlated brown oxide and turquoise granular deposits; dark sheltered growth and restrained rain washing. |
| 11 | Worn Painted Metal | Regular bright scratch ticks dominated an otherwise even paint finish. | Fine coating grain, edge-driven chips and sparse tapered cuts with constant recessed depth. |
| 12 | Heavy Chipped Paint | Soft brown blobs, inflated polygon cracks and bright dash scratches looked artificial. | Sharp fractured paint coverage, shared primer/topcoat fracture seed with different thresholds, mesh-driven edge loss and shallow tapered scoring. |
| 13 | Factory Powder-Coated Metal | Orange peel was exaggerated and the entire edge was outlined. | Finer shallow powder grain, more restrained broken edge exposure and roughness-only handling cuts. |
| 14 | Military Painted Steel | Pale scratch ticks covered every surface; dust washed out the paint. | Matte olive coating with sparse substrate cuts, localized chipped creases and fine low-coverage top dust. |
| 15 | Old Enamel Metal | Bright raised crack lattice covered the entire enamel. | Smooth enamel, fine dark recessed fissures and separate roughness-only hairlines. |
| 16 | Rusting Painted Steel | Soft orange patches read as paint color variation instead of corrosion. | Broken paint coverage reveals dark granular rust beneath; fine orange growth and thin runoff remain separate layers. |
| 17 | Automotive Painted Metal | Broad dull swirls changed the paint color. | Preserve saturated base coat; washing residue and shallow clearcoat cuts affect roughness and height independently. |
| 18 | Burned Painted Metal | Soft brown cloud marks and excessive fine bumps hid the blister structure. | Fractured surviving paint, shallow blister relief, char coverage tied to blister texture and sparse ash. |
| 19 | Heavy Rust | Inflated broad noise dominated the surface, reading like orange rock. | Dark oxide bed and three correlated granular rust scales, with restrained but visible height variation. |
| 20 | Surface Rust | Large pale patches obscured metal without reading as a thin oxide layer. | Small correlated brown and orange oxide sites with near-zero film height over intact brushed steel. |
| 21 | Wet Rust | Large bulges and an unrelated crack network produced a waxy surface. | Shallow rust grain, rough dry scales tied to the same texture and independent roughness-only moisture. |
| 22 | Galvanized Steel | Large white oxidation patches and smooth shading overwhelmed zinc spangles. | Stronger fine spangle roughness with minimal height, sparse grain-correlated oxide and shallow handling cuts. |
| 23 | Welded Steel | Random raised long strokes did not form a weld or a heat-affected zone. | Explicit editable UV-positioned seam and nested gold/blue heat bands, with shallow bead height and separate cavity soot. |
| 24 | Heat-Treated Metal | Random brown and blue clouds looked like colored noise. | Nested UV-positioned temper gradients keep straw, brown, violet and blue in a coherent thermal sequence. |
| 25 | Greasy Machinery | Large machining rings and white service scratches overwhelmed the oil. | Fine machined grain, recessed service cuts, cavity grease, roughness-only oil film and localized runoff. |
| 26 | Abandoned Machinery | Soft color clouds, whole-surface cracks and pale dust hid peeling paint. | Sharp broken teal paint over dark granular rust, local oil leakage and cavity grime with sparse top dust. |
| 27 | Injection-Molded Plastic | Fine grain was visible but broad molding variation altered color unnecessarily. | Shallow finer polymer grain and restrained roughness-only molding flow. |
| 28 | Worn Hard Plastic | Dense short pale ticks and broad handling clouds repeated everywhere. | Sparse tapered stress-whitened cuts, roughness-only fingerprints and localized polished edges. |
| 29 | Soft-Touch Plastic | Cloudy oils and uniformly distributed fine scratches flattened the material. | Fine matte micrograin, restrained shallow abrasion and sparse roughness-only handling ridges. |
| 30 | Sun-Damaged Plastic | Broad blue/white clouds and an unrelated large crack web looked painted on. | Coherent normal-driven bleaching, fine dark fissures gated by exposure and fine chalky polymer grain. |
| 31 | Dirty Rubber | Even pale rim and scattered tiny strokes dominated the rubber. | Dark shallow rubber grain, tapered scoring, roughness-only high-point polishing and granular earth in recesses. |
| 32 | Cracked Old Rubber | Inflated polygon lattice and broad gray mottling resembled stone. | Fine dark branching fissures on shallow rubber grain, subtle grain-correlated fading and protected crevice dust. |

Weld and heat-treatment placement is intentionally exposed as editable UV mask
mapping. Baked curvature cannot identify the intended location of a fabricated
weld or heat source. These presets do not claim to infer manufacturing history
from arbitrary geometry. Fingerprints, dust, handling wear and corrosion remain
authored appearance defaults, not measured material scans.

The changes preserve all 32 material names and existing layer names, their
editable layer-stack workflow, the 15 shared scalar source image identities and
the fixed display material. Existing saved material instances are not silently
rewritten; library presets must be reapplied to use these new authored defaults.

## Second visual pass

All 32 first-pass replacement renders were opened individually at 512 pixels,
using the same curved object and 1,024 texture resolution. Removing the old
patterns exposed additional authoring problems; the first replacement pass was
not accepted as the finished library.

Fourteen recipes received another correction before the final render:

| Material | First replacement still showed | Additional correction |
|---|---|---|
| Worn Brushed Steel | Fingerprints larger than the intended handling scale. | Double the mask repetition and reduce roughness coverage. |
| Machined Aluminum | Too little visible fine machining in the shared lighting. | Increase fine source roughness variation and shallow relief; avoid returning to analytic embossed rings. |
| Scratched Aluminum | An even distribution of circular dent marks. | Much smaller, more selective and lower-opacity dents, with more legible roughness change in the tapered cuts. |
| Cast Iron | Casting skin remained too smooth at the review distance. | Increase casting-grain relief and roughness while preserving the dark continuous oxide skin. |
| Raw Industrial Steel | Bright pristine-metal flecks between oxide texels looked like a salt-and-pepper pattern. | Make the mill-scale skin continuous and carry texture variation in its channels; sparse scoring can still expose substrate. |
| Polished Chrome | Fingerprint ridges were too large. | Double repetition and reduce roughness opacity. |
| Antique Bronze | Patina had been suppressed almost completely and the base looked too pale. | Darker tarnished base, greater cavity contrast with lower noise, less aggressive granular mask multiplication. |
| Oxidized Copper | Bright copper between turquoise oxide texels looked like decorative speckling. | Continuous brown oxide under the patina and softer coherent patina coverage; restrained rain washing. |
| Heavy Chipped Paint | Bright granular substrate dominated the remaining paint, and isolated paint fragments were oversized. | Continuous dark rust bed, smaller fracture regions, wider shared-seed primer transition and gentle paint-boundary levels. |
| Burned Painted Metal | Exposed metallic ground and sparse char made the material look pale. | Darker heat-affected ground and wider char coverage tied to the blister source. |
| Surface Rust | Fine spots altered highlights more than they showed oxide color. | Remove unnecessary film height and increase brown/orange oxide coverage without making macro clouds. |
| Greasy Machinery | Runoff produced conspicuous disconnected vertical dashes. | Restrict lower-opacity runoff to cavity-adjacent regions and tighten the grease mask around actual occlusion. |
| Worn Hard Plastic | Cuts were too faint and fingerprints too large. | Slightly larger stress-whitened tapered cuts and smaller, lower-opacity handling ridges. |
| Soft-Touch Plastic | Large handling ridges still looked stamped over the finish. | Smaller handling masks with less roughness contrast. |

The 512-pixel overview is used to reject visible large-pattern failures. It is
not sufficient by itself to validate the fidelity of fine machining, hairline
scratches or polymer grain at 2K authoring resolution. Closeup checks and final
render evidence belong to the combined report.

## Full 2K texture review

Every entry below was opened individually from
`.blender-test/material-corrections/final-2k/`. Its recorded context is Blender
5.2.1 LTS, full 2,048 texture resolution, a 512-pixel curved-object render and
32 samples. These are observations of that render, not a claim that the source
images are measured scans or that every material is photorealistic.

| # | Material | Final rendered observation |
|---|---|---|
| 1 | Clean Brushed Steel | Fine directional sheen; no conspicuous painted scratch dashes. |
| 2 | Worn Brushed Steel | Handling ridges are smaller and restrained; protected grime is subtle. |
| 3 | Machined Aluminum | Fine directional marks are visible near reflections; large embossed rings are absent. |
| 4 | Scratched Aluminum | Sparse tapered marks remain legible; the first replacement's dent polka dots are gone. |
| 5 | Cast Iron | Continuous dark casting skin has fine visible grain and a narrow exposed edge. |
| 6 | Raw Industrial Steel | Coherent blue-black scale replaces bright speckled metal exposure; sparse scoring remains. |
| 7 | Polished Chrome | Mirror response is preserved; fingerprints appear only subtly around highlights. |
| 8 | Old Brass | Fine irregular tarnish replaces the baseline's broad camouflage pattern. |
| 9 | Antique Bronze | Darker aged base and visible green cavity patina; no all-over inflated pitting. |
| 10 | Oxidized Copper | Coherent turquoise granular patina over brown oxide; bright pristine-metal speckling is gone. |
| 11 | Worn Painted Metal | Sparse tapered scoring and local edge wear; coating micrograin remains subtle at the overview size. |
| 12 | Heavy Chipped Paint | Dark continuous corrosion beneath smaller sharp paint fragments; fragment placement still looks graphic and the peel rim is not strongly visible. |
| 13 | Factory Powder-Coated Metal | Restrained even coating and thin wear at the crease; fine coating grain is subtle. |
| 14 | Military Painted Steel | Sparse abrasion and coherent olive paint, with substantially less pale all-over noise. |
| 15 | Old Enamel Metal | Smooth cream enamel with very fine dark crazing; crazing is faint at the overview size. |
| 16 | Rusting Painted Steel | Dark granular rust is distinct from paint; UV-distributed surviving paint islands still read as an authored pattern. |
| 17 | Automotive Painted Metal | Saturated smooth paint and clearcoat response remain intact; wash details are subtle. |
| 18 | Burned Painted Metal | Charcoal-dark surface replaces the first replacement's pale metallic ground. |
| 19 | Heavy Rust | Fine layered granular corrosion; no baseline-scale inflated blobs. |
| 20 | Surface Rust | Thin brown/orange sites are visible on intact metal without added film bumps. |
| 21 | Wet Rust | Dark glossy moisture and small rough dry scales; no large crack lattice. |
| 22 | Galvanized Steel | Fine reflectance/roughness spangles; no artificial deep crystalline relief. |
| 23 | Welded Steel | A coherent editable seam and heat band replace random strokes; the seam does not reproduce a stacked weld-bead shape. |
| 24 | Heat-Treated Metal | Gold-to-violet-to-blue transitions form coherent nested bands instead of random color clouds. |
| 25 | Greasy Machinery | Barcode-like runoff is gone; grease reads subtly on this shallow-groove object, so the overview appears fairly clean. |
| 26 | Abandoned Machinery | Dark corrosion and surviving teal paint are distinct; fragment distribution remains an authored UV pattern. |
| 27 | Injection-Molded Plastic | Smooth satin finish with shallow grain, without broad color mottling. |
| 28 | Worn Hard Plastic | Sparse scoring is visible and handling ridges are smaller; wear remains restrained. |
| 29 | Soft-Touch Plastic | Matte finish with subdued small handling ridges instead of large stamped fingerprints. |
| 30 | Sun-Damaged Plastic | Broad directional bleaching and fine dark fissures, without noisy color clouds. |
| 31 | Dirty Rubber | Dark matte rubber and protected deposits; the shallow-groove review object reads relatively clean. |
| 32 | Cracked Old Rubber | Fine open dark fissures replace the baseline's inflated polygon lattice. |

The remaining limits are visual authoring limits. In particular, the generated
fracture source still distributes detached paint fragments in UV space; it does
not infer a history of physical delamination, produce folded flakes, or infer
where a real object was handled. Similarly, cavity-only deposits are weak on an
object with little occlusion. These limits should remain visible in the review
record rather than being hidden behind a blanket photorealism claim.
