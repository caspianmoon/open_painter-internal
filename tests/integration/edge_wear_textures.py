"""Edge noise stays within curvature coverage; baking preserves actual output pixels."""

import sys
from pathlib import Path
import bpy
import numpy as np

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "src"))
import open_painter
from open_painter.generators.edge_wear import edge_wear
from open_painter.core import (
    texture_set_manager as sets,
    layer_stack as stack,
    image_manager as images,
)
from open_painter.smart_masks.library import apply
from open_painter.evaluation.mask_eval import evaluate_masks
from open_painter.evaluation.evaluator import LayerEvaluator

noise = np.random.default_rng(17).random((64, 64)).astype(np.float32)
flat = np.zeros_like(noise)
for amount in (0, 0.46, 0.5, 1):
    for strength in (0, 0.65, 1):
        assert not edge_wear(flat, None, noise, amount, 20, strength).any()
edges = flat.copy()
edges[:, 30:34] = 1
wear = edge_wear(edges, None, noise, 0.46, 4, 0.65)
assert wear[:, 30:34].max() > 0.5
assert not wear[edges == 0].any()
assert not edge_wear(edges, np.zeros_like(edges), noise, 0.46, 4, 0.65).any()

open_painter.register()
ts = sets.create(bpy.context.scene, "Edge texture test", 512)
ts.preview_backend = 'CPU'  # This regression verifies the fixed raster display adapter.
sets.assign(bpy.context.scene, ts, bpy.context.object)
base = stack.add(ts, "FILL", "Substrate")
stack.channel_for(base, "BASE_COLOR").color = (0, 0, 0, 1)
coat = stack.add(ts, "FILL", "Wear")
stack.channel_for(coat, "BASE_COLOR").color = (1, 0, 0, 1)
curvature = images.create("Edge Map", 512)
data = np.ones((512, 512, 4), np.float32)
data[..., :3] = 0
data[:, 240:272, :3] = 1
images.write(curvature, data)
mesh_map = ts.baked_maps.add()
mesh_map.kind = "CURVATURE"
mesh_map.image = curvature
apply(ts, "Edge Broken")
mask_pixels = evaluate_masks(ts, coat, 512, 0, 512, {})
assert not mask_pixels[:, :200].any() and mask_pixels[:, 250:260].max() > 0.5
LayerEvaluator(bpy.context.scene).evaluate_texture_set(ts.id)
output = next(o.image for o in ts.outputs if o.channel == "BASE_COLOR")
before = images.pixels(output).copy()
assert not before[:, :200, 0].any() and before[:, 250:260, 0].max() > 0.5
nodes_before = [(n.name, n.bl_idname) for n in ts.material.node_tree.nodes]
assert not any(
    kind in {"ShaderNodeTexNoise", "ShaderNodeNewGeometry", "ShaderNodeTexVoronoi"}
    for _, kind in nodes_before
)
assert bpy.ops.open_painter.mask(action="BAKE") == {"FINISHED"}
assert len(coat.masks) == 1 and coat.masks[0].source == "PAINT"
assert coat.masks[0].image.packed_file
np.testing.assert_allclose(images.pixels(coat.masks[0].image)[..., 0], mask_pixels, atol=1e-6)
LayerEvaluator(bpy.context.scene).evaluate_texture_set(ts.id)
np.testing.assert_allclose(images.pixels(output), before, atol=1e-6)
assert [(n.name, n.bl_idname) for n in ts.material.node_tree.nodes] == nodes_before
path = root / ".blender-test/edge-textures.blend"
bpy.ops.wm.save_as_mainfile(filepath=str(path))
bpy.ops.wm.open_mainfile(filepath=str(path))
ts = bpy.context.scene.open_painter.texture_sets[0]
np.testing.assert_allclose(
    images.pixels(ts.layers[ts.active_layer].masks[0].image)[..., 0], mask_pixels, atol=1e-6
)
open_painter.unregister()
print(
    "EDGE_WEAR_TEXTURES_PASS: zero off-edge coverage, texture bake equivalence, no effect nodes, packed save/reopen"
)
