"""Evaluation-local preparation preserves live edits, failure recovery and pixels."""
from collections import Counter
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

gpu.init()
open_painter.register()
graph = engine = None
created = []
try:
    scene = bpy.context.scene
    ts = sets.create(scene, 'Evaluation state regression', 512)
    ts.preview_backend = 'CPU'
    for index in range(5):
        layer = stack.add(ts, 'FILL', f'Fill {index}')
        prop = stack.channel_for(layer, 'ROUGHNESS')
        prop.enabled, prop.value = True, .15 + index * .1
        mask = layer.masks.add()
        mask.source, mask.procedural = 'PROCEDURAL', 'CELLULAR'
    source = bpy.data.images.new('State source', width=8, height=8, float_buffer=True)
    target = bpy.data.images.new('State target', width=512, height=512, float_buffer=True)
    created.extend((source, target))
    source.colorspace_settings.name = target.colorspace_settings.name = 'Non-Color'
    values = np.linspace(.05, .95, 8 * 8 * 4, dtype=np.float32)
    source.pixels.foreach_set(values)
    prop = stack.channel_for(ts.layers[0], 'ROUGHNESS')
    prop.image = source
    graph = EngineGraph(scene, ts)
    graph.build()
    engine = runtime.Engine(scene, ts)
    engine.adopt(graph)
    engine.pad = False

    def publish(cached, *, invalidate=True):
        if invalidate:
            engine.published.clear()
            for state in engine.states.values():
                state.stamp = None
        counts = Counter()
        signature = engine._signature

        def observe(key):
            counts[key] += 1
            return signature(key)

        with patch.object(runtime, 'CACHE_EVALUATION_STATES', cached), \
                patch.object(engine, '_signature', side_effect=observe):
            written = engine.evaluate(['ROUGHNESS'], images={'ROUGHNESS': target}, budget=False)
        assert engine._state_cache is None
        output = np.frombuffer(gpu.texture.from_image(target).read(), np.float32).copy()
        return output, counts, written

    # Warm shader interfaces before asserting one check per unit; sampler-limit
    # fallback is allowed to reconsider earlier signatures during compilation.
    publish(False)
    for precision in (False, True):
        engine.precise = precision
        publish(False)
        expected, previous_counts, _ = publish(False)
        actual, counts, _ = publish(True)
        np.testing.assert_array_equal(actual, expected)
        assert max(counts.values()) == 1, counts
        assert sum(counts.values()) < sum(previous_counts.values()), (counts, previous_counts)
        _, unchanged_counts, written = publish(True, invalidate=False)
        assert not written
        # Incremental preparation inspects only units whose inputs changed: an
        # unchanged evaluation may check none, and never one twice.
        assert max(unchanged_counts.values(), default=0) <= 1

    # Every following call must re-read both live metadata and uniform sockets.
    for alpha in ('PREMUL', 'STRAIGHT', 'CHANNEL_PACKED'):
        source.alpha_mode = alpha
        prop.value += .03125
        graph.update(prop, check_structure=False)
        expected, _, _ = publish(False)
        actual, _, _ = publish(True)
        np.testing.assert_array_equal(actual, expected, err_msg=alpha)
    values[:] = values[::-1]
    source.pixels.foreach_set(values)
    source['open_painter_pixel_revision'] = 1
    expected, _, _ = publish(False)
    actual, _, _ = publish(True)
    np.testing.assert_array_equal(actual, expected, err_msg='image revision')

    # A failed draw cannot leave a prepared-state cache alive for later edits.
    engine.published.clear()
    for state in engine.states.values():
        state.stamp = None
    with patch.object(runtime.device(), 'draw', side_effect=RuntimeError('state cache failure')):
        try:
            engine.evaluate(['ROUGHNESS'], images={'ROUGHNESS': target}, budget=False)
        except RuntimeError as error:
            assert str(error) == 'state cache failure'
        else:
            raise AssertionError('Expected injected draw failure')
    assert engine._state_cache is None
    prop.value += .0625
    graph.update(prop, check_structure=False)
    expected, _, _ = publish(False)
    actual, _, _ = publish(True)
    np.testing.assert_array_equal(actual, expected, err_msg='failure retry')

    # Inline fallback invalidates cache entries in the same scope. Direct state
    # queries outside evaluation still inspect live bindings every time.
    key = engine.finals['ROUGHNESS'][1]
    signature = engine._signature
    with patch.object(engine, '_signature', wraps=signature) as observed:
        with engine._evaluation_states():
            engine._state(key)
            engine._state(key)
            assert observed.call_count == 1
            old_inline = engine.inline
            engine.inline = frozenset(list(engine.inline))
            assert engine.inline is not old_inline
            engine._state(key)
            assert observed.call_count == 2
        engine._state(key)
        engine._state(key)
        assert observed.call_count == 4
    print('ENGINE_STATE_CACHE_PASS', runtime.device().backend, flush=True)
finally:
    if engine is not None:
        engine.close()
    if graph is not None:
        graph.close()
    for image in created:
        bpy.data.images.remove(image)
    open_painter.unregister()
