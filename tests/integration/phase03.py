import sys
from pathlib import Path
import bpy
import numpy as np

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "src"))
import open_painter
from open_painter.core import texture_set_manager as sets, layer_stack as stack, image_manager
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.blend_modes import blend

open_painter.register()
for mode, value in [
    ("NORMAL", 0.8),
    ("MULTIPLY", 0.16),
    ("ADD", 1.0),
    ("SUBTRACT", -0.6),
    ("OVERLAY", 0.32),
]:
    assert np.allclose(blend(np.array(0.2), np.array(0.8), 1.0, mode), value)
ts = sets.create(bpy.context.scene)
ts.resolution = "512"
sets.assign(bpy.context.scene, ts, bpy.context.object)
stack.add(ts)
stack.channel_for(ts.layers[0], "BASE_COLOR").color = (0.2, 0.4, 0.6, 1)
stack.add(ts).opacity = 0.5
stack.channel_for(ts.layers[1], "BASE_COLOR").color = (0.8, 0.2, 0.4, 1)
e = LayerEvaluator(bpy.context.scene)
e.evaluate_texture_set(ts.id)
out = next(o for o in ts.outputs if o.channel == "BASE_COLOR")
assert np.allclose(image_manager.pixels(out.image)[0, 0, :3], (0.5, 0.3, 0.5), atol=1e-5)
revision = out.revision
stack.channel_for(ts.layers[1], "ROUGHNESS").value = 0.2
e.evaluate_texture_set(ts.id)
assert out.revision == revision
user_node = ts.material.node_tree.nodes.new("ShaderNodeValue")
user_node.name = "Keep Me"
bpy.ops.open_painter.texture_set(action="REPAIR")
assert ts.material.node_tree.nodes.get("Keep Me")
open_painter.unregister()
print("PHASE 3 PASS: five blends, opacity, output pixels, per-channel dirtiness, node safety")
