"""Idle/status polling skips layer detail walks; explicit diagnostics stay live.

Uses the real graph's cache-state resolver and Blender RNA/images without
compiling or rendering GPU programs. Only the engine's report is a test double.
"""
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache, dependencies, shader_preview
from open_painter.evaluation.shader_graph import Graph
from open_painter.ui import status
from open_painter.utils.ids import new_id

open_painter.register()
scene = bpy.context.scene
ts = sets.create(scene, 'Live diagnostic metadata', 512)
image = bpy.data.images.new('Diagnostic cache image', width=512, height=512, float_buffer=True)
bindings, expected = [], {}
for state in ('CURRENT', 'STALE', 'MISSING'):
    layer = ts.layers.add()
    layer.id, layer.name = new_id(), state
    row = layer.cache.add()
    row.channel = 'ROUGHNESS'
    row.image = image if state != 'MISSING' else None
    bindings.append(SimpleNamespace(layer_id=layer.id, channel='ROUGHNESS', stale=state == 'STALE'))
    expected[f'{layer.id}:ROUGHNESS'] = state
graph = Graph(scene, ts, None)
graph.cache_bindings = bindings


class ReportingEngine:
    scene_uid = scene.session_uid
    report_reads = 0
    reject_reads = False

    @property
    def report(self):
        assert not self.reject_reads, 'idle/status polling accessed detailed engine report'
        self.report_reads += 1
        return {'passes': 23, 'channels': ['ROUGHNESS']}


engine = ReportingEngine()
key = cache.key_for(scene, ts)
entry = shader_preview._sessions[key] = dict(
    graph=graph, engine=engine, attached=True, serial=ts.edit_serial,
    image_epoch=dependencies.image_epoch(), dirty=False)

# An idle native session is ready. Neither its scheduler nor the visible
# status text may traverse cached layers or construct the engine report.
engine.reject_reads = True
with patch.object(graph, 'cache_states', side_effect=AssertionError('unexpected layer detail walk')):
    for _ in range(3):
        assert cache.tick() == cache.IDLE_INTERVAL
        assert cache.job_state(scene, ts)['state'] == 'SHADER'
        assert shader_preview.status(scene, ts).endswith('live')
        assert status.of(bpy.context, ts).state == 'Idle'
    entry['error'] = 'Injected resource failure'
    assert cache.job_state(scene, ts)['error'] == entry['error']
    assert status.of(bpy.context, ts).state == 'Failed'
    assert entry['error'] in shader_preview.status(scene, ts)
    entry['error'] = ''
assert engine.report_reads == 0

# Explicit diagnostics retain detailed output and resolve live cache metadata
# even when no authoring callback or edit serial changed.
engine.reject_reads = False
full = shader_preview.diagnostics(scene, ts)
assert full['cache_states'] == expected, full
assert full['engine'] == {'passes': 23, 'channels': ['ROUGHNESS']}
serial = ts.edit_serial
ts.layers[0].cache[0].image = None
bindings[1].stale = False
assert ts.edit_serial == serial
expected[f'{ts.layers[0].id}:ROUGHNESS'] = 'MISSING'
expected[f'{ts.layers[1].id}:ROUGHNESS'] = 'CURRENT'
assert shader_preview.diagnostics(scene, ts)['cache_states'] == expected
removed_id = ts.layers[1].id
ts.layers.remove(1)
assert ts.edit_serial == serial
expected[f'{removed_id}:ROUGHNESS'] = 'MISSING'
assert shader_preview.diagnostics(scene, ts)['cache_states'] == expected

# Lightweight and full modes agree on all readiness and failure fields across
# the transitions consumed by the scheduler and UI, including pending inputs.
def matching_state():
    detailed = shader_preview.diagnostics(scene, ts)
    light = shader_preview.diagnostics(scene, ts, details=False)
    for field in detailed.keys() - {'cache_states', 'engine'}:
        assert light[field] == detailed[field], (field, light, detailed)


matching_state()
for field, value in (('dirty', True), ('attached', False), ('rebuild', True),
                     ('serial', ts.edit_serial - 1), ('error', 'Resource failed')):
    original = entry.get(field)
    entry[field] = value
    matching_state()
    if original is None:
        entry.pop(field)
    else:
        entry[field] = original
with patch.object(type(graph), 'pending_resources', new=property(lambda self: True)):
    matching_state()
    assert status.of(bpy.context, ts).state == 'Updating'
shader_preview._deferred[key] = {1: object()}
with patch.object(cache, 'painting_active', return_value=True):
    matching_state()
    assert status.of(bpy.context, ts).state == 'Paused'
shader_preview._deferred.pop(key)

# Sustained numeric edits must not continually remove the already-due timer.
# No real UI timer is modified by this host scheduling contract test.
from unittest.mock import Mock
timers = SimpleNamespace(is_registered=Mock(return_value=True), unregister=Mock(), register=Mock())
with patch.object(cache, 'bpy', SimpleNamespace(app=SimpleNamespace(background=False, timers=timers))):
    cache._forget_faults()
    for _ in range(100):
        cache.wake()
    assert timers.register.call_count == timers.unregister.call_count == 1
    with patch.object(cache, '_tick', return_value=cache.IDLE_INTERVAL):
        assert cache.tick() == cache.IDLE_INTERVAL
    cache.wake()
    assert timers.register.call_count == 2
    def wake_inside_tick():
        cache.wake()
        cache.wake()
        return cache.IDLE_INTERVAL
    with patch.object(cache, '_tick', side_effect=wake_inside_tick):
        assert cache.tick() == cache.ACTIVE_INTERVAL
    assert timers.register.call_count == 2, 'Never unregister the executing callback'
    timers.is_registered.return_value = False
    cache._forget_faults()
    cache.wake()
    assert not cache._wake_pending and timers.register.call_count == 2
cache._forget_faults()

shader_preview._sessions.pop(key)  # the metadata-only engine owns no GPU resources
open_painter.unregister()
print('DIAGNOSTICS_SCHEDULING_PASS', flush=True)
