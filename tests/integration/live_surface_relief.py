"""Real-window GPU parity for elevated image detail and triplanar damage masks."""

import json
import os
import sys
import traceback
from pathlib import Path
from unittest.mock import patch

import bpy
import gpu
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
import open_painter
from open_painter.constants import CHANNELS
from open_painter.core import image_manager as images, layer_stack as stack, texture_set_manager as sets
from open_painter.evaluation import cache, live_display, live_preview
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.export.surface_normal import combined
from open_painter.smart_materials import composer
from open_painter.smart_materials.catalog import layer, mask, surface_detail

REPORT = Path(os.environ.get("OP_RELIEF_GPU_REPORT", "/private/tmp/op-live-surface-relief.json"))


def consume(job):
    while True:
        try:
            next(job)
        except StopIteration as done:
            return done.value


def main():
    assert not bpy.app.background, "This regression needs a real Blender GPU window"
    open_painter.register()
    if bpy.app.timers.is_registered(cache.tick):
        bpy.app.timers.unregister(cache.tick)
    scene = bpy.context.scene
    n = 512
    ts = sets.create(scene, "Triplanar relief GPU", n)
    obj = bpy.context.active_object
    sets.assign(scene, ts, obj)
    y, x = np.indices((64, 64), dtype=np.float32)
    source_data = np.ones((64, 64, 4), np.float32)
    source_data[..., :3] = (.5 + .4 * np.sin(x * 2 * np.pi / 16) * np.cos(y * 2 * np.pi / 23))[..., None]
    detail = images.create("Relief grain fixture", 64)
    damage = images.create("Relief damage fixture", 64)
    images.write(detail, source_data)
    mask_data = source_data.copy()
    mask_data[..., :3] = (.5 + .45 * np.sin((x * .4 + y) * 2 * np.pi / 51))[..., None]
    images.write(damage, mask_data)
    spec = layer("Raised coating grain", (.16, .28, .45), .52, height=.001,
                 generated_source=surface_detail("polymer_grain", height=.001))
    spec.update(projection="TRIPLANAR", triplanar_hardness=5, height_range=(.56, .66))
    settings = mask("NOISE", generated_source={"id": "machined_scratches"}, black=.2, white=.8)
    settings.update(projection="TRIPLANAR", triplanar_hardness=5)
    spec["mask"] = settings
    with patch.object(composer.generated_sources, "load", side_effect=lambda name: detail if name == "polymer_grain" else damage):
        composer.apply_document(ts, dict(layers=[
            layer("Substrate", (.1, .1, .1), .8, height_value=.48), spec]))
    paint = next(item for item in ts.layers if item.name == "Raised coating grain")
    height_prop = stack.channel_for(paint, "HEIGHT")
    assert height_prop.effects[0].kind == "LEVELS"
    checks = []

    def verify(name):
        serial = ts.edit_serial
        consume(live_preview.steps(scene, ts))
        if not live_preview.current(scene, ts):
            # Discovering a geometry edit retires the captured author revision;
            # the scheduler submits the new one on its next cooperative tick.
            assert ts.edit_serial != serial, "Live output stalled without a newer edit"
            consume(live_preview.steps(scene, ts))
        assert live_preview.current(scene, ts), "Live material did not publish"
        actual = {}
        for channel in CHANNELS:
            _, texture = live_display.target(scene, ts, channel, n)
            actual[channel] = np.array(texture.read(), np.float32).reshape(n, n, 4)
        LayerEvaluator(scene).evaluate_texture_set(ts.id)
        errors = {}
        for channel in CHANNELS:
            expected = images.pixels(images.output(ts, channel).image)
            np.testing.assert_allclose(actual[channel], expected, atol=3e-5, rtol=2e-5, err_msg=name + ":" + channel)
            errors[channel] = float(np.max(abs(actual[channel] - expected)))
        height = actual["HEIGHT"][..., 0]
        assert np.std(height) > .018, "Raised image surface became flat"
        final_normal = combined(ts, actual["NORMAL"], actual["HEIGHT"])
        assert np.std(final_normal[..., :2]) > .001, "Height relief produced no tangent normal signal"
        checks.append(dict(name=name, errors=errors, height_min=float(height.min()),
                           height_max=float(height.max()), height_std=float(height.std()),
                           normal_xy_std=float(np.std(final_normal[..., :2]))))
        return actual

    cold = verify("generated-image-height-range-and-triplanar-mask")
    height_prop.effects[0].output_white = .76
    adjusted = verify("height-levels-edit")
    assert np.mean(abs(adjusted["HEIGHT"] - cold["HEIGHT"])) > .008
    np.testing.assert_allclose(adjusted["BASE_COLOR"], cold["BASE_COLOR"], atol=3e-5)
    mask_data[..., :3] = 1 - mask_data[..., :3]
    images.write(damage, mask_data)
    changed_mask = verify("image-mask-revision-invalidates-projection-cache")
    assert np.mean(abs(changed_mask["HEIGHT"] - adjusted["HEIGHT"])) > .01
    source_data[..., :3] = 1 - source_data[..., :3]
    images.write(detail, source_data)
    changed_detail = verify("detail-image-revision-invalidates-height-cache")
    assert np.mean(abs(changed_detail["HEIGHT"] - changed_mask["HEIGHT"])) > .005
    obj.rotation_euler = (.17, .23, .31)
    obj.location = (.13, -.09, .04)
    scene.view_layers[0].update()
    changed_geometry = verify("geometry-edit-invalidates-triplanar-inputs")
    assert np.mean(abs(changed_geometry["HEIGHT"] - changed_detail["HEIGHT"])) > .005
    REPORT.write_text(json.dumps(dict(result="PASS", backend=gpu.platform.backend_type_get(),
                                      resolution=n, checks=checks), indent=2) + "\n")
    open_painter.unregister()
    print("LIVE_SURFACE_RELIEF_PASS", str(REPORT), flush=True)


def deferred():
    try:
        main()
    except Exception:
        REPORT.write_text(json.dumps(dict(result="FAIL", error=traceback.format_exc()), indent=2))
        traceback.print_exc()
    finally:
        bpy.ops.wm.quit_blender()


bpy.app.timers.register(deferred, first_interval=2)
