# Contributor integration review — 2026-09-28

Reviewed and integrated the twelve commits by `feridmemmedov141098` on
`origin/main` after the common ancestor `8a62636`, into
`task/open-graph-0.4.0` at `ba9d9fa`. The merge preserves the contributor's
history and the branch's current engine, material output node, native mask
graphs, nested mask groups, templates and UDIM workflow.

## Commit decisions

| Commit | Contribution | Result |
| --- | --- | --- |
| `8c84e34` | Automatic GPU selection and CPU fallback | Accepted; retain the saved numeric values of CPU and Scene Device by appending Auto to the enum. |
| `716f988` | Reinstall from source script | Accepted after removing the machine-specific path, using the release packager, validating before disabling, backing up the installation and checking operator results. |
| `5658728` | Texture Set trash button | Accepted. |
| `265dbe5` | High-poly baking disabled by default | Accepted; projection test fixtures now enable it explicitly. |
| `1e794c5` | Shader graph adoption without raw group-pointer reads | Accepted with per-tree identity tags to reject unrelated graphs with matching interfaces, and cleanup compatible with the current engine. |
| `50aac13` | Node editing, row guides and root ordering by height | Accepted after preserving groups/templates and making panel reorders update the graph position baseline. |
| `2c1b95f` | Park masks/filters when their wires are cut | Accepted after fixing cross-layer anchor unlinking, rejected links and parked-entry fixtures. |
| `721632c` | Fill defaults and node controls | Integrated with the contributor's later defaults; Normal enabled alongside Base Color, Roughness and Metallic. Core authoring and node creation now agree. |
| `1103ae3` | Cycles Bevel curvature | Available as an explicit Edge Method. Geometric curvature remains the default because the Cycles method regressed smooth-edge coverage. |
| `d2ca622` | Image nodes, duplication and curvature controls | Accepted after fixing image source precedence, nested duplication and Blender collection-pointer invalidation. |
| `ccc8985` | Rebakes, Edge Wear 2, wider Height and image output | Accepted with staged replacement images instead of writing into the published image; preserves failure isolation, cancellation and undo. |
| `ecc856b` | One mapping block per fill node | Accepted through explicit derived node controls; ordinary channel edits no longer overwrite other channels' independent mappings. |

## Problems corrected

- **Published bake data was used as scratch.** A failure, supersampling resize or
  cancellation could damage an existing map and its undo state. All bakers now
  stage fresh images, publish completed results, rebind this texture set's image
  consumers, keep canonical names and preserve the old packed data for undo.
  Unrelated material and texture-set references retain their chosen images.
- **The proposed curvature default lost smooth-edge coverage.** The existing
  geometric regression expected peaks above 0.9 on a smooth bevel; the Cycles
  method produced approximately 0.029. A thin-reach case also fell to about
  0.054 against a 0.5 threshold. The geometric tests and their thresholds remain
  intact. Cycles Bevel has a separate CPU bake test, method-specific provenance
  and controls, and remains selectable for its reference shader behavior.
- **Mapping callbacks changed unrelated channel state.** Numeric/image edits
  could copy one channel's mapping over an entire fill. Only edits to the
  explicit shared mapping controls now propagate, and only the edited field.
  Derived controls are excluded from snapshots and authoring documents.
- **Graph operations could target stale Blender collection references.** Mask
  sharing and effect duplication now capture stable IDs before collection
  moves. Folder and mask-group duplication copies subtrees once with fresh
  identities and internal references. Simultaneous node deletions remove the
  intended layer subtree without interpreting pending panel additions as deletes.
- **Link edits could enable rejected entries or disable shared sources.** Chain
  endpoints are enabled after a successful reorder. Removing a cross-layer
  anchor feed does not park its source mask. Image feeds clear competing source
  fields, respect independent channel mappings, and detach on panel overrides.
- **Idle graph synchronization could reverse panel ordering.** Root positions
  are compared with the last synchronized positions; panel reorders move their
  graph rows and update that baseline.
- **Owner tags alone could adopt the wrong shader tree.** New tree/instance
  identity tags disambiguate adoption without dereferencing stale group trees.
  Ambiguous legacy state rebuilds safely.
- **The reinstall helper could remove a working installation too early.** It
  now builds and validates first, keeps a backup, stops on disable/install/enable
  errors, and refuses to save factory-startup preferences. The helper itself was
  not run against the user's installed extension.

## Validation

Environment: Blender **5.2.1 LTS** on macOS; Blender tests used isolated temporary
profiles. The packaged extension was built from the reviewed source.

- Pure suite: **285 tests run, 2 skipped, no failures**.
- Additional queue/layout unit suites: **8 tests passed**.
- **16 targeted background integration scripts passed**:
  `graph_mirror.py`, `graph_templates.py`, `graph_link_edits.py`,
  `nodes_and_assistant.py`, `material_nodes.py`, `contributor_regressions.py`,
  `shader_rehydrate_safety.py`, `bake_isolation.py`, `bake_override.py`,
  `bake_view.py`, `geometric_curvature.py`, `curvature_cycles.py`,
  `edge_map_set.py`, `edge_map_jobs.py`, `udim_workflow.py`, `phase14.py`.
- Real Blender UI: `graph_templates_ui.py` passed all five steps, including the
  image-node picker, mapping controls and output; its screenshot was inspected.
- ZIP validation and `install_extension.py` passed: actual extension installation,
  three disable/enable cycles, all 50 material recipes and 28 generated image
  assets, GPU material/native mask graph evaluation and installed authoring API.
- Python compilation and whitespace/conflict checks passed.

New regressions exercise saved bake-device enum values, default channels,
independent/shared mapping edits, root ordering, invalid links, image feeds and
panel overrides, nested duplication, effect identities, multi-node deletion,
staged rebake failures, cancellation, shader-tree identity and method switching.
Existing edge-map job checks also exercise actual undo restoration.

### Limits

Actual Metal GPU normal and Cycles curvature bake attempts exceeded the
300-second test timeout on this machine. Device-selection/fallback unit tests
passed; real Cycles integration bakes were then pinned to CPU and passed. This
does not establish GPU bake reliability or diagnose the GPU timeout. Current
engine GPU rendering was exercised by the material and installation checks.
The entire repository integration suite and the reinstall helper against a
real user profile were not run. No remote push or user-profile installation
was performed.

Transient raw logs and the UI report are in `/tmp/open-painter-review/`; the
candidate archive is `/tmp/open-painter-reviewed.zip`.
