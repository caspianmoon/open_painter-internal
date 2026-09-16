# Bake Doctor and the Bake Correction Brush (0.9.1)

Painter has to diagnose imported geometry; Open Painter owns it. Two tools use that:
a pre-bake check whose findings select the offending geometry in Blender, and a brush
that paints bake fixes per region instead of editing cages or global distances.

## Bake Check

*Bake Mesh Maps* → the **Bake Check** box at the top (`open_painter.bake_doctor`,
actions Check / Full Check / Select / Select All / Clear). The same pre-bake tier runs
before every bake: an error stops the bake with the report retained, a warning does not.

| Check | What it looks at | Finding selects |
| --- | --- | --- |
| UVs valid | assignments, render UV map, degenerate and outside-tile faces, exact partial overlaps and stacked/mirrored islands (from `baking/diagnostics`) | faces |
| Normals valid | faces whose winding disagrees with a consistent recalculation, on targets and high-poly sources; a mostly-flipped source is reported as inside-out (dark AO, inverted detail) | faces |
| Scale applied | unapplied or negative object scale on targets, sources and cages | objects |
| Low/high naming matched | `_low`/`_high` stems, ambiguous stems, sources with no target, and near-miss suffixes such as `_Hi`, `_HP`, `_High`, `-high` (`Body_Hi` looks like a source for `Body_low` but the suffix is not `_high`); unlisted scene meshes that would match | objects |
| No duplicate vertices | coincident vertices on the targets (split seams, broken smoothing) | vertices |
| Render modifiers match | modifiers that are viewport-only or render-only, subdivision levels that differ between viewport and render | objects |
| Cage clear of geometry | for every vertex of the render-evaluated target, the segment from the surface to its cage point (automatic extrusion or the custom cage) cast against the low-poly mesh itself: a hit is a likely cage intersection; custom cage vertices below the surface | faces + vertices |
| UV island seams | island borders that carry no seam flag (reimported meshes lose seams; bakes are unaffected) | edges |
| Cage-ray coverage | *Full Check* only: the sampled cage-ray miss estimate from `diagnostics.ray_coverage` | faces |

Clicking a finding selects what it reports — faces, vertices or edges on the base mesh
(object or edit mode, with the matching select mode) or the objects themselves — and
makes the object active; selection is the only change. Evaluated indices are reported
as not selectable when render modifiers change the topology. The pre-bake tier costs
milliseconds on typical assets (8–14 ms on the test fixtures); the full tier adds the
20 000-ray coverage sample.

Not covered: margin-versus-island-gap bleeding and anti-aliasing artifacts, which
depend on the bake settings rather than the geometry.

## Bake Correction Brush

*Bake Mesh Maps* → **Bake Corrections** (`open_painter.bake_correction`). Five
corrections, each a managed image at the set resolution, neutral where unpainted:

| Correction | Paints | Applied as |
| --- | --- | --- |
| Projection Distance | a signed world-space distance (0.5 grey is neutral; *Distance Range* sets what full white or black means) | added to the cage extrusion per vertex |
| Skew Correction | a 0–1 weight | ray direction blended from the smooth vertex normal toward the dominant adjacent face normal |
| Projection Direction | a 0–1 weight plus a *Direction* vector | ray direction blended toward that vector |
| Ignore High Poly | a 0–1 weight | a second pass without the sources marked *Ignore Brush Excludes*, composited in |
| AO Exclusion | a 0–1 weight | a second AO pass with the part's own occluders only (Individual Parts scope), composited in |

**Bake Correction Brush** starts a native paint session on the chosen map (the layer
stack is untouched; the shader ignores the canvas). The *Value* field is what the brush
paints — a distance for Projection Distance, a weight otherwise — and steps by
0.001 with ctrl-scroll. While painting, the **corrected cage** is drawn as a wire
overlay, and when a stroke ends the maps' painted region is rebaked automatically
(*Rebake Painted Region After Stroke*): every face outside the region is routed to a
tiny scratch image so only the region's texels cost anything, and the result is
composited into the existing map under the same image. *Rebake Region* and *Rebake
All* do the same on demand; *Clear* resets a map.

At bake time Projection Distance, Skew and Direction are sampled per vertex of the
render-evaluated target — each UV corner two texels inside its face — and turned into
one owned cage mesh per part; Cycles bakes with that cage, so a painted fix has exactly
the semantics of a hand-edited cage. A vertex on a UV seam averages its islands (a cage
has one position per vertex). With a custom cage the corrections apply on top of it.
Painted corrections enter the maps' provenance, so a map reads *Stale* after a stroke
until it is rebaked.

## Tests

`tests/integration/bake_doctor.py` plants every defect (flipped faces, an inside-out
source, duplicate vertices, unapplied scale, a `_Hi` suffix, a viewport-only modifier,
a torus whose inner-ring cage crosses its hole, unmarked seams) and checks each is
found and selected. `bake_corrections.py` bakes a tilted plate hovering above a
high-poly cube: without corrections the top face is flat; with Projection Distance
painted white the plate appears only where painted (about 6 % of the island, nothing
outside the margin), the corrected cage lifts the interior vertices by extrusion plus
range, a region rebake restores the baseline into the same image, and Ignore High Poly
removes the plate where painted. `bake_correction_ui.py` drives a real brush stroke in a
window and waits for the scheduler's live region rebake.
