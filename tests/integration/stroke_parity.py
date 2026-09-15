"""Vector strokes in the shader-native preview: the stroke boundary under a paint layer's
content and a Vector Strokes mask agree with the CPU reference; stroke edits re-identify
the boundary without rebuilding; undo rehydrates."""
import sys
from pathlib import Path
from time import monotonic
from types import SimpleNamespace

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import image_manager as images, layer_stack as stack, material_manager
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache, shader_preview
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.shader_graph import Graph
from open_painter.painting import strokes as records
from open_painter.utils.ids import new_id

N = 32
open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.context.scene
project = scene.open_painter
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_plane_add(size=2)
plane = bpy.context.object
ts = sets.create(scene, 'Stroke parity', 512); sets.assign(scene, ts, plane)
ts.preview_backend = 'CPU'
base = stack.add(ts, 'FILL', 'Base')
stack.channel_for(base, 'BASE_COLOR').color = (.2, .5, .7, 1.)
stack.channel_for(base, 'ROUGHNESS').value = .3
paint = stack.add(ts, 'PAINT', 'Strokes')
# A painted image under the strokes proves the composite order.
stack.channel_for(paint, 'ROUGHNESS').enabled = True
stack.channel_for(paint, 'ROUGHNESS').image = images.create('Under', 512, (.9, .9, .9, .35))
masked = stack.add(ts, 'FILL', 'Masked')
stack.channel_for(masked, 'BASE_COLOR').color = (.9, .1, .1, 1.)
stack.channel_for(masked, 'ROUGHNESS').value = .8
mask = masked.masks.add(); mask.id, mask.source = new_id(), 'STROKES'
bpy.context.view_layer.update()
scene.render.engine = 'CYCLES'; scene.cycles.device = 'CPU'; scene.cycles.samples = 1
scene.render.bake.margin = 0; scene.render.bake.use_clear = True
target = bpy.data.images.new('Stroke readback', width=N, height=N, float_buffer=True)
target.colorspace_settings.name = 'Non-Color'


class SampledSet:
    def __init__(self, original):
        self.original = original
        self.resolution = str(N)

    def __getattr__(self, name):
        return getattr(self.original, name)


def settings(**overrides):
    values = dict(radius=.35, hardness=.6, opacity=.9, flow=1., accumulate='MAX', alpha_image=None, projection='SURFACE',
                  blend='PAINT', base_color=(1, .2, 0, 1), roughness=.1, metallic=0., height=.5, ao=1., emission=(0, 0, 0, 1),
                  opacity_value=1., normal=(.5, .5, 1, 1), channels={'ROUGHNESS', 'BASE_COLOR'})
    values.update(overrides)
    return SimpleNamespace(**values)


def diagonal(count=12, radius=.3):
    t = np.linspace(-.7, .7, count, dtype=np.float32)
    return np.stack([t, t * .6, np.zeros(count, np.float32), np.zeros(count, np.float32), np.zeros(count, np.float32),
                     np.ones(count, np.float32), np.linspace(.4, 1, count, dtype=np.float32), np.full(count, radius, np.float32),
                     np.zeros(count, np.float32), np.zeros(count, np.float32),
                     np.full(count, .86, np.float32), np.full(count, .5, np.float32), np.zeros(count, np.float32)], axis=1)


def cpu_channel(channel):
    steps = LayerEvaluator(scene).channel_steps(ts.id, channel, preview_resolution=N)
    try:
        while True:
            next(steps)
    except StopIteration as done:
        image = done.value
    return images.pixels(image)[..., :3].copy()


def parity(label):
    graph = Graph(scene, SampledSet(ts), ts.material)
    graph.build()
    while graph.pending_resources:
        graph.step_resources(.1)
    tree = ts.material.node_tree
    output = next(n for n in tree.nodes if n.bl_idname == 'ShaderNodeOutputMaterial')
    emission = tree.nodes.new('ShaderNodeEmission')
    node = tree.nodes.new('ShaderNodeTexImage'); node.image = target; tree.nodes.active = node
    tree.links.new(emission.outputs[0], output.inputs['Surface'])
    worst = 0.
    for channel in ('BASE_COLOR', 'ROUGHNESS'):
        tree.links.new(graph.outputs[channel], emission.inputs['Color'])
        bpy.ops.object.select_all(action='DESELECT'); plane.select_set(True); bpy.context.view_layer.objects.active = plane
        bpy.ops.object.bake(type='EMIT')
        actual = images.pixels(target)[..., :3]
        expected = cpu_channel(channel)
        error = float(np.abs(actual - expected).max())
        worst = max(worst, error)
        assert np.allclose(actual, expected, atol=3e-3), (label, channel, error)
        assert expected.std() > 1e-3, (label, channel)
    print('STROKE_PARITY', label, round(worst, 5), flush=True)
    tree.nodes.remove(node); tree.nodes.remove(emission)
    graph.close(); material_manager.repair(ts)
    return graph


stroke = records.record(project, ts, paint, diagonal(), settings())
mask.stroke_key = paint.stroke_key
parity('strokes_and_mask')
stroke.hardness = .95
parity('edited_hardness')
eraser = records.record(project, ts, paint, diagonal(count=4, radius=.2), settings(blend='ERASE'))
parity('erase')

# Runtime: a stroke edit re-identifies the boundary; no rebuild. Undo rehydrates.
ts.preview_backend = 'LIVE'
material_manager.repair(ts)


def ready():
    deadline = monotonic() + 60
    while monotonic() < deadline:
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert not info['error'], info
        if shader_preview.current(scene, ts):
            return info
    raise AssertionError(shader_preview.diagnostics(scene, ts))


info = ready()
graph = shader_preview.ensure(scene, ts)
builds = info['graph_builds']
assert any(node.get('op_boundary', '').startswith('["strokes"') for node in graph.nodes())
project.strokes[0].roughness = .7
info = shader_preview.diagnostics(scene, ts)
assert shader_preview.ensure(scene, ts) is graph and info['graph_builds'] == builds, info
assert info['pending_resources'] or info['raster_boundaries_queued'], info
ready()
bpy.ops.ed.undo_push(message='before')
project.strokes[0].roughness = .2
bpy.ops.ed.undo_push(message='edit')
bpy.ops.ed.undo()
scene = bpy.context.scene
ts = next(item for item in scene.open_painter.texture_sets if item.name == 'Stroke parity')
info = ready()
assert info['graph_rehydrations'] == 1 and info['graph_builds'] == 0, info
assert abs(scene.open_painter.strokes[0].roughness - .7) < 1e-6
print('STROKE_PARITY_PASS', flush=True)
open_painter.unregister()
