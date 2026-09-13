"""Run acceptance phases in isolated Blender processes, abort on any failure."""

import argparse, atexit, json, os, subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--blender", default="/Applications/Blender.app/Contents/MacOS/Blender")
parser.add_argument("--gui", action="store_true")
parser.add_argument("--gui-only", action="store_true")
parser.add_argument("--gpu-only", action="store_true", help="Run real-window live GPU math and workflow regressions")
parser.add_argument("--package", type=Path, help="Validate and install this candidate ZIP in isolated resources")
args = parser.parse_args()
env = os.environ.copy()
# Tests never install into or save the artist's preference directory.
env["BLENDER_USER_RESOURCES"] = str(root / ".blender-test" / "user")
# Historical phases generate these bundled fixtures; do not leave the checkout edited.
originals = {p: p.read_bytes() for p in [root / 'examples/phase01.blend', root / 'examples/bake_cube.blend', root / 'docs/native-painter.png'] if p.exists()}
@atexit.register
def restore_fixtures():
    for path, content in originals.items():
        path.write_bytes(content)
if args.package:
    env['OPEN_PAINTER_TEST_ZIP'] = str(args.package.resolve())
    subprocess.run([args.blender, '--background', '--factory-startup', '--command', 'extension', 'validate', str(args.package.resolve())], cwd=root, env=env, check=True)
    subprocess.run([args.blender, '--background', '--factory-startup', '--python-exit-code', '1', '--python', str(root / 'tests/integration/install_extension.py')], cwd=root, env=env, check=True)

if not args.gui_only and not args.gpu_only:
    for test in sorted((root / "tests/integration").glob("phase*.py")) + [
        root / "tests/integration" / name
        for name in ("native_registration.py", "smart_masks.py", "projected_masks.py", "edge_wear_textures.py", "geometric_curvature.py", "utility_maps.py", "catalog_materials.py", "texture_operations.py", "procedural_channels.py", "native_texture_operations.py", "persistent_texture_cache.py", "asset_categories.py", "resource_invalidation.py", "multi_scene_scheduling.py", "failed_evaluation.py", "scan_regression.py", "dirty_tiles.py", "memory_budget.py", "typed_buffers.py", "preview_authority.py", "save_during_evaluation.py", "evaluated_projection.py", "immutable_jobs.py", "interactive_preview.py", "bake_diagnostics.py", "bake_pairing.py", "bake_isolation.py", "scene_masks.py", "reference_layers.py", "geometry_effects.py", "material_states.py", "material_stamps.py", "production_export.py", "offline_analysis.py", "gpu_parity.py", "live_input_dependencies.py")
    ]:
        print(f"Running {test.name}", flush=True)
        subprocess.run(
            [
                args.blender,
                "--background",
                "--factory-startup",
                "--python-exit-code",
                "1",
                "--python",
                str(test),
            ],
            cwd=root,
            env=env,
            check=True,
        )
    subprocess.run(
        [
            args.blender,
            "--background",
            "--factory-startup",
            "--python-exit-code",
            "1",
            "--python-expr",
            "import unittest; suite=unittest.defaultTestLoader.discover('tests/unit'); result=unittest.TextTestRunner(verbosity=2).run(suite); assert result.wasSuccessful()",
        ],
        cwd=root,
        env=env,
        check=True,
    )
if args.gui or args.gui_only:
    for filename, result_name in [
        ("native_ui.py", "native-ui.json"),
        ("texture_operations_ui.py", "texture-operations-ui.json"),
    ]:
        result_path = root / ".blender-test" / result_name
        result_path.unlink(missing_ok=True)
        subprocess.run(
            [
                args.blender,
                "--factory-startup",
                "--enable-event-simulate",
                "--python",
                str(root / "tests/integration" / filename),
                *(["--", "-ApplePersistenceIgnoreState", "YES"] if os.sys.platform == "darwin" else []),
            ],
            cwd=root,
            env=env,
            check=True,
        )
        result = result_path.read_text()
        report = json.loads(result)
        expected = {
            "native_regions": "PASS",
            "custom_handlers": 0,
            "native_asset_click": "PASS",
            "native_T_N": "PASS",
            "native_2d_3d": "PASS",
        } if filename == "native_ui.py" else {
            "native_channel_controls":"PASS", "native_mask_filters":"PASS", "native_search_selection":"PASS",
        }
        if report != expected:
            raise RuntimeError(result)
    stroke_result = root / '.blender-test/gui_result.txt'
    stroke_result.unlink(missing_ok=True)
    subprocess.run([args.blender, '--factory-startup', '--enable-event-simulate', '--python',
                    str(root / 'tests/integration/gui_workflow.py'),
                    *(['--','-ApplePersistenceIgnoreState','YES'] if os.sys.platform == 'darwin' else [])],
                   cwd=root, env=env, check=True)
    if not stroke_result.read_text().startswith('PASS:'):
        raise RuntimeError(stroke_result.read_text())
if args.gpu_only:
    for filename, report_name, variable, success_key, success_value in (
        ('live_gpu_math.py', 'live-gpu-math.json', 'OP_LIVE_MATH_REPORT', 'passed', True),
        ('live_preview.py', 'live-preview.json', 'OP_LIVE_REPORT', 'result', 'PASS'),
    ):
        result_path = root / '.blender-test' / report_name
        result_path.unlink(missing_ok=True)
        subprocess.run([args.blender, '--factory-startup', '--python',
                        str(root / 'tests/integration' / filename),
                        *(['--', '-ApplePersistenceIgnoreState', 'YES'] if os.sys.platform == 'darwin' else [])],
                       cwd=root, env=dict(env, **{variable: str(result_path)}), check=True)
        report = json.loads(result_path.read_text())
        if report.get(success_key) != success_value:
            raise RuntimeError(json.dumps(report))
print("ALL TESTS PASSED")
