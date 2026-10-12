# Notices and credits

Open Painter is licensed under the GNU General Public License, version 3 or later (see
[LICENSE](LICENSE)). It includes or adapts the following third-party work.

## Code

- **[Ucupaint](https://github.com/ucupumar/ucupaint)** by Yusuf Umar and contributors,
  GPL-3.0-or-later. Open Painter adapts its UDIM tile enumeration
  (`src/open_painter/geometry/udim.py`), the edge pairing and signed dihedral weighting of
  its curvature bake (`src/open_painter/baking/maps/mean_curvature.py`), its selection-mask
  bake (`src/open_painter/baking/maps/selection.py`), and its bake-world and padding
  conventions (`src/open_painter/baking/staging.py`, `src/open_painter/baking/cycles_baker.py`).
- **[zlib](https://zlib.net)** by Jean-loup Gailly and Mark Adler, zlib license.
  `adler32_combine` in `src/open_painter/export/image_writer.py` is a Python port of
  zlib's function of the same name.

## Assets

- **[Poly Haven](https://polyhaven.com)**, CC0 1.0. The surface textures listed in
  `src/open_painter/assets/surfaces/catalog.json` (with their source URLs and checksums),
  and the `moss_01` model and textures by Rob Tuytel
  (`src/open_painter/assets/geometry/moss_01/provenance.json`).
- **Generated detail maps** in `src/open_painter/assets/generated/` were made for Open
  Painter with OpenAI's image generation and are distributed under the GPL-3.0-or-later.
  Their provenance is recorded beside them.

## Services used by optional features

- **Material Director** downloads the OpenAI Codex CLI (Apache-2.0) from the npm registry
  when the user connects it, and uses it with the user's own OpenAI account. The CLI is not
  distributed with Open Painter.
