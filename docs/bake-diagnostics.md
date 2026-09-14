# Bake diagnostics: exact UV overlap and selectable findings

Implemented on `task/generated-material-quality` alongside the separate
generated-material work; this change touches only `baking/diagnostics.py`,
`geometry/uv_overlap.py`, the bake panel, the cache reset points and tests.
It closes the M5 gaps "exact UV overlap diagnosis" and "optional sampled
ray/cage analysis", and the M5 acceptance item "diagnostics point to
selectable problem regions where feasible". It does not
change bake algorithms, map resolution, projection or any authored data.

## What was missing

`baking.diagnostics.inspect(ts, evaluated=True)` reported degenerate faces and
faces outside the 0–1 tile, but no UI reached that tier: only the cheap ERROR
checks ran, implicitly, before a bake. Overlap was not analysed at all.

## Analysis

`geometry/uv_overlap.overlaps(uv, eps, exclude_same)` takes evaluated UV
triangles for every object assigned to the texture set (they share one UV
space) and returns exactly overlapping pairs:

- Broad phase: a uniform grid whose cell is the median triangle extent; pairs
  are generated per cell without a Python loop and deduplicated through a
  packed integer key. Bounding boxes are compared before the narrow phase.
- Narrow phase: separating-axis test on all six edge normals. Two triangles
  overlap only when their intersection has positive area; sharing an edge or a
  corner is not overlap. One face's own fan triangles are never paired.
- Tolerance: an overlap must be deeper than a quarter texel at the set's
  authored resolution (`0.25 / resolution` UV units). This is a stated
  threshold, not a claim that shallower overlaps never influence a texel.
- Classification is per polygon, not per triangle: two faces with identical
  corner sets are **stacked**, and opposite winding marks them **mirrored**. A
  mirrored quad triangulates along the other diagonal, so triangle-level
  comparison alone would misreport it as partial.
- Bound: the analysis raises, and the panel reports "skipped", when candidate
  pairs would exceed 16 million. It never returns a sampled or partial answer
  as if it were exact.

`Issue.faces` carries evaluated polygon indices per object. Partial overlaps
are `WARNING`; stacked/mirrored islands are `INFO`, because symmetric assets
stack islands deliberately. Messages name the other overlapping objects.

## Sampled cage-ray coverage

`diagnostics.ray_coverage(ts, samples=20000, seed=0)` is the optional
highest-cost tier and runs from Inspect Bake Setup when Bake from High Poly is
on with Normal or AO selected. It samples the evaluated low-poly surface
area-weighted (deterministic seed), then casts the same rays the bake will:
from an automatic cage, the point pushed out along its corner normal by the
extrusion, travelling inward; from a custom cage, the corresponding cage point
travelling toward the low-poly point. A ray hits when any high-poly source's
BVH is met within Ray Distance (zero means unlimited, as in the bake). Rays are
inward only.

The finding states "N of M sampled rays … missed … a sampled estimate, not
exact coverage", is a WARNING above 1 % misses, and carries the faces that
contained missed samples so they can be selected. Unlimited rays legitimately
pass through the low-poly object and hit sources on the far side, exactly as
the bake does; the test covers that case.

Cost on the reference machine: 20 000 rays against a 125 952-face source take
0.78 s cold (BVH build included); 100 000 rays take 2.29 s.

## UI

Bake Mesh Maps panel → **Inspect Bake Setup** runs the evaluated tier and keeps
the result in a runtime dictionary keyed by scene pointer and set ID (cleared on
file load and unregister; never saved). The panel lists up to eight findings.
**Select Problem Faces** selects reported faces on the base mesh (object or
edit mode). Evaluated indices only address base faces when no modifier changes
the face count; such objects are named in the report instead of guessed.
Selection state is the only change made.

## Measurements

Blender 5.2.1 LTS `9e2066aef7ef`, Apple M3 Pro, background process, Suzanne
with Smart UV Project (island margin 0.002) under a Subdivision Surface
modifier, one 2048 texture set, full `diagnostics.run` including the evaluated
snapshot:

| Evaluated faces | Wall seconds | Partially overlapping faces reported |
| ---: | ---: | ---: |
| 7 872 | 0.24 | 77 |
| 31 488 | 1.02 | 142 |
| 125 952 | 4.67 | 313 |

Before vectorising the pre-existing per-triangle loop and the broad phase the
same cases took 2.20 s, 7.34 s and hit the earlier 4-million-pair bound. Those
Suzanne overlaps are real: for the 7 872-face case all 198 reported partial
pairs have positive shared area by independent point sampling (median
5.0e-7 UV², about two 2K texels; minimum 1.6e-9 UV²).

The unit test compares the grid broad phase against an all-pairs test on 300
random triangles; the integration test builds stacked, mirrored and partially
overlapping faces on a second object in the same set, checks face identities,
selection mapping, the modifier skip and the operator path, and asserts UVs
are unchanged afterwards.

## Limits

- Overlap is exact in UV space only; it does not estimate bake bleeding width
  or margin interaction.
- Ray coverage is a sample, reported as such. It models Blender's inward cage
  projection with one direction per sample and does not reproduce Cycles'
  per-pixel differentials, margins or anti-aliasing.
- Faces outside the single tile are reported, not wrapped, so UDIM-style
  layouts show both "outside" and any overlaps of the wrapped positions are
  not inferred.
- Face selection needs matching base/evaluated face counts.
- Runtime is bounded by the candidate limit, not by a time budget; a dense
  126k-face mesh takes about five seconds on the reference machine.

## Commands

```sh
/Applications/Blender.app/Contents/Resources/5.2/python/bin/python3.13 scripts/run_pure_tests.py
BLENDER_USER_RESOURCES=.blender-test/user /Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup --python-exit-code 1 --python tests/integration/bake_diagnostics.py
```
