"""Paint selection, material-slot isolation, alpha import and recovery regressions."""

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
from open_painter.core.validation import validate
from open_painter.painting import paint_session, alphas
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.export.image_writer import write
from open_painter.ui.dock import state, interaction

open_painter.register()
scene = bpy.context.scene
obj = bpy.context.object
obj.data.materials.append(bpy.data.materials.new("Other Surface"))
obj.data.materials.append(bpy.data.materials.new("Untouched Surface"))
for index, polygon in enumerate(obj.data.polygons):
    polygon.material_index = index % 2
    polygon.select = index % 3 == 0
saved_selection = [p.select for p in obj.data.polygons]
saved_mask = obj.data.use_paint_mask
ts = sets.create(scene, "Paint routing")
ts.resolution = "512"
sets.assign(scene, ts, obj, slot_index=0)
stack.add(ts, "PAINT", "First")
stack.add(ts, "PAINT", "Second")
ts.active_layer = 0
image_a = paint_session.begin(bpy.context, ts, "ROUGHNESS")
assert obj.data.use_paint_mask
assert all(p.select == (p.material_index == 0) for p in obj.data.polygons)
e = LayerEvaluator(scene)
e.evaluate_texture_set(ts.id)
values = images.pixels(image_a)
values[10:15, 10:15] = (0.8, 0.8, 0.8, 1)
images.write(image_a, values, pack=False)
# The old channel must be dirtied even after the requested channel changed.
scene.open_painter.paint_channel = "METALLIC"
paint_session.synchronize(bpy.context)
assert next(o for o in ts.outputs if o.channel == "ROUGHNESS").dirty
assert scene.open_painter.paint_target_channel == "METALLIC"
e.evaluate_texture_set(ts.id)
assert np.isclose(
    images.pixels(next(o.image for o in ts.outputs if o.channel == "ROUGHNESS"))[12, 12, 0], 0.8
)
# A layer click immediately changes the receiving canvas.
ui = state.DockState()
interaction.route(bpy.context, ui, "SELECT_LAYER", 1)
image_b = scene.open_painter.paint_image
assert image_b != image_a
assert image_b == stack.channel_for(ts.layers[1], "METALLIC").image
assert np.count_nonzero(images.pixels(image_b)[..., 3]) == 0
# Export must include a fresh stroke before the polling interval elapses.
e.evaluate_texture_set(ts.id)
values = images.pixels(image_b)
values[20:25, 20:25] = (0.9, 0.9, 0.9, 1)
images.write(image_b, values, pack=False)
from tempfile import TemporaryDirectory
from open_painter.export.exporter import export

with TemporaryDirectory(dir=root / ".blender-test") as destination:
    ts.export.directory = destination
    ts.export.preset = "BLENDER"
    paths = export(bpy.context, ts, ts.export)
    path = next(p for p in paths if "Metallic" in p.name)
    exported = bpy.data.images.load(str(path))
    exported.colorspace_settings.name = "Non-Color"
    assert np.isclose(images.pixels(exported)[22, 22, 0], 0.9, atol=1 / 255)
    bpy.data.images.remove(exported)
# Creating and switching painted masks binds their distinct images.
bpy.ops.open_painter.mask(action="ADD", source="PAINT")
first_mask_image = scene.open_painter.paint_image
assert scene.open_painter.paint_mask_id == ts.layers[1].masks[0].id
bpy.ops.open_painter.mask(action="ADD", source="PAINT")
assert scene.open_painter.paint_image != first_mask_image
interaction.route(bpy.context, ui, "MASK_SELECT", 0)
assert scene.open_painter.paint_image == first_mask_image
scene.open_painter.paint_value = 0.23
from open_painter.painting.brush_bridge import color_owner

assert np.allclose(color_owner(scene).color, (0.23, 0.23, 0.23))
# A native imported alpha is packed and attached as the brush mask texture.
path = root / ".blender-test/test_alpha.png"
write(path, np.ones((8, 8, 4), dtype=np.float32), "PNG", 8)
texture = alphas.import_alpha(bpy.context, str(path))
assert texture.image.packed_file
assert scene.tool_settings.image_paint.brush.mask_texture == texture
bpy.ops.open_painter.brush_action(action="CLEAR_ALPHA")
assert scene.tool_settings.image_paint.brush.mask_texture is None
# Finish remains valid after changing the selected layer/set and restores masks.
fill = stack.add(ts, "FILL")
stack.channel_for(fill, "ROUGHNESS").value = 0.71
paint_session.synchronize(bpy.context)
assert scene.open_painter.paint_image is None
assert [p.select for p in obj.data.polygons] == saved_selection
assert obj.data.use_paint_mask == saved_mask
assert obj.mode == "OBJECT"
# Session face-mask backup survives save/reload and finish.
ts.active_layer = 0
paint_session.begin(bpy.context, ts, "ROUGHNESS")
bpy.ops.wm.save_as_mainfile(filepath=str(root / ".blender-test/paint_resume.blend"))
bpy.ops.wm.open_mainfile(filepath=str(root / ".blender-test/paint_resume.blend"))
scene = bpy.context.scene
ts = scene.open_painter.texture_sets[0]
obj = scene.open_painter.paint_object
paint_session.finish(bpy.context)
assert [p.select for p in obj.data.polygons] == saved_selection
assert obj.data.use_paint_mask == saved_mask
# Tab into Edit Mode ends the session there: the selection restore goes through the edit
# BMesh, since a polygon write in Edit Mode is discarded on the way out.
paint_session.begin(bpy.context, ts, "ROUGHNESS")
bpy.ops.object.mode_set(mode="EDIT")
assert not paint_session.synchronize(bpy.context) and scene.open_painter.paint_image is None
assert obj.mode == "EDIT" and scene.open_painter.paint_object is None
bpy.ops.object.mode_set(mode="OBJECT")
assert [p.select for p in obj.data.polygons] == saved_selection
assert obj.data.use_paint_mask == saved_mask
# A removed evaluated output is rebuilt even when the old output was clean.
e = LayerEvaluator(scene)
out = next(o for o in ts.outputs if o.channel == "ROUGHNESS")
bpy.data.images.remove(out.image)
e.evaluate_texture_set(ts.id)
assert out.image and np.isclose(images.pixels(out.image)[12, 12, 0], 0.71)
# Never silently drop an orphan layer or folder cycle from the composite.
folder = stack.add(ts, "FOLDER")
folder.parent_id = folder.id
assert any("cycle" in issue for issue in validate(ts))
try:
    e.evaluate_texture_set(ts.id)
    raise AssertionError("Unreachable folder cycle was accepted")
except ValueError as exc:
    assert "cycle" in str(exc)
folder.parent_id = ""
# Enabled but never allocated paint channels are valid, deleted textures aren't.
paint_layer = ts.layers[0]
stack.channel_for(paint_layer, "HEIGHT").enabled = True
assert not any("HEIGHT" in issue for issue in validate(ts))
prop = stack.channel_for(paint_layer, "ROUGHNESS")
bpy.data.images.remove(prop.image)
assert any("Texture image missing" in issue for issue in validate(ts))
open_painter.unregister()
print(
    "PHASE 11 PASS: paint target switching, channel dirty routing, mask switching, slot isolation, packed alpha, save/reload restoration, output and hierarchy recovery"
)
