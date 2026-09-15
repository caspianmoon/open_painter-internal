"""Composite anchors on the CPU reference: a layer's blended output and a folder's
child composite, read as fill content and as mask coverage, with cycle rules."""
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'src'))
import bpy
import numpy as np
import open_painter
from open_painter.core import texture_set_manager as sets, layer_stack as stack, image_manager as images
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.references import issues, warnings, dependent_channels
from open_painter.evaluation import anchors
from open_painter.utils.ids import new_id

open_painter.register()
scene = bpy.context.scene
ts = sets.create(scene, 'Output anchors', 512)
ts.preview_backend = 'CPU'


def channel(name):
    LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
    return images.pixels(images.output(ts, name).image)


def rough():
    return channel('ROUGHNESS')[..., 0]


def only(layer, *channels):
    for prop in layer.channels:
        prop.enabled = prop.channel in channels


def paint_mask(layer, value, operation='NORMAL'):
    mask = layer.masks.add()
    mask.id, mask.source, mask.operation = new_id(), 'PAINT', operation
    mask.image = images.create('Output anchor mask', 512, (value, value, value, 1))
    return mask


def layer_by_id(identifier):
    return next(layer for layer in ts.layers if layer.id == identifier)


# 1. Layer output at the root: Base .2 -> Worn .8 at half coverage, Multiply .5 on top.
base = stack.add(ts, 'FILL', 'Base'); base_id = base.id
only(base, 'ROUGHNESS'); stack.channel_for(base, 'ROUGHNESS').value = .2
worn = stack.add(ts, 'FILL', 'Worn'); worn_id = worn.id
only(worn, 'ROUGHNESS'); stack.channel_for(worn, 'ROUGHNESS').value = .8
paint_mask(worn, .5)
worn.publish_output = True
output_id = anchors.output_id(worn_id)
registry = anchors.index(ts)
assert registry[output_id].kind == 'OUTPUT' and registry[output_id].layer_id == worn_id, registry
assert anchors.owned(layer_by_id(worn_id)) == [output_id]
darken = stack.add(ts, 'FILL', 'Darken'); darken_id = darken.id
only(darken, 'ROUGHNESS'); stack.channel_for(darken, 'ROUGHNESS').value = .5
darken.blend_mode = 'MULTIPLY'
expected_worn = .2 + .5 * (.8 - .2)   # .5
np.testing.assert_allclose(rough(), expected_worn * .5, atol=1e-6)

# A consumer above instantiates the post-blend output (not the multiplied composite).
copy = stack.add(ts, 'FILL', 'Copy'); copy_id = copy.id
only(copy, 'ROUGHNESS'); stack.channel_for(copy, 'ROUGHNESS').anchor = output_id
assert not issues(ts) and not warnings(ts), (issues(ts), warnings(ts))
assert [kind for _, kind, _ in anchors.consumers(ts, output_id)] == ['CHANNEL']
np.testing.assert_allclose(rough(), expected_worn, atol=1e-6)
# Editing anything at or below the producer dirties the consumer; editing above does not.
assert 'ROUGHNESS' in dependent_channels(ts, base_id)
assert 'ROUGHNESS' in dependent_channels(ts, worn_id, 'ROUGHNESS')
assert not dependent_channels(ts, darken_id)
assert not dependent_channels(ts, base_id, 'BASE_COLOR')
# Hidden producer: its output is its backdrop.
layer_by_id(worn_id).visible = False
np.testing.assert_allclose(rough(), .2, atol=1e-6)
layer_by_id(worn_id).visible = True
# The anchor as mask coverage: Copy (value 1) masked by Worn's output read as roughness.
stack.channel_for(layer_by_id(copy_id), 'ROUGHNESS').anchor = ''
stack.channel_for(layer_by_id(copy_id), 'ROUGHNESS').value = 1.
reference = layer_by_id(copy_id).masks.add(); reference.id, reference.source = new_id(), 'REFERENCE'
reference.reference_anchor, reference.reference_channel = output_id, 'ROUGHNESS'
composite_below = expected_worn * .5
np.testing.assert_allclose(rough(), composite_below + expected_worn * (1 - composite_below), atol=1e-6)
print('OUTPUT_ANCHOR root layer output: PASS', flush=True)

# 2. Cycles: a consumer below its producer, the producer itself, and a folder's own composite.
layer_by_id(copy_id).masks.remove(0)
below = stack.channel_for(layer_by_id(base_id), 'ROUGHNESS')
below.anchor = output_id
assert any('cycle' in error for error in issues(ts)), issues(ts)
below.anchor = ''
own = stack.channel_for(layer_by_id(worn_id), 'ROUGHNESS')
own.anchor = output_id
assert any('cycle' in error for error in issues(ts)), issues(ts)
own.anchor = ''
assert not issues(ts)
print('OUTPUT_ANCHOR cycles: PASS', flush=True)

# 3. Folder composite (isolated): children premultiplied against black, published straight.
for index in range(len(ts.layers) - 1, -1, -1):
    if ts.layers[index].id in {copy_id, darken_id}:
        ts.layers.remove(index)
folder = stack.add(ts, 'FOLDER', 'Damage'); folder_id = folder.id
folder.group_mode = 'ISOLATED'
folder.opacity = .5
child = stack.add(ts, 'FILL', 'Scratches'); child_id = child.id
child.parent_id = folder_id
only(child, 'ROUGHNESS'); stack.channel_for(child, 'ROUGHNESS').value = 1.
paint_mask(child, .4)
layer_by_id(folder_id).publish_group = True
group_id = anchors.group_id(folder_id)
assert anchors.index(ts)[group_id].kind == 'GROUP'
below_folder = expected_worn
folder_result = below_folder + .5 * .4 * (1 - below_folder)   # folder at .5 over the composite
np.testing.assert_allclose(rough(), folder_result, atol=1e-6)
reader = stack.add(ts, 'FILL', 'Reader'); reader_id = reader.id
only(reader, 'ROUGHNESS'); stack.channel_for(reader, 'ROUGHNESS').value = 0.
mask = reader.masks.add(); mask.id, mask.source = new_id(), 'REFERENCE'
mask.reference_anchor, mask.reference_channel = group_id, 'ROUGHNESS'
assert not issues(ts) and not warnings(ts), (issues(ts), warnings(ts))
# The folder composite is straight value 1 with coverage .4: mask_value = 1 * .4.
np.testing.assert_allclose(rough(), folder_result * (1 - .4), atol=1e-6)
# The folder's own output, in contrast, includes its opacity and blend.
layer_by_id(folder_id).publish_output = True
mask.reference_anchor = anchors.output_id(folder_id)
np.testing.assert_allclose(rough(), folder_result * (1 - folder_result), atol=1e-6)
# Consuming the folder's composite from inside the folder is a cycle.
inside = stack.channel_for(layer_by_id(child_id), 'ROUGHNESS')
inside.anchor = group_id
assert any('cycle' in error for error in issues(ts)), issues(ts)
inside.anchor = ''
# A folder masked by its own child's output is legal (the child does not see the folder mask).
layer_by_id(child_id).publish_output = True
folder_mask = layer_by_id(folder_id).masks.add(); folder_mask.id, folder_mask.source = new_id(), 'REFERENCE'
folder_mask.reference_anchor, folder_mask.reference_channel = anchors.output_id(child_id), 'ROUGHNESS'
assert not issues(ts), issues(ts)
layer_by_id(folder_id).masks.remove(len(layer_by_id(folder_id).masks) - 1)
print('OUTPUT_ANCHOR folder composite: PASS', flush=True)

# 4. Passthrough context: a child's output includes the backdrop below the folder.
layer_by_id(folder_id).group_mode = 'PASSTHROUGH'
layer_by_id(folder_id).opacity = 1.
mask.reference_anchor = anchors.output_id(child_id)
child_output = below_folder + .4 * (1 - below_folder)
np.testing.assert_allclose(rough(), child_output * (1 - child_output), atol=1e-6)
# Passthrough composite equals the child output when the child is the top item.
mask.reference_anchor = group_id
np.testing.assert_allclose(rough(), child_output * (1 - child_output), atol=1e-6)
print('OUTPUT_ANCHOR passthrough: PASS', flush=True)

# 5. Channel selection: a fill reading BASE_COLOR luminance of a published output.
stack.channel_for(layer_by_id(worn_id), 'BASE_COLOR').enabled = True
stack.channel_for(layer_by_id(worn_id), 'BASE_COLOR').color = (1., 0., 0., 1.)
stack.channel_for(layer_by_id(base_id), 'BASE_COLOR').enabled = True
stack.channel_for(layer_by_id(base_id), 'BASE_COLOR').color = (0., 0., 0., 1.)
prop = stack.channel_for(layer_by_id(reader_id), 'ROUGHNESS')
layer_by_id(reader_id).masks.remove(0)
prop.anchor, prop.anchor_channel = output_id, 'BASE_COLOR'
assert not issues(ts), issues(ts)
red = .5   # Worn (red) at half coverage over black
np.testing.assert_allclose(rough(), red, atol=1e-6)   # red channel of the anchored color as roughness
prop.anchor_channel = ''
np.testing.assert_allclose(rough(), expected_worn, atol=1e-6)
print('OUTPUT_ANCHOR channel selection: PASS', flush=True)

# 6. Duplicating the producer subtree remaps derived ids; the original consumer keeps its source.
index = next(i for i, layer in enumerate(ts.layers) if layer.id == worn_id)
duplicate = stack.duplicate(ts, index)
assert duplicate.publish_output and anchors.output_id(duplicate.id) in anchors.index(ts)
assert stack.channel_for(layer_by_id(reader_id), 'ROUGHNESS').anchor == output_id
duplicate_index = next(i for i, layer in enumerate(ts.layers) if layer.id == duplicate.id)
ts.layers.remove(duplicate_index)
# Deleting the producer reports the broken consumer.
from open_painter.ui.anchors import broken_labels
labels = broken_labels(ts, anchors.owned(layer_by_id(worn_id)))
assert labels == ['Reader (Channel content)'], labels
print('OUTPUT_ANCHOR duplicate and deletion: PASS', flush=True)
print('OUTPUT_ANCHORS_PASS', flush=True)
open_painter.unregister()
