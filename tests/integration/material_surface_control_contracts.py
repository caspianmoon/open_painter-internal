"""Rejected ineffective controls and evaluated supported relative/native controls."""
import json
from pathlib import Path
import sys

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
import open_painter
from open_painter.authoring import commands, document, physical_space, transactions
from open_painter.authoring.schema import AuthoringError
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache, channel_eval

open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.context.scene
scene.render.engine = "BLENDER_EEVEE"
bpy.ops.mesh.primitive_plane_add(size=2)
ts = sets.create(scene, "Surface control contracts", 512)
ts.preview_backend = "CPU"
sets.assign(scene, ts, bpy.context.object)


def proposal(command):
    return dict(version=1, summary="Surface control contract", commands=[command])


def create(name, family="WOOD", **values):
    command = dict(op="create_surface", id="new:surface", name=name, family=family, values=values)
    result = transactions.apply(ts, proposal(command), operation_id=name, expected_digest=document.digest(ts))
    return next(layer for layer in ts.layers if layer.id == result["aliases"]["new:surface:finish"])


def reject(command, message):
    before = document.canonical(document.snapshot(ts))
    try:
        commands.compile_proposal(ts, proposal(command), "refused-control")
    except AuthoringError as exc:
        assert message in str(exc), str(exc)
    else:
        raise AssertionError("Ineffective control was accepted")
    assert document.canonical(document.snapshot(ts)) == before


def color(layer):
    return next(prop for prop in layer.channels if prop.channel == "BASE_COLOR")


def pixels(prop):
    return channel_eval.source(prop, "FILL", 512, 0, 512, {}, ts, {})


first = create("Relative wood fine", relative_period=.025)
second = create("Relative wood broad", relative_period=.1, frame="WORLD",
                origin=[0, 0, 0], axis_u=[1, 0, 0], axis_v=[0, 1, 0])
assert np.mean(np.abs(pixels(color(first)) - pixels(color(second)))) > .0001
for layer in (first, second):
    for prop in layer.channels:
        if prop.source_metadata:
            assert json.loads(prop.source_metadata)["mapping_frame"] == "LEGACY_RELATIVE_WORLD"
            assert prop.material_mapping_version == 0
for values in (dict(frame="COMPONENT"), dict(frame="OBJECT"), dict(origin=[.1, 0, 0]),
               dict(axis_u=[0, 1, 0]), dict(axis_v=[0, 0, 1])):
    reject(dict(op="create_surface", id="new:invalid", name="Invalid frame", family="WOOD", values=values),
           "relative surfaces use the default world frame")
print("RELATIVE_CONTROLS_AND_TRUTHFUL_FRAME_PASS", flush=True)

physical_space.declare(ts, 1, "Two-metre diagnostic plane, explicitly constructed")
reject(dict(op="create_surface", id="new:invalid", name="Ignored relative period", family="WOOD",
            values=dict(relative_period=.03)), "use period_m")
native = create("Metric cloth", family="WOVEN_FABRIC", period_m=.1, frame="WORLD")
base = color(native)
before = pixels(base)
transactions.apply(ts, proposal(dict(op="patch_surface", surface_id=native.surface_id,
    mode="APPEARANCE_ONLY", values=dict(color_variation=.7))), operation_id="native-variation",
    expected_digest=document.digest(ts))
assert np.mean(np.abs(pixels(base) - before)) > .001
print("NATIVE_VARIATION_REMAINS_EVALUATED_PASS", flush=True)

image = bpy.data.images.new("Synthetic image content", width=16, height=16, float_buffer=True)
image.colorspace_settings.name = "Non-Color"
y, x = np.indices((16, 16))
rgba = np.ones((16, 16, 4), np.float32)
rgba[..., :3] = (.2 + .5 * x / 15)[..., None]
image.pixels.foreach_set(rgba.ravel())
image["director_image_id"] = "image:synthetic-control-test"
base.image, base.procedural = image, ""
source_before = pixels(base).copy()
reject(dict(op="patch_surface", surface_id=native.surface_id, mode="APPEARANCE_ONLY",
            values=dict(color=[.1, .2, .3, 1], color_variation=.2)),
       "requires a procedural or scalar detail source")
np.testing.assert_array_equal(pixels(base), source_before)
assert base.image == image and not base.effects
transactions.apply(ts, proposal(dict(op="patch_surface", surface_id=native.surface_id,
    mode="COLOR_ONLY", values=dict(color=[.03, .2, .05, 1]))), operation_id="image-palette",
    expected_digest=document.digest(ts))
assert np.mean(np.abs(pixels(base) - source_before)) > .01
assert base.image == image and base.effects[0].kind == "GRADIENT_MAP"
print("IMAGE_CONTENT_VARIATION_REFUSED_PALETTE_SUPPORTED_PASS", flush=True)
print("MATERIAL_SURFACE_CONTROL_CONTRACTS_PASS", flush=True)
