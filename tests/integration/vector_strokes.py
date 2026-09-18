"""Vector strokes: one stroke record rasterises on every set it touches (universal canvas),
crosses UV seams, re-rasterises on edits and resolution changes, and undoes as data."""
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.core import texture_set_manager as sets, layer_stack as stack, image_manager as images
from open_painter.evaluation import dependencies
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.painting import stroke_raster, strokes as records
from open_painter.utils.ids import new_id

open_painter.register()
scene = bpy.context.scene
project = scene.open_painter
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
# A cube with two material slots: the top half and the bottom half belong to different sets.
bpy.ops.mesh.primitive_cube_add(size=2)
cube = bpy.context.object
bpy.ops.object.mode_set(mode='EDIT'); bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.mesh.subdivide(number_cuts=1)   # side faces split at z = 0: the seam between the sets
bpy.ops.uv.smart_project(island_margin=.03); bpy.ops.object.mode_set(mode='OBJECT')
cube.data.materials.append(bpy.data.materials.new('Lower')); cube.data.materials.append(bpy.data.materials.new('Upper'))
for polygon in cube.data.polygons:
    polygon.material_index = 1 if polygon.center.z > 0 else 0
# A housing around the cube in a third set: its bounds enclose every stroke on the cube, but
# no stroke reaches its surface.
bpy.ops.mesh.primitive_cube_add(size=6)
housing_object = bpy.context.object
bpy.ops.object.mode_set(mode='EDIT'); bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.uv.smart_project(island_margin=.03); bpy.ops.object.mode_set(mode='OBJECT')
lower = sets.create(scene, 'Lower', 512); sets.assign(scene, lower, cube, 0)
upper = sets.create(scene, 'Upper', 512); sets.assign(scene, upper, cube, 1)
housing = sets.create(scene, 'Housing', 512); sets.assign(scene, housing, housing_object, 0)
for ts in (lower, upper):
    ts.preview_backend = 'CPU'
    base = stack.add(ts, 'FILL', 'Base'); stack.channel_for(base, 'ROUGHNESS').value = .2
paint = stack.add(lower, 'PAINT', 'Marks')
bpy.context.view_layer.update()


def rough(ts):
    LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
    return images.pixels(images.output(ts, 'ROUGHNESS').image)[..., 0].copy()


def settings(**overrides):
    values = dict(radius=.25, hardness=.9, opacity=1., flow=1., accumulate='MAX', alpha_image=None, projection='SURFACE',
                  blend='PAINT', base_color=(1, 0, 0, 1), roughness=.9, metallic=0., height=.5, ao=1., emission=(0, 0, 0, 1),
                  opacity_value=1., normal=(.5, .5, 1, 1), channels={'ROUGHNESS', 'BASE_COLOR'})
    values.update(overrides)
    return SimpleNamespace(**values)


def vertical_stroke(x=1.0, z0=-.8, z1=.8, count=17, radius=.25):
    """Samples down the +X face, crossing the material seam at z = 0."""
    z = np.linspace(z0, z1, count, dtype=np.float32)
    return np.stack([np.full(count, x, np.float32), np.zeros(count, np.float32), z,
                     np.ones(count, np.float32), np.zeros(count, np.float32), np.zeros(count, np.float32),
                     np.ones(count, np.float32), np.full(count, radius, np.float32),
                     np.zeros(count, np.float32), np.zeros(count, np.float32),
                     np.zeros(count, np.float32), np.zeros(count, np.float32), np.ones(count, np.float32)], axis=1)


# 1. Recording a stroke on the lower set's paint layer links a paint layer in the upper set.
before_upper = rough(upper)
from open_painter.evaluation import metrics
with metrics.Metrics() as counted:
    stroke = records.record(project, lower, paint, vertical_stroke(), settings())
lower_paint = next(l for l in lower.layers if l.type == 'PAINT')
assert lower_paint.stroke_key and stroke.layer_key == lower_paint.stroke_key
linked = records.linked_layers(project, stroke.layer_key)
assert {ts.id for ts, _ in linked} == {lower.id, upper.id}, [ts.name for ts, _ in linked]
# The housing's bounds enclose the stroke, yet it marks no texel of the housing: linking on
# the bounds alone gave it a permanent empty 'Marks' layer re-rastered on every edit of the key.
assert records._intersects(housing, *stroke_raster.bounds(vertical_stroke()))
assert not stroke_raster.coverage(housing, stroke, 512).any()
assert not housing.layers and not dependencies.uses_strokes(housing), [l.name for l in housing.layers]
upper_paint = next(l for ts, l in linked if ts.id == upper.id)
assert upper_paint.type == 'PAINT' and upper_paint.name == 'Marks'
# The link test rasterised the stroke once on each set the bounds reached (the upper set and the
# housing; its own set needs no test). The raster that linked the upper set is the one its first
# evaluation renders: the test used to build it with no cache, so the evaluation built it again.
assert counted.counts['stroke_rasterisations'] == 2, dict(counted.counts)
with counted:
    LayerEvaluator(scene).evaluate_channel(upper.id, 'ROUGHNESS')
assert counted.counts['stroke_rasterisations'] == 2, ('the linked set re-rasterised the stroke', dict(counted.counts))
with counted:
    LayerEvaluator(scene).evaluate_channel(lower.id, 'ROUGHNESS')
assert counted.counts['stroke_rasterisations'] == 3, dict(counted.counts)
rendered = images.pixels(images.output(upper, 'ROUGHNESS').image)[..., 0]
assert rendered.max() > .85, ('the adopted raster must render the stroke', rendered.max())
lower_rough, upper_rough = rough(lower), rough(upper)
assert lower_rough.max() > .85 and upper_rough.max() > .85, ('the stroke must render on both sets', lower_rough.max(), upper_rough.max())
assert (lower_rough > .5).mean() < .5 and not np.allclose(upper_rough, before_upper)
print('VECTOR_STROKES universal canvas: PASS', flush=True)

# 2. Coverage crosses UV seams: the +X face's stroke reaches texels of two islands? It reaches the
#    island of the +X face on each set; a stroke around a corner covers two islands.
corner = np.concatenate([vertical_stroke(x=1.0, z0=-.8, z1=.8, count=9),
                         np.array([[1.0 - t, 0., .8 + .1 * 0, 0, 0, 1, 1, .25, 0, 0, 1, 0, 0] for t in np.linspace(0, .8, 5)], np.float32)])
corner[9:, 2] = 1.0   # the top face samples sit on z = 1 with normal +Z
corner[9:, 3:6] = (0, 0, 1)
around = records.record(project, lower, lower_paint, corner, settings(roughness=.05))
coverage = stroke_raster.coverage(upper, around, 256)
assert (coverage > .5).sum() > 0
labels_hit = set()
from open_painter.geometry.snapshots import targets
with targets(upper) as entries:
    target = entries[0]
    from open_painter.geometry.uv_rasterizer import rasterize
    rows, cols, tri, _ = rasterize(target['uv'][target['tri_loops']], 256)
    hit = coverage[rows, cols] > .5
    polygons = np.unique(target['tri_polygons'][tri[hit]])
    normals = {tuple(np.round(cube.data.polygons[int(p)].normal, 1)) for p in polygons}
    assert len(normals) >= 2, normals
print('VECTOR_STROKES seams: PASS', flush=True)

# 3. Editing a stroke re-rasterises; deleting it clears; erase removes earlier paint.
records.remove(project, around.id)
rough_one = rough(lower)
stroke = next(s for s in project.strokes if s.id == stroke.id)
stroke.roughness = .4
rough_edit = rough(lower)
assert abs(rough_edit.max() - .4) < 1e-3, rough_edit.max()
eraser = records.record(project, lower, lower_paint, vertical_stroke(z0=.2, z1=.8, count=7), settings(blend='ERASE'))
rough_erased = rough(lower)
assert rough_erased.max() < rough_edit.max() + 1e-6 and (rough_erased < rough_edit - 1e-3).any(), 'erase did nothing'
records.remove(project, eraser.id)
records.remove(project, stroke.id)
np.testing.assert_allclose(rough(lower), .2, atol=1e-6)
print('VECTOR_STROKES edits: PASS', flush=True)

# 4. Resolution independence: the same stroke at 512 and 1024 covers the same area.
stroke = records.record(project, lower, lower_paint, vertical_stroke(), settings())
low = rough(lower) > .5
lower.resolution = '1024'
high = rough(lower) > .5
down = high.reshape(512, 2, 512, 2).mean(axis=(1, 3)) > .5
intersection, union = (low & down).sum(), (low | down).sum()
assert union and intersection / union > .95, intersection / union
lower.resolution = '512'
print('VECTOR_STROKES resolution: PASS', flush=True)

# 4b. Rasterize flattens the selected stroke only, into every set rendering it, with the
#     float-canvas (premultiplied) convention: the output is unchanged, the other strokes stay
#     live records, and a mask sharing the key refuses the flatten.
stroke = next(s for s in project.strokes if s.id == stroke.id)
stroke.hardness = .3   # a wide soft edge: partial-alpha texels over the empty canvas
stroke_id, key = stroke.id, stroke.layer_key
flank = vertical_stroke(x=-1.0)
flank[:, 3] = -1.0     # the -X face: its own UV island, never overlapping the +X stroke
other = records.record(project, lower, lower_paint, flank, settings(opacity=.5, hardness=.5, roughness=.6), universal=False)
third = records.record(project, lower, lower_paint, vertical_stroke(z0=.5, z1=.7, count=3, radius=.1), settings(roughness=.4), universal=False)
other_id = other.id
other = next(s for s in project.strokes if s.id == other_id)
stroke = next(s for s in project.strokes if s.id == stroke_id)   # add() moved the collection
other_cover = stroke_raster.coverage(lower, other, 512) > .01
assert other_cover.any() and not (other_cover & (stroke_raster.coverage(lower, stroke, 512) > 0)).any()
expected_lower, expected_upper = rough(lower), rough(upper)
project.active_texture_set = next(i for i, item in enumerate(project.texture_sets) if item.id == lower.id)
# A Vector Strokes mask on the key cannot receive pixels: the flatten is refused, nothing changes.
gate = stack.add(lower, 'FILL', 'Gate')
mask = gate.masks.add(); mask.id, mask.source = new_id(), 'STROKES'
mask.stroke_key = key
lower_paint = next(l for l in lower.layers if l.type == 'PAINT' and l.stroke_key == key)
lower.active_layer = stack.index_of(lower, lower_paint.id)
project.active_stroke = next(i for i, s in enumerate(project.strokes) if s.id == stroke_id)
try:
    bpy.ops.open_painter.stroke(action='RASTERIZE')
    raise AssertionError('a masked key must not be flattened')
except RuntimeError as error:
    assert 'mask' in str(error), error
assert any(s.id == stroke_id for s in project.strokes) and stack.channel_for(lower_paint, 'ROUGHNESS').image is None
stack.remove(lower, stack.index_of(lower, gate.id))
lower_paint = next(l for l in lower.layers if l.type == 'PAINT' and l.stroke_key == key)
lower.active_layer = stack.index_of(lower, lower_paint.id)
project.active_stroke = next(i for i, s in enumerate(project.strokes) if s.id == stroke_id)
count = len(project.strokes)
assert bpy.ops.open_painter.stroke(action='RASTERIZE') == {'FINISHED'}
assert len(project.strokes) == count - 1 and not any(s.id == stroke_id for s in project.strokes)
lower_paint = next(l for l in lower.layers if l.type == 'PAINT' and l.stroke_key == key)
upper_paint = next(l for l in upper.layers if l.type == 'PAINT' and l.stroke_key == key)
for owner in (lower_paint, upper_paint):
    image = stack.channel_for(owner, 'ROUGHNESS').image
    assert image is not None and tuple(image.size) == (512, 512), owner.name
np.testing.assert_allclose(rough(lower), expected_lower, atol=2e-3, err_msg='flattening changed the lower set')
np.testing.assert_allclose(rough(upper), expected_upper, atol=2e-3, err_msg='the linked set lost (or doubled) the stroke')
# The other strokes were not burnt in: deleting one clears its texels.
records.remove(project, other_id)
after = rough(lower)
np.testing.assert_allclose(after[other_cover], .2, atol=1e-6, err_msg='a live stroke was baked into the image')
assert not np.allclose(after, expected_lower)
print('VECTOR_STROKES rasterize: PASS', flush=True)

# 4c. Duplicate reads the snapshot (the RNA reference dies at add()) and gives the copy its own
#     order right after the original, so Move Up/Down keeps distinct orders.
records.record(project, lower, lower_paint, flank, settings(opacity=.5, hardness=.5, roughness=.6), universal=False)
siblings = records.strokes_for(project, key)
assert len(siblings) == 2
first = siblings[0]
first_name, first_id = first.name, first.id
project.active_stroke = next(i for i, s in enumerate(project.strokes) if s.id == first_id)
for _ in range(30):   # enough adds to relocate the collection under the stale reference
    assert bpy.ops.open_painter.stroke(action='DUPLICATE') == {'FINISHED'}
    copy = project.strokes[project.active_stroke]
    assert copy.name == first_name + ' Copy' and copy.layer_key == key, (copy.name, copy.order)
    orders = [s.order for s in records.strokes_for(project, key)]
    assert len(set(orders)) == len(orders), orders
    assert records.strokes_for(project, key)[1].id == copy.id, 'the copy must follow its original'
    project.active_stroke = next(i for i, s in enumerate(project.strokes) if s.id == first_id)
print('VECTOR_STROKES duplicate: PASS', flush=True)

# 4d. A Vector Strokes mask with its own key (New Key) collects strokes recorded under it, re-renders,
#     survives Purge Orphans while linked, and never mirrors paint layers into other sets.
tint = stack.add(lower, 'FILL', 'Tint'); stack.channel_for(tint, 'ROUGHNESS').value = .95
mask = tint.masks.add(); mask.id, mask.source = new_id(), 'STROKES'
lower.active_layer = stack.index_of(lower, tint.id)
assert bpy.ops.open_painter.mask_strokes(action='NEW') == {'FINISHED'}
tint = next(l for l in lower.layers if l.name == 'Tint')
mask = tint.masks[0]
from open_painter.ui.strokes_panel import active_strokes_mask
assert mask.stroke_key and active_strokes_mask(tint) is not None and active_strokes_mask(tint).stroke_key == mask.stroke_key
before = rough(lower)
upper_count = len(upper.layers)
lower.dirty = False
own = records.record(project, lower, tint, vertical_stroke(), settings(), key=mask.stroke_key)
assert own.layer_key == mask.stroke_key and lower.dirty, 'the mask must wake its set'
assert len(upper.layers) == upper_count and not records.linked_layers(project, mask.stroke_key)
assert [m.stroke_key for _, _, m in records.linked_masks(project, mask.stroke_key)] == [mask.stroke_key]
own_cover = stroke_raster.coverage(lower, own, 512)
masked = rough(lower)
assert (own_cover > .999).any() and np.allclose(masked[own_cover > .999], .95, atol=2e-3), masked[own_cover > .999].min()
assert np.allclose(masked[own_cover <= 0], before[own_cover <= 0]), 'the mask leaked outside its strokes'
assert records.purge_orphans(project) == 0
lower.dirty = False
own = next(s for s in project.strokes if s.id == own.id)
own.opacity = .5   # an edit of a mask-key stroke re-renders too
records.touch(project, own)
assert lower.dirty
lower.active_layer = stack.index_of(lower, tint.id)
project.active_stroke = next(i for i, s in enumerate(project.strokes) if s.id == own.id)
try:
    bpy.ops.open_painter.stroke(action='RASTERIZE')
    raise AssertionError('a mask-only key has no paint image to flatten into')
except RuntimeError:
    pass
assert bpy.ops.open_painter.mask_strokes(action='CLEAR') == {'FINISHED'}
assert records.purge_orphans(project) == 1 and not any(s.id == own.id for s in project.strokes)
stack.remove(lower, stack.index_of(lower, next(l for l in lower.layers if l.name == 'Tint').id))
print('VECTOR_STROKES mask key: PASS', flush=True)
# Back to one live stroke on empty canvases for the geometry and undo checks below.
for owner in (lower, upper):
    for item in owner.layers:
        if item.type == 'PAINT':
            for prop in item.channels:
                prop.image = None
for stale in [s.id for s in project.strokes]:
    records.remove(project, stale)
lower_paint = next(l for l in lower.layers if l.type == 'PAINT')
lower.active_layer = stack.index_of(lower, lower_paint.id)
stroke = records.record(project, lower, lower_paint, vertical_stroke(), settings())
low = rough(lower) > .5

# 4e. Dense meshes: coverage rasterises in batches bounded by covered texels. The old guard counted
#     triangles under the stroke's bounds and refused ordinary strokes on 100k-face assets (the
#     set failed until the stroke was deleted); batches must reproduce the single pass exactly.
from unittest.mock import patch
from open_painter.evaluation import memory, raster_cache
from open_painter.geometry import snapshots, uv_rasterizer
key = lower_paint.stroke_key
whole = stroke_raster.coverage(lower, stroke, 512)
budget = stroke_raster.PAIR_BYTES * 500
with snapshots.targets(lower) as entries:
    lo, hi = stroke_raster.bounds(stroke_raster.unpack_samples(stroke.samples))
    corners = entries[0]['positions'][entries[0]['tri_vertices']]
    touched = int((np.all(corners.max(axis=1) >= lo, axis=1) & np.all(corners.min(axis=1) <= hi, axis=1)).sum())
assert touched * 512 * 16 > budget, touched   # the removed guard refused this stroke at this budget
with patch.object(stroke_raster, 'MAX_WORKING_BYTES', budget), \
        patch.object(uv_rasterizer, 'rasterize', wraps=uv_rasterizer.rasterize) as passes:
    batched = stroke_raster.coverage(lower, stroke, 512)
assert passes.call_count > 1, passes.call_count
assert np.array_equal(batched, whole), 'batched rasterisation must match one pass'
# Many strokes: a channel evaluation rasterises each stroke once, under one evaluated mesh, even
# when full-canvas rasters (1 MiB each here, 64 MiB at 4k) would outgrow the shared allowance:
# the tiles used to evict each stroke just before the next tile asked for it again.
extra = [records.record(project, lower, lower_paint, vertical_stroke(z0=-.8 + .1 * i, count=9, radius=.2),
                        settings(roughness=.1 * (i + 1))).id for i in range(5)]
count = len(records.strokes_for(project, key))
assert count == 6, count
allowance = memory.shared.allowance
raster_cache.clear_shared()
memory.shared.configure(3 * 512 * 512 * 4)
try:
    with patch.object(stroke_raster, '_rasterise', wraps=stroke_raster._rasterise) as rasterised, \
            patch.object(snapshots, 'targets', wraps=snapshots.targets) as meshes:
        LayerEvaluator(scene).evaluate_channel(lower.id, 'ROUGHNESS')
    assert rasterised.call_count == count, (rasterised.call_count, count)
    assert meshes.call_count == 1, meshes.call_count
    with patch.object(stroke_raster, '_rasterise', wraps=stroke_raster._rasterise) as rasterised:
        LayerEvaluator(scene).evaluate_channel(lower.id, 'ROUGHNESS')
    assert rasterised.call_count == 0, rasterised.call_count   # the memoised strokes survived
    cached = sum(1 for k in raster_cache.for_texture_set(scene, lower) if isinstance(k, tuple) and k[0] == 'stroke')
    assert cached == count, cached
    assert memory.shared.used <= memory.shared.allowance
finally:
    memory.shared.configure(allowance)
for stale in extra:
    records.remove(project, stale)
stroke = next(s for s in project.strokes if s.layer_key == key)
assert len(records.strokes_for(project, key)) == 1
print('VECTOR_STROKES batches: PASS', flush=True)

# 4f. Planar projection records the view's facing axis. The brush once negated the quaternion
#     instead of the rotated vector (`-q @ v` is the same rotation), storing the direction the
#     view looks along: every texel facing the viewer failed the raster's front-facing test, so a
#     planar stroke drew nothing on the visible surface.
from mathutils import Euler, Vector
from open_painter.painting import gesture
from open_painter.ui.vector_brush import ViewProjector
view = next(a for s in bpy.data.screens for a in s.areas if a.type == 'VIEW_3D').spaces.active.region_3d
brush = project.vector_brush
upper_paint = next(l for l in upper.layers if l.type == 'PAINT' and l.stroke_key == key)
brush.projection, brush.channels, brush.roughness, brush.radius = 'PLANAR', {'ROUGHNESS'}, .9, .25
top_face = vertical_stroke(x=0., z0=1., z1=1., count=5)   # samples across the top face at z = 1
top_face[:, 0] = np.linspace(-.5, .5, 5); top_face[:, 3:6] = (0., 0., 1.)
for rotation in (Euler((0., 0., 0.)), Euler((.35, -.2, .6))):
    view.view_rotation = rotation.to_quaternion()
    projector = ViewProjector(None, view)
    recorder = gesture.Gesture(scene, upper, upper_paint, brush, gesture.Scope.of(project, upper), projector, preview=False)
    recorder.samples, recorder.hit_sets = top_face.tolist(), {upper.id}
    recorder.plane = projector.plane_normal()
    count = len(project.strokes)
    assert recorder.commit() is not None
    assert len(project.strokes) == count + 1
    planar = project.strokes[-1]
    assert planar.projection == 'PLANAR'
    axis = Vector(planar.plane_normal)
    looking = view.view_rotation @ Vector((0., 0., -1.))
    assert axis.dot(looking) < -.99, ('the planar axis must face the viewer', tuple(axis), tuple(looking))
    if rotation.x == rotation.y == rotation.z == 0.:   # the top view: the disc lies in the top face
        top = stroke_raster.coverage(upper, planar, 512)
        assert top.max() > .99, ('a planar stroke from above must land on the top face', top.max())
    records.remove(project, planar.id)
brush.projection = 'SURFACE'
print('VECTOR_STROKES planar view: PASS', flush=True)

# 4g. Portable materials carry the strokes their layers render (0.9.8). The document used to hold
#     only the layer's stroke key: applied elsewhere the layer showed nothing, and the next paint
#     session retired the strokeless key into a paint link, making the loss permanent.
import json
from tempfile import TemporaryDirectory
from zipfile import ZipFile
from open_painter.painting.paint_session import release_idle_keys
from open_painter.smart_materials import loader, serializer
second = records.record(project, lower, lower_paint, flank, settings(opacity=.5, hardness=.5, roughness=.6), universal=False)
second_id = second.id
lower_paint = next(l for l in lower.layers if l.type == 'PAINT' and l.stroke_key == key)
assert len(records.strokes_for(project, key)) == 2
source_rough = rough(lower)
# A twin of the cube in a fresh set: the same geometry under the strokes, none of the links.
twin = cube.copy(); twin.data = cube.data.copy(); scene.collection.objects.link(twin)
copy_set = sets.create(scene, 'Copy', 512); sets.assign(scene, copy_set, twin, 0)
copy_set.preview_backend = 'CPU'
bpy.context.view_layer.update()
with TemporaryDirectory() as directory:
    path = serializer.save(lower, Path(directory) / 'strokes.openpaintmaterial')
    with ZipFile(path) as archive:
        document = json.loads(archive.read('material.json'))
    saved = document.get('strokes', [])
    assert len(saved) == 2 and {s['layer_key'] for s in saved} == {key}, saved
    assert not any(layer.get('paint_link') for layer in document['layers']), 'paint links are not portable'
    assert all('id' not in s and 'revision' not in s and 'selected' not in s for s in saved)
    roots = loader.apply(copy_set, path)
imported = next(l for l in copy_set.layers if l.type == 'PAINT' and l.name == lower_paint.name)
assert imported.stroke_key and imported.stroke_key != key, 'the copy must not alias the source strokes'
assert not imported.paint_link
copies = records.strokes_for(project, imported.stroke_key)
assert len(copies) == 2 and len(records.strokes_for(project, key)) == 2, 'the strokes must be re-imported, not moved'
assert {s.id for s in copies}.isdisjoint({s.id for s in records.strokes_for(project, key)})
for original, copy in zip(records.strokes_for(project, key), copies):
    assert copy.samples == original.samples and copy.order == original.order and copy.revision == 0, copy.name
    assert abs(copy.roughness - original.roughness) < 1e-6 and copy.channels == original.channels
assert [ts.id for ts, _ in records.linked_layers(project, imported.stroke_key)] == [copy_set.id]
copied_rough = rough(copy_set)
assert copied_rough.max() > .85, ('the imported strokes must render on the new set', copied_rough.max())
np.testing.assert_allclose(copied_rough, source_rough, atol=2e-3, err_msg='the copy must render what the source renders')
# A paint session leaves a key whose strokes exist alone; only a strokeless key retires.
idle = stack.add(copy_set, 'PAINT', 'Idle'); idle.stroke_key = new_id()
idle_key = idle.stroke_key
release_idle_keys(project)
imported = next(l for l in copy_set.layers if l.name == lower_paint.name)
idle = next(l for l in copy_set.layers if l.name == 'Idle')
assert imported.stroke_key and len(records.strokes_for(project, imported.stroke_key)) == 2, 'an imported stroke key was retired'
assert not idle.stroke_key and idle.paint_link == idle_key
# The copies are independent records: deleting the source strokes leaves them rendering.
records.remove(project, second_id)
assert len(records.strokes_for(project, imported.stroke_key)) == 2
np.testing.assert_allclose(rough(copy_set), copied_rough, atol=1e-6, err_msg='the copy followed the source')
assert not housing.layers
sets.remove(scene, next(i for i, item in enumerate(project.texture_sets) if item.id == copy_set.id))
assert records.purge_orphans(project) == 2
bpy.data.objects.remove(twin)
lower = next(item for item in project.texture_sets if item.name == 'Lower')
upper = next(item for item in project.texture_sets if item.name == 'Upper')
housing = next(item for item in project.texture_sets if item.name == 'Housing')
lower_paint = next(l for l in lower.layers if l.type == 'PAINT' and l.stroke_key == key)
assert len(records.strokes_for(project, key)) == 1
print('VECTOR_STROKES portable: PASS', flush=True)

# 5. Geometry changes re-rasterise; undo removes the stroke as data.
dependencies.remember(scene, lower, dependencies.geometry_key(scene, lower))
assert dependencies.uses_geometry(lower)
cube.location.x += .5; bpy.context.view_layer.update(); dependencies.refresh(scene)
assert lower.dirty
moved = rough(lower)
assert not np.array_equal(moved > .5, low), 'a moved mesh must re-project the stroke'
cube.location.x -= .5; bpy.context.view_layer.update()
assert not housing.layers, ('a later stroke linked the enclosing set', [l.name for l in housing.layers])
bpy.ops.ed.undo_push(message='before')
count = len(project.strokes)
records.record(project, lower, lower_paint, vertical_stroke(x=-1.0), settings())
bpy.ops.ed.undo_push(message='stroke')
bpy.ops.ed.undo()
assert len(bpy.context.scene.open_painter.strokes) == count
# Save and reload keep strokes.
from tempfile import TemporaryDirectory
with TemporaryDirectory() as directory:
    path = str(Path(directory) / 'strokes.blend')
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    saved = bpy.context.scene.open_painter.strokes
    assert len(saved) == count and stroke_raster.unpack_samples(saved[0].samples).shape[0] == 17
print('VECTOR_STROKES persistence: PASS', flush=True)
print('VECTOR_STROKES_PASS', flush=True)
open_painter.unregister()
