# Damaged Variable Bevel as engine-independent effects

`Blender_Edge_Wear.py` ("Damaged Variable Bevel" 1.4.0) rounds and chips mesh
edges inside the material: noise drives a variable **Bevel node** radius, the
distance between the bevelled normal and the geometry normal is the edge amount,
that amount times the noise (optionally minus an **AO node** cavity term) feeds a
**Bump** node, and the result is added to the incoming normal map. The Bevel and
AO shader nodes only evaluate in Cycles, so the script documents itself as
invisible in EEVEE and Material Preview.

Open Painter keeps the same model but moves the Cycles-only quantities into
textures baked once through the owned Cycles staging scene, whichever engine the
scene uses. After that the effect is ordinary layer data: EEVEE, Cycles, export
and the persistent shader preview all read the same maps.

## Mapping

| Script node / control | Open Painter |
| --- | --- |
| Bevel node normal (rounded shading) | `BEVEL_NORMAL` mesh map: Bevel → Diffuse normal, baked as a tangent normal map |
| `distance(bevel normal, geometry normal)` (`DB_VectorMath`) | `BEVEL_MASK` mesh map: the same expression baked through an emission pass, clamped to 0–1 |
| Bevel radius, samples (`DB_Bevel`, radius multiplier) | Bake settings **Bevel Radius** and **Bevel Samples** |
| Noise → radius ramp (variable radius, clamp min/max) | `MULTI_FRACTAL` procedural mask with Black/White levels, multiplied into the edge band |
| `DB_Mult_Damage` (edge × noise) | Mask stack: Bevel Mask × noise |
| Exclude Cavity (`DB_AO` → invert → ramp → subtract) | Optional `AO` map mask (multiply, levels); added automatically when AO is baked |
| Bump (invert, Distance = damage depth, Strength = amount) | Recessed Height on the effect layer (`height_value` 0.3), through the existing Height → Bump path; layer opacity is the amount |
| Add normal (bevel + normal map) | Rounded Edges layer on the Normal channel with **RNM** blending over whatever is below |
| Custom mask image | Replace the noise mask with an image mask on the layer |

Unlike the script, chips also expose the steel substrate (colour, roughness,
metallic 1); disable those channels on the layer for a normal-only result.

## Effects

- **Rounded Edges**: needs `BEVEL_NORMAL`. One Normal-channel layer, RNM blend.
- **Damaged Bevel**: needs `BEVEL_MASK`; uses `AO` when present. One layer with
  recessed height and steel substrate, masked by Bevel Mask × multifractal noise
  (× AO). Curvature Radius/Bevel Radius set the band width at bake time; the
  noise scale and levels set chip size and density.

Bake both maps from Bake Mesh Maps → Bevel Maps. Bevel Samples and the Cycles
sample count control the Bevel node's grain in the mask, exactly as in the
script's render.

## Recipe format additions

`mask(kind, baked=True)` reads the mesh map named by `kind` (`BAKED` source);
`optional=True` skips such a mask when the map is missing instead of requiring
it. `layer(..., normal_map="BEVEL_NORMAL", blend_mode="RNM")` puts a baked map on
the Normal channel. Portable materials store these as ordinary baked masks and
image channels (verified by round trip).

## Evidence

`scripts/validate_damaged_bevel_eevee.py` (windowed) bakes both maps on a
painted cube, applies the effects and renders the same view in EEVEE and Cycles
before and after ([report](validation/damaged-bevel-eevee.json)). EEVEE changed
11.9 % of pixels, Cycles 11.6 %, and the changed regions agree with a Jaccard
overlap of 0.98: [EEVEE before](validation/damaged-bevel-before-blender_eevee.png),
[EEVEE after](validation/damaged-bevel-after-blender_eevee.png),
[Cycles after](validation/damaged-bevel-after-cycles.png). The user scene's
engine stays on EEVEE throughout; no World, scene or material datablocks leak.

`tests/integration/damaged_bevel.py` checks the requirement errors, the baked
maps (edge bands only, tilted normals only on edges), edge-only recessed damage,
the optional AO term, RNM composition of the bevel normal, shader-graph
compilation and the portable round trip.

## Limits

- The variable radius is approximated: the band is baked at one radius and the
  noise thresholds it, rather than re-bevelling per point. Change Bevel Radius
  and rebake to change the maximum band width.
- Bevel-node grain follows the bake's samples; low sample counts show noise in
  the mask, as in the script's own renders.
- The bevel normal is a texture: it rounds shading, not the silhouette, exactly
  like the shader node.
