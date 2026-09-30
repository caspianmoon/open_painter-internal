"""Bypass (0.9.0): a preview-only kill switch that removes a layer's units and CPU work.

The shader with a bypassed layer matches the CPU reference with that layer
hidden; the CPU reference itself (export, snapshots) keeps evaluating the layer.
"""
import json
from pathlib import Path
import sys
from time import monotonic

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import image_manager as images, layer_stack as stack, material_manager
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache, metrics, purpose, shader_preview
from open_painter.evaluation.composite import Composer
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.shader_graph import Graph
from open_painter.utils.ids import new_id


def revisions(info):
    """Structural revisions of the session: a rebuild, or a patch of its own graph (EngineGraph.patch)."""
    return info['graph_builds'] + info['graph_patches']


N = 32


class SampledSet:
    def __init__(self, original):
        self.original = original
        self.resolution = str(N)

    def __getattr__(self, name):
        return getattr(self.original, name)


def ready(scene, ts):
    deadline = monotonic() + 30
    while monotonic() < deadline:
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert not info['error'], info
        if shader_preview.current(scene, ts):
            return info
    raise AssertionError(shader_preview.diagnostics(scene, ts))


def layer_by_id(ts, identifier):
    return next(layer for layer in ts.layers if layer.id == identifier)


def cpu_channel(scene, ts, channel):
    steps = LayerEvaluator(scene).channel_steps(ts.id, channel, preview_resolution=N)
    try:
        while True:
            next(steps)
    except StopIteration as done:
        image = done.value
    assert image is not None
    return images.pixels(image)[..., :3].copy()


def run():
    open_painter.register()
    if bpy.app.timers.is_registered(cache.tick):
        bpy.app.timers.unregister(cache.tick)
    scene = bpy.context.scene
    ts = sets.create(scene, 'Layer bypass', 512)
    ts.preview_backend = 'CPU'
    shader_preview.clear()
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_plane_add(size=2)
    obj = bpy.context.object
    sets.assign(scene, ts, obj)
    bpy.context.view_layer.update()
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 1
    scene.render.bake.margin = 0
    scene.render.bake.use_clear = True
    target = bpy.data.images.new('Layer bypass readback', width=N, height=N, float_buffer=True)
    target.colorspace_settings.name = 'Non-Color'

    # Red base; a blue fill with a spatial blur (a CPU boundary) and a procedural
    # mask; an isolated folder with a green child; a consumer reading the blue
    # layer's published output.
    base = stack.add(ts, 'FILL', 'Base')
    stack.channel_for(base, 'BASE_COLOR').color = (.8, .1, .1, 1.)
    stack.channel_for(base, 'ROUGHNESS').value = .3
    blue = stack.add(ts, 'FILL', 'Blue blur')
    blue_id = blue.id
    stack.channel_for(blue, 'BASE_COLOR').color = (.1, .1, .9, 1.)
    blur = stack.channel_for(blue, 'BASE_COLOR').effects.add()
    blur.kind, blur.radius = 'GAUSSIAN_BLUR', 3.
    checker = blue.masks.add()
    checker.id, checker.source, checker.procedural, checker.noise_scale = new_id(), 'PROCEDURAL', 'CHECKER', 4.
    folder = stack.add(ts, 'FOLDER', 'Folder')
    folder_id = folder.id
    green = stack.add(ts, 'FILL', 'Green child')
    green_id = green.id
    green.parent_id = folder_id
    stack.channel_for(green, 'BASE_COLOR').color = (.1, .8, .2, .6)
    layer_by_id(ts, blue_id).publish_output = True
    consumer = stack.add(ts, 'FILL', 'Consumer')
    consumer_id = consumer.id
    prop = stack.channel_for(consumer, 'ROUGHNESS')
    prop.anchor = blue_id + ':out'
    small = SampledSet(ts)

    def build():
        graph = Graph(scene, small, ts.material)
        with metrics.Metrics() as observed:
            graph.build()
            while graph.pending_resources:
                graph.step_resources(.1)
        return graph, observed.report()['counters']

    def bake(graph, channel):
        tree = ts.material.node_tree
        output = next(n for n in tree.nodes if n.bl_idname == 'ShaderNodeOutputMaterial')
        emission = tree.nodes.new('ShaderNodeEmission')
        bake_node = tree.nodes.new('ShaderNodeTexImage')
        bake_node.image = target
        tree.nodes.active = bake_node
        tree.links.new(emission.outputs[0], output.inputs['Surface'])
        tree.links.new(graph.outputs[channel], emission.inputs['Color'])
        bpy.ops.object.bake(type='EMIT')
        actual = images.pixels(target)[..., :3].copy()
        tree.nodes.remove(bake_node)
        tree.nodes.remove(emission)
        return actual

    def expected_with_hidden(channel, hidden):
        for identifier in hidden:
            layer_by_id(ts, identifier).visible = False
        try:
            return cpu_channel(scene, ts, channel)
        finally:
            for identifier in hidden:
                layer_by_id(ts, identifier).visible = True

    # Live baseline: the blur is a CPU boundary.
    graph, counters = build()
    assert counters.get('shader_resource_requests', 0) >= 1, counters
    live_nodes = graph.node_count
    live_keys = set(graph.units)
    assert ('SRC', blue_id, 'BASE_COLOR', 1) in live_keys and ('MASK', blue_id, '', False) in live_keys
    graph.close()

    # Bypassed: pass-through units, no content/mask units, zero CPU requests.
    layer_by_id(ts, blue_id).bypass = True
    graph, counters = build()
    assert counters.get('shader_resource_requests', 0) == 0, counters
    assert graph.node_count < live_nodes, (graph.node_count, live_nodes)
    assert not any(key[0] in {'SRC', 'MASK'} and key[1] == blue_id for key in graph.units), sorted(graph.units)
    unit = graph.units[('CHANNEL', blue_id, 'BASE_COLOR')]
    assert unit.flags.get('bypass') and not unit.flags.get('stub'), unit.flags
    worst = 0.
    for channel in ('BASE_COLOR', 'ROUGHNESS'):
        actual = bake(graph, channel)
        expected = expected_with_hidden(channel, [blue_id])
        error = float(np.abs(actual - expected).max())
        worst = max(worst, error)
        assert np.allclose(actual, expected, atol=3e-4), ('bypass parity', channel, error)
    print('LAYER_BYPASS_PARITY', json.dumps({'case': 'bypassed_blur_layer', 'max_error': worst,
                                             'nodes': graph.node_count, 'live_nodes': live_nodes}), flush=True)
    graph.close()

    # Export and the CPU reference ignore bypass entirely.
    full = cpu_channel(scene, ts, 'BASE_COLOR')
    layer_by_id(ts, blue_id).bypass = False
    assert np.array_equal(full, cpu_channel(scene, ts, 'BASE_COLOR')), 'CPU reference must ignore bypass'
    LayerEvaluator(scene).evaluate_texture_set(ts.id)
    exported = images.pixels(next(o for o in ts.outputs if o.channel == 'BASE_COLOR').image).copy()
    layer_by_id(ts, blue_id).bypass = True
    LayerEvaluator(scene).evaluate_texture_set(ts.id)
    assert np.array_equal(exported, images.pixels(next(o for o in ts.outputs if o.channel == 'BASE_COLOR').image)), \
        'export must include bypassed layers'
    # ...but CPU work done for the preview (boundary factories, caches) skips it.
    composer = Composer(ts, 'BASE_COLOR', N, {}, {})
    with purpose.preview():
        preview_rows = composer.output(consumer_id, 0, N)
    layer_by_id(ts, blue_id).bypass = False
    layer_by_id(ts, blue_id).visible = False
    hidden_rows = Composer(ts, 'BASE_COLOR', N, {}, {}).output(consumer_id, 0, N)
    layer_by_id(ts, blue_id).visible = True
    assert np.allclose(preview_rows, hidden_rows), 'preview composition must skip bypassed layers'
    print('LAYER_BYPASS_EXPORT_OK', flush=True)

    # A bypassed folder bypasses its children.
    layer_by_id(ts, folder_id).bypass = True
    graph, counters = build()
    assert graph.units[('CHANNEL', green_id, 'BASE_COLOR')].flags.get('bypass')
    assert not any(key[0] == 'SRC' and key[1] == green_id for key in graph.units)
    actual = bake(graph, 'BASE_COLOR')
    expected = expected_with_hidden('BASE_COLOR', [folder_id])
    assert np.allclose(actual, expected, atol=3e-4), float(np.abs(actual - expected).max())
    graph.close()
    layer_by_id(ts, folder_id).bypass = False

    # In the live session the toggle is structural and scoped to the layer's units.
    ts.preview_backend = 'LIVE'
    material_manager.repair(ts)
    ready(scene, ts)
    session = shader_preview.ensure(scene, ts)
    builds = revisions(shader_preview.diagnostics(scene, ts))
    layer_by_id(ts, blue_id).bypass = True
    info = shader_preview.diagnostics(scene, ts)
    assert info['rebuild_pending'], info
    scope = info['rebuild_scope']
    assert isinstance(scope, list), scope
    assert all(key[1] in {blue_id, consumer_id} for key in scope), scope
    ready(scene, ts)
    after = shader_preview.diagnostics(scene, ts)
    assert revisions(after) == builds + 1 and after['patched'] and shader_preview.ensure(scene, ts) is session
    # The graph an undo compiles keeps the bypass structure.
    from open_painter.evaluation.engine.compiler import EngineGraph
    probe = EngineGraph(scene, ts)
    probe.build()
    assert probe.units[('CHANNEL', blue_id, 'BASE_COLOR')].flags.get('bypass')
    probe.close()
    # Helpers used by the menu operator.
    from open_painter.ui.performance import set_bypass
    set_bypass(ts, {blue_id, green_id}, True)
    ready(scene, ts)
    assert layer_by_id(ts, green_id).bypass and layer_by_id(ts, blue_id).bypass
    set_bypass(ts, {layer.id for layer in ts.layers if layer.bypass}, False)
    ready(scene, ts)
    assert not any(layer.bypass for layer in ts.layers)
    # Editing a bypassed layer still invalidates the export (0.9.7): the shader
    # holds no bindings for it, so it cannot narrow what the reference must redo.
    stack.channel_for(layer_by_id(ts, consumer_id), 'BASE_COLOR').enabled = False   # the opaque top fill
    ready(scene, ts)
    LayerEvaluator(scene).evaluate_texture_set(ts.id)
    output = next(o for o in ts.outputs if o.channel == 'BASE_COLOR')
    assert not output.dirty
    before = images.pixels(output.image).copy()
    layer_by_id(ts, blue_id).bypass = True
    ready(scene, ts)
    stack.channel_for(layer_by_id(ts, blue_id), 'BASE_COLOR').color = (.9, .2, .2, 1.)
    shader_preview.tick(scene, ts)
    assert shader_preview.current(scene, ts)
    assert output.dirty, 'an edit to a bypassed layer was dropped from the export'
    LayerEvaluator(scene).evaluate_texture_set(ts.id)
    assert not np.array_equal(before, images.pixels(output.image)), 'export shipped the pre-edit pixels'
    stack.channel_for(layer_by_id(ts, blue_id), 'BASE_COLOR').color = (.1, .1, .9, 1.)
    stack.channel_for(layer_by_id(ts, consumer_id), 'BASE_COLOR').enabled = True
    layer_by_id(ts, blue_id).bypass = False
    ready(scene, ts)
    print('LAYER_BYPASS_EXPORT_DIRTY_OK', flush=True)
    # A folder whose child composite is published: a consumer reads its GROUP anchor while
    # the folder is bypassed (its pass-through unit still exports the Group sockets, and the
    # consumer unit adopted from the previous build wires them by name).
    layer_by_id(ts, folder_id).publish_group = True
    reader = stack.add(ts, 'FILL', 'Reads the folder')
    reader_id = reader.id
    stack.channel_for(reader, 'BASE_COLOR').color = (.9, .9, .1, 1.)
    reading = reader.masks.add()
    reading.id, reading.source, reading.reference_anchor, reading.reference_channel = new_id(), 'REFERENCE', folder_id + ':group', 'BASE_COLOR'
    info = ready(scene, ts)
    assert not info['warnings'], info['warnings']
    layer_by_id(ts, folder_id).bypass = True
    info = ready(scene, ts)
    assert not info.get('error') and not info['warnings'], info
    layer_by_id(ts, folder_id).bypass = False
    ready(scene, ts)
    stack.remove(ts, stack.index_of(ts, reader_id))
    layer_by_id(ts, folder_id).publish_group = False
    ready(scene, ts)
    print('LAYER_BYPASS_PASS', flush=True)
    open_painter.unregister()


run()
