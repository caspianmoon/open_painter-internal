"""Engine callback dependencies, resource errors, detach, and explicit CPU mode."""
import json
import os
from pathlib import Path
import sys
from time import monotonic
from unittest.mock import patch

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.constants import CHANNELS, OWNER
from open_painter.core import image_manager, layer_stack as stack, material_manager
from open_painter.core import texture_set_manager as sets
from open_painter.core.texture_set_node import NODE_ID
from open_painter.evaluation import cache, shader_preview
from open_painter.evaluation.metrics import Metrics


def links(material):
    return tuple(sorted((link.from_node.as_pointer(), link.from_socket.identifier,
                         link.to_node.as_pointer(), link.to_socket.identifier)
                        for link in material.node_tree.links))


def texture_set_node(material):
    nodes = [node for node in material.node_tree.nodes if node.bl_idname == NODE_ID]
    assert len(nodes) == 1, [node.name for node in material.node_tree.nodes]
    return nodes[0]


def ready(scene, ts):
    deadline = monotonic() + 20
    while monotonic() < deadline:
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert not info['error'], info
        if shader_preview.current(scene, ts):
            return
    raise AssertionError(shader_preview.diagnostics(scene, ts))


# These fixtures verify CPU boundary caching and publication explicitly.
# Supported neighborhood effects now run on the GPU by default; force the
# established fallback here. engine_filters.py covers the native GPU path.
from unittest.mock import patch
_cpu_boundaries = patch('open_painter.evaluation.engine.filters.supported', return_value=False)
_cpu_boundaries.start()
open_painter.register()
scene = bpy.context.scene
ts = sets.create(scene, 'Shader runtime dependencies', 512)
# The set's channel images are the engine's targets, read by the material's texture set node;
# creating the set evaluates nothing on the CPU.
assert {out.channel for out in ts.outputs} == set(CHANNELS)
assert all(out.image.get('op_engine_output') and not out.revision for out in ts.outputs)
assert texture_set_node(ts.material).texture_set_id == ts.id
source = stack.add(ts, 'FILL', 'Published roughness')
source_id = source.id
rough = stack.channel_for(source, 'ROUGHNESS')
rough.value = .4
effect = rough.effects.add()
effect.kind = 'LEVELS'
consumer = stack.add(ts, 'FILL', 'Height consumer')
consumer_id = consumer.id
for prop in consumer.channels:
    prop.enabled = prop.channel == 'HEIGHT'
stack.channel_for(consumer, 'HEIGHT').value = .6
mask = consumer.masks.add()
mask.id, mask.source = 'runtime-published-input', 'REFERENCE'
mask.reference_layer, mask.reference_channel = source_id, 'ROUGHNESS'
material_manager.repair(ts)
ready(scene, ts)

# Existing exported images may be present in a native project. A callback must
# preserve older invalidations and only add this edit's transitive consumers.
for channel in CHANNELS:
    image_manager.output(ts, channel).dirty = False
output = next(out for out in ts.outputs if out.channel == 'BASE_COLOR')
output.dirty = True
source = next(layer for layer in ts.layers if layer.id == source_id)
rough = stack.channel_for(source, 'ROUGHNESS')
effect = rough.effects[0]
before_builds = shader_preview.diagnostics(scene, ts)['graph_builds']
revisions = {out.channel: out.revision for out in ts.outputs}
with Metrics() as observed:
    effect.gamma = 1.6
    info = shader_preview.diagnostics(scene, ts)
    assert not info['rebuild_pending'], info
    assert info['affected_channels'] == ['HEIGHT', 'ROUGHNESS'], info
    assert info['updated_bindings'] == 1, info
    assert {out.channel for out in ts.outputs if out.dirty} == {'BASE_COLOR', 'ROUGHNESS', 'HEIGHT'}
    assert info['graph_builds'] == before_builds
    cache.tick()
    assert shader_preview.current(scene, ts)
    assert shader_preview.diagnostics(scene, ts)['engine']['channels'] == ['HEIGHT', 'ROUGHNESS']
    assert cache.job_state(scene, ts)['state'] == 'SHADER'
    cache.on_save(None)
    assert {out.channel: out.revision for out in ts.outputs} == revisions
    for phase in ('source_generation', 'masking', 'filters', 'blending', 'image_write'):
        assert observed.phases[phase]['calls'] == 0, observed.report()
    for key in ('shader_resource_requests', 'shader_resource_publications', 'live_channel_publications'):
        assert observed.counts[key] == 0, observed.report()
dependency_report = info

# Surface uniforms immediately update fixed material sockets without changing
# channel bake state or invalidating a graph revision.
before_serial = ts.edit_serial
before_dirty = {out.channel: out.dirty for out in ts.outputs}
ts.height_distance, ts.coat_weight = .15, .6
roles = {node.get('role'): node for node in ts.material.node_tree.nodes if node.get(OWNER)}
assert set(roles) == {'Texture Set', 'Surface', 'Output'}, sorted(roles)   # the bump lives in the node's group
assert abs(roles['Surface'].inputs['Coat Weight'].default_value - .6) < 1e-6
assert ts.edit_serial == before_serial and shader_preview.current(scene, ts)
assert {out.channel: out.dirty for out in ts.outputs} == before_dirty
tree = texture_set_node(ts.material).node_tree
assert abs(next(n for n in tree.nodes if n.get('role') == 'Height').inputs['Distance'].default_value - .15) < 1e-6

# Unexpected update failures retain the valid graph and cannot be reported as
# current. A subsequent valid property edit recovers through the same graph.
graph = shader_preview.ensure(scene, ts)
attached = links(ts.material)
with patch.object(graph, 'update', side_effect=ValueError('Injected binding failure')):
    rough.value = .7
assert not shader_preview.current(scene, ts)
assert cache.job_state(scene, ts)['state'] == 'FAILED'
assert 'Injected binding failure' in shader_preview.status(scene, ts)
assert links(ts.material) == attached
rough.value = .5
shader_preview.tick(scene, ts)
assert shader_preview.current(scene, ts)
assert shader_preview.diagnostics(scene, ts)['graph_builds'] == before_builds

# A resource that fails after the first cooperative step must remain FAILED even
# if its queue has completed/popped. The native save flush may not hide it.
graph.resources.request(('runtime-injected-boundary',), lambda: iter(()))
with patch.object(graph, 'step_resources', side_effect=[{}, ValueError('Injected boundary failure')]):
    try:
        shader_preview.flush(scene, ts)
    except ValueError as error:
        assert str(error) == 'Injected boundary failure'
    else:
        raise AssertionError('A failed shader resource was silently accepted')
assert not shader_preview.current(scene, ts)
assert shader_preview.diagnostics(scene, ts)['state'] == 'FAILED'
shader_preview.retry(scene, ts)
ready(scene, ts)

# Dropping the runtime leaves the material as it is: it reads the set's images, which keep
# their last result. The rebuilt session publishes into the same images.
attached = links(ts.material)
shader_preview.clear()
assert not shader_preview.current(scene, ts)
assert links(ts.material) == attached
ready(scene, ts)
assert links(ts.material) == attached
assert not any(node.get('role') == 'Procedural Graph' for node in ts.material.node_tree.nodes)

# External callers may compile before the timer renders. Ready status must describe the
# published images, not compilation; a rebuild never touches the material.
source = next(layer for layer in ts.layers if layer.id == source_id)
# Visibility is a uniform: hiding a layer keeps the compiled graph.
graph = shader_preview.ensure(scene, ts)
identities = graph.node_identities()
source.visible = False
shader_preview.tick(scene, ts)
assert shader_preview.current(scene, ts) and shader_preview.ensure(scene, ts) is graph
assert graph.node_identities() == identities
source.visible = True
shader_preview.tick(scene, ts)
assert shader_preview.current(scene, ts)
patches = shader_preview.diagnostics(scene, ts)['graph_patches']
source.blend_mode = 'MULTIPLY'
replacement = shader_preview.ensure(scene, ts)   # recompiled in place (EngineGraph.patch), not yet rendered
assert replacement is graph and not shader_preview.current(scene, ts)
assert shader_preview.diagnostics(scene, ts)['graph_patches'] == patches + 1
assert shader_preview.diagnostics(scene, ts)['attached'] and links(ts.material) == attached
ready(scene, ts)
surface = next(node for node in ts.material.node_tree.nodes if node.get('role') == 'Surface')
assert surface.inputs['Roughness'].links[0].from_node == texture_set_node(ts.material)
source.blend_mode = 'NORMAL'
ready(scene, ts)

# Explicit raster consumers can temporarily replace the adapter without changing
# any recipe serial. The next native tick must still restore shader connections.
with Metrics() as reattachment:
    material_manager.repair(ts, image_overrides={})
    assert not shader_preview.current(scene, ts)
    assert not shader_preview.diagnostics(scene, ts)['attached']
    ready(scene, ts)
    assert shader_preview.diagnostics(scene, ts)['attached']
    assert reattachment.phases['image_write']['calls'] == 0

# CPU reference remains an explicit, functional choice and writes authoritative
# output pixels. Returning to native mode must not perform another texture bake.
ts.preview_backend = 'CPU'
cache._pending[cache.key_for(scene, ts)] = (ts.edit_serial, monotonic() - 1)
deadline = monotonic() + 20
while ts.dirty and monotonic() < deadline:
    cache.tick()
assert not ts.dirty and all(not out.dirty for out in ts.outputs)
assert all(out.revision > revisions[out.channel] for out in ts.outputs)
assert cache.job_state(scene, ts)['state'] == 'READY'
revisions = {out.channel: out.revision for out in ts.outputs}
ts.preview_backend = 'LIVE'
ready(scene, ts)
assert {out.channel: out.revision for out in ts.outputs} == revisions
surface = next(node for node in ts.material.node_tree.nodes if node.get('role') == 'Surface')
assert surface.inputs['Roughness'].links[0].from_node == texture_set_node(ts.material), 'the material lost its texture set node'

# Managed image writes must invalidate a native mask boundary even before the
# texture set has ever allocated a baked output. Inspect the actual refreshed
# boundary pixels, not merely its dirty flag or source image pointer.
masked = sets.create(scene, 'Unbaked mask image dependency', 512)
layer = stack.add(masked, 'FILL')
for prop in layer.channels:
    prop.enabled = prop.channel == 'ROUGHNESS'
source_image = bpy.data.images.new('Unmanaged mutable mask source', width=16, height=16,
                                  alpha=True, float_buffer=True)
source_image.colorspace_settings.name = 'Non-Color'
data = np.ones((16, 16, 4), np.float32)
data[..., :3] = .2
image_manager.write(source_image, data, pack=False)
mask = layer.masks.add()
mask.id, mask.source, mask.image = 'runtime-mask-image', 'FILL', source_image
filtered = layer.masks.add()
filtered.id, filtered.source = 'runtime-mask-blur', 'FILTER'
filtered.effect.kind, filtered.effect.radius = 'GAUSSIAN_BLUR', 1
material_manager.repair(masked)
ready(scene, masked)
assert not any(out.revision for out in masked.outputs)
graph = shader_preview.ensure(scene, masked)
boundary = graph.resource_bindings[0]
old = image_manager.pixels(boundary.node.image)[..., 0].copy()
serial = masked.edit_serial
data[..., :3] = .8
image_manager.write(source_image, data, pack=False)
assert masked.edit_serial > serial, 'Image write ignored a recipe with no baked outputs'
assert shader_preview.diagnostics(scene, masked)['pending_resources']
ready(scene, masked)
new = image_manager.pixels(boundary.node.image)[..., 0]
assert float(np.mean(new - old)) > .5, 'Mask prefix retained stale source pixels'
assert not any(out.revision for out in masked.outputs), 'the engine path ran the CPU evaluator'

# A raster that raises keeps its identity bound, so the failure must outlive the
# edit that reported it: an unrelated uniform edit cannot report the graph
# current over the placeholder, and Retry rasters the source again.
settled = boundary.node.image
factory = boundary.factory
def failing(signature):
    raise ValueError('Injected raster failure')
    yield
boundary.factory = failing
data[..., :3] = .4
image_manager.write(source_image, data, pack=False)
assert shader_preview.diagnostics(scene, masked)['pending_resources']
shader_preview.tick(scene, masked)
info = shader_preview.diagnostics(scene, masked)
assert info['state'] == 'FAILED' and 'Injected raster failure' in info['error'], info
layer = next(l for l in masked.layers if l.id == layer.id)
layer.opacity = .9
shader_preview.tick(scene, masked)
info = shader_preview.diagnostics(scene, masked)
assert not shader_preview.current(scene, masked)
assert info['state'] == 'FAILED' and 'Injected raster failure' in info['error'], info
assert 'Injected raster failure' in shader_preview.status(scene, masked)
assert boundary.node.image == settled, 'the last valid input stays bound while failed'
try:
    shader_preview.flush(scene, masked)
except RuntimeError as error:
    assert 'Injected raster failure' in str(error)
else:
    raise AssertionError('A failed shader resource was hidden by an unrelated edit')
boundary.factory = factory
shader_preview.retry(scene, masked)
ready(scene, masked)
boundary = shader_preview.ensure(scene, masked).resource_bindings[0]
assert boundary.node.image.get('op_shader_resource_ready') and boundary.pending_image is None
assert abs(float(np.mean(image_manager.pixels(boundary.node.image)[..., 0])) - .4) < .05
assert not any(out.revision for out in masked.outputs)
assert not source_image.get(OWNER) and not source_image.packed_file
cache.on_save(None)
assert source_image.packed_file, 'Saving did not pack a directly assigned source image'

report = Path(os.environ.get('OP_SHADER_RUNTIME_REPORT', '/private/tmp/op-shader-runtime.json'))
report.parent.mkdir(parents=True, exist_ok=True)
report.write_text(json.dumps({'result': 'PASS', 'blender': bpy.app.version_string,
    'published_dependency_edit': dependency_report, 'hot_edit_metrics': observed.report(),
    'checks': ['native-create-no-bake', 'transitive-filter-dependencies', 'retain-previous-dirty',
               'surface-uniforms', 'update-failure-recovery', 'boundary-flush-failure',
               'detach-material-preservation', 'rebuild-keeps-material',
               'external-build-renders-before-ready', 'raster-override-native-restoration',
               'explicit-cpu-reference',
               'native-reattachment', 'image-write-without-baked-outputs',
               'raster-failure-persists-until-retry', 'unmanaged-source-save-packing']},
    indent=2) + '\n')
open_painter.unregister()
print('SHADER_RUNTIME_PASS', str(report), flush=True)
