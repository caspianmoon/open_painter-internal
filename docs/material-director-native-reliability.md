# Material Director native workflow reliability

These changes run in the addon invoked by Blender's Material Assistant panel.
They do not require a scene-preparation script or a benchmark runner.

- The panel provides **Create Texture Set for Selected Mesh** when no set exists.
- Missing or stale geometry maps are listed before sending an instruction. **Bake
  Required Maps (Active Set)** starts Blender's normal cancellable bake queue for
  normal, AO, curvature, world normal, position and thickness. The instruction is
  preserved; press Run after baking. Scene scope still requires ready maps for
  every targeted set. Existing UVs and geometry are not silently repaired.
  When no high-poly sources are configured, this action bakes the assigned mesh;
  configured high-poly sources and their bake mode remain respected.
- Offscreen captures restore the editor's view properties, including on errors.
  Candidate view identity ignores insignificant float32 evaluation noise while
  retaining meaningful camera changes and all other freshness checks.
- Geometry masks share a bounded, content-addressed UV sampling cache. UV edits,
  triangle order and resolution change its key; overlapping texels retain the
  same last-triangle-wins behavior. No live Blender objects enter this cache.
- Disposable review materials evaluate at at most 1024 pixels, including focused
  correspondence inspections. The review packet reports this limit. Original
  texture resolution, source images, geometry and the committed material are
  unchanged. This is a review-performance limit, not proof of final microdetail.
- A failed candidate render preserves the previous candidate and does not spend
  a completed correction slot. Failed work still consumes the existing time
  budget. A previous candidate cannot bypass freshness or visual review checks.
- Vertex-group/attribute fields with an explicit object are validated and sampled
  on that assigned object. Without an explicit object, the existing all-assigned
  objects requirement remains. This resolves the valid dragon horn group being
  rejected because eyes and teeth did not contain it.

Verification: focused unit coverage checks view restoration on successful/failed
draws, numerical view identity, UV-cache overlap parity, invalidation and memory
limits, attribute scoping, geometry-map preflight and generated-surface review
rules. Native Blender UI verification is recorded separately; unit results are
not a substitute for a successful live Director run or an aesthetic assessment.

Native house test (2026-09-23): installed the package, opened `house.blend`,
created its texture set and baked all six required maps through the Blender UI.
Connected through ChatGPT OAuth and submitted the unchanged instruction
"Texture this house realistically, like a lived in house for more than 25 years"
in Create mode with GPT-6-Astra. Director inspected the scene but stopped at
region UV validation before generating or applying a material. Reported valid
surface fractions ranged from approximately 0.879 to 0.992 across the queried
regions, while the native Bake Check reported UVs valid. The validators use
different tile-boundary tolerances; the cause of this discrepancy remains under
investigation. This run does not validate candidate rendering or final appearance.
