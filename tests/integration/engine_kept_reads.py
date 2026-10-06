"""A drag renders only what changes (native engine).

Each tick of a drag changes the same units, and they read the same unchanged results:
the inside of a smart material above the edited layer, which its folder step composes
over again, or the layers below. Those results used to be released after each use and
rendered again on every tick, with everything they read. ``Engine._update_frontier``
keeps them.

A stack of smart materials is dragged at a mask, a leaf and a folder. From the third tick
on, every unit rendered must have changed content, and the result must equal the stack
rendered sequentially from scratch, bit for bit. After idle arming for the selected layer
(``Engine._prearm_reads``), the first tick of each drag must render only what changes too.
With the rule off, the mask drag must render unchanged units again, so the check has teeth.
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
from open_painter.native_bridge.graph import MASK
from open_painter.smart_materials import composer
from open_painter.smart_materials.catalog import CATALOG

REPORT = Path(os.environ.get('OP_KEPT_READS_REPORT', str(Path(os.environ.get('TEMP', '/tmp')) / 'op-kept-reads.json')))
MAPS = ('CURVATURE', 'AO', 'WORLD_NORMAL', 'POSITION', 'NORMAL', 'MEAN_CURVATURE', 'BEVEL_MASK', 'BEVEL_NORMAL')


def build(scene):
    for obj in tuple(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16)
    ts = sets.create(scene, 'Kept reads', 512)
    sets.assign(scene, ts, bpy.context.object)
    y, x = np.indices((128, 128), dtype=np.float32) / 127
    for index, kind in enumerate(MAPS):
        data = np.ones((128, 128, 4), np.float32)
        data[..., 0] = .5 + .5 * np.sin(6.2831 * (x * (index + 2) + y))
        data[..., 1] = .5 + .5 * np.cos(6.2831 * y * (index + 1))
        image = images.create(f'Kept {kind}', 128)
        images.write(image, data)
        entry = ts.baked_maps.add()
        entry.kind, entry.image = kind, image
    names = sorted(CATALOG)
    index = 0
    while len(ts.layers) < 32:
        composer.apply(ts, names[index % len(names)], seed=index + 1)
        index += 1
    for layer in ts.layers:
        if layer.type != 'FOLDER':
            for channel in CHANNELS:
                prop = stack.channel_for(layer, channel)
                if prop is not None and channel in {'BASE_COLOR', 'ROUGHNESS', 'METALLIC', 'HEIGHT'}:
                    prop.enabled = True
    stack.invalidate(ts)
    return ts


class Capture:
    """The final unit textures handed to publication."""

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


def from_scratch(scene, ts):
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


def drag(scene, ts, owner, attribute, ticks=6):
    """Edit on consecutive ticks; per tick, the units rendered whose content did not change."""
    engine = shader_preview.session_engine(scene, ts)
    wasted, rendered = [], []
    for index in range(ticks):
        setattr(owner(), attribute, .45 + .3 * math.sin(index * 1.3))
        before = {key: (state.stamp & MASK if isinstance(state.stamp, int) else None)
                  for key, state in engine.states.items()}
        if index == ticks - 1:
            with Capture(engine) as results:
                shader_preview.tick(scene, ts)
        else:
            shader_preview.tick(scene, ts)
        assert shader_preview.current(scene, ts), shader_preview.diagnostics(scene, ts)
        stamps = engine._native_graph.stamps
        wasted.append(sorted(str(key) for key in engine.rendered if before.get(key) == stamps.get(key)))
        rendered.append(len(engine.rendered))
    return wasted, rendered, results


def main():
    gpu.init()
    open_painter.register()
    runtime.PIXEL_BUDGET_MS = math.inf
    runtime.FUSE_PIXEL_PUBLISH = False
    fusion.PUBLISH = False   # Capture reads the final units handed to publication
    scene = bpy.context.scene
    report = dict(status='RUNNING', cases=[])
    try:
        ts = build(scene)
        shader_preview.flush(scene, ts, pixels=False)
        engine = shader_preview.session_engine(scene, ts)
        assert engine._native_graph is not None, engine.report
        leaves = [layer for layer in ts.layers if layer.type != 'FOLDER']
        folders = [layer for layer in ts.layers if layer.type == 'FOLDER']
        masked = next(layer.id for layer in leaves[len(leaves) // 3:] if len(layer.masks))
        middle = leaves[len(leaves) // 2].id
        top = folders[-1].id

        def layer(identifier):
            return lambda: next(item for item in ts.layers if item.id == identifier)

        cases = [('mask_black', lambda: layer(masked)().masks[0], 'black'),
                 ('middle_leaf', layer(middle), 'opacity'),
                 ('top_folder', layer(top), 'opacity')]
        for name, owner, attribute in cases:
            shader_preview.flush(scene, ts, pixels=False)
            wasted, rendered, results = drag(scene, ts, owner, attribute)
            truth = from_scratch(scene, ts)
            row = dict(case=name, rendered=rendered, wasted=[len(w) for w in wasted], first_wasted=wasted[2][:6])
            report['cases'].append(row)
            print('KEPT_READS_CASE', json.dumps(row), flush=True)
            # From the third tick on, everything rendered had changed.
            assert not any(wasted[2:]), row
            assert results, row
            for channel, values in results.items():
                np.testing.assert_array_equal(values.view(np.uint32), truth[channel].view(np.uint32),
                                              err_msg=f'{name}: {channel}')
        # Idle arming keeps what an edit of the selected layer will read: its first tick
        # renders (almost) only what changes.
        from open_painter.evaluation import dependencies
        prearm = suffix.PREARM
        suffix.PREARM = True
        try:
            for name, owner, attribute in cases:
                identifier = {'mask_black': masked, 'middle_leaf': middle, 'top_folder': top}[name]
                shader_preview.flush(scene, ts, pixels=False)
                setattr(owner(), attribute, .2)
                shader_preview.flush(scene, ts, pixels=False)   # another value: the kept reads are stale
                for unit in [key for key in engine._frontier]:
                    engine._frontier.discard(unit)
                    engine._settle(unit, engine.graph.n)
                ts.active_layer = next(i for i, item in enumerate(ts.layers) if item.id == identifier)
                rounds = 0
                while rounds < 64 and engine.prearm(identifier, dependencies.image_epochs()):
                    rounds += 1
                engine.prearm(identifier, dependencies.image_epochs())
                wasted, rendered, _ = drag(scene, ts, owner, attribute, ticks=1)
                row = dict(case=name, prearm_rounds=rounds, kept=len(engine._frontier), first_rendered=rendered[0],
                           first_wasted=len(wasted[0]), wasted_units=wasted[0][:6])
                report.setdefault('prearmed', []).append(row)
                print('KEPT_READS_PREARMED', json.dumps(row), flush=True)
                assert row['first_rendered'] and not row['first_wasted'], row
        finally:
            suffix.PREARM = prearm
        # Without the rule, the mask drag renders unchanged units again on every tick.
        runtime.KEEP_READS = False
        try:
            shader_preview.flush(scene, ts, pixels=False)
            wasted, rendered, _ = drag(scene, ts, cases[0][1], cases[0][2])
        finally:
            runtime.KEEP_READS = True
        report['without'] = dict(rendered=rendered, wasted=[len(w) for w in wasted])
        print('KEPT_READS_WITHOUT', json.dumps(report['without']), flush=True)
        assert all(wasted[2:]), report['without']
        report['status'] = 'PASS'
        print('ENGINE_KEPT_READS_PASS', flush=True)
    finally:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, indent=2) + '\n')
        open_painter.unregister()


main()
