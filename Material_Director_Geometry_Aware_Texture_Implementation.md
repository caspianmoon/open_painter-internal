# Material Director: Geometry-Aware Texture Correspondence Implementation

## Goal

Make Material Director detect and correct texture failures caused by UV seams, broken pattern continuity, stretching, directional flow errors, and inconsistent detail across physically continuous surfaces.

The user experience remains:

> Prompt → textured model

All inspection, diagnosis, correction, and rechecking happen inside the same Material Director run.

## 1. Complete candidate views

After Codex creates a material candidate, render:

- Front
- Back
- Left
- Right
- Top
- Bottom
- Close-up

Codex must inspect the actual textured model from all of these views before commit.

## 2. Preserve render-to-surface correspondence

For each candidate view, retain enough internal data to resolve any visible pixel back to the mesh:

```text
render pixel
→ object
→ triangle
→ barycentric coordinate
→ 3D position
→ normal
→ UV coordinate
→ UV island
```

This data stays internal until Codex requests inspection of a problematic area.

## 3. Build UV seam correspondence

For every texture set, detect every physically continuous mesh edge that is split in UV space.

Store:

```text
seam_id
object_id
texture_set_id
3D edge
adjacent faces

side_a:
    UV edge
    UV island

side_b:
    UV edge
    UV island

orientation relationship
```

This lets Material Director know that distant UV edges represent the same physical surface boundary.

## 4. Add `inspect_texture_correspondence`

Create one read-only Material Director tool:

```text
inspect_texture_correspondence
```

Input:

```json
{
  "texture_set_id": "...",
  "candidate_digest": "...",
  "view": "right",
  "x": 0.72,
  "y": 0.61
}
```

`x` and `y` are normalized coordinates in the selected candidate view.

Open Painter resolves the visible point back to the actual surface and finds nearby UV seams and corresponding UV regions.

## 5. Return a focused correspondence packet

The tool should return high-resolution visual evidence for the selected problem area.

### Required images

1. **3D close-up**
   - Current Eevee result around the problem.
   - Highlight the relevant surface or seam.

2. **UV side A**
   - High-resolution crop around one side of the seam.

3. **UV side B**
   - High-resolution crop around the physically corresponding UV edge.

4. **Seam correspondence view**
   - Shows that side A and side B are the same continuous 3D edge.
   - Shows orientation reversal or rotation where relevant.

5. **Current generated source**
   - The actual Codex-generated image currently driving the surface.

6. **Local geometry crops**
   - AO
   - Curvature
   - World Normal
   - Position
   - Normal when useful

All returned images must be clearly labeled by role.

## 6. Extend candidate critique

Codex must check candidate renders for:

- visible UV seams
- broken pattern continuity
- abrupt feature termination
- inconsistent detail scale
- directional flow changes
- mirrored or duplicated features
- stretching
- texture that looks correct from one side and wrong from another
- roughness discontinuity
- height or normal discontinuity
- material leakage across components

When Codex finds one of these problems, it should call `inspect_texture_correspondence` on that visible area before attempting correction.

## 7. Correct the existing generated texture

For a localized failure, Codex should edit the current generated source instead of regenerating the whole material.

The correction request should include:

- current generated source
- 3D close-up
- paired UV-side crops
- seam correspondence
- relevant local geometry maps

Example internal instruction:

> Edit the current generated texture only around this physical surface boundary. UV side A and UV side B represent the same continuous 3D surface. Continue the existing pattern naturally across the boundary. Preserve the current material identity, colors, feature scale, surrounding detail, and all unrelated parts of the texture.

The output becomes a replacement source for the same generated surface.

## 8. Regenerate only when the source itself is wrong

Codex should distinguish between:

### Local failure

Examples:

- one broken UV seam
- one stretched region
- local pattern direction error
- isolated mapping problem

Action:

```text
localized edit
```

### Global failure

Examples:

- whole generated source has wrong detail scale
- material concept is wrong
- pattern direction is wrong everywhere
- overall source looks artificial

Action:

```text
replace or regenerate source
```

## 9. Rebuild and review automatically

After every corrected generated source:

```text
update source
→ rebuild material candidate
→ render all candidate views again
→ return them to Codex
```

Codex then checks whether:

- the original problem is fixed
- another seam appeared
- unrelated successful areas were preserved
- material identity remains consistent

## 10. Add local surface-direction evidence

For directional materials, the correspondence packet should include local surface flow information.

Examples:

- scales
- feathers
- stripes
- wood grain
- brushed metal
- scratches
- cables
- horns
- tails

Represent useful local directions visually, such as:

```text
along component
around component
```

This gives Codex evidence for how the pattern should continue across the seam rather than relying only on UV orientation.

## 11. Keep inspection targeted

Do not send every seam and every UV relationship at the beginning of the Material Director request.

Use:

```text
initial global context
→ candidate
→ Codex discovers visible problem
→ targeted correspondence inspection
→ correction
```

This keeps the Codex context focused and allows high-resolution inspection where it matters.

## 12. Keep corrections inside the same run

The internal flow should be:

```text
USER PROMPT
    ↓
CODEX GENERATES MATERIAL
    ↓
CANDIDATE
    ↓
MULTI-VIEW REVIEW
    ↓
CODEX FINDS PROBLEM
    ↓
inspect_texture_correspondence
    ↓
FOCUSED GEOMETRY + UV EVIDENCE
    ↓
CODEX EDITS GENERATED SOURCE
    ↓
NEW CANDIDATE
    ↓
CODEX REVIEWS AGAIN
    ↓
COMMIT
```

No extra user interaction should be required.

## 13. Implementation targets

Likely areas to extend:

```text
src/open_painter/ai/
    candidate visual inputs
    generated image reuse and edit flow

src/open_painter/authoring/
    candidate tools
    correspondence inspection
    Material Director prompt

src/open_painter/geometry/
    UV seam correspondence
    render-pixel → surface mapping
    local surface-direction analysis
```

Implement support for:

```text
candidate pixel → surface lookup
UV seam pairing
paired seam orientation
correspondence packet generation
localized generated-source replacement
candidate rebuild after replacement
multi-view correction loop
```

## 14. Success Criterion

Material Director succeeds when a user can give a normal texturing prompt and the final result remains visually continuous across physically continuous surfaces, even when those surfaces are split across multiple UV islands.

The user should never need to inspect or repair UV seams manually.
