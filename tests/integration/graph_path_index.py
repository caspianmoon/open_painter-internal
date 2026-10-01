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
    # The plan's collection fingerprint reads stored properties: the sizes RNA reports.
    from open_painter.evaluation.shader_graph import _collection_print
    for layer in ts.layers:
        rna = (len(layer.masks), len(layer.cache), tuple(len(prop.effects) for prop in layer.channels))
        assert _collection_print(layer) == rna, (layer.name, _collection_print(layer), rna)
    assert any(any(print_[2]) and print_[0] for print_ in map(_collection_print, ts.layers))
    assert len(paths) == len({s.as_pointer() for s in structs}), (len(paths), len(structs))
    # Paths are spelled from the Scene, as ``path_from_id`` did before stacks moved into
    # layer stores (``layer_store.scene_path``): bindings and provenance keep them.
    from open_painter.core.layer_store import scene_path
    for struct in structs:
        assert paths[struct.as_pointer()] == scene_path(struct), (paths[struct.as_pointer()], scene_path(struct))

    # A patch keeps the index of a layer that only moved in the array: its structs follow its
    # index cell, its own address is keyed again (``Graph._refresh_paths``).
    from types import SimpleNamespace
    from open_painter.evaluation.shader_graph import Graph

    def every_struct():
        found = [ts, ts.bake, *ts.baked_maps]
        for layer in ts.layers:
            found.append(layer)
            for prop in layer.channels:
                found.extend((prop, *prop.effects))
            for mask in layer.masks:
                found.extend((mask, mask.effect, mask.influence, mask.mesh))
            found.extend(layer.cache)
        return found

    graph = Graph.__new__(Graph)
    graph._head, graph._paths = [graph], None   # the live graph is itself (``Graph._live``)
    graph.ts = ts
    for struct in every_struct():
        assert graph._path(struct) == scene_path(struct)

    def moved_since(before):
        now = {layer.id: layer.as_pointer() for layer in ts.layers}
        plan = SimpleNamespace(ids=tuple(layer.id for layer in ts.layers), pointers=now,
                               layers={layer.id: layer for layer in ts.layers})
        return plan, {identifier for identifier, pointer in now.items() if before.get(identifier) != pointer}

    for edit in ('insert', 'move'):
        before = {layer.id: layer.as_pointer() for layer in ts.layers}
        if edit == 'insert':
            ts.active_layer = 1
            inserted = stack.add(ts, 'FILL', 'Inserted').id
        else:
            stack.move(ts, stack.index_of(ts, ts.layers[0].id), 3)
        plan, moved = moved_since(before)
        edited = {inserted} if edit == 'insert' else set()
        assert moved - edited, (edit, 'no layer struct moved')
        graph._refresh_paths(plan, edited, moved)
        for struct in every_struct():
            assert graph._path(struct) == scene_path(struct), (edit, graph._path(struct), scene_path(struct))
    print('GRAPH_PATH_INDEX_PASS', len(structs), flush=True)
finally:
    open_painter.unregister()
