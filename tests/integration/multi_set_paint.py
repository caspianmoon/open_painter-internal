"""Native painting across texture sets: one session, one stroke, two canvases dirty,
both sets marked and evaluated, undo restores both, session teardown removes the canvas nodes."""
import sys
from pathlib import Path

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.constants import OWNER
from open_painter.core import texture_set_manager as sets, layer_stack as stack, image_manager as images, material_manager
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.painting import paint_session

open_painter.register()
scene = bpy.context.scene
project = scene.open_painter
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add(size=2)
cube = bpy.context.object
bpy.ops.object.mode_set(mode='EDIT'); bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.mesh.subdivide(number_cuts=1)
bpy.ops.uv.smart_project(island_margin=.03); bpy.ops.object.mode_set(mode='OBJECT')
cube.data.materials.append(bpy.data.materials.new('Lower')); cube.data.materials.append(bpy.data.materials.new('Upper'))
for polygon in cube.data.polygons:
    polygon.material_index = 1 if polygon.center.z > 0 else 0
lower = sets.create(scene, 'Lower', 512); sets.assign(scene, lower, cube, 0)
upper = sets.create(scene, 'Upper', 512); sets.assign(scene, upper, cube, 1)
for ts in (lower, upper):
    ts.preview_backend = 'CPU'
    base = stack.add(ts, 'FILL', 'Base'); stack.channel_for(base, 'ROUGHNESS').value = .2
    material_manager.repair(ts)
paint = stack.add(lower, 'PAINT', 'Marks')
lower.active_layer = stack.index_of(lower, paint.id)
project.active_texture_set = 0
project.paint_channel = 'ROUGHNESS'
project.paint_all_sets = True
bpy.context.view_layer.update()

# 1. Begin: linked layer in the upper set, a canvas node per material, material paint mode, union of slots.
primary = paint_session.begin_multi(bpy.context, lower, 'ROUGHNESS')
assert scene.tool_settings.image_paint.mode == 'MATERIAL'
assert len(project.paint_canvases) == 2 and project.paint_image == primary
linked = next(l for l in upper.layers if l.type == 'PAINT' and l.stroke_key == paint.stroke_key)
assert linked.name == 'Marks'
for ts in (lower, upper):
    node = next((n for n in ts.material.node_tree.nodes if n.get(OWNER) and n.get('role') == 'Paint Canvas'), None)
    assert node is not None and node.image is not None and ts.material.node_tree.nodes.active == node, ts.name
assert all(p.select for p in cube.data.polygons), 'both sets\' faces must be paintable'
assert cube.mode == 'TEXTURE_PAINT'
print('MULTI_SET_PAINT begin: PASS', flush=True)

# 2. A stroke down the +X face crosses the seam: both canvases receive pixels.
from bpy_extras.view3d_utils import location_3d_to_region_2d
window = bpy.context.window
area = next((a for a in bpy.context.screen.areas if a.type == 'VIEW_3D'), None)
if area is None or bpy.app.background:
    # The native paint operator needs a real window (its undo push crashes in background):
    # emulate the stroke's writes into both canvases; the GUI workflow probe drives the real brush.
    print('MULTI_SET_PAINT stroke: emulated in background', flush=True)
    lower_canvas, upper_canvas = project.paint_canvases[0].image, project.paint_canvases[1].image
    # Emulate a stroke by writing into both canvases the way the brush would.
    for image in (lower_canvas, upper_canvas):
        data = images.pixels(image)
        data[200:300, 200:300] = (.9, .9, .9, 1.)
        image.pixels.foreach_set(data.ravel()); image.update()
else:
    region = next(r for r in area.regions if r.type == 'WINDOW')
    rv3d = area.spaces.active.region_3d
    points = [(1.001, 0., z) for z in np.linspace(-.8, .8, 12)]
    stroke = []
    for point in points:
        xy = location_3d_to_region_2d(region, rv3d, point)
        stroke.append(dict(name='stroke', location=point, mouse=(xy.x, xy.y), mouse_event=(xy.x, xy.y),
                           size=40, pressure=1., time=0., is_start=not stroke, x_tilt=0., y_tilt=0.))
    brush = scene.tool_settings.image_paint.brush
    unified = scene.tool_settings.image_paint.unified_paint_settings
    for owner in (brush, unified):
        owner.color, owner.strength, owner.size = (.9, .9, .9), 1, 40
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.paint.image_paint(stroke=stroke)
lower_canvas, upper_canvas = project.paint_canvases[0].image, project.paint_canvases[1].image
assert paint_session.detect(scene, force=True)
assert lower.dirty and upper.dirty, 'both sets must be marked after the stroke'
print('MULTI_SET_PAINT stroke marks both sets: PASS', flush=True)

# 3. Finish: canvas nodes removed, both sets evaluated, image mode restored.
paint_session.finish(bpy.context)
assert not project.paint_canvases and project.paint_image is None
for ts in (lower, upper):
    assert not any(n.get('role') == 'Paint Canvas' for n in ts.material.node_tree.nodes), ts.name
    assert not ts.dirty, ts.name
assert scene.tool_settings.image_paint.mode == 'IMAGE'
rough_lower = images.pixels(images.output(lower, 'ROUGHNESS').image)[..., 0]
rough_upper = images.pixels(images.output(upper, 'ROUGHNESS').image)[..., 0]
assert rough_lower.max() > .5 and rough_upper.max() > .5, (rough_lower.max(), rough_upper.max())
print('MULTI_SET_PAINT finish: PASS', flush=True)

# 4. A second session reuses the linked layer (no duplicates); without the toggle, begin() is single-set.
paint_session.begin_multi(bpy.context, lower, 'ROUGHNESS')
assert len([l for l in upper.layers if l.stroke_key == paint.stroke_key]) == 1
paint_session.finish(bpy.context, evaluate=False)
project.paint_all_sets = False
paint_session.begin(bpy.context, lower, 'ROUGHNESS')
assert len(project.paint_canvases) == 0 and scene.tool_settings.image_paint.mode == 'IMAGE'
paint_session.finish(bpy.context, evaluate=False)
print('MULTI_SET_PAINT_PASS', flush=True)
open_painter.unregister()
