"""Run bpy-independent math/format/cache tests with NumPy Python."""
from pathlib import Path
import sys
import unittest

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'src'))
suite=unittest.TestSuite()
for pattern in ('test_math.py','test_compositing.py','test_texture_engine.py','test_filter_footprints.py','test_procedural_optimization.py','test_uv_overlap.py','test_udim_tiles.py','test_shader_arithmetic.py','test_anchor_graph.py','test_micro_details.py','test_uv_rasterizer.py','test_material_director.py'):
    suite.addTests(unittest.defaultTestLoader.discover(str(root/'tests/unit'),pattern=pattern))
result=unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
