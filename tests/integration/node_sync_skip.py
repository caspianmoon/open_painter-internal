"""The texture set node's sync skips work only when nothing it reads changed.

Every evaluation syncs the set's internal tree, so a drag synced on every tick. ``sync`` now
skips a sync whose inputs and tree match the last full one (for at most ``SYNC_RECHECK``
seconds). Each change must still reach the tree on the next sync: a replaced output image, a
display's proxy (replaced by the set's image), height distance, AO in the preview, a removed
node, and, once ``SYNC_RECHECK`` has passed, an edit the key cannot see.
"""
from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import layer_stack as stack, material_manager, texture_set_manager as sets
from open_painter.core import texture_set_node as node_sync
from open_painter.evaluation import cache

open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.context.scene
bpy.ops.mesh.primitive_plane_add(size=2)
ts = sets.create(scene, 'Node sync', 512)
sets.assign(scene, ts, bpy.context.object)
stack.add(ts, 'FILL', 'Base')
material_manager.repair(ts)

calls = []
full_sync = node_sync.sync_tree


def counting(*args, **kwargs):
    calls.append(args[0].name)
    return full_sync(*args, **kwargs)


node_sync.sync_tree = counting


def sync():
    """Sync as an evaluation does; how many full syncs it took."""
    calls.clear()
    node_sync.sync(ts)
    return len(calls)


def role(name):
    tree = bpy.data.node_groups.get(node_sync.tree_name(ts))
    return next((node for node in tree.nodes if node.get('role') == name), None)


def output(channel):
    return next(item for item in ts.outputs if item.channel == channel)


sync()
assert sync() == 0, 'unchanged inputs and tree: the sync is skipped'
assert sync() == 0

# A display's proxy: the next sync shows the set's own image again, as before.
proxy = bpy.data.images.new('Node sync proxy', 4, 4)
assert node_sync.show_image(ts, 'BASE_COLOR', proxy)
assert sync() == 1 and node_sync.channel_nodes(ts)['BASE_COLOR'].image == output('BASE_COLOR').image

# A replaced output image reaches its sampler.
replacement = bpy.data.images.new('Node sync replacement', 4, 4)
original = output('ROUGHNESS').image
output('ROUGHNESS').image = replacement
assert sync() == 1 and node_sync.channel_nodes(ts)['ROUGHNESS'].image == replacement
output('ROUGHNESS').image = original
assert sync() == 1 and node_sync.channel_nodes(ts)['ROUGHNESS'].image == original

# Height distance and AO in the preview.
ts.height_distance = 0.123
sync()
assert abs(role('Height').inputs['Distance'].default_value - 0.123) < 1e-6
ts.preview_ao = not ts.preview_ao
sync()
assert role('AO Preview').inputs[0].default_value == node_sync.ao_factor(ts)

# A removed node is created again.
tree = bpy.data.node_groups.get(node_sync.tree_name(ts))
tree.nodes.remove(role('Normal Map'))
assert sync() == 1 and role('Normal Map') is not None and role('Normal Map').outputs['Normal'].links

# A channel sampler removed: the next sync is full and creates it again, without reading the
# removed node (a Python reference to a removed node reads freed memory). Repeated, as an
# operator that rebuilds the tree would.
for _ in range(8):
    tree.nodes.remove(node_sync.channel_nodes(ts)['ROUGHNESS'])
    assert sync() == 1 and node_sync.channel_nodes(ts)['ROUGHNESS'].image == output('ROUGHNESS').image
    assert sync() == 0

# An edit the key cannot see waits for the recheck, then is repaired.
node_sync.channel_nodes(ts)['METALLIC'].interpolation = 'Closest'
assert sync() == 0
recheck, node_sync.SYNC_RECHECK = node_sync.SYNC_RECHECK, 0.0
try:
    assert sync() == 1 and node_sync.channel_nodes(ts)['METALLIC'].interpolation == 'Linear'
finally:
    node_sync.SYNC_RECHECK = recheck
assert sync() == 0

# Another sync of the tree (a display's overrides): the samplers' images differ from the last
# full sync's, so the next one is full and shows the set's images again.
node_sync.tree_for(ts, {'BASE_COLOR': proxy})
assert sync() == 1 and node_sync.channel_nodes(ts)['BASE_COLOR'].image == output('BASE_COLOR').image
print('NODE_SYNC_SKIP_PASS', flush=True)
node_sync.sync_tree = full_sync
open_painter.unregister()
