"""Baking on the real asset file: iterate at a lower resolution, rebake mesh maps, fix a bake
with the correction brush, and act on Bake Check findings.

Companion of ``artist_file_workflow.py`` (same file, same skip rule). The artist drops the
set to 512 to iterate, bakes Normal/AO/Curvature and the utility maps from the low-poly
itself (no high-poly in this file), watches the generators pick the new maps up, paints a
projection-distance correction, region-rebakes, clears it, then selects what Bake Check
reported.
"""
import os
import sys
from pathlib import Path
from time import monotonic

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
FILE = Path(os.environ.get('OP_ARTIST_FILE', ROOT / 'open painter test.blend'))
if not FILE.exists():
    print(f'ARTIST_FILE_SKIPPED {FILE}', flush=True)
    print('ARTIST_FILE_BAKING_PASS', flush=True)
    sys.exit(0)

import open_painter
from open_painter.baking import corrections, doctor
from open_painter.baking.bake_manager import bake_selected
from open_painter.core import image_manager as images, layer_stack as stack
from open_painter.evaluation import cache
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.mask_eval import evaluate_masks
from open_painter.geometry.provenance import status

open_painter.register()
bpy.ops.wm.open_mainfile(filepath=str(FILE))
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.context.scene
project = scene.open_painter
ts = project.texture_sets[0]
# This fixture was baked on CPU before the default changed to AUTO.
ts.bake.device = 'CPU'
low = next(r.object for r in ts.assignments if r.object)
timings = {}


def timed(name, fn, limit):
    started = monotonic()
    result = fn()
    timings[name] = monotonic() - started
    assert timings[name] < limit, f'{name} took {timings[name]:.1f}s (limit {limit}s)'
    return result


def cpu(channel, n):
    steps = LayerEvaluator(scene).channel_steps(ts.id, channel, preview_resolution=n)
    try:
        while True:
            next(steps)
    except StopIteration as done:
        return images.pixels(done.value).copy()


def coverage(layer, n=256):
    return np.asarray(evaluate_masks(ts, layer, n, 0, n, {}, {}), np.float32)


# ---- 1. iterate at a lower resolution: the maps are stale until rebaked ------------------------
maps = {m.kind: m for m in ts.baked_maps}
assert status(ts, maps['NORMAL']) == 'Ready'
ts.resolution = '512'
assert status(ts, maps['NORMAL']).startswith('Stale'), status(ts, maps['NORMAL'])
generator_layers = [l for l in ts.layers if any(m.source == 'GENERATOR' for m in l.masks)]
assert len(generator_layers) >= 3
before = {l.id: coverage(l) for l in generator_layers}
assert all(np.isfinite(v).all() for v in before.values()), 'stale maps still evaluate (resampled)'

# ---- 2. bake the mesh maps from the low-poly itself -------------------------------------------
settings = ts.bake
settings.selected_to_active = False
settings.normal = settings.ao = settings.curvature = True
settings.world_normal = settings.position = True
settings.samples, settings.margin = 4, 4
scene.render.engine, scene.cycles.device = 'CYCLES', 'CPU'
with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0], object=low, active_object=low):
    timed('bake', lambda: bake_selected(bpy.context, ts), 900)
maps = {m.kind: m for m in ts.baked_maps}
for kind in ('NORMAL', 'AO', 'CURVATURE', 'WORLD_NORMAL', 'POSITION'):
    prop = maps[kind]
    assert prop.image is not None and tuple(prop.image.size) == (512, 512), kind
    assert status(ts, prop) == 'Ready', (kind, status(ts, prop))
ao = images.pixels(maps['AO'].image)[..., 0]
assert ao.std() > .02 and ao.max() > .9, (ao.std(), ao.max())   # a ring-shaped prop occludes itself
normal = images.pixels(maps['NORMAL'].image)[..., :3]
assert np.abs(normal.mean(axis=(0, 1)) - (.5, .5, 1.)).max() < .15, normal.mean(axis=(0, 1))
assert doctor.last(ts) is not None and not doctor.last(ts).errors
after = {l.id: coverage(l) for l in generator_layers}
assert all(0 < v.mean() < 1 for v in after.values()), {k: float(v.mean()) for k, v in after.items()}
assert any(not np.allclose(after[k], before[k], atol=1e-3) for k in after), 'fresh maps change the generators'
colour = cpu('BASE_COLOR', 256)
assert np.isfinite(colour).all() and colour[..., :3].std() > .02
# Arranging the scene is not a bake input: moving or turning the prop keeps the surface maps
# current; only the maps that read where it stands (AO of the assembly, world axes) go stale.
# Resizing it changes world sizes, so everything goes stale.
low.location.x += .5
low.rotation_euler.z += .7
bpy.context.view_layer.update()
placement = {kind: status(ts, maps[kind]) for kind in ('NORMAL', 'CURVATURE', 'AO', 'WORLD_NORMAL', 'POSITION')}
assert placement['NORMAL'] == 'Ready' and placement['CURVATURE'] == 'Ready', placement
assert all(placement[k].startswith('Stale') for k in ('AO', 'WORLD_NORMAL', 'POSITION')), placement
low.scale = (1.5, 1.5, 1.5)
bpy.context.view_layer.update()
assert all(status(ts, maps[k]).startswith('Stale') for k in ('NORMAL', 'CURVATURE'))
low.scale = (1., 1., 1.)
low.location.x -= .5
low.rotation_euler.z -= .7
bpy.context.view_layer.update()
restored = {k: status(ts, maps[k]) for k in ('NORMAL', 'CURVATURE', 'AO', 'WORLD_NORMAL', 'POSITION')}
assert all(v == 'Ready' for v in restored.values()), restored
print('ARTIST_FILE_BAKE_OK', {k: round(v, 1) for k, v in timings.items()}, flush=True)

# ---- 3. the correction brush: paint projection distance, region-rebake, clear -----------------
with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0], object=low, active_object=low):
    offset = corrections.begin_brush(bpy.context, ts, 'OFFSET')
assert project.paint_correction == 'OFFSET' and project.paint_image is offset and low.mode == 'TEXTURE_PAINT'
assert tuple(offset.size) == (512, 512)
normal_before = images.pixels(maps['NORMAL'].image).copy()
data = images.pixels(offset)
data[200:260, 200:260, :3] = 1.0   # full outward projection distance on a patch
offset.pixels.foreach_set(data.ravel())
offset.update()
assert corrections.stroke_finished(scene)
assert status(ts, maps['NORMAL']).startswith('Stale'), 'a painted correction marks the map stale'
with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0], object=low, active_object=low):
    corrections.end_brush(bpy.context)
    result = timed('region rebake', lambda: corrections.rebake_region(bpy.context, ts, ['NORMAL']), 600)
assert result['NORMAL'] > 0, result
assert status(ts, maps['NORMAL']) == 'Ready'
assert maps['NORMAL'].image is not None and tuple(maps['NORMAL'].image.size) == (512, 512)
normal_after = images.pixels(maps['NORMAL'].image)
changed = np.abs(normal_after - normal_before)[..., :3].max(axis=-1) > 1e-3
assert changed.sum() < changed.size * .5, 'a region rebake touches only the painted faces'
assert corrections.rebake_region(bpy.context, ts, ['NORMAL']) == {'NORMAL': 0}
assert project.paint_image is None and low.mode == 'OBJECT'
with bpy.context.temp_override(scene=scene):
    assert bpy.ops.open_painter.bake_correction(action='CLEAR_ALL') == {'FINISHED'}
assert not corrections.active_keys(ts)
assert status(ts, maps['NORMAL']).startswith('Stale'), 'clearing a used correction is a bake input change'
print('ARTIST_FILE_CORRECTION_OK', flush=True)

# ---- 4. Bake Check findings select the geometry ------------------------------------------------
report = doctor.examine(ts, 'FULL')
selectable = [f for f in report.findings if f.selectable]
assert selectable, report.summary()
finding = selectable[0]
with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0], object=low, active_object=low):
    message = doctor.select(ts, finding, bpy.context)
assert message.startswith('selected'), message
target = bpy.data.objects[finding.object_name]
if finding.faces:
    flags = np.empty(len(target.data.polygons), bool)
    target.data.polygons.foreach_get('select', flags)
    assert flags.sum() == len(set(finding.faces)), (flags.sum(), len(finding.faces))
elif finding.vertices:
    flags = np.empty(len(target.data.vertices), bool)
    target.data.vertices.foreach_get('select', flags)
    assert flags.sum() == len(set(finding.vertices))
print('ARTIST_FILE_BAKE_CHECK_SELECT_OK', finding.check, message, flush=True)
print('ARTIST_FILE_BAKING_PASS', flush=True)
open_painter.unregister()
