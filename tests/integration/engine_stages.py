"""Engine stages: where an update's time goes, each stage on its own (build, update, CPU
inputs, translate, compile, render, publish), and a profile that times every pass on the GPU.

Headless (``gpu.init``). A first build records a build with every unit rebuilt; a numeric
edit records an update without a build, translation or compile; a structural edit in a
Mask Graph rebuilds that graph's unit alone and compiles its program again. The profile
times each unit's pass (a Mask Graph is a pass of its own), leaves the images as they
were, and fills the per-layer GPU figures of Profile Layers. The copied report names every
stage and the heaviest passes.
"""
import json
import sys
from collections import Counter
from pathlib import Path
from time import monotonic

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import layer_stack as stack, material_manager
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache, profile, shader_preview
from open_painter.evaluation.engine import runtime, stages
from open_painter.procedural_graph import templates
from open_painter.utils.ids import new_id


def ready(scene, ts, seconds=60):
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert not info['error'], info
        if shader_preview.current(scene, ts):
            return info
    raise AssertionError(shader_preview.diagnostics(scene, ts))


def rows(scene, ts):
    return list(shader_preview._sessions[shader_preview.key_for(scene, ts)].get('history', ()))


def completed_update(history):
    """One edit may publish over several budgeted Vulkan ticks; retain every stage."""
    assert history, 'the update must record an evaluation'
    combined = dict(history[-1])
    for field in (*stages.STAGES, 'builds', 'rebuilt', 'reused', 'updates',
                  'compiles', 'passes', 'total'):
        combined[field] = sum(row[field] for row in history)
    for field in ('render_by_kind', 'passes_by_kind'):
        totals = Counter()
        for row in history:
            totals.update(row[field])
        combined[field] = dict(totals)
    combined['published'] = [channel for row in history for channel in row['published']]
    assert not combined['deferred'], 'the completed update still holds channels'
    return combined


def layer_by_id(ts, identifier):
    return next(layer for layer in ts.layers if layer.id == identifier)


open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_plane_add(size=2)
ts = sets.create(scene, 'Engine stages', 512)
sets.assign(scene, ts, bpy.context.object)
base = stack.add(ts, 'FILL', 'Base')
stack.channel_for(base, 'BASE_COLOR').color = (.3, .3, .35, 1.)
rust_id = stack.add(ts, 'FILL', 'Rust').id
rust = layer_by_id(ts, rust_id)
for prop in rust.channels:
    prop.enabled = prop.channel in {'BASE_COLOR', 'ROUGHNESS'}
stack.channel_for(rust, 'BASE_COLOR').color = (.55, .25, .1, 1.)
mask = rust.masks.add()
mask_id = mask.id = new_id()
mask.source = 'GRAPH'
mask.node_group = templates.build('LAYERED_NOISE', 'Rust Graph')
material_manager.repair(ts)
report = {}

# ---- 1. the first build: every unit rebuilt, programs compiled, channels published --------------------
ready(scene, ts)
first = completed_update(rows(scene, ts))
assert first['builds'] == 1 and first['rebuilt'] > 0 and first['reused'] == 0, first
assert first['compiles'] > 0 and first['compile'] > 0 and first['translate'] > 0, first
assert first['passes'] > 0 and first['render'] > 0 and first['published'], first
assert first['publish'] > 0 and first['publish_path'], first
assert first['passes_by_kind'].get('GRAPH') == 1, first['passes_by_kind']
assert abs(first['total'] - sum(first[s] for s in stages.STAGES)) < 1e-6
report['first'] = first
print('ENGINE_STAGES first build: PASS', json.dumps({k: first[k] for k in ('total', 'build', 'compile', 'render')}),
      flush=True)

# ---- 2. a numeric edit: an update, no build, translation or compile -------------------------------------
count = len(rows(scene, ts))
layer_by_id(ts, rust_id).masks[0].strength = .6
ready(scene, ts)
numeric = completed_update(rows(scene, ts)[count:])
assert numeric['builds'] == 0 and numeric['updates'] >= 1 and numeric['update'] > 0, numeric
assert numeric['compiles'] == 0 and numeric['translate'] == 0 and numeric['compile'] == 0, numeric
assert numeric['published'] and numeric['render'] > 0, numeric
report['numeric'] = numeric
print('ENGINE_STAGES numeric edit: PASS', flush=True)

# ---- 3. a structural edit inside the Mask Graph: its unit alone is rebuilt ------------------------------
graph = layer_by_id(ts, rust_id).masks[0].node_group
multiply = next(node for node in graph.nodes if node.bl_idname == 'OPMGMath')
count = len(rows(scene, ts))
multiply.operation = 'MAXIMUM'
ready(scene, ts)
structural = completed_update(rows(scene, ts)[count:])
assert structural['builds'] == 1 and structural['rebuilt'] == 1 and structural['reused'] > 0, structural
assert structural['compiles'] >= 1 and structural['passes_by_kind'].get('GRAPH') == 1, structural
report['structural'] = structural
print('ENGINE_STAGES structural graph edit: PASS', flush=True)

# ---- 4. the profile: every pass timed on the GPU, the images unchanged ----------------------------------
engine = shader_preview.session_engine(scene, ts)
before = engine.render_channel('BASE_COLOR').copy()
count = len(rows(scene, ts))
profiled = shader_preview.profile_engine(scene, ts)
entry = shader_preview._sessions[shader_preview.key_for(scene, ts)]
units = entry['profile']['units']
graph_key = ('GRAPH', rust_id, mask_id)
assert profiled['profiled'] and len(rows(scene, ts)) == count + 1, profiled
assert graph_key in units and units[graph_key] > 0, sorted(units)
# Two octave-summed noises cost more than a flat fill's pass.
assert units[graph_key] >= min(ms for key, ms in units.items() if key[0] == 'CHANNEL'), units
# Uniform output proofs can omit compiled channel units entirely. Profile each
# executed pass once; skipped work must not be charged as a rendered pass.
assert set(units) == set(engine.rendered) and len(units) == len(engine.rendered), 'every executed pass is timed once'
if runtime.device().backend == 'VULKAN' and runtime.PIXEL_PUBLISH is None and runtime.device().pixel_publish:
    # The Vulkan pixel route's uniform proofs (runtime.VULKAN_PIXEL_ROUTE) skip a neutral channel's unit;
    # framebuffer publication renders it, as on every other backend.
    assert any(key not in engine.inline and key not in units for key in engine.states), 'neutral channel was not skipped'
assert abs(sum(units.values()) - profiled['render']) < 1e-6, (sum(units.values()), profiled['render'])
assert len(profiled['published']) == len([o for o in ts.outputs if o.image is not None]), profiled['published']
assert np.abs(engine.render_channel('BASE_COLOR') - before).max() == 0, 'profiling changed the result'
assert not engine.profiling, 'the profile must end'
statistics = stages.summary(rows(scene, ts))
assert statistics['total'][0] > 0 and statistics['render'][2] >= statistics['render'][0]
table = {row['layer_id']: row for row in profile.rows(scene, ts)}
assert table[rust_id]['gpu_ms'] > 0 and table[rust_id]['gpu_current'], table[rust_id]
report['profile'] = {repr(key): ms for key, ms in units.items()}
print('ENGINE_STAGES profile: PASS', flush=True)

# ---- 5. the report a tester pastes -------------------------------------------------------------------------
text = stages.report(scene, ts)
for needed in ('Open Painter engine report', 'Blender', 'Set "Engine stages"', 'Last update', 'build', 'update',
               'CPU inputs', 'translate', 'compile', 'render', 'publish', 'Profile', 'Mask Graph "Rust Graph"',
               'by layer', '"Rust"'):
    assert needed in text, (needed, text)
assert bpy.ops.open_painter.engine_report(action='COPY') == {'FINISHED'}
report['text'] = text
print(text, flush=True)
print('ENGINE_STAGES report: PASS', flush=True)

report['status'] = 'PASS'
destination = ROOT / '.blender-test' / 'engine-stages.json'
destination.parent.mkdir(exist_ok=True)
destination.write_text(json.dumps(report, indent=1, default=str))
print('ENGINE_STAGES_PASS', flush=True)
open_painter.unregister()
