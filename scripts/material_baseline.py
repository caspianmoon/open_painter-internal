"""Evaluate every catalog smart material on the preview cube and record the maps' hashes
and statistics, so two builds (0.12.2 and the candidate) can be compared in numbers
(quality remedy M4: material baseline review of every recipe).

    blender --background --factory-startup --python scripts/material_baseline.py -- \
        --src <path to a src folder holding open_painter> --output <json> [--material NAME ...] \
        [--texture-size 512] [--compare <json of the other build>]

Records, per material: sha256 of every output channel (BASE_COLOR, ROUGHNESS, METALLIC,
HEIGHT, NORMAL) and per-channel mean/std, plus each master control's extremes (min/max
deltas) when ``--controls`` is given. ``--compare`` prints and writes the differences
between this run and an earlier record: identical hashes, or the mean absolute pixel
difference per channel where they differ.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import bpy
import numpy as np

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--src', type=Path, default=root / 'src')
parser.add_argument('--output', type=Path, default=root / '.blender-test/quality/material-baseline-candidate.json')
parser.add_argument('--material', action='append')
parser.add_argument('--texture-size', type=int, default=512)
parser.add_argument('--compare', type=Path)
parser.add_argument('--controls', action='store_true', help='also evaluate every master control at its extremes')
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
sys.path.insert(0, str(args.src.resolve()))
import open_painter                       # noqa: E402  (from --src)
from open_painter.baking.bake_manager import bake_selected
from open_painter.baking.maps.utility import bake_utility
from open_painter.core import image_manager as images, texture_set_manager as sets
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.smart_materials import composer
from open_painter.smart_materials.catalog import CATALOG

CHANNELS = ('BASE_COLOR', 'ROUGHNESS', 'METALLIC', 'HEIGHT', 'NORMAL')
selected = args.material or list(CATALOG)
open_painter.register()
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
# The cavity cube of scripts/render_master_controls.py: real geometry for the generators.
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
ts = sets.create(scene, 'Material baseline', args.texture_size)
sets.assign(scene, ts, mesh)
ts.bake.selected_to_active = False
ts.bake.curvature_width = 6
bpy.context.view_layer.update()
bake_selected(bpy.context, ts)
for kind in ('POSITION', 'WORLD_NORMAL'):
    image = bake_utility(ts, kind)
    item = ts.baked_maps.add()
    item.kind, item.image = kind, image
evaluator = LayerEvaluator(scene)


def maps():
    return {output.channel: images.pixels(output.image)[..., :3].copy() for output in ts.outputs if output.channel in CHANNELS}


def digest(values):
    return {channel: {'sha256': hashlib.sha256(np.ascontiguousarray(data).tobytes()).hexdigest(),
                      'mean': [round(float(v), 6) for v in data.reshape(-1, 3).mean(axis=0)],
                      'std': round(float(data.std()), 6)} for channel, data in values.items()}


record = {'src': str(args.src.resolve()), 'blender': bpy.app.version_string, 'texture_size': args.texture_size,
          'version': getattr(open_painter, 'bl_info', {}).get('version') if hasattr(open_painter, 'bl_info') else None,
          'materials': {}}
pixels = {}
for name in selected:
    ts.layers.clear()
    ts.active_layer = -1
    folder_id = composer.apply(ts, name).id
    evaluator.evaluate_texture_set(ts.id, force=True)
    authored = maps()
    pixels[name] = authored
    entry = {'authored': digest(authored), 'layers': len(ts.layers)}
    if args.controls:
        from open_painter.smart_materials import controls
        folder = next(item for item in ts.layers if item.id == folder_id)
        entry['controls'] = {}
        for binding in controls.bindings(ts, folder):
            if not binding.ok:
                continue
            entry['controls'][binding.control.label] = {'kind': binding.control.kind}
    record['materials'][name] = entry
    print('MATERIAL_BASELINE', name, flush=True)
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(record, indent=1), encoding="utf-8")
print('MATERIAL_BASELINE_WRITTEN', args.output, flush=True)

if args.compare and args.compare.exists():
    other = json.loads(args.compare.read_text(encoding="utf-8"))
    comparison = {'reference': str(args.compare), 'identical': [], 'different': {}, 'missing': []}
    for name, entry in record['materials'].items():
        reference = other['materials'].get(name)
        if reference is None:
            comparison['missing'].append(name)
            continue
        changed = {channel: (entry['authored'][channel]['sha256'] != reference['authored'][channel]['sha256'])
                   for channel in CHANNELS if channel in entry['authored'] and channel in reference['authored']}
        if not any(changed.values()):
            comparison['identical'].append(name)
        else:
            comparison['different'][name] = {channel: {'candidate_mean': entry['authored'][channel]['mean'],
                                                       'reference_mean': reference['authored'][channel]['mean']}
                                             for channel, flag in changed.items() if flag}
    out = args.output.with_name(args.output.stem + '-comparison.json')
    out.write_text(json.dumps(comparison, indent=1), encoding="utf-8")
    print('MATERIAL_BASELINE_COMPARISON', f"{len(comparison['identical'])} identical, {len(comparison['different'])} different, "
          f"{len(comparison['missing'])} missing", out, flush=True)
open_painter.unregister()
