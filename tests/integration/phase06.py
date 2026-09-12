import sys
from pathlib import Path
import bpy
import numpy as np

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "src"))
import open_painter
from open_painter.generators.edge_wear import edge_wear
from open_painter.generators.dirt import dirt
from open_painter.generators.noise import noise
from open_painter.core import layer_stack as stack, image_manager as images
from open_painter.evaluation.evaluator import LayerEvaluator

open_painter.register()
x = np.array([0.0, 1.0])
n = np.full(2, 0.5)
assert edge_wear(x, None, n, 0.5, 1, 0)[1] > edge_wear(x, None, n, 0.5, 1, 0)[0]
assert dirt(x, 1 - x, n, 0.5, 1, 0)[0] > dirt(x, 1 - x, n, 0.5, 1, 0)[1]
u, v = np.meshgrid(np.arange(8) / 8, np.arange(8) / 8)
assert np.allclose(noise(u, v), np.concatenate([noise(u[:4], v[:4]), noise(u[4:], v[4:])]))
bpy.ops.wm.open_mainfile(filepath=str(root / "examples" / "bake_cube.blend"))
ts = bpy.context.scene.open_painter.texture_sets[0]
stack.add(ts)
bpy.ops.open_painter.mask(action="ADD", source="GENERATOR")
ts.layers[0].masks[0].generator = "EDGE_WEAR"
LayerEvaluator(bpy.context.scene).evaluate_texture_set(ts.id)
assert all(o.image for o in ts.outputs)
open_painter.unregister()
print(
    "PHASE 6 PASS: curvature edge selection, AO dirt, tile-stable noise, real baked generator evaluation"
)
