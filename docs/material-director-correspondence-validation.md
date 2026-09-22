# Geometry-aware candidate correspondence

Implemented from `Material_Director_Geometry_Aware_Texture_Implementation.md`.

The candidate now retains seven viewing transforms and evaluated geometry internally. Its
read-only `inspect_texture_correspondence` tool returns targeted rendered, UV, source and
geometry evidence. `replace_surface_source` supports local edits with bounded source windows
and global source replacement. Both participate in the existing preview/critique/commit flow;
no artist transaction occurs during inspection.

## Validation

- `tests/integration/director_correspondence.py`: real Eevee views of a continuous bent surface
  with separate, reversed UV islands. Tests pixel/triangle/barycentric/UV lookup, paired edge
  orientation, orthographic picking, foreground occluders, stale candidate rejection, position
  and world-normal crops, missing-map reporting, packet caching and temporary-resource cleanup.
  Verifies UV, triplanar, decal and metric source windows. A simulated generator intentionally
  changes the whole image; only the inspected windows reach the candidate. Checks exact outside
  preservation for albedo, derived detail and separately generated roughness/height, then reviews
  the new candidate and commits once. Also rejects a global replacement of a locally critiqued defect.
- `tests/integration/director_correspondence_reopen.py`: saved local corrections and derived
  detail remain packed and preserve outside pixels after reopening; one undo removes the transaction.
- `tests/integration/director_candidates.py`: existing isolation, shared-slot, scene-input,
  stale-review, correction, commit and save/reopen safeguards, extended for top/bottom views.
- `tests/integration/director_surface_maps.py`: existing reference-conditioned scalar maps,
  normal relief, alignment/coverage, save/reopen and undo.
- `tests/unit/test_generated_surface_workflow.py`: all-view acknowledgment and current
  correspondence evidence required for geometry/texture critiques.
- `test_material_director*.py`: Director unit regressions.
- `scripts/probe_director_codex.py`: the managed 0.154.0 app-server advertises the new Painter
  tool with the existing restricted tool inventory; dynamic-tool and companion roundtrips pass.
  Uses a local fake provider, no live account.

The correspondence fixture completes in approximately 13 seconds on the development machine.
Its packet PNGs, result JSON and saved blend are under `.blender-test/director-correspondence/`.
These synthetic checks validate implementation and preservation, not artistic quality. No new
live AI dragon generation or aesthetic success claim is included in this validation.

## Boundaries

UV pairing uses manifold, shared topological edges within a texture-set assignment. It does not
infer welding between coincident disconnected vertices. Geometry lookup accounts for visible
mesh occluders; retained depth is used when the viewport provides actual surface depth. Eevee on
Metal may return a uniform compositing-quad depth instead; this is discarded rather than mistaken
for the material surface. Geometric picking cannot promise exact shader-transparency correspondence.

Direction arrows represent component principal-axis tangents, not semantic anatomy. Source windows
are conservative neighborhoods. Reused/tiled sources may affect other occurrences, so every
correction must be checked in all views. Wrong mapping or absent back coverage can require placement
changes or an additional regional source. Numerical diagnostics do not approve artistic quality.
