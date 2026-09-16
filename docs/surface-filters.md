# Seamless surface filters (0.9.4)

Painter's blur, warp and dilation run in UV image space, so they stop at island
borders and mix the margin in: the seam shows. Open Painter distinguishes **UV**
from **Surface** space per effect, and Surface is the default for every
neighbourhood effect.

## UV vs Surface

Every neighbourhood effect — Gaussian and box blur, high pass, sharpen, smooth
mask, dilate/erode and the morphological pair, edge detect, height to normal,
directional blur, slope blur, warp, directional warp, vector warp and UV
distortion — has a **Space** switch:

- **UV**: image space. Fast, bounded footprints, seams visible.
- **Surface** (default): the filter follows the mesh. Texels across a UV seam are
  the neighbours, and directional effects take a world-space **Direction**
  instead of an image angle.

Noise, gradients and procedural fills already follow the mesh through the
*Triplanar* projection (world space); Surface space completes the picture for the
operations that read neighbourhoods.

## How Surface space works

`geometry/seams.py` builds a **seam map** per texture set and resolution, cached
against the evaluated geometry:

1. Every 3D edge shared by two faces whose two sides carry different UVs is a
   seam. For each side, the band of texels *outside* the face — up to 48 texels
   deep, extended past the edge ends so the corner squares fill too — is mapped
   across the seam: the same 3D edge on the other island, at the same parameter
   along it and the same distance from it, scaled by the islands' relative UV
   density, clamped a texel inside the far island so bilinear reads never
   straddle its own margin.
2. `pad` fills those texels from across the closest seam (three passes, so
   samples that land just outside the far island read padded values too).
3. A filter runs **per island** on a crop of the island plus its footprint, with
   the island's own band re-padded from *its* seams even where another island's
   seam is closer, and writes back only the island's texels. Islands packed
   tighter than the filter footprint therefore still see their own continuation.
4. Per-triangle tangent Jacobians (dUV/dWorld) turn a world direction into a
   per-texel UV direction; directional blur and directional warp use it as a
   per-texel field instead of one angle. On a face perpendicular to the direction
   the in-plane component vanishes and nothing moves, as it should.

Surface-space effects need the whole image, so they leave the strip-halo path:
`operation_contracts.footprint` returns *whole image* for them, the CPU stack
evaluates the layer or mask stack once at full size and caches it, and the
shader's isolated inputs (`_channel_prefix`, mask prefixes) take the same seam-
aware path — the boundary image equals the CPU reference. Mesh edits invalidate
through `dependencies.uses_geometry`.

On a single-island mesh (a plane) Surface and UV agree exactly.

## Limits

The correspondence is first order: a straight continuation of the adjacent
face. Around a fold the blurred field follows the unfolded surface, which is the
geodesic neighbourhood for smooth surfaces and a close approximation at hard
creases; at vertices where three or more islands meet, the corner squares take
the clamped continuation of the two adjacent edges. Non-manifold seams (an edge
shared by more than two faces) are not mapped. The seam map costs one
rasterization plus a Python loop over seam edges (a few hundred milliseconds on
a 4k set with thousands of seams) and is cached until the geometry changes.

Generators that blur internally (micro details) still work in UV space.

## Tests

`tests/integration/surface_filters.py`: a subdivided cube with six islands and a
vertex-group field (x + 1) / 2 that is continuous across every seam. A Gaussian
blur in UV space departs from the field by 0.18 within four texels of a seam;
in Surface space by 0.013 (the fold's own curvature and corners), and both agree
away from seams. Dilation, warp, sharpen and directional blur differ at seams;
the tangent field of world X vanishes on the ±X faces; a mask filter takes the
whole-image path; the shader boundary matches the CPU result; a plane gives
identical results in both spaces.
