# Contributing

Thanks for helping. Bug reports, fixes and new features are welcome as GitHub issues and pull
requests. Contributions are licensed under the GPL-3.0-or-later, like the rest of the project.

## Setting up

- Use Blender 5.2 or later. Its bundled Python and NumPy run the add-on.
- Load the source tree in Blender's Python console by adding `src` to `sys.path`, importing
  `open_painter` and calling `open_painter.register()`. Or build a zip with
  `python3 scripts/build_extension.py` and install it from disk.
- The native library is optional for development: `python3 scripts/build_native.py` builds
  and tests it; point `OP_LIBOPENPAINTER_PATH` at the built library to use it.

## Testing

- `python3 scripts/run_pure_tests.py` (with a NumPy-capable Python 3.11+, such as Blender's
  bundled interpreter) runs the reference and unit suites without Blender.
- `python3 scripts/run_tests.py --blender /path/to/blender` runs the Blender integration
  suites in isolated, factory-startup profiles; `--gui-only` and `--gpu-only` run the
  real-window probes. Each launch goes through `scripts/run_guarded.py`, which caps memory.
- See [TESTING.md](TESTING.md) for the individual suites.

Add a regression test with every fix, especially for data loss, bake-state restoration,
composition math, serialization and exported pixels. Prefer tests that check behaviour over
tests that mirror the implementation. Check register/unregister cycles after lifecycle changes.

## Design rules

- Keep persistent state in RNA and stable identifiers separate from names. Keep evaluation
  independent of the UI and registration centralized.
- Never delete Blender data the add-on doesn't own, and never overwrite external files as
  error recovery.
- Native kernels must give bit-identical results to the Python they replace, with a scalar
  path and a test for every vector path.
- Document changes to the smart-material format in
  [SMART_MATERIAL_FORMAT.md](SMART_MATERIAL_FORMAT.md) and bump its version for incompatible
  schema changes.
- Don't infer cross-platform support from tests on one platform.

Developer Mode in the extension preferences enables verbose logging, evaluation timings and
texture-set IDs.
