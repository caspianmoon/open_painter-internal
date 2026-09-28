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
from open_painter.evaluation.shader_resources import RESOURCE, Resources, alive
from open_painter.utils.ids import new_id


def ready(ts):
    deadline = monotonic() + 30
    while monotonic() < deadline:
        shader_preview.tick(ts.id_data, ts)
        info = shader_preview.diagnostics(ts.id_data, ts)
        assert not info['error'], info
        if shader_preview.current(ts.id_data, ts):
            return shader_preview.ensure(ts.id_data, ts)
    raise AssertionError(info)


# These fixtures verify CPU boundary caching and publication explicitly.
# Supported neighborhood effects now run on the GPU by default; force the
# established fallback here. engine_filters.py covers the native GPU path.
from unittest.mock import patch
_cpu_boundaries = patch('open_painter.evaluation.engine.filters.supported', return_value=False)
_cpu_boundaries.start()
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
    shader_preview.tick(scene, ts)   # the engine renders the edits
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
assert not any(out.revision for out in ts.outputs), 'An isolated prefix ran the CPU evaluator'
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
# Tables validate their entries lazily: another set's table adopts the same
# leaf and may evict it, and Purge Unused Data takes any user-less leaf or
# in-flight placeholder. A dead entry is scheduled again, never dereferenced.
other = Resources(16)
shared = other.request(('shared-leaf',), lambda: source_rows(.5))
while other.pending:
    other.step()
assert resources.request(('shared-leaf',), lambda: source_rows(.5)) == shared
bpy.data.images.remove(shared)
resources.step()
again = resources.request(('shared-leaf',), lambda: source_rows(.5))
while resources.pending:
    resources.step()
assert again.get('op_shader_resource_ready') and abs(float(image_manager.pixels(again)[0, 0, 0]) - .5) < 1e-6
placeholder = resources.request(('purged-placeholder',), lambda: source_rows(.75))
bpy.data.images.remove(placeholder)
resources.step()
assert not resources.pending
placeholder = resources.request(('purged-placeholder',), lambda: source_rows(.75))
while resources.pending:
    resources.step()
assert placeholder.get('op_shader_resource_ready') and abs(float(image_manager.pixels(placeholder)[0, 0, 0]) - .75) < 1e-6
# A leaf a rehydrated node still samples under an identity the table does not
# list (an interrupted placeholder, or one superseded by the entry of its own
# key) is never cached and never removed while bound: the table releases it
# once the binding moves on, instead of leaving it in bpy.data unlisted.
tree = bpy.data.node_groups.new('OP boundary holder', 'ShaderNodeTree')
holder, sibling = tree.nodes.new('ShaderNodeTexImage'), tree.nodes.new('ShaderNodeTexImage')
stale = bpy.data.images.new('OP stale placeholder', width=16, height=16, float_buffer=True)
stale[RESOURCE], stale['op_shader_resource_ready'] = 'stale-placeholder', False
holder.image = stale
listed = resources.request(('listed-leaf',), lambda: source_rows(.5))
superseded = bpy.data.images.new('OP superseded placeholder', width=16, height=16, float_buffer=True)
superseded[RESOURCE], superseded['op_shader_resource_ready'] = listed[RESOURCE], False
sibling.image = superseded
for image in (stale, superseded):
    resources.adopt(image)
    resources.adopt(image)   # listed once
assert len(resources.superseded) == 2 and 'stale-placeholder' not in resources.entries
resources.step()
assert alive(stale) and alive(superseded) and resources.entries[listed[RESOURCE]].image == listed
holder.image = None
resources.step()
from open_painter.evaluation.shader_resources import released
assert released(stale), 'a superseded placeholder outlived the binding that held it'   # released, not removed (0.13 hardening)
assert alive(superseded) and superseded.users == 1
sibling.image = None
resources.step()
assert released(superseded) and alive(listed) and not released(listed), 'the listed entry was released with its superseded twin'
assert not resources.superseded
bpy.data.node_groups.remove(tree)
other.close()
resources.close()
# Evaluated-geometry changes reach the engine's surface rasters (a triplanar procedural is
# evaluated on the GPU from world position and normal) through the normal depsgraph observer.
from open_painter.evaluation import cache, dependencies
geometry_ts = sets.create(scene, 'Geometry source dependencies', 512)
sets.assign(scene, geometry_ts, bpy.context.object)
geometry_layer = stack.add(geometry_ts, 'FILL')
geometry_prop = stack.channel_for(geometry_layer, 'ROUGHNESS')
geometry_prop.procedural, geometry_prop.projection = 'FBM', 'TRIPLANAR'
material_manager.repair(geometry_ts)
ready(geometry_ts)


def roughness(ts):
    shader_preview.flush(scene, ts)
    return image_manager.pixels(next(o.image for o in ts.outputs if o.channel == 'ROUGHNESS'))[..., 0].copy()


before_move = roughness(geometry_ts)
bpy.context.object.location.x += .123
bpy.context.view_layer.update()
dependencies.refresh(scene)
cache.tick()
ready(geometry_ts)
assert np.abs(roughness(geometry_ts) - before_move).max() > 1e-3, 'moving the object did not re-render its triplanar field'
# The frame is no part of a static prop's identities: a scrub of the timeline keeps the
# geometry key, the session and every published channel.
assert not dependencies.animated(geometry_ts)
frame_key = dependencies.geometry_key(scene, geometry_ts)
evaluations = shader_preview.diagnostics(scene, geometry_ts)['engine']
settled = roughness(geometry_ts)
scene.frame_set(scene.frame_current + 1)
bpy.context.view_layer.update()
dependencies._candidates.add(scene.as_pointer())
with Metrics() as frame_observed:
    dependencies.refresh(scene)
    cache.tick()
    assert shader_preview.current(scene, geometry_ts), 'a frame change invalidated a static prop'
    assert not shader_preview.diagnostics(scene, geometry_ts)['pending_resources']
    assert frame_observed.counts['shader_resource_requests'] == 0, frame_observed.report()
    assert frame_observed.counts.get('engine_channel_publications', 0) == 0, frame_observed.report()
assert dependencies.geometry_key(scene, geometry_ts) == frame_key, 'a frame change re-keyed a static prop'
# An armature-deformed prop follows the pose: the same scrub re-renders its triplanar field.
rig = bpy.data.objects.new('Boundary rig', bpy.data.armatures.new('Boundary rig'))
scene.collection.objects.link(rig)
deform = bpy.context.object.modifiers.new('Boundary armature', 'ARMATURE')
deform.object = rig
bpy.context.view_layer.update()
dependencies._candidates.add(scene.as_pointer())
dependencies.refresh(scene)   # the modifier itself re-keys the set
cache.tick()
ready(geometry_ts)
assert dependencies.animated(geometry_ts)
deformed_key = dependencies.geometry_key(scene, geometry_ts)
assert deformed_key[1] == (scene.frame_current, scene.frame_subframe)
scene.frame_set(scene.frame_current + 1)
bpy.context.view_layer.update()
dependencies._candidates.add(scene.as_pointer())
with Metrics() as deformed_observed:
    dependencies.refresh(scene)
    cache.tick()
    ready(geometry_ts)
assert dependencies.geometry_key(scene, geometry_ts) != deformed_key, 'a frame change left a deformed prop keyed'
assert deformed_observed.counts.get('engine_channel_publications', 0) >= 1, 'a frame change of a deformed prop re-rendered nothing'
bpy.context.object.modifiers.remove(deform)
bpy.data.objects.remove(rig)
bpy.context.view_layer.update()
dependencies._candidates.add(scene.as_pointer())
dependencies.refresh(scene)
cache.tick()
ready(geometry_ts)
assert not dependencies.animated(geometry_ts)
# Purge Unused Data removes an in-flight placeholder (nothing samples it yet)
# and the unused leaf of a previous slider value: the session rasters the
# source again instead of failing on the removed image.
spatial = geometry_layer.masks.add()
spatial.id, spatial.source = new_id(), 'FILTER'
spatial.effect.kind, spatial.effect.radius = 'GAUSSIAN_BLUR', 2
material_manager.repair(geometry_ts)
geometry_graph = ready(geometry_ts)
boundary = geometry_graph.resource_bindings[0]
settled = boundary.node.image
spatial = next(m for m in geometry_layer.masks if m.id == spatial.id)
spatial.effect.radius = 5
placeholder = boundary.pending_image
assert placeholder is not None and not placeholder.get('op_shader_resource_ready')
bpy.data.images.remove(placeholder)
ready(geometry_ts)
assert boundary.node.image != settled and boundary.node.image.get('op_shader_resource_ready')
assert boundary.pending_image is None
bpy.data.images.remove(settled)
spatial.effect.radius = 2
ready(geometry_ts)
assert boundary.node.image.get('op_shader_resource_ready') and boundary.node.image.has_data
assert not shader_preview.diagnostics(scene, geometry_ts)['error']
scene_mask = geometry_layer.masks.add()
scene_mask.id, scene_mask.source = 'manual-scene-field', 'SCENE'
scene_mask.influence.kind = 'UP'
material_manager.repair(geometry_ts)
ready(geometry_ts)
assert scene_mask.influence.image
with Metrics() as scene_observed:
    scene_mask.black = .2
    shader_preview.tick(scene, geometry_ts)
    assert shader_preview.current(scene, geometry_ts)
    assert scene_observed.counts['shader_resource_requests'] == 0, scene_observed.report()
    assert scene_observed.phases.get('scene_analysis', {}).get('calls', 0) == 0
report = ROOT / '.blender-test/shader-native/boundaries.json'
report.parent.mkdir(parents=True, exist_ok=True)
report.write_text(json.dumps({'result': 'PASS', 'checks': ['published-prefix-invalidation',
    'unrelated-channel-no-raster', 'filter-strength-and-levels-native', 'atomic-input-swap',
    'no-cpu-evaluation', 'superseded-value-recovery', 'removed-image-recovery',
    'purged-placeholder-recovery', 'superseded-leaf-release', 'geometry-input-invalidation', 'static-frame-no-raster',
    'deformed-frame-raster', 'scene-levels-no-analysis'], 'dependency_edit': info}, indent=2) + '\n')
open_painter.unregister()
print('SHADER_BOUNDARIES_PASS', report, flush=True)
