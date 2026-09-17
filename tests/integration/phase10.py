import sys, time
from pathlib import Path
import bpy
import numpy as np

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "src"))
import open_painter
from open_painter.core import (
    texture_set_manager as sets,
    layer_stack as stack,
    image_manager as images,
)
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.export.image_writer import write
from open_painter.painting.projection import geometry

open_painter.register()
ts = sets.create(bpy.context.scene, "Stability")
ts.resolution = "512"
sets.assign(bpy.context.scene, ts, bpy.context.object)
stack.add(ts)
texture = images.create("Projection Checker", 8)
data = np.ones((8, 8, 4), dtype=np.float32)
data[::2, :, 0] = 0.1
data[1::2, :, 1] = 0.1
images.write(texture, data)
prop = stack.channel_for(ts.layers[0], "BASE_COLOR")
prop.image = texture
prop.projection = "TRIPLANAR"
e = LayerEvaluator(bpy.context.scene)
e.evaluate_texture_set(ts.id)
base = next(o.image for o in ts.outputs if o.channel == "BASE_COLOR")
assert images.pixels(base)[..., :3].std() > 0.01
original_id = ts.id
source_serial = ts.edit_serial
other = sets.duplicate(bpy.context.scene, 0)
assert other.id != original_id and not other.assignments and len(other.layers) == 1
ts = bpy.context.scene.open_painter.texture_sets[0]
assert not ts.dirty and not any(o.dirty for o in ts.outputs), 'duplicating must leave the evaluated source clean'
assert ts.edit_serial == source_serial and not ts.layers[0].dirty, 'duplicating must not edit the source set'
# A layer edit must leave unrelated texture sets clean.
e.evaluate_texture_set(other.id)
ts = bpy.context.scene.open_painter.texture_sets[0]
ts.layers[0].opacity = 0.9
assert not other.dirty
# A channel edit on the copy lands on the copy: the copy's channels once shared
# the source layer's id, and the owner is resolved by address, not by that id.
e.evaluate_texture_set(ts.id)
stack.channel_for(other.layers[0], "BASE_COLOR").opacity = 0.8
assert other.dirty and not ts.dirty and not any(o.dirty for o in ts.outputs)
e.evaluate_texture_set(other.id)
# A snapshot restored outside a replay still carries the source layer's id while
# its callbacks fire; they must dirty the set that holds the channel.
source_serial = ts.edit_serial
extra = other.layers.add()
stack.restore(stack.snapshot(ts.layers[0]), extra)
other = next(t for t in bpy.context.scene.open_painter.texture_sets if t.id != original_id)
assert other.dirty, 'restoring into the copy must dirty the copy'
assert not ts.dirty and ts.edit_serial == source_serial and not any(o.dirty for o in ts.outputs), (
    'restoring a snapshot into another set must leave the source clean')
other.layers.remove(len(other.layers) - 1)
# Independent RGBA must survive PNG/TIFF, both depths, even at zero alpha.
values = np.ones((4, 4, 4), dtype=np.float32)
values[..., :3] = (0.2, 0.4, 0.6)
values[..., 3] = 0
values[0, 0] = (0.1, 0.3, 0.5, 0.7)
for fmt in ("PNG", "TIFF"):
    for bits in (8, 16):
        path = root / ".blender-test" / f"packed_{bits}.{fmt.lower()}"
        write(path, values, fmt, bits)
        image = bpy.data.images.load(str(path))
        image.colorspace_settings.name = "Non-Color"
        image.alpha_mode = "CHANNEL_PACKED"
        loaded = images.pixels(image)
        assert np.allclose(loaded[::-1], values, atol=2 / ((1 << bits) - 1)), (
            fmt,
            bits,
            loaded[0, 0],
        )
# A float file in a colour space (16-bit PNG/TIFF, EXR) with Straight alpha is
# read premultiplied and already scene-linear; every CPU consumer decodes it to
# straight colour like the shader's Image Texture node (0.9.7): a half-alpha red
# sticker over white composites to (1, .5, .5), not a dark fringe, and a FILL
# mask reads the sticker's red, not red * alpha.
from open_painter.evaluation.image_snapshots import associated, read
from open_painter.evaluation.mask_eval import evaluate_masks
from open_painter.utils.ids import new_id
sticker = np.zeros((8, 8, 4), np.float32)
sticker[..., 0], sticker[..., 3] = 1, .5
sticker[0, 0] = (.5, .25, 0, 1)
path = root / ".blender-test" / "sticker_16.png"
write(path, sticker, "PNG", 16)
float_image = bpy.data.images.load(str(path))
assert float_image.is_float and float_image.alpha_mode == "STRAIGHT" and float_image.colorspace_settings.name == "sRGB"
assert associated(float_image)
raw = images.pixels(float_image)
assert np.allclose(raw[1, 1], (.5, 0, 0, .5), atol=2e-3), raw[1, 1]   # Blender's buffer is premultiplied
decoded = read({}, float_image, linear=True)
assert np.allclose(decoded[1, 1], (1, 0, 0, .5), atol=2e-3), decoded[1, 1]
linear = lambda c: c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4
assert np.allclose(decoded[-1, 0], (linear(.5), linear(.25), 0, 1), atol=2e-3), decoded[-1, 0]   # linearised once
byte_image = bpy.data.images.load(str(root / ".blender-test" / "packed_8.png"))
assert not associated(byte_image)
float_image.colorspace_settings.name = "Non-Color"
assert not associated(float_image) and np.allclose(read({}, float_image, linear=True)[1, 1], (1, 0, 0, .5), atol=2e-3)
float_image.colorspace_settings.name = "sRGB"
managed = images.create("Managed float", 4)
assert managed.is_float and not associated(managed)
sticker_set = sets.create(bpy.context.scene, "Sticker", 512)   # UV fills need no mesh
white = stack.add(sticker_set)
stack.channel_for(white, "BASE_COLOR").color = (1, 1, 1, 1)
stamped = stack.add(sticker_set)
stack.channel_for(stamped, "BASE_COLOR").image = float_image
e.evaluate_channel(sticker_set.id, "BASE_COLOR")
composite = images.pixels(next(o.image for o in sticker_set.outputs if o.channel == "BASE_COLOR"))
assert np.allclose(composite[256, 256, :3], (1, .5, .5), atol=5e-3), composite[256, 256]
stamped = next(layer for layer in sticker_set.layers if layer.id == stamped.id)
fill_mask = stamped.masks.add()
fill_mask.id, fill_mask.source, fill_mask.image = new_id(), "FILL", float_image
coverage = evaluate_masks(sticker_set, stamped, 512, 0, 512, {}, {})
assert np.allclose(coverage[256, 256], 1, atol=5e-3), coverage[256, 256]
stamped.masks.clear()
# Other bakers leave inf/NaN in unused EXR texels (0.9.7): the decoder reads
# them as 1/0, so a FILL of such a file evaluates finite output and an effect
# on it no longer fails the set with 'Texture filter input must be finite'.
height = np.full((8, 8, 4), .5, np.float32)
height[..., 3], height[0, 0, :3], height[1, 1, :3], height[2, 2, :3] = 1, np.inf, np.nan, -np.inf
authored = bpy.data.images.new("Authored height", 8, 8, alpha=True, float_buffer=True)
authored.pixels.foreach_set(height.ravel())
path = root / ".blender-test" / "nonfinite.exr"
authored.filepath_raw, authored.file_format = str(path), "OPEN_EXR"
authored.save()
exr = bpy.data.images.load(str(path))
assert exr.is_float and not np.isfinite(images.pixels(exr)).all()
assert np.isfinite(read({}, exr, linear=True)).all()
rough = stack.channel_for(stamped, "ROUGHNESS")
rough.enabled, rough.image = True, exr
blur = rough.effects.add(); blur.kind, blur.radius = "GAUSSIAN_BLUR", 2
e.evaluate_channel(sticker_set.id, "ROUGHNESS")
roughness = images.pixels(next(o.image for o in sticker_set.outputs if o.channel == "ROUGHNESS"))
assert np.isfinite(roughness).all() and abs(roughness[256, 256, 0] - .5) < 5e-2, roughness[256, 256]
rough.effects.clear()
rough.image, rough.enabled = None, False
ts = bpy.context.scene.open_painter.texture_sets[0]   # creating a set invalidated the reference
# Representative CPU composition benchmark: 20 layers, six channels at 2K.
ts.resolution = "2048"
stack.channel_for(ts.layers[0], "BASE_COLOR").image = None
for index in range(19):
    layer = stack.add(ts)
    layer.opacity = 0.2
for layer in ts.layers:
    for channel in layer.channels:
        channel.enabled = True
start = time.perf_counter()
e.evaluate_texture_set(ts.id)
full = time.perf_counter() - start
stack.channel_for(ts.layers[-1], "ROUGHNESS").value = 0.7
start = time.perf_counter()
e.evaluate_texture_set(ts.id)
edit = time.perf_counter() - start
print(f"BENCHMARK 2K / 20 layers / 6 channels: full={full:.3f}s; roughness edit={edit:.3f}s")
# Clean lifecycle: callbacks must not grow across reloads.
open_painter.unregister()
for _ in range(3):
    before = len(bpy.app.handlers.load_post)
    open_painter.register()
    open_painter.unregister()
    assert len(bpy.app.handlers.load_post) == before
print(
    "PHASE 10 PASS: triplanar, isolated duplication/dirty state, PNG/TIFF 8/16 RGBA, lifecycle, performance"
)
