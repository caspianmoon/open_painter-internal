# Layer workflow audit — 0.1.10

Reference: [Adobe layer stack](https://experienceleague.adobe.com/en/docs/substance-3d-painter/using/interface/layer-stack/layer-stack) and [creating layers](https://experienceleague.adobe.com/en/docs/substance-3d-painter/using/interface/layer-stack/creating-layers), accessed September 12, 2026.

Implemented: insert layers above selection, create an empty folder from the toolbar, duplicate a subtree next to its source with remapped IDs, select its copied root, and separate the viewed stack channel from brush targeting. Click a row’s opacity to edit its channel opacity and blend mode. Scalar channel swatches display scalar values.

Validation: `tests/integration/layer_workflows.py` checks insertion, nested ownership, adjacent folder duplication, selected root identity, and channel independence. The material/hierarchy and regional compositor regressions remain relevant. `shelf_ui.py` checks real GPU-drawn controls and T/N in Blender.

This is a targeted behavior update, not a claim of full parity. The current brush engine still targets one channel per stroke. Full multi-channel material strokes, Substance procedural asset execution, and the complete Adobe effect stack are not established by these tests. The historical PDF inventory contains experimental implementation claims and must not be treated as current verification.

Performance and library changes carried into this release are documented in improvements-0.1.9.md with measured limits; no end-to-end real-time brush latency claim is made.
