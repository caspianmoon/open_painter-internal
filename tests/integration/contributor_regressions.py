"""Contributor integration: mapping, image feeds, row order and safe duplication."""
from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.authoring import assets
from open_painter.core import layer_stack as stack, texture_set_manager as sets
from open_painter.graph import authoring, edits, mirror, rows
from open_painter.graph.tree import IMAGE_PREFIX, SOCKET_CONTENT
from open_painter.properties.bake import OP_BakeSettings
from open_painter.utils.ids import new_id

open_painter.register()
scene = bpy.context.scene
ts = sets.create(scene, 'Contributor review', 512)
ts.preview_backend = 'CPU'
assert ts.bake.device == 'AUTO' and not ts.bake.selected_to_active
assert {item.identifier: item.value for item in OP_BakeSettings.bl_rna.properties['device'].enum_items} == {
    'CPU': 0, 'SCENE': 1, 'AUTO': 2}
a_id = stack.add(ts, 'FILL', 'A').id
b_id = stack.add(ts, 'FILL', 'B').id

def layer(identifier):
    return next(item for item in ts.layers if item.id == identifier)

def prop(channel, identifier=a_id):
    return stack.channel_for(layer(identifier), channel)

assert {p.channel for p in layer(a_id).channels if p.enabled} == stack.FILL_DEFAULT_CHANNELS
assert {p['channel'] for p in assets.new_layer('id', 'Fill')['channels'] if p['enabled']} == stack.FILL_DEFAULT_CHANNELS
prop('BASE_COLOR').scale = (2, 3)
prop('ROUGHNESS').scale = (7, 8)
prop('ROUGHNESS').value = .42
prop('ROUGHNESS').enabled = False
assert tuple(prop('BASE_COLOR').scale) == (2, 3), 'unrelated edit overwrote another channel mapping'
assert tuple(prop('ROUGHNESS').scale) == (7, 8)
prop('HEIGHT').value = 4.5
prop('ROUGHNESS').value = 4.5
assert prop('HEIGHT').value == 4.5 and prop('ROUGHNESS').value == 1
prop('BASE_COLOR').fill_scale = (4, 6)
assert all(tuple(p.scale) == (4, 6) for p in layer(a_id).channels)
assert 'fill_scale' not in stack.snapshot(prop('BASE_COLOR'))
prop('ROUGHNESS').enabled = True

# Panel reordering is never undone by an idle graph. Actual drags still reorder.
tree = mirror.ensure_tree(ts)
ts.layers.move(stack.index_of(ts, a_id), stack.index_of(ts, b_id))
stack.invalidate(ts)
expected = [item.id for item in ts.layers]
assert not rows.order_from_y(tree, ts)
mirror.reconcile(tree, ts)
assert not rows.order_from_y(tree, ts) and [item.id for item in ts.layers] == expected
nodes = mirror.nodes_by_id(tree)
nodes[a_id].location_absolute.y = nodes[b_id].location_absolute.y - 500
assert rows.order_from_y(tree, ts)
mirror.reconcile(tree, ts)

# Rejected chain links must not enable parked masks.
mask_id = authoring.add(ts, layer(a_id), 'PROCEDURAL', 'VALUE')
mask = next(m for m in layer(a_id).masks if m.id == mask_id)
mask.enabled = False
mirror.reconcile(tree, ts)
nodes = mirror.nodes_by_id(tree)
tree.links.new(nodes[mask_id].outputs['Coverage'], nodes[mask_id].inputs['Previous'])
assert not next(m for m in layer(a_id).masks if m.id == mask_id).enabled
mirror.reconcile(tree, ts)

# An image replaces a procedural/anchor source, keeps its own per-channel mapping,
# survives numeric edits, and disconnects without changing other channels.
image = bpy.data.images.new('Graph image', width=8, height=8)
with mirror.syncing():
    node = tree.nodes.new('OPImageNode')
    node.op_id, node.op_kind = IMAGE_PREFIX + new_id(), 'IMAGE'
    node.image, node.scale = image, (9, 10)
    node.outputs.new(SOCKET_CONTENT, 'Image', identifier='out')
image_id = node.op_id
prop('BASE_COLOR').procedural = 'VALUE'
edits.feed_image(ts, tree, image_id, a_id, 'content#BASE_COLOR')
assert prop('BASE_COLOR').image == image and not prop('BASE_COLOR').procedural
assert tuple(prop('BASE_COLOR').scale) == (9, 10)
assert tuple(prop('ROUGHNESS').scale) == (4, 6)
mirror.reconcile(tree, ts)
assert (image_id, 'out', a_id, 'content#BASE_COLOR') in edits.link_keys(tree)
node.scale = (11, 12)
assert tuple(prop('BASE_COLOR').scale) == (11, 12)
assert tuple(prop('ROUGHNESS').scale) == (4, 6)
link = next(link for link in tree.links if link.from_node == node)
tree.links.remove(link)
assert prop('BASE_COLOR').image is None
mirror.reconcile(tree, ts)
edits.feed_image(ts, tree, image_id, a_id, 'content#BASE_COLOR')
other_image = bpy.data.images.new('Panel override', width=8, height=8)
prop('BASE_COLOR').image = other_image
node.image = image
assert prop('BASE_COLOR').image == other_image, 'a stale feed overwrote the panel image'
mirror.reconcile(tree, ts)

# Duplicate a nested mask group and its visible child together. The copied child
# belongs to one copied group, and references/IDs are independent.
group_id = authoring.add(ts, layer(a_id), 'MASK', 'GROUP')
child_id = authoring.add(ts, layer(a_id), 'MASK', 'NOISE', mask_index=len(layer(a_id).masks)-1)
assert next(m for m in layer(a_id).masks if m.id == child_id).parent_id == group_id
mirror.reconcile(tree, ts)
count = len(layer(a_id).masks)
with mirror.syncing():
    for identifier in (group_id, child_id):
        original = mirror.nodes_by_id(tree)[identifier]
        extra = tree.nodes.new(original.bl_idname)
        extra.op_id, extra.op_kind, extra.op_owner = original.op_id, original.op_kind, original.op_owner
assert edits.adopt_duplicates(tree, ts)
assert len(layer(a_id).masks) == count + 2
copies = [m for m in layer(a_id).masks if m.name.endswith(' Copy')]
assert len(copies) == 2 and copies[1].parent_id == copies[0].id
assert not copies[0].enabled
mirror.reconcile(tree, ts)

# A copied filter returns its own identity after being moved in the RNA collection.
filter_id = authoring.add(ts, layer(a_id), 'FILTER', 'LEVELS', 'ROUGHNESS')
authoring.add(ts, layer(a_id), 'FILTER', 'INVERT', 'ROUGHNESS')
channel = prop('ROUGHNESS')
copy_id = edits._duplicate_effect(ts, layer(a_id), channel, channel.effects[0])
assert copy_id == channel.effects[1].id and copy_id != filter_id
assert channel.effects[1].kind == 'LEVELS' and not channel.effects[1].enabled

# Folder + child duplication adopts the child already copied with the folder.
folder_id = stack.add(ts, 'FOLDER', 'Folder').id
nested_id = stack.add(ts, 'FILL', 'Nested', folder_id).id
mirror.reconcile(tree, ts)
count = len(ts.layers)
with mirror.syncing():
    for identifier in (nested_id, folder_id):
        original = mirror.nodes_by_id(tree)[identifier]
        extra = tree.nodes.new(original.bl_idname)
        extra.op_id, extra.op_kind, extra.op_owner = original.op_id, original.op_kind, original.op_owner
assert edits.adopt_duplicates(tree, ts)
assert len(ts.layers) == count + 2, 'folder children were duplicated twice'
mirror.reconcile(tree, ts)

# Deleting a layer and its mask node together removes the stack layer too.
with mirror.syncing():
    nodes = mirror.nodes_by_id(tree)
    tree.nodes.remove(nodes[a_id])
    tree.nodes.remove(nodes[mask_id])
edits.on_tree_update(tree)
assert a_id not in {item.id for item in ts.layers}
mirror.reconcile(tree, ts)
assert not any(node.op_owner == a_id for node in tree.nodes if hasattr(node, 'op_owner'))

open_painter.unregister()
print('CONTRIBUTOR_REGRESSIONS_PASS', flush=True)
