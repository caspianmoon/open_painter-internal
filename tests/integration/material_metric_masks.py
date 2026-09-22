"""Actual metric mask radii and effect reach, including Eevee/CPU/export parity.

Deterministic capability diagnostics, no provider calls or artistic scores.
"""
import json
from pathlib import Path
import sys

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.authoring import commands, document, physical_space, surfaces, transactions
from open_painter.authoring.schema import AuthoringError
from open_painter.core import texture_set_manager as sets, image_manager
from open_painter.evaluation import cache
from open_painter.evaluation.mask_eval import _evaluate_masks
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.shader_graph import Graph
from open_painter.export import exporter
from open_painter.generators import scene_fields

OUT = ROOT / '.blender-test' / 'material-metric-masks'
OUT.mkdir(parents=True, exist_ok=True)


def apply(ts, label, *cmds):
    return transactions.apply(ts, dict(version=1, summary=label, commands=list(cmds)),
                              operation_id=label, expected_digest=document.digest(ts))


def refused(call, match):
    try:
        call()
    except AuthoringError as error:
        assert match in str(error), str(error)
    else:
        raise AssertionError('Expected rejection: ' + match)


def computed(ts, field):
    steps = scene_fields.compute_steps(ts, field)
    while True:
        try:
            next(steps)
        except StopIteration as result:
            return result.value


open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.context.scene
scene.render.engine = 'BLENDER_EEVEE'
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_plane_add(size=.016)
obj = bpy.context.object
ts = sets.create(scene, 'Metric mask controls', 512)
ts.preview_backend = 'CPU'
sets.assign(scene, ts, obj)
base = [dict(op='add_layer', id='new:base', name='Radius diagnostic', kind='FILL'),
        dict(op='set_channel', layer_id='new:base', channel='BASE_COLOR', values=dict(color=[1, 1, 1, 1])),
        dict(op='add_mask', id='new:chip', layer_id='new:base', name='Circular chip', source='PROCEDURAL',
             values=dict(procedural='DOTS', chip_radius_m=.00028))]
refused(lambda: apply(ts, 'unknown radius', *base), 'Physical size is unknown')
physical_space.declare(ts, 1, 'Diagnostic mesh: 16mm plane in metre coordinates')
result = apply(ts, 'radius controls', *base)
layer = next(l for l in ts.layers if l.id == result['aliases']['new:base'])
mask = layer.masks[0]
first = _evaluate_masks(ts, layer, 512, 0, 512, {}, {})
# Cell center is at .5mm; choose the nearest raster row. The >50% boundary
# measures the actual circular primitive radius, not its stored parameter.
row = round((.0005 + .008) / .016 * 512 - .5)
col = row
left, right = col, col
while first[row, left - 1] > .5:
    left -= 1
while first[row, right + 1] > .5:
    right += 1
measured = (right - left + 1) * .016 / 512 / 2
assert abs(measured - .00028) <= .016 / 512, (measured, first[row, left:right + 1])
assert abs(mask.chip_radius_m - .00028) < 1e-10
assert 'chip_radius_m' not in document.snapshot(ts)['layers'][0]['masks'][0]
obj.scale = (100, 100, 100)
bpy.context.view_layer.update()
physical_space.declare(ts, .01, 'Same16mm plane represented with100x world coordinates')
apply(ts, 'equivalent radius', dict(op='set_mask', layer_id=layer.id, mask_id=mask.id, values=dict(chip_radius_m=.00028)))
second = _evaluate_masks(ts, layer, 512, 0, 512, {}, {})
np.testing.assert_allclose(first, second, atol=.0002)
mask.chip_radius_m = .00014
smaller = _evaluate_masks(ts, layer, 512, 0, 512, {}, {})
assert np.mean(abs(smaller - first)) > .1, 'Editable radius must change evaluated spatial scale'
mask.chip_radius_m = .00028
obj.scale = (1, 1, 1)
bpy.context.view_layer.update()
physical_space.declare(ts, 1, 'Diagnostic16mm plane in metres')
apply(ts, 'restore metric mapping', dict(op='set_mask', layer_id=layer.id, mask_id=mask.id, values=dict(chip_radius_m=.00028)))
print('METRIC_CHIP_EVALUATED_RADIUS_PASS', measured, flush=True)

# Actual Eevee shader boundary, CPU output and production16bit texture export.
LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
expected = image_manager.pixels(next(o.image for o in ts.outputs if o.channel == 'BASE_COLOR')).copy()
ts.export.follow_target = False
ts.export.preset, ts.export.file_format, ts.export.bit_depth = 'BLENDER', 'PNG', '16'
ts.export.padding_mode = 'NONE'
paths = exporter.export(bpy.context, ts, ts.export, directory=str(OUT / 'maps'), overwrite=True)
material = ts.material
material.node_tree.nodes.clear()
graph = Graph(scene, ts, material).build()
while graph.pending_resources:
    graph.step_resources(.1)
output = material.node_tree.nodes.new('ShaderNodeOutputMaterial')
emission = material.node_tree.nodes.new('ShaderNodeEmission')
material.node_tree.links.new(emission.outputs[0], output.inputs['Surface'])
bpy.ops.object.camera_add(location=(0, 0, .04))
camera = bpy.context.object
camera.data.type = 'ORTHO'
camera.data.ortho_scale = .016
camera.data.clip_start = .001
camera.rotation_euler = (0, 0, 0)
scene.camera = camera
scene.render.resolution_x = scene.render.resolution_y = 512
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'OPEN_EXR'
scene.render.image_settings.color_depth = '32'
scene.view_settings.view_transform = 'Standard'
scene.view_settings.look = 'None'
scene.view_settings.exposure = 0
scene.view_settings.gamma = 1


def render(name, socket):
    material.node_tree.links.new(socket, emission.inputs['Color'])
    scene.render.filepath = str(OUT / (name + '.exr'))
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(scene.render.filepath, check_existing=False)
    result = image_manager.pixels(image)[..., :3].copy()
    bpy.data.images.remove(image)
    return result


native = render('native', graph.outputs['BASE_COLOR'])
texture = material.node_tree.nodes.new('ShaderNodeTexImage')
cpu = bpy.data.images.new('Mask CPU reference', width=512, height=512, float_buffer=True)
cpu.colorspace_settings.name = 'Non-Color'
cpu.pixels.foreach_set(expected.ravel())
cpu.update()
texture.image = cpu
cpu_render = render('cpu', texture.outputs['Color'])
path = next(p for p in paths if 'basecolor' in p.stem.lower().replace('_', ''))
exported = bpy.data.images.load(str(path), check_existing=False)
exported.colorspace_settings.name = 'sRGB'
texture.image = exported
export_render = render('export', texture.outputs['Color'])
errors = dict(cpu_shader_mae=float(abs(native - cpu_render).mean()), export_shader_mae=float(abs(native - export_render).mean()))
assert max(errors.values()) < .001, errors
print('METRIC_MASK_EEVEE_CPU_EXPORT_PASS', errors, flush=True)

# Effect distances and bounds are evaluated in current world space exactly once.
result = apply(ts, 'physical effect', dict(op='add_layer', id='new:effect', name='Effect reach', kind='FILL'),
    dict(op='add_mask', id='new:field', layer_id='new:effect', name='Ground distance', source='SCENE'),
    dict(op='set_field', layer_id='new:effect', mask_id='new:field', values=dict(kind='GROUND', direction=[1, 0, 0], distance_m=.004, resolution='512')))
effect = next(l for l in ts.layers if l.id == result['aliases']['new:effect'])
field = effect.masks[0].influence
original = computed(ts, field)
line = original[256]
# 4mm reach falls from1 at x=0 to0 atx=4mm; midpoint .5 at2mm.
mid = round((.002 + .008) / .016 * 512 - .5)
assert abs(float(line[mid]) - .5) < .01
obj.scale = (100, 100, 100)
bpy.context.view_layer.update()
physical_space.declare(ts, .01, 'Same mesh with100x world coordinates')
apply(ts, 'equivalent effect', dict(op='set_field', layer_id=effect.id, mask_id=effect.masks[0].id, values=dict(distance_m=.004)))
np.testing.assert_allclose(original, computed(ts, field), atol=1e-5)
assert abs(field.distance - .4) < 1e-7 and abs(field.distance_m - .004) < 1e-8
field.distance_m = .002
assert np.mean(abs(original - computed(ts, field))) > .05
apply(ts, 'physical bounds', dict(op='set_field', layer_id=effect.id, mask_id=effect.masks[0].id,
      values=dict(kind='WORLD_HEIGHT', bounds_m=[-.004, .004])))
assert np.allclose(field.bounds, [-.4, .4]) and np.allclose(field.bounds_m, [-.004, .004])
height = computed(ts, field)
assert abs(float(height[256, 256]) - .5) < .01
assert not ({'distance_m', 'bounds_m'} & document.snapshot(ts)['layers'][-1]['masks'][0]['influence'].keys())
refused(lambda: apply(ts, 'double units', dict(op='set_field', layer_id=effect.id, mask_id=effect.masks[0].id,
    values=dict(distance=.1, distance_m=.001))), 'not both')
print('METRIC_EFFECT_DISTANCE_AND_BOUNDS_PASS', flush=True)

# Native paint loss has correct group algebra, stable child aliases and editable
# channels. Physical controls do not create arbitrary scripts or hidden graphs.
result = apply(ts, 'physical paint', dict(op='create_surface', id='new:paint', name='Paint', family='PAINTED_METAL',
    values=dict(chip_radius_m=.00028, effect_distance_m=.002, paint_loss_opacity=.7)))
paint = next(l for l in ts.layers if l.id == result['aliases']['new:paint:finish'])
group, wear, chips = paint.masks
assert group.source == 'GROUP' and group.invert and group.surface_role == 'PAINT_LOSS'
assert wear.parent_id == chips.parent_id == group.id and not wear.invert
assert wear.edge_source == 'LIVE' and abs(wear.influence.distance_m - .002) < 1e-8
assert result['aliases']['new:paint:chips'] == chips.id
assert result['aliases']['new:paint:wear_placement'] == wear.id
assert abs(next(s for s in surfaces.describe(ts) if s['id'] == result['aliases']['new:paint'])['controls']['chip_radius_m'] - .00028) < 1e-9
# On this flat diagnostic plane wear is zero, so coating stays100% despite dots.
coverage = _evaluate_masks(ts, paint, 512, 0, 512, {}, {})
assert float(coverage.min()) > .99, 'Chips must not subtract paint away from geometry wear'
print('METRIC_NATIVE_PAINT_GROUP_PASS', flush=True)
report = dict(engine=scene.render.engine, declared_radius_m=.00028, measured_radius_m=measured,
    parity=errors, field_distance_m=.004, fixture='16mm diagnostic plane', artistic_rating='NOT_APPLICABLE')
(OUT / 'result.json').write_text(json.dumps(report, indent=2))
print('MATERIAL_METRIC_MASKS_PASS', flush=True)
