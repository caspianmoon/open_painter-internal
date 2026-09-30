"""Decal objects (0.9.6): an Empty is the decal; moving, rotating, scaling and duplicating it edits the layer."""
import math
import sys
from pathlib import Path
from time import monotonic

import bpy
import numpy as np
from mathutils import Euler, Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import image_manager as images, layer_stack as stack, material_manager
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache, channel_eval, dependencies, shader_preview
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.raster_cache import for_texture_set
from open_painter.evaluation.shader_graph import Graph
from open_painter.evaluation.snapshots import capture
from open_painter.painting import decals

N = 32
n = 512


def ready(scene, ts):
    deadline = monotonic() + 60
    while monotonic() < deadline:
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert not info['error'], info
        if shader_preview.current(scene, ts):
            return info
    raise AssertionError(shader_preview.diagnostics(scene, ts))


def raster(ts, layer, channel='BASE_COLOR'):
    prop = stack.channel_for(capture(layer), channel)
    return channel_eval.source(prop, 'FILL', n, 0, n, {}, capture(ts), for_texture_set(scene, ts))


def centroid(alpha):
    ys, xs = np.nonzero(alpha > .5)
    return (xs.mean() + .5) / n, (ys.mean() + .5) / n


open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_plane_add(size=2)
plane = bpy.context.object
plane.name = 'Panel'
ts = sets.create(scene, 'Decals', 512)
sets.assign(scene, ts, plane)
base = stack.add(ts, 'FILL', 'Base')
stack.channel_for(base, 'BASE_COLOR').color = (.2, .2, .2, 1.)

# A 64 px decal: red on the left half, blue on the right; a constant tilted normal.
color = images.create('Caution', 64, color_data=True)
data = np.ones((64, 64, 4), np.float32)
data[:, :32, :3] = (1., 0., 0.)
data[:, 32:, :3] = (0., 0., 1.)
images.write(color, data, pack=False)
normal = images.create('Caution normal', 64)
data = np.ones((64, 64, 4), np.float32)
data[..., :3] = (.75, .5, .933)     # tilted toward +X in the decal frame, unit length
images.write(normal, data, pack=False)

scene.cursor.location = (0., 0., .1)
with bpy.context.temp_override(scene=scene):
    assert bpy.ops.open_painter.decal(action='ADD', name='Caution_01', image='Caution', size=.5) == {'FINISHED'}
layer = ts.layers[ts.active_layer]
layer_id = layer.id
empty = decals.resolve(layer)
assert empty is not None and empty.name == 'OP_Decal_Caution_01' and empty.empty_display_type == 'IMAGE' and empty.data == color
assert empty.get(decals.MARKER) == layer_id and all(p.projection == 'DECAL' for p in layer.channels)
layer_of = lambda: next(l for l in ts.layers if l.id == layer_id)
prop = stack.channel_for(layer_of(), 'NORMAL')
prop.image, prop.enabled = normal, True

# Footprint: a 0.5 m decal at the origin covers the middle quarter of the 2 m plane.
rgba = raster(ts, layer_of())
alpha = rgba[..., 3]
assert .05 < alpha.mean() < .08, alpha.mean()          # (0.5/2)^2 = 6.25 %
cu, cv = centroid(alpha)
assert abs(cu - .5) < .01 and abs(cv - .5) < .01, (cu, cv)
covered = alpha > .5
red = covered & (rgba[..., 0] > .5)
blue = covered & (rgba[..., 2] > .5)
assert red.sum() > 0 and blue.sum() > 0
assert centroid(red)[0] < centroid(blue)[0] and abs(centroid(red)[1] - centroid(blue)[1]) < .01, 'red left, blue right'
print('DECAL_FOOTPRINT_OK', flush=True)

# Move: the texture moves. Rotate: it rotates. Scale: it scales.
empty.location.x = .25
bpy.context.view_layer.update()
alpha = raster(ts, layer_of())[..., 3]
assert abs(centroid(alpha)[0] - .625) < .01, centroid(alpha)
empty.location.x = 0.
empty.rotation_euler = Euler((0., 0., math.radians(90)))
bpy.context.view_layer.update()
rgba = raster(ts, layer_of())
covered = rgba[..., 3] > .5
red, blue = covered & (rgba[..., 0] > .5), covered & (rgba[..., 2] > .5)
assert abs(centroid(red)[0] - centroid(blue)[0]) < .01 and centroid(red)[1] != centroid(blue)[1], 'the split turned with the decal'
empty.rotation_euler = Euler((0., 0., 0.))
empty.scale = (2., 2., 2.)
bpy.context.view_layer.update()
big = raster(ts, layer_of())[..., 3]
assert abs(big.mean() / alpha.mean() - 4) < .15, big.mean() / alpha.mean()
empty.scale = (1., 1., 1.)
bpy.context.view_layer.update()
print('DECAL_TRANSFORM_OK', flush=True)

# The tangent frame is memoised with the geometry rasters: a resolution change or a moved
# target rebuilds it, instead of the old frame breaking (shape) or skewing every export.
from open_painter.painting import projection
before = raster(ts, layer_of(), 'NORMAL')
assert 'tangents' in for_texture_set(scene, ts), 'the frame is cached after a NORMAL evaluation'
ts.resolution = '1024'
prop = stack.channel_for(capture(layer_of()), 'NORMAL')
grown = channel_eval.source(prop, 'FILL', 1024, 0, 1024, {}, capture(ts), for_texture_set(scene, ts))
assert grown.shape[:2] == (1024, 1024), 'the 512 frame was served at 1024'
ts.resolution = str(n)
plane.rotation_euler = Euler((0., 0., math.radians(90)))
bpy.context.view_layer.update()
turned = raster(ts, layer_of(), 'NORMAL')
from open_painter.evaluation.raster_cache import clear_shared
clear_shared()
np.testing.assert_allclose(turned, raster(ts, layer_of(), 'NORMAL'), atol=1e-6, err_msg='a moved target kept the old frame')
tilt = turned[turned[..., 3] > .5][:, :3].mean(0)
assert abs(tilt[0] - .5) < .02 and abs(tilt[1] - .25) < .02, ('the tilt follows the turned tangent frame', tilt)
plane.rotation_euler = Euler((0., 0., 0.))
bpy.context.view_layer.update()
tilt = raster(ts, layer_of(), 'NORMAL')
tilt = tilt[tilt[..., 3] > .5][:, :3].mean(0)
assert abs(tilt[0] - .75) < .02 and abs(tilt[1] - .5) < .02, tilt
# An unassigned (duplicated) set gets the neutral frame geometry() gives it, not an error.
t, b = projection.tangents(capture(ts).replaced(assignments=()), 8)
assert t.shape == (8, 8, 3) and (t[..., 0] == 1).all() and (b[..., 1] == 1).all()
print('DECAL_FRAME_CACHE_OK', flush=True)

# Depth and facing.
empty.location.z = .5
bpy.context.view_layer.update()
layer_of().decal_depth = .25
assert raster(ts, layer_of())[..., 3].max() == 0, 'beyond the projection depth'
layer_of().decal_depth = 1.
assert raster(ts, layer_of())[..., 3].max() > .5
empty.rotation_euler = Euler((math.radians(180), 0., 0.))   # now looks up: the plane faces away
bpy.context.view_layer.update()
assert raster(ts, layer_of())[..., 3].max() == 0, 'front faces only'
layer_of().decal_facing = False
assert raster(ts, layer_of())[..., 3].max() > .5
layer_of().decal_facing = True
empty.rotation_euler = Euler((0., 0., 0.))
empty.location.z = .1
layer_of().decal_depth = .25
layer_of().decal_falloff = .25
bpy.context.view_layer.update()
soft = raster(ts, layer_of())[..., 3]
assert ((soft > .05) & (soft < .95)).sum() > 100 and soft.max() > .95, 'soft border'
layer_of().decal_falloff = 0.
print('DECAL_DEPTH_FACING_OK', flush=True)

# Normals: the decal's tilt lands in the target's tangent frame; rotating the decal rotates it.
rgba = raster(ts, layer_of(), 'NORMAL')
inside = rgba[..., 3] > .5
tilt = rgba[inside][:, :3].mean(0)
assert abs(tilt[0] - .75) < .02 and abs(tilt[1] - .5) < .02 and abs(tilt[2] - .933) < .02, tilt
empty.rotation_euler = Euler((0., 0., math.radians(90)))
bpy.context.view_layer.update()
rgba = raster(ts, layer_of(), 'NORMAL')
tilt = rgba[rgba[..., 3] > .5][:, :3].mean(0)
assert abs(tilt[0] - .5) < .02 and abs(tilt[1] - .75) < .02, tilt
empty.rotation_euler = Euler((0., 0., 0.))
bpy.context.view_layer.update()
print('DECAL_NORMAL_OK', flush=True)

# Shader: the same projection natively; parity off the decal's edge band.
ts.preview_backend = 'CPU'
shader_preview.clear()
scene.render.engine = 'CYCLES'; scene.cycles.device = 'CPU'; scene.cycles.samples = 1
scene.render.bake.margin = 0; scene.render.bake.use_clear = True
target = bpy.data.images.new('Decal readback', width=N, height=N, float_buffer=True)
target.colorspace_settings.name = 'Non-Color'


class SampledSet:
    def __init__(self, original):
        self.original = original
        self.resolution = str(N)

    def __getattr__(self, name):
        return getattr(self.original, name)


def bake_channel(graph, channel):
    plane.select_set(True); bpy.context.view_layer.objects.active = plane
    tree = ts.material.node_tree
    output = next(node for node in tree.nodes if node.bl_idname == 'ShaderNodeOutputMaterial')
    emission = tree.nodes.new('ShaderNodeEmission')
    bake_node = tree.nodes.new('ShaderNodeTexImage')
    bake_node.image = target
    tree.nodes.active = bake_node
    tree.links.new(emission.outputs[0], output.inputs['Surface'])
    tree.links.new(graph.outputs[channel], emission.inputs['Color'])
    bpy.ops.object.bake(type='EMIT')
    result = images.pixels(target)[..., :3].copy()
    tree.nodes.remove(bake_node); tree.nodes.remove(emission)
    return result


def cpu_channel(channel):
    steps = LayerEvaluator(scene).channel_steps(ts.id, channel, preview_resolution=N)
    try:
        while True:
            next(steps)
    except StopIteration as done:
        return images.pixels(done.value)[..., :3].copy()


# An enabled decal channel without an image projects nothing: the base shows
# through everywhere (not the layer's flat value over the whole mesh).
stack.channel_for(next(l for l in ts.layers if l.name == 'Base'), 'ROUGHNESS').value = .9
stack.channel_for(layer_of(), 'ROUGHNESS').enabled = True


def decal_parity(label, channels=('BASE_COLOR', 'NORMAL', 'ROUGHNESS')):
    graph = Graph(scene, SampledSet(ts), ts.material)
    graph.build()
    while graph.pending_resources:
        graph.step_resources(.1)
    assert any(node.bl_idname == 'ShaderNodeTexCoord' and node.object == empty for node in graph.nodes())
    for channel in channels:
        actual, expected = bake_channel(graph, channel), cpu_channel(channel)
        coverage = raster(ts, layer_of())[..., 3]
        small = coverage.reshape(N, n // N, N, n // N).mean(axis=(1, 3))
        edge = np.zeros((N, N), bool)
        for axis in (0, 1):
            for step in (1, -1):
                edge |= np.abs(np.roll(small, step, axis=axis) - small) > .01
        error = float(np.abs(actual - expected)[~edge].max())
        assert error < 2e-3, (label, channel, error)
        if channel == 'ROUGHNESS':
            assert abs(expected[0, 0, 0] - .9) < 1e-3 and abs(actual[0, 0, 0] - .9) < 2e-3, (expected[0, 0], actual[0, 0])
        print('DECAL_PARITY', label, channel, error, flush=True)
    graph.close()
    material_manager.repair(ts)


decal_parity('plane')
# A 2:1 label: the Empty draws it 0.5 wide and 0.25 tall, so the layer projects that
# rectangle (not a stretched square); the shader divides by the same per-axis extents.
label = bpy.data.images.new('Label', width=128, height=64, alpha=True, float_buffer=True)
label.colorspace_settings.name = 'Linear Rec.709'
data = np.ones((64, 128, 4), np.float32)
data[:, :64, :3] = (1., 0., 0.)
data[:, 64:, :3] = (0., 0., 1.)
images.write(label, data, pack=False)
base_prop = stack.channel_for(layer_of(), 'BASE_COLOR')
base_prop.image = label
dependencies._candidates.add(scene.as_pointer())
dependencies.refresh(scene)   # the Empty shows the new image before the frame is keyed
assert empty.data == label and decals.aspect(empty) == (1., .5) and decals.signature(layer_of())[7] == (1., .5)
rgba = raster(ts, layer_of())
alpha = rgba[..., 3]
ys, xs = np.nonzero(alpha > .5)
assert abs((xs.max() - xs.min() + 1) / n - .25) < .01 and abs((ys.max() - ys.min() + 1) / n - .125) < .01, \
    ((xs.max() - xs.min() + 1) / n, (ys.max() - ys.min() + 1) / n)
assert abs(alpha.mean() - .03125) < .004, alpha.mean()     # 0.5 x 0.25 of a 2 m plane
covered = alpha > .5
red, blue = covered & (rgba[..., 0] > .5), covered & (rgba[..., 2] > .5)
assert abs(red.sum() - blue.sum()) < .05 * covered.sum(), 'the label is not stretched: each half keeps its width'
decal_parity('label', channels=('BASE_COLOR',))
base_prop.image = color
dependencies._candidates.add(scene.as_pointer())
dependencies.refresh(scene)
assert empty.data == color and decals.aspect(empty) == (1., 1.)
print('DECAL_ASPECT_OK', flush=True)
# Mirrored UVs (a game asset's symmetric half): the shader's bitangent carries the
# UV handedness, like painting.projection.tangents, so decal relief agrees on export.
uv_layer = plane.data.uv_layers.active.data
for loop in uv_layer:
    loop.uv = (loop.uv.x, 1. - loop.uv.y)
plane.data.update()
dependencies._mesh_epochs[plane.data.as_pointer()] = dependencies.mesh_epoch(plane.data) + 1
from open_painter.evaluation.raster_cache import clear_shared
clear_shared()   # tangent frames are memoised with the geometry
empty.rotation_euler = Euler((0., 0., math.radians(90)))   # the tilt now lies along the decal's Y
bpy.context.view_layer.update()
decals.sync(ts)
tilt = raster(ts, layer_of(), 'NORMAL')
tilt = tilt[tilt[..., 3] > .5][:, :3].mean(0)
assert abs(tilt[1] - .25) < .02, ('the CPU flips V on a mirrored island', tilt)
decal_parity('mirrored_island', channels=('NORMAL',))
for loop in uv_layer:
    loop.uv = (loop.uv.x, 1. - loop.uv.y)
plane.data.update()
dependencies._mesh_epochs[plane.data.as_pointer()] = dependencies.mesh_epoch(plane.data) + 1
clear_shared()
empty.rotation_euler = Euler((0., 0., 0.))
bpy.context.view_layer.update()
decals.sync(ts)
stack.channel_for(layer_of(), 'ROUGHNESS').enabled = False

# Live session: moving the Empty syncs the frame uniforms and invalidates the set.
ts.preview_backend = 'LIVE'
material_manager.repair(ts)
ready(scene, ts)
assert dependencies.uses_geometry(ts)
serial = ts.edit_serial
empty.rotation_euler = Euler((0., 0., math.radians(45)))
bpy.context.view_layer.update()
dependencies._candidates.add(scene.as_pointer())
dependencies.refresh(scene)
assert ts.edit_serial != serial
axes = np.asarray(layer_of().decal_axes).reshape(3, 3)
assert abs(axes[0, 0] - math.cos(math.radians(45))) < 1e-4 and abs(axes[0, 1] - math.sin(math.radians(45))) < 1e-4
ready(scene, ts)


def revisions(info):
    """Structural revisions of the session: a rebuild, or a patch of its own graph (EngineGraph.patch)."""
    return info['graph_builds'] + info['graph_patches']


builds = revisions(shader_preview.diagnostics(scene, ts))
empty.rotation_euler = Euler((0., 0., 0.))
bpy.context.view_layer.update()
dependencies._candidates.add(scene.as_pointer())
dependencies.refresh(scene)
ready(scene, ts)
assert revisions(shader_preview.diagnostics(scene, ts)) == builds, 'a drag is a numeric update, not a rebuild'
# The display size is a compiled constant: changing it restructures the session.
empty.empty_display_size = 2.
bpy.context.view_layer.update()
dependencies._candidates.add(scene.as_pointer())
dependencies.refresh(scene)
ready(scene, ts)
assert revisions(shader_preview.diagnostics(scene, ts)) > builds or shader_preview.diagnostics(scene, ts)['rebuild_pending']
empty.empty_display_size = 1.
bpy.context.view_layer.update()
dependencies._candidates.add(scene.as_pointer())
dependencies.refresh(scene)
ready(scene, ts)
# Choosing another Empty mirrors its name (the re-link key), and clearing it clears the name.
# The Empty the layer leaves gives up the marker: the scheduler's refresh must not take it
# for a Shift+D copy and duplicate the layer from the timer.
other_empty = decals.create_object(bpy.context, 'OP_Decal_Other')
layers_before = len(ts.layers)
layer_of().decal_object = other_empty
assert layer_of().decal_object_name == 'OP_Decal_Other'
assert empty.get(decals.MARKER) is None and other_empty.get(decals.MARKER) is None
assert decals.adopt_duplicates(scene) == [] and len(ts.layers) == layers_before, 'no phantom layer from the old Empty'
layer_of().decal_object = None
assert layer_of().decal_object_name == ''
layer_of().decal_object = empty
assert layer_of().decal_object_name == empty.name
dependencies._candidates.add(scene.as_pointer())
dependencies.refresh(scene)
assert len(ts.layers) == layers_before
empty[decals.MARKER] = layer_id   # left and re-picked by hand: the rest of the suite treats it as the layer's own again
bpy.data.objects.remove(other_empty)
ready(scene, ts)
print('DECAL_LIVE_OK', flush=True)

# Duplicate: Shift+D on the Empty becomes its own layer; the operator does the same.
copy = empty.copy()
copy.name = 'OP_Decal_Caution_02'
copy.location.x = -.5
scene.collection.objects.link(copy)
created = decals.adopt_duplicates(scene)
assert len(created) == 1
twin = next(l for l in ts.layers if l.id == created[0])
assert twin.decal_object == copy and copy.get(decals.MARKER) == twin.id and twin.id != layer_id
assert stack.channel_for(twin, 'BASE_COLOR').image == color and twin.name == 'Caution_02'
bpy.context.view_layer.update()
assert abs(centroid(raster(ts, twin)[..., 3])[0] - .25) < .01
assert abs(centroid(raster(ts, layer_of())[..., 3])[0] - .5) < .01, 'the original stays where it was'
ts.active_layer = stack.index_of(ts, layer_id)
with bpy.context.temp_override(scene=scene):
    assert bpy.ops.open_painter.decal(action='DUPLICATE') == {'FINISHED'}
assert len(decals.layers_of(ts)) == 3
# Deleting a decal layer removes the Empty it owns; a shared Empty and a hand-picked one stay.
third_id = ts.layers[ts.active_layer].id
third_empty = decals.resolve(ts.layers[ts.active_layer])
empty_name = third_empty.name
assert third_empty.get(decals.MARKER) == third_id
sharing_id = stack.duplicate(ts, stack.index_of(ts, third_id)).id   # a layer copy shares the Empty
assert next(l for l in ts.layers if l.id == sharing_id).decal_object == third_empty
stack.remove(ts, stack.index_of(ts, sharing_id))
assert empty_name in bpy.data.objects, 'still driven by the other layer'
stack.remove(ts, stack.index_of(ts, third_id))
assert empty_name not in bpy.data.objects, 'the layer\'s own Empty goes with it'
# The other order: the copy inherits the marker when its original goes, so the Empty
# leaves with the last layer projecting from it instead of lingering in the outliner.
ts.active_layer = stack.index_of(ts, layer_id)
with bpy.context.temp_override(scene=scene):
    assert bpy.ops.open_painter.decal(action='DUPLICATE') == {'FINISHED'}
fourth_id = ts.layers[ts.active_layer].id
fourth_empty = decals.resolve(ts.layers[ts.active_layer])
empty_name = fourth_empty.name
assert fourth_empty.get(decals.MARKER) == fourth_id
sharing_id = stack.duplicate(ts, stack.index_of(ts, fourth_id)).id
stack.remove(ts, stack.index_of(ts, fourth_id))
assert empty_name in bpy.data.objects and bpy.data.objects[empty_name].get(decals.MARKER) == sharing_id
stack.remove(ts, stack.index_of(ts, sharing_id))
assert empty_name not in bpy.data.objects, 'the copy was the last layer projecting from the Empty'
hand_made = decals.create_object(bpy.context, 'OP_Decal_Handmade')
picked_id = stack.add(ts, 'FILL', 'Picked').id
next(l for l in ts.layers if l.id == picked_id).decal_object = hand_made
stack.remove(ts, stack.index_of(ts, picked_id))
assert hand_made.name in bpy.data.objects, 'an Empty the artist picked is theirs'
bpy.data.objects.remove(hand_made)
assert len(decals.layers_of(ts)) == 2
# Clearing the field on a layer that still owns its Empty (the X in the panel): the Empty
# gives up the marker, so the scheduler's refresh after the next viewport click finds no
# Shift+D copy to adopt, and the Empty stays in the scene as the artist's to re-pick.
with bpy.context.temp_override(scene=scene):
    assert bpy.ops.open_painter.decal(action='ADD', name='Detached', image='Caution', size=.5) == {'FINISHED'}
detached_id = ts.layers[ts.active_layer].id
detached_empty = decals.resolve(ts.layers[ts.active_layer])
layers_before = len(ts.layers)
next(l for l in ts.layers if l.id == detached_id).decal_object = None
for _ in range(2):
    dependencies._candidates.add(scene.as_pointer())
    dependencies.refresh(scene)
assert len(ts.layers) == layers_before and [l.name for l in ts.layers].count('Detached') == 1, [l.name for l in ts.layers]
assert detached_empty.get(decals.MARKER) is None and detached_empty.name in bpy.data.objects
stack.remove(ts, stack.index_of(ts, detached_id))
assert detached_empty.name in bpy.data.objects, 'no longer the layer\'s own: it stays like a hand-picked one'
bpy.data.objects.remove(detached_empty)
assert len(decals.layers_of(ts)) == 2
print('DECAL_DUPLICATE_OK', flush=True)

# Delete Global (or the outliner, or a script) frees the Empty and nulls the pointer without
# the update callback. The layer must not adopt the next Empty that takes the name: Add
# Decal with the default name would otherwise revive the discarded layer on the new frame.
with bpy.context.temp_override(scene=scene):
    assert bpy.ops.open_painter.decal(action='ADD', name='Gone', image='Caution', size=.5) == {'FINISHED'}
gone_id = ts.layers[ts.active_layer].id
gone_of = lambda: next(l for l in ts.layers if l.id == gone_id)
bpy.data.objects.remove(decals.resolve(gone_of()))
assert gone_of().decal_object is None and gone_of().decal_object_name == 'OP_Decal_Gone'
with bpy.context.temp_override(scene=scene):
    assert bpy.ops.open_painter.decal(action='ADD', name='Gone', image='Caution', size=.5) == {'FINISHED'}
fresh_id = ts.layers[ts.active_layer].id
fresh_empty = decals.resolve(next(l for l in ts.layers if l.id == fresh_id))
assert fresh_empty.name == 'OP_Decal_Gone', fresh_empty.name
assert gone_of().decal_object is None and gone_of().decal_object_name == '' and decals.resolve(gone_of()) is None
assert fresh_empty.get(decals.MARKER) == fresh_id
stack.remove(ts, stack.index_of(ts, fresh_id))
assert 'OP_Decal_Gone' not in bpy.data.objects, 'only the new layer projected from it'
stack.remove(ts, stack.index_of(ts, gone_id))
# The same through the scheduler's sync when an unrelated object takes the name.
with bpy.context.temp_override(scene=scene):
    assert bpy.ops.open_painter.decal(action='ADD', name='Gone', image='Caution', size=.5) == {'FINISHED'}
gone_id = ts.layers[ts.active_layer].id
bpy.data.objects.remove(decals.resolve(gone_of()))
stranger = decals.create_object(bpy.context, 'OP_Decal_Gone')
assert decals.resolve(gone_of()) is None, 'the freed pointer never falls back to a namesake'
decals.sync(ts)
assert gone_of().decal_object is None and gone_of().decal_object_name == ''
bpy.data.objects.remove(stranger)
stack.remove(ts, stack.index_of(ts, gone_id))
assert len(decals.layers_of(ts)) == 2
print('DECAL_FREED_EMPTY_OK', flush=True)

# Portable materials keep the Empty's name and re-link on import.
from open_painter.smart_materials.serializer import save
from open_painter.smart_materials.loader import apply as load
archive = ROOT / '.blender-test' / 'decal-material.zip'
if archive.exists():
    archive.unlink()
save(ts, archive)
other = sets.create(scene, 'Other', 512)
bpy.ops.mesh.primitive_plane_add(size=2, location=(0, 5, 0))
sets.assign(scene, other, bpy.context.object)
load(other, archive)
imported = next(l for l in other.layers if l.decal_object_name == empty.name)
assert imported.decal_object == empty, 'the imported layer follows the same decal object'
print('DECAL_OBJECTS_PASS', flush=True)
open_painter.unregister()
