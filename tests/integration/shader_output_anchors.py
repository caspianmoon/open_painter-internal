"""Composite anchors in the shader compiler: CPU parity for layer outputs and
folder composites in every group context, scoped rebuilds and undo rehydration."""
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
from open_painter.evaluation import anchors, cache, layer_cache, shader_preview
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.shader_graph import Graph
from open_painter.utils.ids import new_id

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
    ts = sets.create(scene, 'Shader output anchors', 512)
    ts.preview_backend = 'CPU'
    shader_preview.clear()
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_plane_add(size=2)
    sets.assign(scene, ts, bpy.context.object)
    bpy.context.view_layer.update()
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 1
    scene.render.bake.margin = 0
    scene.render.bake.use_clear = True
    target = bpy.data.images.new('Output anchors readback', width=N, height=N, float_buffer=True)
    target.colorspace_settings.name = 'Non-Color'
    report = {'cases': []}

    # Stack: Base; Worn (checker mask, published output); Darken (multiply);
    # isolated folder Damage [Scratches (stripes mask, published)] (published composite);
    # passthrough folder Wear [Grime]; consumers above read the anchors.
    base = stack.add(ts, 'FILL', 'Base'); base_id = base.id
    stack.channel_for(base, 'BASE_COLOR').color = (.2, .5, .7, 1.)
    stack.channel_for(base, 'ROUGHNESS').value = .3
    worn = stack.add(ts, 'FILL', 'Worn'); worn_id = worn.id
    stack.channel_for(worn, 'BASE_COLOR').color = (.9, .3, .1, 1.)
    stack.channel_for(worn, 'ROUGHNESS').value = .8
    checker = worn.masks.add()
    checker.id, checker.source, checker.procedural, checker.noise_scale = new_id(), 'PROCEDURAL', 'CHECKER', 3.
    worn.publish_output = True
    darken = stack.add(ts, 'FILL', 'Darken'); darken_id = darken.id
    darken.blend_mode = 'MULTIPLY'
    stack.channel_for(darken, 'BASE_COLOR').color = (.5, .6, .7, 1.)
    stack.channel_for(darken, 'ROUGHNESS').value = .5
    folder = stack.add(ts, 'FOLDER', 'Damage'); folder_id = folder.id
    folder.group_mode = 'ISOLATED'
    folder.opacity = .6
    folder.publish_group = True
    scratches = stack.add(ts, 'FILL', 'Scratches'); scratches_id = scratches.id
    scratches.parent_id = folder_id
    stack.channel_for(scratches, 'BASE_COLOR').color = (.1, .9, .2, 1.)
    stack.channel_for(scratches, 'ROUGHNESS').value = 1.
    stripes = scratches.masks.add()
    stripes.id, stripes.source, stripes.procedural, stripes.noise_scale = new_id(), 'PROCEDURAL', 'STRIPES', 4.
    scratches.publish_output = True
    wear = stack.add(ts, 'FOLDER', 'Wear'); wear_id = wear.id
    wear.group_mode = 'PASSTHROUGH'
    wear.publish_group = True
    grime = stack.add(ts, 'FILL', 'Grime'); grime_id = grime.id
    grime.parent_id = wear_id
    grime.blend_mode = 'SCREEN'
    stack.channel_for(grime, 'BASE_COLOR').color = (.2, .2, .2, .5)
    stack.channel_for(grime, 'ROUGHNESS').value = .4
    grime.publish_output = True
    # Consumers: fill content from Worn's output (base colour luminance into roughness),
    # a mask from Damage's composite, a mask from Scratches' output inside the isolated
    # folder, and a mask from Grime's passthrough output.
    reader = stack.add(ts, 'FILL', 'Reader'); reader_id = reader.id
    prop = stack.channel_for(reader, 'ROUGHNESS')
    prop.anchor, prop.anchor_channel = anchors.output_id(worn_id), 'BASE_COLOR'
    stack.channel_for(reader, 'BASE_COLOR').anchor = anchors.output_id(worn_id)
    reader.opacity = .7
    masked = stack.add(ts, 'FILL', 'Masked by composite'); masked_id = masked.id
    stack.channel_for(masked, 'BASE_COLOR').color = (.05, .05, .6, 1.)
    stack.channel_for(masked, 'ROUGHNESS').value = .15
    composite_mask = masked.masks.add()
    composite_mask.id, composite_mask.source = new_id(), 'REFERENCE'
    composite_mask.reference_anchor, composite_mask.reference_channel = anchors.group_id(folder_id), 'ROUGHNESS'
    inner_mask = masked.masks.add()
    inner_mask.id, inner_mask.source, inner_mask.operation = new_id(), 'REFERENCE', 'ADD'
    inner_mask.reference_anchor, inner_mask.reference_channel = anchors.output_id(scratches_id), 'BASE_COLOR'
    passthrough_mask = masked.masks.add()
    passthrough_mask.id, passthrough_mask.source, passthrough_mask.operation = new_id(), 'REFERENCE', 'MULTIPLY'
    passthrough_mask.reference_anchor, passthrough_mask.reference_channel = anchors.output_id(grime_id), 'ROUGHNESS'
    wear_mask = masked.masks.add()
    wear_mask.id, wear_mask.source, wear_mask.operation = new_id(), 'REFERENCE', 'MULTIPLY'
    wear_mask.reference_anchor, wear_mask.reference_channel = anchors.group_id(wear_id), 'BASE_COLOR'
    from open_painter.evaluation.references import issues, warnings
    assert not issues(ts) and not warnings(ts), (issues(ts), warnings(ts))
    small = SampledSet(ts)

    def parity(label, channels=('BASE_COLOR', 'ROUGHNESS'), cached=False):
        graph = Graph(scene, small, ts.material)
        graph.build()
        while graph.pending_resources:
            graph.step_resources(.1)
        if cached:
            layer_cache.flush(scene)
            graph.update()   # adopt the published cache images
            assert all(v == 'CURRENT' for v in graph.cache_states().values()), graph.cache_states()
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
            assert expected.std() > 1e-3, (label, channel, 'flat expectation proves nothing')
        record = {'case': label, 'max_error': worst, 'units': len(graph.units), 'nodes': graph.node_count,
                  'warnings': graph._build_warnings()}
        report['cases'].append(record)
        print('SHADER_OUTPUT_ANCHORS_PARITY', json.dumps(record), flush=True)
        tree.nodes.remove(bake_node)
        tree.nodes.remove(emission)
        graph.close()
        material_manager.repair(ts)
        return record

    parity('composite_anchors')
    layer_by_id(ts, worn_id).visible = False
    parity('hidden_output_producer')
    layer_by_id(ts, worn_id).visible = True
    layer_by_id(ts, folder_id).visible = False
    parity('hidden_published_folder')
    layer_by_id(ts, folder_id).visible = True
    stack.channel_for(layer_by_id(ts, worn_id), 'ROUGHNESS').enabled = False
    parity('untouched_producer_channel')
    stack.channel_for(layer_by_id(ts, worn_id), 'ROUGHNESS').enabled = True
    layer_by_id(ts, folder_id).group_mode = 'PASSTHROUGH'
    parity('folder_switched_to_passthrough')
    layer_by_id(ts, folder_id).group_mode = 'ISOLATED'
    # Soft coverage inside the isolated folder: the published Out (premultiplied in the
    # chain) and a passthrough sub-folder's Group are read straight, like the CPU publishes.
    stripes = next(m for m in layer_by_id(ts, scratches_id).masks)
    stripes.procedural, stripes.noise_scale = 'RADIAL_GRADIENT', 1.5
    parity('soft_isolated_output')
    inner = stack.add(ts, 'FOLDER', 'Inner'); inner_id = inner.id
    inner.parent_id, inner.group_mode, inner.publish_group = folder_id, 'PASSTHROUGH', True
    speck = stack.add(ts, 'FILL', 'Speck'); speck_id = speck.id
    speck.parent_id = inner_id
    stack.channel_for(speck, 'BASE_COLOR').color = (.7, .1, .8, 1.)
    stack.channel_for(speck, 'ROUGHNESS').value = .05
    radial = speck.masks.add()
    radial.id, radial.source, radial.procedural, radial.noise_scale = new_id(), 'PROCEDURAL', 'RADIAL_GRADIENT', 2.5
    nested_mask = layer_by_id(ts, masked_id).masks.add()
    nested_mask.id, nested_mask.source, nested_mask.operation = new_id(), 'REFERENCE', 'MAX'
    nested_mask.reference_anchor, nested_mask.reference_channel = anchors.group_id(inner_id), 'ROUGHNESS'
    parity('soft_passthrough_group_in_isolated')
    # A folder whose channel is disabled still publishes what its children compose
    # (its own composite and a descendant's output), as composite.Composer does.
    for channel in ('BASE_COLOR', 'ROUGHNESS'):
        stack.channel_for(layer_by_id(ts, folder_id), channel).enabled = False
    parity('folder_channels_disabled_published')
    for channel in ('BASE_COLOR', 'ROUGHNESS'):
        stack.channel_for(layer_by_id(ts, folder_id), channel).enabled = True
    stack.channel_for(layer_by_id(ts, wear_id), 'BASE_COLOR').enabled = False
    parity('passthrough_channel_disabled_published')
    stack.channel_for(layer_by_id(ts, wear_id), 'BASE_COLOR').enabled = True
    # A cached folder samples its image, but anchors published inside it are read
    # live (without a backdrop), and the limitation is reported.
    layer_by_id(ts, folder_id).cache_mode = 'CACHED'
    record = parity('cached_published_folder', cached=True)
    assert any('cached folder' in w and 'without its backdrop' in w for w in record['warnings']), record['warnings']
    layer_by_id(ts, folder_id).cache_mode = 'LIVE'
    layer_cache.release_layer(layer_by_id(ts, folder_id))

    # Runtime: toggling a publish flag is a scoped rebuild; consumers follow producers.
    ts.preview_backend = 'LIVE'
    material_manager.repair(ts)
    info = ready(scene, ts)
    graph = shader_preview.ensure(scene, ts)
    trees = {key: unit.tree.as_pointer() for key, unit in graph.units.items()}
    builds = info['graph_builds']
    # A numeric edit below the producer reaches every consumer channel.
    stack.channel_for(layer_by_id(ts, base_id), 'BASE_COLOR').color = (.25, .5, .7, 1.)
    info = shader_preview.diagnostics(scene, ts)
    assert not info['rebuild_pending'] and info['graph_builds'] == builds, info
    assert {'BASE_COLOR', 'ROUGHNESS'} <= set(info['affected_channels']), info
    shader_preview.tick(scene, ts)   # the engine renders every channel the edit reaches
    assert shader_preview.current(scene, ts)
    assert {'BASE_COLOR', 'ROUGHNESS'} <= set(shader_preview.diagnostics(scene, ts)['engine']['channels'])
    # Publishing a second folder composite rebuilds only that folder's channel units.
    extra = stack.add(ts, 'FOLDER', 'Extra'); extra_id = extra.id
    layer_by_id(ts, extra_id).opacity = .99
    info = ready(scene, ts)
    graph = shader_preview.ensure(scene, ts)
    trees = {key: unit.tree.as_pointer() for key, unit in graph.units.items()}
    builds = info['graph_builds']
    layer_by_id(ts, extra_id).publish_group = True
    assert not shader_preview.current(scene, ts)
    scope = shader_preview.diagnostics(scene, ts)['rebuild_scope']
    assert scope and all(key[0] == 'CHANNEL' and key[1] == extra_id for key in scope), scope
    info = ready(scene, ts)
    rebuilt = shader_preview.ensure(scene, ts)
    assert info['rebuilt_units'] == len(scope) and info['reused_units'] == len(trees) - len(scope), info
    assert all(rebuilt.units[key].tree.as_pointer() == pointer for key, pointer in trees.items()
               if key not in scope), 'unrelated units were rebuilt'
    assert all('Group' in rebuilt.units[key].exports for key in scope), 'the published folder exports no Group'
    graph = rebuilt
    builds = info['graph_builds']

    # Undo/redo keep a graph that imports composite exports: the undo restores a numeric value,
    # so the graph follows the restored structs (undo_follow), or is rebuilt with
    # OP_FOLLOW_NUMERIC_UNDO=0.
    bpy.ops.ed.undo_push(message='before edit')
    stack.channel_for(layer_by_id(ts, masked_id), 'ROUGHNESS').value = .2
    ready(scene, ts)
    bpy.ops.ed.undo_push(message='edit')
    bpy.ops.ed.undo()
    scene = bpy.context.scene
    ts = next(item for item in scene.open_painter.texture_sets if item.name == 'Shader output anchors')
    info = ready(scene, ts)
    assert info['graph_builds'] + info['undo_follows'] == 1, info
    rebuilt = shader_preview.ensure(scene, ts)
    assert any(producer[0] == 'CHANNEL' and export in {'Out', 'Group'}
               for unit in rebuilt.units.values() for producer, export, _ in unit.imports), \
        'the rebuilt graph lost the composite imports'
    bpy.ops.ed.redo()
    scene = bpy.context.scene
    ts = next(item for item in scene.open_painter.texture_sets if item.name == 'Shader output anchors')
    ready(scene, ts)
    assert abs(stack.channel_for(layer_by_id(ts, masked_id), 'ROUGHNESS').value - .2) < 1e-6

    # A cycle through a composite anchor is refused with the reference-cycle message.
    stack.channel_for(layer_by_id(ts, base_id), 'ROUGHNESS').anchor = anchors.output_id(worn_id)
    shader_preview.tick(scene, ts)
    info = shader_preview.diagnostics(scene, ts)
    assert info['error'] and 'cycle' in info['error'].lower(), info
    stack.channel_for(layer_by_id(ts, base_id), 'ROUGHNESS').anchor = ''
    ready(scene, ts)

    destination = ROOT / '.blender-test' / 'shader-output-anchors.json'
    destination.write_text(json.dumps(report, indent=1))
    print('SHADER_OUTPUT_ANCHORS_PASS', flush=True)
    open_painter.unregister()


run()
