"""Bounded scratch churn and exact outputs across repeated engine rebuilds."""
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

import bpy
import gpu
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import layer_stack as stack, texture_set_manager as sets
from open_painter.evaluation.engine import runtime
from open_painter.evaluation.engine.compiler import EngineGraph
from open_painter.evaluation.engine.formats import texture_bytes

runtime.ensure_gpu()
open_painter.register()
dev = runtime.device()
graph = engine = reference_graph = reference = None
created = []
channels = ('BASE_COLOR', 'ROUGHNESS', 'NORMAL', 'HEIGHT')


def accounting():
    actual = sum(texture_bytes(n, n, fmt) * len(free) for (n, fmt), free in dev.pool.items())
    identities = [id(texture) for free in dev.pool.values() for texture in free]
    assert actual == dev._pooled_bytes == dev.memory()['pooled_bytes']
    assert actual <= runtime.POOL_BYTES
    assert all(0 < len(free) <= runtime.POOL_SPARES for free in dev.pool.values())
    assert set(identities) == dev._pooled_ids and len(identities) == len(dev._pooled_ids)
    assert not (dev._pooled_ids & dev._leases.keys())


def fingerprints(outputs):
    result = {}
    for channel, image in outputs.items():
        texture = gpu.texture.from_image(image)
        values = texture.read()
        result[channel] = {'gpu': hashlib.sha256(memoryview(values)).hexdigest()}
        if dev.pixel_publish:
            pixels = np.empty(image.size[0] * image.size[1] * 4, np.float32)
            image.pixels.foreach_get(pixels)
            result[channel]['cpu'] = hashlib.sha256(pixels).hexdigest()
    return result


def fill(ts, name, value):
    layer = stack.add(ts, 'FILL', name)
    for channel in channels:
        prop = stack.channel_for(layer, channel)
        prop.enabled = True
        if channel in {'ROUGHNESS', 'HEIGHT'}:
            prop.value = value
        else:
            prop.color = (value, .35, .8, 1.)
    return layer


try:
    scene = bpy.context.scene
    ts = sets.create(scene, 'GPU memory lifecycle', 512)
    ts.preview_backend = 'CPU'
    bottom = fill(ts, 'Base', .21)
    top = fill(ts, 'Masked coat', .73)
    mask = top.masks.add()
    mask.source, mask.procedural = 'PROCEDURAL', 'CELLULAR'
    base_leases = dict(dev._leases)
    dev.trim(0)

    # Model the real surface-filter/undo pattern: many distinct extents return
    # scratch before Engine.evaluate can reach its final trim. A small cap makes
    # the regression bounded while still exercising actual driver allocations.
    with patch.object(runtime, 'POOL_BYTES', 16 * 1024 * 1024):
        sentinel = dev.texture(32, 'RGBA32F')
        try:
            dev.fill(sentinel, 32, (.125, .25, .5, 1.))
            sentinel_bits = np.frombuffer(dev.readback(sentinel, 32), np.float32).copy()
            for step in range(48):
                n = 129 + step * 7
                textures = [dev.texture(n, 'RGBA32F') for _ in range(4)]
                for texture in textures:
                    dev.fill(texture, n, (.25, .5, .75, 1.))
                    dev.release(texture, n, 'RGBA32F')
                    accounting()
                    assert id(sentinel) in dev._leases
                del textures, texture
            np.testing.assert_array_equal(np.frombuffer(dev.readback(sentinel, 32), np.float32), sentinel_bits)
        finally:
            dev.release(sentinel, 32, 'RGBA32F')
            del sentinel
    dev.trim(0)
    assert not dev.pool and not dev._pooled_ids and dev._pooled_bytes == 0
    assert dev._leases == base_leases
    print('ENGINE_MEMORY_SOAK bounded mid-evaluation scratch and live lease: PASS', flush=True)

    engine = runtime.Engine(scene, ts)
    engine.pad = False
    extra_id = None
    rows = []
    for cycle, n in enumerate((512, 1024, 2048, 512, 2048, 1024)):
        for image in created:
            bpy.data.images.remove(image)
        created.clear()
        ts.resolution = str(n)
        if cycle % 2 == 0:
            extra_id = fill(ts, 'Temporary coat', .42 + cycle * .03).id
        else:
            stack.remove(ts, next(index for index, layer in enumerate(ts.layers) if layer.id == extra_id))
            extra_id = None
        # The masked spatial dependency survives structural and resolution churn.
        layer = next(layer for layer in ts.layers if layer.name == 'Masked coat')
        stack.channel_for(layer, 'ROUGHNESS').value = .61 + cycle * .025
        layer.masks[0].strength = .3 + cycle * .1
        candidate = EngineGraph(scene, ts)
        candidate.build(reuse=graph)
        previous, graph = graph, candidate
        engine.precise = cycle >= 3
        engine.adopt(graph)
        if previous is not None:
            previous.close()
        outputs = {}
        for channel in channels:
            image = bpy.data.images.new('Memory soak ' + channel, width=n, height=n,
                                        alpha=True, float_buffer=True)
            image.colorspace_settings.name = 'Non-Color'
            image.alpha_mode = 'CHANNEL_PACKED'
            image.use_half_precision = channel != 'HEIGHT'
            outputs[channel] = image
            created.append(image)
        assert engine.evaluate(channels, images=outputs, budget=False) == set(channels)
        expected = fingerprints(outputs)
        accounting()

        reference_graph = EngineGraph(scene, ts)
        reference_graph.build()
        reference = runtime.Engine(scene, ts)
        reference.pad, reference.precise = False, engine.precise
        reference.adopt(reference_graph)
        assert reference.evaluate(channels, images=outputs, budget=False) == set(channels)
        assert fingerprints(outputs) == expected, f'Fresh engine output mismatch at cycle {cycle}, {n}'
        reference.close()
        reference_graph.close()
        reference = reference_graph = None
        assert not engine.evaluate(channels, images=outputs, budget=False), 'Unchanged state republished'
        accounting()
        rows.append(dict(cycle=cycle, resolution=n, precise=engine.precise,
                         memory=dev.memory(), output_hashes=expected))
        print('ENGINE_MEMORY_SOAK_CYCLE', json.dumps(rows[-1]), flush=True)

    engine.close()
    graph.close()
    engine = graph = None
    dev.trim(0)
    assert dev._leases == base_leases, (base_leases, dev._leases)
    assert not dev.pool and not dev._pooled_ids and dev._pooled_bytes == 0
    print('ENGINE_MEMORY_SOAK_PASS', dev.backend, '6 cycles; full CPU/GPU fingerprints; final leases restored', flush=True)
finally:
    for item in (reference, engine):
        if item is not None:
            item.close()
    for item in (reference_graph, graph):
        if item is not None:
            item.close()
    dev.trim(0)
    for image in created:
        bpy.data.images.remove(image)
    open_painter.unregister()
