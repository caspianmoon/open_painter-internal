"""Answers only a structural edit changes are memoised per structure: whether a set reads
its geometry, its geometry key, where its readers sit.

The scheduler and every property callback ask. A numeric drag (opacity, values, effect
strength) keeps the answers without walking the stack; every edit that can change them is
still seen.
"""
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import layer_stack as stack
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache, dependencies

open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add(size=1)
cube = bpy.context.object
bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.uv.smart_project(island_margin=.02)
bpy.ops.object.mode_set(mode='OBJECT')
ts = sets.create(scene, 'Memo', 512)
sets.assign(scene, ts, cube)
ids = [stack.add(ts, 'FILL', f'Fill {index}').id for index in range(6)]


def layer(index):
    """Resolved afresh: adding a layer relocates the collection's structs."""
    return next(item for item in ts.layers if item.id == ids[index])

walks = [0]
_walk = dependencies.uses_placement


def counted(item):
    walks[0] += 1
    return _walk(item)


dependencies.uses_placement = counted


def uses():
    return dependencies.uses_geometry(ts)


def agree():
    """The per-structure reader index answers what a walk over the stack does."""
    from open_painter.painting import decals
    ids = lambda items: [item.as_pointer() for item in items]
    assert ids(dependencies.decal_layers(ts)) == ids(decals.layers_of(ts))
    assert ids(dependencies._live_readers(ts)) == ids(dependencies.live_fields(ts))
    assert ids(dependencies._mesh_readers(ts)) == ids(dependencies.mesh_masks(ts))


def drag(check):
    """Numeric edits of a layer, a channel, an effect and a mask, as a drag's ticks, and a
    brush input on a layer's channel (pixels change nothing structural)."""
    from open_painter.painting.channel_paint import mark_changed
    effect = stack.channel_for(layer(4), 'ROUGHNESS').effects
    effect = effect[0] if len(effect) else None
    mask = layer(5).masks[0] if len(layer(5).masks) else None
    before = walks[0]
    for step in range(5):
        layer(2).opacity = .2 + .1 * step
        stack.channel_for(layer(3), 'ROUGHNESS').value = .1 * step
        if effect is not None:
            effect.strength = .5 + .1 * step
        if mask is not None:
            mask.invert = bool(step % 2)
        mark_changed(ts, layer(1), 'BASE_COLOR')
        check()
    assert walks[0] == before, f'a numeric drag re-walked the stack {walks[0] - before} times'


# Nothing reads the geometry: a drag asks without walking.
assert not uses()
drag(lambda: None if not uses() else (_ for _ in ()).throw(AssertionError('a drag made the set read geometry')))
assert dependencies.geometry_key(scene, ts) is None

# Every structural input is seen.
channel = stack.channel_for(layer(1), 'BASE_COLOR')
channel.projection = 'TRIPLANAR'
assert uses(), 'a triplanar channel reads where the object stands'
channel.projection = 'UV'
assert not uses()

blur = stack.channel_for(layer(4), 'ROUGHNESS').effects.add()
blur.kind = 'GAUSSIAN_BLUR'
assert uses(), 'a blur follows the surface by default'
blur.space = 'UV'
assert not uses(), 'a UV-space blur reads no geometry'
blur.space = 'SURFACE'
assert uses(), 'a surface-space blur reads the seam map'
blur.space = 'UV'
assert not uses()

mask = layer(5).masks.add()
mask.source = 'MESH'
assert uses(), 'a mesh mask reads the scene structure'
agree()
mask.enabled = False
assert not uses()
agree()
mask.enabled = True
assert uses()

# The geometry key survives a numeric drag too, and the drag leaves it unchanged.
key = dependencies.geometry_key(scene, ts)
assert key is not None
drag(lambda: None if dependencies.geometry_key(scene, ts) == key
     else (_ for _ in ()).throw(AssertionError('a numeric drag re-keyed the geometry')))
mask.source = 'PAINT'
assert not uses()

# A Mask Graph's own nodes are read live: adding a Position input moves no serial of the set.
graph = bpy.data.node_groups.new('Memo mask graph', 'ShaderNodeTree')
graph.interface.new_socket(name='Coverage', in_out='OUTPUT', socket_type='NodeSocketFloat')
mask.source = 'GRAPH'
mask.node_group = graph
assert uses() and not dependencies.uses_placement(ts)
serial = ts.edit_serial
graph.interface.new_socket(name='Position', in_out='INPUT', socket_type='NodeSocketVector')
assert ts.edit_serial == serial and dependencies.uses_placement(ts), 'a graph reading Position reads placement'
mask.node_group = None
mask.source = 'PAINT'
assert not uses() and not dependencies.uses_placement(ts)

# A replay's edits skip their callbacks and move no serial until it ends: answered afresh.
assert not uses()
with stack.replay(ts):
    stack.channel_for(layer(1), 'BASE_COLOR').projection = 'TRIPLANAR'
    assert uses() and dependencies.uses_placement(ts), 'a replay hid its own triplanar edit'
assert uses()
with stack.replay(ts):
    stack.channel_for(layer(1), 'BASE_COLOR').projection = 'UV'
assert not uses()

# A units switch whose derived texture values stay put moves no edit serial: still seen.
serial = ts.edit_serial
flat = stack.channel_for(layer(0), 'METALLIC')
flat.units = 'PHYSICAL'
assert ts.edit_serial == serial, 'the fixture meant a units switch that moves no serial'
assert uses(), 'physical units follow the surface'
flat.units = 'TEXTURE'
assert not uses()

# The auto edge rebake toggle has no callback: it is read live.
ts.bake.auto_edge_rebake = True
assert uses()
ts.bake.auto_edge_rebake = False
assert not uses()

# A decal layer's numeric fields are its projection frame: they re-key the geometry.
bpy.ops.object.empty_add(type='IMAGE', location=(0, 0, 2))
empty = bpy.context.object
layer(2).decal_object = empty
assert uses(), 'a decal reads where the object stands'
agree()
key = dependencies.geometry_key(scene, ts)
layer(2).decal_depth = .5
assert dependencies.geometry_key(scene, ts) != key, 'a decal depth edit kept the geometry key'
key = dependencies.geometry_key(scene, ts)
layer(3).opacity = .7
assert dependencies.geometry_key(scene, ts) == key
# A renamed Empty reaches the name mirror; a deleted one is nulled without a callback.
empty.name = 'Memo decal renamed'
dependencies.refresh_names(ts)
assert layer(2).decal_object_name == 'Memo decal renamed'
bpy.data.objects.remove(empty)
assert layer(2).decal_object is None
agree()
layer(2).decal_object_name = ''
agree()
stack.invalidate(ts)
assert not uses()

# A live scene field is indexed like a walk finds it.
live = layer(4).masks.add()
live.source = 'SCENE'
live.influence.mode = 'LIVE'
assert uses() and dependencies.uses_placement(ts)
agree()
live.source = 'PAINT'
assert not uses()
agree()

# Property callbacks ask which channels read the edited layer; a stack with no reader answers
# without a walk, and a numeric drag keeps the answer.
from open_painter.evaluation import anchors, references
walks_before = [0]
_consumed = anchors.consumed


def consumed(item):
    walks_before[0] += 1
    return _consumed(item)


anchors.consumed = consumed
assert references.dependent_channels(ts, layer(1).id) == set()
reader = layer(3).masks.add()
reader.source = 'REFERENCE'
reader.reference_layer = layer(1).id
expected = {p.channel for p in layer(3).channels if p.enabled}
assert references.dependent_channels(ts, layer(1).id) == expected
before = walks_before[0]
for step in range(4):
    layer(1).opacity = .2 + .1 * step
    assert references.dependent_channels(ts, layer(1).id) == expected
assert walks_before[0] == before, 'a numeric drag re-walked the stack for readers'
reader.source = 'PAINT'
assert references.dependent_channels(ts, layer(1).id) == set()
anchors.consumed = _consumed

# The layer list filters on every redraw: per structure and folder expansion (no callback).
from types import SimpleNamespace
from open_painter.ui.native import OP_UL_native_layers


def list_agrees():
    flags, order = OP_UL_native_layers.filter_items(SimpleNamespace(bitflag_filter_item=1), bpy.context, ts, 'layers')
    rows = stack.visible_rows(ts)
    visible = {index for index, _ in rows}
    ordered = [index for index, _ in rows] + [index for index in range(len(ts.layers)) if index not in visible]
    assert flags == [1 if index in visible else 0 for index in range(len(ts.layers))], (flags, rows)
    assert order == [ordered.index(index) for index in range(len(ts.layers))], (order, ordered)


list_agrees()
layer(2).opacity = .45
list_agrees()
folder_id = stack.add(ts, 'FOLDER', 'Memo folder').id
stack.add(ts, 'FILL', 'Memo child', parent_id=folder_id)
list_agrees()
folder = lambda: next(item for item in ts.layers if item.id == folder_id)
serial = ts.edit_serial
folder().expanded = False
assert ts.edit_serial == serial, 'the fixture meant a collapse that moves no serial'
list_agrees()
folder().expanded = True
list_agrees()
stack.parent(ts, layer(1), folder_id)
list_agrees()
stack.remove(ts, next(index for index, item in enumerate(ts.layers) if item.name == 'Memo child'))
list_agrees()

# Adding a layer, and undo or load, start over.
before = walks[0]
stack.add(ts, 'PAINT', 'Paint')
assert not uses() and walks[0] == before + 1, 'adding a layer keeps its memo'
dependencies.clear()
before = walks[0]
assert not uses() and walks[0] == before + 1, 'undo or load keeps its memo'
print('GEOMETRY_USES_MEMO_OK', flush=True)
