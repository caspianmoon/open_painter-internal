"""Affine suffix collapse equals sequential rendering (Metal, native engine).

A stack mixing smart-material folders, a passthrough folder, affine and
non-affine blend modes, procedural masks, a spatial filter and every channel is
dragged at several depths. After each drag the published channels, which the
collapse produced, are compared with a forced sequential re-render of the same
recipe. Full-float intermediates must agree to float rounding; display precision
within the half-float rounding the sequential path adds at each layer.
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

REPORT = Path(os.environ.get('OP_SUFFIX_REPORT', '/private/tmp/op-native-suffix.json'))
MAPS = ('CURVATURE', 'AO', 'WORLD_NORMAL', 'POSITION', 'NORMAL', 'MEAN_CURVATURE', 'BEVEL_MASK', 'BEVEL_NORMAL')


def baked_maps(ts, size=256):
    y, x = np.indices((size, size), dtype=np.float32) / (size - 1)
    for index, kind in enumerate(MAPS):
        data = np.ones((size, size, 4), np.float32)
        data[..., 0] = .5 + .5 * np.sin(6.2831 * (x * (index + 2) + y))
        data[..., 1] = .5 + .5 * np.cos(6.2831 * y * (index + 1))
        data[..., 2] = .7
        image = images.create(f'Suffix {kind}', size)
        images.write(image, data)
        entry = ts.baked_maps.add()
        entry.kind, entry.image = kind, image


def fill(ts, name, color, roughness, mode='NORMAL', opacity=.7, parent_id='', masked=None, procedural=None):
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
    stack.channel_for(layer, 'EMISSION').color = (color[2] * .2, color[1] * .1, 0, 1)
    if procedural:
        prop = stack.channel_for(layer, 'BASE_COLOR')
        prop.procedural, prop.procedural_scale = procedural, 6.0
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
    ts = sets.create(scene, 'Suffix', 512)
    sets.assign(scene, ts, bpy.context.object)
    baked_maps(ts)
    ids = {'base': fill(ts, 'Base', (.2, .3, .5), .6, opacity=1.0, procedural='FBM')}
    composer.apply(ts, 'Worn Painted Metal', seed=3)
    ids['material_leaf'] = next(layer.id for layer in ts.layers
                                if layer.parent_id and layer.type != 'FOLDER' and layer.masks)
    modes = ['MULTIPLY', 'SCREEN', 'ADD', 'OVERLAY', 'HARD_LIGHT', 'SUBTRACT', 'EXCLUSION',
             'SOFT_LIGHT', 'NORMAL', 'MULTIPLY', 'SCREEN']
    for index, mode in enumerate(modes):
        key = fill(ts, f'{mode.title()} {index}', ((index * .13) % 1, .5, (index * .29) % 1), .2 + index * .05,
                   mode, masked=('FBM', 'VORONOI', None)[index % 3])
        if index == 1:
            ids['below_overlay'] = key
    ts.active_layer = len(ts.layers) - 1
    folder = stack.add(ts, 'FOLDER', 'Passthrough')
    folder.group_mode = 'PASSTHROUGH'
    folder_id = folder.id
    for index, mode in enumerate(('NORMAL', 'MULTIPLY', 'NORMAL')):
        key = fill(ts, f'Child {index}', (.8, .6 - index * .2, .2), .4, mode, parent_id=folder_id,
                   masked='FBM' if index else None)
        if index == 0:
            ids['passthrough_child'] = key
    ts.active_layer = next(i for i, item in enumerate(ts.layers) if item.id == folder_id)
    composer.apply(ts, 'Heavy Rust', seed=5)
    ts.active_layer = len(ts.layers) - 1
    blurred = fill(ts, 'Blurred mask', (.9, .9, .85), .3, masked='FBM')
    layer = next(item for item in ts.layers if item.id == blurred)
    blur = layer.masks.add()
    blur.id, blur.source = new_id(), 'FILTER'
    blur.effect.kind = 'BLUR'
    for index in range(4):
        fill(ts, f'Top {index}', (.1 * index, .7, .4), .5, ('NORMAL', 'MULTIPLY', 'SCREEN', 'NORMAL')[index],
             masked='VORONOI' if index % 2 else None)
    stack.invalidate(ts)
    return ts, ids


class Capture:
    """The final unit textures handed to publication: before padding and before the
    output images' half-float storage rounds them."""

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
    """The same recipe rendered layer by layer from scratch, without collapse."""
    engine = shader_preview.session_engine(scene, ts)
    enabled = suffix.ENABLED
    suffix.ENABLED = False
    try:
        engine._suffix.clear()
        engine.published.clear()
        for state in engine.states.values():
            state.stamp = None
        with Capture(engine) as results:
            shader_preview.flush(scene, ts, pixels=False)
        return results
    finally:
        suffix.ENABLED = enabled


def drag(scene, ts, owner, attribute, samples=6, final=.37):
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


def difference(a, b):
    return {channel: float(np.max(np.abs(a[channel] - b[channel]))) for channel in a if channel in b}


def main():
    gpu.init()
    open_painter.register()
    # This suite measures the collapse per tick. Pixel publication (Vulkan) spends a time
    # budget per tick and leaves channels for later ticks; lift it so every tick publishes
    # all channels, as framebuffer publication does (where the budget does not apply).
    runtime.PIXEL_BUDGET_MS = math.inf
    # Capture reads the final units handed to publication; a fused final (Vulkan) never is.
    # Fusion over collapsed runs is checked on its own at the end. No fused chain renders in a
    # publication pass either (fusion.publish).
    runtime.FUSE_PIXEL_PUBLISH = False
    fusion.PUBLISH = False
    scene = bpy.context.scene
    report = dict(status='RUNNING', cases=[])
    try:
        ts, ids = build(scene)
        shader_preview.flush(scene, ts, pixels=False)
        engine = shader_preview.session_engine(scene, ts)
        assert engine.report['execution_backend'] == 'c17-host-' + gpu.platform.backend_type_get().lower(), engine.report
        affine = sum(1 for key, state in engine.states.items()
                     if key[0] == 'CHANNEL' and state.program is not None and state.program.affine is not None)
        barriers = sum(1 for key, state in engine.states.items()
                       if key[0] == 'CHANNEL' and state.program is not None and state.program.affine is None)
        assert affine > 20 and barriers > 5, (affine, barriers)
        report.update(layers=len(ts.layers), affine_units=affine, barrier_units=barriers)

        def layer(key):
            return lambda: next(item for item in ts.layers if item.id == ids[key])

        cases = [('base', layer('base'), 'opacity'),
                 ('material_leaf', layer('material_leaf'), 'opacity'),
                 ('below_overlay', layer('below_overlay'), 'opacity'),
                 ('passthrough_child', layer('passthrough_child'), 'opacity'),
                 ('base_roughness', lambda: stack.channel_for(layer('base')(), 'ROUGHNESS'), 'value')]
        for name, owner, attribute in cases:
            engine.precise = True
            shader_preview.flush(scene, ts, pixels=False)
            precise_counts, collapsed_precise = drag(scene, ts, owner, attribute)
            truth = sequential(scene, ts)
            engine.precise = False
            shader_preview.flush(scene, ts, pixels=False)
            display_counts, collapsed_display = drag(scene, ts, owner, attribute)
            sequential_display = sequential(scene, ts)
            assert collapsed_precise and set(collapsed_precise) == set(collapsed_display), name
            exact = difference(collapsed_precise, truth)
            display = difference(collapsed_display, truth)
            baseline = difference(sequential_display, truth)
            row = dict(case=name, precise_errors=exact, display_errors=display, sequential_display_errors=baseline,
                       applications=(precise_counts.get('engine_suffix_applications', 0),
                                     display_counts.get('engine_suffix_applications', 0)),
                       arms=(precise_counts.get('engine_suffix_arms', 0), display_counts.get('engine_suffix_arms', 0)))
            report['cases'].append(row)
            print('SUFFIX_CASE', json.dumps(row), flush=True)
            assert all(math.isfinite(e) for e in (*exact.values(), *display.values())), row
            # Full float: the collapse equals sequential rendering to float rounding.
            assert max(exact.values()) <= 2e-5, row
            # Display precision: the collapse skips the per-layer half-float stores, so
            # it is at least as close to the full-float result as sequential rendering
            # (one half-float step of slack for the final store).
            for channel, error in display.items():
                assert error <= baseline[channel] + 2.0 ** -11, (channel, row)
        applied = {row['case'] for row in report['cases'] if all(row['applications'])}
        # Deep drags must actually take the collapsed path for the comparison to count.
        assert {'base', 'material_leaf', 'below_overlay', 'base_roughness'} <= applied, applied

        # Idle arming: the selected layer's transforms are armed before the drag,
        # so its very first tick collapses, and matches sequential rendering.
        from open_painter.evaluation import dependencies
        suffix.PREARM = True
        engine.precise = True
        shader_preview.flush(scene, ts, pixels=False)
        base_index = next(i for i, item in enumerate(ts.layers) if item.id == ids['base'])
        ts.active_layer = base_index
        prearmed = 0
        while prearmed < 64 and engine.prearm(ids['base'], dependencies.image_epochs()):
            prearmed += 1
        assert prearmed and not engine.prearm(ids['base'], dependencies.image_epochs()), prearmed
        with Metrics() as metrics:
            layer('base')().opacity = .52
            with Capture(engine) as first:
                shader_preview.tick(scene, ts)
        counts = dict(metrics.counts)
        assert counts.get('engine_suffix_applications', 0) >= prearmed, (counts, prearmed)
        assert not counts.get('engine_suffix_arms', 0), counts   # nothing left to arm on the first tick
        truth = sequential(scene, ts)
        error = max(difference(first, truth).values())
        report['prearm'] = dict(runs=prearmed, first_tick_applications=counts.get('engine_suffix_applications', 0),
                                error=error)
        print('SUFFIX_PREARM', json.dumps(report['prearm']), flush=True)
        assert error <= 2e-5, report['prearm']

        # A structural edit below an armed layer rebuilds the graph but keeps the
        # members of the runs above it: their transforms are reused, not re-armed.
        target = ids['below_overlay']
        ts.active_layer = next(i for i, item in enumerate(ts.layers) if item.id == target)
        while engine.prearm(target, dependencies.image_epochs()):
            pass
        armed = set(engine._suffix.entries)
        layer('base')().blend_mode = 'MULTIPLY'
        shader_preview.flush(scene, ts, pixels=False)
        assert armed <= set(engine._suffix.entries), 'a rebuild discarded armed transforms'
        with Metrics() as metrics:
            layer('below_overlay')().opacity = .33
            with Capture(engine) as rebuilt:
                shader_preview.tick(scene, ts)
        counts = dict(metrics.counts)
        truth = sequential(scene, ts)
        error = max(difference(rebuilt, truth).values())
        report['rebuild_reuse'] = dict(applications=counts.get('engine_suffix_applications', 0),
                                       arms=counts.get('engine_suffix_arms', 0), error=error)
        print('SUFFIX_REBUILD_REUSE', json.dumps(report['rebuild_reuse']), flush=True)
        assert report['rebuild_reuse']['applications'] and not report['rebuild_reuse']['arms'], report['rebuild_reuse']
        assert error <= 2e-5, report['rebuild_reuse']
        if runtime.device().pixel_publish:
            # Vulkan publishes each final fused with its publication pass, over the collapsed
            # runs below it: the images equal those of the same drag published unfused.
            published, fused_count = {}, 0
            for fused in (True, False):
                runtime.FUSE_PIXEL_PUBLISH = fused
                counts, _ = drag(scene, ts, layer('material_leaf'), 'opacity')
                if fused:
                    fused_count = counts.get('engine_fused_publications', 0)
                published[fused] = {out.channel: images.pixels(out.image).copy() for out in ts.outputs}
            runtime.FUSE_PIXEL_PUBLISH = False
            assert fused_count, 'the fused drag published nothing fused'
            for channel, values in published[True].items():
                np.testing.assert_array_equal(values, published[False][channel], err_msg='fused ' + channel)
            report['fused_over_collapse'] = dict(fused_publications=fused_count, identical=True)
            print('SUFFIX_FUSED', json.dumps(report['fused_over_collapse']), flush=True)

        # Images written in place (a window): a collapsed run topped by a channel's final applies
        # in the publication pass (fusion.publish_suffix). Every channel's image, at display and
        # full precision, padded or not, equals the same drag applied and then published.
        def images_now():
            engine.sync_pixels(set(CHANNELS))
            return {out.channel: np.array(images.pixels(out.image), np.float32).copy()
                    for out in ts.outputs if out.image}

        # A final published so keeps no texture until an idle tick keeps it: where idle arming runs.
        pixel_publish, pad, prearm = runtime.PIXEL_PUBLISH, engine.pad, suffix.PREARM
        runtime.PIXEL_PUBLISH, suffix.PREARM = False, True
        try:
            outcomes = []
            for precise in (False, True):
                for padded in (True, False):
                    engine.precise, engine.pad = precise, padded
                    pictures, counts, passes = {}, {}, {}
                    for fused in (False, True):
                        fusion.PUBLISH = fused
                        shader_preview.flush(scene, ts, pixels=False)
                        start = runtime.device().passes
                        counts[fused], _ = drag(scene, ts, layer('base'), 'opacity')
                        passes[fused] = runtime.device().passes - start
                        pictures[fused] = images_now()
                    row = dict(precise=precise, padded=padded,
                               publications=counts[True].get('engine_suffix_publications', 0),
                               unfused=counts[False].get('engine_suffix_publications', 0),
                               mismatches=counts[True].get('engine_chain_mismatches', 0),
                               drag_passes=(passes[False], passes[True]))
                    outcomes.append(row)
                    print('SUFFIX_PUBLISH', json.dumps(row), flush=True)
                    assert row['publications'] and not row['unfused'] and not row['mismatches'], row
                    # One pass fewer for each channel a run published (a first check of each
                    # shader renders three).
                    assert passes[True] < passes[False], row
                    for channel, values in pictures[False].items():
                        np.testing.assert_array_equal(pictures[True][channel], values,
                                                      err_msg=f'{channel} {row}')
            report['suffix_publish'] = outcomes

            # A drag's last finals hold no texture. The next idle tick renders and keeps them, as an
            # unfused publication would have: publishing again from them renders nothing, and gives
            # the same images.
            fusion.PUBLISH = True
            engine.precise, engine.pad = False, True
            shader_preview.flush(scene, ts, pixels=False)
            drag(scene, ts, layer('base'), 'opacity')
            dragged = images_now()
            with Metrics() as metrics:
                while engine.prearm(ids['base'], dependencies.image_epochs()):
                    pass
            kept = metrics.counts.get('engine_fused_finals_kept', 0)
            engine.published.clear()
            engine.evaluate(epochs=dependencies.image_epochs(), budget=False)
            report['idle_keep'] = dict(kept=kept, rendered=len(engine.rendered))
            print('SUFFIX_IDLE_KEEP', json.dumps(report['idle_keep']), flush=True)
            assert kept and not engine.rendered, (kept, engine.rendered)
            for channel, values in dragged.items():
                np.testing.assert_array_equal(images_now()[channel], values, err_msg='kept ' + channel)
        finally:
            runtime.PIXEL_PUBLISH, engine.pad, suffix.PREARM = pixel_publish, pad, prearm
            fusion.PUBLISH = False
        report['status'] = 'PASS'
        print('NATIVE_SUFFIX_PASS', flush=True)
    finally:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, indent=2) + '\n')
        open_painter.unregister()


main()
