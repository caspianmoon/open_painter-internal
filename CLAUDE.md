# Open Painter: notes for coding agents

A Blender 5.2 add-on (`src/open_painter`) with a portable C17 core (`libopenpainter`).

- **The engine and its performance history:** `docs/native/deep-stacks.md`.
- **Platforms and vector paths:** `docs/native/platforms.md`.
- **Testing:** `TESTING.md`.

Project rules:

- Zero failing tests: triage and fix every red test, including ones that predate your
  change.
- Don't run large Blender benchmarks (100+ layers, 2K+) on a shared machine without asking.
  Run one Blender process at a time, through the memory guard (`scripts/run_guarded.py`).
- Native kernels must be bit-identical to the Python they replace, with no FMA contraction,
  a scalar path and a test for every vector path.
- The managed material is exactly three nodes, and there is one node editor type, Open
  Painter Graph.
- Ask before pushing.
