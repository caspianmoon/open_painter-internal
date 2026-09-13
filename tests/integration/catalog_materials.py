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
from open_painter.smart_materials import composer
from open_painter.smart_materials.catalog import CATALOG

open_painter.register()
scene = bpy.context.scene
ts = sets.create(scene, "Catalog", 512)
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

assert len(CATALOG) == 50
hashes = {}
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
        assert height.enabled == ("height" in spec)
        if height.enabled:
            assert abs(height.procedural_height - spec["height"]) < 1e-6
        if spec.get("mask"):
            assert child.masks[0].generator == spec["mask"]["kind"]
            assert child.masks[0].invert == spec["mask"].get("invert", False)
        assert len(child.masks) == len(composer.masks_for(spec))
        for extra in list(child.masks)[1:]:
            assert extra.operation == "MULTIPLY", "Coating breakup must preserve edge losses"
    image = LayerEvaluator(scene).evaluate_channel(ts.id, "BASE_COLOR")
    values = images.pixels(image)
    assert np.isfinite(values).all() and values[..., :3].std() > 1e-5, name
    digest = hashlib.sha256(np.round(values, 5).tobytes()).hexdigest()
    assert digest not in hashes, (name, hashes.get(digest))
    hashes[digest] = name
    print("CATALOG_RECIPE_PASS", name, flush=True)

# Effects are single editable layers. Channel-only scratches retain base color.
for name, spec in composer.EFFECTS.items():
    ts.layers.clear()
    ts.active_layer = 0
    base = stack.add(ts, "FILL", "Base")
    stack.channel_for(base, "BASE_COLOR").color = (.12, .23, .34, 1)
    effect = composer.apply_effect(ts, name)
    assert len(ts.layers) == 2 and effect.type == "FILL" and len(effect.masks) == 1
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
path = ROOT / ".blender-test" / "catalog-materials.blend"
path.parent.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=str(path))
bpy.ops.wm.open_mainfile(filepath=str(path))
ts = bpy.context.scene.open_painter.texture_sets[0]
assert any(stack.channel_for(item, "HEIGHT").procedural_height < 0 for item in ts.layers)
LayerEvaluator(bpy.context.scene).evaluate_texture_set(ts.id, force=True)
for out in ts.outputs:
    np.testing.assert_allclose(images.pixels(out.image), expected[out.channel], atol=2e-6)
assert not any(node.bl_idname in {"ShaderNodeTexNoise", "ShaderNodeTexVoronoi", "ShaderNodeValToRGB"}
               for node in ts.material.node_tree.nodes)
report = {"recipes": len(hashes), "layers": sum(len(data["layers"]) for data in CATALOG.values()),
          "effects": len(composer.EFFECTS), "pixel_fingerprints": len(hashes),
          "missing_map_preflight": "PASS", "rollback": "PASS", "save_reopen": "PASS"}
(ROOT / ".blender-test" / "catalog-materials.json").write_text(json.dumps(report, indent=2))
open_painter.unregister()
print("CATALOG_MATERIALS_PASS", json.dumps(report), flush=True)
