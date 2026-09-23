# Material Director: Surface-Space Texture Generation Implementation Plan

## Goal

Replace projection-first generated texturing with a generic surface-space workflow that works across arbitrary meshes.

Codex should author appearance against rendered views of the actual 3D surface. Open Painter should transfer that appearance back onto the exact mesh triangles and existing UV atlas using geometry correspondence.

The user workflow remains:

> Prompt → textured model

## 1. Add surface-observation rendering

Create a surface-observation renderer that can capture arbitrary camera views of the current evaluated mesh.

Each observation must retain:

```text
camera transform
projection matrix
render resolution
visible object
visible triangle ID
barycentric coordinates
3D position
surface normal
UV coordinate
texture set
material slot
```

For every rendered pixel, Open Painter must be able to resolve:

```text
pixel
→ visible triangle
→ barycentric point
→ UV coordinate
```

Do not rely on screen color alone.

## 2. Add geometry-driven camera selection

Create a generic camera-selection system.

Initial cameras should provide broad surface coverage:

```text
front
back
left
right
top
bottom
```

Then compute actual mesh coverage from those views.

For every triangle, track:

```text
visibility
view angle
projected pixel area
distance from silhouette
occlusion state
```

Use these values to score observation quality.

## 3. Build a surface coverage map

Maintain coverage per triangle and per UV texel.

Each covered surface sample should store:

```text
observation count
best view angle
best projected resolution
best confidence
source observation IDs
```

Classify coverage as:

```text
GOOD
WEAK
MISSING
```

This map drives additional camera generation.

## 4. Choose additional cameras automatically

When important geometry has weak or missing coverage, choose another camera from geometry.

Select the direction that maximizes visibility of under-covered triangles while minimizing:

```text
occlusion
edge-on viewing
duplicate coverage
silhouette ambiguity
```

Continue until useful surface coverage is sufficient or the generation budget is exhausted.

## 5. Create Codex surface-generation packets

For each selected observation, send Codex a clearly labeled packet containing:

```text
current rendered surface view
AO evidence
Curvature evidence
Normal evidence
World Normal evidence
Position evidence
Thickness evidence
surface mask
artist prompt
current material context
```

All evidence for an observation must refer to the same visible surface.

Codex generates appearance for the visible surface represented by that observation.

## 6. Preserve observation identity through image generation

Every generated image must retain the observation ID it belongs to.

Store:

```text
generated_image_id
observation_id
camera transform
projection matrix
surface visibility buffer
triangle buffer
barycentric buffer
UV buffer
```

Generated images are surface observations, not free-floating texture sources.

## 7. Reproject generated appearance onto the actual mesh

For every generated observation pixel:

```text
generated pixel
→ observation pixel
→ triangle ID
→ barycentric point
→ UV coordinate
```

Write its appearance contribution into the target texture atlas.

Reject pixels where:

```text
no valid mesh hit exists
the hit belongs to another texture set
the original surface was occluded
the observation is outside the valid surface mask
```

This prevents projection spill onto unrelated geometry.

## 8. Add confidence-weighted multi-view fusion

The same UV texel may receive appearance from multiple observations.

Compute a contribution weight using:

```text
facing quality
visibility confidence
projected resolution
silhouette confidence
```

Facing quality decreases as the surface becomes edge-on.

Silhouette confidence decreases near projected object boundaries.

Occluded observations contribute zero.

Fuse valid observations into one UV-space texture using normalized weights.

## 9. Add seam-aware UV accumulation

When physically adjacent surface points lie on separate UV islands, treat them as neighboring surface samples during fusion.

Use the existing seam-correspondence system to preserve continuity across:

```text
UV island boundaries
mirrored island orientation
rotated islands
different island placement
```

UV islands are storage locations. Physical mesh adjacency determines continuity.

## 10. Add hole detection after reprojection

After fusion, detect atlas regions corresponding to real assigned surface that have:

```text
no generated contribution
very weak contribution
conflicting observations
```

Map those texels back to their physical surface.

Generate a targeted observation for that geometry and repeat:

```text
render
→ Codex generation
→ reprojection
→ fusion
```

Do not fill uncovered surface by blindly stretching nearby pixels.

## 11. Add generic surface-direction evidence

Compute generic local surface directions where a stable tangent field can be obtained.

Expose:

```text
primary tangent
secondary tangent
surface normal
local curvature directions when stable
connected-component direction
```

Codex can use these for directional appearance such as:

```text
grain
scales
scratches
fibers
brushing
stripes
wear flow
```

Do not add object-specific semantic logic.

## 12. Reproject generated PBR channels through the same correspondence

Base Color, Roughness, Height and other generated channels must use the same observation-to-surface mapping.

If Codex generates separate supporting maps for an observation, reproject them through the same:

```text
pixel
→ triangle
→ barycentric point
→ UV
```

pipeline.

Do not allow channel-specific placement drift.

## 13. Make luminance-derived detail a fallback

Keep the existing luminance-derived detail only as a fallback.

For high-detail generated surfaces, prefer explicit generated or authored:

```text
Base Color
Roughness
Height
Metallic when relevant
```

when Codex determines those channels are needed.

## 14. Add a fused generated-surface representation

Create a generated surface representation for fused surface observations.

Conceptually:

```text
GENERATED_SURFACE_FIELD
```

It should reference:

```text
observation images
observation transforms
surface correspondence
fusion state
coverage state
resulting UV textures
```

The resulting UV textures remain normal editable Open Painter material sources.

## 15. Integrate surface-space generation into Material Director

Add a Material Director operation conceptually equivalent to:

```text
generate_surface_field
```

It should identify:

```text
texture_set_id
region_id if bounded
observation IDs
generated image IDs
channel intent
```

Open Painter handles reprojection and fusion.

## 16. Support bounded regional generation

The same workflow must support:

```text
whole assigned surface
```

and:

```text
specific geometric region
```

For a bounded region, only triangles belonging to that region are valid reprojection targets.

## 17. Update candidate preview

Candidate preview must render the fused UV-space result from multiple directions.

Codex reviews:

```text
front
back
left
right
top
bottom
close-up
```

and component views when useful.

The preview must represent the final UV-applied result, not an intermediate projection.

## 18. Reuse the current correspondence system for correction

Keep:

```text
inspect_texture_correspondence
```

After the fused result is rendered, Codex can inspect:

```text
UV seam failures
pattern discontinuity
local scale problems
roughness discontinuity
relief discontinuity
localized placement errors
```

The current correspondence system becomes the refinement layer over the new surface-space generation path.

## 19. Add surface-space correction

When a defect is caused by poor surface observation rather than a flat texture-source error, allow Codex to request a new observation.

Add a correction path conceptually equivalent to:

```text
inspect_surface_area
→ generate corrective observation
→ reproject only that surface region
→ refusion
→ rebuild candidate
```

## 20. Distinguish correction types

Material Director should choose between:

### Texture-source correction

Use when:

```text
appearance itself is wrong
color/detail is wrong
local image content is wrong
```

### Surface-observation correction

Use when:

```text
coverage is weak
view synthesis failed
geometry is poorly represented
detail direction is wrong on the surface
```

### Mapping/fusion correction

Use when:

```text
multiple observations disagree
a seam is caused by fusion
a contribution came from a weak view
```

## 21. Preserve editability and provenance

After fusion, resulting textures must become normal Open Painter sources.

Store enough provenance to know:

```text
which observations created them
which regions they affect
which channels they drive
which generated images were used
```

Subsequent refinement should be able to regenerate or correct only the affected part.

## 22. Update the Material Director tool flow

The internal workflow becomes:

```text
USER PROMPT
    ↓
CODEX PLANS MATERIAL
    ↓
OPEN PAINTER CAPTURES SURFACE OBSERVATIONS
    ↓
CODEX GENERATES OBSERVATION APPEARANCE
    ↓
OPEN PAINTER REPROJECTS TO SURFACE
    ↓
MULTI-VIEW FUSION
    ↓
COVERAGE ANALYSIS
    ↓
MORE OBSERVATIONS IF NEEDED
    ↓
UV MATERIAL RESULT
    ↓
EEVEE CANDIDATE
    ↓
CODEX REVIEW
    ↓
TARGETED SURFACE / TEXTURE CORRECTION
    ↓
REBUILD
    ↓
CODEX REVIEW
    ↓
COMMIT
```

## 23. Implementation areas

### `src/open_painter/ai/`

Add or extend:

```text
surface observation capture
camera selection
surface coverage state
Codex observation packets
candidate review integration
```

### `src/open_painter/geometry/`

Add:

```text
pixel → triangle correspondence
surface visibility buffers
barycentric buffers
surface coverage analysis
camera scoring
surface tangent fields
```

### `src/open_painter/authoring/`

Add:

```text
generated surface-field representation
surface reprojection
multi-view fusion
UV texture accumulation
coverage-hole handling
surface-field replacement/refinement
```

### `src/open_painter/evaluation/`

Add support for evaluating fused UV textures as normal material sources.

## 24. Make surface-space generation the primary generic path

`assign_projected_surface` can remain available for intentionally decal-like or planar content.

General generated surface appearance should use:

```text
surface observations
→ exact geometry correspondence
→ UV reprojection
→ multi-view fusion
```

## 25. Final architecture

```text
                     GEOMETRY
                        ↓
               observation cameras
                        ↓
                  rendered views
                        ↓
                      CODEX
                        ↓
             generated view appearance
                        ↓
              pixel/triangle mapping
                        ↓
                surface reprojection
                        ↓
                multi-view fusion
                        ↓
                 existing UV atlas
                        ↓
                  PBR material
                        ↓
                candidate renders
                        ↓
                      CODEX
                        ↓
             targeted correction loop
                        ↓
                      COMMIT
```

The central implementation rule is:

> Generated appearance is authored against the actual visible 3D surface first, then converted into UV textures through exact geometry correspondence.
