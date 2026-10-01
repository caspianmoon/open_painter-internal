"""Render actual composed texture materials on a beveled UV preview object."""
import sys
import argparse
import json
import math
import hashlib
from copy import deepcopy
from pathlib import Path
from time import perf_counter
import bpy
import numpy as np

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'src'))
import open_painter
from open_painter.core import texture_set_manager as sets, image_manager as images
from open_painter.smart_materials.catalog import CATALOG
from open_painter.smart_materials.composer import apply_document
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.baking.bake_manager import bake_selected
from open_painter.baking.maps.utility import bake_utility
from open_painter.export.image_writer import write as write_image
from open_painter.export.surface_normal import combined as combined_normal
from open_painter.smart_materials.generated_sources import ROOT as SOURCE_ROOT

parser = argparse.ArgumentParser()
parser.add_argument('--material', action='append', choices=list(CATALOG),
                    help='Render selected materials only; may be repeated')
parser.add_argument('--output', type=Path, default=root / 'src/open_painter/assets/previews')
parser.add_argument('--size', type=int, default=256)
parser.add_argument('--texture-size', type=int, default=1024, choices=(512, 1024, 2048, 4096))
parser.add_argument('--samples', type=int, default=16)
parser.add_argument('--shape', choices=('cavity', 'curved'), default='cavity')
parser.add_argument('--closeup', action='store_true',
                    help='Crop the same actual surface with a 1.2-unit orthographic camera; lights stay fixed')
parser.add_argument('--channel-maps', action='store_true',
                    help='Save unscaled 16-bit Height, authored Normal, and combined surface Normal PNGs')
parser.add_argument('--label', help='Human-readable identity of this recipe/render revision')
parser.add_argument('--overwrite', action='store_true', help='Explicitly replace existing preview files')
parser.add_argument('--recipes', type=Path, help='Explicit saved recipe snapshot for reproducible comparisons')
parser.add_argument('--legacy-detail', action='store_true',
                    help='Render the preserved procedural recipes before generated detail, for comparison')
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
selected = args.material or list(CATALOG)
if not args.overwrite:
    collisions = [args.output / (name.replace(' ', '_') + '.png') for name in selected]
    collisions.append(args.output / 'render-context.json')
    if any(file.exists() for file in collisions):
        parser.error('Output already contains previews. Choose a fresh directory or use --overwrite.')

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
scene.view_settings.exposure = 0
scene.view_settings.gamma = 1
if args.shape == 'curved':
    bpy.ops.mesh.primitive_uv_sphere_add(segments=96, ring_count=64, radius=1)
else:
    bpy.ops.mesh.primitive_cube_add(size=1.6)
mesh = bpy.context.object
# A real inset cavity is essential: a wholly convex sample cannot demonstrate
# cavity grime, patina or grease. Boolean geometry is baked before any recipe.
if args.shape == 'curved':
    # Continuous original sphere UVs expose oversized masks and repetition.
    # Two shallow actual geometric grooves also exercise baked cavity/edge data.
    for vertex in mesh.data.vertices:
        z = vertex.co.z
        depression = .025 * sum(math.exp(-((z - center) / .035) ** 2)
                                for center in (-.48, .48))
        vertex.co *= 1 - depression
    for polygon in mesh.data.polygons:
        polygon.use_smooth = True
else:
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
camera.data.ortho_scale = 1.2 if args.closeup else 2.7
scene.camera = camera
lighting = [((-3,-4,4),700,3.5,(1,.95,.9)),((4,-1,2),500,2,(.8,.9,1)),((0,3,3),900,2,(1,1,1))]
for location, energy, size, color in lighting:
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
recipes = json.loads(args.recipes.read_text(encoding="utf-8")) if args.recipes else CATALOG
records = []
context = {
    'schema_version': 2, 'label': args.label or args.output.name,
    'blender': bpy.app.version_string, 'shape': args.shape, 'closeup': args.closeup,
    'texture_resolution': args.texture_size, 'render_resolution': args.size,
    'samples': args.samples, 'engine': scene.render.engine, 'device': scene.cycles.device,
    'denoising': scene.cycles.use_denoising, 'seed': scene.cycles.seed,
    'camera': {'location': list(camera.location), 'rotation': list(camera.rotation_euler),
               'type': camera.data.type, 'ortho_scale': camera.data.ortho_scale},
    'lighting': lighting, 'world_color': list(scene.world.color),
    'color_management': {'view_transform': scene.view_settings.view_transform,
                         'look': scene.view_settings.look, 'exposure': scene.view_settings.exposure,
                         'gamma': scene.view_settings.gamma},
    'geometry_sha256': hashlib.sha256(np.array([v.co[:] for v in mesh.data.vertices], np.float32).tobytes()).hexdigest(),
    'uv_sha256': hashlib.sha256(np.array([uv.uv[:] for uv in mesh.data.uv_layers.active.data], np.float32).tobytes()).hexdigest(),
    'baked_maps_sha256': {item.kind: hashlib.sha256(images.pixels(item.image).tobytes()).hexdigest()
                           for item in ts.baked_maps},
    'recipes_file': str(args.recipes) if args.recipes else None,
    'legacy_detail': args.legacy_detail, 'channel_maps': args.channel_maps,
    'channel_encoding': '16-bit PNG, linear data, no contrast remap; OpenGL normals' if args.channel_maps else None,
    'records': records,
}
for name in selected:
    start = perf_counter()
    ts.layers.clear()
    ts.active_layer = -1
    recipe = deepcopy(recipes[name])
    if args.legacy_detail:
        for spec in recipe['layers']:
            spec.pop('generated_source', None)
    apply_document(ts, recipe, name)
    LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
    stem = name.replace(' ', '_')
    height = images.pixels(next(out.image for out in ts.outputs if out.channel == 'HEIGHT'))
    scalar = height[..., 0]
    gradients = np.gradient(scalar)
    stats = {'minimum': float(scalar.min()), 'maximum': float(scalar.max()),
             'mean': float(scalar.mean()), 'standard_deviation': float(scalar.std()),
             'range': float(np.ptp(scalar)), 'percentile_01': float(np.percentile(scalar, 1)),
             'percentile_99': float(np.percentile(scalar, 99)),
             'gradient_rms_per_texel': float(np.sqrt(np.mean(gradients[0] ** 2 + gradients[1] ** 2))),
             'height_distance': float(ts.height_distance),
             'world_height_span': float(np.ptp(scalar) * ts.height_distance)}
    maps = {}
    if args.channel_maps:
        normal = images.pixels(next(out.image for out in ts.outputs if out.channel == 'NORMAL'))
        combined = combined_normal(ts, normal, height)
        for channel, values in (('height', height), ('normal_raw', normal), ('normal_combined', combined)):
            filename = stem + '_' + channel + '.png'
            write_image(path / filename, values[::-1], bits=16, srgb=False)
            maps[channel] = filename
        stats['combined_normal_xy_rms'] = float(np.sqrt(np.mean((combined[..., :2] * 2 - 1) ** 2)))
    source_ids = set()
    for spec in recipe['layers']:
        for settings in [spec, spec.get('mask', {}), *spec.get('mask_effects', ())]:
            if settings.get('generated_source'):
                source_ids.add(settings['generated_source']['id'])
    sources = {identifier: hashlib.sha256((SOURCE_ROOT / (identifier + '.png')).read_bytes()).hexdigest()
               for identifier in sorted(source_ids)}
    scene.render.filepath = str(path / (stem + '.png'))
    bpy.ops.render.render(write_still=True)
    records.append({'material': name, 'seconds': perf_counter()-start,
                    'recipe': recipe, 'render': stem + '.png', 'maps': maps,
                    'height_stats': stats, 'source_sha256': sources})
    (path / 'render-context.json').write_text(json.dumps(context, indent=2), encoding="utf-8")
    print('CATALOG_PREVIEW', name, round(perf_counter()-start,3), flush=True)
open_painter.unregister()
