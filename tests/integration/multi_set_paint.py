"""Native painting across texture sets: one session, one stroke, two canvases dirty,
both sets marked and evaluated, undo restores both, session teardown removes the canvas nodes.
Regression checks (0.9.7): the link is not a stroke key, idle detection is throttled, a
retarget stays multi-set and drops the old canvases, a swapped slot refuses to start."""
import sys
from pathlib import Path
from time import monotonic
from unittest.mock import patch

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.constants import OWNER
from open_painter.core import texture_set_manager as sets, layer_stack as stack, image_manager as images, material_manager
from open_painter.evaluation import cache, shader_preview
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.painting import paint_session

open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
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
linked = next(l for l in upper.layers if l.type == 'PAINT' and l.paint_link == paint.paint_link)
assert linked.name == 'Marks' and paint.paint_link
# The link is its own id: a stroke key would route both layers through the vector-stroke boundary.
assert not paint.stroke_key and not linked.stroke_key
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

# 2b. Idle detection: a painted canvas stays dirty until it is packed, so the secondary canvas
# is re-read at the primary's 0.75 s pace, not on every scheduler tick.
data = images.pixels(upper_canvas); data[300:320, 300:320] = (.1, .1, .1, 1.)
upper_canvas.pixels.foreach_set(data.ravel()); upper_canvas.update()
assert upper_canvas.is_dirty
secondary_key = (scene.as_pointer(), upper_canvas.as_pointer())
with patch.object(paint_session, 'pixels', wraps=paint_session.pixels) as reads:
    for _ in range(20):
        assert not paint_session.detect(scene)
    assert reads.call_count == 0, 'an unforced detect inside the pace window must not read the canvases'
    paint_session._checks[secondary_key] = (monotonic() - 1., None)
    assert paint_session.detect(scene)
    assert reads.call_count == 1

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
assert len([l for l in upper.layers if l.paint_link == paint.paint_link]) == 1
paint_session.finish(bpy.context, evaluate=False)
project.paint_all_sets = False
paint_session.begin(bpy.context, lower, 'ROUGHNESS')
assert len(project.paint_canvases) == 0 and scene.tool_settings.image_paint.mode == 'IMAGE'
paint_session.finish(bpy.context, evaluate=False)
print('MULTI_SET_PAINT reuse: PASS', flush=True)

# 5. A retarget inside the session (channel dropdown, another set) stays multi-set and drops
# the old canvases from detection.
project.paint_all_sets = True
paint_session.begin_multi(bpy.context, lower, 'ROUGHNESS')
stale = [(entry.image, (scene.as_pointer(), entry.image.as_pointer())) for entry in project.paint_canvases]
project.paint_channel = 'BASE_COLOR'
assert paint_session.synchronize(bpy.context)
assert scene.tool_settings.image_paint.mode == 'MATERIAL' and len(project.paint_canvases) == 2
assert all(p.select for p in cube.data.polygons), "both sets' faces stay paintable after a channel change"
assert project.paint_image == stack.channel_for(paint, 'BASE_COLOR').image
assert project.paint_canvases[1].image == stack.channel_for(linked, 'BASE_COLOR').image
assert not any(key in paint_session._rasters for _, key in stale), 'the old canvases left detection'
assert all(image not in {entry.image for entry in project.paint_canvases} for image, _ in stale)
# The other set's linked layer becomes the primary: its canvas is read once per check.
project.active_texture_set = 1
upper.active_layer = stack.index_of(upper, linked.id)
assert paint_session.synchronize(bpy.context)
primary = project.paint_image
assert primary == stack.channel_for(linked, 'BASE_COLOR').image and project.paint_set_id == upper.id
assert len(project.paint_canvases) == 2 and project.paint_canvases[1].image != primary
assert (scene.as_pointer(), primary.as_pointer()) not in paint_session._rasters
data = images.pixels(primary); data[50:60, 50:60] = (.2, .3, .4, 1.)
primary.pixels.foreach_set(data.ravel()); primary.update()
serial = upper.edit_serial
with patch.object(paint_session, 'pixels', wraps=paint_session.pixels) as reads:
    assert paint_session.detect(scene, force=True)
    assert reads.call_count == 2, 'one read per canvas'
assert upper.edit_serial == serial + 1, 'the primary canvas is detected once, not twice'
paint_session.finish(bpy.context, evaluate=False)
project.active_texture_set = 0
project.paint_channel = 'ROUGHNESS'
print('MULTI_SET_PAINT retarget: PASS', flush=True)

# 6. Shader-native preview: the linked layers stay native Image inputs (no Strokes boundary),
# and a native stroke rebinds the image without queueing a CPU raster.
for ts in (lower, upper):
    ts.preview_backend = 'LIVE'
    material_manager.repair(ts)


def ready(ts):
    deadline = monotonic() + 60
    while monotonic() < deadline:
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert not info['error'], info
        if shader_preview.current(scene, ts):
            return info
    raise AssertionError(shader_preview.diagnostics(scene, ts))


primary = paint_session.begin_multi(bpy.context, lower, 'ROUGHNESS')
for ts, canvas in ((lower, project.paint_canvases[0].image), (upper, project.paint_canvases[1].image)):
    ready(ts)
    graph = shader_preview.ensure(scene, ts)
    assert not any(b.identifier.startswith('Strokes:') for b in graph.resource_bindings), ts.name
    assert canvas.as_pointer() in graph.unique_images(), f'{ts.name}: the shader samples the canvas directly'
data = images.pixels(primary); data[60:70, 60:70] = (.4, .4, .4, 1.)
primary.pixels.foreach_set(data.ravel()); primary.update()
assert shader_preview.native_paint_input(scene)
info = shader_preview.diagnostics(scene, lower)
assert not info['pending_resources'] and not info.get('raster_boundaries_queued'), info
shader_preview.tick(scene, lower)   # the engine renders the painted canvas
assert shader_preview.current(scene, lower), shader_preview.diagnostics(scene, lower)
paint_session.finish(bpy.context, evaluate=False)
assert not paint.stroke_key and not linked.stroke_key
print('MULTI_SET_PAINT live: PASS', flush=True)

# 6b. A file whose link was a stroke key (before paint_link) returns to the native path.
paint.stroke_key = linked.stroke_key = 'legacy-link'
paint.paint_link = linked.paint_link = ''
paint_session.begin_multi(bpy.context, lower, 'ROUGHNESS')
assert len(project.paint_canvases) == 2 and project.paint_canvases[1].layer_id == linked.id
assert not paint.stroke_key and not linked.stroke_key and paint.paint_link == linked.paint_link == 'legacy-link'
paint_session.finish(bpy.context, evaluate=False)
print('MULTI_SET_PAINT legacy key: PASS', flush=True)

# 7. A slot the artist swapped for their own material refuses the session, like begin(): material
# paint mode would write that half of the stroke into the foreign material's image node.
foreign = bpy.data.materials.new('Render'); foreign.use_nodes = True
photo = foreign.node_tree.nodes.new('ShaderNodeTexImage')
photo.image = bpy.data.images.new('Photo', 64, 64)
kept = cube.material_slots[1].material
cube.material_slots[1].material = foreign
layers_before = len(upper.layers)
try:
    paint_session.begin_multi(bpy.context, lower, 'ROUGHNESS')
except ValueError as exc:
    assert 'material slots changed' in str(exc), exc
else:
    raise AssertionError('a swapped slot must refuse a multi-set session')
assert project.paint_image is None and not project.paint_canvases and cube.mode == 'OBJECT'
assert len(upper.layers) == layers_before, 'the refusal leaves no linked layer behind'
cube.material_slots[1].material = kept
paint_session.begin_multi(bpy.context, lower, 'ROUGHNESS')
assert len(project.paint_canvases) == 2
paint_session.finish(bpy.context, evaluate=False)
print('MULTI_SET_PAINT_PASS', flush=True)
open_painter.unregister()
