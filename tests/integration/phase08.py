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
from open_painter.utils.ids import new_id

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
# Button clicks (undo=True, like layout.operator) store the properties they set as the
# operator's last-used values; a Finish click must not turn the next Paint click into a
# Finish, a Paint Mask click must not turn it into a mask session, and a per-channel row
# must not override the dropdown channel later on.
project = bpy.context.scene.open_painter
project.paint_channel = "ROUGHNESS"
bpy.ops.open_painter.paint(True, stop=True)
bpy.ops.open_painter.paint(True)
assert bpy.context.object.mode == "TEXTURE_PAINT" and project.paint_image is not None
bpy.ops.open_painter.paint(True, stop=True)
assert bpy.context.object.mode == "OBJECT" and project.paint_image is None
bpy.ops.open_painter.paint(True, channel="BASE_COLOR")
assert project.paint_channel == "BASE_COLOR" and project.paint_image is not None
bpy.ops.open_painter.paint(True, stop=True)
project.paint_channel = "ROUGHNESS"
bpy.ops.open_painter.paint(True)
assert project.paint_channel == "ROUGHNESS", project.paint_channel
roughness = next(c.image for c in ts.layers[0].channels if c.channel == "ROUGHNESS")
assert project.paint_image == roughness
bpy.ops.open_painter.paint(True, stop=True)
mask = ts.layers[0].masks.add()
mask.id, mask.source = new_id(), "PAINT"
mask.image = images.create("Grime mask", 512, (0, 0, 0, 1))
bpy.ops.open_painter.paint(True, mask=True)
assert project.paint_mask and project.paint_image == mask.image
bpy.ops.open_painter.paint(True, stop=True)
bpy.ops.open_painter.paint(True)
assert not project.paint_mask and project.paint_image == roughness, "Paint after Paint Mask must open the channel"
bpy.ops.open_painter.paint(True, stop=True)
open_painter.unregister()
print(
    "PHASE 8 PASS: lazy channel images, native brush/canvas, changed raster detection, scoped output update"
)
