"""Mask → curves → geometry: a painted stitch line becomes a world-space curve on the surface,
a stitch instancer follows it, and the weld bead accepts it."""
import sys
from pathlib import Path

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.core import image_manager as images, layer_stack as stack, texture_set_manager as sets
from open_painter.core.published_outputs import publish_mask
from open_painter.geometry_effects import curves, manager, stitch, weld
from open_painter.utils.ids import new_id

open_painter.register()
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
# A UV sphere with a deterministic cylindrical map: u = longitude, v = height, seam faces pushed past u = 1.
import math
bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24, radius=1)
sphere = bpy.context.object
mesh = sphere.data
uv_layer = mesh.uv_layers.new(name='Cylindrical')
mesh.uv_layers.active = uv_layer
for polygon in mesh.polygons:
    us = []
    for index in polygon.loop_indices:
        co = mesh.vertices[mesh.loops[index].vertex_index].co
        us.append(math.atan2(co.y, co.x) / math.tau + .5)
    if max(us) - min(us) > .5:
        us = [u + 1 if u < .5 else u for u in us]
    for index, u in zip(polygon.loop_indices, us):
        co = mesh.vertices[mesh.loops[index].vertex_index].co
        uv_layer.data[index].uv = (u, (co.z + 1) / 2)
ts = sets.create(scene, 'Leather', 512); sets.assign(scene, ts, sphere)
ts.preview_backend = 'CPU'
layer = stack.add(ts, 'FILL', 'Seam')
mask = layer.masks.add(); mask.id, mask.source = new_id(), 'PAINT'
line = images.create('Stitch line', 512)
data = np.zeros((512, 512, 4), np.float32); data[..., 3] = 1
data[250:262, 60:450, :3] = 1.0            # a horizontal band across the UV square (one latitude)
images.write(line, data, pack=True)
mask.image = line
bpy.context.view_layer.update()

published = publish_mask(ts, layer)
steps = curves.extract_steps(ts, published, n=512, threshold=.5, tolerance=1.5)
polylines = None
while True:
    try:
        next(steps)
    except StopIteration as done:
        polylines = done.value
        break
assert polylines, 'no curve extracted'
points = np.concatenate([p for p, _ in polylines])
assert len(points) >= 3, len(points)   # simplified in world space
radii = np.linalg.norm(points, axis=1)
assert np.abs(radii - 1.0).max() < .02, np.abs(radii - 1.0).max()   # on the sphere surface
assert np.ptp(points[:, 2]) < .12, np.ptp(points[:, 2])                # one latitude band
assert np.ptp(np.arctan2(points[:, 1], points[:, 0])) > 3.0            # around most of the sphere
assert len(polylines) <= 2, len(polylines)                             # at most split at the UV seam
curve = curves.create_curve(scene, sphere, polylines, 'OP Stitch Path', manager.TAG)
assert curve.type == 'CURVE' and curve.parent == sphere and len(curve.data.splines) == len(polylines)
print('CURVE_GEOMETRY extraction: PASS', flush=True)

helper = stitch.create(scene, curve, 'STITCH', spacing=.05, size=.02, seed=2, source=sphere)
assert helper.get(manager.TAG) == 'INSTANCE_HELPER' and helper['instance_count'] > 20
with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
    graph = bpy.context.evaluated_depsgraph_get()
instances = sum(1 for item in graph.object_instances if item.is_instance and item.parent and item.parent.original == helper)
assert instances >= 20, instances
bead = weld.create(scene, curve, radius=.01)
assert bead.get(manager.TAG) == 'INSTANCE_HELPER' and bead['kind'] == 'WELD'
manager.remove(bead); manager.remove(helper)
print('CURVE_GEOMETRY stitches and bead: PASS', flush=True)

# A blockout plate (cube scaled to Z = 0) has a singular matrix: extraction must not raise
# after the curve is linked, and the curve stays on the surface instead of being
# re-transformed by the parent.
bpy.ops.mesh.primitive_cube_add()
plate = bpy.context.object
plate.scale = (1.0, 1.0, 0.0)
bpy.context.view_layer.update()
plate_set = sets.create(scene, 'Plate', 512); sets.assign(scene, plate_set, plate)
plate_set.preview_backend = 'CPU'
plate_layer = stack.add(plate_set, 'FILL', 'Scribe')
plate_mask = plate_layer.masks.add(); plate_mask.id, plate_mask.source = new_id(), 'PAINT'
plate_mask.image = line
bpy.context.view_layer.update()
steps = curves.extract_steps(plate_set, publish_mask(plate_set, plate_layer), n=512, threshold=.5, tolerance=1.5)
while True:
    try:
        next(steps)
    except StopIteration as done:
        plate_lines = done.value
        break
assert plate_lines, 'no curve extracted from the plate'
before = {o.name for o in bpy.data.objects}
plate_curve = curves.create_curve(scene, plate, plate_lines, 'OP Curves · Scribe', manager.TAG)
assert {o.name for o in bpy.data.objects} - before == {plate_curve.name}
bpy.context.view_layer.update()
expected = np.concatenate([p for p, _ in plate_lines])
placed = np.array([(plate_curve.matrix_world @ point.co.to_3d())[:] for spline in plate_curve.data.splines for point in spline.points])
assert np.abs(placed - expected).max() < 1e-4, np.abs(placed - expected).max()
assert np.abs(placed[:, 2]).max() < 1e-4, placed[:, 2]   # flat on the plate
print('CURVE_GEOMETRY singular source: PASS', flush=True)
print('CURVE_GEOMETRY_PASS', flush=True)
open_painter.unregister()
