"""Stock-Blender catalog integration: recipes, maps, rollback, pixels and persistence."""
import hashlib
import json
import sys
from pathlib import Path

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import open_painter
from open_painter.core import image_manager as images, layer_stack as stack, texture_set_manager as sets
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.export.surface_normal import combined as combined_normal
from open_painter.smart_materials import composer, generated_sources
from open_painter.smart_materials.catalog import CATALOG


def authored_height_state(texture_set):
    result = []
    for item in texture_set.layers:
        channel = stack.channel_for(item, "HEIGHT")
        result.append((item.id, channel.enabled, channel.value, channel.procedural,
                       channel.procedural_height, channel.projection, tuple(channel.scale),
                       channel.detail_image.get("open_painter_generated_source") if channel.detail_image else None,
                       [(effect.kind, effect.enabled, effect.output_black, effect.output_white)
                        for effect in channel.effects]))
    return result


open_painter.register()
scene = bpy.context.scene
ts = sets.create(scene, "Catalog", 512)
# Triplanar sources need real assigned geometry; an unassigned staging texture
# intentionally samples a neutral point and cannot establish relief quality.
bpy.ops.mesh.primitive_plane_add(size=2)
sets.assign(scene, ts, bpy.context.object)
bpy.context.view_layer.update()
sentinel = stack.add(ts, "FILL", "User layer")
stack.channel_for(sentinel, "BASE_COLOR").color = (.12, .23, .34, 1)
sentinel_id = sentinel.id
before = (len(ts.layers), ts.active_layer, ts.edit_serial, len(bpy.data.images))
try:
    composer.apply(ts, "Worn Painted Metal")
    raise AssertionError("A mesh-aware recipe accepted missing maps")
except composer.MissingMeshMaps as error:
    assert set(error.missing) == {"CURVATURE", "AO"}
assert before == (len(ts.layers), ts.active_layer, ts.edit_serial, len(bpy.data.images))

# Synthetic baked fields exercise generator sampling and channel composition;
# geometric correctness of the mesh bakes is covered by the mesh-map suite.
u, v = np.meshgrid((np.arange(32) + .5) / 32, (np.arange(32) + .5) / 32)
for kind in ("CURVATURE", "AO", "POSITION", "WORLD_NORMAL"):
    entry = ts.baked_maps.add()
    entry.kind = kind
    entry.image = images.create("Catalog " + kind, 32)
    value = np.ones((32, 32, 4), np.float32)
    if kind == "CURVATURE":
        edge = np.clip((.16 - np.abs(u - .5)) * 8, 0, 1)
        value[..., :3] = edge[..., None]
    elif kind == "AO":
        value[..., :3] = (.25 + .75 * v)[..., None]
    elif kind == "POSITION":
        value[..., 0], value[..., 1], value[..., 2] = u, v, v
    else:
        # Unit world normals spanning -Z to +Z, encoded as RGB.
        z = 2 * v - 1
        value[..., 0] = .5 + .5 * np.sqrt(np.maximum(0, 1 - z * z))
        value[..., 1], value[..., 2] = .5, v
    images.write(entry.image, value)

# A mid-composition failure must remove only the new subtree and restore state.
before = composer._state(ts)
original_fill = composer._fill
calls = 0


def failing_fill(*args, **kwargs):
    global calls
    calls += 1
    value = original_fill(*args, **kwargs)
    if calls == 2:
        raise RuntimeError("Injected authoring failure")
    return value


composer._fill = failing_fill
try:
    try:
        composer.apply(ts, "Old Brass")
        raise AssertionError("Injected failure did not run")
    except RuntimeError as error:
        assert str(error) == "Injected authoring failure"
finally:
    composer._fill = original_fill
assert composer._state(ts) == before
assert ts.layers[0].id == sentinel_id
np.testing.assert_allclose(stack.channel_for(ts.layers[0], "BASE_COLOR").color, (.12, .23, .34, 1))

original_catalog = json.loads((ROOT / "tests/fixtures/catalog_v025.json").read_text())
assert len(CATALOG) == 50 and CATALOG.keys() == original_catalog.keys()
hashes = {}
height_hashes = {}
relief = {}
for name, data in CATALOG.items():
    composer.validate(data)
    ts.layers.clear()
    ts.active_layer = 0
    node_count = len(ts.material.node_tree.nodes)
    folder = composer.apply(ts, name)
    folder_id = folder.id
    assert folder.type == "FOLDER" and ts.layers[ts.active_layer].id == folder_id
    assert len(ts.material.node_tree.nodes) == node_count, "Recipe authored shader effects"
    children = [item for item in ts.layers if item.parent_id == folder_id]
    assert [item.name for item in children] == [item["name"] for item in data["layers"]]
    assert len(children) == len(data["layers"]) and not children[0].masks
    for child, spec in zip(children, data["layers"]):
        height = stack.channel_for(child, "HEIGHT")
        selected = spec.get("channels", ("BASE_COLOR", "ROUGHNESS", "METALLIC", "HEIGHT"))
        assert height.enabled == ("HEIGHT" in selected and any(
            key in spec for key in ("height", "height_value", "height_range")))
        if height.enabled:
            if "height_range" in spec:
                assert height.procedural_height == 1
                assert len(height.effects) == 1 and height.effects[0].kind == "LEVELS"
                np.testing.assert_allclose((height.effects[0].output_black, height.effects[0].output_white),
                                           spec["height_range"], atol=1e-7)
            elif "height_value" in spec:
                assert not height.procedural and not height.detail_image
                assert abs(height.value - spec["height_value"]) < 1e-6
            else:
                assert abs(height.procedural_height - spec.get("generated_source", {}).get("height", spec["height"])) < 1e-6
        for channel in child.channels:
            assert channel.projection == spec.get("projection", "UV")
            assert abs(channel.triplanar_hardness - spec.get("triplanar_hardness", 4)) < 1e-6
        assert len(child.masks) == len(composer.masks_for(spec))
        for index, (mask, settings) in enumerate(zip(child.masks, composer.masks_for(spec))):
            if settings.get("procedural"):
                assert mask.source == "PROCEDURAL"
            elif settings.get("generated_source"):
                source_id = settings["generated_source"]["id"]
                assert source_id in generated_sources.SOURCE_IDS
                assert mask.source == "FILL" and mask.image.packed_file
                assert mask.image.get("open_painter_generated_source") == source_id
                assert mask.image.colorspace_settings.name == "Non-Color"
            else:
                assert mask.generator == settings["kind"]
            assert mask.invert == settings.get("invert", False)
            assert mask.operation == settings.get("operation", "NORMAL" if index == 0 else "MULTIPLY")
            assert mask.projection == settings.get("projection", "UV")
    evaluator = LayerEvaluator(scene)
    image = evaluator.evaluate_channel(ts.id, "BASE_COLOR")
    values = images.pixels(image)
    assert np.isfinite(values).all() and values[..., :3].std() > 1e-5, name
    digest = hashlib.sha256(np.round(values, 5).tobytes()).hexdigest()
    assert digest not in hashes, (name, hashes.get(digest))
    hashes[digest] = name
    heights = images.pixels(evaluator.evaluate_channel(ts.id, "HEIGHT"))
    scalar = heights[..., 0]
    assert np.isfinite(heights).all() and scalar.min() >= 0 and scalar.max() <= 1, name
    assert scalar.std() > 1e-6 and np.ptp(scalar) > 1e-5 and ts.height_distance > 0, \
        (name, "Material has no usable height relief")
    height_digest = hashlib.sha256(np.round(scalar, 6).tobytes()).hexdigest()
    assert height_digest not in height_hashes, (name, "Duplicate height", height_hashes.get(height_digest))
    height_hashes[height_digest] = name
    normals = images.pixels(evaluator.evaluate_channel(ts.id, "NORMAL"))
    surface = combined_normal(ts, normals, heights)
    vectors = surface[..., :3] * 2 - 1
    assert np.isfinite(surface).all()
    np.testing.assert_allclose(np.linalg.norm(vectors, axis=-1), 1, atol=2e-5)
    assert np.max(np.abs(vectors[..., :2])) > 1e-5, (name, "Combined normal is flat")
    relief[name] = {"height_min": float(scalar.min()), "height_max": float(scalar.max()),
                    "height_std": float(scalar.std()), "height_distance": float(ts.height_distance),
                    "world_height_span": float(np.ptp(scalar) * ts.height_distance),
                    "combined_normal_xy_rms": float(np.sqrt(np.mean(vectors[..., :2] ** 2)))}
    print("CATALOG_RECIPE_PASS", name, flush=True)

# Effects are single editable layers. Channel-only scratches retain base color.
# The bevel effects read Cycles-baked bevel maps; synthetic ones stand in here.
from open_painter.baking.bake_manager import register_map
for kind, color in (("BEVEL_MASK", (0, 0, 0, 1)), ("BEVEL_NORMAL", (.5, .5, 1, 1))):
    if not any(m.kind == kind and m.image for m in ts.baked_maps):
        register_map(ts, kind, images.create("synthetic " + kind, 32, color))
for name, spec in composer.EFFECTS.items():
    ts.layers.clear()
    ts.active_layer = 0
    base = stack.add(ts, "FILL", "Base")
    stack.channel_for(base, "BASE_COLOR").color = (.12, .23, .34, 1)
    effect = composer.apply_effect(ts, name)
    assert len(ts.layers) == 2 and effect.type == "FILL"
    assert len(effect.masks) == (len(composer.masks_for(spec)) if name == "Damaged Bevel" else 1 if spec.get("mask") else 0)
    if "channels" in spec:
        assert not stack.channel_for(effect, "BASE_COLOR").enabled
        assert not stack.channel_for(effect, "METALLIC").enabled

ts.layers.clear()
ts.active_layer = 0
folder = composer.apply(ts, "Worn Painted Metal")
folder_id = folder.id
polish = composer.apply_effect(ts, "Edge Polish")
assert polish.parent_id == folder_id
children = [item for item in ts.layers if item.parent_id == folder_id]
assert children[-1].id == polish.id, "New folder effects must composite above its substrate"
evaluator = LayerEvaluator(scene)
evaluator.evaluate_texture_set(ts.id, force=True)
expected = {out.channel: images.pixels(out.image).copy() for out in ts.outputs}
expected_height_state = authored_height_state(ts)
packed_inputs = {image.get("open_painter_generated_source"):
                 hashlib.sha256(bytes(image.packed_file.data)).hexdigest()
                 for image in bpy.data.images if image.get("open_painter_generated_source") and image.packed_file}
path = ROOT / ".blender-test" / "catalog-materials.blend"
path.parent.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=str(path))
bpy.ops.wm.open_mainfile(filepath=str(path))
ts = bpy.context.scene.open_painter.texture_sets[0]
for item in ts.layers:
    for image in [channel.detail_image for channel in item.channels] + [mask.image for mask in item.masks]:
        if image and image.get("open_painter_generated_source"):
            identifier = image.get("open_painter_generated_source")
            assert image.packed_file and image.colorspace_settings.name == "Non-Color"
            assert hashlib.sha256(bytes(image.packed_file.data)).hexdigest() == packed_inputs[identifier]
assert authored_height_state(ts) == expected_height_state, \
    "Save/reopen changed authored relief values, elevated coating ranges or source bindings"
LayerEvaluator(bpy.context.scene).evaluate_texture_set(ts.id, force=True)
for out in ts.outputs:
    np.testing.assert_allclose(images.pixels(out.image), expected[out.channel], atol=2e-6)
assert not any(node.bl_idname in {"ShaderNodeTexNoise", "ShaderNodeTexVoronoi", "ShaderNodeValToRGB"}
               for node in ts.material.node_tree.nodes)
report = {"recipes": len(hashes), "layers": sum(len(data["layers"]) for data in CATALOG.values()),
          "effects": len(composer.EFFECTS), "pixel_fingerprints": len(hashes),
          "height_fingerprints": len(height_hashes), "relief": relief,
          "missing_map_preflight": "PASS", "rollback": "PASS", "save_reopen": "PASS"}
(ROOT / ".blender-test" / "catalog-materials.json").write_text(json.dumps(report, indent=2))
open_painter.unregister()
print("CATALOG_MATERIALS_PASS", json.dumps(report), flush=True)
