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
lower = sets.create(scene, 'Lower', 512); sets.assign(scene, lower, cube, 0)
upper = sets.create(scene, 'Upper', 512); sets.assign(scene, upper, cube, 1)
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
stroke = records.record(project, lower, paint, vertical_stroke(), settings())
lower_paint = next(l for l in lower.layers if l.type == 'PAINT')
assert lower_paint.stroke_key and stroke.layer_key == lower_paint.stroke_key
linked = records.linked_layers(project, stroke.layer_key)
assert {ts.id for ts, _ in linked} == {lower.id, upper.id}, [ts.name for ts, _ in linked]
upper_paint = next(l for ts, l in linked if ts.id == upper.id)
assert upper_paint.type == 'PAINT' and upper_paint.name == 'Marks'
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

# 5. Geometry changes re-rasterise; undo removes the stroke as data.
dependencies.remember(scene, lower, dependencies.geometry_key(scene, lower))
assert dependencies.uses_geometry(lower)
cube.location.x += .5; bpy.context.view_layer.update(); dependencies.refresh(scene)
assert lower.dirty
moved = rough(lower)
assert not np.array_equal(moved > .5, low), 'a moved mesh must re-project the stroke'
cube.location.x -= .5; bpy.context.view_layer.update()
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
