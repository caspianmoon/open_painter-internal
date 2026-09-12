"""Reference capability increment: masks, linked projection and effect persistence."""

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
    effect_stack,
)
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.smart_materials import serializer, loader

open_painter.register()
scene = bpy.context.scene
ts = sets.create(scene, "Reference", 512)
layer = stack.add(ts, "FILL")
stack.channel_for(layer, "BASE_COLOR").color = (0.2, 0.4, 0.6, 1)
effect = effect_stack.add(ts, layer, kind="FILTER")
effect.filter = "INVERT"
LayerEvaluator(scene).evaluate_texture_set(ts.id)
np.testing.assert_allclose(images.pixels(ts.outputs[0].image)[0, 0, :3], (0.8, 0.6, 0.4), atol=1e-6)
levels = effect_stack.add(ts, layer, kind="LEVELS")
levels.gamma = 2
LayerEvaluator(scene).evaluate_texture_set(ts.id)
np.testing.assert_allclose(
    images.pixels(ts.outputs[0].image)[0, 0, :3], np.sqrt([0.8, 0.6, 0.4]), atol=1e-6
)
# Black/white masks are explicit, initialized raster operations.
bpy.ops.open_painter.mask(action="ADD", source="PAINT", initial_value=0)
LayerEvaluator(scene).evaluate_texture_set(ts.id)
np.testing.assert_allclose(images.pixels(ts.outputs[0].image)[0, 0, :3], (0.5, 0.5, 0.5), atol=1e-6)
mask_effect = effect_stack.add(ts, layer, target="MASK", kind="FILTER")
mask_effect.filter = "INVERT"
LayerEvaluator(scene).evaluate_texture_set(ts.id)
np.testing.assert_allclose(
    images.pixels(ts.outputs[0].image)[0, 0, :3], np.sqrt([0.8, 0.6, 0.4]), atol=1e-6
)
# Native effect clipboard duplicates values with fresh identities.
bpy.ops.open_painter.effect(action="COPY", target="CONTENT")
bpy.ops.open_painter.effect(action="PASTE", target="CONTENT")
assert len(layer.effects) == 3 and len({e.id for e in layer.effects}) == 3
bpy.ops.open_painter.effect(action="DELETE", target="CONTENT")
source = stack.channel_for(layer, "BASE_COLOR")
source.scale = (2, 3)
source.rotation = 0.4
source.offset = (0.1, 0.2)
bpy.ops.open_painter.link_projection()
assert all(
    tuple(c.scale) == tuple(source.scale) and c.rotation == source.rotation for c in layer.channels
)
# Bitmap masks use an independently loaded grayscale resource.
bitmap = images.create("Bitmap Test", 512, (0, 0, 0, 1))
bitmap.filepath_raw = str(root / ".blender-test/bitmap_mask.png")
bitmap.file_format = "PNG"
bitmap.save()
bpy.ops.open_painter.bitmap_mask(filepath=bitmap.filepath_raw)
assert layer.masks[-1].name == "Bitmap Mask" and layer.masks[-1].image != bitmap
assert layer.masks[-1].image.colorspace_settings.name == "Non-Color"
bpy.ops.open_painter.mask(action="DELETE")
# Save portable effects and check fresh IDs on import.
path = root / ".blender-test/reference_effects.opmat"
path.unlink(missing_ok=True)
serializer.save(ts, path)
original_ids = {e.id for e in layer.effects}
copy = sets.create(scene, "Imported", 512)
loader.apply(copy, path)
assert len(copy.layers[0].effects) == 2
assert not original_ids & {e.id for e in copy.layers[0].effects}
LayerEvaluator(scene).evaluate_texture_set(copy.id)
expected = images.pixels(copy.outputs[0].image)
file = root / ".blender-test/reference_effects.blend"
bpy.ops.wm.save_as_mainfile(filepath=str(file))
bpy.ops.wm.open_mainfile(filepath=str(file))
copy = bpy.context.scene.open_painter.texture_sets[1]
LayerEvaluator(bpy.context.scene).evaluate_texture_set(copy.id)
np.testing.assert_allclose(images.pixels(copy.outputs[0].image), expected, atol=1e-6)
open_painter.unregister()
print(
    "PHASE 15 PASS: filter and levels pixels, black mask/inverted mask effects, effect copy/paste, linked projection, portable fresh IDs and save/reload"
)
