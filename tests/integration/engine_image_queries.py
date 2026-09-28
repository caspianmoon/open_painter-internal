"""Live image bindings survive cached node membership; optional CPU timings."""
import argparse
import json
from pathlib import Path
import statistics
import sys
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import layer_stack as stack, texture_set_manager as sets
from open_painter.evaluation.engine import headless
from open_painter.evaluation.engine.compiler import EngineGraph
from open_painter.evaluation.engine.runtime import Engine

parser = argparse.ArgumentParser()
parser.add_argument('--report', type=Path)
parser.add_argument('--layers', type=int, default=50)
parser.add_argument('--samples', type=int, default=40)
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])


def scanned(graph, key):
    return [(node, node.image) for node in graph.units[key].tree.nodes
            if node.bl_idname == 'ShaderNodeTexImage']


def queries(graph):
    return {key: graph.unit_images(key) for key in graph.units}


open_painter.register()
graph = engine = None
images = []
try:
    tree = headless.Tree('Live image topology')
    fake = SimpleNamespace(units={'test': SimpleNamespace(tree=tree)})
    query = lambda: EngineGraph.unit_images(fake, 'test')
    assert query() == []
    tree.nodes.new('ShaderNodeMath')
    first = tree.nodes.new('ShaderNodeTexImage')
    assert query() == [(first, None)]
    image_a = bpy.data.images.new('Query A', width=1, height=1, float_buffer=True)
    image_b = bpy.data.images.new('Query B', width=1, height=1, float_buffer=True)
    images.extend((image_a, image_b))
    first.image = image_a
    assert query() == [(first, image_a)]
    first.image = image_b
    assert query() == [(first, image_b)]
    second = tree.nodes.new('ShaderNodeTexImage')
    second.image = image_a
    assert query() == [(first, image_b), (second, image_a)]
    tree.nodes.remove(first)
    first.release_image()
    assert query() == [(second, image_a)]
    # Removing and inserting preserves the count but changes node identity.
    tree.nodes.remove(second)
    second.release_image()
    third = tree.nodes.new('ShaderNodeTexImage')
    assert query() == [(third, None)]
    fake.units['test'].tree = headless.Tree('Replacement unit')
    assert query() == []

    scene = bpy.context.scene
    ts = sets.create(scene, 'Image query regression', 512)
    ts.preview_backend = 'CPU'
    for index in range(args.layers):
        layer = stack.add(ts, 'FILL', f'Layer {index}')
        mask = layer.masks.add()
        mask.source, mask.procedural = 'PROCEDURAL', 'CELLULAR'
    # Include real bound images among arithmetic-heavy units.
    stack.channel_for(ts.layers[0], 'BASE_COLOR').image = image_a
    stack.channel_for(ts.layers[-1], 'ROUGHNESS').image = image_b
    graph = EngineGraph(scene, ts)
    graph.build()
    engine = Engine(scene, ts)
    engine.adopt(graph)
    expected = {key: scanned(graph, key) for key in graph.units}
    assert queries(graph) == expected
    cached_signatures = {key: engine._signature(key) for key in graph.units}
    with patch.object(graph, 'unit_images', lambda key: scanned(graph, key)):
        assert {key: engine._signature(key) for key in graph.units} == cached_signatures
    # Alpha handling must remain live even when topology is warm.
    image_a.alpha_mode = 'CHANNEL_PACKED'
    changed = {key: engine._signature(key) for key in graph.units}
    assert changed != cached_signatures
    with patch.object(graph, 'unit_images', lambda key: scanned(graph, key)):
        assert {key: engine._signature(key) for key in graph.units} == changed

    if args.report:
        samples = {'scanned': [], 'cached': []}
        for index in range(args.samples + 2):
            order = ('scanned', 'cached') if index % 2 == 0 else ('cached', 'scanned')
            for mode in order:
                callback = (lambda key: scanned(graph, key)) if mode == 'scanned' else (
                    lambda key: EngineGraph.unit_images(graph, key))
                with patch.object(graph, 'unit_images', callback):
                    started = perf_counter()
                    actual = {key: engine._signature(key) for key in graph.units}
                    elapsed = (perf_counter() - started) * 1000
                assert actual == changed
                if index >= 2:
                    samples[mode].append(elapsed)
        report = dict(status='PASS', layers=args.layers, units=len(graph.units),
                      nodes=sum(len(unit.tree.nodes) for unit in graph.units.values()),
                      samples=samples, median_ms={key: statistics.median(value)
                                                 for key, value in samples.items()},
                      scope='All engine program signatures; CPU only, no GPU evaluation or viewport FPS')
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report['median_ms']), flush=True)
    print('ENGINE_IMAGE_QUERIES_PASS: live replacements, insertion/removal, alpha metadata and unit replacement', flush=True)
finally:
    if engine is not None:
        engine.close()
    if graph is not None:
        graph.close()
    for image in images:
        if image.name in bpy.data.images:
            bpy.data.images.remove(image)
    open_painter.unregister()
