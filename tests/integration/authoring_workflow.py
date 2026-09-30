"""Real gestures, GPU adoption/cancellation, text parity and deterministic replay."""
import json
import sys
import tempfile
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

import bpy
import gpu
import numpy as np
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.core import texture_set_manager as sets, layer_stack as stack, image_manager as images
from open_painter.evaluation import cache, shader_preview, metrics
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.engine import runtime
from open_painter.painting import gesture, stroke_raster, payload, brush_graph, particles

open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
gpu.init()
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_plane_add(size=2)
obj = bpy.context.object
ts = sets.create(scene, 'Gestures', 512)
sets.assign(scene, ts, obj)
layer = stack.add(ts, 'PAINT', 'Paint')
settings = scene.open_painter.vector_brush
settings.channels = {'BASE_COLOR', 'ROUGHNESS', 'HEIGHT'}
settings.radius, settings.spacing = .12, .5
settings.height, settings.roughness = .50013, .7
bpy.context.view_layer.update()

class View:
    def ray(self, xy):
        return Vector((xy[0], xy[1], 5)), Vector((0, 0, -1))
    def world_per_pixel(self, xy, point):
        return .01

def ready():
    deadline = perf_counter() + 90
    while perf_counter() < deadline:
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert not info.get('error'), info
        if shader_preview.current(scene, ts):
            return
    raise AssertionError('Engine timed out')

def stroke(start, stop, preview=True, cancel=False):
    g = gesture.Gesture(scene, ts, layer, settings, gesture.Scope.of(scene.open_painter, ts), View(), preview=preview)
    assert g.start(start, (.8, .1, -.1, 1), g.hit(start)), g.reason
    g.advance(stop, (.5, -.1, .2, 1))
    return g.end(not cancel), g

first, _ = stroke((-.5, 0), (-.2, 0), preview=False)
ready()
with metrics.Metrics() as observed:
    second, g = stroke((-.2, 0), (.2, 0))
    ready()
    assert observed.counts.get('stroke_gpu_adoptions') == 3, observed.report()
    if not runtime.device().pixel_publish:
        assert not observed.counts.get('engine_texture_readbacks'), observed.report()
print('GPU_ADOPTION_PASS', flush=True)
with patch.object(gesture, 'PREVIEW_LIMIT', 128), metrics.Metrics() as observed:
    stroke((-.2, .15), (.2, .15))
    ready()
    if not runtime.device().pixel_publish:
        assert not observed.counts.get('engine_texture_readbacks'), observed.report()
    if gesture._native_metal():
        # Native Metal previews at full resolution (gesture.Canvas): no bound, the finished
        # stroke's GPU result is adopted as it is.
        assert observed.counts.get('stroke_gpu_adoptions') == 3, observed.report()
        assert not observed.counts.get('stroke_gpu_replays'), observed.report()
    else:
        assert observed.counts.get('stroke_gpu_replays') == 3, observed.report()
print('BOUNDED_GPU_PREVIEW_PASS', flush=True)
count = len(scene.open_painter.strokes)
stroke((0, .3), (.3, .3), cancel=True)
ready()
assert len(scene.open_painter.strokes) == count
print('CANCEL_PASS', flush=True)

settings.symmetry = (True, False, False)
mirrored, _ = stroke((-.5, .4), (-.3, .4), preview=False)
rows = stroke_raster.unpack_samples(mirrored.samples)
assert rows[:, 0].min() < -.45 and rows[:, 0].max() > .45
assert len(rows) % 2 == 0
settings.symmetry = (False, False, False)

tree = bpy.data.node_groups.new('Recorded Brush', 'OpenPainterGraph')
tree[brush_graph.MARKER] = True
input = tree.nodes.new('OPBrushInputs')
output = tree.nodes.new('OPBrushOutput')
tree.links.new(input.outputs['Pressure'], output.inputs['Roughness'])
settings.node_group = tree
record, _ = stroke((-.3, -.3), (.2, -.3))
resolved = payload.decode(record, record.sample_count)
assert resolved and all('roughness' in dab['values'] for dab in resolved)
before = record.samples, record.dab_payload
tree.links.clear()
assert (record.samples, record.dab_payload) == before
settings.node_group = None
settings.particles, settings.particle_count = True, 3
particle, _ = stroke((.4, .3), (.45, .3), preview=False)
settings.particles = False
assert particle.sample_count >= 3
settings.blend = 'SMUDGE'
smudge, _ = stroke((-.1, 0), (.4, 0))
assert all('donor' in dab for dab in payload.decode(smudge, smudge.sample_count))
ready()
engine = shader_preview.session_engine(scene, ts)
gpu_pixels = {c: engine.render_channel(c) for c in settings.channels}
with patch.object(particles, 'contacts', side_effect=AssertionError('Physics ran during replay')):
    LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
for output in ts.outputs:
    if output.channel in gpu_pixels:
        expected = images.pixels(output.image)
        error = float(np.abs(expected - gpu_pixels[output.channel]).max())
        assert error < (2e-5 if output.channel == 'HEIGHT' else .002), (output.channel, error)
print('RESOLVED_REPLAY_PASS', flush=True)

# Copies must preserve resolved graph values and smudge donors, not just positions.
from open_painter.painting import strokes as records
originals = [(s.samples, s.payload_version, s.dab_payload, s.brush_graph_data)
             for s in records.strokes_for(scene.open_painter, layer.stroke_key, ts)]
copied_key = records.copy_key(scene.open_painter, layer.stroke_key, ts)
assert [(s.samples, s.payload_version, s.dab_payload, s.brush_graph_data)
        for s in records.strokes_for(scene.open_painter, copied_key, ts)] == originals
records.purge_orphans(scene.open_painter)
print('RESOLVED_DUPLICATION_PASS', flush=True)

text_layer = stack.add(ts, 'FILL', 'Text')
mask = text_layer.masks.add()
from open_painter.utils.ids import new_id
mask.id, mask.source, mask.operation = new_id(), 'TEXT', 'MULTIPLY'
mask.text.content = 'CPU\\nGPU'
ready()
actual = shader_preview.session_engine(scene, ts).render_channel('BASE_COLOR')
LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
expected = images.pixels(next(o.image for o in ts.outputs if o.channel == 'BASE_COLOR'))
assert np.abs(actual - expected).max() < .002
print('TEXT_STACK_PARITY_PASS', flush=True)
identifier = ts.id
path = str(Path(tempfile.gettempdir()) / 'op-authoring-save.blend')
bpy.ops.wm.save_as_mainfile(filepath=path)
bpy.ops.wm.open_mainfile(filepath=path)
scene = bpy.context.scene
ts = next(t for t in scene.open_painter.texture_sets if t.id == identifier)
assert all(s.sample_count == len(stroke_raster.unpack_samples(s.samples)) for s in scene.open_painter.strokes)
LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
print('AUTHORING_WORKFLOW_PASS', flush=True)
open_painter.unregister()
