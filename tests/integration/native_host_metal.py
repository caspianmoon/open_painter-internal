"""Real C scheduling, executor switching and restore against host GPU output: Metal, or a backend
being qualified (OP_NATIVE_HOST_BACKENDS=VULKAN or OPENGL)."""
import json
import os
from pathlib import Path
import sys

import bpy
import gpu
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))
import open_painter
from native_fixtures import add_source
from open_painter.constants import CHANNELS
from open_painter.core import layer_stack as stack, texture_set_manager as sets, material_manager
from open_painter.evaluation import shader_preview
from open_painter.native_bridge import lifecycle

gpu.init()
BACKEND = gpu.platform.backend_type_get()
EXPECTED = 'c17-host-' + BACKEND.lower()
assert BACKEND in lifecycle.host_backends(), (BACKEND, lifecycle.host_backends())
open_painter.register()
assert lifecycle.execution_library(BACKEND) is not None, lifecycle.diagnostics()
scene = bpy.context.scene
obj = bpy.context.object
original_engine = scene.render.engine
ts = sets.create(scene, 'C host Metal regression', 512)
sets.assign(scene, ts, obj)
base = stack.add(ts, 'FILL', 'Base')
coat = stack.add(ts, 'FILL', 'Coat')
for layer, value in ((base, .2), (coat, .8)):
    for channel in CHANNELS:
        prop = stack.channel_for(layer, channel)
        prop.enabled = True
        prop.value = value
coat.opacity = .6
add_source('image', scene, ts, coat, 512)
add_source('paint', scene, ts, coat, 512)
material_manager.repair(ts)


def ready():
    for _ in range(300):
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert not info['error'], info
        if shader_preview.current(scene, ts):
            return shader_preview.session_engine(scene, ts)
    raise AssertionError(info)


def pixels():
    return {output.channel: np.array(gpu.texture.from_image(output.image).read(), np.float32).copy()
            for output in ts.outputs}


def mip_pixels():
    from open_painter.native_bridge.presentation import HostGPUProbe
    reader = HostGPUProbe(lifecycle._core, 512)
    return {(output.channel, level): reader.read_mip(gpu.texture.from_image(output.image), level, 512 >> level)
            for output in ts.outputs for level in range(1, 10)}


try:
    engine = ready()
    assert engine.report['execution_backend'] == EXPECTED, engine.report
    from open_painter.evaluation.engine import runtime
    # Framebuffer publication on Metal accepts channel batches as native revisions: atomic,
    # rolled back as a whole, restored after eviction. Vulkan and OpenGL publish each channel
    # in place (runtime.REVISION_BACKENDS), restored after eviction by InPlacePresentation;
    # pixel publication (a background Vulkan session) writes each channel's pixels within a
    # per-tick budget. Without revisions a fault can leave the channels published before it,
    # so the rollback checks run where revisions exist.
    FRAMEBUFFER = not runtime.device().pixel_publish
    REVISIONS = FRAMEBUFFER and runtime.device().backend in runtime.REVISION_BACKENDS
    assert (engine._native_revisions is not None) == REVISIONS
    first = pixels()
    assert np.ptp(first['BASE_COLOR']) > .1
    topology_builds = engine._native_graph.rebuilds
    for value in (.3, .9, .6):
        coat.opacity = value
        engine = ready()
        assert engine._native_graph.rebuilds == topology_builds
        assert engine.report['stages']['compiles'] == 0, engine.report
    restored = pixels()
    for channel in CHANNELS:
        np.testing.assert_array_equal(first[channel], restored[channel], err_msg=channel)

    # Atomic publication has a stable working set: no GPU allocation after
    # both reusable revision buffers and final channel results are warmed.
    from unittest.mock import patch
    with patch.object(gpu.types, 'GPUTexture', side_effect=AssertionError('warm texture allocation')):
        for value in (.43, .57, .6):
            coat.opacity = value
            engine = ready()
            assert not any(engine._in_use.values())

    # Shared cache eviction cannot destroy shaders still held by an active unit.
    from open_painter.evaluation.engine import runtime
    from types import SimpleNamespace
    from unittest.mock import patch
    dev = runtime.device()
    with patch.object(dev.shaders, 'budget', 128 * 1024), patch.object(runtime, 'INTERFACE_ENTRIES', 2):
        shaders = []
        for index in range(5):
            program = SimpleNamespace(source=f'void main() {{ op_out0 = vec4({index}.0 / 8.0); }}',
                                      library='', samplers=(), parameter_slots=0, objects=(),
                                      targets=(0,), procedurals=(), native=True)
            shaders.append(dev.shader(program))
        assert len(dev.shaders) == 1 and len(dev._interfaces) <= 2
        target = dev.texture(8)
        try:
            for index, shader in enumerate(shaders):
                dev.draw(shader, [target], 8)
                np.testing.assert_allclose(np.array(target.read()), index / 8, atol=1e-5)
                assert len(dev._interfaces) <= 2
        finally:
            dev.release(target, 8)

    # Reject before GPU allocation; preserve active work and all accepted outputs.
    before_pressure = pixels()
    revision = engine._native_revisions.revision if REVISIONS else None
    with patch.object(runtime, 'DEVICE_BYTES', 0), patch.object(gpu.types, 'GPUTexture', side_effect=AssertionError('allocated past budget')):
        try:
            with engine._evaluation_states():
                dev.texture(64)
        except MemoryError as error:
            assert 'budget exhausted' in str(error)
        else:
            raise AssertionError('Memory admission failure was not exercised')
    if REVISIONS:
        assert engine._native_revisions.revision == revision
    for channel, actual in pixels().items():
        np.testing.assert_array_equal(before_pressure[channel], actual, err_msg='budget ' + channel)
    # Idle result reclamation is allowed, while its accepted revision survives.
    idle = runtime.Engine(scene, ts)
    idle.graph = SimpleNamespace(n=128)
    idle.states['pressure-fixture'] = SimpleNamespace(textures=[dev.texture(128)], formats=(runtime.RGBA,))
    idle.retained['pressure-fixture'] = runtime.texture_bytes(128, 128, runtime.RGBA)
    try:
        # Select this idle victim explicitly; the live engine may now reclaim
        # its reusable publication buffers before it needs to evict results.
        with patch.object(runtime, 'DEVICE_BYTES', dev.memory()['leased_bytes']), \
                patch.object(dev, 'engines', [idle]):
            scratch = dev.texture(64)
            assert not idle.retained and idle.states['pressure-fixture'].textures is None
            dev.release(scratch, 64)
    finally:
        idle.close()
    coat.opacity = .55
    ready()
    coat.opacity = .6
    engine = ready()
    for channel, actual in pixels().items():
        np.testing.assert_array_equal(first[channel], actual, err_msg='budget retry ' + channel)

    # Native channel state owns no-op selection, subset acceptance and host target changes.
    assert engine.report['native_channel_selection'] == FRAMEBUFFER
    # Direct calls use their own source-epoch snapshot; warm that snapshot once.
    engine.evaluate(CHANNELS, budget=False)
    revision = engine._native_revisions.revision if REVISIONS else 0
    from open_painter.evaluation.engine import runtime
    passes = runtime.device().passes
    assert engine.evaluate(CHANNELS, budget=False) == set()
    assert not REVISIONS or engine._native_revisions.revision == revision
    from open_painter.evaluation.engine import runtime
    assert runtime.device().passes == passes
    coat.opacity = .42
    engine.graph.update(check_structure=False)
    assert engine.evaluate(['ROUGHNESS'], budget=False) == {'ROUGHNESS'}
    partial = pixels()
    for channel in CHANNELS:
        if channel != 'ROUGHNESS':
            np.testing.assert_array_equal(first[channel], partial[channel], err_msg='subset ' + channel)
    assert not REVISIONS or engine._native_revisions.revision == revision + 1
    remaining = engine.evaluate(CHANNELS, budget=False)
    assert 'ROUGHNESS' not in remaining and 'BASE_COLOR' in remaining
    assert not REVISIONS or engine._native_revisions.revision == revision + 2
    coat.opacity = .6
    engine = ready()
    original = pixels()['ROUGHNESS']
    replacement = bpy.data.images.new('Native alternate channel target', width=512, height=512, float_buffer=True)
    replacement.colorspace_settings.name = 'Non-Color'
    try:
        if REVISIONS:
            # A target not owned by the revision stream can have arbitrary existing
            # mip contents. Cold rollback must snapshot those exact levels rather
            # than regenerate them from the base or reduce their precision.
            from open_painter.native_bridge.presentation import HostGPUProbe
            probe = HostGPUProbe(lifecycle._core, 512)
            target = gpu.texture.from_image(replacement)
            with dev.state():
                for level in range(10):
                    fb = gpu.types.GPUFrameBuffer(color_slots=[{'texture': target, 'mip': level}])
                    with fb.bind():
                        fb.clear(color=(level / 20, .123456789, .7654321, 1.))
            before = [probe.read_mip(target, level, 512 >> level) for level in range(10)]
            original_draw, failed = dev.draw, []
            revision = engine._native_revisions.revision
            def fail_cold_mip(shader, targets, size, *args, **kwargs):
                original_draw(shader, targets, size, *args, **kwargs)
                if (not failed and isinstance(targets[0], dict) and targets[0]['texture'] is target
                        and targets[0]['mip'] == 4):
                    failed.append(True)
                    raise MemoryError('cold mip publication')
            with patch.object(dev, 'draw', side_effect=fail_cold_mip):
                try:
                    engine.evaluate(['ROUGHNESS'], images={'ROUGHNESS': replacement}, budget=False)
                except MemoryError as error:
                    assert 'cold mip publication' in str(error)
                else:
                    raise AssertionError('Cold target failure was not exercised')
            assert failed and engine._native_revisions.revision == revision
            for level, values in enumerate(before):
                np.testing.assert_array_equal(probe.read_mip(target, level, 512 >> level), values,
                                              err_msg=f'cold target mip {level}')
        assert engine.evaluate(['ROUGHNESS'], images={'ROUGHNESS': replacement}, budget=False) == {'ROUGHNESS'}
        np.testing.assert_array_equal(np.array(gpu.texture.from_image(replacement).read(), np.float32), original)
        assert engine.evaluate(['ROUGHNESS'], budget=False) == {'ROUGHNESS'}
    finally:
        bpy.data.images.remove(replacement)

    # The next two blocks inject faults inside the revision stream (its host bookkeeping
    # and its retirement), which publication without revisions does not have.
    if REVISIONS:
        # Host metadata allocations occur while GPU output is still staged. An
        # exception must leave native acceptance and visible images at the old state.
        before_metadata = pixels()
        revision = engine._native_revisions.revision
        coat.opacity = .48
        engine.graph.update(check_structure=False)
        class FailingDirty(set):
            calls = 0
            def add(self, value):
                self.calls += 1
                if self.calls == 2:
                    raise MemoryError('injected host mirror allocation failure')
                super().add(value)
        engine.dirty_cpu = FailingDirty(engine.dirty_cpu)
        try:
            engine.evaluate(CHANNELS, budget=False)
        except MemoryError as error:
            assert 'host mirror' in str(error)
        else:
            raise AssertionError('Host metadata failure was not exercised')
        assert engine._native_revisions.revision == revision
        for channel, actual in pixels().items():
            np.testing.assert_array_equal(before_metadata[channel], actual, err_msg='metadata rollback ' + channel)
        assert 'BASE_COLOR' in engine.evaluate(CHANNELS, budget=False)
        assert engine._native_revisions.revision == revision + 1
        coat.opacity = .6
        engine = ready()

        # Cleanup after acceptance can fail independently; do not roll back host
        # bookkeeping or make C accept the same material revision a second time.
        from unittest.mock import patch
        stream = engine._native_revisions
        revision = stream.revision
        coat.opacity = .44
        engine.graph.update(check_structure=False)
        collect = stream.collect
        retirement_failed = []
        def fail_retirement():
            if not retirement_failed and not stream.submitting and stream.revision > revision:
                retirement_failed.append(True)
                raise MemoryError('injected retirement failure')
            collect()
        with patch.object(stream, 'collect', side_effect=fail_retirement):
            try:
                engine.evaluate(CHANNELS, budget=False)
            except MemoryError as error:
                assert 'retirement' in str(error)
            else:
                raise AssertionError('Retirement failure was not exercised')
        assert stream.revision == revision + 1 and retirement_failed
        assert 'BASE_COLOR' in engine.dirty_cpu
        assert engine.evaluate(CHANNELS, budget=False) == set()
        assert stream.revision == revision + 1 and not stream._scratch
        assert len(stream.resources) == len(CHANNELS)
        coat.opacity = .6
        engine = ready()

    # Native batches retain shared inputs until the last consumer, then enforce
    # the budget even if the final pass itself is not a retained cache entry.
    from unittest.mock import patch
    from open_painter.evaluation.engine import runtime
    with patch.object(runtime, 'RETAIN_BYTES', 0):
        for value in (.45, .6):
            coat.opacity = value
            engine = ready()
            assert not any(engine._in_use.values()), engine._in_use
            assert sum(engine.retained.values()) == 0, engine.retained
    for channel, actual in pixels().items():
        np.testing.assert_array_equal(first[channel], actual, err_msg=channel)

    # Switch in the same session with resident textures from the other executor.
    errors = {}
    for enabled in ('0', '1'):
        os.environ['OP_NATIVE_ENGINE'] = enabled
        engine.published.clear()
        engine.evaluate(CHANNELS, budget=False)
        assert engine.report['execution_backend'] == ('legacy' if enabled == '0' else EXPECTED)
        actual = pixels()
        errors[enabled] = {c: float(np.max(abs(actual[c] - first[c]))) for c in CHANNELS}
        assert max(errors[enabled].values()) <= .002, errors

    # A unit draw can fail after overwriting its old allocation. Reverting the
    # parameter must render again, not reuse those pixels under the old stamp.
    before_draw_failure = pixels()
    coat.opacity = .37
    engine.graph.update(check_structure=False)
    original_draw = runtime.device().draw
    failed_unit = []
    def fail_unit(shader, targets, size, *args, **kwargs):
        original_draw(shader, targets, size, *args, **kwargs)
        if not failed_unit:
            for operation in (lambda: engine.evaluate(CHANNELS), engine.close, lambda: engine.adopt(engine.graph)):
                try:
                    operation()
                except RuntimeError:
                    pass
                else:
                    raise AssertionError('Channel submission allowed destructive reentry')
            for key, state in engine.states.items():
                if state.textures and any(target is texture for target in targets for texture in state.textures):
                    failed_unit.append(key)
                    raise MemoryError('unit draw completed before failure')
    with patch.object(runtime.device(), 'draw', side_effect=fail_unit):
        try:
            engine.evaluate(CHANNELS, budget=False)
        except MemoryError as error:
            assert 'unit draw completed' in str(error)
        else:
            raise AssertionError('unit failure was not injected')
    assert failed_unit and engine.states[failed_unit[0]].stamp is None
    assert not any(engine._in_use.values()), engine._in_use
    if REVISIONS:   # without revisions the channels published before the fault stay
        for channel, actual in pixels().items():
            np.testing.assert_array_equal(before_draw_failure[channel], actual, err_msg=channel)
    coat.opacity = .6
    engine.graph.update(check_structure=False)
    engine.evaluate(CHANNELS, budget=False)
    for channel, actual in pixels().items():
        np.testing.assert_array_equal(before_draw_failure[channel], actual, err_msg=channel)

    if REVISIONS:
        # Fail after the first complete channel has reached host image storage.
        # Every channel and the accepted native revision must remain unchanged.
        from unittest.mock import patch
        from open_painter.evaluation.engine import runtime
        before_failure = pixels()
        before_failure_mips = mip_pixels()
        revision = engine._native_revisions.revision
        coat.opacity = .25
        engine.graph.update(check_structure=False)
        original_draw = runtime.device().draw
        copies = [0]
        def fail_once(shader, targets, size, *args, **kwargs):
            if targets and isinstance(targets[0], dict) and targets[0].get('mip') == 0:
                copies[0] += 1
                if copies[0] == 2:
                    midway = pixels()
                    assert np.max(abs(midway['BASE_COLOR'] - before_failure['BASE_COLOR'])) > .01, 'The fault must interrupt a genuinely changed revision'
                    raise RuntimeError('injected publication failure')
            return original_draw(shader, targets, size, *args, **kwargs)
        with patch.object(runtime.device(), 'draw', side_effect=fail_once):
            try:
                engine.evaluate(CHANNELS, budget=False)
            except RuntimeError as error:
                assert 'injected publication failure' in str(error), error
            else:
                raise AssertionError('Publication fault was not exercised')
        assert engine._native_revisions.revision == revision
        for channel, values in pixels().items():
            np.testing.assert_array_equal(values, before_failure[channel], err_msg='rollback ' + channel)
        for key, values in mip_pixels().items():
            np.testing.assert_array_equal(values, before_failure_mips[key], err_msg='mip rollback ' + str(key))
        engine.evaluate(CHANNELS, budget=False)
        assert engine._native_revisions.revision == revision + 1
        assert np.max(abs(pixels()['BASE_COLOR'] - before_failure['BASE_COLOR'])) > .01
        coat.opacity = .6
        engine = ready()

    else:
        # Without revisions (through pixels, or in place on Vulkan and OpenGL): a fault while
        # publishing keeps the channels published before it; the next evaluation must finish
        # exactly what a fresh evaluation renders, with no stale stamp or hold left by the
        # failed one. The second channel's readback fails, or its publication draw in place.
        from unittest.mock import patch
        coat.opacity = .25
        engine.graph.update(check_structure=False)
        dev = runtime.device()
        if dev.pixel_publish:
            hooked, original, publish, calls = 'readback', dev.readback, None, [0]
        else:
            hooked, original, publish, calls = 'draw', dev.draw, dev.fixed('publish'), [0]
        def fail_second(*args, **kwargs):
            if publish is None or args[0] is publish:
                calls[0] += 1
                if calls[0] == 2:
                    raise RuntimeError('injected publication failure')
            return original(*args, **kwargs)
        with patch.object(dev, hooked, side_effect=fail_second):
            try:
                engine.evaluate(CHANNELS, budget=False)
            except RuntimeError as error:
                assert 'injected publication failure' in str(error), error
            else:
                raise AssertionError('Publication fault was not exercised')
        assert not any(engine._in_use.values()), engine._in_use
        engine.evaluate(CHANNELS, budget=False)
        recovered = pixels()
        assert np.max(abs(recovered['BASE_COLOR'] - before_draw_failure['BASE_COLOR'])) > .01
        engine.published.clear()
        for state in engine.states.values():
            state.stamp = None
        engine.evaluate(CHANNELS, budget=False)
        for channel, values in pixels().items():
            np.testing.assert_array_equal(values, recovered[channel], err_msg='pixel retry ' + channel)
        coat.opacity = .6
        engine = ready()

    # Blender can evict image-owned GPU storage while CPU pixels lag behind.
    # Reconstruct it from the accepted native GPU revision, including mips.
    before_eviction = pixels()
    from open_painter.native_bridge.presentation import HostGPUProbe
    mip_reader = HostGPUProbe(lifecycle._core, 512)
    before_mips = {(output.channel, level): mip_reader.read_mip(gpu.texture.from_image(output.image), level, 512 >> level)
                   for output in ts.outputs for level in range(1, 10)}
    revision = engine._native_revisions.revision if REVISIONS else None
    for output in ts.outputs:
        output.image.gl_free()
    engine = ready()
    for channel, values in pixels().items():
        np.testing.assert_array_equal(values, before_eviction[channel], err_msg='eviction ' + channel)
    assert not REVISIONS or engine._native_revisions.revision == revision
    for output in ts.outputs:
        for level in range(1, 10):
            np.testing.assert_array_equal(mip_reader.read_mip(gpu.texture.from_image(output.image), level, 512 >> level),
                                          before_mips[output.channel, level], err_msg=f'eviction mip {output.channel}:{level}')

    old = engine._native_graph
    lifecycle._restore()
    assert not old.handle
    engine.published.clear()
    engine.evaluate(CHANNELS, budget=False)
    assert engine._native_graph is not old and engine._native_graph.handle
    for channel, values in pixels().items():
        np.testing.assert_array_equal(first[channel], values, err_msg='restore ' + channel)
    assert scene.render.engine == original_engine and bpy.context.object == obj
    report = dict(status='PASS', backend=EXPECTED, errors=errors, revisions=REVISIONS,
                  managed_budget_failure_recovery=True, idle_cache_reclamation=True,
                  live_shader_after_eviction=True,
                  topology_rebuilds=topology_builds, channels=list(CHANNELS),
                  native_batch_zero_budget=True, unit_draw_failure_recovery=True,
                  native_channel_selection=True, channel_subset_and_target_changes=True, host_metadata_failure_recovery=True, accepted_revision_retirement_failure=True,
                  original_object=obj.name, render_engine=original_engine, publication_rollback=True, image_eviction_recovery=True,
                  compiled_binding=bool(getattr(lifecycle.active_library(), '_op_binding', None)))
    path = Path(os.environ.get('OP_NATIVE_HOST_REPORT', str(ROOT / '.blender-test/native/metal-integration/native-host-metal.json')))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + '\n')
    print('NATIVE_HOST_METAL_PASS', report)
finally:
    os.environ.pop('OP_NATIVE_ENGINE', None)
    open_painter.unregister()
    assert lifecycle.execution_library(BACKEND) is None
