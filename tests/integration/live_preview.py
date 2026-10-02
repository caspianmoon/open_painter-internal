"""Legacy GPU numerical parity plus current shader authoring lifecycle.

The texture compositor is invoked explicitly as a reference backend. LIVE is
the native material graph; the scheduler/save checks below exercise that public
path without adding an obsolete authoring mode to production.
"""
import json
import os
import sys
import time
import traceback
from tempfile import TemporaryDirectory
from pathlib import Path

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tests' / 'performance'))
import open_painter
from open_painter.constants import CHANNELS, OWNER
from open_painter.core import image_manager as images, layer_stack as stack
from open_painter.evaluation import cache, live_preview, live_display, shader_preview
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.metrics import Metrics

REPORT = Path(os.environ.get('OP_LIVE_REPORT', '/private/tmp/op-live-preview-validation.json'))


def consume(job):
    while True:
        try:
            next(job)
        except StopIteration as finished:
            return finished.value


def gpu_pixels(scene, ts, channel):
    image, texture = live_display.target(scene, ts, channel, int(ts.resolution))
    return np.array(texture.read(), np.float32).reshape(int(ts.resolution), int(ts.resolution), 4)


def live(scene, ts):
    started = time.perf_counter()
    with Metrics() as measurements:
        consume(live_preview.steps(scene, ts))
    return dict(wall_s=time.perf_counter() - started, **measurements.report())


def main():
    open_painter.register()
    if bpy.app.timers.is_registered(cache.tick):
        bpy.app.timers.unregister(cache.tick)
    scene = bpy.context.scene
    from fixtures import create, edit
    benchmark = os.environ.get('OP_LIVE_BENCHMARK') == '1'
    n = 2048 if benchmark else 512
    ts = create(scene, 'worn', n, generated_detail=True)[0]
    assert ts.preview_backend == 'LIVE'
    revisions = {output.channel: output.revision for output in ts.outputs}
    cold = live(scene, ts)
    assert live_preview.current(scene, ts) and ts.dirty
    assert {output.channel: output.revision for output in ts.outputs} == revisions
    assert cold['phases']['image_write']['calls'] == 0
    assert cold['phases']['packing']['calls'] == 0
    handles = {channel: live_display.target(scene, ts, channel, n)[0].as_pointer() for channel in CHANNELS}
    edits = []
    for _ in range(3):
        edit(ts)
        report = live(scene, ts)
        assert report['phases']['image_write']['calls'] == 0, report
        assert report['phases']['source_generation']['calls'] == 0, report
        assert report['phases']['masking']['calls'] == 0, report
        assert report['phases']['packing']['calls'] == 0, report
        assert report['counters'].get('live_gpu_upload_bytes', 0) == 0, report
        assert report['counters'].get('live_gpu_shader_compilations', 0) == 0, report
        assert report['counters'].get('live_channel_publications') == 1, report
        edits.append(report)
    assert {channel: live_display.target(scene, ts, channel, n)[0].as_pointer() for channel in CHANNELS} == handles
    readback_started = time.perf_counter()
    displayed = {channel: gpu_pixels(scene, ts, channel) for channel in CHANNELS}
    verification_readback_s = time.perf_counter() - readback_started
    # An explicit full evaluation flushes the same recipe through the reference.
    started = time.perf_counter()
    LayerEvaluator(scene).evaluate_texture_set(ts.id)
    flush_s = time.perf_counter() - started
    errors = {}
    for channel in CHANNELS:
        expected = images.pixels(images.output(ts, channel).image)
        error = float(np.max(np.abs(displayed[channel] - expected)))
        errors[channel] = error
        np.testing.assert_allclose(displayed[channel], expected, atol=3e-5, rtol=2e-5,
                                   err_msg=channel)
    assert not ts.dirty and not live_preview.current(scene, ts)
    assert not any(image.get('open_painter_live_gpu_display') for image in bpy.data.images)
    del displayed

    # A layer opacity edit composes all affected channels but regenerates no leaves.
    folder = next(layer for layer in ts.layers if layer.type == 'FOLDER')
    live(scene, ts)
    folder.opacity = .73
    opacity = live(scene, ts)
    assert opacity['counters'].get('live_input_upload_bytes', 0) == 0
    assert opacity['phases']['source_generation']['calls'] == 0
    assert opacity['phases']['masking']['calls'] == 0
    assert opacity['counters'].get('live_checkpoint_hits', 0) > 0, opacity

    if not benchmark:
        # Changing a mask input must rebuild it, not reuse an unrelated old leaf.
        masked = next(layer for layer in ts.layers if any(m.enabled for m in layer.masks))
        mask = next(m for m in masked.masks if m.enabled)
        mask.strength *= .5
        changed_mask = live(scene, ts)
        assert (changed_mask['counters'].get('live_input_uploads', 0) > 0 or
                changed_mask['counters'].get('live_gpu_mask_stacks', 0) > 0)
        # New edits between yields cannot publish an obsolete recipe revision.
        folder.opacity = .6
        pending = live_preview.steps(scene, ts)
        next(pending)
        folder.opacity = .4
        consume(pending)
        assert not live_preview.current(scene, ts)
        live(scene, ts)
        cache.tick()
        shader_preview.flush(scene, ts)
        assert cache.job_state(scene, ts)['state'] == 'SHADER'
        # A native float paint source uses Blender's updated GPU image directly.
        layer = stack.add(ts, 'PAINT')
        prop = stack.channel_for(layer, 'ROUGHNESS')
        prop.enabled = True
        prop.image = images.create('Live authored paint', n)
        data = np.zeros((n, n, 4), np.float32)
        data[n//3:n//2, n//4:n//2] = (.36, .36, .36, .6)
        images.write(prop.image, data, pack=False)
        native = live(scene, ts)
        assert native['counters'].get('live_native_paint_inputs', 0) > 0
        project = scene.open_painter
        project.paint_set_id, project.paint_layer_id = ts.id, layer.id
        project.paint_target_channel = 'ROUGHNESS'
        project.paint_image = prop.image
        with Metrics() as paint_input:
            assert live_preview.native_paint_input(scene)
        assert paint_input.phases['image_read']['calls'] == 0
        with Metrics() as shader_paint_input:
            assert shader_preview.native_paint_input(scene)
        assert shader_paint_input.phases['image_read']['calls'] == 0
        project.paint_image = None
        project.paint_set_id = project.paint_layer_id = project.paint_target_channel = ''
        actual = gpu_pixels(scene, ts, 'ROUGHNESS')
        LayerEvaluator(scene).evaluate_texture_set(ts.id)
        np.testing.assert_allclose(actual, images.pixels(images.output(ts, 'ROUGHNESS').image), atol=3e-5)
        # GPU failure retains dirty CPU output work; explicit CPU evaluation recovers.
        folder.opacity = .2
        live_preview.failed(scene, ts, RuntimeError('injected live failure'))
        assert not live_preview.available(scene, ts) and ts.dirty
        LayerEvaluator(scene).evaluate_texture_set(ts.id)
        assert not ts.dirty
        live_preview.retry(scene, ts)
        # Native save preserves the latest recipe/source without generating full
        # channel rasters. Export remains an explicit authoritative texture bake.
        layer.parent_id = ''
        data[:] = (.77, .77, .77, 1)
        images.write(prop.image, data, pack=False)
        saved_output = images.pixels(images.output(ts, 'ROUGHNESS').image).copy()
        saved_revisions = {output.channel: output.revision for output in ts.outputs}
        with Metrics() as saved:
            cache.on_save(None)
        assert ts.dirty and shader_preview.current(scene, ts)
        assert saved.phases['image_write']['calls'] == 0, saved.report()
        assert {output.channel: output.revision for output in ts.outputs} == saved_revisions
        from open_painter.evaluation.engine import runtime
        if runtime.device().pixel_publish:
            # Through pixels (Vulkan) publishing the flushed preview is writing the output's pixels:
            # they show the edit (0.77 at half precision), as the output's GPU texture does on
            # framebuffer backends, and the save wrote no image (above).
            np.testing.assert_allclose(images.pixels(images.output(ts, 'ROUGHNESS').image)[..., :3], .77, atol=5e-4)
        else:
            np.testing.assert_array_equal(images.pixels(images.output(ts, 'ROUGHNESS').image), saved_output)
        np.testing.assert_allclose(images.pixels(prop.image)[..., 0], .77, atol=3e-5)
        assert prop.image.packed_file
        assert not any(image.get('open_painter_disposable_preview') for image in bpy.data.images)
        # Export after a further edit must flush before reading any channel.
        data[:] = (.23, .23, .23, 1)
        images.write(prop.image, data, pack=False)
        from open_painter.export.exporter import export
        with TemporaryDirectory(prefix='op-live-export-') as folder_path:
            ts.export.directory = folder_path
            paths = export(bpy.context, ts, ts.export)
            roughness_path = next(path for path in paths if 'Roughness' in path.name)
            loaded = bpy.data.images.load(str(roughness_path), check_existing=False)
            loaded.colorspace_settings.name = 'Non-Color'
            np.testing.assert_allclose(images.pixels(loaded)[..., 0], .23, atol=.005)
            bpy.data.images.remove(loaded)
        # Distinct scenes retaining UUIDs cannot collide. Shared material falls back.
        duplicate = scene.copy()
        duplicate_ts = duplicate.open_painter.texture_sets[0]
        assert duplicate_ts.id == ts.id
        assert not live_preview.available(duplicate, duplicate_ts)
        bpy.data.scenes.remove(duplicate)
        live_preview.retry(scene, ts)
        folder.opacity = .5
        live(scene, ts)
        live_preview.clear()
        assert not live_display._entries and not live_preview._leaves.entries
        assert all(node.image == images.output(ts, node['role']).image
                   for node in ts.material.node_tree.nodes if node.get(OWNER)
                   and node.get('role') in CHANNELS)
        shader_preview.tick(scene, ts, force_repair=True)
        shader_preview.flush(scene, ts)
        assert shader_preview.current(scene, ts)

    result = dict(result='PASS', blender=bpy.app.version_string,
                  build=bpy.app.build_hash.decode(), backend='Legacy GPU reference / Shader Native lifecycle', resolution=n,
                  fixture='Worn Painted Metal with generated scalar images; six initial layer records; synthetic 32² AO/curvature',
                  precision='Float32 leaves, composition, display texture and CPU authority',
                  cold=cold, warm_roughness_edits=edits, opacity_edit=opacity,
                  max_absolute_error=errors, authoritative_flush_s=flush_s,
                  verification_readback_s=verification_readback_s,
                  timing_scope='Main-thread prepare/submit/publish; no GPU fence in production or timed warm path. '
                  'Readback occurs separately for parity; these are not input-to-displayed-frame measurements.')
    REPORT.write_text(json.dumps(result, indent=2) + '\n')
    print('LIVE_PREVIEW_PASS', REPORT, flush=True)
    open_painter.unregister()


if bpy.app.background:
    raise RuntimeError('This regression requires a real Blender GPU window')
else:
    def deferred():
        try:
            main()
        except Exception:
            REPORT.write_text(json.dumps(dict(result='FAIL', error=traceback.format_exc()), indent=2))
            traceback.print_exc()
        finally:
            bpy.ops.wm.quit_blender()
    bpy.app.timers.register(deferred, first_interval=2)
