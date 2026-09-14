"""Native callback dependencies, resource errors, detach, and explicit CPU mode."""
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
from open_painter.evaluation import cache, shader_preview
from open_painter.evaluation.metrics import Metrics


def links(material):
    return tuple(sorted((link.from_node.as_pointer(), link.from_socket.identifier,
                         link.to_node.as_pointer(), link.to_socket.identifier)
                        for link in material.node_tree.links))


def ready(scene, ts):
    deadline = monotonic() + 20
    while monotonic() < deadline:
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert not info['error'], info
        if shader_preview.current(scene, ts):
            return
    raise AssertionError(shader_preview.diagnostics(scene, ts))


open_painter.register()
scene = bpy.context.scene
ts = sets.create(scene, 'Shader runtime dependencies', 512)
assert not ts.outputs, 'Creating a native recipe allocated baked outputs'
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
    assert shader_preview.current(scene, ts), info
    assert info['affected_channels'] == ['HEIGHT', 'ROUGHNESS'], info
    assert info['updated_bindings'] == 1, info
    assert {out.channel for out in ts.outputs if out.dirty} == {'BASE_COLOR', 'ROUGHNESS', 'HEIGHT'}
    assert info['graph_builds'] == before_builds
    assert cache.job_state(scene, ts)['state'] == 'SHADER'
    cache.tick()
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
assert abs(roles['Height'].inputs['Distance'].default_value - .15) < 1e-6
assert abs(roles['Surface'].inputs['Coat Weight'].default_value - .6) < 1e-6
assert ts.edit_serial == before_serial and shader_preview.current(scene, ts)
assert {out.channel: out.dirty for out in ts.outputs} == before_dirty

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

# Detaching the Python runtime preserves a useful material without the add-on.
# Reattachment replaces the old owned graph instead of growing duplicate groups.
attached = links(ts.material)
shader_preview.clear()
assert not shader_preview.current(scene, ts)
assert links(ts.material) == attached
ready(scene, ts)
assert sum(node.get('role') == 'Procedural Graph' for node in ts.material.node_tree.nodes) == 1

# External callers may compile before the timer gets a chance to wire the
# replacement. Ready status must describe the visible adapter, not compilation.
source = next(layer for layer in ts.layers if layer.id == source_id)
# Visibility is a shader uniform: hiding a layer keeps the compiled graph.
graph = shader_preview.ensure(scene, ts)
identities = graph.node_identities()
source.visible = False
assert shader_preview.current(scene, ts) and shader_preview.ensure(scene, ts) is graph
assert graph.node_identities() == identities
source.visible = True
assert shader_preview.current(scene, ts)
source.blend_mode = 'MULTIPLY'
replacement = shader_preview.ensure(scene, ts)
assert not shader_preview.current(scene, ts)
assert not shader_preview.diagnostics(scene, ts)['attached']
ready(scene, ts)
surface = next(node for node in ts.material.node_tree.nodes if node.get('role') == 'Surface')
assert surface.inputs['Roughness'].links[0].from_node == replacement.instance
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
graph = shader_preview.ensure(scene, ts)
surface = next(node for node in ts.material.node_tree.nodes if node.get('role') == 'Surface')
assert surface.inputs['Roughness'].links[0].from_node == graph.instance, 'CPU images remained attached'

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
assert not masked.outputs
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
assert not masked.outputs
assert not source_image.get(OWNER) and not source_image.packed_file
cache.on_save(None)
assert source_image.packed_file, 'Saving did not pack a directly assigned source image'

report = Path(os.environ.get('OP_SHADER_RUNTIME_REPORT', '/private/tmp/op-shader-runtime.json'))
report.parent.mkdir(parents=True, exist_ok=True)
report.write_text(json.dumps({'result': 'PASS', 'blender': bpy.app.version_string,
    'published_dependency_edit': dependency_report, 'hot_edit_metrics': observed.report(),
    'checks': ['native-create-no-bake', 'transitive-filter-dependencies', 'retain-previous-dirty',
               'surface-uniforms', 'update-failure-recovery', 'boundary-flush-failure',
               'detach-material-preservation', 'reattach-no-duplicate-groups',
               'external-build-requires-surface-attachment', 'raster-override-native-restoration',
               'explicit-cpu-reference',
               'native-reattachment', 'image-write-without-baked-outputs', 'unmanaged-source-save-packing']},
    indent=2) + '\n')
open_painter.unregister()
print('SHADER_RUNTIME_PASS', str(report), flush=True)
