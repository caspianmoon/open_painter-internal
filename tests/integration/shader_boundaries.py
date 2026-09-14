"""Isolated fallback dependencies, superseded jobs and shader-only controls."""
import json
from pathlib import Path
import sys
from time import monotonic

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import texture_set_manager as sets, layer_stack as stack, material_manager, image_manager
from open_painter.evaluation import shader_preview
from open_painter.evaluation.metrics import Metrics
from open_painter.evaluation.shader_resources import Resources


def ready(ts):
    deadline = monotonic() + 30
    while monotonic() < deadline:
        shader_preview.tick(ts.id_data, ts)
        info = shader_preview.diagnostics(ts.id_data, ts)
        assert not info['error'], info
        if shader_preview.current(ts.id_data, ts):
            return shader_preview.ensure(ts.id_data, ts)
    raise AssertionError(info)


open_painter.register()
scene = bpy.context.scene
ts = sets.create(scene, 'Isolated reference blur', 512)
source = stack.add(ts, 'FILL', 'Published source')
source_id = source.id
prop = stack.channel_for(source, 'ROUGHNESS')
prop.value = .2
other = stack.channel_for(source, 'METALLIC')
other.value = .3
consumer = stack.add(ts, 'FILL', 'Reference consumer')
for channel in consumer.channels:
    channel.enabled = channel.channel == 'HEIGHT'
reference = consumer.masks.add()
reference.id, reference.source = 'published', 'REFERENCE'
reference.reference_layer, reference.reference_channel = source_id, 'ROUGHNESS'
blur = consumer.masks.add()
blur.id, blur.source = 'blurred-reference', 'FILTER'
blur.effect.kind, blur.effect.radius = 'GAUSSIAN_BLUR', 1
material_manager.repair(ts)
graph = ready(ts)
assert len(graph.resource_bindings) == 1
binding = graph.resource_bindings[0]
old_image = binding.node.image
old = image_manager.pixels(old_image)[..., 0].copy()
# Unrelated sources and the final filter's shader-native mix/levels stay hot.
source = next(layer for layer in ts.layers if layer.id == source_id)
with Metrics() as observed:
    stack.channel_for(source, 'METALLIC').value = .9
    blur.effect.strength = .3
    blur.black = .1
    blur.strength = .7
    assert shader_preview.current(scene, ts)
    assert observed.counts['shader_resource_requests'] == 0, observed.report()
    assert binding.node.image == old_image
# Published source dependency reaches only this isolated prefix. While it runs,
# keep the prior valid image attached; publish the replacement atomically.
stack.channel_for(source, 'ROUGHNESS').value = .8
info = shader_preview.diagnostics(scene, ts)
assert info['raster_boundaries_queued'] == 1, info
assert binding.node.image == old_image and binding.pending_image
ready(ts)
assert binding.node.image != old_image and not binding.pending_image
new = image_manager.pixels(binding.node.image)[..., 0]
assert float(np.mean(new - old)) > .59
assert not ts.outputs, 'An isolated prefix allocated final channel textures'
# Returning to a superseded compatibility input must regenerate it, not accept
# a cancelled placeholder as a completed cache entry.
resources = Resources(16)
def source_rows(value):
    yield 0, np.full((16, 16), value, np.float32)
a = resources.request(('cancelled',), lambda: source_rows(.25))
resources.used.clear()
resources.step()
assert not resources.entries
b = resources.request(('cancelled',), lambda: source_rows(.25))
while resources.pending:
    resources.step()
assert b.get('op_shader_resource_ready')
assert abs(float(image_manager.pixels(b)[0, 0, 0]) - .25) < 1e-6
resources.close()
# Evaluated-geometry changes reach cached triplanar inputs through the normal
# depsgraph observer; native mask Levels never repeat initial scene analysis.
from open_painter.evaluation import cache, dependencies
geometry_ts = sets.create(scene, 'Geometry source dependencies', 512)
sets.assign(scene, geometry_ts, bpy.context.object)
geometry_layer = stack.add(geometry_ts, 'FILL')
geometry_prop = stack.channel_for(geometry_layer, 'ROUGHNESS')
geometry_prop.procedural, geometry_prop.projection = 'FBM', 'TRIPLANAR'
material_manager.repair(geometry_ts)
geometry_graph = ready(geometry_ts)
old_geometry_image = geometry_graph.resource_bindings[0].node.image
bpy.context.object.location.x += .123
bpy.context.view_layer.update()
dependencies.refresh(scene)
cache.tick()
assert shader_preview.diagnostics(scene, geometry_ts)['pending_resources']
ready(geometry_ts)
assert geometry_graph.resource_bindings[0].node.image != old_geometry_image
scene_mask = geometry_layer.masks.add()
scene_mask.id, scene_mask.source = 'manual-scene-field', 'SCENE'
scene_mask.influence.kind = 'UP'
material_manager.repair(geometry_ts)
ready(geometry_ts)
assert scene_mask.influence.image
with Metrics() as scene_observed:
    scene_mask.black = .2
    assert shader_preview.current(scene, geometry_ts)
    assert scene_observed.counts['shader_resource_requests'] == 0, scene_observed.report()
    assert scene_observed.phases.get('scene_analysis', {}).get('calls', 0) == 0
report = ROOT / '.blender-test/shader-native/boundaries.json'
report.parent.mkdir(parents=True, exist_ok=True)
report.write_text(json.dumps({'result': 'PASS', 'checks': ['published-prefix-invalidation',
    'unrelated-channel-no-raster', 'filter-strength-and-levels-native', 'atomic-input-swap',
    'no-output-allocation', 'superseded-value-recovery', 'geometry-input-invalidation',
    'scene-levels-no-analysis'], 'dependency_edit': info}, indent=2) + '\n')
open_painter.unregister()
print('SHADER_BOUNDARIES_PASS', report, flush=True)
