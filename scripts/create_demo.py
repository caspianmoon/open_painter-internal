"""Create a portable, packed demo after the bake and viewport acceptance tests."""

import sys
from pathlib import Path
import bpy
from mathutils import Quaternion

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
import open_painter
from open_painter.core import layer_stack as stack
from open_painter.smart_materials import loader, serializer
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.export.exporter import export

open_painter.register()
bpy.ops.wm.open_mainfile(filepath=str(root / "examples/bake_cube.blend"))
ts = bpy.context.scene.open_painter.texture_sets[0]
ts.name = "Painted_Steel_Cube"
loader.apply(ts, root / "src/open_painter/assets/smart_materials/Painted_Steel.openpaintmaterial")
bpy.ops.open_painter.bake(action="USE_MAPS")
stack.add(ts, "PAINT", "Hand-painted Roughness")
painted = root / ".blender-test/viewport_painted.blend"
with bpy.data.libraries.load(str(painted), link=False) as (source, target):
    target.images = [
        name for name in source.images if name == "Viewport Test_Paint Layer_ROUGHNESS"
    ]
image = target.images[0]
channel = stack.channel_for(ts.layers[-1], "ROUGHNESS")
channel.enabled = True
channel.image = image
LayerEvaluator(bpy.context.scene).evaluate_texture_set(ts.id)
high = next(ref.object for ref in ts.bake.high_objects)
high.hide_set(True)
high.hide_render = True
low = ts.assignments[0].object
for obj in bpy.context.selected_objects:
    obj.select_set(False)
low.select_set(True)
bpy.context.view_layer.objects.active = low
for screen in bpy.data.screens:
    for area in screen.areas:
        if area.type == "VIEW_3D":
            space = area.spaces.active
            space.shading.type = "MATERIAL"
            space.overlay.show_overlays = False
            space.show_region_ui = True
            space.region_3d.view_distance = 6
            space.region_3d.view_rotation = Quaternion((0.822, 0.424, 0.176, 0.339))
# Save the reusable stack and example Unreal outputs, then store a relative export path.
material = root / "examples/Painted_Steel_Cube.openpaintmaterial"
material.unlink(missing_ok=True)
serializer.save(ts, material)
folder = root / "examples/exports"
folder.mkdir(exist_ok=True)
for name in ("BaseColor", "Normal", "ORM"):
    (folder / f"{ts.name}_{name}.png").unlink(missing_ok=True)
ts.export.directory = str(folder)
ts.export.preset = "UNREAL"
export(bpy.context, ts, ts.export)
ts.export.directory = "//exports"
from open_painter.ui import editor

area = next(a for a in bpy.context.screen.areas if a.type == "VIEW_3D")
editor.mount(bpy.context.window, area)
bpy.ops.wm.save_as_mainfile(filepath=str(root / "examples/open_painter_demo.blend"))
print("DEMO READY: baked maps, Painted Steel, actual brush raster, smart material and UE5 textures")
