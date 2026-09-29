"""Run bpy-independent math/format/cache tests with NumPy Python."""
from pathlib import Path
import sys
import unittest

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'src'))
suite=unittest.TestSuite()
suite.addTests(unittest.defaultTestLoader.discover(str(root/'tests/unit'), pattern='test_native_*.py'))
suite.addTests(unittest.defaultTestLoader.discover(str(root/'tests/unit'), pattern='test_blender_test_profile.py'))
suite.addTests(unittest.defaultTestLoader.discover(str(root/'tests/unit'), pattern='test_benchmark_pairs.py'))
suite.addTests(unittest.defaultTestLoader.discover(str(root/'tests/unit'), pattern='test_benchmark_native_metal.py'))
suite.addTests(unittest.defaultTestLoader.discover(str(root/'tests/unit'), pattern='test_gpu_backend_contract.py'))
suite.addTests(unittest.defaultTestLoader.discover(str(root/'tests/unit'), pattern='test_engine_uniforms.py'))
suite.addTests(unittest.defaultTestLoader.discover(str(root/'tests/unit'), pattern='test_engine_constant_channels.py'))
suite.addTests(unittest.defaultTestLoader.discover(str(root/'tests/unit'), pattern='test_engine_compact_transfer.py'))
suite.addTests(unittest.defaultTestLoader.discover(str(root/'tests/unit'), pattern='test_engine_texture_pool.py'))
suite.addTests(unittest.defaultTestLoader.discover(str(root/'tests/unit'), pattern='test_engine_allocation_failures.py'))
suite.addTests(unittest.defaultTestLoader.discover(str(root/'tests/unit'), pattern='test_triangle_bounds.py'))
for pattern in ('test_bake_device.py','test_math.py','test_compositing.py','test_texture_engine.py','test_filter_footprints.py','test_procedural_optimization.py','test_uv_overlap.py','test_udim_tiles.py','test_shader_arithmetic.py','test_anchor_graph.py','test_micro_details.py','test_uv_rasterizer.py','test_material_director.py','test_material_director_benchmark*.py','test_director_quality_gate_regressions.py','test_director_context_compaction.py','test_aging_model.py','test_weathering_fields.py','test_export_padding.py','test_export_transaction.py','test_surface_reach.py'):
    suite.addTests(unittest.defaultTestLoader.discover(str(root/'tests/unit'),pattern=pattern))
result=unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
