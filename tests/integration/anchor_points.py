"""Anchor points: mid-stack captures, fill content, navigation, persistence."""
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

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
ts = sets.create(scene, 'Anchor points', 512)
ts.preview_backend = 'CPU'


def rough():
    LayerEvaluator(scene).evaluate_texture_set(ts.id)
    return images.pixels(images.output(ts, 'ROUGHNESS').image)[..., 0]


def only(layer, channel):
    for prop in layer.channels:
        prop.enabled = prop.channel == channel


def paint_mask(layer, value, operation='NORMAL'):
    mask = layer.masks.add()
    mask.id, mask.source, mask.operation = new_id(), 'PAINT', operation
    mask.image = images.create('Anchor mask', 512, (value, value, value, 1))
    return mask


# 1. A mask anchor publishes the stack before it: [PAINT .25, ANCHOR, PAINT .5 MULTIPLY].
source = stack.add(ts, 'FILL', 'Source'); source_id = source.id
only(source, 'ROUGHNESS'); stack.channel_for(source, 'ROUGHNESS').value = .2
paint_mask(source, .25)
anchor = source.masks.add(); anchor.id, anchor.name = new_id(), 'Damage anchor'; anchor.source = 'ANCHOR'
anchor_id = anchor.id
paint_mask(source, .5, 'MULTIPLY')
consumer = stack.add(ts, 'FILL', 'Consumer'); consumer_id = consumer.id
only(consumer, 'ROUGHNESS'); stack.channel_for(consumer, 'ROUGHNESS').value = 1.
reference = consumer.masks.add(); reference.id, reference.source = new_id(), 'REFERENCE'
reference.reference_anchor = anchor_id
registry = anchors.index(ts)
assert registry[anchor_id].kind == 'MASK' and registry[anchor_id].position == 1, registry
assert not issues(ts) and not warnings(ts), (issues(ts), warnings(ts))
assert [kind for _, kind, _ in anchors.consumers(ts, anchor_id)] == ['REFERENCE_MASK']
# Source: .5 -> .2 at .125 coverage = .4625; consumer: 1 at the anchored .25 coverage.
np.testing.assert_allclose(rough(), .4625 + .25 * (1 - .4625), atol=1e-6)
# The legacy whole-stack reference sees the multiplied .125 instead.
reference.reference_anchor = ''
reference.reference_layer, reference.reference_channel = source_id, 'MASK'
np.testing.assert_allclose(rough(), .4625 + .125 * (1 - .4625), atol=1e-6)
reference.reference_anchor = anchor_id

# 2. A channel anchor publishes the channel after the filters above it.
prop = stack.channel_for(source, 'ROUGHNESS')
levels = prop.effects.add(); levels.kind = 'LEVELS'; levels.output_black = levels.output_white = .7
channel_anchor = prop.effects.add(); channel_anchor.id, channel_anchor.name = new_id(), 'Roughness anchor'
channel_anchor.kind = 'ANCHOR'; channel_anchor_id = channel_anchor.id
later = prop.effects.add(); later.kind = 'LEVELS'; later.output_black = later.output_white = .9
copy = stack.add(ts, 'FILL', 'Copy'); copy_id = copy.id
only(copy, 'ROUGHNESS'); stack.channel_for(copy, 'ROUGHNESS').anchor = channel_anchor_id
assert anchors.index(ts)[channel_anchor_id].channel == 'ROUGHNESS'
assert not issues(ts) and not warnings(ts)
np.testing.assert_allclose(rough(), .7, atol=1e-6)  # Copy covers everything with the anchored .7.
blur = stack.channel_for(copy, 'ROUGHNESS').effects.add(); blur.kind = 'GAUSSIAN_BLUR'; blur.radius = 2
np.testing.assert_allclose(rough(), .7, atol=1e-5)  # Filters run on top of anchored content.
copy.visible = False
np.testing.assert_allclose(rough(), .5 + .125 * (.9 - .5) + .25 * (1 - (.5 + .125 * .4)), atol=1e-6)
copy.visible = True
# The consumer channel leaves the scalar fast path; an anchor item alone does not.
from open_painter.evaluation.buffers import scalar_compatible
properties = {layer.id: stack.channel_for(layer, 'ROUGHNESS') for layer in ts.layers}
assert not scalar_compatible('ROUGHNESS', ts.layers, properties)
assert scalar_compatible('ROUGHNESS', [source], {source_id: properties[source_id]})

# 3. Same-stack references: allowed below the anchor, a cycle above it.
same = source.masks.add(); same.id, same.source = new_id(), 'REFERENCE'; same.reference_anchor = anchor_id
assert not issues(ts), issues(ts)
rough()
source.masks.move(len(source.masks) - 1, 0)
assert any('cycle' in text for text in issues(ts)), issues(ts)
source.masks.move(0, len(source.masks) - 1)
assert not issues(ts)
# A channel reading an anchor placed in its own filter stack is a cycle too.
stack.channel_for(source, 'ROUGHNESS').anchor = channel_anchor_id
assert any('cycle' in text for text in issues(ts)), issues(ts)
stack.channel_for(source, 'ROUGHNESS').anchor = ''
assert not issues(ts)

# 4. Dirtying follows anchors: a mask image edit and a channel edit reach consumers.
rough()
for out in ts.outputs:
    out.dirty = False
assert 'ROUGHNESS' in dependent_channels(ts, source_id, 'MASK')
images.write(source.masks[0].image, np.tile(np.array([.3, .3, .3, 1], np.float32), (512, 512, 1)), pack=False)
assert images.output(ts, 'ROUGHNESS').dirty
rough()
for out in ts.outputs:
    out.dirty = False
levels.output_black = levels.output_white = .6
assert images.output(ts, 'ROUGHNESS').dirty
np.testing.assert_allclose(rough(), .6, atol=1e-5)

# 5. Operators: create, pick, locate, select consumer and delete with a report.
ts.active_layer = next(i for i, layer in enumerate(ts.layers) if layer.id == copy_id)
scene.open_painter.properties_channel = 'ROUGHNESS'
assert bpy.ops.open_painter.anchor(action='ADD_MASK') == {'FINISHED'}
copy = next(layer for layer in ts.layers if layer.id == copy_id)
added = copy.masks[-1]
assert added.source == 'ANCHOR' and added.name.startswith('Anchor Point'), (added.source, added.name)
assert bpy.ops.open_painter.anchor(action='ADD_CHANNEL') == {'FINISHED'}
effect = stack.channel_for(copy, 'ROUGHNESS').effects[-1]
assert effect.kind == 'ANCHOR' and effect.id and effect.id in anchors.index(ts)
names = [anchor.name for anchor in anchors.index(ts).values()]
assert len(names) == len(set(names)), names
def refused(call, **kwargs):
    # A reported error surfaces as RuntimeError from bpy.ops; the field is reverted.
    try:
        call(**kwargs)
    except RuntimeError as error:
        return str(error)
    raise AssertionError('Operator accepted ' + repr(kwargs))


assert 'cycle' in refused(bpy.ops.open_painter.anchor_pick, target='CHANNEL', choice=effect.id)  # own stack
assert stack.channel_for(copy, 'ROUGHNESS').anchor == channel_anchor_id
assert bpy.ops.open_painter.anchor_pick(target='CHANNEL', choice=added.id) == {'FINISHED'}
assert stack.channel_for(copy, 'ROUGHNESS').anchor == added.id
assert bpy.ops.open_painter.anchor_pick(target='CHANNEL', clear=True) == {'FINISHED'}
assert stack.channel_for(copy, 'ROUGHNESS').anchor == ''
assert bpy.ops.open_painter.anchor_pick(target='CHANNEL', choice=channel_anchor_id) == {'FINISHED'}
# UI buttons (undo=True here) replay the operator's last-used properties: a clear
# click must not ghost clear=True into the next picker click on any consumer.
assert bpy.ops.open_painter.anchor_pick('EXEC_DEFAULT', True, target='CHANNEL', clear=True) == {'FINISHED'}
assert stack.channel_for(copy, 'ROUGHNESS').anchor == ''
assert bpy.ops.open_painter.anchor_pick('EXEC_DEFAULT', True, target='CHANNEL', choice=channel_anchor_id) == {'FINISHED'}
assert stack.channel_for(copy, 'ROUGHNESS').anchor == channel_anchor_id, stack.channel_for(copy, 'ROUGHNESS').anchor
assert bpy.ops.open_painter.anchor_locate(anchor_id=channel_anchor_id) == {'FINISHED'}
assert ts.layers[ts.active_layer].id == source_id
assert scene.open_painter.properties_channel == 'ROUGHNESS'
assert stack.channel_for(ts.layers[ts.active_layer], 'ROUGHNESS').active_effect == 1
assert bpy.ops.open_painter.anchor_locate(anchor_id=anchor_id) == {'FINISHED'}
assert ts.layers[ts.active_layer].masks[ts.layers[ts.active_layer].active_mask].id == anchor_id
assert bpy.ops.open_painter.anchor_select_consumer(layer_id=consumer_id, kind='REFERENCE_MASK', key=reference.id) == {'FINISHED'}
assert ts.layers[ts.active_layer].id == consumer_id
assert 'missing' in refused(bpy.ops.open_painter.anchor_locate, anchor_id='missing')
folder = stack.add(ts, 'FOLDER', 'Folder')
ts.active_layer = next(i for i, layer in enumerate(ts.layers) if layer.id == folder.id)
assert 'fill layer' in refused(bpy.ops.open_painter.anchor, action='ADD_CHANNEL')
stack.remove_tree(ts, folder.id)
# Deleting the channel anchor breaks Copy's content: reported, never fatal.
ts.active_layer = next(i for i, layer in enumerate(ts.layers) if layer.id == source_id)
stack.channel_for(ts.layers[ts.active_layer], 'ROUGHNESS').active_effect = 1
scene.open_painter.status = ''
assert bpy.ops.open_painter.channel_effect(action='DELETE') == {'FINISHED'}
assert scene.open_painter.status.startswith('Broke 1 reference'), scene.open_painter.status
assert not issues(ts) and any('anchor point is missing' in text for text in warnings(ts)), warnings(ts)
copy = next(layer for layer in ts.layers if layer.id == copy_id)
assert copy.masks[-1].source == 'ANCHOR'
copy.masks.remove(len(copy.masks) - 1)
stack.channel_for(copy, 'ROUGHNESS').effects.clear()
missing = rough()  # Copy contributes nothing while its anchor is missing.
copy.visible = False
np.testing.assert_allclose(rough(), missing, atol=1e-6)
copy.visible = True
stack.channel_for(copy, 'ROUGHNESS').anchor = ''
stack.channel_for(copy, 'ROUGHNESS').value = .7
assert not warnings(ts)

# 6. Group duplication remaps anchors inside the copy; set duplication too.
bundle = stack.group(ts, [layer.id for layer in ts.layers], 'Bundle')
bundle_id = bundle.id
duplicate = stack.duplicate(ts, next(i for i, layer in enumerate(ts.layers) if layer.id == bundle_id))
copied = [layer for layer in ts.layers if layer.parent_id == duplicate.id]
new_source = next(layer for layer in copied if layer.name.startswith('Source'))
new_anchor = next(mask.id for mask in new_source.masks if mask.source == 'ANCHOR')
assert new_anchor != anchor_id
assert all(mask.reference_anchor == new_anchor for layer in copied for mask in layer.masks if mask.source == 'REFERENCE')
assert not issues(ts) and not warnings(ts), warnings(ts)
rough()
copied_set = sets.duplicate(scene, next(i for i, t in enumerate(scene.open_painter.texture_sets) if t.id == ts.id))
assert not issues(copied_set) and not warnings(copied_set)
LayerEvaluator(scene).evaluate_texture_set(copied_set.id)
ts = next(t for t in scene.open_painter.texture_sets if t.id != copied_set.id)

# 7. Smart material round trips keep anchors linked; dangling ids load as warnings; cycles roll back.
with TemporaryDirectory(prefix='op-anchor-') as directory:
    from open_painter.smart_materials.serializer import save
    from open_painter.smart_materials.loader import apply
    from open_painter.smart_materials.format import VERSION
    path = save(ts, Path(directory) / 'anchors.openpaintmaterial')
    other = sets.create(scene, 'Imported', 512); other.preview_backend = 'CPU'
    apply(other, path)
    assert not issues(other) and not warnings(other), warnings(other)
    assert not set(anchors.index(other)) & set(anchors.index(ts))
    LayerEvaluator(scene).evaluate_texture_set(other.id)
    np.testing.assert_allclose(images.pixels(images.output(other, 'ROUGHNESS').image)[..., 0],
                               images.pixels(images.output(ts, 'ROUGHNESS').image)[..., 0], atol=1e-6)
    dangling = Path(directory) / 'dangling.openpaintmaterial'
    dangling.write_text(json.dumps({'format_version': VERSION, 'layers': [{
        'id': 'a', 'type': 'FILL', 'name': 'Dangling', 'channels': [{'channel': 'ROUGHNESS', 'enabled': True, 'value': .3}],
        'masks': [{'id': 'm', 'source': 'REFERENCE', 'reference_anchor': 'nowhere'}]}]}))
    loose = sets.create(scene, 'Dangling', 512); loose.preview_backend = 'CPU'
    apply(loose, dangling)
    assert not issues(loose) and any('missing' in text for text in warnings(loose)), warnings(loose)
    LayerEvaluator(scene).evaluate_texture_set(loose.id)
    np.testing.assert_allclose(images.pixels(images.output(loose, 'ROUGHNESS').image)[..., 0], .5, atol=1e-6)
    cyclic = Path(directory) / 'cyclic.openpaintmaterial'
    cyclic.write_text(json.dumps({'format_version': VERSION, 'layers': [{
        'id': 'a', 'type': 'FILL', 'name': 'Cyclic', 'channels': [{'channel': 'ROUGHNESS', 'enabled': True, 'value': .3}],
        'masks': [{'id': 'ref', 'source': 'REFERENCE', 'reference_anchor': 'anchor'},
                  {'id': 'anchor', 'source': 'ANCHOR'}]}]}))
    before = len(loose.layers)
    try:
        apply(loose, cyclic)
        raise AssertionError('Cyclic material accepted')
    except ValueError as error:
        assert 'cycle' in str(error), error
    assert len(loose.layers) == before
    identifier = ts.id
    bpy.ops.wm.save_as_mainfile(filepath=directory + '/anchors.blend')
    bpy.ops.wm.open_mainfile(filepath=directory + '/anchors.blend')
    ts = next(t for t in bpy.context.scene.open_painter.texture_sets if t.id == identifier)
    assert not issues(ts) and not warnings(ts)
    assert anchors.index(ts)
open_painter.unregister()
print('ANCHOR_POINTS_PASS', flush=True)
