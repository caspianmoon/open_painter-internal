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
reference_id, wear_id, anchor_id = reference.id, wear.id, anchor_mask.id
user_link(folder_id, 'group', reference_id, 'ref')
assert layer(folder_id).publish_group, 'linking the composite socket must publish it'
assert layer(reader_id).masks[0].reference_anchor == anchors.group_id(folder_id)
settle()
assert (folder_id, 'group', reference_id, 'ref') in link_keys(tree)
assert socket(by_id()[folder_id], 'group', True).name == 'Composite'
print('GRAPH_LINKS composite link: PASS', flush=True)

# 2. A mask anchor into a fill channel's content; a layer output into a micro detail slot.
user_link(anchor_id, 'out', reader_id, 'content#ROUGHNESS')
assert stack.channel_for(layer(reader_id), 'ROUGHNESS').anchor == anchor_mask.id
user_link(child_id, 'out', wear_id, 'detail#HEIGHT')
assert layer(reader_id).masks[1].detail_height_anchor == anchors.output_id(child_id)
assert layer(child_id).publish_output
settle()
keys = link_keys(tree)
assert (anchor_id, 'out', reader_id, 'content#ROUGHNESS') in keys
assert (child_id, 'out', wear_id, 'detail#HEIGHT') in keys
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
user_link(reader_id, 'out', OUTPUT_NODE, f'stack#{base_id}')
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
legacy_id = legacy.id
settle()
assert (base_id, 'legacy#ROUGHNESS', legacy_id, 'ref') in link_keys(tree)
nodes = by_id()
link = next(l for l in tree.links if l.to_node.op_id == legacy_id and l.to_socket.identifier == 'ref')
tree.links.remove(link)
assert layer(reader_id).masks[2].reference_layer == ''
settle()
assert not any(key[1] == 'legacy#ROUGHNESS' for key in link_keys(tree)), 'unused legacy output survived'
# A legacy output cannot be linked; anchors are the way.
layer(reader_id).masks[2].reference_layer = base_id
settle()
user_link(base_id, 'legacy#ROUGHNESS', reference_id, 'ref')
assert layer(reader_id).masks[0].reference_anchor == anchors.group_id(folder_id)
assert 'anchor' in scene.open_painter.status.lower()
print('GRAPH_LINKS removal and legacy: PASS', flush=True)

# 5. Replacing a link on a one-link input swaps the reference in one step.
user_link(anchor_id, 'out', reference_id, 'ref')
assert layer(reader_id).masks[0].reference_anchor == anchor_id
settle()
assert (anchor_id, 'out', reference_id, 'ref') in link_keys(tree)
assert (folder_id, 'group', reference_id, 'ref') not in link_keys(tree)
print('GRAPH_LINKS replacement: PASS', flush=True)

# 6. Structural links reorder: a mask before another, a filter to its chain end, a layer under another.
scene.open_painter.status = ''
extra = layer(reader_id).masks.add(); extra.id, extra.name, extra.source = new_id(), 'Noise', 'NOISE'
extra_id = extra.id
settle()
assert [m.id for m in layer(reader_id).masks][-1] == extra_id
user_link(extra_id, 'out', reference_id, 'prev')            # Noise placed before the reference mask
assert [m.id for m in layer(reader_id).masks][:2] == [extra_id, reference_id], [m.name for m in layer(reader_id).masks]
assert not scene.open_painter.status, scene.open_painter.status
settle()
user_link(extra_id, 'out', reader_id, 'mask')                # ... and back to the chain end
assert [m.id for m in layer(reader_id).masks][-1] == extra_id
settle()
prop = stack.channel_for(layer(reader_id), 'ROUGHNESS')
for kind in ('LEVELS', 'INVERT', 'BLUR'):
    effect = prop.effects.add(); effect.kind = kind
settle()
first = mirror.effect_node_id(layer(reader_id), 'ROUGHNESS', 0, prop.effects[0])
last = mirror.effect_node_id(layer(reader_id), 'ROUGHNESS', 2, prop.effects[2])
user_link(last, 'out', first, 'in')                            # Blur before Levels
assert [e.kind for e in stack.channel_for(layer(reader_id), 'ROUGHNESS').effects] == ['BLUR', 'LEVELS', 'INVERT']
settle()
# Anchored content enters a filtered channel at its first filter; the layer's channel socket is the chain end.
blur = mirror.effect_node_id(layer(reader_id), 'ROUGHNESS', 0, prop.effects[0])
user_link(anchor_id, 'out', blur, 'in')
assert stack.channel_for(layer(reader_id), 'ROUGHNESS').anchor == anchor_id
settle()
keys = link_keys(tree)
assert (anchor_id, 'out', blur, 'in') in keys
assert not any(k[2] == reader_id and k[3] == 'content#ROUGHNESS' and k[0] == anchor_id for k in keys)
scene.open_painter.status = ''
user_link(anchor_id, 'out', reader_id, 'content#ROUGHNESS')   # refused: filtered channels take anchors at their first filter
assert 'first filter' in scene.open_painter.status, scene.open_painter.status
settle()
order = [l.id for l in ts.layers]
scene.open_painter.status = ''
user_link(reader_id, 'out', base_id, 'below')                  # refused: roots stack by row, not by link
assert [l.id for l in ts.layers] == order, 'a root below-link changed the stack'
assert 'row' in scene.open_painter.status.lower(), scene.open_painter.status
settle()
assert (reader_id, 'out', base_id, 'below') not in link_keys(tree)
# Y rows order flat roots instead: Reader dragged under Base in the view.
from open_painter.graph import rows as graph_rows
nodes = by_id()
reader_node, base_node = nodes[reader_id], nodes[base_id]
reader_node.location_absolute = (base_node.location_absolute[0], base_node.location_absolute[1] - 500.)
assert graph_rows.order_from_y(tree, ts) is True
assert [l.id for l in ts.layers].index(reader_id) == [l.id for l in ts.layers].index(base_id) - 1
settle()
assert (reader_id, 'out', OUTPUT_NODE, f'stack#{reader_id}') in link_keys(tree)
user_link(reader_id, 'out', folder_id, 'children')             # ... then becomes the folder's top child
assert layer(reader_id).parent_id == folder_id and [l.id for l in ts.layers if l.parent_id == folder_id][-1] == reader_id
settle()
scene.open_painter.status = ''
user_link(base_id, 'out', extra_id, 'prev')                    # masks never chain across layers
assert 'Anchor Point' in scene.open_painter.status
settle()
print('GRAPH_LINKS chain reorder: PASS', flush=True)

# 7. Deleting a mask or filter node deletes it; painted masks are kept and their node returns.
count = len(layer(reader_id).masks)
tree.nodes.remove(by_id()[extra_id])
assert len(layer(reader_id).masks) == count - 1 and extra_id not in [m.id for m in layer(reader_id).masks]
settle()
painted = layer(reader_id).masks.add(); painted.id, painted.source = new_id(), 'PAINT'
painted_id = painted.id
settle()
scene.open_painter.status = ''
tree.nodes.remove(by_id()[painted_id])
assert painted_id in [m.id for m in layer(reader_id).masks] and 'Painter panel' in scene.open_painter.status
settle()
assert painted_id in by_id()
tree.nodes.remove(by_id()[blur])
assert [e.kind for e in stack.channel_for(layer(reader_id), 'ROUGHNESS').effects] == ['LEVELS', 'INVERT']
settle()
print('GRAPH_LINKS node deletion: PASS', flush=True)

# 8. The Add menu operator parks masks and filters until they are wired.
ts.active_layer = stack.index_of(ts, child_id)
area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
with bpy.context.temp_override(area=area):
    assert bpy.ops.open_painter.graph_add(kind='GENERATOR', choice='EDGE_WEAR') == {'FINISHED'}
    assert bpy.ops.open_painter.graph_add(kind='SCENE', choice='UP') == {'FINISHED'}
    assert bpy.ops.open_painter.graph_add(kind='FILTER', choice='LEVELS', channel='ROUGHNESS') == {'FINISHED'}
    assert bpy.ops.open_painter.graph_add(kind='CHANNEL_ANCHOR', choice='', channel='ROUGHNESS') == {'FINISHED'}
added = layer(child_id)
assert [m.source for m in added.masks] == ['GENERATOR', 'SCENE'] and added.masks[1].influence.kind == 'UP'
assert [e.kind for e in stack.channel_for(added, 'ROUGHNESS').effects] == ['LEVELS', 'ANCHOR']
assert [m.enabled for m in added.masks] == [False, False]
assert [e.enabled for e in stack.channel_for(added, 'ROUGHNESS').effects] == [False, True]
settle()
keys = link_keys(tree)
assert not any(k[2] == child_id and k[3] == 'mask' for k in keys)
# The enabled channel anchor still publishes the unchanged channel content.
assert (stack.channel_for(added, 'ROUGHNESS').effects[1].id, 'out', child_id, 'content#ROUGHNESS') in keys
# Wiring a parked node puts it to work and moves it into place.
scene_node = mirror.mask_node_id(added, 1, added.masks[1])
user_link(scene_node, 'out', child_id, 'mask')
assert added.masks[1].enabled
levels_node = mirror.effect_node_id(added, 'ROUGHNESS', 0, stack.channel_for(added, 'ROUGHNESS').effects[0])
anchor_node = mirror.effect_node_id(added, 'ROUGHNESS', 1, stack.channel_for(added, 'ROUGHNESS').effects[1])
user_link(levels_node, 'out', anchor_node, 'in')
assert stack.channel_for(added, 'ROUGHNESS').effects[0].enabled
settle()
keys = link_keys(tree)
assert (scene_node, 'out', child_id, 'mask') in keys
assert (levels_node, 'out', anchor_node, 'in') in keys
channel_anchor = stack.channel_for(added, 'ROUGHNESS').effects[1].id
assert (channel_anchor, 'out', child_id, 'content#ROUGHNESS') in keys
print('GRAPH_LINKS add menu: PASS', flush=True)

# 9. Robustness: a stale tree, duplicated nodes and deleted frames.
scratch = layer(child_id).masks.add(); scratch.id, scratch.source, scratch.reference_anchor = new_id(), 'REFERENCE', anchor_id
link = next(l for l in tree.links if l.to_node.op_id == child_id and l.to_socket.identifier == 'mask')
tree.links.remove(link)        # a user edit before the tick reconciled the new mask
assert layer(child_id).masks[-1].reference_anchor == anchor_id, 'stale-tree edit cleared a fresh reference'
settle()
nodes = by_id()
nodes[child_id].location_absolute = (4000., -2000.)
extra_mask = layer(child_id).masks.add(); extra_mask.id, extra_mask.source = new_id(), 'NOISE'
settle()
x, y = by_id()[extra_mask.id].location_absolute
assert abs(y + 2000.) < 1 and 0 < 4000. - x < 2000, (x, y)   # placed on its moved layer's row
duplicate = tree.nodes.new('OPMaskNode'); duplicate.op_id, duplicate.op_kind = extra_mask.id, 'MASK'
frame = next(n for n in tree.nodes if n.bl_idname == 'NodeFrame')
tree.nodes.remove(frame)
assert tree.get('op_links_dirty')
settle()
assert sum(1 for n in tree.nodes if getattr(n, 'op_id', '') == extra_mask.id) == 1
assert any(n.bl_idname == 'NodeFrame' for n in tree.nodes)
print('GRAPH_LINKS robustness: PASS', flush=True)

# 10. Cross-layer mask sharing: one mask fans out into other layers' Mask inputs.
from open_painter.graph.edits import is_mask_share
scene.open_painter.status = ''
base_count, reader_count, child_count = (len(layer(i).masks) for i in (base_id, reader_id, child_id))
# An existing anchor shares with one link and no inserted anchor...
assert is_mask_share(ts, anchor_id, 'out', reader_id, 'mask')
assert not is_mask_share(ts, anchor_id, 'out', anchor_id, 'mask')
user_link(anchor_id, 'out', reader_id, 'mask')
assert len(layer(base_id).masks) == base_count, 'sharing minted an anchor it did not need'
reader_ref = layer(reader_id).masks[-1]
assert reader_ref.reference_anchor == anchor_id
settle()
keys = link_keys(tree)
assert (anchor_id, 'out', reader_ref.id, 'ref') in keys
assert (reader_ref.id, 'out', reader_id, 'mask') in keys
# ...and fans out: the same anchor feeds a second layer too.
user_link(anchor_id, 'out', child_id, 'mask')
assert len(layer(base_id).masks) == base_count
assert layer(child_id).masks[-1].reference_anchor == anchor_id
settle()
assert (anchor_id, 'out', layer(child_id).masks[-1].id, 'ref') in link_keys(tree)
# A plain mask shares through an auto-inserted anchor that leaves its source unchanged.
gen_id = next(m.id for m in layer(child_id).masks if m.source == 'GENERATOR')
user_link(gen_id, 'out', reader_id, 'mask')
child_masks = layer(child_id).masks
auto = child_masks[1]
assert auto.source == 'ANCHOR' and child_masks[0].id == gen_id
assert layer(reader_id).masks[-1].reference_anchor == auto.id
settle()
assert (auto.id, 'out', layer(reader_id).masks[-1].id, 'ref') in link_keys(tree)
# A share reading the target's complete output would cycle; nothing is left behind.
layer(base_id).publish_output = True
next(m for m in layer(reader_id).masks if m.id == wear_id).detail_height_anchor = anchors.output_id(base_id)
settle()
scene.open_painter.status = ''
before = ([m.id for m in layer(reader_id).masks], [m.id for m in layer(base_id).masks])
user_link(wear_id, 'out', base_id, 'mask')
assert ([m.id for m in layer(reader_id).masks], [m.id for m in layer(base_id).masks]) == before
assert 'cycle' in scene.open_painter.status.lower(), scene.open_painter.status
settle()
print('GRAPH_LINKS mask sharing: PASS', flush=True)

# 11. Optional wiring: cutting a chain wire parks its source, wiring re-enables,
# and the chain heals around parked nodes.
scene.open_painter.status = ''
scene_id = next(m.id for m in layer(child_id).masks if m.source == 'SCENE')
scratch_id = next(m.id for m in layer(child_id).masks if m.source == 'REFERENCE')
# Section 9 parked the scene mask: link it again before testing a fresh cut.
user_link(scene_id, 'out', scratch_id, 'prev')
settle()
link = next(l for l in tree.links if l.from_node.op_id == scene_id and l.to_socket.identifier == 'prev')
tree.links.remove(link)
assert not next(m for m in layer(child_id).masks if m.id == scene_id).enabled
settle()
keys = link_keys(tree)
assert not any(k[0] == scene_id for k in keys)
assert (scene_id, 'out', scratch_id, 'prev') not in keys
user_link(scene_id, 'out', scratch_id, 'prev')
assert next(m for m in layer(child_id).masks if m.id == scene_id).enabled
settle()
assert (scene_id, 'out', scratch_id, 'prev') in link_keys(tree)
print('GRAPH_LINKS optional wiring: PASS', flush=True)
print('GRAPH_LINK_EDITS_PASS', flush=True)
open_painter.unregister()
