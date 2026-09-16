"""Bake Correction Brush (0.9.1): painted projection distance, source exclusion and region rebakes."""
import math
import sys
from pathlib import Path

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.baking import corrections
from open_painter.baking.bake_manager import register_map
from open_painter.baking.cycles_baker import bake
from open_painter.core import image_manager as images, texture_set_manager as sets
from open_painter.geometry.provenance import status


def cube(name, size, subdivisions=0):
    bpy.ops.mesh.primitive_cube_add(size=size)
    obj = bpy.context.object
    obj.name = name
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    if subdivisions:
        bpy.ops.mesh.subdivide(number_cuts=subdivisions)
    bpy.ops.uv.smart_project(island_margin=.03)
    bpy.ops.object.mode_set(mode='OBJECT')
    return obj


open_painter.register()
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
low = cube('Body_low', 2.0, 2)   # interior top vertices belong to the top island only
high = cube('Body_high', 2.0, 2)
bpy.ops.mesh.primitive_cube_add(size=1.0)
plate = bpy.context.object
plate.name = 'Plate_high'
plate.scale = (.5, .5, .02)
plate.location = (0, 0, 1.3)   # entirely above the 0.1 cage, below a 0.5 one
plate.rotation_euler = (math.radians(25), 0, 0)
bpy.ops.object.transform_apply(scale=True, rotation=True)
ts = sets.create(scene, 'Corrections', 512)
sets.assign(scene, ts, low)
for obj in (high, plate):
    ref = ts.bake.high_objects.add()
    ref.object, ref.name = obj, obj.name
settings = ts.bake
settings.selected_to_active = True
settings.cage_mode = 'AUTO'
settings.separate_cage_distance = True
settings.cage_extrusion = .1
settings.ray_distance = 1.0
settings.samples = 1
settings.margin = 2
settings.normal, settings.ao, settings.curvature = True, False, False
scene.render.engine = 'CYCLES'
n = int(ts.resolution)

# The top island's UV footprint is where the plate would project.
tops = [p for p in low.data.polygons if p.normal.z > .9]
uv = low.data.uv_layers.active
corners = np.asarray([uv.data[i].uv[:] for p in tops for i in p.loop_indices], np.float32)
u0, v0 = corners.min(0); u1, v1 = corners.max(0)
rows = slice(int(v0 * n) + 2, int(v1 * n) - 2)
cols = slice(int(u0 * n) + 2, int(u1 * n) - 2)
inside = np.zeros((n, n), bool); inside[rows, cols] = True

baseline = images.pixels(bake(bpy.context, ts, 'NORMAL'))[..., :3].copy()
assert not corrections.active_keys(ts)
flat = np.abs(baseline[inside] - (.5, .5, 1.)).max()
assert flat < .02, f'without corrections the top face bakes the flat cube top ({flat})'

# Projection Distance painted white over the top face lifts the cage above the plate.
ts.bake.corrections.offset_range = .4
offset = corrections.image_of(ts, 'OFFSET', create_missing=True)
data = images.pixels(offset)
data[rows, cols, :3] = 1.0
images.write(offset, data, pack=False)
assert corrections.refresh_used(offset) and corrections.active_keys(ts) == ['OFFSET']
weights = corrections.vertex_weights(low.data, {'OFFSET': data[..., 0]})['OFFSET']
top_vertices = {v for p in tops for v in p.vertices}
lifted = [v for v in top_vertices if abs(low.data.vertices[v].co.x) < .99 and abs(low.data.vertices[v].co.y) < .99]
others = [v for v in range(len(low.data.vertices)) if v not in top_vertices]
assert lifted and all(weights[v] > .9 for v in lifted), weights[lifted]
assert weights[others].max() < .51, weights[others].max()
corrected = images.pixels(bake(bpy.context, ts, 'NORMAL'))[..., :3].copy()
changed = np.abs(corrected - baseline).max(-1) > .05
# The plate covers 0.5 x 0.46 of the 2 x 2 face: about 6 % of the island.
assert .04 < changed[inside].mean() < .12, f'the plate must appear where the distance was painted ({changed[inside].mean():.2f})'
outside = ~corrections.dilate(inside, settings.margin + 2)
assert not changed[outside].any(), f'{changed[outside].sum()} texels changed outside the painted region'
print('BAKE_CORRECTIONS_OFFSET_OK', flush=True)

# The corrected cage is what a hand-made cage would be: top vertices raised by extrusion + range.
from open_painter.baking.render_geometry import scope
with scope(bpy.context, [low]) as frozen:
    cage = corrections.corrected_cage(ts, frozen[low], corrections.world_rasters(ts))
    positions = np.empty((len(cage.data.vertices), 3), np.float32); cage.data.vertices.foreach_get('co', positions.ravel())
    for v in lifted:
        assert positions[v][2] > 1.0 + .1 + .3, positions[v]
    bpy.data.meshes.remove(cage.data)

# Region rebake: only the faces whose corrections changed are rebaked, into the same image.
prop = None
register_map(ts, 'NORMAL', bake(bpy.context, ts, 'NORMAL'))
prop = next(m for m in ts.baked_maps if m.kind == 'NORMAL')
assert status(ts, prop) == 'Ready'
before_image = prop.image
data = images.pixels(offset)
data[rows, cols, :3] = .5     # paint the correction away again
images.write(offset, data, pack=False)
corrections.refresh_used(offset)
assert status(ts, prop).startswith('Stale'), 'painting a correction marks the map stale'
result = corrections.rebake_region(bpy.context, ts, ['NORMAL'])
assert result['NORMAL'] > 0 and prop.image is before_image, result
rebaked = images.pixels(prop.image)[..., :3]
assert np.abs(rebaked - baseline).max() < .02, float(np.abs(rebaked - baseline).max())
assert status(ts, prop) == 'Ready'
assert corrections.rebake_region(bpy.context, ts, ['NORMAL']) == {'NORMAL': 0}
print('BAKE_CORRECTIONS_REGION_OK', flush=True)

# Ignore High Poly: where painted, the ignorable plate leaves the projection.
data[rows, cols, :3] = 1.0
images.write(offset, data, pack=False); corrections.refresh_used(offset)
ts.bake.high_objects[1].ignorable = True
ignore = corrections.image_of(ts, 'IGNORE', create_missing=True)
mask = images.pixels(ignore)
half = slice(cols.start, (cols.start + cols.stop) // 2)
mask[rows, half, :3] = 1.0
images.write(ignore, mask, pack=False); corrections.refresh_used(ignore)
ignored = images.pixels(bake(bpy.context, ts, 'NORMAL'))[..., :3]
left = np.zeros((n, n), bool); left[rows, half] = True
right = inside & ~left
assert np.abs(ignored[left] - baseline[left]).max() < .02, 'ignored region must match the bake without the plate'
assert np.abs(ignored[right] - corrected[right]).max() < .02, 'unpainted region keeps the plate'
print('BAKE_CORRECTIONS_IGNORE_OK', flush=True)

# Brush session: targets the correction image, survives synchronize, ends cleanly.
from open_painter.painting import paint_session
project = scene.open_painter
image = corrections.begin_brush(bpy.context, ts, 'OFFSET')
assert image is offset and project.paint_correction == 'OFFSET' and project.paint_image is offset
assert low.mode == 'TEXTURE_PAINT' and scene.tool_settings.image_paint.canvas is offset
assert paint_session.synchronize(bpy.context)
ts.bake.corrections.value = .1
assert abs(ts.bake.corrections.value - .1) < 1e-4
from open_painter.evaluation import shader_preview
assert shader_preview.native_paint_input(scene) is True
data = images.pixels(offset); data[0:8, 0:8, :3] = .9
offset.pixels.foreach_set(data.ravel()); offset.update()
assert corrections.stroke_finished(scene) and offset['open_painter_pixel_revision'] >= 1
assert corrections.pending_rebake(scene) is None
import time; time.sleep(corrections.SETTLE + .05)
assert corrections.pending_rebake(scene).id == ts.id
overlays = [o for o in scene.objects if o.get(corrections.OVERLAY)]
assert overlays and overlays[0].hide_select
ts.bake.corrections.active = 'SKEW'
assert project.paint_correction == 'SKEW' and project.paint_image is corrections.image_of(ts, 'SKEW')
corrections.end_brush(bpy.context)
assert not project.paint_correction and project.paint_image is None and low.mode == 'OBJECT'
assert not [o for o in scene.objects if o.get(corrections.OVERLAY)]
with bpy.context.temp_override(scene=scene):
    assert bpy.ops.open_painter.bake_correction(action='CLEAR_ALL') == {'FINISHED'}
assert not corrections.active_keys(ts) and ts.bake.corrections.offset_image is None
print('BAKE_CORRECTIONS_PASS', flush=True)
open_painter.unregister()
