# UDIM tile access gate (M11), rerun on Blender 5.2.1 LTS

Historical API research. The integrated [UDIM workflow](udim-workflow.md) now
keeps authoritative per-tile managed images and does not rely on switching the
active tile to read `Image.pixels`. The results below describe the earlier probe.

The earlier gate ([milestones-udim-probe.json](performance/milestones-udim-probe.json))
tried one path, `Image.pixels` after `tiles.active_index`, and failed. It said
so explicitly: a failed path, not proof that every access method fails. This
rerun of `scripts/probe_udim.py` tests six paths on the same build
(`9e2066aef7ef`, macOS Apple Silicon). Raw reports:
[window run](performance/udim-probe-gui-0.2.5.json) and
[background run](performance/udim-probe-background-0.2.5.json).

| Path | Result | Evidence |
| --- | --- | --- |
| `Image.pixels` + `tiles.active_index` | FAILED | Both tiles read as tile 1001's red. The RNA accessor acquires the buffer without an image user. |
| Per-tile files through a `<UDIM>` filepath (`save`, load tile file, rewrite tile file, `reload`) | **PASS** | 1001 read red, 1002 read green; after rewriting the 1002 file and reloading, 1002 is blue and 1001 still red. |
| `gpu.texture.from_image` on the tiled image, `read()` | FAILED | Returns one 16×16 `SRGB8_A8` layer holding tile 1001 only; in background mode the call aborts the process instead of raising. |
| Native texture paint onto a plane whose UVs lie in tile 1002 | **PASS** (window only) | After `save`, the 1002 file is painted white and the 1001 file is unchanged. |
| Cycles CPU emission bake onto a plane in tile 1002 | **PASS** with `use_clear` off | 1002 is baked blue and 1001 stays red. With the default `use_clear` on, the bake clears **every** tile: 1001 turned black. |
| `pack()`, save `.blend`, delete tile files, reopen | **PASS** (background run) | Two packed files; both tiles present after reopen; 1001 red and 1002 green read back through freshly written tile files. In a windowed startup script `open_mainfile` ends the script, so reopen is NOT_RUN there. |

## What this establishes

- Independent per-tile pixel read and write are possible on the supported
  build, through tile files. A disk round trip per tile is the only proven
  transfer; there is no in-memory or GPU path to a non-first tile from Python.
- Blender's native brush writes to the correct tile. Painted tiles can be read
  back after `Image.save()`.
- Cycles bakes address the correct tile, but a tile-aware bake must disable
  `use_clear` or restore untouched tiles itself.
- Packed tiled images survive save and reopen with both tiles.

## What it does not establish

- These are isolated 16×16 probes, not an Open Painter workflow: no texture
  set, layer stack, evaluator output, export profile or provenance record has
  been exercised with two tiles.
- No performance figure: a file round trip per tile per publication would be
  far slower than the current in-memory `foreach_set` transfer and would need
  its own benchmark before any interactive claim.
- Nothing in the evaluator, caches, output handles, projection, provenance,
  package paths or export names carries a tile identity yet.

## Design consequence for the UDIM subsystem

Given this evidence, a UDIM implementation on this build would have to keep
**one plain authoritative image per tile** for evaluation and export, and treat
the tiled image only as the native paint target, synchronised through tile
files on explicit boundaries (stroke end, save, export). That is a different
architecture from extending the current single-image handles, so it remains a
dedicated work package. No UDIM UI is advertised by this change.

## Command

```sh
BLENDER_USER_RESOURCES=.blender-test/user OP_UDIM_REPORT=.blender-test/udim-probe-gui.json \
  /Applications/Blender.app/Contents/MacOS/Blender --factory-startup --python scripts/probe_udim.py -- --quit -ApplePersistenceIgnoreState YES
BLENDER_USER_RESOURCES=.blender-test/user OP_UDIM_REPORT=.blender-test/udim-probe-background.json \
  /Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup --python-exit-code 1 --python scripts/probe_udim.py
```
