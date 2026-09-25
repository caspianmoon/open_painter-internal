"""Graph recipes, nested mask edits, pinned ownership, rollback and persistence."""
import sys
from pathlib import Path
from unittest.mock import patch

import bpy
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import layer_stack as stack, mask_stack, texture_set_manager as sets
from open_painter.evaluation import anchors
from open_painter.evaluation.references import issues
from open_painter.evaluation.shader_graph import Graph
from open_painter.graph import authoring, edits, mirror, templates
from open_painter.graph.operators import _search_items
from open_painter.graph.tree import TREE_ID

open_painter.register()
scene = bpy.context.scene


def find(ts_id):
    return next(ts for ts in bpy.context.scene.open_painter.texture_sets if ts.id == ts_id)


def assert_projection(ts):
    tree = mirror.ensure_tree(ts)
    nodes, frames, links = mirror.project(ts)
    assert edits.link_keys(tree) == set(links)
    assert set(mirror.nodes_by_id(tree)) == set(nodes) | set(frames)
    positions = [tuple(node.location_absolute) for node in tree.nodes if hasattr(node, 'op_id')]
    assert len(positions) == len(set(positions)), 'nodes overlap'
    assert not issues(ts), issues(ts)
    return tree


# Every recipe builds a connected graph and compiles through the real shader engine.
for key in templates.TEMPLATES:
    ts = sets.create(scene, key, 512)
    ts.preview_backend = 'CPU'
    bpy.ops.mesh.primitive_plane_add(size=2)
    sets.assign(scene, ts, bpy.context.object)
    first = templates.apply(ts, key)
    first_id = first.id
    first_members = stack.descendants(ts, first_id) | {first_id}
    assert len(first_members) > 1
    tree = assert_projection(ts)
    graph = Graph(scene, ts, ts.material)
    graph.build()
    while graph.pending_resources:
        graph.step_resources(.1)
    assert graph.outputs and graph.node_count > 0
    graph.close()
    ids = {layer.id for layer in ts.layers}
    ids |= {mask.id for layer in ts.layers for mask in layer.masks}
    ids |= {effect.id for layer in ts.layers for prop in layer.channels for effect in prop.effects}
    shader_groups = {mask.node_group.name for layer in ts.layers for mask in layer.masks if mask.node_group}
    second = templates.apply(ts, key)
    second_id = second.id
    assert second_id != first_id and second.name.endswith('2')
    new_layers = [layer for layer in ts.layers if layer.id not in first_members]
    new_ids = {layer.id for layer in new_layers}
    new_ids |= {mask.id for layer in new_layers for mask in layer.masks}
    new_ids |= {effect.id for layer in new_layers for prop in layer.channels for effect in prop.effects}
    assert not ids & new_ids
    assert not shader_groups & {mask.node_group.name for layer in new_layers for mask in layer.masks if mask.node_group}
    for layer in new_layers:
        for prop in layer.channels:
            if prop.anchor:
                assert prop.anchor in new_ids, 'template instance reads another instance'
    assert_projection(ts)
    print('GRAPH_TEMPLATE', key, 'PASS', flush=True)

# Failed insertion after a shader group was made restores existing layers and selection.
before = [stack.snapshot(layer) for layer in ts.layers]
active_id = stack.active(ts).id
groups_before = set(bpy.data.node_groups.keys())
with patch.object(templates, '_masks', side_effect=ValueError('injected recipe failure')):
    try:
        templates.apply(ts, 'NOISE_RAMP')
        raise AssertionError('failure was swallowed')
    except ValueError as exc:
        assert 'injected' in str(exc)
assert [stack.snapshot(layer) for layer in ts.layers] == before
assert stack.active(ts).id == active_id and set(bpy.data.node_groups.keys()) == groups_before
print('GRAPH_TEMPLATE rollback PASS', flush=True)

# Nested masks are separate chains; moving a group carries its entire subtree.
ts = sets.create(scene, 'Nested edits', 512)
ts.preview_backend = 'CPU'
bpy.ops.mesh.primitive_plane_add(size=2)
sets.assign(scene, ts, bpy.context.object)
layer_id = stack.add(ts, 'FILL', 'Masks').id
layer = lambda: next(item for item in ts.layers if item.id == layer_id)
group = authoring.add(ts, layer(), 'MASK', 'GROUP')
a = authoring.add(ts, layer(), 'PROCEDURAL', 'FBM', mask_index=0)
nested = authoring.add(ts, layer(), 'MASK', 'GROUP', mask_index=0)
b = authoring.add(ts, layer(), 'MASK', 'NOISE', mask_index=2)
tail = authoring.add(ts, layer(), 'MASK', 'NOISE')
tree = assert_projection(ts)
links = edits.link_keys(tree)
assert (b, 'out', nested, 'members') in links
assert (nested, 'out', group, 'members') in links
assert (a, 'out', nested, 'prev') in links
assert (group, 'out', tail, 'prev') in links
assert (tail, 'out', layer_id, 'mask') in links
assert mirror.nodes_by_id(tree)[b].parent == mirror.nodes_by_id(tree)[mirror.mask_frame_id(layer().masks[2])]
edits.reorder(ts, group, 'out', layer_id, 'mask')
assert [m.id for m in layer().masks] == [tail, group, a, nested, b]
assert mask_stack.block(layer().masks, 1) == (1, 5)
edits.reorder(ts, tail, 'out', nested, 'members')
assert [m.id for m in layer().masks] == [group, a, nested, b, tail]
assert layer().masks[-1].parent_id == nested
before = [(m.id, m.parent_id) for m in layer().masks]
try:
    edits.reorder(ts, group, 'out', nested, 'members')
    raise AssertionError('cycle accepted')
except edits.LinkError:
    pass
assert before == [(m.id, m.parent_id) for m in layer().masks]
paint = authoring.add(ts, layer(), 'MASK', 'STROKES', mask_index=2)
assert edits.delete(ts, nested)
assert paint in {m.id for m in layer().masks}
assert all(m.parent_id == group for m in layer().masks if m.id != group)
assert_projection(ts)
print('GRAPH_TEMPLATE nested edits PASS', flush=True)

# Invalid node requests leave no partial masks or filters. New sources have dedicated node types.
before = stack.snapshot(layer())
for kind, choice in [('PROCEDURAL', 'NO_SUCH_PATTERN'), ('FILTER', 'NO_SUCH_FILTER'), ('MESH', 'NO_SUCH_SELECTOR')]:
    try:
        authoring.add(ts, layer(), kind, choice)
        raise AssertionError('invalid node accepted')
    except ValueError:
        pass
assert stack.snapshot(layer()) == before
# Upgrading a generic saved mask to a specialized node preserves its layout.
upgraded = authoring.add(ts, layer(), 'MASK', 'NOISE')
tree = mirror.ensure_tree(ts)
node = mirror.nodes_by_id(tree)[upgraded]
node.location_absolute, node.width, node.hide = (4200, -1700), 245, True
mask = next(mask for mask in layer().masks if mask.id == upgraded)
mask.source, mask.mesh.kind = 'MESH', 'NORMAL_DIRECTION'
mirror.reconcile(tree, ts)
node = mirror.nodes_by_id(tree)[upgraded]
assert node.bl_idname == 'OPMeshMaskNode'
assert tuple(node.location_absolute) == (4200, -1700) and node.width == 245 and node.hide
for kind, choice, expected in [('MESH', 'NORMAL_DIRECTION', 'OPMeshMaskNode'),
                                ('MASK', 'GRAPH', 'OPCustomGraphNode'), ('MASK', 'STROKES', 'OPStrokeMaskNode')]:
    identifier = authoring.add(ts, layer(), kind, choice)
    tree = assert_projection(ts)
    assert mirror.nodes_by_id(tree)[identifier].bl_idname == expected
search = _search_items(None, bpy.context)
assert len(search) == len({item[0] for item in search})
assert {'MASK:GROUP', 'MASK:GRAPH', 'MESH:NORMAL_DIRECTION', 'TEMPLATE:SHARED_DETAIL'} <= {item[0] for item in search}
print('GRAPH_TEMPLATE additions PASS', flush=True)

# Graph operators use the pinned graph owner even when another set is active,
# and target a just-clicked node without waiting for the lifecycle tick.
owner_id, owner_tree = ts.id, tree
other = sets.create(scene, 'Other active set', 512)
other_id = other.id
area = next(area for area in bpy.context.screen.areas if area.type == 'VIEW_3D')
area.ui_type = TREE_ID
area.spaces.active.node_tree = owner_tree
area.spaces.active.pin = True
with bpy.context.temp_override(area=area):
    with mirror.syncing():
        for node in owner_tree.nodes:
            node.select = getattr(node, 'op_id', '') == layer_id
        owner_tree.nodes.active = mirror.nodes_by_id(owner_tree)[layer_id]
    assert bpy.ops.open_painter.graph_add(kind='FILTER', choice='GAMMA', channel='ROUGHNESS') == {'FINISHED'}
    assert owner_tree.nodes.active.op_kind == 'EFFECT'
    assert bpy.ops.open_painter.graph_template(template='SHARED_DETAIL') == {'FINISHED'}
assert not find(other_id).layers
ts = find(owner_id)
root_id = stack.active(ts).id
assert stack.active(ts).name == 'Shared Surface Detail'
assert any(prop.anchor for layer in ts.layers for prop in layer.channels)

# Real memfile undo restores a whole template and its mirrored tree as one state.
bpy.ops.ed.undo_push(message='before template')
with bpy.context.temp_override(area=area):
    bpy.ops.open_painter.graph_template(template='LAYERED_GRUNGE')
added_id = stack.active(find(owner_id)).id
bpy.ops.ed.undo_push(message='template added')
bpy.ops.ed.undo()
ts = find(owner_id)
assert added_id not in {layer.id for layer in ts.layers}
assert root_id in {layer.id for layer in ts.layers}
assert added_id not in mirror.nodes_by_id(mirror.find_tree(ts))
print('GRAPH_TEMPLATE pinned ownership and undo PASS', flush=True)

# Save/reopen preserves graph IDs, nested frames, shader datablocks and anchor links.
path = ROOT / '.blender-test' / 'graph-templates.blend'
expected = {ts.id: mirror.signature(ts) for ts in bpy.context.scene.open_painter.texture_sets}
shader_signatures = {tree.name: len(tree.links) for tree in bpy.data.node_groups if tree.bl_idname == 'ShaderNodeTree'}
bpy.ops.wm.save_as_mainfile(filepath=str(path))
bpy.ops.wm.open_mainfile(filepath=str(path))
for ts_id, signature in expected.items():
    ts = find(ts_id)
    assert mirror.signature(ts) == signature
    tree = mirror.ensure_tree(ts)
    assert edits.link_keys(tree) == set(mirror.project(ts)[2])
for name, count in shader_signatures.items():
    assert len(bpy.data.node_groups[name].links) == count
assert anchors.index(find(owner_id))
print('GRAPH_TEMPLATE persistence PASS', flush=True)
open_painter.unregister()
open_painter.register()
open_painter.unregister()
print('GRAPH_TEMPLATES_PASS', flush=True)
