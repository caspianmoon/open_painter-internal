"""A texture artist's session on a real asset file.

``open painter test.blend`` (ignored by git; ``OP_ARTIST_FILE`` overrides the
path) holds a ten-object imported prop, one 2048 texture set with a full
Worn Painted Metal stack (folder, generators, triplanar fills, an effect),
baked mesh maps and saved outputs. The suite reopens it the way the artist
does — the add-on registered, ``load_post`` running — and then works on it:
paints, adds mesh-driven and decal layers, a surface-space filter, mask
groups, caches the heavy folder, locks sizes to metres, runs Bake Check,
profiles, saves, reopens, exports and finally removes the set. Every step
checks what the artist would see; the saved outputs are the oracle for the
evaluation pipeline (the file was authored with the shipped release).

Skips (passes) when the file is absent, so the gate does not depend on it.
"""
import os
import sys
from pathlib import Path
from time import monotonic
from unittest.mock import patch

import bpy
import numpy as np
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
FILE = Path(os.environ.get('OP_ARTIST_FILE', ROOT / 'open painter test.blend'))
if not FILE.exists():
    print(f'ARTIST_FILE_SKIPPED {FILE}', flush=True)
    print('ARTIST_FILE_WORKFLOW_PASS', flush=True)
    sys.exit(0)

import open_painter
from open_painter.core import image_manager as images, layer_stack as stack, mask_stack, material_manager
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache, dependencies, layer_cache, physical, shader_preview
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.mask_eval import evaluate_masks
from open_painter.evaluation.references import issues, warnings
from open_painter.geometry.provenance import status
from open_painter.painting import decals, paint_session
from open_painter.utils.ids import new_id

open_painter.register()
bpy.ops.wm.open_mainfile(filepath=str(FILE))   # load_post: on_load runs as it does for the artist
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.context.scene
project = scene.open_painter
timings = {}


def timed(name, fn, limit):
    started = monotonic()
    result = fn()
    timings[name] = monotonic() - started
    assert timings[name] < limit, f'{name} took {timings[name]:.1f}s (limit {limit}s)'
    return result


def ready(ts, limit=180):
    deadline = monotonic() + limit
    while monotonic() < deadline:
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert not info.get('error'), info
        if shader_preview.current(scene, ts):
            return info
    raise AssertionError(shader_preview.diagnostics(scene, ts))


def cpu(ts, channel, n):
    steps = LayerEvaluator(scene).channel_steps(ts.id, channel, preview_resolution=n)
    try:
        while True:
            next(steps)
    except StopIteration as done:
        return images.pixels(done.value).copy()


def downsampled(image, n):
    px = images.pixels(image)
    f = px.shape[0] // n
    return px.reshape(n, f, n, f, 4).mean(axis=(1, 3))


def coverage(ts, layer, n=256):
    return np.asarray(evaluate_masks(ts, layer, n, 0, n, {}, {}), np.float32)


# ---- 0. opening a file keeps every handler (they must all be persistent) ---------------------
from open_painter.evaluation.cache import on_load, on_render, on_save, on_undo
for handlers, fn in ((bpy.app.handlers.depsgraph_update_post, dependencies.on_depsgraph),
                     (bpy.app.handlers.frame_change_post, dependencies.on_frame),
                     (bpy.app.handlers.load_post, on_load), (bpy.app.handlers.undo_post, on_undo),
                     (bpy.app.handlers.redo_post, on_undo),
                     (bpy.app.handlers.save_pre, on_save), (bpy.app.handlers.render_pre, on_render)):
    assert fn in handlers, f'{fn.__name__} was dropped by opening the file'
epoch = dependencies.epoch()
bpy.data.objects[0].location.x += 1e-3
bpy.context.view_layer.update()
assert dependencies.epoch() > epoch, 'the depsgraph handler no longer runs after opening a file'
bpy.data.objects[0].location.x -= 1e-3
bpy.context.view_layer.update()

# ---- 1. the file as saved -----------------------------------------------------------------
assert len(project.texture_sets) == 1
ts = project.texture_sets[0]
# This fixture was baked on CPU before the default changed to AUTO.
ts.bake.device = 'CPU'
set_id = ts.id
ts_of = lambda: next(t for t in project.texture_sets if t.id == set_id)
assert int(ts.resolution) == 2048 and ts.preview_backend == 'LIVE'
assert [l.type for l in ts.layers] == ['FOLDER'] + ['FILL'] * 5 and ts.layers[0].name == 'Worn Painted Metal'
assert all(l.parent_id == ts.layers[0].id for l in ts.layers[1:])
assert not issues(ts) and not warnings(ts), (issues(ts), warnings(ts))
low = next(r.object for r in ts.assignments if r.object)
assert low.type == 'MESH' and len(low.data.polygons) > 30000
statuses = {m.kind: status(ts, m) for m in ts.baked_maps}
assert all(v == 'Ready' or (k == 'CURVATURE' and v.startswith('Stale')) for k, v in statuses.items()), statuses
outputs = {o.channel: o.image for o in ts.outputs}
assert all(img is not None and tuple(img.size) == (2048, 2048) for img in outputs.values())
print('ARTIST_FILE_LOADED', {k: (v if v == 'Ready' else 'stale') for k, v in statuses.items()}, flush=True)

# The saved outputs are the oracle: the CPU reference at 512 must agree with them (box
# downsampled), so the evaluation pipeline has not drifted since the file was authored.
N = 512
baseline = {}
for channel, limit in (('BASE_COLOR', .02), ('ROUGHNESS', .02), ('METALLIC', .03), ('HEIGHT', .01), ('NORMAL', .02)):
    fresh = timed(f'cpu {channel}', lambda: cpu(ts, channel, N), 120)
    stored = downsampled(outputs[channel], N)
    assert np.isfinite(fresh).all()
    error = float(np.abs(fresh[..., :3] - stored[..., :3]).mean())
    assert error < limit, (channel, error)
    baseline[channel] = fresh
assert baseline['BASE_COLOR'][..., :3].std() > .02, 'a textured result, not a flat fill'
print('ARTIST_FILE_OUTPUT_PARITY_OK', flush=True)

# The native shader session compiles the stack without warnings.
material_manager.repair(ts)
info = timed('shader session', lambda: ready(ts), 180)
assert not info['warnings'] and info['graph_builds'] == 1, info
print('ARTIST_FILE_SESSION_OK', {k: info[k] for k in ('units', 'unique_images', 'build_ms')}, flush=True)

# Moving an unrelated object in the scene must not invalidate the set.
serial = ts.edit_serial
other = next(o for o in scene.objects if o.type == 'MESH' and o != low)
other.location.x += .1
bpy.context.view_layer.update()
dependencies._candidates.add(scene.as_pointer())
dependencies.refresh(scene)
assert ts.edit_serial == serial, 'an unrelated transform invalidated the set'

# ---- 2. hand painting on top of the stack ----------------------------------------------------
paint = stack.add(ts, 'PAINT', 'Hand-painted grime')
paint_id = paint.id
layer_of = lambda identifier: next(l for l in ts_of().layers if l.id == identifier)
ts.active_layer = stack.index_of(ts, paint_id)
with bpy.context.temp_override(scene=scene, object=low, active_object=low):
    canvas = paint_session.begin(bpy.context, ts, 'BASE_COLOR')
assert project.paint_image == canvas and tuple(canvas.size) == (2048, 2048)
assert stack.channel_for(layer_of(paint_id), 'BASE_COLOR').image == canvas
pointer = scene.as_pointer()
block = images.pixels(canvas)
block[900:1100, 900:1100] = (.05, .6, .1, 1.)   # an opaque green dab in UV space
with patch.object(cache, '_modal_painting', lambda s: True):
    canvas.pixels.foreach_set(block.ravel())
    canvas.update()
    cache._paint_input[pointer] = (True, monotonic())
    cache._live_checks.pop(pointer, None)
    cache.tick()
    assert shader_preview.diagnostics(scene, ts)['frozen'], 'heavy work waits for the stroke to end'
cache._paint_input[pointer] = (True, monotonic() - .2)
cache._live_checks.pop(pointer, None)
cache.tick()
with bpy.context.temp_override(scene=scene, object=low, active_object=low):
    paint_session.finish(bpy.context)
assert project.paint_image is None
ready(ts)
painted = cpu(ts, 'BASE_COLOR', N)
dab = painted[N // 2 - 20:N // 2 + 20, N // 2 - 20:N // 2 + 20, :3].mean(axis=(0, 1))
assert dab[1] > .5 and dab[0] < .15, dab
outside = np.abs(painted[:N // 4, :N // 4, :3] - baseline['BASE_COLOR'][:N // 4, :N // 4, :3]).mean()
assert outside < 1e-3, 'painting changed texels outside the dab'
print('ARTIST_FILE_PAINT_OK', flush=True)

# ---- 3. mesh-driven and decal layers -----------------------------------------------------------
rust = stack.add(ts, 'FILL', 'Rust on the upward faces')
rust_id = rust.id
stack.channel_for(rust, 'BASE_COLOR').color = (.35, .12, .05, 1.)
mesh_mask = rust.masks.add()
mesh_mask.id, mesh_mask.name, mesh_mask.source = new_id(), 'Facing up', 'MESH'
mesh_mask.mesh.kind, mesh_mask.mesh.object = 'OBJECT', low
full = coverage(ts, layer_of(rust_id))
assert full.max() > .99 and full.mean() > .3, full.mean()
mesh_mask.mesh.kind = 'NORMAL_DIRECTION'
mesh_mask.mesh.direction, mesh_mask.mesh.angle, mesh_mask.mesh.softness = (0., 0., 1.), .6, .3
up = coverage(ts, layer_of(rust_id))
assert 0 < up.mean() < full.mean(), (up.mean(), full.mean())

decal_image = images.create('Caution decal', 64, (1., .8, 0., 1.))
px = images.pixels(decal_image)
px[:, 32:, :3] = (0., 0., 0.)
images.write(decal_image, px, pack=False)
# The artist drops the decal on a surface: the 3D cursor sits just above an upward-facing texel
# (the prop is ring-shaped, so its centre is empty space).
from open_painter.painting.projection import geometry
positions, normals, on_mesh = geometry(ts, 256, coverage=True)
upward = on_mesh & (normals[..., 2] > .9)
spot = positions[upward][np.argmax(positions[upward][:, 2])] if upward.any() else positions[on_mesh][np.argmax(positions[on_mesh][:, 2])]
extent = max(low.dimensions) or 1.
scene.cursor.location = (spot[0], spot[1], spot[2] + extent * .02)
with bpy.context.temp_override(scene=scene, object=low, active_object=low):
    assert bpy.ops.open_painter.decal(action='ADD', name='Caution_01', image=decal_image.name, size=extent * .15) == {'FINISHED'}
decal_layer = ts.layers[ts.active_layer]
decal_id = decal_layer.id
empty = decals.resolve(decal_layer)
assert empty is not None and empty.name == 'OP_Decal_Caution_01' and empty.data == decal_image
layer_of(decal_id).decal_depth = extent * .1
decal_rgba = np.asarray(decals.sample(stack.channel_for(layer_of(decal_id), 'BASE_COLOR'), layer_of(decal_id), 256, 0, 256, ts, {}, {}), np.float32)
stamped = decal_rgba[..., 3][on_mesh]
assert stamped.max() > .99 and 0 < stamped.mean() < .5, stamped.mean()
ready(ts)
print('ARTIST_FILE_MESH_DECAL_OK', float(up.mean()), float(stamped.mean()), flush=True)

# ---- 4. a surface-space filter and a mask group on the catalog layers -------------------------
grime = stack.channel_for(layer_of(paint_id), 'BASE_COLOR')
blur = grime.effects.add()
blur.kind, blur.radius, blur.space = 'GAUSSIAN_BLUR', 3., 'SURFACE'
blurred = timed('surface blur 256', lambda: cpu(ts, 'BASE_COLOR', 256), 240)
assert np.isfinite(blurred).all()
blue = next(l for l in ts.layers if l.name == 'Blue paint coat')
blue_id = blue.id
before_group = coverage(ts, blue)
noise = blue.masks.add()
noise.id, noise.name, noise.source, noise.operation, noise.noise_scale = new_id(), 'Speckle', 'NOISE', 'MULTIPLY', 24.
group = blue.masks.add()
group.id, group.name, group.source, group.strength = new_id(), 'Chip wear', 'GROUP', .5
group_index = len(blue.masks) - 1
blue.masks.move(group_index, 0)
for m in list(layer_of(blue_id).masks)[1:]:
    m.parent_id = layer_of(blue_id).masks[0].id
assert mask_stack.block(list(layer_of(blue_id).masks), 0) == (0, 3)
grouped = coverage(ts, layer_of(blue_id))
assert np.isfinite(grouped).all() and abs(grouped.mean() - before_group.mean()) < .5
assert not np.allclose(grouped, before_group), 'the group changed the coverage'
ready(ts)
print('ARTIST_FILE_FILTER_GROUP_OK', flush=True)

# ---- 5. performance tools: profile, cache the heavy folder, bypass -----------------------------
with bpy.context.temp_override(scene=scene):
    assert bpy.ops.open_painter.profile_layers(resolution='256') == {'FINISHED'}
from open_painter.evaluation import profile
rows = profile.rows(scene, ts)
assert {row['layer_id'] for row in rows} == {l.id for l in ts.layers}
folder = ts.layers[0]
folder_id = folder.id
folder.cache_mode = 'CACHED'
ready(ts)
timed('folder cache', lambda: layer_cache.flush(scene, ts), 300)
states = shader_preview.session_graph(scene, ts).cache_states()
cached_states = {k: v for k, v in states.items() if k.startswith(folder_id)}
assert cached_states and all(v == 'CURRENT' for v in cached_states.values()), states
# Only the channels the folder's children touch are cached: no 64 MiB image for a pass-through.
touched = {p.channel for l in ts.layers if l.parent_id == folder_id for p in l.channels if p.enabled}
assert {k.split(':')[1] for k in cached_states} == touched, (cached_states, touched)
assert sum(1 for row in layer_of(folder_id).cache if row.image is not None) == len(touched)
assert all(row.image is not None and tuple(row.image.size) == (2048, 2048)
           for row in layer_of(folder_id).cache if row.image is not None)
export_view = cpu(ts, 'BASE_COLOR', 256)
layer_of(decal_id).bypass = True
ready(ts)
assert np.array_equal(cpu(ts, 'BASE_COLOR', 256), export_view), 'bypass is preview-only'
layer_of(decal_id).bypass = False
print('ARTIST_FILE_PERFORMANCE_OK', flush=True)

# ---- 6. physical scale: lock the material to metres --------------------------------------------
tile = physical.density(ts)
assert tile is not None and tile > 0, tile
before_lock = {m.name: m.noise_scale for l in ts.layers for m in l.masks if m.source in {'NOISE', 'GENERATOR', 'PROCEDURAL'}}
count = physical.lock(ts)
assert count > 0 and physical.uses(ts)
assert physical.sync(ts) == 0, 'locking keeps the current look'
after_lock = {m.name: m.noise_scale for l in ts.layers for m in l.masks if m.source in {'NOISE', 'GENERATOR', 'PROCEDURAL'}}
assert all(abs(after_lock[k] - v) <= 1e-3 * max(1., v) for k, v in before_lock.items()), (before_lock, after_lock)
assert 'Texel' in physical.describe(ts)
ready(ts)
states = shader_preview.session_graph(scene, ts).cache_states()
assert all(v == 'CURRENT' for k, v in states.items() if k.startswith(folder_id)), (states, 'locking sizes keeps the look, so caches stay current')
print('ARTIST_FILE_PHYSICAL_OK', physical.describe(ts), flush=True)

# ---- 7. Bake Check on the real mesh ------------------------------------------------------------
from open_painter.baking import doctor
report = timed('bake check', lambda: doctor.examine(ts, 'PRE_BAKE'), 120)
assert not report.errors, [f.message for f in report.errors]
full_report = timed('bake check full', lambda: doctor.examine(ts, 'FULL'), 300)
assert not full_report.errors, [f.message for f in full_report.errors]
with bpy.context.temp_override(scene=scene, object=low, active_object=low):
    doctor.select_all(ts, full_report, bpy.context)
print('ARTIST_FILE_BAKE_CHECK_OK', report.summary(), '|', full_report.summary(), flush=True)

# ---- 8. save, reopen, keep working -------------------------------------------------------------
before_save = cpu(ts, 'BASE_COLOR', 256)
roundtrip = ROOT / '.blender-test' / 'artist-file-roundtrip.blend'
timed('save', lambda: bpy.ops.wm.save_as_mainfile(filepath=str(roundtrip), copy=False), 300)
bpy.ops.wm.open_mainfile(filepath=str(roundtrip))
scene = bpy.context.scene
project = scene.open_painter
ts = ts_of()
low = next(r.object for r in ts.assignments if r.object)
names = [l.name for l in ts.layers]
originals = ['Worn Painted Metal', 'Steel substrate', 'Oxide beneath paint', 'Blue paint coat', 'Scratched exposed steel', 'Recess dirt']
assert [n for n in names if n in originals] == originals, names
assert [l.name for l in ts.layers if l.parent_id == folder_id] == originals[1:], names
assert {l.name for l in ts.layers if not l.parent_id} == {'Worn Painted Metal', 'Hand-painted grime', 'Rust on the upward faces', 'Caution_01'}
assert not issues(ts) and not warnings(ts)
statuses = {m.kind: status(ts, m) for m in ts.baked_maps}
assert all(v == 'Ready' or (k == 'CURVATURE' and v.startswith('Stale')) for k, v in statuses.items()), statuses
assert stack.channel_for(layer_of(paint_id), 'BASE_COLOR').image is not None
assert decals.resolve(layer_of(decal_id)) is not None and layer_of(decal_id).decal_object_name == 'OP_Decal_Caution_01'
assert layer_of(rust_id).masks[0].mesh.kind == 'NORMAL_DIRECTION'
assert mask_stack.block(list(layer_of(blue_id).masks), 0) == (0, 3)
assert layer_of(folder_id).cache_mode == 'CACHED' and any(row.image is not None for row in layer_of(folder_id).cache)
assert physical.uses(ts)
reopened = cpu(ts, 'BASE_COLOR', 256)
assert np.abs(reopened - before_save).max() < 1e-3, float(np.abs(reopened - before_save).max())
material_manager.repair(ts)
info = ready(ts)
assert not info['warnings'], info['warnings']
states = shader_preview.session_graph(scene, ts).cache_states()
assert all(v == 'CURRENT' for k, v in states.items() if k.startswith(folder_id)), states
print('ARTIST_FILE_ROUNDTRIP_OK', flush=True)

# ---- 9. export for a game engine --------------------------------------------------------------
from open_painter.export.exporter import export
from tempfile import TemporaryDirectory
ts.export.preset, ts.export.file_format, ts.export.resolution = 'UNREAL', 'PNG', '1024'
with TemporaryDirectory(prefix='op-artist-') as directory:
    with bpy.context.temp_override(scene=scene):
        written = timed('export', lambda: export(bpy.context, ts, ts.export, directory=directory), 600)
    assert written and all(p.exists() and p.stat().st_size > 1000 for p in written), written
    names = sorted(p.name for p in written)
    # Exporting below the authored resolution box-filters the 2048 outputs (no point sampling).
    exported = bpy.data.images.load(str(next(p for p in written if p.name.endswith('_BaseColor.png'))))
    linear = np.clip(downsampled(next(o.image for o in ts.outputs if o.channel == 'BASE_COLOR'), 1024)[..., :3], 0, 1)
    srgb = np.where(linear <= .0031308, 12.92 * linear, 1.055 * linear ** (1 / 2.4) - .055)   # 8-bit files store sRGB
    # Since 0.13 the exporter filters over the texels the geometry covers and pads the gutters:
    # the plain box filter is the reference where a 2×2 source block is fully covered.
    from open_painter.export import padding as export_padding
    whole = export_padding.ownership(ts, 2048).covered.reshape(1024, 2, 1024, 2).all(axis=(1, 3))
    assert whole.sum() > 1000, int(whole.sum())
    error = float(np.abs(images.pixels(exported)[..., :3] - srgb)[whole].mean())
    assert error < .01, error
    bpy.data.images.remove(exported)
print('ARTIST_FILE_EXPORT_OK', names, flush=True)

# ---- 10. removing the set leaves no owned images behind ----------------------------------------
from open_painter.evaluation.layer_cache import TAG as CACHE_TAG
from open_painter.baking.corrections import TAG as CORRECTION_TAG
assert any(img.get(CACHE_TAG) for img in bpy.data.images)
sets.remove(scene, next(i for i, t in enumerate(project.texture_sets) if t.id == set_id))
assert not any(img.get(CACHE_TAG) or img.get(CORRECTION_TAG) for img in bpy.data.images)
print('ARTIST_FILE_TIMINGS', {k: round(v, 1) for k, v in timings.items()}, flush=True)
print('ARTIST_FILE_WORKFLOW_PASS', flush=True)
open_painter.unregister()
