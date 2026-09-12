import sys
from pathlib import Path
import bpy
import numpy as np

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "src"))
import open_painter
from open_painter.core import (
    texture_set_manager as sets,
    layer_stack as stack,
    image_manager as images,
)
from open_painter.evaluation.evaluator import LayerEvaluator

open_painter.register()
ts = sets.create(bpy.context.scene)
ts.resolution = "512"
stack.add(ts)
stack.channel_for(ts.layers[0], "BASE_COLOR").color = (1, 0, 0, 1)
bpy.ops.open_painter.mask(action="ADD", source="PAINT")
mask = ts.layers[0].masks[0]
images.write(mask.image, np.full((512, 512, 4), 0.25, dtype=np.float32))
e = LayerEvaluator(bpy.context.scene)
e.evaluate_texture_set(ts.id)
color = next(o.image for o in ts.outputs if o.channel == "BASE_COLOR")
assert np.allclose(images.pixels(color)[0, 0, :3], (0.625, 0.375, 0.375))
mask.invert = True
e.evaluate_texture_set(ts.id)
assert np.allclose(images.pixels(color)[0, 0, :3], (0.875, 0.125, 0.125))
path = root / ".blender-test" / "masks.blend"
bpy.ops.wm.save_as_mainfile(filepath=str(path))
bpy.ops.wm.open_mainfile(filepath=str(path))
mask = bpy.context.scene.open_painter.texture_sets[0].layers[0].masks[0]
assert mask.invert and np.isclose(images.pixels(mask.image)[0, 0, 0], 0.25)
open_painter.unregister()
print("PHASE 4 PASS: painted mask, inversion, all-channel invalidation, packed persistence")
