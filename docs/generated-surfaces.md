# Generated surfaces and visual correction

## Matching material maps

`inspect_surface_source` returns the original generated albedo of an existing fill,
its source ID and canvas dimensions. Director can use that image as the reference
for separate grayscale height, roughness and metallic generation. The image budget
is eight per instruction, including supporting maps; existing color can be reused.

`assign_surface_maps` attaches those maps to the same fill, preserving albedo and
its complete placement. It validates reference identity, scalar-map aspect ratio,
and grayscale data; it imports channels as Non-Color and applies the reference's
alpha coverage. Different resolutions sample the same normalized canvas. This
does not prove that generated features align: Director must inspect the result.
Original maps and editable channel inputs remain packed in the project.

Height describes local surface relief around a flat 0.5 substrate, not object
depth or the brightness of paint and shadows. Its nonzero `height_amplitude` is
scaled by the set's Height Distance. The existing shader uses composed height
for bump relief, and normal export derives the corresponding surface gradients.
The candidate NORMAL visualization now includes this height relief too. There
is no second independently generated normal map applying the same relief twice.
This operation currently accepts native UV, triplanar and decal mapping; metric
height needs a physical amplitude and is rejected with an actionable error.

Synthetic integration coverage: `tests/integration/director_surface_maps.py`
checks unchanged color, shared alpha/mapping, scalar variation, derived normals,
packed save/reopen and exact refinement undo. Visual quality and semantic
registration still require inspection of an actual generated result.

Before Create, Refine or Plan, Director requires current AO, Curvature, Normal,
World Normal, Position and Thickness maps for every texture set in scope.
Missing, stale, unverified or unloaded maps stop the request with an instruction
to use **Bake Mesh Maps**. View and map sharing must be enabled. Nothing is sent
until this check passes; Director does not silently bake or replace artist maps.
Explain remains available without these prerequisites.

Initial visual context includes the current view, opposite and side views, a
central closeup, and the six labeled map previews. Region inspection supplies
component views during authoring. The sharing summary lists the actual views.

Generate Textures now enables a required surface plan and disposable Eevee review.
Director describes the object's components, substrate and handling histories,
macro/meso/micro detail, source roles and mapping before authoring. It must assign
an actual generated source (or retain an existing one during refinement).

`assign_generated_surface` places original generated color and an aligned scalar
detail image on ordinary editable channels with shared mapping. Roughness uses
an editable range. Optional height uses a bounded amplitude; zero leaves relief
disabled. The scalar source uses local luminance contrast from the same pixels.
Its metadata explicitly identifies an approximation, not measured PBR or inferred
geometry. Pigment and illumination in a source can produce inappropriate relief;
Director must inspect evaluated channels and keep height disabled when unsuitable.

`add_generated_detail` creates a separate nonrepeating fill with editable mapping,
source alpha and a geometric region mask. Transparent source artwork can represent
fictional markings, residue or stickers. This is an editable projected fill, not
an automatic semantic decal-placement system. Markings are fictional by default.

Each candidate provides four object orientations, a larger central closeup,
up to three planned region closeups, evaluated color/roughness/metallic/height/normal
atlases, and two generated-source bypass views. Bypass occurs only on a temporary
scene: generated content is replaced by its average, and scalar detail is removed.
The comparison isolates spatial influence while retaining the overall source color.
Original images, layers, geometry, lighting and artist history remain untouched.

Baked edge wear and chips without detail anchors use one automatically refreshed
source-mask texture in the live shader. This avoids binding four curvature maps,
AO and breakup textures simultaneously; it does not flatten the editable layer
stack. Candidate validation also counts the constructed graph's image bindings
and sampling settings before rendering, in addition to estimating the proposal.

Diagnostics report covered-UV variation, frequency-band proxies, lag correlations,
authored mask coverage and thresholded normal-versus-bypass differences. These are
not physical frequency measurements, surface-area percentages or aesthetic scores.
UV seams and unequal texel density affect them; render differences include the
unchanged background. A low influence warning does not automatically reject art.

Director must submit an image-referenced critique before another candidate or
commit. The first generated candidate requires a concrete correction. There are
at most two correction passes and four generated images per instruction, shared
across candidates. A commit must match the latest reviewed candidate and unchanged
artist state. Any weaknesses remaining at the limit must be disclosed. Saving and
reopening retain packed sources, derived detail, editable masks and provenance.

Verification: `tests/integration/director_generated_surfaces.py` runs in visible
Blender with Eevee; `tests/integration/director_candidates.py` checks lifecycle,
isolation, stale inputs and scene coordination. Neither uses paid inference.

## Localized surface generation

Director can call `inspect_surface(texture_set_id, region_id, normal_direction,
up_direction)` after inspecting or querying a geometric region. Directions are
world-space vectors. The read-only tool returns an orthographic silhouette and
its exact projection frame. It has no object-category rules, semantic component
coordinates, or predefined surface description. Director supplies the art
direction and generated-image prompt from its observations and the artist request.

`assign_projected_surface` assigns the generated source and shared derived scalar
detail to that frame, restricted by an ordinary editable region mask. The frame
is stored on the fill layer, so candidates and undo do not allocate projection
objects or modify geometry, UVs, or material slots. Center, size, axes and depth
are editable in the decal controls. Projected color samples natively in Eevee;
projected derived channels use the existing versioned source boundary. Their
cache identity includes the projection frame, so moving a source also updates
roughness/relief. Source images remain packed and available after reopening.

The template is placement evidence, not a finished texture or semantic analysis.
A nonrepeating source still requires visual inspection of mapping, feature scale,
lighting baked into color, and correspondence between channels. Derived luminance
is an approximation for subtle detail; it cannot recover physical fasteners or
cavities. A shaded grayscale picture is not a valid height map. Successful tool
execution is not evidence that the material meets an artistic quality target.

Focused regression: `tests/integration/director_surface_projection.py` checks
read-only template capture, front/back coverage, shared channel placement, frame
edit invalidation, Eevee compilation, packed persistence and transaction undo.

## Keeping detail within the live shader budget

Director can set an isolated folder's `cache_mode` to `CACHED`. This uses the
existing automatic layer cache: composed channel images replace the folder's
live shader inputs while its descendants remain editable. Source changes trigger
a refresh. This is a rendering choice, not a flattened material or a substitute
for generated detail. Passthrough folders cannot use this optimization; anchor
consumers can require descendants to remain live. The final GPU sampler check
still applies.

Candidate readiness waits for cache jobs to publish before capturing evidence.
Automatic cache publications are excluded from authoring identity and undo
conflicts; explicitly frozen cache content remains part of authored identity.
Run the projection integration with `OP_TEST_AUTO_CACHE=1` to cover automatic
refresh, sampler budgeting, packed persistence and undo through this path.

Save/render flushing also drains requested automatic layer-cache jobs and rebinds
their published images. A compiled graph alone is not sufficient evidence that
its cached channels are ready; otherwise a final capture can show white
placeholders even after a correctly textured candidate review.

## Recovering an invalidated candidate

Candidate review binds the proposal to its source pixels, geometry, material,
lighting, and viewing state. Automatically framed evidence derives its view from
the camera orientation/lens or viewport rotation/lens, never Blender's cached
`RegionView3D.window_matrix`. Camera-border zoom and viewport redraws can replace
that display matrix without changing the evidence view. Rendering and validation
use the same stable framing inputs; actual lens/orientation edits still invalidate
review. A wrong or superseded candidate ID is reported
separately from changed inputs. When inputs change after a preview, the error
names the changed categories and permits a fresh preview without requiring a
critique of stale images. The same material can be rendered again for this
recovery; the existing three-preview and time limits still apply. Fresh evidence
must be reviewed before commit. This does not authorize stale evidence or
automatically accept a material's visual quality.

`tests/integration/director_candidates.py` exercises generated-source recovery
through the public review and commit tools, alongside pixel-edit, scene-change,
superseded-ID, proposal-change, and save/reopen checks.
