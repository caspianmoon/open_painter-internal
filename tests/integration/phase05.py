import sys
from pathlib import Path
import bpy
import numpy as np

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "src"))
import open_painter
from open_painter.core import texture_set_manager as sets, image_manager as images
from open_painter.baking.bake_manager import bake_selected

open_painter.register()
low = bpy.context.object
low.name = "Cube_LP"
bpy.ops.object.duplicate()
high = bpy.context.object
high.name = "Cube_HP"
bevel = high.modifiers.new("High detail bevel", "BEVEL")
bevel.width = 0.15
bevel.segments = 3
bpy.ops.object.modifier_apply(modifier=bevel.name)
ts = sets.create(bpy.context.scene, "Bake Cube")
ts.resolution = "512"
sets.assign(bpy.context.scene, ts, low)
ref = ts.bake.high_objects.add()
ref.object = high
# High poly is beveled inward; rays originate outside the low poly.
ts.bake.ray_distance = 0.3
ts.bake.samples = 2
scene = bpy.context.scene
state = (
    scene.render.engine,
    scene.render.bake.use_selected_to_active,
    scene.render.bake.margin,
    bpy.context.view_layer.objects.active,
    tuple(bpy.context.selected_objects),
    low.active_material,
)
bake_selected(bpy.context, ts)
assert state == (
    scene.render.engine,
    scene.render.bake.use_selected_to_active,
    scene.render.bake.margin,
    bpy.context.view_layer.objects.active,
    tuple(bpy.context.selected_objects),
    low.active_material,
)
# The edge map bakes as a four-reach set around the requested Curvature Radius.
assert {m.kind for m in ts.baked_maps} == {"NORMAL", "AO", "CURVATURE", "CURVATURE_THIN",
                                           "CURVATURE_WIDE", "CURVATURE_XWIDE"}, {m.kind for m in ts.baked_maps}
for m in ts.baked_maps:
    data = images.pixels(m.image)
    assert data.shape == (512, 512, 4) and np.isfinite(data).all()
assert (
    images.pixels(next(m.image for m in ts.baked_maps if m.kind == "CURVATURE"))[..., :3].max() > 0
)
bpy.ops.wm.save_as_mainfile(filepath=str(root / "examples" / "bake_cube.blend"))
open_painter.unregister()
print("PHASE 5 PASS: real Cycles HP/LP Normal + AO + Curvature; state restored")
