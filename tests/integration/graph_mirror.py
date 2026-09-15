"""The Open Graph mirrors the stack: projection, incremental reconcile, undo."""
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'src'))
import bpy
import open_painter
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
for layer in ts.layers:
    node = by_id[layer.id]
    assert node.label == layer.name and node.op_kind == ('FOLDER' if layer.type == 'FOLDER' else 'LAYER')
    assert [s.identifier for s in node.inputs] == [i for i, _, _ in nodes[layer.id]['inputs']]
    assert [s.identifier for s in node.outputs] == [i for i, _, _ in nodes[layer.id]['outputs']]
    assert all(s.link_limit == 1 for s in node.inputs)
    if layer.type == 'FOLDER':
        assert node.parent is not None and node.parent['op_id'] == mirror.FRAME_PREFIX + layer.id
        assert (node.parent.parent['op_id'] == mirror.FRAME_PREFIX + layer.parent_id) if layer.parent_id \
            else node.parent.parent is None
    elif layer.parent_id:
        assert node.parent is not None and node.parent['op_id'] == mirror.FRAME_PREFIX + layer.parent_id
    else:
        assert node.parent is None
from open_painter.graph.edits import link_keys
assert link_keys(tree) == set(links), (link_keys(tree) ^ set(links))
# The stack result feeds the output node from the top root layer.
top = [layer for layer in ts.layers if not layer.parent_id][-1]
assert all((top.id, 'out', OUTPUT_NODE, f'final#{c}') in links for c in ('BASE_COLOR', 'ROUGHNESS'))
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
mirror.reconcile(tree, ts)
by_id = mirror.nodes_by_id(tree)
assert producer_id in by_id and consumer_id in by_id
assert (producer_id, 'out', consumer_id, f'mask#{mask.id}') in link_keys(tree)
assert next(s for s in by_id[producer_id].outputs if s.identifier == 'out').name == 'Output'
# Removing the producer removes its node; the dangling reference draws no link.
ts.layers.remove(next(i for i, item in enumerate(ts.layers) if item.id == producer_id))
mirror.reconcile(tree, ts)
assert producer_id not in mirror.nodes_by_id(tree)
assert not any(key[2] == consumer_id for key in link_keys(tree))
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
