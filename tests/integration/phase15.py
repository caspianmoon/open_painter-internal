"""Native texture filter order, bitmap masks, independent stacks and persistence."""

import sys
from pathlib import Path

import bpy
import numpy as np

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'src'))
import open_painter
from open_painter.core import texture_set_manager as sets, layer_stack as stack, image_manager as images
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.smart_materials import serializer, loader

open_painter.register()
scene = bpy.context.scene
ts = sets.create(scene, 'Reference', 512)
layer = stack.add(ts, 'FILL')
channel = stack.channel_for(layer, 'BASE_COLOR')
channel.color = (.2, .4, .6, 1)
scene.open_painter.properties_channel = 'BASE_COLOR'


def actual():
    LayerEvaluator(scene).evaluate_texture_set(ts.id)
    return images.pixels(images.output(ts, 'BASE_COLOR').image)[0, 0, :3]


assert bpy.ops.open_painter.choose_filter(choice='INVERT', target='CHANNEL') == {'FINISHED'}
np.testing.assert_allclose(actual(), (.8, .6, .4), atol=1e-6)
assert bpy.ops.open_painter.choose_filter(choice='LEVELS', target='CHANNEL') == {'FINISHED'}
channel.effects[1].gamma = 2
np.testing.assert_allclose(actual(), np.sqrt([.8, .6, .4]), atol=1e-6)
# The native ordered list changes execution order and supports bypass/delete.
assert bpy.ops.open_painter.channel_effect(action='UP') == {'FINISHED'}
np.testing.assert_allclose(actual(), 1 - np.sqrt([.2, .4, .6]), atol=1e-6)
assert bpy.ops.open_painter.channel_effect(action='DOWN') == {'FINISHED'}
channel.effects[1].enabled = False
np.testing.assert_allclose(actual(), [.8, .6, .4], atol=1e-6)
channel.effects[1].enabled = True
assert bpy.ops.open_painter.choose_filter(choice='INVERT', target='CHANNEL') == {'FINISHED'}
assert bpy.ops.open_painter.channel_effect(action='DELETE') == {'FINISHED'}
assert len(channel.effects) == 2
# Masks contain raster input followed by an independently editable FILTER item.
assert bpy.ops.open_painter.mask(action='ADD', source='PAINT', black=True) == {'FINISHED'}
np.testing.assert_allclose(actual(), [.5, .5, .5], atol=1e-6)
assert bpy.ops.open_painter.choose_filter(choice='INVERT', target='MASK') == {'FINISHED'}
assert layer.masks[1].source == 'FILTER' and layer.masks[1].effect.kind == 'INVERT'
np.testing.assert_allclose(actual(), np.sqrt([.8, .6, .4]), atol=1e-6)
layer.masks[1].effect.enabled = False
np.testing.assert_allclose(actual(), [.5, .5, .5], atol=1e-6)
layer.masks[1].effect.enabled = True

# Duplicating a layer creates independent filter property groups and paint masks.
original_id = layer.id
clone = stack.duplicate(ts, 0)
layer = next(item for item in ts.layers if item.id == original_id)
channel = stack.channel_for(layer, 'BASE_COLOR')
clone_channel = stack.channel_for(clone, 'BASE_COLOR')
assert clone.id != layer.id
assert clone_channel.effects[1].as_pointer() != channel.effects[1].as_pointer()
clone_channel.effects[1].gamma = 3
assert channel.effects[1].gamma == 2
assert clone.masks[0].image != layer.masks[0].image
assert clone.masks[1].id != layer.masks[1].id
stack.remove(ts, ts.active_layer)
layer = ts.layers[0]
channel = stack.channel_for(layer, 'BASE_COLOR')
channel.scale, channel.rotation, channel.offset = (2, 3), .4, (.1, .2)
channel.procedural, channel.procedural_scale, channel.procedural_seed = 'FBM', 4.5, 23
channel.procedural_detail = .17
height = stack.channel_for(layer, 'HEIGHT')
height.enabled = True
height.procedural, height.procedural_height = 'SCRATCHES', -.18

# A bitmap is an independently loaded Non-Color mask texture, not a shader effect.
folder = root / '.blender-test'
folder.mkdir(exist_ok=True)
bitmap = images.create('Bitmap Test', 32, (0, 0, 0, 1))
bitmap.filepath_raw, bitmap.file_format = str(folder / 'bitmap_mask.png'), 'PNG'
bitmap.save()
loaded = bpy.data.images.load(bitmap.filepath_raw, check_existing=False)
loaded.colorspace_settings.name = 'Non-Color'
assert bpy.ops.open_painter.mask(action='ADD', source='FILL') == {'FINISHED'}
layer.masks[-1].image = loaded
assert loaded != bitmap and loaded.colorspace_settings.name == 'Non-Color'
np.testing.assert_allclose(actual(), [.5, .5, .5], atol=1e-6)
assert bpy.ops.open_painter.mask(action='DELETE') == {'FINISHED'}

# Portable material serialization retains every nested channel and mask effect,
# procedural controls, signed height and transform without sharing identities.
path = folder / 'reference_effects.opmat'
path.unlink(missing_ok=True)
serializer.save(ts, path)
original_layer_id = layer.id
original_mask_ids = {mask.id for mask in layer.masks}
copy = sets.create(scene, 'Imported', 512)
loader.apply(copy, path)
imported = copy.layers[0]
imported_channel = stack.channel_for(imported, 'BASE_COLOR')
assert imported.id != original_layer_id
assert not original_mask_ids & {mask.id for mask in imported.masks}
assert [effect.kind for effect in imported_channel.effects] == ['INVERT', 'LEVELS']
assert imported_channel.effects[1].gamma == 2
assert imported.masks[1].effect.kind == 'INVERT'
assert imported_channel.procedural == 'FBM' and imported_channel.procedural_seed == 23
np.testing.assert_allclose(imported_channel.scale, (2, 3))
np.testing.assert_allclose(imported_channel.offset, (.1, .2))
assert abs(imported_channel.rotation - .4) < 1e-6
assert abs(stack.channel_for(imported, 'HEIGHT').procedural_height + .18) < 1e-6
assert imported.masks[0].image != layer.masks[0].image
LayerEvaluator(scene).evaluate_texture_set(ts.id)
LayerEvaluator(scene).evaluate_texture_set(copy.id)
for output in ts.outputs:
    np.testing.assert_allclose(images.pixels(images.output(copy, output.channel).image),
                               images.pixels(output.image), atol=1e-6)
expected = {output.channel: images.pixels(output.image) for output in copy.outputs}
file = folder / 'reference_effects.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(file))
bpy.ops.wm.open_mainfile(filepath=str(file))
copy = bpy.context.scene.open_painter.texture_sets[1]
LayerEvaluator(bpy.context.scene).evaluate_texture_set(copy.id)
for output in copy.outputs:
    np.testing.assert_allclose(images.pixels(output.image), expected[output.channel], atol=1e-6)
open_painter.unregister()
print('PHASE 15 PASS: native channel/filter order, black mask/filter inversion, independent duplication, bitmap masks, portable effect/procedural transforms and save/reload')
