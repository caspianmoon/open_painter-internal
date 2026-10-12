"""Run acceptance phases in isolated Blender processes, abort on any failure."""

import argparse, atexit, json, os, subprocess, sys
from pathlib import Path

from blender_test_profile import isolated_environment

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--blender", default="/Applications/Blender.app/Contents/MacOS/Blender")
parser.add_argument("--gui", action="store_true")
parser.add_argument("--start-at", help="Resume background regressions at this filename")
parser.add_argument("--keep-going", action="store_true", help="Collect background failures and exit nonzero at the end")
parser.add_argument("--gui-only", action="store_true")
parser.add_argument("--gui-start-at", help="Resume GUI regressions at this filename")
parser.add_argument("--gpu-only", action="store_true", help="Run real-window live GPU math and workflow regressions")
parser.add_argument("--package", type=Path, help="Validate and install this candidate ZIP in isolated resources")
parser.add_argument("--user-resources", type=Path, help="Isolated Blender profile (defaults to .blender-test/user)")
parser.add_argument("--gpu-backend", choices=("metal", "opengl", "vulkan"),
                    help="Blender's --gpu-backend for every launch (default: the factory setting)")
parser.add_argument("--cap-gb", type=float, default=4.0,
                    help="Kill a Blender process whose memory footprint passes this (scripts/run_guarded.py)")
parser.add_argument("--timeout-minutes", type=float, default=30.0,
                    help="Kill a Blender process still running after this long; the slowest suite takes about 17")
args = parser.parse_args()
# Suites measured above the default cap on an 18 GB Mac (docs/native/deep-stacks.md, "Heavy suites").
MEMORY_CAPS_GB = {'export_padding.py': 6.0, 'resolution_switch.py': 6.0, 'director_candidates.py': 6.0,
                  'shader_preview_ui.py': 6.0, 'edge_map_refresh_ui.py': 6.0,
                  'material_painting_ui.py': 6.0, 'quality_baseline_paint_ui.py': 6.0}
# Three texture sets at 2K need about 9 GiB in a window, more than a 16-18 GB machine has free
# beside other work: the gate paints them at 1K (the same checks). Set these to 2048 for 2K.
PAINT_RESOLUTION = {'material_painting_ui.py': ('OP_MATERIAL_UI_RESOLUTION', '1024'),
                    'quality_baseline_paint_ui.py': ('OP_QUALITY_PAINT_RESOLUTION', '1024')}


def blender(suite=None):
    """Every Blender launch goes through the memory guard: one runaway phase must not
    take the machine down. The guard waits for free memory and serializes runs."""
    cap = max(args.cap_gb, MEMORY_CAPS_GB.get(suite, 0.0))
    return [sys.executable, str(root / 'scripts/run_guarded.py'), '--cap-gb', str(cap),
            '--start-gb', '3', '--wait-seconds', '120', '--timeout-seconds', str(args.timeout_minutes * 60),
            '--', args.blender,
            *(['--gpu-backend', args.gpu_backend] if args.gpu_backend else [])]


BLENDER = blender()
# Tests never install into or save the artist's preference directory.
env = isolated_environment(args.user_resources or root / ".blender-test" / "user")
profile_guard = (
    'import bpy,sys; '
    f'sys.path.insert(0, {str(root / "scripts")!r}); '
    'from blender_test_profile import assert_blender_profile; '
    f'assert_blender_profile(bpy, {env["BLENDER_USER_RESOURCES"]!r})')
subprocess.run([*BLENDER, '--background', '--factory-startup', '--python-exit-code', '1',
                '--python-expr', profile_guard], cwd=root, env=env, check=True)
# Historical phases generate these bundled fixtures; do not leave the checkout edited.
originals = {p: p.read_bytes() for p in [root / 'examples/phase01.blend', root / 'examples/bake_cube.blend', root / 'docs/native-painter.png'] if p.exists()}
@atexit.register
def restore_fixtures():
    for path, content in originals.items():
        path.write_bytes(content)
if args.package:
    env['OPEN_PAINTER_TEST_ZIP'] = str(args.package.resolve())
    subprocess.run([*BLENDER, '--background', '--factory-startup', '--command', 'extension', 'validate', str(args.package.resolve())], cwd=root, env=env, check=True)
    subprocess.run([*BLENDER, '--background', '--factory-startup', '--python-exit-code', '1', '--python', str(root / 'tests/integration/install_extension.py')], cwd=root, env=env, check=True)

failures = []
if not args.gui_only and not args.gpu_only:
    started = not args.start_at
    for test in sorted((root / "tests/integration").glob("phase*.py")) + [
        root / "tests/integration" / name
        for name in ("authoring_sources.py", "authoring_records.py", "authoring_workflow.py", "gpu_strokes.py", "engine_filters.py", "curvature_cycles.py", "bake_override.py", "shader_rehydrate_safety.py", "contributor_regressions.py", "udim_workflow.py", "udim_material.py", "native_registration.py", "layer_drop.py", "layer_list_ops.py", "smart_masks.py", "projected_masks.py", "edge_wear_textures.py", "geometric_curvature.py", "utility_maps.py", "catalog_materials.py", "material_recipe_masks.py", "generated_material_library.py", "generated_detail_inputs.py", "generated_material_portability.py", "damaged_bevel.py", "texture_operations.py", "procedural_channels.py", "native_texture_operations.py", "persistent_texture_cache.py", "asset_categories.py", "dock_lifecycle.py", "editor_areas.py", "resource_invalidation.py", "multi_scene_scheduling.py", "failed_evaluation.py", "scan_regression.py", "dirty_tiles.py", "memory_budget.py", "typed_buffers.py", "preview_authority.py", "save_during_evaluation.py", "evaluated_projection.py", "immutable_jobs.py", "interactive_preview.py", "bake_diagnostics.py", "bake_pairing.py", "bake_isolation.py", "scene_masks.py", "reference_layers.py", "anchor_points.py", "micro_detail_anchors.py", "geometry_effects.py", "geometry_sources.py", "material_aging.py", "weathering_shader.py", "material_stamps.py", "production_export.py", "mesh_bundle.py", "surface_relief_export.py", "offline_analysis.py", "gpu_parity.py", "live_input_dependencies.py", "live_identity_planning.py", "shader_nodes.py", "shader_sources.py", "shader_preview.py", "shader_boundaries.py", "resource_keys.py", "shader_runtime.py", "shader_bypass.py", "shader_units.py", "shader_structural_fields.py", "engine_procedurals.py", "engine_parity.py", "mask_graphs.py", "engine_stages.py", "material_nodes.py", "engine_pixel_publish.py", "engine_uniform_publish.py", "engine_fused_publish.py", "engine_constant_publish.py", "engine_detail_fields.py", "engine_eviction.py", "shader_undo.py", "undo_follow.py", "undo_leaf_lifetime.py", "shader_lifecycle_edges.py", "shader_adoption.py", "edge_map_set.py", "edge_map_jobs.py", "output_anchors.py", "shader_output_anchors.py", "graph_mirror.py", "graph_link_edits.py", "graph_templates.py", "module_documents.py", "module_update.py", "catalog_modules.py", "live_fields.py", "live_edge_exposure.py", "auto_edge_rebake.py", "engine_preview.py", "export_target.py", "vector_strokes.py", "stroke_parity.py", "multi_set_paint.py", "geometry_displace.py", "curve_geometry.py", "bake_back.py", "layer_bypass.py", "layer_cache.py", "layer_profile.py", "paint_freeze.py", "bake_doctor.py", "bake_corrections.py", "mesh_masks.py", "mask_groups.py", "surface_filters.py", "physical_scale.py", "geometry_uses_memo.py", "layer_store.py", "moved_layer_adoption.py", "structural_patch.py", "decal_objects.py", "artist_file_workflow.py", "artist_file_baking.py", "artist_file_multiset.py", "artist_file_edits.py", "resolution_switch.py", "texture_set_lifecycle.py", "ui_baseline.py", "selection_target.py", "inspector_panels.py", "asset_workflow.py", "bake_view.py", "export_view.py", "contextual_tools.py", "nodes_and_assistant.py", "ui_release.py", "master_controls.py", "portable_smart_materials.py", "material_painting.py", "export_padding.py", "export_safety.py")
    ] + [root / "tests/integration" / name for name in
         ("navigation_scheduling.py", "diagnostics_scheduling.py", "graph_signature_cache.py", "graph_channel_controls.py", "engine_image_queries.py", "engine_compact_readback.py", "engine_preview_readback.py", "engine_state_cache.py", "engine_memory_soak.py", "stroke_surface_bounds.py", "node_sync_skip.py")] + [root / "tests/integration" / name for name in
         ("material_director.py", "director_surface_field.py", "director_regions.py", "director_region_queries.py", "director_coverage.py", "director_scene_gutters.py", "director_region_gutters.py", "material_surfaces.py", "material_surface_sources.py", "material_surface_control_contracts.py", "material_surface_parity.py", "material_metric_masks.py")]:
        if not started:
            started = test.name == args.start_at
        if not started:
            continue
        print(f"Running {test.name}", flush=True)
        completed = subprocess.run(
            [
                *blender(test.name),
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
    # The compatibility contract needs a file written by the 0.12.2 build (see the test's
    # --build/--record modes); without it the check is reported as skipped, never as passed.
    if (root / ".blender-test/quality/compat-0.12.2.blend").exists() and (root / ".blender-test/quality/compat-0.12.2.json").exists():
        print("Running compatibility_contract.py", flush=True)
        completed = subprocess.run([*BLENDER, "--background", "--factory-startup", "--python-exit-code", "1", "--python",
                                    str(root / "tests/integration/compatibility_contract.py")], cwd=root, env=env, check=not args.keep_going)
        if completed.returncode:
            failures.append("compatibility_contract.py")
    else:
        print("SKIPPED compatibility_contract.py: no 0.12.2 reference file (build it with --build/--record)", flush=True)
    subprocess.run(
        [
            *BLENDER,
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
    # These acceptance scripts own their assertions and nonzero failure exits.
    gui_started = not args.gui_start_at
    # Timer-driven: in background Blender their timers never fire and they would pass empty.
    for filename in ("director_candidates.py", "director_regions.py", "director_region_queries.py", "director_coverage_tools.py",
                     "director_correspondence.py", "director_generated_surfaces.py", "director_surface_maps.py",
                     "director_surface_projection.py"):
        if not gui_started:
            gui_started = filename == args.gui_start_at
        if not gui_started:
            continue
        subprocess.run([*blender(filename), "--factory-startup", "--python",
                        str(root / "tests/integration" / filename)], cwd=root, env=env, check=True)
        if filename == "director_correspondence.py":
            # Reopens the file the GUI run just saved.
            subprocess.run([*BLENDER, "--background", "--factory-startup", "--python-exit-code", "1", "--python",
                            str(root / "tests/integration/director_correspondence_reopen.py")], cwd=root, env=env, check=True)
    for filename, result_name in [
        ("authoring_ui.py", "authoring-upgrade/authoring-ui.json"),
        ("native_ui.py", "native-ui.json"),
        ("texture_operations_ui.py", "texture-operations-ui.json"),
        ("bake_queue_ui.py", "bake-queue-ui.json"),
        ("shader_preview_ui.py", "shader-preview-ui.json"),
        ("shader_snapshot_ui.py", "shader-snapshot-ui.json"),
        ("edge_map_refresh_ui.py", "edge-map-refresh-ui.json"),
        ("layer_drag_ui.py", "layer-drag-ui.json"),
        ("graph_view_ui.py", "graph-view-ui.json"),
        ("graph_templates_ui.py", "graph-templates-ui.json"),
        ("module_ui.py", "module-ui.json"),
        ("live_fields_ui.py", "live-fields-ui.json"),
        ("vector_brush_ui.py", "vector-brush-ui.json"),
        ("multi_set_paint_ui.py", "multi-set-paint-ui.json"),
        ("udim_paint_ui.py", "udim-paint-ui.json"),
        ("paint_freeze_ui.py", "paint-freeze-ui.json"),
        ("paint_undo_ui.py", "paint-undo-ui.json"),
        ("bake_correction_ui.py", "bake-correction-ui.json"),
        ("material_director_ui.py", "material-director-ui.json"),
        ("material_aging_ui.py", "material-aging-ui.json"),
        ("geometry_effects_ui.py", "geometry-effects-ui.json"),
        ("artist_file_ui.py", "artist-file-ui.json"),
        ("inspector_ui.py", "inspector-ui.json"),
        ("master_controls_ui.py", "master-controls-ui.json"),
        ("launch_audit_ui.py", "launch-audit.json"),
        ("ui_response_ui.py", "ui-response.json"),
        ("material_painting_ui.py", "material-painting-ui.json"),
        ("material_target_ui.py", "material-target-ui.json"),
        ("quality_baseline_paint_ui.py", "quality-baseline-painting.json"),
    ] + ([("material_session_ui.py", "material-session-ui.json")] if os.environ.get("OP_RUN_SESSION") else []):
        if not gui_started:
            gui_started = filename == args.gui_start_at
        if not gui_started:
            continue
        result_path = root / ".blender-test" / result_name
        result_path.unlink(missing_ok=True)
        subprocess.run(
            [
                *blender(filename),
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
                "graph_templates_ui.py": {"OP_GRAPH_TEMPLATES_UI_REPORT": str(result_path)},
                "module_ui.py": {"OP_MODULE_UI_REPORT": str(result_path)},
                "live_fields_ui.py": {"OP_LIVE_FIELDS_UI_REPORT": str(result_path)},
                "vector_brush_ui.py": {"OP_VECTOR_BRUSH_UI_REPORT": str(result_path)},
                "multi_set_paint_ui.py": {"OP_MULTI_SET_PAINT_UI_REPORT": str(result_path)},
                "paint_freeze_ui.py": {"OP_PAINT_FREEZE_UI_REPORT": str(result_path)},
                "paint_undo_ui.py": {"OP_PAINT_UNDO_UI_REPORT": str(result_path)},
                "bake_correction_ui.py": {"OP_BAKE_CORRECTION_UI_REPORT": str(result_path)},
                "artist_file_ui.py": {"OP_ARTIST_FILE_UI_REPORT": str(result_path)},
                "inspector_ui.py": {"OP_INSPECTOR_UI_REPORT": str(result_path)},
                "master_controls_ui.py": {"OP_MASTER_CONTROLS_UI_REPORT": str(result_path)},
                "launch_audit_ui.py": {"OP_LAUNCH_AUDIT_REPORT": str(result_path)},
                "ui_response_ui.py": {"OP_UI_RESPONSE_REPORT": str(result_path)},
                "material_painting_ui.py": {"OP_MATERIAL_UI_REPORT": str(result_path)},
                "material_target_ui.py": {"OP_MATERIAL_TARGET_REPORT": str(result_path)},
                "material_session_ui.py": {"OP_SESSION_REPORT": str(result_path)},
                "quality_baseline_paint_ui.py": {"OP_QUALITY_BASELINE_PAINT_REPORT": str(result_path)},
            }.get(filename, {})),
                     **({PAINT_RESOLUTION[filename][0]: os.environ.get(*PAINT_RESOLUTION[filename])}
                        if filename in PAINT_RESOLUTION else {})),
            check=True,
        )
        result = result_path.read_text(encoding="utf-8")
        report = json.loads(result)
        if filename in {"authoring_ui.py", "bake_queue_ui.py", "edge_map_refresh_ui.py", "layer_drag_ui.py", "graph_view_ui.py", "graph_templates_ui.py", "module_ui.py", "live_fields_ui.py", "vector_brush_ui.py", "multi_set_paint_ui.py", "udim_paint_ui.py", "paint_freeze_ui.py", "paint_undo_ui.py", "bake_correction_ui.py", "artist_file_ui.py", "material_painting_ui.py", "material_target_ui.py", "material_session_ui.py", "quality_baseline_paint_ui.py"}:
            if report.get("status") != "PASS":
                raise RuntimeError(result)
            continue
        if filename in {"shader_preview_ui.py", "shader_snapshot_ui.py", "material_director_ui.py", "material_aging_ui.py", "geometry_effects_ui.py", "inspector_ui.py", "master_controls_ui.py", "launch_audit_ui.py", "ui_response_ui.py"}:
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
    subprocess.run([*BLENDER, '--factory-startup', '--enable-event-simulate', '--python',
                    str(root / 'tests/integration/gui_workflow.py'),
                    *(['--','-ApplePersistenceIgnoreState','YES'] if os.sys.platform == 'darwin' else [])],
                   cwd=root, env=env, check=True)
    if not stroke_result.read_text(encoding="utf-8").startswith('PASS:'):
        raise RuntimeError(stroke_result.read_text(encoding="utf-8"))
    regions_result = root / '.blender-test/native_regions_result.txt'
    regions_result.unlink(missing_ok=True)
    subprocess.run([*BLENDER, '--factory-startup', '--enable-event-simulate', '--python',
                    str(root / 'tests/integration/native_regions_ui.py'),
                    *(['--','-ApplePersistenceIgnoreState','YES'] if os.sys.platform == 'darwin' else [])],
                   cwd=root, env=env, check=True)
    if not regions_result.read_text(encoding="utf-8").startswith('PASS:'):
        raise RuntimeError(regions_result.read_text(encoding="utf-8"))
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
        subprocess.run([*BLENDER, '--factory-startup', '--python',
                        str(root / 'tests/integration' / filename),
                        *(['--', '-ApplePersistenceIgnoreState', 'YES'] if os.sys.platform == 'darwin' else [])],
                       cwd=root, env=dict(env, **{variable: str(result_path)}), check=True)
        report = json.loads(result_path.read_text(encoding="utf-8"))
        if report.get(success_key) != success_value:
            raise RuntimeError(json.dumps(report))
if failures:
    raise RuntimeError("Background tests failed: " + ", ".join(failures))
print("ALL TESTS PASSED")
