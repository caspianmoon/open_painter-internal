# Open Painter 0.13.0

Layer, bake, paint and export PBR texture sets inside Blender: editable smart materials with
authored master controls, mesh-aware masks, continuous material painting, a focused bake and
export flow with padded, safely published exports, and an optional material assistant.

## Requirements

- **Blender 5.2.0 or newer** (`blender_version_min` in the manifest).
- **Tested on Blender 5.2.1 LTS, macOS 26 (Darwin 25.6), Apple Silicon (arm64).** Other
  platforms and newer Blender versions have not been tested; the add-on uses only Blender's
  Python, NumPy and Cycles/EEVEE, so they are expected to work but are unverified.
- Optional: the **Material Assistant** needs the Codex CLI and a ChatGPT sign-in; everything
  else works without it. No engine, SciPy or account is needed.

## Install

1. Download `open_painter-0.13.0.zip` from this release.
2. Blender → **Edit → Preferences → Add-ons** → menu at the top right → **Install from Disk** →
   choose the ZIP. Enable **Open Painter** if it is not enabled already.
3. Open any editor's **Editor Type** menu and choose **Open Painter**.
4. Select a UV-unwrapped mesh, click **+** in Texture Set, bake the maps the material needs
   from the **Bake** tab, apply a smart material from the shelf, paint, then **Export**.

The release was verified with the clean-install test (an empty profile, only this ZIP, two
sessions through set-up, bake, material, controls, layers, mask, a native brush stroke, a
material brush stroke with live feedback, save, reopen, padded exports for Blender and
Unreal, disable/re-enable, reopen) and an upgrade session that opens a file saved by 0.12.2
(`scripts/run_clean_install.py`, record in `.blender-test/clean-install.json`).

## What changed in 0.13.0 — the quality remedy

Full report with measurements and limits: `docs/quality-remedy-0.13.md`.

- **Paint Material.** The paint inspector leads with a material brush: one continuous gesture
  paints the enabled PBR channels together (Color, Roughness, Metallic, Height, plus the other
  channels under Stroke Details) and the material changes under the pointer while you drag.
  The gesture is resampled at the brush spacing between pointer events and re-projected onto
  the surface, so a fast drag leaves no holes; a miss, an unassigned slot, a hidden object or
  a silhouette edge lifts the brush instead of bridging. The stroke goes to the selected set
  by default; **Paint Across Sets** lists the sets it may reach and links only those the
  pointer crosses. Pressure to size and pressure to flow are separate settings; **Depth
  Limit** keeps a nearby parallel surface clean. Esc discards the stroke in progress and
  exits when idle; one gesture is one undo step; a save during a gesture commits it once.
  Zero values (black, roughness 0, metallic 0) are valid paint. Existing single-channel
  painting and mask painting are unchanged; strokes recorded by earlier versions render as
  before.
- **Faster settle after a stroke.** The stroke boundary of the live material is regenerated
  incrementally (only the rows the stroke touched, from the previous result), the first
  boundary of a layer shows its paint canvas instead of a grey slab, and published
  boundaries no longer accumulate over a session.
- **UV padding on export.** Every export extends its maps into the gutters from the nearest
  valid texel, with validity and island ownership derived from the export geometry — never
  from pixels — so black, transparent and unpainted surfaces are as valid as painted ones.
  Modes: **Automatic** (4 px at 512 … 64 px at 8K), **Custom**, **Fill Empty Space**,
  **None**; the effective width shows next to the resolution, and the last export reports
  islands whose gutters meet, undersized islands and overlaps rather than promising clean
  mips. Resampling to another resolution filters only valid texels (no more empty-texel
  contamination of island edges); tangent normals are filtered as vectors; packed maps are
  padded per semantic channel before packing; colour is encoded once. Files saved before
  0.13 get Automatic padding and the Export view says so until you choose a mode.
- **Safe export publication.** An export request — every map of every set, manifests and
  the mesh — is generated in staging and published as one transaction: existing files are
  backed up, replaced atomically and restored on any failure; a journal beside the outputs
  lets the next export recover an interrupted one; creating files stays exclusive unless
  replacing was allowed; the previous mesh is never deleted before its replacement exists;
  a manifest is never written for files that did not land. The Export to Target button and
  evaluated bundles use the same path, and an export no longer edits a set's saved settings.
- **Engine validation kit.** `engine_validation/` holds the fixture builder, an Unreal Editor
  Python script and a Unity URP editor script with the exact import settings they apply and
  record. The engines were **not run** for this release (no editor on the reference
  machine); their status is reported as NOT RUN, not as passed.

## Known limitations

- Material-brush latency and settle on the 2K/25-layer reference: event-to-visible p95
  ≈ 120–150 ms (target 100), release-to-current ≈ 0.4–0.5 s with four channels (target 250 ms;
  faster with fewer channels), ≈ 1.5 s at 4K. The first stroke on a layer that had none
  rebuilds the material once (≈ 0.7 s at 2K).
- Surface following: the default *Surface* reach paints the surface under the pointer and
  what connects to it without turning away (a same-facing sheet behind, a neighbouring
  object, a disconnected piece or a pocket under a lip stays clean); a surface that
  continues smoothly under something else is painted along. *Depth Band* is the explicit
  broader reach (every surface within Depth Limit × radius) and what older strokes keep.
- Fill Empty Space is exact within 64 px of an island and coarser beyond.
- Unreal and Unity validation: NOT RUN (scripts and fixtures delivered).
- Tablet pressure and tilt: tested with synthetic input only.
- The earlier limitations of 0.12.x (asset shelf catalog tabs, EEVEE re-sync of very large
  materials, mesh maps for mesh-aware generators, the Assistant's external process) remain.

## Verification of this release

Blender 5.2.1 LTS on macOS Apple Silicon: the pure NumPy suite (incl. the padding oracle
tests), the background integration suites (incl. material painting replay, export padding,
export safety, compatibility contract), the real-window probes (incl. the material painting
probe at 2K and 4K and a 500-stroke session), the material baseline (all 50 recipes
byte-identical to 0.12.2), the packaged-extension install test and the clean-install and
upgrade sessions. Details and evidence paths: `docs/quality-remedy-0.13.md`.
