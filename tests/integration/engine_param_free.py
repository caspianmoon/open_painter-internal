"""A folder's parameters do not reach the channels its children leave empty (native engine).

A smart material's children often paint only some channels. In the others the folder step
blends a transparent group over its backdrop, and its result does not depend on its own
opacity or masks (``Engine._param_free``, checked once per program on the GPU). Dragging
the folder's opacity then renders and publishes nothing in those channels.

The suite drags a folder's opacity on a stack of smart materials. From the second tick on:
- none of the folder's empty-channel steps renders;
- every channel's published image equals a from-scratch render bit for bit, the channels
  that were not published again included.
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
from open_painter.smart_materials import composer
from open_painter.smart_materials.catalog import CATALOG

REPORT = Path(os.environ.get('OP_PARAM_FREE_REPORT', str(Path(os.environ.get('TEMP', '/tmp')) / 'op-param-free.json')))
MAPS = ('CURVATURE', 'AO', 'WORLD_NORMAL', 'POSITION', 'NORMAL', 'MEAN_CURVATURE', 'BEVEL_MASK', 'BEVEL_NORMAL')


def build(scene):
    for obj in tuple(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16)
    ts = sets.create(scene, 'Param free', 512)
    sets.assign(scene, ts, bpy.context.object)
    y, x = np.indices((128, 128), dtype=np.float32) / 127
    for index, kind in enumerate(MAPS):
        data = np.ones((128, 128, 4), np.float32)
        data[..., 0] = .5 + .5 * np.sin(6.2831 * (x * (index + 2) + y))
        data[..., 1] = .5 + .5 * np.cos(6.2831 * y * (index + 1))
        image = images.create(f'Param free {kind}', 128)
        images.write(image, data)
        entry = ts.baked_maps.add()
        entry.kind, entry.image = kind, image
    names = sorted(CATALOG)
    index = 0
    while len(ts.layers) < 24:
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


def published(engine, ts):
    """Every channel's published image, as its pixels."""
    engine.sync_pixels(set(CHANNELS))
    return {out.channel: np.array(images.pixels(out.image), np.float32).copy() for out in ts.outputs if out.image}


def from_scratch(scene, ts):
    engine = shader_preview.session_engine(scene, ts)
    enabled = suffix.ENABLED, fusion.ENABLED, runtime.PARAM_FREE
    suffix.ENABLED = fusion.ENABLED = runtime.PARAM_FREE = False
    try:
        engine._suffix.clear()
        engine.published.clear()
        for state in engine.states.values():
            state.stamp = None
        shader_preview.flush(scene, ts, pixels=False)
        return published(engine, ts)
    finally:
        suffix.ENABLED, fusion.ENABLED, runtime.PARAM_FREE = enabled


def main():
    gpu.init()
    open_painter.register()
    runtime.PIXEL_BUDGET_MS = math.inf
    scene = bpy.context.scene
    report = dict(status='RUNNING')
    try:
        ts = build(scene)
        shader_preview.flush(scene, ts, pixels=False)
        engine = shader_preview.session_engine(scene, ts)
        assert engine._native_graph is not None, engine.report
        folders = [layer for layer in ts.layers if layer.type == 'FOLDER']
        empty = {}
        for folder in folders:
            empty[folder.id] = [channel for channel in CHANNELS
                                if engine.positions.get(('CHANNEL', folder.id, channel), (None, None))[1] == runtime.EMPTY_GROUP]
        folder = next(f for f in reversed(folders) if empty[f.id] and len(empty[f.id]) < len(CHANNELS))
        steps = {('CHANNEL', folder.id, channel) for channel in empty[folder.id]}
        rendered = []
        for index in range(5):
            folder.opacity = .5 + .3 * math.sin(index * 1.7)
            shader_preview.tick(scene, ts)
            assert shader_preview.current(scene, ts), shader_preview.diagnostics(scene, ts)
            rendered.append(sorted(str(key) for key in engine.rendered if key in steps))
        free = sum(1 for value in runtime.device().param_free.values() if value)
        refused = sum(1 for value in runtime.device().param_free.values() if not value)
        drag = published(engine, ts)
        truth = from_scratch(scene, ts)
        report.update(folder=folder.name, empty=empty[folder.id], rendered=[len(r) for r in rendered],
                      programs_free=free, programs_refused=refused)
        print('PARAM_FREE', json.dumps(report), flush=True)
        assert free, report
        assert not any(rendered[1:]), (report, rendered)
        assert set(drag) == set(truth) and len(drag) >= 4, (sorted(drag), sorted(truth))
        for channel in drag:
            np.testing.assert_array_equal(drag[channel].view(np.uint32), truth[channel].view(np.uint32),
                                          err_msg=channel)
        report['status'] = 'PASS'
        print('ENGINE_PARAM_FREE_PASS', flush=True)
    finally:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, indent=2) + '\n')
        open_painter.unregister()


main()
