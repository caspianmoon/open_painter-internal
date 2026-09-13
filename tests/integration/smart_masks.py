import sys
from pathlib import Path
import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import open_painter
from open_painter.core import (
    texture_set_manager as sets,
    layer_stack as stack,
    image_manager as images,
)
from open_painter.smart_masks.library import PRESETS, apply
from open_painter.evaluation.mask_eval import evaluate_masks
from open_painter.ui.dock.interaction import slider_owner

open_painter.register()
ts = sets.create(bpy.context.scene, "Masks", 512)
layer = stack.add(ts, "FILL")
try:
    apply(ts, "Cavity Dirt")
    raise AssertionError("Missing maps were accepted")
except ValueError as e:
    assert "AO" in str(e) and "CURVATURE" in str(e)
assert not layer.masks
for kind in ["AO", "CURVATURE", "WORLD_NORMAL", "POSITION"]:
    m = ts.baked_maps.add()
    m.kind = kind
    m.image = images.create(kind, 32)
    data = np.ones((32, 32, 4), np.float32)
    data[..., :3] = np.linspace(0, 1, 32)[None, :, None]
    if kind == "WORLD_NORMAL":
        z = np.linspace(-1, 1, 32, dtype=np.float32)[:, None]
        data[..., 0] = .5 + .5 * np.sqrt(np.maximum(0, 1 - z * z))
        data[..., 1], data[..., 2] = .5, (z + 1) * .5
    elif kind == "POSITION":
        data[..., 0] = np.linspace(0, 1, 32)[None, :]
        data[..., 1] = data[..., 2] = np.linspace(0, 1, 32)[:, None]
    images.write(m.image, data)
outputs = []
for preset in PRESETS:
    layer.masks.clear()
    mask = apply(ts, preset[0])
    value = evaluate_masks(ts, layer, 512, 0, 512, {})
    assert np.isfinite(value).all() and value.std() > 0.01
    outputs.append(value)
    assert mask.name == preset[0] and len(ts.layers) == 1
assert all(not np.array_equal(a, b) for i, a in enumerate(outputs) for b in outputs[i + 1 :])
identifier = layer.id
stack.add(ts, "FILL", "Another layer")
bpy.context.scene.open_painter.blend_channel = "METALLIC"
bpy.context.scene.open_painter.paint_channel = "ROUGHNESS"
owner, key = slider_owner(bpy.context, ("ROW_CHANNEL", identifier))
setattr(owner, key, 0.35)
assert abs(stack.channel_for(ts.layers[0], "METALLIC").opacity - 0.35) < 1e-6
assert stack.channel_for(ts.layers[1], "METALLIC").opacity == 1
assert bpy.context.scene.open_painter.paint_channel == "ROUGHNESS"
# Normal effects replace prior coverage at full strength; Multiply remains opt-in.
ts.active_layer = 0
layer = stack.active(ts)
layer.masks.clear()
first = apply(ts, "Edge Clean")
second = apply(ts, "Dirt Heavy")
combined = evaluate_masks(ts, layer, 512, 0, 512, {})
first.enabled = False
single = evaluate_masks(ts, layer, 512, 0, 512, {})
np.testing.assert_allclose(combined, single)
first.enabled = True
second.operation = "MULTIPLY"
assert not np.array_equal(evaluate_masks(ts, layer, 512, 0, 512, {}), single)
apply(ts, "Edge Broken", replace=True)
assert len(layer.masks) == 1 and layer.masks[0].source == "GENERATOR"
path = Path(__file__).resolve().parents[2] / ".blender-test/native-masks.blend"
bpy.ops.wm.save_as_mainfile(filepath=str(path))
bpy.ops.wm.open_mainfile(filepath=str(path))
mask = bpy.context.scene.open_painter.texture_sets[0].layers[0].masks[0]
assert mask.source == "GENERATOR" and mask.generator == "EDGE_WEAR" and mask.operation == "NORMAL"
open_painter.unregister()
print("SMART_MASKS_AND_INLINE_OPACITY_PASS", flush=True)
