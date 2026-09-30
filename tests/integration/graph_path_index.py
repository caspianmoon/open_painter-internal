"""The compiler's struct path index spells every path exactly as RNA does."""
from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import layer_stack as stack, texture_set_manager as sets
from open_painter.evaluation.shader_graph import path_index
from open_painter.utils.ids import new_id

open_painter.register()
try:
    scene = bpy.context.scene
    sets.create(scene, 'Decoy', 512)          # the tested set is not the first one
    ts = sets.create(scene, 'Paths', 512)
    for index in range(4):
        layer = stack.add(ts, 'FOLDER' if index == 2 else 'FILL', f'Layer {index}')
        if layer.type == 'FILL':
            effect = stack.channel_for(layer, 'BASE_COLOR').effects.add()
            effect.kind = 'LEVELS'
            stack.channel_for(layer, 'ROUGHNESS').effects.add().kind = 'LEVELS'
            for source in ('PROCEDURAL', 'FILTER'):
                mask = layer.masks.add()
                mask.id, mask.source = new_id(), source
            layer.cache_mode = 'CACHED'
    image = bpy.data.images.new('Paths curvature', 8, 8)
    baked = ts.baked_maps.add()
    baked.kind, baked.image = 'CURVATURE', image
    paths = path_index(ts)
    structs = [ts, ts.bake, *ts.baked_maps]
    for layer in ts.layers:
        structs.append(layer)
        for prop in layer.channels:
            structs.extend((prop, *prop.effects))
        for mask in layer.masks:
            structs.extend((mask, mask.effect, mask.influence, mask.mesh))
        structs.extend(layer.cache)
    assert any(len(layer.cache) for layer in ts.layers)
    assert len(paths) == len({s.as_pointer() for s in structs}), (len(paths), len(structs))
    for struct in structs:
        assert paths[struct.as_pointer()] == struct.path_from_id(), (paths[struct.as_pointer()], struct.path_from_id())
    print('GRAPH_PATH_INDEX_PASS', len(structs), flush=True)
finally:
    open_painter.unregister()
