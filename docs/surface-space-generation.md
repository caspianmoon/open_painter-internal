# Surface-space texture generation

Generated appearance is authored against the actual visible 3D surface first, then
converted into the existing UV atlas through exact geometry correspondence.

This replaces projection-first generated texturing as the generic path. `assign_projected_surface`
remains for intentionally decal-like or planar content.

## Flow

```text
USER PROMPT
  -> plan_surface
  -> capture_surface_observations          (Open Painter renders the real surface)
  -> Codex generates one image per observation
  -> generate_surface_field                (reprojection + multi-view fusion)
  -> preview_proposal                      (Eevee candidate on artist geometry)
  -> inspect_surface_coverage / inspect_texture_correspondence
  -> inspect_surface_area                  (corrective observation) or replace_surface_source
  -> critique_candidate
  -> propose_changes
```

## Observations

An observation is one orthographic camera over the evaluated scene. Orthographic on
purpose: a pixel footprint is then constant across the frame, which keeps projected
resolution, silhouette distance and reprojection weights exact instead of depth dependent.

`geometry/surface_observation.py` rasterizes the whole visible scene in screen space with
the same barycentric rasterizer UV baking uses (`uv_rasterizer.rasterize(..., unique=False)`
plus a per-pixel depth sort), so a pixel resolves to the same triangle that
`inspect_texture_correspondence` reports for the same ray. Occluders take part in the depth
test exactly like target geometry and are labelled `owner = -1`, so a pixel showing a hidden
surface is never attributed to the surface behind it.

`ai/surface_observations.py` owns the per-instruction registry. Each entry retains the camera
transform, projection, resolution and the visible-surface, silhouette and depth buffers; the
triangle, barycentric and UV buffers are reproduced exactly from the same camera and evaluated
mesh rather than stored. The registry is cleared by `session.release`.

An observation's identity is a hash of the instruction, label, region, resolution, direction
and framing — the same named direction over a different region is a different camera.

### Packets

Each packet carries the rendered surface view, AO / Curvature / Normal / World Normal /
Position / Thickness evidence **sampled through that observation's own UV buffer** (so every
image refers to the same visible surface), the valid surface mask, generic surface directions,
the artist prompt and the material it belongs to.

## Camera selection

Six labelled directions come from `viewport.principal_directions`, which candidate review uses
too, so `front` means the same thing in both. `geometry/surface_coverage.py` then measures what
each camera actually showed — visibility, view angle, projected pixel area, silhouette distance,
occlusion — and ranks further directions by the under-covered area they face, penalising
duplicates. The best few candidates are rasterized for real before one is accepted, so the
chosen camera is judged on measured occlusion rather than on normals alone. Capture stops when
good coverage reaches 90 % of the assigned area or the budget is spent.

## Reprojection and fusion

`geometry/surface_fusion.py` gathers in UV space rather than splatting in screen space: every
atlas texel of the assigned surface resolves to its own world position and normal, is projected
back into each observation, and takes appearance only from observations that saw that point.
No texel is left unwritten merely because an observation was coarser than the atlas.

A contribution is rejected when there is no valid mesh hit, when the hit belongs to another
texture set or region, when the original surface was occluded, or when the pixel is outside the
valid surface mask. Surviving contributions are weighted by

```text
facing quality ^ 2  x  silhouette confidence  x  projected resolution
```

and normalised. Facing quality falls to zero as the surface turns edge-on; silhouette confidence
ramps over three pixels from a projected boundary or a self-occlusion depth cliff; projected
resolution compares the observation's sampling length, stretched by foreshortening, to the
atlas texel. Every channel of a field shares these weights, so Base Color, Roughness, Height and
Metallic cannot drift apart. A weighted deviation across observations is kept, so views that
disagree about the same surface point are reported (`CONFLICTED`) rather than averaged silently.

## Seams

UV islands are storage; physical mesh adjacency determines continuity. Because fusion gathers per
texel from world positions, two texels on different islands that are physically adjacent already
receive the same appearance. `geometry/surface_seams.py` handles what remains: it builds the
similarity transform that carries one island across a shared edge into the paired island — derived
from the existing seam-correspondence records, and handling mirrored, rotated and separately placed
islands — and uses it to fill gutter texels from the physically continuous surface. It only moves
values fusion already produced.

## Holes

After fusion, texels are classified `GOOD` / `WEAK` / `MISSING` / `CONFLICTED` and uncovered
texels are grouped into world-space clusters. Uncovered surface is reported, never filled by
stretching nearby pixels: `inspect_surface_coverage` returns the clusters and the directions that
would cover them, and `inspect_surface_area` captures a corrective observation framed on exactly
that geometry, which is then refused into the same field.

## The field

`authoring/surface_field.py` stores a `$surface_field` descriptor per channel — texture set,
resolution, region, observation IDs, camera transforms and generated image IDs — resolved by
`assets.prepare` into a packed UV image, exactly like the other generated-source descriptors.
Every channel of one field shares one fusion pass through a bounded cache. The result is a normal
editable Open Painter source with a coverage mask, a region mask when bounded, and provenance in
`source_metadata` naming the observations, generated images, channels and region.

Derived Roughness and Height (`LOCAL_LUMINANCE_CONTRAST_V1`) are a fallback used only when an
observation supplies no generated map for that channel; they read the padded fused colour so an
island edge is not read as a feature.

## Correction types

`critique_candidate` requires each weakness to name one:

| type | when |
| --- | --- |
| `TEXTURE_SOURCE` | the appearance itself is wrong |
| `SURFACE_OBSERVATION` | coverage is weak, a view synthesised badly, geometry is poorly represented, detail direction is wrong on the surface |
| `MAPPING_FUSION` | observations disagree, a seam comes from fusion, a contribution came from a weak view |

`replace_surface_source` refuses a defect the critique classified as anything but
`TEXTURE_SOURCE`, and refuses a fused field outright: a field has no flat canvas to edit.

## Surface directions

`geometry/surface_tangents.py` exposes a generic tangent field: UV parametrization tangents with a
stability flag, the shape operator fitted from normal variation over incident edges (full 3x3
normal equations; a unit cylinder returns k_max = 1, k_min = 0 exactly), and the connected-component
principal axis. Directions are reported only where they are measurable. Nothing in it is object or
material specific.

## Tests

- `tests/unit/test_surface_space_generation.py` — observation identity, occlusion, silhouette
  distance, exact reprojection, edge-on weighting, disagreement, hole reporting, region bounds,
  camera ranking, seam continuation, tangents and the command/critique gates.
- `tests/integration/director_surface_field.py` — real mesh, real UVs, real seams: the tool flow,
  packets, fused reprojection accuracy against an independently rebuilt correspondence, shared
  correspondence across explicit channels, bounded regions, provenance and the rejection paths.
  Only the shaded Eevee draw is substituted.
