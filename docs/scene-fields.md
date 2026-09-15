# Live scene fields — 0.5.0

A scene field is a mask source that reads the actual scene: which way a face
points, how far it is from other objects, how high it sits, how convex the mesh
is *right now*. Since 0.5.0 the analysis is live and cooperative: move a bolt and
the rust follows, bevel an edge and the wear widens, with no bake, no export and
no frozen UI.

## Kinds

| Kind | Reads | Inputs |
| --- | --- | --- |
| Upward / Downward Orientation | world normal against Direction | direction |
| Relative Height / World Height | position along Direction, relative to an object's origin / absolute | object (relative), bounds |
| Distance to Ground | 1 at the ground plane (world Z = 0 or the Ground object's origin along Direction), 0 at Distance above it | ground object, distance |
| Object Distance / Contact Proximity | nearest distance to the influencers, normalised by Distance (Distance = 1 far, Contact = 1 near) | influencers, distance |
| Proximity | 1 at an influencer, 0 at its radius: Distance by default, per object `open_painter_field_radius` | influencers, distance |
| Directional Exposure | 1 where a ray along Direction is free within Distance | influencers, direction, distance |
| Radial Heat / Moisture / Directional Soot | radial falloff from an object's origin | object, distance, direction |
| Convexity / Concavity (live) | signed turning density of the evaluated mesh within Reach, relative to the surface's baseline | reach |
| Edge Exposure (live) | Convexity plus crease lines above the bake settings' crease angle, Reach wide | reach |
| Named Attribute / Vertex Group | a mesh attribute rasterised through the UVs | attribute |

Every kind has a **Falloff** (Linear, Smooth, Squared, Root) applied when the
field is sampled — it never re-analyses. **Analysis Resolution** goes up to 1024
(object queries up to 512).

**Influencers** for the query kinds are either the chosen object/collection or
*Every Other Mesh*: every visible mesh in the scene except this set's targets,
generated helpers and an *Exclude* collection, capped by *Max Objects* (an error
asks you to narrow the scope beyond it). Adding a bolt to the scene therefore
re-triggers the field.

**Presets** (mask properties → Presets): *Dirt in crevices near other parts*
(Up × Proximity(scene) × Concavity), *Radial rust from bolts*, *Ground mud*,
*Live edge exposure*. They are ordinary stacked masks: the new **Lighten (Max)**
and **Darken (Min)** combine modes act as OR / AND of fields, and every term's
levels, invert, strength and falloff stay live shader uniforms.

## Live edges without a bake

Edge Wear and Chips masks have **Edges From**: *Baked Edge Maps* (the four-reach
Curvature set with Edge Size) or *Live Geometry*. Live edges are an Edge Exposure
field on the mask's own influence (Edge Reach, Analysis resolution) — a bevel
modifier, an edge loop or a mesh edit changes the wear as soon as the analysis
republishes. Live edges evaluate on the CPU reference and as a raster boundary in
the shader-native preview; the legacy GPU mask path declines them. Live edge
values are not identical to the baked map: the baked kernel integrates turning
over a physical radius in texel space, the live analysis measures it per vertex
(each edge hands `angle × min(length, 2·reach)` to its vertices, each face corner
at most a half-disc of area, then diffuses over the mesh graph for `reach` worth of
edges) and subtracts the surface's 25th-percentile density, so a sphere reads flat
and a coarse mesh's flat faces stay at zero. Dense meshes read the same bands; a
six-quad cube reads its creases only (that is what the crease lines are for).

**Auto-rebake Edge Maps** (bake settings, off by default) rebakes the baked set in
the background two seconds after a target mesh's *geometry* changes (transforms
never trigger it), so baked-edge materials can follow mesh edits too.

## How it stays responsive

`evaluation/field_jobs.py` owns the analyses. A LIVE field whose inputs changed
(`geometry_key` in `evaluation/dependencies.py` now separates target changes
from field-signature changes) is *requested*, not computed: the evaluation keeps
sampling the last published raster. The scheduler timer starts one job per
scene once the inputs have been quiet for 150 ms and no target is in edit mode,
advances it in strips under an 8 ms budget with `Analysing <mask>… N%` in the
status line, and publishes atomically (image swap + provenance) before
invalidating the set; a job whose inputs changed underneath it never publishes,
and moving an object back to a published position discards the pending work.
Boundary identities (`shader_graph._scene_identity`) and CPU cache keys use the
published image while the engine runs, so nothing re-evaluates until a
publication. Without the timer (background scripts, `Update Analysis`) the
analysis stays synchronous.

Object queries run through Geometry Nodes (`geometry/gn_queries.py`): a
disposable scene holds a point-cloud carrier (one vertex per texel) and the
influencers' evaluated triangles with a per-face radius attribute; *Geometry
Proximity* / *Sample Nearest Surface* / *Raycast* evaluate all points in one
multithreaded pass and the result is read back with `foreach_get`. Any node or
socket mismatch falls back to the per-point BVH reference (`geometry/queries.py`),
which the tests compare against to 1e-4.

Tests: `tests/integration/live_fields.py` (queries vs reference, kinds, scope,
job engine), `live_edge_exposure.py` (fields on a cube and a cylinder, live edge
wear on CPU and shader, bevel follow, structural fields), `auto_edge_rebake.py`,
`live_fields_ui.py` (real window), `tests/unit/test_live_curvature.py`.
