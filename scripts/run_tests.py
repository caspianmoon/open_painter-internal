"""Run acceptance phases in isolated Blender processes, abort on any failure."""

import argparse, atexit, json, os, subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--blender", default="/Applications/Blender.app/Contents/MacOS/Blender")
parser.add_argument("--gui", action="store_true")
parser.add_argument("--start-at", help="Resume background regressions at this filename")
parser.add_argument("--keep-going", action="store_true", help="Collect background failures and exit nonzero at the end")
parser.add_argument("--gui-only", action="store_true")
parser.add_argument("--gpu-only", action="store_true", help="Run real-window live GPU math and workflow regressions")
parser.add_argument("--package", type=Path, help="Validate and install this candidate ZIP in isolated resources")
parser.add_argument("--user-resources", type=Path, help="Isolated Blender profile (defaults to .blender-test/user)")
args = parser.parse_args()
env = os.environ.copy()
# Tests never install into or save the artist's preference directory.
env["BLENDER_USER_RESOURCES"] = str((args.user_resources or root / ".blender-test" / "user").resolve())
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

failures = []
if not args.gui_only and not args.gpu_only:
    started = not args.start_at
    for test in sorted((root / "tests/integration").glob("phase*.py")) + [
        root / "tests/integration" / name
        for name in ("native_registration.py", "layer_drop.py", "smart_masks.py", "projected_masks.py", "edge_wear_textures.py", "geometric_curvature.py", "utility_maps.py", "catalog_materials.py", "material_recipe_masks.py", "generated_material_library.py", "generated_detail_inputs.py", "generated_material_portability.py", "damaged_bevel.py", "texture_operations.py", "procedural_channels.py", "native_texture_operations.py", "persistent_texture_cache.py", "asset_categories.py", "resource_invalidation.py", "multi_scene_scheduling.py", "failed_evaluation.py", "scan_regression.py", "dirty_tiles.py", "memory_budget.py", "typed_buffers.py", "preview_authority.py", "save_during_evaluation.py", "evaluated_projection.py", "immutable_jobs.py", "interactive_preview.py", "bake_diagnostics.py", "bake_pairing.py", "bake_isolation.py", "scene_masks.py", "reference_layers.py", "anchor_points.py", "micro_detail_anchors.py", "geometry_effects.py", "material_states.py", "material_stamps.py", "production_export.py", "surface_relief_export.py", "offline_analysis.py", "gpu_parity.py", "live_input_dependencies.py", "live_identity_planning.py", "shader_nodes.py", "shader_sources.py", "shader_preview.py", "shader_boundaries.py", "shader_runtime.py", "shader_bypass.py", "shader_units.py", "shader_structural_fields.py", "shader_undo.py", "shader_lifecycle_edges.py", "edge_map_set.py", "output_anchors.py", "shader_output_anchors.py", "graph_mirror.py", "graph_link_edits.py", "module_documents.py", "module_update.py", "catalog_modules.py", "live_fields.py", "live_edge_exposure.py", "auto_edge_rebake.py")
    ]:
        if not started:
            started = test.name == args.start_at
        if not started:
            continue
        print(f"Running {test.name}", flush=True)
        completed = subprocess.run(
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
            check=not args.keep_going,
        )
        if completed.returncode:
            failures.append(test.name)
    if not started:
        raise ValueError(f"Unknown background test: {args.start_at}")
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
        ("bake_queue_ui.py", "bake-queue-ui.json"),
        ("shader_preview_ui.py", "shader-preview-ui.json"),
        ("shader_snapshot_ui.py", "shader-snapshot-ui.json"),
        ("edge_map_refresh_ui.py", "edge-map-refresh-ui.json"),
        ("layer_drag_ui.py", "layer-drag-ui.json"),
        ("graph_view_ui.py", "graph-view-ui.json"),
        ("module_ui.py", "module-ui.json"),
        ("live_fields_ui.py", "live-fields-ui.json"),
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
            env=dict(env, **({
                "bake_queue_ui.py": {"OP_BAKE_QUEUE_REPORT": str(result_path)},
                "shader_preview_ui.py": {"OP_SHADER_UI_REPORT": str(result_path)},
                "shader_snapshot_ui.py": {"OP_SNAPSHOT_UI_REPORT": str(result_path)},
                "edge_map_refresh_ui.py": {"OP_EDGE_MAP_UI_REPORT": str(result_path)},
                "two_windows_ui.py": {"OP_TWO_WINDOWS_REPORT": str(result_path)},
                "layer_drag_ui.py": {"OP_LAYER_DRAG_UI_REPORT": str(result_path)},
                "graph_view_ui.py": {"OP_GRAPH_UI_REPORT": str(result_path)},
                "module_ui.py": {"OP_MODULE_UI_REPORT": str(result_path)},
                "live_fields_ui.py": {"OP_LIVE_FIELDS_UI_REPORT": str(result_path)},
            }.get(filename, {}))),
            check=True,
        )
        result = result_path.read_text()
        report = json.loads(result)
        if filename in {"bake_queue_ui.py", "edge_map_refresh_ui.py", "layer_drag_ui.py", "graph_view_ui.py", "module_ui.py", "live_fields_ui.py"}:
            if report.get("status") != "PASS":
                raise RuntimeError(result)
            continue
        if filename in {"shader_preview_ui.py", "shader_snapshot_ui.py"}:
            if report.get("result") != "PASS":
                raise RuntimeError(result)
            continue
        expected = {
            "native_regions": "PASS",
            "custom_handlers": 0,
            "native_asset_click": "PASS",
            "native_T_N": "PASS",
            "native_2d_3d": "PASS",
        } if filename == "native_ui.py" else {
            "native_channel_controls":"PASS", "native_mask_filters":"PASS", "native_anchor_controls":"PASS",
            "native_search_selection":"PASS",
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
        ('live_gpu_checkpoints.py', 'live-gpu-checkpoints.json', 'OP_CHECKPOINT_REPORT', 'passed', True),
        ('live_procedurals.py', 'live-procedurals.json', 'OP_SCALAR_REPORT', 'status', 'PASS'),
        ('live_masks.py', 'live-masks.json', 'OP_MASK_REPORT', 'status', 'PASS'),
        ('live_surface_relief.py', 'live-surface-relief.json', 'OP_RELIEF_GPU_REPORT', 'result', 'PASS'),
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
if failures:
    raise RuntimeError("Background tests failed: " + ", ".join(failures))
print("ALL TESTS PASSED")
