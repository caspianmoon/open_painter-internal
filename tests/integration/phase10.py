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
other = sets.duplicate(bpy.context.scene, 0)
assert other.id != original_id and not other.assignments and len(other.layers) == 1
# A layer edit must leave unrelated texture sets clean.
e.evaluate_texture_set(other.id)
ts = bpy.context.scene.open_painter.texture_sets[0]
ts.layers[0].opacity = 0.9
assert not other.dirty
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
