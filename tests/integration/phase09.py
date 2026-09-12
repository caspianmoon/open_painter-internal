import sys, shutil
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
from open_painter.export.exporter import export, filename

open_painter.register()
ts = sets.create(bpy.context.scene, "Asset")
ts.resolution = "512"
stack.add(ts)
for key, value in [("AO", 0.8), ("ROUGHNESS", 0.3), ("METALLIC", 0.6)]:
    p = stack.channel_for(ts.layers[0], key)
    p.enabled = True
    p.value = value
p = stack.channel_for(ts.layers[0], "BASE_COLOR")
p.color = (0.18, 0.18, 0.18, 1)
folder = root / ".blender-test" / "export"
shutil.rmtree(folder, ignore_errors=True)
ts.export.directory = str(folder)
ts.export.preset = "UNREAL"
paths = export(bpy.context, ts, ts.export)
assert {p.name for p in paths} == {"Asset_BaseColor.png", "Asset_Normal.png", "Asset_ORM.png"}
image = bpy.data.images.load(str(folder / "Asset_ORM.png"))
image.colorspace_settings.name = "Non-Color"
assert tuple(image.size) == (512, 512)
assert np.allclose(images.pixels(image)[0, 0, :3], (0.8, 0.3, 0.6), atol=1 / 255)
base = bpy.data.images.load(str(folder / "Asset_BaseColor.png"))
base.colorspace_settings.name = "Non-Color"
assert np.isclose(images.pixels(base)[0, 0, 0], 0.461, atol=0.005)
assert filename("../My Asset", "ORM", "png", "P_", "_v1") == "P_.._My_Asset_ORM_v1.png"
try:
    export(bpy.context, ts, ts.export)
    raise AssertionError("Overwrite accepted")
except ValueError:
    pass
ts.export.preset = "UNITY"
ts.export.prefix = "Unity_"
ts.export.resolution = "1024"
export(bpy.context, ts, ts.export)
image = bpy.data.images.load(str(folder / "Unity_Asset_MetallicSmoothness.png"))
image.colorspace_settings.name = "Non-Color"
assert tuple(image.size) == (1024, 1024)
assert np.allclose(images.pixels(image)[0, 0, [0, 3]], (0.6, 0.7), atol=1 / 255)
open_painter.unregister()
print(
    "PHASE 9 PASS: UE5 names + ORM pixels, sRGB Base Color, Unity packing, resampling, overwrite protection"
)
