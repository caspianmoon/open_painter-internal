# Contributing

Use Blender 5.2's bundled Python and NumPy. Build with `python3 scripts/build_extension.py`.
Run `python3 scripts/run_tests.py --blender /path/to/blender`; add `--gui` for an actual native
viewport brush stroke and editor screenshot. The tests use factory startup and isolated user
resources. Test scenes and logs live in `.blender-test/`; phase acceptance samples live in `examples/`.

Follow the build guide's phase order for changes. Keep persistent state in RNA, stable identifiers
separate from names, evaluation independent of UI, and registration centralized. Add regression tests
for data loss, bake-state restoration, composition math, serialization, and exported pixels. Check
register/unregister cycles after lifecycle changes. Avoid tests that merely mirror implementation.

Never delete unowned Blender data or overwrite external output files as an error-recovery strategy.
Document changes to the smart-material format and update its version for incompatible schema changes.
Use Blender's Python console for interactive experiments; the source entry point can be loaded by
adding `src` to `sys.path`, importing `open_painter`, and calling `open_painter.register()`.

Developer Mode in extension preferences enables verbose logging, evaluation timings and texture-set IDs.
Before a release, rerun the GUI workflow, review the screenshot, check the Editor Type menu and demo in a fresh Blender session,
and validate the extension ZIP with Blender's extension command-line tooling. Do not infer cross-platform
support from successful macOS tests. Contributions are licensed GPL-3.0-or-later.
