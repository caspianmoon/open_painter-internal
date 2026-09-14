"""Render every geometry effect close up for visual review (Cycles CPU, headless).

    blender --background --factory-startup --python scripts/render_geometry_effects.py -- --out .blender-test/effects
"""
import argparse
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import open_painter
from open_painter.core import image_manager as images, texture_set_manager as sets
from open_painter.geometry_effects import manager, weld

parser = argparse.ArgumentParser()
parser.add_argument("--out", type=Path, default=ROOT / ".blender-test" / "effects")
parser.add_argument("--samples", type=int, default=48)
parser.add_argument("--size", type=int, default=720)
parser.add_argument("--only", nargs="*", default=None)
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
args.out.mkdir(parents=True, exist_ok=True)
open_painter.register()
scene = bpy.context.scene
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete()
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.samples = args.samples
scene.cycles.use_denoising = True
scene.render.resolution_x, scene.render.resolution_y = args.size, int(args.size * .7)
scene.render.image_settings.file_format = "PNG"
scene.world = scene.world or bpy.data.worlds.new("w")
scene.world.use_nodes = True
scene.world.node_tree.nodes["Background"].inputs["Color"].default_value = (.45, .5, .58, 1)
scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value = .8
sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
scene.collection.objects.link(sun)
sun.data.energy, sun.data.angle = 3.5, .08
sun.rotation_euler = (.9, .25, .7)

# A painted steel slab, 1 m square, 8 cm thick, with a soft bevel.
bpy.ops.mesh.primitive_cube_add(size=1)
slab = bpy.context.object
slab.scale = (.5, .5, .04)
bpy.ops.object.transform_apply(scale=True)
bevel = slab.modifiers.new("Bevel", "BEVEL")
bevel.width, bevel.segments = .012, 3
bpy.ops.object.shade_smooth()
paint = bpy.data.materials.new("Painted steel")
paint.use_nodes = True
principled = paint.node_tree.nodes["Principled BSDF"]
principled.inputs["Base Color"].default_value = (.32, .34, .36, 1)
principled.inputs["Roughness"].default_value = .45
principled.inputs["Metallic"].default_value = .1
slab.data.materials.append(paint)
ts = sets.create(scene, "Effects", 512)
sets.assign(scene, ts, slab)
slab.data.materials[0] = paint  # keep the neutral review material on the slab
mask = images.create("Everywhere", 64, (1, 1, 1, 1))
# A soft patch mask: moss and snow gather in a region rather than covering everything.
patch = images.create("Patch", 128, (0, 0, 0, 1))
yy, xx = np.mgrid[0:128, 0:128] / 127
falloff = np.clip(1.4 - np.hypot((xx - .55) * 1.6, (yy - .5) * 1.1) * 3, 0, 1) ** 1.5
rgba = np.zeros((128, 128, 4), np.float32)
rgba[..., :3] = falloff[..., None]
rgba[..., 3] = 1
images.write(patch, rgba)

cam_data = bpy.data.cameras.new("cam")
camera = bpy.data.objects.new("cam", cam_data)
scene.collection.objects.link(camera)
scene.camera = camera
cam_data.lens = 85


def frame(target, distance):
    camera.location = target + Vector((.55, -.75, .6)).normalized() * distance
    camera.rotation_euler = (target - camera.location).to_track_quat("-Z", "Y").to_euler()


def render(name):
    scene.render.filepath = str(args.out / (name + ".png"))
    bpy.ops.render.render(write_still=True)
    print("EFFECT_RENDER", name, scene.render.filepath, flush=True)


setups = {
    "rust_flakes": dict(kind="RUST", density=2600, size=.018, lift=0.0004, distance=.3, mask=mask),
    "moss": dict(kind="MOSS", density=6000, size=.08, lift=0, distance=.36, mask=patch),
    "snow_clumps": dict(kind="SNOW", density=1600, size=.07, lift=0, distance=.5, mask=patch),
    "splinters": dict(kind="SPLINTERS", density=1200, size=.04, lift=.0008, distance=.32, mask=mask),
    "peel_fragments": dict(kind="PEEL", density=900, size=.03, lift=.0003, distance=.28, mask=mask),
}
for name, setup in setups.items():
    if args.only and name not in args.only:
        continue
    # Moss normally sits on the Moss texture effect's green base; show that context.
    principled.inputs["Base Color"].default_value = (.07, .09, .035, 1) if name == "moss" else (.32, .34, .36, 1)
    helper = manager.create(ts, slab, setup["mask"], setup["kind"], setup["density"], setup["size"], 7, setup["lift"])
    frame(Vector((0.1, -0.05, .04)), setup["distance"])
    render(name)
    manager.remove(helper)

if not args.only or "weld" in args.only:
    principled.inputs["Base Color"].default_value = (.32, .34, .36, 1)
    curve_data = bpy.data.curves.new("seam", "CURVE")
    curve_data.dimensions = "3D"
    spline = curve_data.splines.new("BEZIER")
    spline.bezier_points.add(2)
    for point, co in zip(spline.bezier_points, ((-.35, -.1, .04), (0, .05, .04), (.35, -.05, .04))):
        point.co = co
        point.handle_left_type = point.handle_right_type = "AUTO"
    curve = bpy.data.objects.new("Weld path", curve_data)
    scene.collection.objects.link(curve)
    helper = weld.create(scene, curve, radius=.012)
    frame(Vector((0, 0, .045)), .28)
    render("weld")
    manager.remove(helper)
