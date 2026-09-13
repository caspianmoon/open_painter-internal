# Texture workflow v1 — Open Painter 0.2.0

The library now composes texture layers and grayscale mask operations. Generators never add noise, curvature, wear or corrosion nodes to the material. A fixed Principled material displays the final eight channel images. The UV tangent, normal and bump nodes interpret those images for preview.

## Using the library

1. Assign a UV-unwrapped mesh to a texture set. Set the texture resolution.
2. Bake the maps listed in the [50-material catalog](material-catalog.md). Materials report missing maps before adding layers; they do not start an unexpected bake on a library click.
3. Click a material thumbnail or use **Asset Library → Add Smart Material** to search by name. The material creates a folder containing independently editable fills and masks.
4. Select a child layer and a channel in Properties. Choose a procedural, edit its scale/seed/detail, or supply an image. UV and triplanar projection are evaluated into the texture. Triplanar uses the base mesh's world positions and interpolated vertex normals; apply topology-changing modifiers first.
5. Add filters below the channel input. Filter order matters. Select a mask to add procedural sources, mesh generators and filters to its grayscale stack. A filter transforms the accumulated mask; it does not multiply a second unrelated gray image over it.
6. Add a stackable effect from the library controls. For coating loss, put the wear mask on the paint layer, above the substrate. A folder mask intentionally masks its substrate too.
7. Paint the selected raster channel or painted mask, save the `.blend`, or export textures. Smart material files preserve procedural/filter parameters and packed image inputs.

The controls use Blender's native widgets, search popups, numeric fields, image pickers, lists and theme. Assets remain below the viewport; texture sets/layers/properties remain on the right. T/N retain their native region toggles.

## Implemented foundation

| Area | Available in 0.2.0 |
| --- | --- |
| Smart materials | All 50 requested named materials, 229 editable layers; original compositions and rendered previews |
| Stackable effects | 16 editable effects, including Rust, Dirt, Dust, Edge Wear, Chipping, Scratches, Grease, Water Stains, fingerprints and wetness |
| Smart masks | 18 original edge, cavity, dust, rust, scratch and drip presets |
| Procedurals | 45 named noise families plus 20 pattern/detail sources; deterministic Perlin, Simplex, FBM, Value, Voronoi/Worley, grunge, scratches, cracks, fibers, grain, fingerprints and swirls |
| Filters | 52 operations, including Levels, histogram controls, Gaussian/directional/slope blur, warps, morphology, gradient map, HSL/HSV, sharpening, remapping and normal conversion/blending |
| Mesh maps | Normal, AO, convex crease Curvature, Position, World/Object Normal, Material/Object ID, UV Island/Border, Thickness |
| Channels | Base Color, Roughness, Metallic, Normal, Height, AO, Emission, Opacity; independently enabled, with independent blend and opacity |
| Blending | Arithmetic/separable Photoshop-style modes, Hue/Saturation/Color/Luminosity, Whiteout Normal Combine, RNM, additive normals and replacement normals |
| Painting | Blender texture paint, erase, clone, soften/sharpen and smear on the selected channel; pressure size/strength, spacing, position jitter, stabilization, alpha/stencil, rotation and follow-stroke controls |
| Persistence | `.blend` storage and version-2 portable material files, including ordered channel/mask filters and surface display settings; version-1 materials still import |

This is the requested composable v1 foundation and 50-material library, not a claim that every item in the longer wishlist is implemented. Anchor/reference layers, global adjustment layers, simultaneous multi-channel material strokes, independent pressure-to-height/flow, additional named environmental generators, distance transform, bevel-mask and normal-to-height filters remain outside this release. No unavailable operation is represented by a fake button. The historical PDF parity inventory remains separate.

Anisotropy and clear coat currently belong to the texture set's display surface settings, rather than independently composited coat channels. The recipe catalog states which materials use them. Environmental generators are authored mask models, not physical simulations of corrosion, weather or liquid flow.

## Geometry and performance

Edge wear is gated by baked convex target-mesh creases. Increasing generator amount does not turn triangulation edges into eligible edges. Edge width and minimum angle are bake settings. Broad paint peeling combines separate surface breakup with edge masks, rather than broadening the geometric edge classification.

Dust reads baked world-space normals, and cavity dirt reads AO. Position-based streaks use world height. Map baking and procedural projection use the render UV layer in the 0–1 tile. Thickness uses actual ray intersections at an explicitly chosen sampling grid; see [utility map contracts](research/utility-mesh-maps.md). UDIM painting and high-poly transfer of every utility map are not implemented.

Unfiltered and pointwise operations run in cooperative row tiles, yielding between expensive sources. Scalar procedurals and masks are reused across channels and across parameter-only edits; changing a source, seed, UV transform, generator, map or paint buffer invalidates the affected data. Shared texture memoization has a 256 MiB LRU budget; mesh geometry, output images and the current filter workspace are additional allocations. One required raster may exceed that budget. Neighborhood filters operate at the requested texture resolution so blur/warp results have no row-tile seams. They can take longer on large textures; this release does not promise real-time 4K/8K filter recomputation.

A local Blender 5.2.1 run with Worn Painted Metal (five fills plus one folder, 2048²) measured 24.7 seconds for the first full eight-channel evaluation and 2.08 seconds for a subsequent roughness edit. The earlier implementation regenerated the same source textures for that edit and took 22.1 seconds. Cached texture arrays used 144 MiB. The largest complete cooperative step, including the final image upload, used 42.4 ms of CPU time and 190 ms of wall time; host load affects wall-clock pauses. These are CPU-compositor measurements on synthetic mesh-map fixtures, not an interactive frame-rate guarantee. The [raw measurement](research/texture-performance-0.2.0.json) records the full context.

## References and validation

- [Adobe Painter mask workflow](https://experienceleague.adobe.com/en/docs/substance-3d-painter/using/interface/layer-stack/masking-and-effects): masks belong to layer content and can contain ordered effects.
- [Ken Perlin's improved-noise reference](https://mrl.cs.nyu.edu/~perlin/noise/): gradient interpolation reference; Open Painter uses its own deterministic integer hashing.
- [W3C compositing and blending](https://www.w3.org/TR/compositing-1/#blending): separable and nonseparable blend definitions.
- [Blending in Detail](https://blog.selfshadow.com/publications/blending-in-detail/): RNM and normal-blending derivations.
- [Blender texture-paint brushes](https://docs.blender.org/manual/en/5.1/sculpt_paint/texture_paint/brushes.html): native paint, clone, smear, soften and sharpen behavior.

Tests cover all procedural/filter catalog entries, tile partition equality, filter order, correct normals, mask grayscale conversion, 50 distinct material outputs, editable effect channels, missing-map preflight, rollback, save/reload, native control rendering/search activation, and no procedural effect shader nodes. Preview thumbnails are rendered from the actual texture stacks on a mesh with convex edges and a recess.
