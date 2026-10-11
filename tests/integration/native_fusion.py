"""Chain fusion equals sequential rendering bit for bit (native engine).

Smart-material folders composite their layers with straight alpha, which suffix collapse
cannot fold, so an edit inside one re-renders the siblings above it. Chain fusion renders
such a run as one pass (engine/fusion.py), rounding between members as their texture
stores would.

A stack is dragged at several places, with fusion and without. It holds smart materials,
a run of non-affine top-level layers and a passthrough folder. Every final unit handed to
publication must then be identical bit for bit:
- to the same drag rendered sequentially, and
- to the stack rendered sequentially from scratch,
at display precision (half-float intermediates) and at full float. With suffix collapse
on, fused chains under collapsed runs change nothing either.
"""
import json
import math
import os
from pathlib import Path
import sys

import bpy
import gpu
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.constants import CHANNELS
from open_painter.core import image_manager as images, layer_stack as stack
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import shader_preview
from open_painter.evaluation.engine import fusion, runtime, suffix
from open_painter.evaluation.metrics import Metrics
from open_painter.smart_materials import composer
from open_painter.utils.ids import new_id

REPORT = Path(os.environ.get('OP_FUSION_REPORT', str(Path(os.environ.get('TEMP', '/tmp')) / 'op-native-fusion.json')))
MAPS = ('CURVATURE', 'AO', 'WORLD_NORMAL', 'POSITION', 'NORMAL', 'MEAN_CURVATURE', 'BEVEL_MASK', 'BEVEL_NORMAL')


def baked_maps(ts, size=256):
    y, x = np.indices((size, size), dtype=np.float32) / (size - 1)
    for index, kind in enumerate(MAPS):
        data = np.ones((size, size, 4), np.float32)
        data[..., 0] = .5 + .5 * np.sin(6.2831 * (x * (index + 2) + y))
        data[..., 1] = .5 + .5 * np.cos(6.2831 * y * (index + 1))
        data[..., 2] = .7
        image = images.create(f'Fusion {kind}', size)
        images.write(image, data)
        entry = ts.baked_maps.add()
        entry.kind, entry.image = kind, image


def fill(ts, name, color, roughness, mode='NORMAL', opacity=.7, parent_id='', masked=None):
    layer = stack.add(ts, 'FILL', name, parent_id)
    layer.blend_mode = mode
    layer.opacity = opacity
    for channel in CHANNELS:
        prop = stack.channel_for(layer, channel)
        if prop is not None:
            prop.enabled = channel in {'BASE_COLOR', 'ROUGHNESS', 'METALLIC', 'HEIGHT', 'NORMAL', 'EMISSION'}
    stack.channel_for(layer, 'BASE_COLOR').color = (*color, 1)
    stack.channel_for(layer, 'ROUGHNESS').value = roughness
    stack.channel_for(layer, 'METALLIC').value = roughness * .5
    stack.channel_for(layer, 'HEIGHT').value = .3 + roughness * .4
    if masked:
        mask = layer.masks.add()
        mask.id, mask.source, mask.procedural = new_id(), 'PROCEDURAL', masked
        mask.scale = (3.0, 3.0)
    ts.active_layer = next(i for i, item in enumerate(ts.layers) if item.id == layer.id)
    return layer.id


def build(scene):
    for obj in tuple(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16)
    ts = sets.create(scene, 'Fusion', 512)
    sets.assign(scene, ts, bpy.context.object)
    baked_maps(ts)
    ids = {'base': fill(ts, 'Base', (.2, .3, .5), .6, opacity=1.0)}
    for seed, name in enumerate(('Worn Painted Metal', 'Heavy Rust')):
        composer.apply(ts, name, seed=seed + 3)
    folders = [layer for layer in ts.layers if layer.type == 'FOLDER']
    children = [layer for layer in ts.layers if layer.parent_id == folders[0].id and layer.type != 'FOLDER']
    ids['material_bottom'] = children[0].id
    ids['material_middle'] = children[len(children) // 2].id
    ids['material_top'] = children[-1].id
    ids['material_masked'] = next(layer.id for layer in children if layer.masks)
    ts.active_layer = len(ts.layers) - 1
    for index, mode in enumerate(('OVERLAY', 'SOFT_LIGHT', 'OVERLAY', 'LINEAR_LIGHT')):
        key = fill(ts, f'Barrier {index}', ((index * .23) % 1, .4, .6), .3 + index * .1, mode,
                   masked='FBM' if index % 2 else None)
        if index == 0:
            ids['barrier'] = key
    ts.active_layer = len(ts.layers) - 1
    folder = stack.add(ts, 'FOLDER', 'Passthrough')
    folder.group_mode = 'PASSTHROUGH'
    for index, mode in enumerate(('NORMAL', 'OVERLAY', 'NORMAL')):
        fill(ts, f'Child {index}', (.8, .6 - index * .2, .2), .4, mode, parent_id=folder.id,
             masked='VORONOI' if index else None)
    ts.active_layer = next(i for i, item in enumerate(ts.layers) if item.id == folder.id)
    # An affine run on top, which suffix collapse folds.
    for index, mode in enumerate(('NORMAL', 'MULTIPLY', 'SCREEN', 'NORMAL')):
        fill(ts, f'Top {index}', (.1 * index, .7, .4), .5, mode, masked='VORONOI' if index % 2 else None)
    stack.invalidate(ts)
    return ts, ids


class Capture:
    """The final unit textures handed to publication, before padding and storage."""

    def __init__(self, engine):
        self.engine, self.results = engine, {}

    def __enter__(self):
        original = type(self.engine)._publish

        def publish(image, channel, result, n):
            runtime.device().finish()
            self.results[channel] = np.array(result.read(), np.float32)
            return original(self.engine, image, channel, result, n)
        self.engine._publish = publish
        return self.results

    def __exit__(self, *exc):
        del self.engine._publish


def sequential(scene, ts):
    """The same recipe rendered layer by layer from scratch, without fusion or collapse."""
    engine = shader_preview.session_engine(scene, ts)
    enabled = suffix.ENABLED, fusion.ENABLED
    suffix.ENABLED = fusion.ENABLED = False
    try:
        engine._suffix.clear()
        engine.published.clear()
        for state in engine.states.values():
            state.stamp = None
        with Capture(engine) as results:
            shader_preview.flush(scene, ts, pixels=False)
        return results
    finally:
        suffix.ENABLED, fusion.ENABLED = enabled


def drag(scene, ts, owner, attribute, samples=5, final=.37):
    """Edit on consecutive ticks, ending at ``final``; returns counters and the last tick's results."""
    engine = shader_preview.session_engine(scene, ts)
    values = [.45 + .3 * math.sin(i * 1.3) for i in range(samples - 1)] + [final]
    with Metrics() as metrics:
        for index, value in enumerate(values):
            setattr(owner(), attribute, value)
            if index == len(values) - 1:
                with Capture(engine) as results:
                    shader_preview.tick(scene, ts)
            else:
                shader_preview.tick(scene, ts)
            assert shader_preview.current(scene, ts), shader_preview.diagnostics(scene, ts)
    return dict(metrics.counts), results


def toward_zero(values):
    """float32 to half toward zero, from numpy's nearest-even conversion: a result farther
    from zero than the value steps one half toward zero (infinity to the largest half)."""
    halves = values.astype(np.float16)
    finite = np.isfinite(values)
    away = finite & (np.abs(halves.astype(np.float32)) > np.abs(values))
    halves[away] = np.nextafter(halves[away], np.float16(0))
    return halves.astype(np.float32)


def emulations():
    """Each float-to-half emulation against an independent model, on this GPU, whichever
    rounding its own stores use: toward zero, and to nearest even (numpy's conversion)."""
    dev = runtime.device()
    with dev.state():
        values = fusion._pattern(dev, 'RGBA32F', 'v').view(np.float32)
        models = {'op_store_rtz': toward_zero(values), 'op_store_rne': values.astype(np.float16).astype(np.float32)}
        nan = np.isnan(values)
        for name, function in fusion.STORES.items():
            body = f'vec4({name}(v.x), {name}(v.y), {name}(v.z), {name}(v.w))'
            emulated = fusion._pattern(dev, 'RGBA32F', body, function).view(np.float32)
            np.testing.assert_array_equal(emulated[~nan].view(np.uint32), models[name][~nan].view(np.uint32),
                                          err_msg=name)
            assert np.isnan(emulated[nan]).all(), name
    return int(values.size)


def identical(a, b, label):
    assert a and set(a) == set(b), (label, sorted(a), sorted(b))
    for channel in a:
        np.testing.assert_array_equal(a[channel].view(np.uint32), b[channel].view(np.uint32),
                                      err_msg=f'{label}: {channel}')


def main():
    gpu.init()
    open_painter.register()
    runtime.PIXEL_BUDGET_MS = math.inf     # every tick publishes every changed channel
    runtime.FUSE_PIXEL_PUBLISH = False     # Capture reads the final units handed to publication
    fusion.PUBLISH = False                 # (a chain publishing its result is checked at the end)
    scene = bpy.context.scene
    report = dict(status='RUNNING', cases=[])
    try:
        ts, ids = build(scene)
        shader_preview.flush(scene, ts, pixels=False)
        engine = shader_preview.session_engine(scene, ts)
        assert engine.report['execution_backend'] == 'c17-host-' + gpu.platform.backend_type_get().lower(), engine.report
        rounding = fusion.store_rounding(runtime.device())
        assert rounding, 'no float-to-half emulation matches this device'
        report.update(layers=len(ts.layers), rounding=rounding, emulated_values=emulations())
        print('FUSION_ROUNDING', rounding, flush=True)

        def layer(key):
            return lambda: next(item for item in ts.layers if item.id == ids[key])

        def mask(key):
            return lambda: layer(key)().masks[0]

        cases = [('material_bottom', layer('material_bottom'), 'opacity', True),
                 ('material_middle', layer('material_middle'), 'opacity', True),
                 ('material_top', layer('material_top'), 'opacity', False),
                 ('material_mask', mask('material_masked'), 'strength', True),
                 ('material_roughness', lambda: stack.channel_for(layer('material_bottom')(), 'ROUGHNESS'),
                  'value', True),
                 ('barrier', layer('barrier'), 'opacity', True),
                 ('base', layer('base'), 'opacity', False)]
        suffix.ENABLED = False
        for precise in (False, True):
            engine.precise = precise
            for name, owner, attribute, fuses in cases:
                results, counts = {}, {}
                for fused in (False, True):
                    fusion.ENABLED = fused
                    shader_preview.flush(scene, ts, pixels=False)
                    counts[fused], results[fused] = drag(scene, ts, owner, attribute)
                fusion.ENABLED = True
                truth = sequential(scene, ts)
                passes = counts[True].get('engine_chain_passes', 0)
                row = dict(case=name, precise=precise, chain_passes=passes,
                           chain_units=counts[True].get('engine_chain_units', 0),
                           group_links=counts[True].get('engine_chain_group_links', 0),
                           unfused_passes=counts[False].get('engine_chain_passes', 0),
                           mismatches=counts[True].get('engine_chain_mismatches', 0),
                           channels=sorted(results[True]))
                report['cases'].append(row)
                print('FUSION_CASE', json.dumps(row), flush=True)
                assert not row['unfused_passes'], row
                # Blender compiles Metal shaders with fast math, so at full float a fused shader
                # can differ from its members in the last bits; its check then retires it and
                # the chain renders sequentially, as the identities below confirm. At display
                # precision, and on every other backend, every fused shader is exact.
                retires = precise and gpu.platform.backend_type_get() == 'METAL'
                assert retires or not row['mismatches'], row
                if fuses:
                    assert passes and row['chain_units'] > passes, row
                if name.startswith('material'):
                    # The material's folder step takes the chain inside the folder in a register.
                    assert row['group_links'], row
                identical(results[True], results[False], f'{name} fused vs sequential drag')
                identical({c: truth[c] for c in results[True]}, results[True], f'{name} fused vs from scratch')

        # With suffix collapse: chains fused below collapsed runs change no bit either.
        suffix.ENABLED = True
        engine.precise = False
        published, counts = {}, {}
        for fused in (False, True):
            fusion.ENABLED = fused
            engine._suffix.clear()
            shader_preview.flush(scene, ts, pixels=False)
            counts[fused], published[fused] = drag(scene, ts, layer('material_middle'), 'opacity', samples=6)
        fusion.ENABLED = True
        report['with_collapse'] = dict(applications=[counts[f].get('engine_suffix_applications', 0) for f in (False, True)],
                                       chain_passes=counts[True].get('engine_chain_passes', 0))
        print('FUSION_COLLAPSE', json.dumps(report['with_collapse']), flush=True)
        assert all(report['with_collapse']['applications']) and report['with_collapse']['chain_passes'], report
        identical(published[True], published[False], 'fused under collapse')

        # A chain ending at a channel's final renders in the publication pass (images written in
        # place, as in a window): every channel's image, those not published again included,
        # equals the same drag published from the final units, and a from-scratch render.
        def images_now():
            engine.sync_pixels(set(CHANNELS))
            return {out.channel: np.array(images.pixels(out.image), np.float32).copy()
                    for out in ts.outputs if out.image}

        # A final published so keeps no texture until an idle tick keeps it: where idle arming runs.
        pixel_publish, prearm = runtime.PIXEL_PUBLISH, suffix.PREARM
        runtime.PIXEL_PUBLISH, suffix.PREARM = False, True
        suffix.ENABLED = False
        try:
            outcomes = {}
            for name, owner, attribute in (('barrier', layer('barrier'), 'opacity'),
                                           ('material_middle', layer('material_middle'), 'opacity')):
                pictures, counts = {}, {}
                for fused in (False, True):
                    fusion.PUBLISH = fused
                    shader_preview.flush(scene, ts, pixels=False)
                    counts[fused], _ = drag(scene, ts, owner, attribute, samples=6)
                    pictures[fused] = images_now()
                sequential(scene, ts)
                truth = images_now()
                outcomes[name] = dict(publications=counts[True].get('engine_chain_publications', 0),
                                      unfused=counts[False].get('engine_chain_publications', 0),
                                      mismatches=counts[True].get('engine_chain_mismatches', 0))
                print('FUSION_PUBLISH', name, json.dumps(outcomes[name]), flush=True)
                assert not outcomes[name]['unfused'] and not outcomes[name]['mismatches'], outcomes
                identical(pictures[True], pictures[False], f'{name} published fused vs from the finals')
                identical(pictures[True], truth, f'{name} published fused vs from scratch')
            assert all(row['publications'] for row in outcomes.values()), outcomes
            report['publish'] = outcomes

            # With collapse: the run topped by each final takes the fused chain below it (the
            # material's chain and folder step, its input) in its publication pass. The images
            # equal the same drag rendered and applied one pass each (collapse rounds otherwise
            # than layers rendered one by one, so both sides collapse).
            suffix.ENABLED = True
            # The passthrough folder under the top run does not fuse: without it, the barriers'
            # fused chain is the run's input.
            stack.remove_tree(ts, next(layer.id for layer in ts.layers if layer.name == 'Passthrough'))
            stack.invalidate(ts)
            pictures, counts = {}, {}
            for fused in (False, True):
                fusion.ENABLED = fusion.PUBLISH = fused
                engine._suffix.clear()
                shader_preview.flush(scene, ts, pixels=False)
                counts[fused], _ = drag(scene, ts, layer('material_middle'), 'opacity', samples=6)
                pictures[fused] = images_now()
            fusion.ENABLED = True
            outcome = dict(run_publications=counts[True].get('engine_chain_run_publications', 0),
                           group_links=counts[True].get('engine_chain_group_links', 0),
                           mismatches=counts[True].get('engine_chain_mismatches', 0),
                           unfused=counts[False].get('engine_suffix_publications', 0))
            report['run_inputs'] = outcome
            print('FUSION_RUN_INPUT', json.dumps(outcome), flush=True)
            assert outcome['run_publications'] and not outcome['mismatches'] and not outcome['unfused'], outcome
            identical(pictures[True], pictures[False], 'run input fused vs one pass each')

            # A single changed unit as the run's input (the barrier under the run, dragged alone).
            top_barrier = next(item.id for item in ts.layers if item.name == 'Barrier 3')
            pictures, counts = {}, {}
            for fused in (False, True):
                fusion.ENABLED = fusion.PUBLISH = fused
                engine._suffix.clear()
                shader_preview.flush(scene, ts, pixels=False)
                counts[fused], _ = drag(scene, ts, lambda: next(item for item in ts.layers if item.id == top_barrier),
                                        'opacity', samples=6)
                pictures[fused] = images_now()
            fusion.ENABLED = True
            single = dict(run_publications=counts[True].get('engine_chain_run_publications', 0),
                          mismatches=counts[True].get('engine_chain_mismatches', 0))
            report['run_input_unit'] = single
            print('FUSION_RUN_INPUT_UNIT', json.dumps(single), flush=True)
            assert single['run_publications'] and not single['mismatches'], single
            identical(pictures[True], pictures[False], 'single run input fused vs one pass each')
        finally:
            runtime.PIXEL_PUBLISH, suffix.PREARM = pixel_publish, prearm
            fusion.ENABLED, fusion.PUBLISH = True, False
        report['status'] = 'PASS'
        print('NATIVE_FUSION_PASS', flush=True)
    finally:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, indent=2) + '\n')
        open_painter.unregister()


main()
