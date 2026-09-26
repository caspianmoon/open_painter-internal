"""Geometry effects follow the material (0.10.1): placements from what the aging shows, from mesh
features, from painted spots; pieces coloured by the surface under them; the artist's own
prototypes; helpers that regenerate when the material changes."""
import sys
from pathlib import Path

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
import open_painter
from open_painter.aging import engine
from open_painter.core import image_manager as images, layer_stack as stack, texture_set_manager as sets
from open_painter.evaluation.blend_modes import sample_uv
from open_painter.geometry_effects import manager, materials, sources

open_painter.register()
scene = bpy.context.scene
cube = scene.objects["Cube"]


def bake_synthetic(ts, n=64):
    u, v = np.meshgrid((np.arange(n) + .5) / n, (np.arange(n) + .5) / n)
    for kind in ("CURVATURE", "AO", "POSITION", "WORLD_NORMAL"):
        entry = ts.baked_maps.add()
        entry.kind = kind
        entry.image = images.create(f"{ts.name} {kind}", n)
        value = np.ones((n, n, 4), np.float32)
        if kind == "CURVATURE":
            value[..., :3] = np.clip((.16 - np.abs(u - .5)) * 8, 0, 1)[..., None]
        elif kind == "AO":
            value[..., :3] = (.25 + .75 * v)[..., None]
        elif kind == "POSITION":
            value[..., 0], value[..., 1], value[..., 2] = u, v, v
        else:
            z = 2 * v - 1
            value[..., 0] = .5 + .5 * np.sqrt(np.maximum(0, 1 - z * z))
            value[..., 1], value[..., 2] = .5, v
        images.write(entry.image, value)


def attribute(helper, name, size):
    return np.array([getattr(item, "vector" if size == 3 else "color" if size == 4 else "value")[:] if size > 1
                     else getattr(item, "value") for item in helper.data.attributes[name].data])


ts = sets.create(scene, "Aged steel", 512)
sets.assign(scene, ts, cube)
bake_synthetic(ts)
ts.aging.environment, ts.aging.age_years, ts.aging.usage = "COASTAL", 25, "HEAVY"
engine.apply(ts)
n = sources.resolution(ts)
assert n == 512

# ---- mesh features come from the geometry itself; the bakes add cavities and edges ------------
feats = sources.features(ts, n)
assert feats["valid"].any() and 0 < float(feats["up"].mean()) < 1 and feats["cavity"] is not None and feats["edges"] is not None
assert feats["down"][feats["valid"]].max() > .9 and feats["low"][feats["valid"]].max() > .9

# ---- rust flakes go where the corrosion is, not where a random mask says ----------------------
rust = sources.placement(ts, "RUST", "MATERIAL")
assert rust.source == "MATERIAL" and rust.label == "corrosion and surface rust" and rust.density.max() == 1.0
outcome = sources._outcome(ts, "RUST", n)
assert not np.any(rust.density[outcome <= 1e-4] > 0), "flakes outside the corrosion"
assert rust.direction is None and not rust.spots
helper = manager.create(ts, cube, None, "RUST", 120, .03, 5, 0, placement=rust, follow=True)
assert helper is not None and helper["source"] == "MATERIAL" and helper["source_label"] == rust.label and helper["follow"]
assert helper["match"] == materials.MATCH["RUST"] and helper.get("mask_image") is None
colors = attribute(helper, "op_color", 4)
assert np.allclose(colors[:, 3], materials.MATCH["RUST"]) and len(np.unique(colors[:, :3].round(2), axis=0)) > 3, "pieces take the surface colour under them"
rust_count = helper["instance_count"]
material = helper.modifiers[0].node_group.nodes[3].inputs["Object"].default_value.data.materials[0]
assert any(node.name == "OP Match Surface" for node in material.node_tree.nodes)
# Instances land on faces the corrosion covers: sample the outcome at each point's UV.
uvs = manager.scatter_points(ts, cube, rust.density, 120, 5, helper, "RUST", .03, slope=False)["uv"]
assert len(uvs) == rust_count and float(sample_uv(outcome[..., None], uvs[:, 0], uvs[:, 1]).min()) > 0
print("GEOMETRY_SOURCES rust", rust_count, flush=True)

# ---- peel fragments sit along the edge of the coating loss and face the paint that holds -------
peel = sources.placement(ts, "PEEL", "MATERIAL")
assert peel.label == "edge of the coating loss" and peel.direction is not None and peel.color_offset > 0
paint = sources._outcome(ts, "PEEL", n)
band = peel.density > 0
width = max(2, n // 96)
smooth = sources._blur(paint, max(1, width // 2))
assert band.any() and float(smooth[band].max()) <= .5 + 1e-6, "the band lies on the bare side"
intact = smooth > .5
assert sources._grow(intact, width)[band].all(), "and within reach of the paint"
lengths = np.linalg.norm(peel.direction[band], axis=-1)
assert np.allclose(lengths[lengths > 0], 1, atol=1e-4)
peel_helper = manager.create(ts, cube, None, "PEEL", 150, .02, 5, 0, placement=peel)
assert peel_helper is not None and np.allclose(attribute(peel_helper, "op_color", 4)[:, 3], materials.MATCH["PEEL"])
paint_color = stack.channel_for(engine.roles_of(ts, engine.folder_of(ts))["paint"], "BASE_COLOR").color[:3]
assert peel.color is not None and np.allclose(attribute(peel_helper, "op_color", 4)[:, :3], paint_color, atol=1e-5), "a chip is the paint that left"
scattered = manager.scatter_points(ts, cube, peel.density, 150, 5, peel_helper, "PEEL", .02, direction=peel.direction, slope=False)
facing = scattered["facing"]
assert facing is not None and np.allclose(np.linalg.norm(facing, axis=1)[np.linalg.norm(facing, axis=1) > 0], 1, atol=1e-4)
assert np.abs(np.sum(facing * scattered["normals"], axis=1)).max() < 1e-3, "facing lies in the surface"
manager.remove(peel_helper)

# ---- nothing to place is said, not scattered ----------------------------------------------------
moss = sources.placement(ts, "MOSS", "MATERIAL")
assert moss.label == "biological growth" and float(moss.density.max()) == 0
assert manager.create(ts, cube, None, "MOSS", 1000, .05, 1, 0, placement=moss) is None
scene.open_painter.active_texture_set = list(scene.open_painter.texture_sets).index(ts)
bpy.context.view_layer.objects.active = cube
try:
    bpy.ops.open_painter.geometry_effect(action="MOSS", source="MATERIAL")
    raise AssertionError("moss was placed on a set without growth")
except RuntimeError as error:
    assert "Nothing to place" in str(error), error

# ---- the operator: rust from the material, bolts on painted spots --------------------------------
before = len([o for o in scene.objects if o.get(manager.TAG) == "INSTANCE_HELPER"])
from open_painter.geometry_effects import limits
budget = limits.headroom(scene)[1] // manager.KINDS["RUST"]["triangles"]
assert bpy.ops.open_painter.geometry_effect(action="RUST", source="MATERIAL", density=200, size=.03, seed=2) == {"FINISHED"}
placed = bpy.context.view_layer.objects.active
assert placed.get(manager.TAG) == "INSTANCE_HELPER" and placed["source"] == "MATERIAL" and placed["kind"] == "RUST"
assert "pieces on corrosion and surface rust" in scene.open_painter.status
# The heavily aged set rusts all over and the first helper holds most of the budget: the dense
# placement takes the pieces the project can still hold, spread over the area, and says so.
assert placed["capped"] and placed["instance_count"] == budget, (placed["instance_count"], budget)
assert "piece budget is reached" in scene.open_painter.status, scene.open_painter.status
manager.remove(placed)

spots_layer = stack.add(ts, "FILL", "Bolt spots")
mask = spots_layer.masks.add()
mask.source = "PAINT"
mask.image = images.create("Spots", 512)
data = np.zeros((512, 512, 4), np.float32)
data[..., 3] = 1
from open_painter.painting.projection import geometry
positions, normals, valid = geometry(ts, 512, coverage=True)
rows, cols = np.nonzero(valid)
rng = np.random.default_rng(1)
yy, xx = np.mgrid[0:512, 0:512]
centres = []
for _ in range(6):
    index = rng.integers(len(rows))
    r = int(rng.integers(6, 12))
    if any(abs(rows[index] - cy) < 40 and abs(cols[index] - cx) < 40 for cy, cx, _ in centres):
        continue
    centres.append((rows[index], cols[index], r))
    data[(yy - rows[index]) ** 2 + (xx - cols[index]) ** 2 <= r * r, :3] = 1
images.write(mask.image, data)
ts.active_layer = stack.index_of(ts, spots_layer.id)
spots_layer = next(l for l in ts.layers if l.name == "Bolt spots")
placement = sources.placement(ts, "BOLT", "SPOTS", spots_layer)
assert len(placement.spots) == len(centres), (len(placement.spots), len(centres))
labels, count = sources.label_runs(data[..., 0] > .5)
assert count == len(centres) and labels.max() == count
for position, normal, diameter, uv in placement.spots:
    row, col = int(uv[1] * 512), int(uv[0] * 512)
    nearest = min(centres, key=lambda c: (c[0] - row) ** 2 + (c[1] - col) ** 2)
    assert abs(nearest[0] - row) <= 2 and abs(nearest[1] - col) <= 2, "one piece at each spot's centre"
    assert np.allclose(position, positions[nearest[0], nearest[1]], atol=.05) and abs(np.linalg.norm(normal) - 1) < 1e-4
bpy.context.view_layer.objects.active = cube
assert bpy.ops.open_painter.geometry_effect(action="BOLT", source="SPOTS", size=1.0) == {"FINISHED"}
bolts = bpy.context.view_layer.objects.active
assert bolts["kind"] == "BOLT" and bolts["instance_count"] == len(centres) and bolts["source"] == "SPOTS" and bolts["source_layer"] == spots_layer.id
scales = attribute(bolts, "op_scale", 3)
diameters = sorted(spot[2] for spot in placement.spots)
assert np.allclose(sorted(scales[:, 0]), diameters, rtol=1e-4), "sized by the spot"
assert scales[:, 0].max() > 1.2 * scales[:, 0].min(), "different spots, different sizes"
# Half the size, twice the spots: the helper follows its layer's mask on regenerate.
bolts["size"] = .5
data[(yy - 30) ** 2 + (xx - 30) ** 2 <= 100, :3] = 1 if valid[30, 30] else 0
images.write(mask.image, data)
regenerated = manager.regenerate(ts, bolts)
assert regenerated["instance_count"] == len(sources.placement(ts, "BOLT", "SPOTS", spots_layer).spots)
assert np.allclose(sorted(attribute(regenerated, "op_scale", 3)[:, 0])[-len(diameters):], [d * .5 for d in diameters], rtol=1e-4)
print("GEOMETRY_SOURCES bolts", regenerated["instance_count"], flush=True)

# ---- the artist's own pieces ------------------------------------------------------------------------
kit = bpy.data.collections.new("Bolt kit")
scene.collection.children.link(kit)
bpy.ops.mesh.primitive_cylinder_add(radius=.5, depth=.25)
own = bpy.context.active_object
for collection in list(own.users_collection):
    collection.objects.unlink(own)
kit.objects.link(own)
bpy.ops.mesh.primitive_cube_add(size=2)
second = bpy.context.active_object
for collection in list(second.users_collection):
    collection.objects.unlink(second)
kit.objects.link(second)
bpy.context.view_layer.objects.active = cube
assert bpy.ops.open_painter.geometry_effect(action="BOLT", source="SPOTS", size=1.0, prototypes="Bolt kit") == {"FINISHED"}
custom = bpy.context.view_layer.objects.active
group = custom.modifiers[0].node_group
instanced = [node.inputs["Object"].default_value for node in group.nodes if node.bl_idname == "GeometryNodeObjectInfo"]
assert {o.name for o in instanced} == {own.name, second.name} and custom["variants"] == 2 and custom["prototypes"] == kit
assert own.get(manager.TAG) is None, "the artist's objects are not owned"
variants = attribute(custom, "op_variant", 1)
scales = attribute(custom, "op_scale", 3)
factors = {0: 1 / max(own.dimensions), 1: 1 / max(second.dimensions)}
expected = {spot[2] for spot in sources.placement(ts, "BOLT", "SPOTS", spots_layer).spots}
for scale, variant in zip(scales[:, 0], variants):
    assert any(abs(scale - d * factors[int(variant)]) < 1e-4 for d in expected), "Size means the same for any prototype"
manager.remove(custom)
assert bpy.data.objects.get(own.name) is not None
bpy.context.view_layer.objects.active = cube
try:
    bpy.ops.open_painter.geometry_effect(action="BOLT", source="SPOTS", prototypes="Nope")
    raise AssertionError("an unknown collection was accepted")
except RuntimeError as error:
    assert "No collection named" in str(error)
manager.remove(regenerated)

# ---- following the material: age changes, pieces catch up ---------------------------------------------
following = manager.helpers_of(ts, following=True)
assert [h["kind"] for h in following] == ["RUST"]
helper_name = following[0].name
ts.aging.age_years = 3
assert (scene.name, ts.id) in manager._pending, "the aging apply schedules a follow"
replaced = manager.follow_now(ts)
assert len(replaced) == 1 and replaced[0]["instance_count"] < rust_count and (scene.name, ts.id) not in manager._pending
assert [h.name for h in manager.helpers_of(ts, following=True)] == [replaced[0].name]
frozen = manager.create(ts, cube, None, "RUST", 120, .03, 9, 0, placement=sources.placement(ts, "RUST", "MATERIAL"), follow=False)
assert frozen is not None and not frozen["follow"]
frozen_name = frozen.name
ts.aging.age_years = 40
manager.follow_now(ts)
assert scene.objects.get(frozen_name) is not None, "a helper that does not follow stays"
bpy.context.view_layer.objects.active = cube
assert bpy.ops.open_painter.geometry_effect(action="REGENERATE_ALL") == {"FINISHED"}
assert "Regenerated 2 helpers" in scene.open_painter.status
for item in manager.helpers_of(ts):
    manager.remove(item)

# ---- without aging, the mesh features stand in ------------------------------------------------------
sets.remove(scene, list(scene.open_painter.texture_sets).index(ts))
plain = sets.create(scene, "Plain steel", 512)
sets.assign(scene, plain, cube)
bake_synthetic(plain)
assert sources.placement(plain, "RUST", "MATERIAL").label == "mesh edges and cavities"
assert sources.placement(plain, "PEEL", "MATERIAL").label == "mesh edges"
assert sources.placement(plain, "MOSS", "MATERIAL").label == "cavities, shade and the ground"
snow = sources.placement(plain, "SNOW", "MATERIAL")
assert snow.label == "upward faces"
snow_helper = manager.create(plain, cube, None, "SNOW", 150, .06, 1, 0, placement=snow)
points = manager.scatter_points(plain, cube, snow.density, 150, 1, snow_helper, "SNOW", .06, slope=False)
assert snow_helper is not None and float(np.mean(points["normals"][:, 2] > .5)) > .9, "snow settles on faces that look up"
manager.remove(snow_helper)
surface = sources.placement(plain, "SCALE", "SURFACE")
assert surface.label == "the whole surface" and float(surface.density.max()) == 1
whole = manager.create(plain, cube, None, "SCALE", 200, .03, 1, 0, placement=surface)
assert whole is not None and whole["source"] == "SURFACE"
manager.remove(whole)
# The pre-0.10.1 call with a published mask image still works and keeps the snapshot.
mask_image = images.create("Everywhere", 64, (1, 1, 1, 1))
legacy = manager.create(plain, cube, mask_image, "RUST", 50, .03, 1, 0)
assert legacy is not None and legacy["mask_image"] == mask_image and legacy["source"] == "MASK"
legacy_count = legacy["instance_count"]
again = manager.regenerate(plain, legacy)      # the helper is replaced; the snapshot regenerates it
assert again["instance_count"] == legacy_count and again["mask_image"] == mask_image
manager.remove(again)
manager.reap_orphans()

open_painter.unregister()
print("GEOMETRY_SOURCES_PASS", flush=True)
