"""Render old/new aging on identical geometry, mesh maps, history and lighting.

blender -b --factory-startup --python scripts/render_aging_review.py -- --output /tmp/aging
"""
import argparse
import json
import sys
from pathlib import Path
import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.aging import document, document_v1, engine
from open_painter.baking.bake_manager import bake_selected
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import shader_preview

parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--case', choices=['paint', 'copper'], default='paint')
parser.add_argument('--years', type=float, default=12)
parser.add_argument('--only', choices=['before', 'after'])
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
args.output.mkdir(parents=True, exist_ok=True)
open_painter.register()
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
scene.render.engine = 'CYCLES'
scene.cycles.device = 'CPU'
scene.cycles.samples = 32
scene.cycles.use_denoising = True
scene.render.resolution_x = scene.render.resolution_y = 800
scene.render.resolution_percentage = 100
scene.view_settings.view_transform = 'AgX'
scene.view_settings.look = 'AgX - Medium High Contrast'
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 1.22))
obj = bpy.context.object
obj.name = 'Industrial inspection cover'
obj.dimensions = (2, .20, 2.4)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
for x in (-.79, .79):
    for z in (.24, 2.20):
        bpy.ops.mesh.primitive_cylinder_add(vertices=48, radius=.09, depth=.6,
                                          location=(x, 0, z), rotation=(1.5707963, 0, 0))
        cutter = bpy.context.object
        bpy.context.view_layer.objects.active = obj
        mod = obj.modifiers.new('Bolt hole', 'BOOLEAN')
        mod.operation, mod.object = 'DIFFERENCE', cutter
        bpy.ops.object.modifier_apply(modifier=mod.name)
        bpy.data.objects.remove(cutter, do_unlink=True)
bpy.ops.object.select_all(action='DESELECT')
obj.select_set(True)
bpy.context.view_layer.objects.active = obj
mod = obj.modifiers.new('Worked edges', 'BEVEL')
mod.width, mod.segments = .012, 3
bpy.ops.object.modifier_apply(modifier=mod.name)
bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.uv.smart_project(island_margin=.025)
bpy.ops.object.mode_set(mode='OBJECT')
ts = sets.create(scene, 'Aging comparison', 512)
sets.assign(scene, ts, obj)
with engine.batch():
    ts.aging.material = 'COPPER' if args.case == 'copper' else 'CARBON_STEEL'
    ts.aging.paint = 'NONE' if args.case == 'copper' else 'POLYURETHANE_PAINT'
    ts.aging.primer = 'NONE' if args.case == 'copper' else 'EPOXY_PRIMER'
    ts.aging.environment = 'COASTAL'
    ts.aging.maintenance = 'NONE'
    ts.aging.usage = 'MODERATE'
    ts.aging.age_years = args.years
    ts.aging.paint_color = (.065, .14, .16)
    ts.aging.seed = 4182
settings = ts.bake
settings.selected_to_active = False
settings.normal = False
settings.ao = settings.curvature = settings.world_normal = settings.position = True
settings.samples, settings.margin = 8, 8
bake_selected(bpy.context, ts)
print('REVIEW_MAPS_READY', flush=True)
bpy.ops.mesh.primitive_plane_add(size=200)
ground = bpy.context.object
mat = bpy.data.materials.new('Neutral studio')
mat.use_nodes = True
mat.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value = (.065, .072, .080, 1)
mat.node_tree.nodes['Principled BSDF'].inputs['Roughness'].default_value = .72
ground.data.materials.append(mat)
scene.world.use_nodes = True
scene.world.node_tree.nodes['Background'].inputs[0].default_value = (.12, .15, .19, 1)
scene.world.node_tree.nodes['Background'].inputs[1].default_value = .4
target = Vector((0, 0, 1.20))
for location, power, size in [((-3, -4, 5), 700, 3), ((3, -2, 2), 280, 2), ((1, 2, 4), 600, 2)]:
    bpy.ops.object.light_add(type='AREA', location=location)
    light = bpy.context.object
    light.data.energy, light.data.size = power, size
    light.rotation_euler = (target - light.location).to_track_quat('-Z', 'Y').to_euler()
bpy.ops.object.camera_add(location=(2.1, -6.5, 3.0))
scene.camera = bpy.context.object
scene.camera.rotation_euler = (target - scene.camera.location).to_track_quat('-Z', 'Y').to_euler()
scene.camera.data.type, scene.camera.data.ortho_scale = 'ORTHO', 3.15
result = engine.prediction(ts)
report = {'case': args.case, 'outcomes': result['state'].outcomes(), 'versions': {}}
for label, builder in [('before', document_v1), ('after', document)]:
    if args.only and args.only != label:
        continue
    with engine.batch(), shader_preview.authoring_batch(ts):
        ts.layers.clear()
        doc = builder.build(result['state'], result['construction'], seed=ts.aging.seed)
        folder = engine._build(ts, doc, ts.aging.seed)
        ts.aging.folder_id = folder.id
        ts.aging.state_data = json.dumps({'recipe_version': doc['version']})
    ts.preview_backend = 'LIVE'
    shader_preview.flush(scene, ts)
    scene.render.filepath = str(args.output / f'{args.case}-{label}.png')
    bpy.ops.render.render(write_still=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output / f'{args.case}-{label}.blend'))
    report['versions'][label] = doc['version']
    print('REVIEW_RENDER', args.case, label, flush=True)
(args.output / f'{args.case}.json').write_text(json.dumps(report, indent=2), encoding="utf-8")
