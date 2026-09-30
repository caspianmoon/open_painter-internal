"""Whether a set reads its geometry, and its geometry key, are memoised per structure.

The scheduler asks on every tick. A numeric drag (opacity, values, effect strength) keeps
both answers without walking the stack; every edit that can change them is still seen.
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


def drag(check):
    """Numeric edits of a layer, a channel, an effect and a mask, as a drag's ticks."""
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
mask.enabled = False
assert not uses()
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
key = dependencies.geometry_key(scene, ts)
layer(2).decal_depth = .5
assert dependencies.geometry_key(scene, ts) != key, 'a decal depth edit kept the geometry key'
key = dependencies.geometry_key(scene, ts)
layer(3).opacity = .7
assert dependencies.geometry_key(scene, ts) == key
layer(2).decal_object = None
layer(2).decal_object_name = ''
stack.invalidate(ts)
assert not uses()

# Adding a layer, and undo or load, start over.
before = walks[0]
stack.add(ts, 'PAINT', 'Paint')
assert not uses() and walks[0] == before + 1, 'adding a layer keeps its memo'
dependencies.clear()
before = walks[0]
assert not uses() and walks[0] == before + 1, 'undo or load keeps its memo'
print('GEOMETRY_USES_MEMO_OK', flush=True)
