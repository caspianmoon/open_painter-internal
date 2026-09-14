"""Deterministic CPU workloads. Worn fixture preserves the committed 0.2.0 inputs."""
import bpy
import numpy as np
from copy import deepcopy
import json
from pathlib import Path
from open_painter.core import texture_set_manager as sets, layer_stack as stack, image_manager as images
from open_painter.smart_materials import composer

NAMES = ('worn', 'constant', 'abandoned', 'gaussian_mask', 'warp', 'painted_mask', 'triplanar_modifiers', 'three_sets')


def synthetic_maps(ts, extended=False):
    x, y = np.meshgrid((np.arange(32) + .5) / 32, (np.arange(32) + .5) / 32)
    for kind in (('AO', 'CURVATURE', 'POSITION', 'WORLD_NORMAL') if extended else ('AO', 'CURVATURE')):
        item = ts.baked_maps.add()
        item.kind = kind
        item.image = images.create('Benchmark ' + kind, 32)
        data = np.ones((32, 32, 4), np.float32)
        field = .3 + .7*y if kind == 'AO' else np.clip((.15-abs(x-.5))*8, 0, 1)
        data[..., :3] = field[..., None]
        if kind == 'POSITION':
            data[..., :3] = np.stack((x, y, y), -1)
        elif kind == 'WORLD_NORMAL':
            data[..., :3] = (.5, .5, 1)
        images.write(item.image, data)


def create(scene, name, resolution, *, generated_detail=False):
    result = []
    for index in range(3 if name == 'three_sets' else 1):
        ts = sets.create(scene, 'Texture benchmark ' + str(index), resolution)
        result.append(ts)
        if name in {'worn', 'abandoned', 'three_sets'}:
            synthetic_maps(ts, name == 'abandoned')
            material = 'Abandoned Machinery' if name == 'abandoned' else 'Worn Painted Metal'
            recipe = deepcopy(composer.CATALOG[material])
            if not generated_detail:
                if material == 'Worn Painted Metal':
                    recipe = json.loads(Path(__file__).with_name('worn_v020.json').read_text())
                else:
                    for spec in recipe['layers']:
                        spec.pop('generated_source', None)
            composer.apply_document(ts, recipe, material)
        else:
            for _ in range(20 if name == 'constant' else 1):
                layer = stack.add(ts, 'FILL')
            if name == 'triplanar_modifiers':
                obj = scene.objects.get('Cube')
                ref = ts.assignments.add()
                ref.object = obj
                modifier = obj.modifiers.new('Benchmark bevel', 'BEVEL')
                modifier.width, modifier.segments = .1, 2
                channel = stack.channel_for(layer, 'ROUGHNESS')
                channel.procedural, channel.projection = 'FBM', 'TRIPLANAR'
            elif name in {'gaussian_mask', 'warp', 'painted_mask'}:
                mask = layer.masks.add()
                mask.source, mask.procedural = 'PROCEDURAL', 'FBM'
                if name == 'painted_mask':
                    mask.source = 'PAINT'
                    mask.image = images.create('Authored paint mask', resolution)
                    data = np.ones((resolution, resolution, 4), np.float32)
                    data[..., :3] = 0
                    data[resolution//4:resolution//2, :, :3] = .8
                    images.write(mask.image, data)
                else:
                    effect = layer.masks.add()
                    effect.source = 'FILTER'
                    effect.effect.kind = 'GAUSSIAN_BLUR' if name == 'gaussian_mask' else 'WARP'
                    effect.effect.radius, effect.effect.amount = 3, .02
    return result


def edit(ts):
    layer = next((l for l in ts.layers if l.name == 'Blue paint coat'), next(l for l in ts.layers if l.type == 'FILL'))
    stack.channel_for(layer, 'ROUGHNESS').value += .03
