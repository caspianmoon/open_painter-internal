"""Render the material-driven geometry effects (0.10.1) for visual review (Cycles CPU, headless).

    blender --background --factory-startup --python scripts/render_material_geometry.py -- --out .blender-test/material-geometry

A painted steel plate with a boss and a slot is baked (AO, curvature, position, world normal),
aged twenty-five coastal years, and receives rust flakes and peel fragments where the aging
puts them, bolts on painted spots and snow on its upward faces; an oak plank aged in the
tropics receives moss and splinters. Each render shows the surface and the pieces together.
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
from open_painter.aging import engine
from open_painter.baking.bake_manager import bake_selected
from open_painter.core import image_manager as images, layer_stack as stack, texture_set_manager as sets
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.geometry_effects import manager, sources

parser = argparse.ArgumentParser()
parser.add_argument("--out", type=Path, default=ROOT / ".blender-test" / "material-geometry")
parser.add_argument("--samples", type=int, default=32)
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
cam_data = bpy.data.cameras.new("cam")
camera = bpy.data.objects.new("cam", cam_data)
scene.collection.objects.link(camera)
scene.camera = camera
cam_data.lens = 70


def frame(target, distance, direction=(.55, -.75, .6)):
    camera.location = target + Vector(direction).normalized() * distance
    camera.rotation_euler = (target - camera.location).to_track_quat("-Z", "Y").to_euler()


def render(name):
    scene.render.filepath = str(args.out / (name + ".png"))
    bpy.ops.render.render(write_still=True)
    print("MATERIAL_GEOMETRY_RENDER", name, scene.render.filepath, flush=True)


def plate():
    """A 60 x 40 cm steel plate, 1 cm thick, with a round boss and a slot, bevelled, smart-UV unwrapped."""
    bpy.ops.mesh.primitive_cube_add(size=1)
    obj = bpy.context.object
    obj.name = "Plate"
    obj.scale = (.3, .2, .005)
    bpy.ops.object.transform_apply(scale=True)
    bpy.ops.mesh.primitive_cylinder_add(radius=.05, depth=.03, location=(-.12, .05, .015), vertices=48)
    boss = bpy.context.object
    bpy.ops.mesh.primitive_cube_add(size=1, location=(.12, -.04, .0))
    slot = bpy.context.object
    slot.scale = (.12, .03, .05)
    bpy.ops.object.transform_apply(scale=True)
    for other, operation in ((boss, "UNION"), (slot, "DIFFERENCE")):
        modifier = obj.modifiers.new(operation.title(), "BOOLEAN")
        modifier.object, modifier.operation = other, operation
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.modifier_apply(modifier=modifier.name)
        bpy.data.objects.remove(other, do_unlink=True)
    bevel = obj.modifiers.new("Bevel", "BEVEL")
    bevel.width, bevel.segments, bevel.limit_method = .004, 2, "ANGLE"
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier="Bevel")
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=1.15, island_margin=.02)
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.shade_smooth_by_angle(angle=.6)
    return obj


def plank():
    """An oak plank, 60 x 12 x 3 cm, with rounded edges."""
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0))
    obj = bpy.context.object
    obj.name = "Plank"
    obj.scale = (.3, .06, .015)
    bpy.ops.object.transform_apply(scale=True)
    bevel = obj.modifiers.new("Bevel", "BEVEL")
    bevel.width, bevel.segments = .006, 3
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier="Bevel")
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=1.15, island_margin=.02)
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.shade_smooth_by_angle(angle=.6)
    return obj


def prepare(obj, name, material, coats, environment, years, usage="MODERATE"):
    ts = sets.create(scene, name, 1024)
    sets.assign(scene, ts, obj)
    ts.bake.normal, ts.bake.ao, ts.bake.curvature = False, True, True
    ts.bake.selected_to_active = False
    ts.bake.position, ts.bake.world_normal = True, True
    bake_selected(bpy.context, ts)
    aging = ts.aging
    aging.material = material
    aging.conversion, aging.primer, aging.paint, aging.topcoat = coats
    aging.environment = environment
    aging.usage = usage
    aging.age_years = years
    engine.apply(ts)
    LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
    print("PREPARED", name, engine.prediction(ts)["label"], flush=True)
    return ts


def effect(ts, obj, kind, source="MATERIAL", layer=None, density=1500, size=.02, lift=.0004, seed=7, prototypes=None):
    placement = sources.placement(ts, kind, source, layer)
    helper = manager.create(ts, obj, None, kind, density, size, seed, lift, placement=placement, prototypes=prototypes, follow=True)
    print("EFFECT", kind, placement.label, f"field mean {float(placement.density.mean()):.3f}",
          None if helper is None else helper["instance_count"], flush=True)
    return helper


steel = plate()
steel_set = prepare(steel, "Steel plate", "CARBON_STEEL", ("NONE", "EPOXY_PRIMER", "POLYURETHANE_PAINT", "NONE"), "COASTAL", 14, "HEAVY")
steel.location = (0, 0, 0)
wanted = lambda name: not args.only or name in args.only
helpers = []
if wanted("rust_flakes"):
    helpers.append(effect(steel_set, steel, "RUST", density=6000, size=.012))
    frame(Vector((0, 0, .01)), .55)
    render("rust_flakes")
if wanted("peel_fragments"):
    helpers.append(effect(steel_set, steel, "PEEL", density=6000, size=.014, lift=.0002))
    frame(Vector((0, 0, .01)), .55)
    render("rust_and_peel")
    frame(Vector((-.1, .04, .02)), .22, (.3, -.8, .5))
    render("rust_and_peel_close")
for helper in helpers:
    if helper is not None:
        manager.remove(helper)
if wanted("bolts"):
    spots = stack.add(steel_set, "FILL", "Bolt spots")
    mask = spots.masks.add()
    mask.source = "PAINT"
    mask.image = images.create("Spots", 512)
    data = np.zeros((512, 512, 4), np.float32)
    data[..., 3] = 1
    # Dots in the plate's UV space: sample the world-position raster to find the top face.
    from open_painter.painting.projection import geometry
    positions, normals, valid = geometry(steel_set, 512, coverage=True)
    top = valid & (normals[..., 2] > .9) & (positions[..., 2] > 0)
    rows, cols = np.nonzero(top)
    print("TOP TEXELS", len(rows), flush=True)
    rng = np.random.default_rng(3)
    yy, xx = np.mgrid[0:512, 0:512]
    for _ in range(9):
        index = rng.integers(len(rows))
        r = int(rng.integers(5, 9))
        data[(yy - rows[index]) ** 2 + (xx - cols[index]) ** 2 <= r * r, :3] = 1
    images.write(mask.image, data)
    spots.visible = False
    steel_set.active_layer = stack.index_of(steel_set, spots.id)
    helper = effect(steel_set, steel, "BOLT", "SPOTS", spots, size=1.0, lift=0)
    frame(Vector((0, 0, .01)), .55)
    render("bolts_on_spots")
    if helper is not None: manager.remove(helper)
if wanted("snow"):
    helper = effect(steel_set, steel, "SNOW", density=900, size=.05, lift=0)
    frame(Vector((0, 0, .01)), .6)
    render("snow")
    if helper is not None: manager.remove(helper)
steel.hide_render = steel.hide_viewport = True

oak = plank()
oak.location = (0, 0, .3)
oak_set = prepare(oak, "Oak plank", "OAK", ("NONE", "NONE", "NONE", "NONE"), "TROPICAL", 12, "LIGHT")
if wanted("moss"):
    helper = effect(oak_set, oak, "MOSS", density=4000, size=.05, lift=0)
    frame(Vector((0, 0, .31)), .5)
    render("moss")
    if helper is not None: manager.remove(helper)
if wanted("splinters"):
    helper = effect(oak_set, oak, "SPLINTERS", density=1500, size=.03, lift=.0006)
    frame(Vector((0, 0, .31)), .5)
    render("splinters")
    if helper is not None: manager.remove(helper)
print("MATERIAL_GEOMETRY_DONE", flush=True)
