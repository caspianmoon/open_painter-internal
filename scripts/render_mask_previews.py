"""Render actual baked grayscale mask textures on a recessed preview mesh.

The temporary emission material displays the already evaluated texture pixels;
all mask and generator work remains in Open Painter's texture evaluator.
"""

import argparse
import json
import sys
from pathlib import Path
from time import perf_counter

import bpy
import numpy as np

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
import open_painter
from open_painter.baking.bake_manager import bake_selected, register_map
from open_painter.baking.maps.utility import bake_utility
from open_painter.core import image_manager, layer_stack, texture_set_manager as sets
from open_painter.evaluation.mask_eval import evaluate_masks
from open_painter.smart_masks.library import PRESETS, apply

parser = argparse.ArgumentParser()
parser.add_argument("--mask", action="append", choices=[preset[0] for preset in PRESETS])
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])

open_painter.register()
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = 16
scene.cycles.use_denoising = False
scene.render.resolution_x = scene.render.resolution_y = 256
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGBA"
scene.world.color = (.035, .035, .035)
scene.view_settings.view_transform = "Standard"

# Same real cavity used by the material catalog's preview mesh. A convex sphere
# cannot demonstrate cavity dirt, moisture, sheltered grime or drip origins.
bpy.ops.mesh.primitive_cube_add(size=1.6)
mesh = bpy.context.object
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, -.75, 0))
cutter = bpy.context.object
cutter.scale = (1.05, .72, .96)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
bpy.context.view_layer.objects.active = mesh
cut = mesh.modifiers.new("Preview cavity", "BOOLEAN")
cut.operation = "DIFFERENCE"
cut.solver = "EXACT"
cut.object = cutter
bpy.ops.object.modifier_apply(modifier=cut.name)
bpy.data.objects.remove(cutter, do_unlink=True)
mesh.select_set(True)
bpy.context.view_layer.objects.active = mesh
bevel = mesh.modifiers.new("Preview bevel", "BEVEL")
bevel.width, bevel.segments = .06, 1
bpy.ops.object.modifier_apply(modifier=bevel.name)
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.uv.smart_project(island_margin=.025)
bpy.ops.object.mode_set(mode="OBJECT")
bpy.ops.object.camera_add(location=(2.9, -4.5, 2.5))
camera = bpy.context.object
camera.rotation_euler = (-camera.location).to_track_quat("-Z", "Y").to_euler()
camera.data.type, camera.data.ortho_scale = "ORTHO", 2.7
scene.camera = camera

texture_set = sets.create(scene, "Smart mask preview", 1024)
sets.assign(scene, texture_set, mesh)
texture_set.bake.selected_to_active = False
texture_set.bake.normal = False
texture_set.bake.ao = texture_set.bake.curvature = True
texture_set.bake.curvature_width = 6
texture_set.bake.samples = 32
bpy.context.view_layer.update()
bake_selected(bpy.context, texture_set)
for kind in ("POSITION", "WORLD_NORMAL"):
    register_map(texture_set, kind, bake_utility(texture_set, kind))
assert {item.kind for item in texture_set.baked_maps} == {"AO", "CURVATURE", "POSITION", "WORLD_NORMAL"}

layer = layer_stack.add(texture_set, "FILL", "Preview mask output")
preview_material = bpy.data.materials.new("Grayscale texture display")
preview_material.use_nodes = True
nodes = preview_material.node_tree.nodes
nodes.clear()
texture = nodes.new("ShaderNodeTexImage")
texture.interpolation = "Linear"
emission = nodes.new("ShaderNodeEmission")
output = nodes.new("ShaderNodeOutputMaterial")
preview_material.node_tree.links.new(texture.outputs["Color"], emission.inputs["Color"])
preview_material.node_tree.links.new(emission.outputs[0], output.inputs["Surface"])
mesh.data.materials[0] = preview_material

path = root / "src/open_painter/assets/previews"
path.mkdir(parents=True, exist_ok=True)
resolution = int(texture_set.resolution)
image = image_manager.create("Evaluated grayscale mask", resolution)
texture.image = image
rgba = np.ones((resolution, resolution, 4), dtype=np.float32)
statistics = {}
for name in args.mask or [preset[0] for preset in PRESETS]:
    started = perf_counter()
    layer.masks.clear()
    apply(texture_set, name)
    value = evaluate_masks(texture_set, layer, resolution, 0, resolution, {}, {})
    if not np.isfinite(value).all() or float(value.std()) < 1e-5:
        raise ValueError(f"{name} produced an invalid or constant mask on the preview fixture.")
    rgba[..., :3] = value[..., None]
    image_manager.write(image, rgba)
    scene.render.filepath = str(path / (name.replace(" ", "_") + ".png"))
    bpy.ops.render.render(write_still=True)
    statistics[name] = {"seconds": round(perf_counter() - started, 3),
                        "stddev": float(value.std()), "minimum": float(value.min()),
                        "maximum": float(value.max()), "coverage": float(np.mean(value > .1))}
    print("MASK_PREVIEW", name, statistics[name], flush=True)
report = root / ".blender-test" / "mask-preview-statistics.json"
report.parent.mkdir(exist_ok=True)
report.write_text(json.dumps(statistics, indent=2), encoding="utf-8")
open_painter.unregister()
