"""The Open Painter engine against the CPU reference (the export path), end to end.

Headless (``gpu.init``): each fixture set is evaluated by the engine through the real
session path (material repair, scheduler ticks, publication into the set's channel
images), then every channel image is compared with ``LayerEvaluator`` on the texels
the set's UVs cover (the engine pads the rest across seams, the reference does not).
The material must hold one Open Painter Texture Set node and no compiled stack.
A numeric edit and a structural edit must reach the images with the same parity.
"""
import json
import os
import sys
from pathlib import Path
from time import perf_counter

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tests/integration'))
import open_painter  # noqa: E402

REPORT = Path(os.environ.get('OP_ENGINE_PARITY_REPORT', '/private/tmp/op-engine-parity.json'))
TOLERANCE = 2e-3          # full-float intermediates; outputs are half floats except Height
DISPLAY_TOLERANCE = 6e-3  # display precision: half-float intermediates except Height


def ready(scene, ts, limit=120):
    from open_painter.evaluation import shader_preview
    deadline = perf_counter() + limit
    while perf_counter() < deadline:
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert not info['error'], info
        if shader_preview.current(scene, ts):
            return info
    raise AssertionError(shader_preview.diagnostics(scene, ts))


def engine_channels(ts):
    import gpu
    from open_painter.constants import CHANNELS
    result = {}
    for channel in CHANNELS:
        image = next(o.image for o in ts.outputs if o.channel == channel)
        n = image.size[0]
        result[channel] = np.array(gpu.texture.from_image(image).read(), np.float32).reshape(n, n, 4)[..., :3].copy()
    return result


def reference_channels(scene, ts):
    from open_painter.constants import CHANNELS
    from open_painter.core import image_manager as images
    from open_painter.evaluation.evaluator import LayerEvaluator
    evaluator = LayerEvaluator(scene)
    result = {}
    for channel in CHANNELS:
        output = evaluator.evaluate_channel(ts.id, channel)
        image = output.image if hasattr(output, 'image') else output
        result[channel] = np.clip(images.pixels(image)[..., :3], 0, 1).copy()
    return result


def compare(label, scene, ts, tolerance=TOLERANCE):
    from open_painter.export import padding
    engine = engine_channels(ts)
    reference = reference_channels(scene, ts)
    covered = padding.ownership(ts, int(ts.resolution)).covered
    errors = {}
    for channel, values in engine.items():
        diff = np.abs(values - reference[channel])[covered]
        errors[channel] = float(diff.max()) if diff.size else 0.0
    worst = max(errors.values())
    assert worst <= tolerance, f'{label}: engine differs from the CPU reference by {worst:.2e}: {errors}'
    # The reference evaluator wrote its own result into the images: have the engine republish.
    from open_painter.evaluation import shader_preview
    entry = shader_preview._sessions.get((scene.as_pointer(), ts.id))
    if entry and entry.get('engine'):
        entry['engine'].published.clear()
        entry['dirty'] = True
    return errors


def material_is_clean(ts):
    """The managed material is the Texture Set node, a Principled BSDF and a Material Output."""
    from open_painter.core import texture_set_node
    material = ts.material
    tree = material.node_tree
    kinds = sorted(node.bl_idname for node in tree.nodes)
    assert kinds == sorted([texture_set_node.NODE_ID, 'ShaderNodeBsdfPrincipled', 'ShaderNodeOutputMaterial']), kinds
    assert not any(group.get('op_shader_graph') for group in bpy.data.node_groups), 'compiled node groups remain'
    node = next(node for node in tree.nodes if node.bl_idname == texture_set_node.NODE_ID)
    assert node.texture_set_id == ts.id and node.node_tree is not None
    return len(tree.nodes), len(node.node_tree.nodes)


def main():
    report = {'fixtures': {}}
    open_painter.register()
    from open_painter.evaluation.engine import runtime
    runtime.PRECISE = True
    import ui_fixture
    from open_painter.core import material_manager
    from open_painter.evaluation import shader_preview
    from open_painter.smart_materials import composer
    from open_painter.utils.ids import new_id
    scene = bpy.context.scene

    # A tall stack: fills with procedural colour and generator masks, plus a smart material.
    large = ui_fixture.build_large(scene, layers=8, resolution='512')
    composer.apply(large, 'Worn Painted Metal', seed=2)
    large = next(t for t in scene.open_painter.texture_sets if t.name == 'Large')
    large.preview_backend = 'LIVE'
    started = perf_counter()
    material_manager.repair(large)
    info = ready(scene, large)
    entry = {'first_ready_s': round(perf_counter() - started, 2), 'units': info.get('units'),
             'engine': info.get('engine')}
    entry['material_nodes'], entry['node_tree_nodes'] = material_is_clean(large)
    entry['initial'] = compare('large initial', scene, large)
    ready(scene, large)

    # A numeric edit: one mask's strength.
    layer = next(item for item in large.layers if item.masks)
    layer_id = layer.id
    layer.masks[0].strength = .35
    ready(scene, large)
    entry['numeric'] = compare('large mask strength', scene, large)
    ready(scene, large)

    # A structural edit: a noise mask appended to the same layer.
    layer = next(item for item in large.layers if item.id == layer_id)
    mask = layer.masks.add()
    mask.id, mask.name, mask.source = new_id(), 'Noise', 'NOISE'
    mask.noise_scale = 12.0
    from open_painter.core.layer_stack import invalidate
    invalidate(large)
    ready(scene, large)
    entry['structural'] = compare('large added mask', scene, large)
    ready(scene, large)

    # A mask's kind changed: only the mask's unit is rebuilt. A layer with one channel compiles
    # its mask into that channel's program, which is adopted unchanged and must still take the
    # mask's new code (it rendered the old kind before 0.14).
    from open_painter.core import layer_stack as stack
    single_id = stack.add(large, 'FILL', 'One channel').id
    single = next(item for item in large.layers if item.id == single_id)
    for prop in single.channels:
        prop.enabled = prop.channel == 'BASE_COLOR'
    stack.channel_for(single, 'BASE_COLOR').color = (.9, .2, .1, 1)
    mask = single.masks.add()
    mask.id, mask.name, mask.source, mask.procedural = new_id(), 'Cells', 'PROCEDURAL', 'CELLULAR'
    invalidate(large)
    ready(scene, large)
    compare('large one-channel masked layer', scene, large)
    ready(scene, large)
    single = next(item for item in large.layers if item.id == single_id)
    single.masks[0].procedural = 'VORONOI'
    info = ready(scene, large)
    assert info['reused_units'] > 0 and info['rebuilt_units'] < info['units'], info
    entry['mask_kind'] = compare('large mask kind changed', scene, large)
    # Drips streaks and Runoff trails are fields of the baked position, evaluated in the unit's
    # GPU program (the material graph rasters them on the CPU).
    weathered_id = stack.add(large, 'FILL', 'Weathered').id
    weathered = next(item for item in large.layers if item.id == weathered_id)
    stack.channel_for(weathered, 'BASE_COLOR').color = (.25, .12, .05, 1)
    for generator, scale, seed in (('DRIPS', 3.5, 91), ('RUNOFF', 2.25, 417)):
        mask = weathered.masks.add()
        mask.id, mask.name, mask.source, mask.generator = new_id(), generator.title(), 'GENERATOR', generator
        mask.noise_scale, mask.seed, mask.amount, mask.operation = scale, seed, .6, 'MAX'
    invalidate(large)
    info = ready(scene, large)
    graph = shader_preview.ensure(scene, large)
    weathered = next(item for item in large.layers if item.id == weathered_id)
    masks = {mask.as_pointer() for mask in weathered.masks}
    assert not any(masks & set(b.owners) for b in graph.resource_bindings), \
        'a Drips or Runoff field was rastered on the CPU'
    entry['weathering'] = compare('large drips and runoff', scene, large)
    # Display precision (the default): half-float intermediates except Height.
    engine = shader_preview.session_engine(scene, large)
    engine.precise = False
    engine.published.clear()
    shader_preview._sessions[(scene.as_pointer(), large.id)]['dirty'] = True
    ready(scene, large)
    entry['display'] = compare('large display precision', scene, large, DISPLAY_TOLERANCE)
    report['fixtures']['large'] = entry

    # The UI fixture: nested folders, a paint layer with a painted mask, several mask sources,
    # a Material Aging folder, a second set. A fresh startup file: the Cube is taken.
    bpy.ops.wm.read_homefile(use_empty=False)
    scene = bpy.context.scene
    ids = ui_fixture.build(scene)
    for name in ('body', 'panel'):
        identifier = ids.get(name)
        ts = next((t for t in scene.open_painter.texture_sets if t.id == identifier), None)
        if ts is None:
            continue
        ts.preview_backend = 'LIVE'
        material_manager.repair(ts)
        ready(scene, ts)
        material_is_clean(ts)
        report['fixtures'][name] = {'initial': compare(f'{name} initial', scene, ts)}

    report['status'] = 'PASS'
    REPORT.write_text(json.dumps(report, indent=1))
    print('ENGINE_PARITY_PASS', json.dumps(report), flush=True)


main()
