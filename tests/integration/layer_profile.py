"""Profile Layers (0.9.0): per-layer compile, boundary and CPU cost with badges and states."""
import json
from pathlib import Path
import sys
from time import monotonic

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
# Exercise the retained CPU-boundary fallback; GPU filters have dedicated parity coverage.
from unittest.mock import patch
patch('open_painter.evaluation.engine.filters.supported', return_value=False).start()
from open_painter.core import layer_stack as stack, material_manager
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache, layer_cache, profile, shader_preview
from open_painter.ui.performance import CACHE_ICONS, badge, cache_icon
from open_painter.utils.ids import new_id


def ready(scene, ts):
    deadline = monotonic() + 60
    while monotonic() < deadline:
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert not info['error'], info
        if shader_preview.current(scene, ts):
            return info
    raise AssertionError(shader_preview.diagnostics(scene, ts))


def layer_by_id(ts, identifier):
    return next(layer for layer in ts.layers if layer.id == identifier)


open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_plane_add(size=2)
ts = sets.create(scene, 'Layer profile', 512)
sets.assign(scene, ts, bpy.context.object)

base = stack.add(ts, 'FILL', 'Base')
stack.channel_for(base, 'BASE_COLOR').color = (.5, .4, .3, 1.)
plain = stack.add(ts, 'FILL', 'Plain fill')
plain_id = plain.id
stack.channel_for(plain, 'ROUGHNESS').value = .4
blurred = stack.add(ts, 'FILL', 'Blurred')
blurred_id = blurred.id
stack.channel_for(blurred, 'BASE_COLOR').color = (.1, .2, .8, 1.)
effect = stack.channel_for(blurred, 'BASE_COLOR').effects.add()
effect.kind, effect.radius = 'GAUSSIAN_BLUR', 4.
checker = blurred.masks.add()
checker.id, checker.source, checker.procedural, checker.noise_scale = new_id(), 'PROCEDURAL', 'CHECKER', 6.
folder = stack.add(ts, 'FOLDER', 'Folder')
folder_id = folder.id
child_a = stack.add(ts, 'FILL', 'Child A')
child_a_id = child_a.id
child_a.parent_id = folder_id
stack.channel_for(child_a, 'BASE_COLOR').color = (.9, .1, .1, .7)
child_b = stack.add(ts, 'FILL', 'Child B')
child_b_id = child_b.id
child_b.parent_id = folder_id
child_b.blend_mode = 'MULTIPLY'
stack.channel_for(child_b, 'BASE_COLOR').color = (.4, .9, .4, 1.)
painted = stack.add(ts, 'PAINT', 'Painted')
painted_id = painted.id
material_manager.repair(ts)
ready(scene, ts)
shader_preview.flush(scene, ts)
ready(scene, ts)

# Compile costs are recorded by every build; boundary time by every resource step.
rows = {row['layer_id']: row for row in profile.rows(scene, ts)}
assert set(rows) == {layer.id for layer in ts.layers}
assert not rows[plain_id]['profiled'] and badge(scene, ts, layer_by_id(ts, plain_id)) is None
info = shader_preview.diagnostics(scene, ts)
for layer in ts.layers:
    expected = sum(ms for key, ms in info['unit_build_ms'].items() if eval(key)[1] == layer.id)
    assert abs(rows[layer.id]['compile_ms'] - expected) < 1e-6, (layer.name, rows[layer.id]['compile_ms'], expected)
    assert rows[layer.id]['state'] == 'LIVE', (layer.name, rows[layer.id])
    # An empty paint layer touches no channel and compiles no unit.
    assert (rows[layer.id]['nodes'] > 0) == (layer.id != painted_id), (layer.name, rows[layer.id])
assert rows[blurred_id]['boundary_ms'] > 0, rows[blurred_id]
assert rows[plain_id]['boundary_ms'] == 0

# The explicit pass composes the stack on the CPU with a per-layer timer.
with bpy.context.temp_override(scene=scene):
    assert bpy.ops.open_painter.profile_layers(resolution='256') == {'FINISHED'}
rows = {row['layer_id']: row for row in profile.rows(scene, ts)}
total = profile.total(scene, ts)
assert all(row['profiled'] and row['current'] for row in rows.values())
assert all(row['cpu_ms'] >= 0 and row['inclusive_ms'] >= row['cpu_ms'] - 1e-9 for row in rows.values())
children = rows[child_a_id]['inclusive_ms'] + rows[child_b_id]['inclusive_ms']
assert rows[folder_id]['inclusive_ms'] >= children - 1e-6, (rows[folder_id], children)
assert abs(sum(row['cost_ms'] for row in rows.values()) - total) < 1e-6
assert rows[blurred_id]['cost_ms'] > rows[plain_id]['cost_ms']
mark = badge(scene, ts, layer_by_id(ts, blurred_id))
assert mark is not None and mark[1] == 'ERROR' and mark[0].endswith('ms'), (mark, rows[blurred_id])
assert profile.table(scene, ts)['resolution'] == 256
print('LAYER_PROFILE_ROWS', json.dumps({row['name']: round(row['cost_ms'], 2) for row in rows.values()}), flush=True)

# The table walks every layer's ancestors with one shared id map: a sidebar
# redraw used to rebuild the map per layer, quadratic in the stack.
walks = []
original_bypassed = profile._bypassed
profile._bypassed = lambda ts_, layer_, by_id=None: (walks.append(by_id is None), original_bypassed(ts_, layer_, by_id))[1]
try:
    profile.rows(scene, ts)
finally:
    profile._bypassed = original_bypassed
assert len(walks) == len(ts.layers) and not any(walks), walks


def row_icon(layer):
    """The layer list's cache icon, read from the table instead of the shader session."""
    row = {row['layer_id']: row for row in profile.rows(scene, ts)}.get(layer.id)
    return CACHE_ICONS.get(row['state'], 'FILE_CACHE') if row is not None and layer.cache_mode != 'LIVE' else None


# States follow bypass and cache modes; an edit marks the CPU figures out of date.
layer_by_id(ts, blurred_id).bypass = True
ready(scene, ts)
rows = {row['layer_id']: row for row in profile.rows(scene, ts)}
assert rows[blurred_id]['state'] == 'BYPASSED' and not rows[blurred_id]['current']
assert cache_icon(scene, ts, layer_by_id(ts, blurred_id)) is None
assert row_icon(layer_by_id(ts, blurred_id)) is None
layer_by_id(ts, blurred_id).bypass = False
layer_by_id(ts, folder_id).cache_mode = 'CACHED'
ready(scene, ts)
assert profile.state(scene, ts, layer_by_id(ts, folder_id)) == 'PENDING'
assert cache_icon(scene, ts, layer_by_id(ts, folder_id)) == 'SORTTIME'
assert row_icon(layer_by_id(ts, folder_id)) == 'SORTTIME'
layer_cache.flush(scene)
ready(scene, ts)
assert profile.state(scene, ts, layer_by_id(ts, folder_id)) == 'CACHED', profile.state(scene, ts, layer_by_id(ts, folder_id))
assert cache_icon(scene, ts, layer_by_id(ts, folder_id)) == 'FILE_CACHE'
assert row_icon(layer_by_id(ts, folder_id)) == 'FILE_CACHE'
rows = {row['layer_id']: row for row in profile.rows(scene, ts)}
assert rows[folder_id]['refresh_ms'] > 0
layer_by_id(ts, folder_id).cache_mode = 'FROZEN'
ready(scene, ts)
layer_by_id(ts, child_a_id).opacity = .4
assert profile.state(scene, ts, layer_by_id(ts, folder_id)) == 'STALE'
assert cache_icon(scene, ts, layer_by_id(ts, folder_id)) == 'TIME'
assert row_icon(layer_by_id(ts, folder_id)) == 'TIME'
# A bypassed cached layer keeps the cache icon, as the shader-side lookup gave it.
layer_by_id(ts, folder_id).bypass = True
ready(scene, ts)
assert profile.state(scene, ts, layer_by_id(ts, folder_id)) == 'BYPASSED'
assert cache_icon(scene, ts, layer_by_id(ts, folder_id)) == 'FILE_CACHE'
assert row_icon(layer_by_id(ts, folder_id)) == 'FILE_CACHE'
with bpy.context.temp_override(scene=scene):
    assert bpy.ops.open_painter.profile_layers(action='CLEAR') == {'FINISHED'}
assert not profile.profiled(scene, ts)
print('LAYER_PROFILE_PASS', flush=True)
open_painter.unregister()
