# Open Painter: notes for Claude Code

A Blender 5.2 add-on (`src/open_painter`) with a portable C17 core (`libopenpainter`).

- **On Windows or Linux:** read `docs/native/windows-plan.md` first. It gives the state,
  the rules and the plan, step by step.
- **The engine and its performance history:** `docs/native/deep-stacks.md`.
- **Platforms and vector paths:** `docs/native/platforms.md`.
- **Testing:** `TESTING.md`.

Rules the user set:

- Zero failing tests: triage and fix every red test, including ones that predate your
  change.
- Never run large Blender benchmarks (100+ layers, 2K+) without the user's approval. Run
  one Blender process at a time, through the memory guard (`scripts/run_guarded.py`).
- Native kernels must be bit-identical to the Python they replace, with no FMA contraction,
  a scalar path and a test for every vector path.
- The managed material is exactly three nodes, and there is one node editor type, Open
  Painter Graph.
- Ask before pushing.
