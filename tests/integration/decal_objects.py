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


graph = Graph(scene, SampledSet(ts), ts.material)
graph.build()
while graph.pending_resources:
    graph.step_resources(.1)
assert any(node.bl_idname == 'ShaderNodeTexCoord' and node.object == empty for node in graph.nodes())
for channel in ('BASE_COLOR', 'NORMAL'):
    actual, expected = bake_channel(graph, channel), cpu_channel(channel)
    coverage = raster(ts, layer_of())[..., 3]
    small = coverage.reshape(N, n // N, N, n // N).mean(axis=(1, 3))
    edge = np.zeros((N, N), bool)
    for axis in (0, 1):
        for step in (1, -1):
            edge |= np.abs(np.roll(small, step, axis=axis) - small) > .01
    error = float(np.abs(actual - expected)[~edge].max())
    assert error < 2e-3, (channel, error)
    print('DECAL_PARITY', channel, error, flush=True)
graph.close()
material_manager.repair(ts)

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
empty.rotation_euler = Euler((0., 0., 0.))
bpy.context.view_layer.update()
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
print('DECAL_DUPLICATE_OK', flush=True)

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
