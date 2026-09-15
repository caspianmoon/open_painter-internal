"""Links drawn in the Open Graph become anchor references; invalid ones are refused."""
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'src'))
import bpy
import open_painter
from open_painter.core import texture_set_manager as sets, layer_stack as stack
from open_painter.evaluation import anchors
from open_painter.graph import mirror
from open_painter.graph.edits import link_keys
from open_painter.graph.tree import OUTPUT_NODE
from open_painter.utils.ids import new_id

open_painter.register()
scene = bpy.context.scene
ts = sets.create(scene, 'Graph links', 512)
ts.preview_backend = 'CPU'
base = stack.add(ts, 'FILL', 'Base'); base_id = base.id
anchor_mask = base.masks.add(); anchor_mask.id, anchor_mask.name, anchor_mask.source = new_id(), 'Base coverage', 'ANCHOR'
folder = stack.add(ts, 'FOLDER', 'Damage'); folder_id = folder.id
child = stack.add(ts, 'FILL', 'Scratches'); child_id = child.id
child.parent_id = folder_id
reader = stack.add(ts, 'FILL', 'Reader'); reader_id = reader.id
reference = reader.masks.add(); reference.id, reference.name, reference.source = new_id(), 'Damage mask', 'REFERENCE'
wear = reader.masks.add(); wear.id, wear.name, wear.source, wear.generator = new_id(), 'Wear', 'GENERATOR', 'EDGE_WEAR'
tree = mirror.ensure_tree(ts)


def by_id():
    return mirror.nodes_by_id(tree)


def socket(node, identifier, output):
    return next(s for s in (node.outputs if output else node.inputs) if s.identifier == identifier)


def user_link(from_id, from_socket, to_id, to_socket):
    """A link made outside the mirror's own sync, as the node editor would."""
    nodes = by_id()
    return tree.links.new(socket(nodes[from_id], from_socket, True), socket(nodes[to_id], to_socket, False))


def settle():
    """What the lifecycle tick does after an edit: reconcile when needed."""
    if tree.get('op_signature') != mirror.signature(ts) or tree.get('op_links_dirty'):
        tree['op_links_dirty'] = False
        mirror.reconcile(tree, ts)


def layer(identifier):
    return next(item for item in ts.layers if item.id == identifier)


# 1. Dragging from a folder's Composite into a reference mask publishes the folder and sets the anchor.
assert not layer(folder_id).publish_group
user_link(folder_id, 'group', reader_id, f'mask#{reference.id}')
assert layer(folder_id).publish_group, 'linking the composite socket must publish it'
assert layer(reader_id).masks[0].reference_anchor == anchors.group_id(folder_id)
settle()
assert (folder_id, 'group', reader_id, f'mask#{reference.id}') in link_keys(tree)
assert socket(by_id()[folder_id], 'group', True).name == 'Composite'
print('GRAPH_LINKS composite link: PASS', flush=True)

# 2. A mask anchor into a fill channel's content; a layer output into a micro detail slot.
user_link(base_id, anchor_mask.id, reader_id, 'content#ROUGHNESS')
assert stack.channel_for(layer(reader_id), 'ROUGHNESS').anchor == anchor_mask.id
user_link(child_id, 'out', reader_id, f'detail#{wear.id}#HEIGHT')
assert layer(reader_id).masks[1].detail_height_anchor == anchors.output_id(child_id)
assert layer(child_id).publish_output
settle()
keys = link_keys(tree)
assert (base_id, anchor_mask.id, reader_id, 'content#ROUGHNESS') in keys
assert (child_id, 'out', reader_id, f'detail#{wear.id}#HEIGHT') in keys
print('GRAPH_LINKS content and detail links: PASS', flush=True)

# 3. A cycle is refused: the field is reverted, the status explains, the stray link is dropped on sync.
scene.open_painter.status = ''
layer(base_id).publish_output = True
settle()
user_link(reader_id, 'out', base_id, 'content#ROUGHNESS')   # Base (below) reading Reader (above)
assert stack.channel_for(layer(base_id), 'ROUGHNESS').anchor == '', 'a cyclic link changed the stack'
assert 'cycle' in scene.open_painter.status.lower(), scene.open_painter.status
assert tree.get('op_links_dirty')
settle()
assert (reader_id, 'out', base_id, 'content#ROUGHNESS') not in link_keys(tree)
# Links into the output node are informational: refused without touching the stack.
before = {layer.id: stack.channel_for(layer, 'ROUGHNESS').anchor for layer in ts.layers}
user_link(base_id, 'out', OUTPUT_NODE, 'final#ROUGHNESS')
settle()
assert before == {layer.id: stack.channel_for(layer, 'ROUGHNESS').anchor for layer in ts.layers}
print('GRAPH_LINKS refusal: PASS', flush=True)

# 4. Removing a link clears the field; removing a legacy reference link clears the layer reference.
nodes = by_id()
link = next(l for l in tree.links if l.to_node == nodes[reader_id] and l.to_socket.identifier == 'content#ROUGHNESS')
tree.links.remove(link)
assert stack.channel_for(layer(reader_id), 'ROUGHNESS').anchor == ''
legacy = layer(reader_id).masks.add(); legacy.id, legacy.source = new_id(), 'REFERENCE'
legacy.reference_layer, legacy.reference_channel = base_id, 'ROUGHNESS'
settle()
assert (base_id, 'legacy#ROUGHNESS', reader_id, f'mask#{legacy.id}') in link_keys(tree)
nodes = by_id()
link = next(l for l in tree.links if l.to_socket.identifier == f'mask#{legacy.id}')
tree.links.remove(link)
assert layer(reader_id).masks[2].reference_layer == ''
settle()
assert not any(key[1] == 'legacy#ROUGHNESS' for key in link_keys(tree)), 'unused legacy output survived'
# A legacy output cannot be linked; anchors are the way.
legacy.reference_layer = base_id
settle()
user_link(base_id, 'legacy#ROUGHNESS', reader_id, f'mask#{reference.id}')
assert layer(reader_id).masks[0].reference_anchor == anchors.group_id(folder_id)
assert 'anchor' in scene.open_painter.status.lower()
print('GRAPH_LINKS removal and legacy: PASS', flush=True)

# 5. Replacing a link on a one-link input swaps the reference in one step.
user_link(base_id, anchor_mask.id, reader_id, f'mask#{reference.id}')
assert layer(reader_id).masks[0].reference_anchor == anchor_mask.id
settle()
assert (base_id, anchor_mask.id, reader_id, f'mask#{reference.id}') in link_keys(tree)
assert (folder_id, 'group', reader_id, f'mask#{reference.id}') not in link_keys(tree)
print('GRAPH_LINKS replacement: PASS', flush=True)
print('GRAPH_LINK_EDITS_PASS', flush=True)
open_painter.unregister()
