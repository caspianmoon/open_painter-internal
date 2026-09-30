"""Per-unit shader compilation: parity, uniform visibility, partial rebuilds, stubs."""
import json
from pathlib import Path
import sys
from time import monotonic

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.constants import CHANNELS
from open_painter.core import image_manager as images, layer_stack as stack, material_manager
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache, shader_preview
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


def settled(scene, ts):
    """One tick renders what the RNA callback bound: the images show the edit."""
    shader_preview.tick(scene, ts)
    return shader_preview.current(scene, ts)


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
    ts = sets.create(scene, 'Shader units', 512)
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
    target = bpy.data.images.new('Shader units readback', width=N, height=N, float_buffer=True)
    target.colorspace_settings.name = 'Non-Color'
    report = {'cases': []}

    # Stack: base, an isolated folder (masked fill + multiply fill), a passthrough
    # folder with a fill whose mask reads the base's filtered roughness.
    base = stack.add(ts, 'FILL', 'Base')
    base_id = base.id
    stack.channel_for(base, 'BASE_COLOR').color = (.2, .5, .7, 1.)
    rough = stack.channel_for(base, 'ROUGHNESS')
    rough.value = .35
    levels = rough.effects.add()
    levels.kind, levels.black, levels.white, levels.gamma = 'LEVELS', .1, .9, 1.4
    folder = stack.add(ts, 'FOLDER', 'Isolated')
    folder_id = folder.id
    folder.group_mode = 'ISOLATED'
    inner = stack.add(ts, 'FILL', 'Masked inner')
    inner_id = inner.id
    inner.parent_id = folder_id
    stack.channel_for(inner, 'BASE_COLOR').color = (.9, .3, .1, 1.)
    stack.channel_for(inner, 'ROUGHNESS').value = .8
    checker = inner.masks.add()
    checker.id, checker.source, checker.procedural, checker.noise_scale = new_id(), 'PROCEDURAL', 'CHECKER', 3.
    multiply = stack.add(ts, 'FILL', 'Multiply inner')
    multiply_id = multiply.id
    multiply.parent_id = folder_id
    multiply.blend_mode = 'MULTIPLY'
    multiply.opacity = .6
    stack.channel_for(multiply, 'BASE_COLOR').color = (.4, .8, .6, .7)
    passthrough = stack.add(ts, 'FOLDER', 'Passthrough')
    passthrough_id = passthrough.id
    passthrough.group_mode = 'PASSTHROUGH'
    consumer = stack.add(ts, 'FILL', 'Consumer')
    consumer_id = consumer.id
    consumer.parent_id = passthrough_id
    stack.channel_for(consumer, 'BASE_COLOR').color = (.1, .1, .9, 1.)
    reference = consumer.masks.add()
    reference.id, reference.source = new_id(), 'REFERENCE'
    reference.reference_layer, reference.reference_channel = base_id, 'ROUGHNESS'
    reference.reference_stage = 'POST_FILTER'
    small = SampledSet(ts)

    def parity(label, channels=('BASE_COLOR', 'ROUGHNESS')):
        graph = Graph(scene, small, ts.material)
        graph.build()
        while graph.pending_resources:
            graph.step_resources(.1)
        tree = ts.material.node_tree
        output = next(n for n in tree.nodes if n.bl_idname == 'ShaderNodeOutputMaterial')
        emission = tree.nodes.new('ShaderNodeEmission')
        bake_node = tree.nodes.new('ShaderNodeTexImage')
        bake_node.image = target
        tree.nodes.active = bake_node
        tree.links.new(emission.outputs[0], output.inputs['Surface'])
        worst = 0.
        for channel in channels:
            tree.links.new(graph.outputs[channel], emission.inputs['Color'])
            bpy.ops.object.bake(type='EMIT')
            actual = images.pixels(target)[..., :3]
            expected = cpu_channel(scene, ts, channel)
            error = float(np.abs(actual - expected).max())
            worst = max(worst, error)
            assert np.allclose(actual, expected, atol=3e-4), (label, channel, error)
        record = {'case': label, 'max_error': worst, 'units': len(graph.units), 'nodes': graph.node_count}
        report['cases'].append(record)
        print('SHADER_UNITS_PARITY', json.dumps(record), flush=True)
        tree.nodes.remove(bake_node)
        tree.nodes.remove(emission)
        graph.close()
        material_manager.repair(ts)

    parity('folders_and_reference')
    layer_by_id(ts, inner_id).visible = False
    parity('hidden_inner_layer')
    layer_by_id(ts, folder_id).visible = False
    parity('hidden_isolated_folder')
    layer_by_id(ts, folder_id).visible = True
    layer_by_id(ts, inner_id).visible = True
    layer_by_id(ts, base_id).visible = False
    parity('hidden_published_source')
    layer_by_id(ts, base_id).visible = True
    levels.enabled = False
    parity('disabled_pointwise_effect')
    levels.enabled = True

    # Runtime: visibility and pointwise enable flags are uniforms.
    ts.preview_backend = 'LIVE'
    material_manager.repair(ts)
    info = ready(scene, ts)
    graph = shader_preview.ensure(scene, ts)
    identities = graph.node_identities()
    trees = {key: unit.tree.as_pointer() for key, unit in graph.units.items()}
    builds = revisions(info)
    inner = layer_by_id(ts, inner_id)
    inner.visible = False
    info = shader_preview.diagnostics(scene, ts)
    assert not info['rebuild_pending'] and settled(scene, ts), info
    assert shader_preview.ensure(scene, ts) is graph and graph.node_identities() == identities
    assert info['updated_bindings'] == 1 and revisions(info) == builds, info
    assert 'BASE_COLOR' in info['affected_channels'] and 'ROUGHNESS' in info['affected_channels'], info
    inner.visible = True
    assert settled(scene, ts) and shader_preview.ensure(scene, ts) is graph
    rough = stack.channel_for(layer_by_id(ts, base_id), 'ROUGHNESS')
    rough.effects[0].enabled = False
    assert settled(scene, ts) and shader_preview.ensure(scene, ts) is graph
    assert revisions(shader_preview.diagnostics(scene, ts)) == builds
    rough.effects[0].enabled = True
    assert settled(scene, ts)

    # A channel blend-mode change recompiles one unit, in place; every other unit stays.
    stack.channel_for(layer_by_id(ts, multiply_id), 'ROUGHNESS').blend_mode = 'SCREEN'
    assert not shader_preview.current(scene, ts)
    assert shader_preview.diagnostics(scene, ts)['rebuild_scope'] == [('CHANNEL', multiply_id, 'ROUGHNESS')]
    info = ready(scene, ts)
    rebuilt = shader_preview.ensure(scene, ts)
    assert rebuilt is graph and info['patched'] and info['rebuilt_units'] == 1, info
    assert info['reused_units'] == len(trees) - 1, info
    for key, pointer in trees.items():
        if key != ('CHANNEL', multiply_id, 'ROUGHNESS'):
            assert rebuilt.units[key].tree.as_pointer() == pointer, key
    assert rebuilt.units[('CHANNEL', multiply_id, 'ROUGHNESS')].tree.as_pointer() != trees[('CHANNEL', multiply_id, 'ROUGHNESS')]
    graph, trees = rebuilt, {key: unit.tree.as_pointer() for key, unit in rebuilt.units.items()}
    builds = revisions(info)

    # Adopted units keep working bindings and dependency closure.
    stack.channel_for(layer_by_id(ts, inner_id), 'ROUGHNESS').value = .55
    info = shader_preview.diagnostics(scene, ts)
    assert info['updated_bindings'] == 1 and settled(scene, ts), info
    assert info['affected_channels'] == ['ROUGHNESS'], info
    assert shader_preview.diagnostics(scene, ts)['engine']['channels'] == ['ROUGHNESS'], 'the edit re-rendered other channels'

    # Adding a mask reallocates that layer's mask collection: only the units that
    # captured those masks (its mask chain) rebuild; everything else moves over.
    added = layer_by_id(ts, consumer_id).masks.add()
    added.id, added.source, added.procedural = new_id(), 'PROCEDURAL', 'STRIPES'
    layer_by_id(ts, consumer_id).opacity = .95  # a callback publishes the structural change
    info = ready(scene, ts)
    rebuilt = shader_preview.ensure(scene, ts)
    assert revisions(info) == builds + 1, info
    mask_key = ('MASK', consumer_id, '', False)
    assert info['rebuilt_units'] == 1 and rebuilt.units[mask_key].tree.as_pointer() != trees[mask_key], info
    assert all(rebuilt.units[key].tree.as_pointer() == pointer for key, pointer in trees.items()
               if key in rebuilt.units and key != mask_key), 'unrelated units were rebuilt'
    graph, trees = rebuilt, {key: unit.tree.as_pointer() for key, unit in rebuilt.units.items()}
    builds = revisions(info)

    # A published source gaining an effect rebuilds its prefix and its consumers' mask.
    base_rough = stack.channel_for(layer_by_id(ts, base_id), 'ROUGHNESS')
    gamma = base_rough.effects.add()
    gamma.kind, gamma.gamma = 'GAMMA', .8
    layer_by_id(ts, base_id).opacity = .99
    info = ready(scene, ts)
    rebuilt = shader_preview.ensure(scene, ts)
    consumer_channels = [key for key in rebuilt.units if key[0] == 'CHANNEL' and key[1] == consumer_id]
    assert consumer_channels and all(rebuilt.units[key].tree.as_pointer() == trees[key] for key in consumer_channels)
    assert ('SRC', base_id, 'ROUGHNESS', 2) in rebuilt.units and ('SRC', base_id, 'ROUGHNESS', 1) not in rebuilt.units
    assert any(key[0] == 'MASK' and key[1] == consumer_id and rebuilt.units[key].tree.as_pointer() != trees.get(key)
               for key in rebuilt.units), 'the consumer mask did not follow its published source'
    graph = rebuilt

    # Mutual cross-layer references compile; a reference cycle is refused.
    left = stack.add(ts, 'FILL', 'Left')
    left_id = left.id
    right = stack.add(ts, 'FILL', 'Right')
    right_id = right.id
    left_mask = layer_by_id(ts, left_id).masks.add()
    left_mask.id, left_mask.source = new_id(), 'REFERENCE'
    left_mask.reference_layer, left_mask.reference_channel = right_id, 'BASE_COLOR'
    right_mask = layer_by_id(ts, right_id).masks.add()
    right_mask.id, right_mask.source = new_id(), 'REFERENCE'
    right_mask.reference_layer, right_mask.reference_channel = left_id, 'ROUGHNESS'
    layer_by_id(ts, right_id).opacity = .9
    info = ready(scene, ts)
    assert shader_preview.current(scene, ts), info
    # right.mask -> left.mask -> right.BASE_COLOR is acyclic; both masks reading
    # each other's whole mask stack is a cycle and must be refused.
    for identifier in (left_id, right_id):
        item = layer_by_id(ts, identifier).masks[0]
        item.reference_channel, item.reference_mask = 'MASK', ''
    shader_preview.tick(scene, ts)
    info = shader_preview.diagnostics(scene, ts)
    assert info['error'] and 'cycle' in info['error'].lower(), info
    layer_by_id(ts, left_id).masks[0].reference_channel = 'BASE_COLOR'
    layer_by_id(ts, right_id).masks[0].reference_channel = 'ROUGHNESS'
    ready(scene, ts)

    # A hidden layer with invalid content compiles as a stub with a warning; the
    # error surfaces when the layer is shown and clears when it is fixed.
    broken = stack.add(ts, 'FILL', 'Hidden broken')
    broken_id = broken.id
    broken.visible = False
    bad = stack.channel_for(layer_by_id(ts, broken_id), 'ROUGHNESS').effects.add()
    bad.kind, bad.black, bad.white = 'LEVELS', .8, .2
    layer_by_id(ts, broken_id).opacity = .5
    info = ready(scene, ts)
    assert any('Hidden broken' in warning for warning in info.get('warnings', [])), info
    layer_by_id(ts, broken_id).visible = True
    shader_preview.tick(scene, ts)
    info = shader_preview.diagnostics(scene, ts)
    assert info['state'] == 'FAILED' and 'White level' in info['error'], info
    bad = stack.channel_for(layer_by_id(ts, broken_id), 'ROUGHNESS').effects[0]
    bad.white = .95
    info = ready(scene, ts)
    assert not any('Hidden broken' in warning for warning in info.get('warnings', [])), info

    report['result'] = 'PASS'
    destination = ROOT / '.blender-test' / 'shader-units.json'
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(report, indent=2))
    print('SHADER_UNITS_PASS', destination, flush=True)
    open_painter.unregister()


run()
