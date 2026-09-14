"""Render actual composed texture materials on a beveled UV preview object."""
import sys
import argparse
from copy import deepcopy
from pathlib import Path
from time import perf_counter
import bpy
from mathutils import Vector

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'src'))
import open_painter
from open_painter.core import texture_set_manager as sets, layer_stack
from open_painter.smart_materials.catalog import CATALOG
from open_painter.smart_materials.composer import apply_document
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.baking.bake_manager import bake_selected
from open_painter.baking.maps.utility import bake_utility

parser = argparse.ArgumentParser()
parser.add_argument('--material', action='append', choices=list(CATALOG),
                    help='Render selected materials only; may be repeated')
parser.add_argument('--output', type=Path, default=root / 'src/open_painter/assets/previews')
parser.add_argument('--size', type=int, default=256)
parser.add_argument('--texture-size', type=int, default=1024, choices=(512, 1024, 2048, 4096))
parser.add_argument('--samples', type=int, default=16)
parser.add_argument('--legacy-detail', action='store_true',
                    help='Render the preserved procedural recipes before generated detail, for comparison')
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])

open_painter.register()
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
scene.render.engine = 'CYCLES'
scene.cycles.device = 'CPU'
scene.cycles.samples = args.samples
scene.cycles.use_denoising = True
scene.render.resolution_x = scene.render.resolution_y = args.size
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'PNG'
scene.world.color = (.18, .18, .18)
bpy.ops.mesh.primitive_cube_add(size=1.6)
mesh = bpy.context.object
# A real inset cavity is essential: a wholly convex sample cannot demonstrate
# cavity grime, patina or grease. Boolean geometry is baked before any recipe.
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, -.75, 0))
cutter = bpy.context.object
cutter.scale = (1.05, .72, .96)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
bpy.context.view_layer.objects.active = mesh
cut = mesh.modifiers.new('Preview cavity', 'BOOLEAN')
cut.operation = 'DIFFERENCE'
cut.object = cutter
bpy.ops.object.modifier_apply(modifier=cut.name)
bpy.data.objects.remove(cutter, do_unlink=True)
mesh.select_set(True)
bpy.context.view_layer.objects.active = mesh
bevel = mesh.modifiers.new('Preview bevel', 'BEVEL')
bevel.width = .06
bevel.segments = 1
bpy.ops.object.modifier_apply(modifier=bevel.name)
bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.uv.smart_project(island_margin=.025)
bpy.ops.object.mode_set(mode='OBJECT')
bpy.ops.object.camera_add(location=(2.9, -4.5, 2.5))
camera = bpy.context.object
camera.rotation_euler = (-camera.location).to_track_quat('-Z', 'Y').to_euler()
camera.data.type = 'ORTHO'
camera.data.ortho_scale = 2.7
scene.camera = camera
for location, energy, size, color in [((-3,-4,4),700,3.5,(1,.95,.9)),((4,-1,2),500,2,(.8,.9,1)),((0,3,3),900,2,(1,1,1))]:
    bpy.ops.object.light_add(type='AREA', location=location)
    light = bpy.context.object
    light.data.energy, light.data.size, light.data.color = energy, size, color
    light.data.shape = 'DISK'
    light.rotation_euler = (-light.location).to_track_quat('-Z','Y').to_euler()
ts = sets.create(scene, 'Material preview', args.texture_size)
sets.assign(scene, ts, mesh)
ts.bake.selected_to_active = False
ts.bake.curvature_width = 6
bpy.context.view_layer.update()
bake_selected(bpy.context, ts)
for kind in ('POSITION','WORLD_NORMAL'):
    image = bake_utility(ts, kind)
    item = ts.baked_maps.add()
    item.kind, item.image = kind, image
path = args.output
path.mkdir(parents=True, exist_ok=True)
for name in args.material or CATALOG:
    start = perf_counter()
    ts.layers.clear()
    ts.active_layer = -1
    recipe = deepcopy(CATALOG[name])
    if args.legacy_detail:
        for spec in recipe['layers']:
            spec.pop('generated_source', None)
    apply_document(ts, recipe, name)
    LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
    scene.render.filepath = str(path / (name.replace(' ','_')+'.png'))
    bpy.ops.render.render(write_still=True)
    print('CATALOG_PREVIEW', name, round(perf_counter()-start,3), flush=True)
open_painter.unregister()
