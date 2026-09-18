# Open Painter 0.12.2

Layer, bake, paint and export PBR texture sets inside Blender: editable smart materials with
authored master controls, mesh-aware masks, a focused bake and export flow, and an optional
material assistant.

## Requirements

- **Blender 5.2.0 or newer** (`blender_version_min` in the manifest).
- **Tested on Blender 5.2.1 LTS, macOS 15 (Darwin 25.6), Apple Silicon (arm64).** Other
  platforms and newer Blender versions have not been tested; the add-on uses only Blender's
  Python, NumPy and Cycles/EEVEE, so they are expected to work but are unverified.
- Optional: the **Material Assistant** needs the Codex CLI (`codex` on `PATH` or chosen in the
  add-on preferences) and a ChatGPT sign-in; everything else works without it.

## Install

1. Download `open_painter-0.12.2.zip` from this release.
2. Blender → **Edit → Preferences → Add-ons** → menu at the top right → **Install from Disk** →
   choose the ZIP. Enable **Open Painter** if it is not enabled already.
3. Open any editor's **Editor Type** menu (top left of the area) and choose **Open Painter**. It
   opens in that area: Assets along the bottom, the paint viewport in the middle, Texture Set,
   Layers, Inspector and Assets on the right, with Bake, Export and Assistant tabs.
4. Select a UV-unwrapped mesh, click **+** in Texture Set, bake the maps the material needs
   from the **Bake** tab, apply a smart material from the shelf, paint, then **Export**.

The release was verified with a clean-install test: an empty Blender profile, only this ZIP,
two Blender sessions through set-up, bake, material, controls, layers, mask, a real brush
stroke, save, reopen, export for Blender and for Unreal, disable/re-enable and reopen
(`scripts/run_clean_install.py`, record in the repository's `.blender-test/clean-install.json`).

## What changed in 0.12.x

- **0.12.0 — the interface, refactored in place.** One editing target shared by Layers, the
  Inspector, the Nodes view and the asset shelf, with a readable path (Body › Worn Painted
  Metal › Chips). Readable Layers rows with Add Layer / Add Mask / Add Effect / More. A
  selection-specific Inspector: a smart material's master controls first, then its component
  layers; fill, paint, mask and effect controls on their own; View Mask / Back to Material and
  Add Paint to Mask that never rasterises a procedural mask. A focused **Bake** tab
  (Setup / Maps / Results) with truthful map states — Missing, Up to date, Out of date,
  Baking, Failed, Not checked — and honest cancellation. A **Export** tab whose Output
  Preview is exactly what gets written, with explicit scope and no silent widening. Aging,
  geometry effects and analysis in context; the Material Assistant as Target → Instruction →
  Run → Changes; one status line.
- **0.12.1 — master controls for all fifty built-in smart materials** (4–8 each), bound
  through stable ids so renaming, reordering, saving and duplicating keep them, with
  computed controls for inverted amounts, depths, relief and pattern scale.
- **0.12.2 — the launch remedy.** Every amount-like control reaches visually none at 0
  (coverage-gated amounts on cavity-dirt, rust and drips layers); recipes whose top-level
  control did nothing visible were fixed (galvanized oxidation, ash, aluminum dents,
  clearcoat scratches); labels say what they move (Paint Loss / Paint Loss Contrast, Loose
  Fibers, Surface Roughness, Flake Scale, Spangle Scale, Enamel Grain, Mud); the Layers
  strip names its **Blend Channel**; a launch audit of the smart-material, mask-correction,
  bake, export and asset workflows; the clean-install test above; a release package without
  development artefacts.

## Known limitations

- The asset shelf's catalog tabs cannot be enabled from Python; the tool header's type
  filter (Materials, Smart Materials, Smart Masks, …) covers the same categories, and the
  shelf's ☰ menu enables the tabs if you want them.
- A control edit on a very large material settles with Blender's EEVEE re-sync of the whole
  material node tree (≈ 25–30 µs per node; a 48-layer stack of ~10 000 nodes takes 230–300 ms).
- Mesh-aware generators need baked mesh maps; the Bake tab says which maps a material reads.
- CAVITY_DIRT, RUST and DRIPS generators keep some coverage at their own amount 0; the
  master controls gate this (0 = none), the component-level parameters do not.
- The Material Assistant depends on an external Codex process and a ChatGPT account.
- No unassisted usability study with external artists has been run yet.

## Verification of this release

Blender 5.2.1 LTS on macOS Apple Silicon: the pure NumPy reference suite, the background
Blender integration suites, the real-window GUI probes (including the launch audit), the GPU
parity checks, the packaged-extension install test and the clean-install launch test all
passed on the release commit; every smart material's master controls were rendered at their
extremes for inspection (`scripts/render_master_controls.py`).
