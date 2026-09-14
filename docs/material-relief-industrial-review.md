# Industrial and polymer visual review

Reviewed materials 17–32 individually on 2026-09-14, then repaired and rendered
nine entries whose defining effects were weak. The final review accepts those
repairs: overlapping weld pools, blister rims and charred craters, dark oil
smears, dragged dirt and branching rubber fissures are now visible. Repeated
circular fingerprint marks were removed.

The accepted repairs and their height/normal maps are in
`.blender-test/material-relief/industrial-final/`. Unchanged entries use
`final/`, and Abandoned Machinery uses `tuned/`. These are 512-pixel Cycles beauty renders of 2048-pixel
textures, at 32 samples with denoising. Judgments concern visible material
appearance in those renders; they do not establish that a faint effect has no
height data. The shader ball's large circular grooves are geometry, not evidence
of texture relief.

| # | Material | Actual observation and assessment |
|---|---|---|
| 17 | Automotive Painted Metal | Clean red paint has coherent glossy highlights. Micrograin is nearly invisible at this size, but the smoothness suits a clean automotive coating. Acceptable clean baseline; insufficient evidence of strong texture relief. |
| 18 | Burned Painted Metal — repaired | Raised blister rims, ruptured craters and dark scorched interiors now share the same generated source. The surface retains intact green paint between damaged regions; the earlier uniform charcoal-grey finish has been replaced by visible heat damage. |
| 19 | Heavy Rust | Dense granular/flaky relief breaks up the lighting across the surface and remains readable outside the strongest highlight. Broad brown/orange variation supports the material. No conspicuous zebra pattern; good relief result, although the fine flakes are fairly uniform in scale. |
| 20 | Surface Rust | Sparse orange corrosion flecks sit over reflective brushed metal. Small roughness and relief changes are visible without turning the entire substrate into rust. Appropriate light-corrosion treatment. |
| 21 | Wet Rust | Coarse corrosion remains visible beneath broader, glossier highlights. Its raised texture and wetter response distinguish it from Heavy Rust. Good readable relief. |
| 22 | Galvanized Steel — repaired | Sparse pale oxidation now allows the silver crystalline mottling to read beneath the highlights. The prior white chipped-patch impression is reduced. Crystal contrast remains restrained, appropriate to a mostly intact metal surface. |
| 23 | Welded Steel — repaired | The seam has overlapping scalloped solidification pools, varied crest relief and small adjacent spatters. A generated bead image controls both its coverage and height. It no longer reads as a smooth continuous ribbon. |
| 24 | Heat-Treated Metal | Fine brushing carries a coherent gold/violet/blue heat tint. The broad tint band is regular but plausible for this generic material. Acceptable; its clean microrelief should not be presented as heavy damage. |
| 25 | Greasy Machinery — repaired | Irregular dark oil deposits and clotted wipe marks are visible between areas of exposed reflective steel. Oil has a distinct glossy response and shallow buildup; weaker service abrasion supports the material instead of dominating it. |
| 26 | Abandoned Machinery — tuned | Irregular teal paint islands and brown corrosion are clearly separated, with modest coating-edge relief. The pattern avoids the earlier zebra appearance. Improved, though rust interiors still look relatively uniform and deep pitting is limited at this render size. |
| 27 | Injection-Molded Plastic | Smooth blue-grey satin response with very faint grain. Appropriate clean molded-plastic baseline; texture detail is too subtle here to demonstrate pronounced relief. |
| 28 | Worn Hard Plastic — repaired | Fine grouped scuffs and restrained pale stress whitening are now visible over the blue-grey polymer. Their coverage also recesses the height, replacing the nearly clean appearance without introducing broad white stroke marks. |
| 29 | Soft-Touch Plastic — repaired | The matte rubberized response remains intentionally subtle. Irregular low-contrast oily smears replace the repeated concentric fingerprint motifs. No obvious repeated circular patches remain. |
| 30 | Sun-Damaged Plastic — repaired | The pale-top to blue-bottom bleaching remains, now accompanied by readable fine branching fissures. The crack image drives the dark line coverage and its recessed height together. |
| 31 | Dirty Rubber — repaired | Localized tan granular dirt and dragged deposits are clearly visible over exposed dark rubber. Coverage is image-driven with additional cavity accumulation, so dirt is no longer absent on the shallow fixture. |
| 32 | Cracked Old Rubber — repaired | Dark branching fissures of varied width visibly interrupt the dry rubber surface. Recessed centers and shallower shoulders replace the earlier unrecognizable fine noise. |

The first review rejected the smooth weld ribbon, nearly invisible rubber
cracks, weak grease/burn/dirt effects and repeated fingerprints. Those findings
were reported before changing recipes. Four additional AI-authored sources
(`weld_bead_v2`, `burn_relief_v2`, `rubber_cracks_v2`, `grease_deposits_v2`) were
then incorporated, and the actual replacement renders were inspected. Burn
and grease coverage received a further render-guided correction.

All nine accepted repair records match the current catalog recipes and source
image hashes. Geometry, camera, lighting and render settings match across the
replacement runs; per-record metadata preserves the original UV and baked-map
hashes, which are not byte-identical across separate Blender runs. These are
AI-authored material inputs, not measured surface scans. Clean automotive and
molded-plastic entries remain intentionally smooth; nonzero height/normal
statistics alone are not treated as proof of successful appearance.
