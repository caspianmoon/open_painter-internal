"""Real native 2D operators keep source canvases and explicit snapshots distinct."""
import hashlib
import json
import os
from pathlib import Path
import sys
import traceback

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import image_manager, layer_stack as stack, texture_set_manager as sets
from open_painter.evaluation import cache, composite_snapshot, shader_preview
from open_painter.evaluation.metrics import Metrics
from open_painter.painting import paint_session
from open_painter.ui import native

REPORT = Path(os.environ.get('OP_SNAPSHOT_UI_REPORT', str(ROOT / '.blender-test/shader-native/snapshot-ui.json')))
area = ts = base = canvas = snapshot = None
checks = []


def pixels_hash(image):
    return hashlib.sha256(image_manager.pixels(image).tobytes()).hexdigest()


def later(function, delay=.2):
    def run():
        try:
            if area:
                region = next(region for region in area.regions if region.type == 'WINDOW')
                with bpy.context.temp_override(window=bpy.context.window, area=area, region=region):
                    function()
            else:
                function()
        except Exception:
            error = traceback.format_exc()
            print(error, flush=True)
            REPORT.parent.mkdir(parents=True, exist_ok=True)
            REPORT.write_text(json.dumps({'result': 'FAIL', 'error': error}, indent=2))
            bpy.ops.wm.quit_blender()
    bpy.app.timers.register(run, first_interval=delay)


def no_bake(observed):
    for phase in ('source_generation', 'masking', 'filters', 'blending', 'image_write'):
        assert observed.phases[phase]['calls'] == 0, observed.report()
    assert observed.counts['composite_snapshot_evaluations'] == 0, observed.report()


def setup():
    global area, ts, base
    assert not bpy.app.background
    open_painter.register()
    bpy.context.preferences.view.show_splash = False
    area = max((item for item in bpy.context.screen.areas if item.type == 'VIEW_3D'),
               key=lambda item: item.width * item.height)
    with bpy.context.temp_override(area=area):
        bpy.ops.open_painter.open_editor()
        ts = sets.create(bpy.context.scene, 'Explicit 2D snapshot', 512)
        sets.assign(bpy.context.scene, ts, bpy.context.scene.objects['Cube'])
        base = stack.add(ts, 'FILL', 'Editable base')
        stack.channel_for(base, 'BASE_COLOR').color = (.1, .25, .4, 1)
        bpy.context.scene.open_painter.display_channel = 'BASE_COLOR'
        shader_preview.tick(bpy.context.scene, ts)
        assert shader_preview.current(bpy.context.scene, ts)
    later(open_snapshot)


def open_snapshot():
    global snapshot
    # The engine's channel images exist from the start; no CPU evaluation has written one.
    assert not any(out.revision for out in ts.outputs)
    with Metrics() as observed:
        assert bpy.ops.open_painter.native_view(view='IMAGE_EDITOR') == {'FINISHED'}
    snapshot = area.spaces.active.image
    assert snapshot and snapshot.get(composite_snapshot.MARKER)
    assert [out.channel for out in ts.outputs if out.revision] == ['BASE_COLOR']
    assert observed.counts['composite_snapshot_evaluations'] == 1
    assert observed.phases['image_write']['calls'] == 1
    assert not composite_snapshot.stale(ts, snapshot)
    np.testing.assert_allclose(image_manager.pixels(snapshot)[0, 0, :3], (.1, .25, .4), atol=1e-6)
    checks.append('opening-2d-evaluates-one-selected-channel')
    later(edit_native)


def edit_native():
    previous = pixels_hash(snapshot)
    revision = next(out.revision for out in ts.outputs if out.channel == 'BASE_COLOR')
    with Metrics() as observed:
        stack.channel_for(base, 'BASE_COLOR').color = (.7, .15, .08, 1)
        for _ in range(3):
            cache.tick()
        no_bake(observed)
    assert shader_preview.current(bpy.context.scene, ts)
    assert composite_snapshot.stale(ts, snapshot)
    assert next(out.revision for out in ts.outputs if out.channel == 'BASE_COLOR') == revision
    from open_painter.evaluation.engine import runtime
    if runtime.device().pixel_publish and snapshot == next(out.image for out in ts.outputs
                                                           if out.channel == 'BASE_COLOR'):
        # Through pixels (Vulkan) the one-channel snapshot is the output image, and publishing
        # the edit writes its pixels: it shows the edit (at half precision), with no CPU
        # evaluation (the revision above) and still marked stale for an explicit refresh.
        np.testing.assert_allclose(image_manager.pixels(snapshot)[0, 0, :3], (.7, .15, .08), atol=5e-4)
    else:
        assert pixels_hash(snapshot) == previous
    assert area.spaces.active.image == snapshot
    checks.append('shader-edit-leaves-visible-snapshot-explicitly-stale')
    later(refresh_snapshot)


def refresh_snapshot():
    with Metrics() as observed:
        assert bpy.ops.open_painter.native_snapshot() == {'FINISHED'}
    assert area.spaces.active.image == snapshot and not composite_snapshot.stale(ts, snapshot)
    assert observed.phases['image_write']['calls'] == 1
    np.testing.assert_allclose(image_manager.pixels(snapshot)[0, 0, :3], (.7, .15, .08), atol=1e-6)
    checks.append('refresh-publishes-current-selected-channel')
    later(begin_source)


def begin_source():
    global canvas
    layer = stack.add(ts, 'PAINT', 'Authored paint source')
    stack.channel_for(layer, 'BASE_COLOR').enabled = True
    canvas = paint_session.begin(bpy.context, ts, 'BASE_COLOR')
    shader_preview.tick(bpy.context.scene, ts)
    assert canvas != snapshot and area.spaces.active.image == canvas
    assert area.spaces.active.ui_mode == 'PAINT'
    with Metrics() as observed:
        assert bpy.ops.open_painter.native_view(view='IMAGE_EDITOR') == {'FINISHED'}
        no_bake(observed)
    assert area.spaces.active.image == canvas and not native.OP_OT_native_snapshot.poll(bpy.context)
    data = np.ones((512, 512, 4), np.float32)
    data[..., :3], data[..., 3] = (.3, .6, .1), .5
    image_manager.write(canvas, data, pack=False)
    checks.append('2d-paint-retains-authored-canvas-without-compositing')
    later(finish_source)


def finish_source():
    previous = pixels_hash(canvas)
    with Metrics() as observed:
        paint_session.finish(bpy.context)
        no_bake(observed)
    assert not bpy.context.scene.open_painter.paint_image
    assert area.spaces.active.image == canvas and area.spaces.active.ui_mode == 'VIEW'
    assert pixels_hash(canvas) == previous and canvas != snapshot
    assert composite_snapshot.stale(ts, snapshot)
    checks.append('finish-painting-keeps-source-and-does-not-bake')
    with Metrics() as observed:
        assert bpy.ops.open_painter.native_snapshot() == {'FINISHED'}
    assert observed.counts['composite_snapshot_evaluations'] == 1
    assert area.spaces.active.image == snapshot and snapshot != canvas
    assert pixels_hash(canvas) == previous
    checks.append('explicit-composite-remains-distinct-from-painted-source')
    later(finish)


def finish():
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({'result': 'PASS', 'blender': bpy.app.version_string,
        'checks': checks, 'source': canvas.name, 'snapshot': snapshot.name,
        'allocated_output_channels': [out.channel for out in ts.outputs],
        'automatic_shader_edit_bakes': 0}, indent=2) + '\n')
    print('SHADER_SNAPSHOT_UI_PASS', str(REPORT), flush=True)
    open_painter.unregister()
    bpy.ops.wm.quit_blender()


later(setup, 1)
