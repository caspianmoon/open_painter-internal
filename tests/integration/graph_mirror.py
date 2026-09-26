"""The Open Graph mirrors the stack: projection, incremental reconcile, undo."""
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'src'))
import bpy
import open_painter
from open_painter.constants import CHANNELS
from open_painter.core import texture_set_manager as sets, layer_stack as stack
from open_painter.evaluation import anchors
from open_painter.graph import mirror
from open_painter.graph.tree import OUTPUT_NODE, TREE_ID
from open_painter.smart_materials import composer
from open_painter.utils.ids import new_id

open_painter.register()
scene = bpy.context.scene
ts = sets.create(scene, 'Graph mirror', 512)
ts.preview_backend = 'CPU'
from open_painter.core import image_manager as images
for kind in ('CURVATURE', 'AO', 'POSITION', 'WORLD_NORMAL', 'THICKNESS', 'UV_BORDER'):
    entry = ts.baked_maps.add()
    entry.kind, entry.image = kind, images.create('Graph ' + kind, 32)
composer.apply(ts, 'Worn Painted Metal')
ts_id = ts.id


def find():
    return next(item for item in bpy.context.scene.open_painter.texture_sets if item.id == ts_id)


tree = mirror.ensure_tree(ts)
assert tree.bl_idname == TREE_ID and tree['op_graph_set'] == ts_id and tree.use_fake_user
nodes, frames, links = mirror.project(ts)
by_id = mirror.nodes_by_id(tree)
assert set(by_id) == set(nodes) | set(frames), (set(by_id) ^ (set(nodes) | set(frames)))
assert OUTPUT_NODE in by_id and by_id[OUTPUT_NODE].bl_idname == 'OPOutputNode'
folders = [layer for layer in ts.layers if layer.type == 'FOLDER']
assert folders and all(mirror.FRAME_PREFIX + f.id in frames for f in folders)
for identifier, spec in nodes.items():
    node = by_id[identifier]
    assert node.label == spec['label'] and node.op_kind == spec['kind'], (node.label, spec)
    assert [s.identifier for s in node.inputs] == [i for i, _, _ in spec['inputs']]
    assert [s.identifier for s in node.outputs] == [i for i, _, _ in spec['outputs']]
    assert all(s.link_limit == 1 for s in node.inputs)
    parent = node.parent['op_id'] if node.parent is not None else ''
    assert parent == spec['frame'], (identifier, parent, spec['frame'])
    if parent:
        outer = node.parent.parent['op_id'] if node.parent.parent is not None else ''
        assert outer == frames[parent]['frame'], (identifier, outer, frames[parent]['frame'])
for layer in ts.layers:
    node = by_id[layer.id]
    if layer.type == 'FOLDER':
        assert node.parent['op_id'] == mirror.FRAME_PREFIX + layer.id
    # Every mask and every channel filter is a node inside the layer's frame.
    for index, mask in enumerate(layer.masks):
        assert by_id[mirror.mask_node_id(layer, index, mask)].op_owner == layer.id
    for prop in layer.channels:
        for index, effect in enumerate(prop.effects):
            item = by_id[mirror.effect_node_id(layer, prop.channel, index, effect)]
            assert item.op_owner == layer.id and item.op_channel == prop.channel
from open_painter.graph.edits import link_keys
assert link_keys(tree) == set(links), (link_keys(tree) ^ set(links))
# Every root layer feeds the output node from its own socket; root order reads from
# Y rows, so roots never chain through Backdrop; nested children still chain
# bottom to top, and the top child feeds its folder's Children.
roots = [layer for layer in ts.layers if not layer.parent_id]
assert roots
assert all((layer.id, 'out', OUTPUT_NODE, f'stack#{layer.id}') in links for layer in roots)
# Compact view: no per-channel map sockets are drawn, but every channel rides
# the bundle in the background.
assert nodes[OUTPUT_NODE]['maps'] == list(CHANNELS)
root_ids = {layer.id for layer in roots}
assert not any(to_socket == 'below' and to_node in root_ids
               for _, _, to_node, to_socket in links)
folder = folders[0]
kids = [layer for layer in ts.layers if layer.parent_id == folder.id]
assert len(kids) > 1
assert all((a.id, 'out', b.id, 'below') in links for a, b in zip(kids, kids[1:]))
assert (kids[-1].id, 'out', folder.id, 'children') in links
# Enabled masks chain into the layer's Mask input (parked ones float free);
# enabled filters chain into the channel input.
for layer in ts.layers:
    chain = [mirror.mask_node_id(layer, i, mask) for i, mask in enumerate(layer.masks) if mask.enabled]
    if chain:
        assert (chain[-1], 'out', layer.id, 'mask') in links
        for first, second in zip(chain, chain[1:]):
            assert (first, 'out', second, 'prev') in links
    for prop in layer.channels:
        chain = [mirror.effect_node_id(layer, prop.channel, i, effect)
                 for i, effect in enumerate(prop.effects) if effect.enabled]
        if chain:
            assert (chain[-1], 'out', layer.id, f'content#{prop.channel}') in links
            for first, second in zip(chain, chain[1:]):
                assert (first, 'out', second, 'in') in links
assert any(spec['kind'] == 'MASK' for spec in nodes.values()) and any(spec['kind'] == 'EFFECT' for spec in nodes.values())
# Nodes were laid out: no two layer nodes share a position; the output node sits rightmost.
positions = {identifier: tuple(node.location_absolute) for identifier, node in by_id.items()
             if identifier in nodes}
assert len(set(positions.values())) == len(positions), positions
assert positions[OUTPUT_NODE][0] > max(x for i, (x, _) in positions.items() if i != OUTPUT_NODE)
print('GRAPH_MIRROR projection: PASS', flush=True)

# Reconcile is idempotent and keeps node identity; a numeric edit does not change the signature.
pointers = {identifier: node.as_pointer() for identifier, node in by_id.items()}
signature = tree['op_signature']
mirror.reconcile(tree, ts)
assert {i: n.as_pointer() for i, n in mirror.nodes_by_id(tree).items()} == pointers
assert tree['op_signature'] == signature
layer = next(layer for layer in ts.layers if layer.type == 'FILL')
serial = ts.edit_serial
layer.opacity = .37
assert ts.edit_serial != serial and mirror.signature(ts) == signature, 'a slider edit must not re-mirror'
# A moved node keeps its position across reconciles.
moved = mirror.nodes_by_id(tree)[layer.id]
moved.location_absolute = (1234., -567.)
layer.name = 'Renamed'
assert mirror.signature(ts) != signature
mirror.reconcile(tree, ts)
assert tuple(mirror.nodes_by_id(tree)[layer.id].location_absolute) == (1234., -567.)
assert mirror.nodes_by_id(tree)[layer.id].label == 'Renamed'
print('GRAPH_MIRROR incremental reconcile: PASS', flush=True)

# Structural edits: a new layer with a published output and a reference mask appears with its link.
producer = stack.add(ts, 'FILL', 'Producer'); producer_id = producer.id
producer.publish_output = True
consumer = stack.add(ts, 'FILL', 'Consumer'); consumer_id = consumer.id
mask = consumer.masks.add(); mask.id, mask.source = new_id(), 'REFERENCE'
mask.reference_anchor, mask.reference_channel = anchors.output_id(producer_id), 'ROUGHNESS'
mask_id = mask.id
mirror.reconcile(tree, ts)
by_id = mirror.nodes_by_id(tree)
assert producer_id in by_id and consumer_id in by_id and mask_id in by_id
assert (producer_id, 'out', mask_id, 'ref') in link_keys(tree)
assert (mask_id, 'out', consumer_id, 'mask') in link_keys(tree)
assert next(s for s in by_id[producer_id].outputs if s.identifier == 'out').name == 'Output'
# Removing the producer removes its node; the dangling reference draws no link.
ts.layers.remove(next(i for i, item in enumerate(ts.layers) if item.id == producer_id))
mirror.reconcile(tree, ts)
assert producer_id not in mirror.nodes_by_id(tree)
assert not any(key[2] == mask_id and key[3] == 'ref' for key in link_keys(tree))
print('GRAPH_MIRROR structural edits: PASS', flush=True)

# Undo restores stack and tree together; the next reconcile is a no-op.
bpy.ops.ed.undo_push(message='before')
extra = stack.add(find(), 'FILL', 'Extra'); extra_id = extra.id
mirror.reconcile(mirror.find_tree(find()), find())
assert extra_id in mirror.nodes_by_id(mirror.find_tree(find()))
bpy.ops.ed.undo_push(message='extra')
bpy.ops.ed.undo()
ts = find()
tree = mirror.find_tree(ts)
assert tree is not None and extra_id not in {layer.id for layer in ts.layers}
assert extra_id not in mirror.nodes_by_id(tree), 'the tree datablock did not undo with the stack'
assert tree['op_signature'] == mirror.signature(ts)
print('GRAPH_MIRROR undo: PASS', flush=True)
print('GRAPH_MIRROR_PASS', flush=True)
open_painter.unregister()
