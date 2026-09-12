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
from open_painter.painting import paint_session
from open_painter.evaluation.evaluator import LayerEvaluator

open_painter.register()
ts = sets.create(bpy.context.scene)
ts.resolution = "512"
sets.assign(bpy.context.scene, ts, bpy.context.object)
stack.add(ts, "PAINT")
assert not any(c.image for c in ts.layers[0].channels)
image = paint_session.begin(bpy.context, ts, "ROUGHNESS")
assert bpy.context.object.mode == "TEXTURE_PAINT"
assert bpy.context.scene.tool_settings.image_paint.canvas == image
assert bpy.context.scene.tool_settings.image_paint.brush
assert sum(bool(c.image) for c in ts.layers[0].channels) == 1
LayerEvaluator(bpy.context.scene).evaluate_texture_set(ts.id)
revision = next(o.revision for o in ts.outputs if o.channel == "BASE_COLOR")
# Simulate native brush raster output; actual viewport stroke is covered by GUI validation.
values = images.pixels(image)
values[200:300, 200:300, :3] = 0.85
values[200:300, 200:300, 3] = 1
images.write(image, values, pack=False)
assert paint_session.detect(bpy.context.scene, force=True)
LayerEvaluator(bpy.context.scene).evaluate_texture_set(ts.id)
out = next(o.image for o in ts.outputs if o.channel == "ROUGHNESS")
assert np.isclose(images.pixels(out)[250, 250, 0], 0.85)
assert next(o.revision for o in ts.outputs if o.channel == "BASE_COLOR") == revision
# Native float brush pixels are premultiplied; fractional coverage must not darken twice.
values[100:110, 100:110, :3] = 0.85 * 0.25
values[100:110, 100:110, 3] = 0.25
images.write(image, values, pack=False)
paint_session.detect(bpy.context.scene, force=True)
LayerEvaluator(bpy.context.scene).evaluate_texture_set(ts.id)
assert np.isclose(images.pixels(out)[105, 105, 0], 0.85 * 0.25 + 0.5 * 0.75)
bpy.ops.open_painter.paint(stop=True)
assert bpy.context.object.mode == "OBJECT"
open_painter.unregister()
print(
    "PHASE 8 PASS: lazy channel images, native brush/canvas, changed raster detection, scoped output update"
)
