"""Render every smart material's master controls at their extremes, for visual validation.

One contact sheet per material: a row per control, columns = minimum · as authored ·
maximum (a colour control: as authored · magenta · green). Each tile is a Cycles render of
the real composited material on the beveled cavity preview cube with real mesh maps, the
control's label and value printed in the frame. ``report.json`` records, per control, the
mean absolute change of every output channel between the extremes and the authored state,
so a control that touches an unrelated channel shows up in numbers as well as pixels.

    blender --background --factory-startup --python scripts/render_master_controls.py -- \
        [--material NAME ...] [--output DIR] [--size 224] [--texture-size 512] [--samples 12]
"""
import argparse
import json
import sys
from pathlib import Path
from time import perf_counter

import bpy
import numpy as np

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'src'))
import open_painter
from open_painter.baking.bake_manager import bake_selected
from open_painter.baking.maps.utility import bake_utility
from open_painter.core import image_manager as images, texture_set_manager as sets
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.export.image_writer import write as write_image
from open_painter.smart_materials import composer, controls
from open_painter.smart_materials.catalog import CATALOG

parser = argparse.ArgumentParser()
parser.add_argument('--material', action='append', choices=list(CATALOG))
parser.add_argument('--output', type=Path, default=root / '.blender-test/master-controls')
parser.add_argument('--size', type=int, default=224)
parser.add_argument('--texture-size', type=int, default=512, choices=(512, 1024, 2048))
parser.add_argument('--samples', type=int, default=12)
parser.add_argument('--control', action='append', help='Render these control labels only')
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
selected = args.material or list(CATALOG)
COLORS = ((0.9, 0.05, 0.6, 1), (0.05, 0.9, 0.2, 1))

open_painter.register()
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
scene.render.engine = 'CYCLES'
scene.cycles.device = 'CPU'
scene.cycles.samples = args.samples
scene.cycles.use_denoising = True
scene.cycles.seed = 0
scene.render.resolution_x = scene.render.resolution_y = args.size
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'PNG'
scene.world.color = (.18, .18, .18)
scene.view_settings.view_transform = 'AgX'
scene.view_settings.look = 'None'
# The same cavity cube as scripts/render_catalog_previews.py: an inset and bevels give the
# edge, cavity, dust and drip generators real geometry to read.
bpy.ops.mesh.primitive_cube_add(size=1.6)
mesh = bpy.context.object
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, -.75, 0))
cutter = bpy.context.object
cutter.scale = (1.05, .72, .96)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
bpy.context.view_layer.objects.active = mesh
cut = mesh.modifiers.new('Preview cavity', 'BOOLEAN')
cut.operation, cut.object = 'DIFFERENCE', cutter
bpy.ops.object.modifier_apply(modifier=cut.name)
bpy.data.objects.remove(cutter, do_unlink=True)
mesh.select_set(True)
bpy.context.view_layer.objects.active = mesh
bevel = mesh.modifiers.new('Preview bevel', 'BEVEL')
bevel.width, bevel.segments = .06, 1
bpy.ops.object.modifier_apply(modifier=bevel.name)
bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.uv.smart_project(island_margin=.025)
bpy.ops.object.mode_set(mode='OBJECT')
bpy.ops.object.camera_add(location=(2.9, -4.5, 2.5))
camera = bpy.context.object
camera.rotation_euler = (-camera.location).to_track_quat('-Z', 'Y').to_euler()
camera.data.type, camera.data.ortho_scale = 'ORTHO', 2.7
scene.camera = camera
for location, energy, size, color in (((-3, -4, 4), 700, 3.5, (1, .95, .9)), ((4, -1, 2), 500, 2, (.8, .9, 1)),
                                      ((0, 3, 3), 900, 2, (1, 1, 1))):
    bpy.ops.object.light_add(type='AREA', location=location)
    light = bpy.context.object
    light.data.energy, light.data.size, light.data.color, light.data.shape = energy, size, color, 'DISK'
    light.rotation_euler = (-light.location).to_track_quat('-Z', 'Y').to_euler()
# The label: an emissive text object parented to the camera, in the top-left of the frame.
curve = bpy.data.curves.new('Control label', 'FONT')
curve.size, curve.body = .12, ''
label = bpy.data.objects.new('Control label', curve)
scene.collection.objects.link(label)
label.parent, label.location = camera, (-1.28, 1.14, -4.0)
emissive = bpy.data.materials.new('Label')
emissive.use_nodes = True
nodes = emissive.node_tree.nodes
emission = nodes.new('ShaderNodeEmission')
emission.inputs['Color'].default_value, emission.inputs['Strength'].default_value = (1, 1, 1, 1), 6
emissive.node_tree.links.new(emission.outputs[0], nodes['Material Output'].inputs['Surface'])
label.data.materials.append(emissive)

ts = sets.create(scene, 'Master controls', args.texture_size)
sets.assign(scene, ts, mesh)
ts.bake.selected_to_active = False
ts.bake.curvature_width = 6
bpy.context.view_layer.update()
bake_selected(bpy.context, ts)
for kind in ('POSITION', 'WORLD_NORMAL'):
    image = bake_utility(ts, kind)
    item = ts.baked_maps.add()
    item.kind, item.image = kind, image
out = args.output
out.mkdir(parents=True, exist_ok=True)
tiles = out / 'tiles'
tiles.mkdir(exist_ok=True)
CHANNELS = ('BASE_COLOR', 'ROUGHNESS', 'METALLIC', 'HEIGHT', 'NORMAL')


def outputs():
    return {output.channel: images.pixels(output.image).copy() for output in ts.outputs if output.channel in CHANNELS}


def render(path, text):
    label.data.body = text
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(str(path))
    image.colorspace_settings.name = 'Non-Color'
    pixels = images.pixels(image).copy()
    bpy.data.images.remove(image)
    return pixels


def states(binding):
    """(name, value) of the extremes a control is rendered at."""
    control = binding.control
    if control.kind == 'color':
        return [('magenta', COLORS[0]), ('green', COLORS[1])]
    if control.kind == 'factor':
        return [('min', 0.0), ('max', 1.0)]
    if control.kind == 'multiplier':
        pattern = any(member.field in {'scale', 'noise_scale'} for _, member, _ in binding.members)
        return [('min', 0.25 if pattern else 0.0), ('max', 3.0)]
    prop = binding.owner.bl_rna.properties[binding.field]
    return [('min', prop.hard_min), ('max', prop.hard_max)]


def get(binding):
    if binding.control.computed:
        return list(getattr(binding.owner, binding.field))[binding.index]
    value = getattr(binding.owner, binding.field)
    return tuple(value) if binding.control.kind == 'color' else value


def put(binding, value):
    if binding.control.computed:
        vector = list(getattr(binding.owner, binding.field))
        vector[binding.index] = value
        setattr(binding.owner, binding.field, vector)
    else:
        setattr(binding.owner, binding.field, value)


def describe(value):
    return ('%.2f' % value) if isinstance(value, float) else ('rgb' if isinstance(value, tuple) else str(value))


report = {}
evaluator = LayerEvaluator(scene)
for name in selected:
    started = perf_counter()
    ts.layers.clear()
    ts.active_layer = -1
    folder_id = composer.apply(ts, name).id
    folder = next(item for item in ts.layers if item.id == folder_id)
    evaluator.evaluate_texture_set(ts.id, force=True)
    authored_maps = outputs()
    stem = name.replace(' ', '_')
    authored_tile = render(tiles / f'{stem}__authored.png', f'{name} · as authored')
    rows, records = [], []
    for binding in controls.bindings(ts, folder):
        control = binding.control
        if args.control and control.label not in args.control:
            continue
        record = {'label': control.label, 'kind': control.kind, 'ok': binding.ok, 'reason': binding.reason}
        if not binding.ok:
            records.append(record)
            continue
        before = get(binding)
        record['authored'] = describe(before) if control.kind == 'color' else before
        row, deltas = [], {}
        for state, value in states(binding):
            put(binding, value)
            evaluator.evaluate_texture_set(ts.id, force=True)
            maps = outputs()
            deltas[state] = {channel: float(np.abs(maps[channel][..., :3] - authored_maps[channel][..., :3]).mean())
                             for channel in maps}
            row.append(render(tiles / f'{stem}__{control.label.replace(" ", "_").replace("/", "-")}__{state}.png',
                              f'{control.label} = {describe(value)}'))
        put(binding, before)
        record['deltas'] = deltas
        records.append(record)
        rows.append([row[0], authored_tile, row[1]])
    if rows:
        sheet = np.concatenate([np.concatenate(row, axis=1) for row in reversed(rows)], axis=0)
        write_image(out / f'{stem}.png', sheet[::-1], bits=8, srgb=False)
    report[name] = {'seconds': round(perf_counter() - started, 1), 'controls': records}
    (out / 'report.json').write_text(json.dumps(report, indent=1), encoding="utf-8")
    print('MASTER_CONTROLS', name, len(records), round(perf_counter() - started, 1), flush=True)
open_painter.unregister()
