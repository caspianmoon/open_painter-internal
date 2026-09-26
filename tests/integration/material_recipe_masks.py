"""Recipe masks use real source coverage, shared chip seeds and editable values."""

import copy
import sys
from pathlib import Path

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import open_painter
from open_painter.core import image_manager as images, layer_stack as stack, texture_set_manager as sets
from open_painter.evaluation import channel_eval, mask_eval, metrics
from open_painter.smart_materials import composer
from open_painter.smart_materials.catalog import layer, mask, surface_detail
from open_painter.texture_engine import procedurals


open_painter.register()
scene = bpy.context.scene
ts = sets.create(scene, "Source mask regression", 512)
base = stack.add(ts, "FILL", "Existing artist layer")
base_id = base.id

# Explicit paint-loss seeds and identical source coordinates align primer and
# paint boundaries, independent of their layer names, order or material seed.
shared = mask("CHIPS", scale=5, procedural="VORONOI", seed=79,
              uv_scale=(1.25, .75), black=.2, white=.8)
document = {"layers": [
    layer("Primer", (.22, .21, .19), .67, mask=shared,
          channels=("ROUGHNESS", "HEIGHT"), opacity=.4, height_value=.48,
          procedural="FBM", height=.8,
          generated_source=surface_detail("fine_wood", height=.7)),
    layer("Paint", (.26, .08, .03), .4, mask=copy.deepcopy(shared)),
    layer("Wood pores", (.08, .04, .02), .7,
          mask=mask("CHIPS", generated_source={"id": "fine_wood", "scale": (2, 3)},
                    black=.25, white=.7, invert=True),
          mask_effects=(mask("NOISE", scale=3, procedural="FBM", operation="ADD",
                             seed=23, strength=.25),)),
]}
assert composer.requirements(document) == set(), "Texture source masks must not require curvature"
folder = composer.apply_document(ts, document, "Editable source masks", seed=997)
folder_id = folder.id
children = [item for item in ts.layers if item.parent_id == folder_id]
primer, paint, pores = children
np.testing.assert_allclose(primer.opacity, .4, atol=1e-7)
assert not stack.channel_for(primer, "BASE_COLOR").enabled
assert not stack.channel_for(primer, "METALLIC").enabled
height = stack.channel_for(primer, "HEIGHT")
assert height.enabled and height.procedural == "" and not height.detail_image
np.testing.assert_allclose(height.value, .48, atol=1e-7)
# No second field: the channel itself remains a constant depth while its mask
# defines the exact footprint. This also covers height_value without height.
plain = copy.deepcopy(document["layers"][0])
plain.pop("height")
constant_height = channel_eval.source(height, "FILL", 64, 0, 64, {}, ts, {})
np.testing.assert_allclose(constant_height[..., :3], .48, atol=1e-7)
first = mask_eval.evaluate_masks(ts, primer, 64, 0, 64, {}, {})
second = mask_eval.evaluate_masks(ts, paint, 64, 0, 64, {}, {})
np.testing.assert_array_equal(first, second)
assert first.std() > .05 and primer.masks[0].seed == paint.masks[0].seed == 79
np.testing.assert_allclose(primer.masks[0].scale, (1.25, .75), atol=1e-7)

# Image masks reference the same packed source as channel detail, rather than
# saving per-layer copies. Levels and inversion are applied to source pixels.
assert pores.masks[0].source == "FILL" and pores.masks[0].image.packed_file
assert pores.masks[0].image == stack.channel_for(primer, "ROUGHNESS").detail_image
np.testing.assert_allclose(pores.masks[0].scale, (2, 3))
assert pores.masks[1].source == "PROCEDURAL" and pores.masks[1].operation == "ADD"
source = channel_eval.source(pores.masks[0], "FILL", 64, 0, 64, {}, ts, {})[..., 0]
expected = 1 - np.clip((source - .25) / (.7 - .25), 0, 1)
u, v = np.meshgrid((np.arange(64, dtype=np.float32) + .5) / 64,
                   (np.arange(64, dtype=np.float32) + .5) / 64)
expected = np.clip(expected + procedurals.evaluate("FBM", u, v, scale=3, seed=23) * .25, 0, 1)
actual = mask_eval.evaluate_masks(ts, pores, 64, 0, 64, {}, {})
np.testing.assert_allclose(actual, expected, atol=5e-7)

# Packed library masks retain their sampled result after a material-only edit.
# Managed image writes must still invalidate that result and consuming outputs.
resources = {}
mask_eval.evaluate_masks(ts, pores, 64, 0, 64, {}, resources)
stack.channel_for(pores, "ROUGHNESS").value = .43
with metrics.Metrics() as warm:
    same = mask_eval.evaluate_masks(ts, pores, 64, 0, 64, {}, resources)
assert warm.phases["masking"]["calls"] == 0
assert warm.phases["image_read"]["calls"] == 0
np.testing.assert_array_equal(same, actual)
source_image = pores.masks[0].image
original_pixels = images.pixels(source_image)
for output in ts.outputs:
    output.dirty = False
before_serial = ts.edit_serial
try:
    revised_pixels = original_pixels.copy()
    revised_pixels[..., :3] = .35
    images.write(source_image, revised_pixels, pack=False)
    consuming = {prop.channel for prop in pores.channels if prop.enabled}
    assert ts.edit_serial > before_serial and consuming and all(
        output.dirty == (output.channel in consuming) for output in ts.outputs)
    with metrics.Metrics() as refreshed:
        changed = mask_eval.evaluate_masks(ts, pores, 64, 0, 64, {}, resources)
    assert refreshed.phases["masking"]["calls"] == 1
    assert refreshed.phases["image_read"]["calls"] >= 1
    assert np.max(np.abs(changed - same)) > .05
finally:
    images.write(source_image, original_pixels, pack=False)

# Unmanaged external fills retain their conservative authoring-serial identity.
external = images.create("External editable fill", 32)
pores.masks[0].image = external
first_signature = mask_eval._stack_signature(ts, pores, {})
stack.channel_for(pores, "ROUGHNESS").value = .44
assert mask_eval._stack_signature(ts, pores, {}) != first_signature
pores.masks[0].image = source_image

# Adding layers may reallocate Blender's RNA collection: finish checks of the
# existing handles first, and use stable IDs for later authoring operations.
constant_layer = composer._fill(ts, plain, folder_id, 42)
constant_prop = stack.channel_for(constant_layer, "HEIGHT")
assert constant_prop.enabled and not constant_prop.procedural and not constant_prop.detail_image
np.testing.assert_allclose(constant_prop.value, .48, atol=1e-7)

# Defaults on pre-existing recipe payloads retain the old generator behavior.
legacy = layer("Legacy generator", (.2, .2, .2), .5,
               mask=mask("EDGE_WEAR", .2, 40),
               mask_effects=(mask("NOISE", .3, 21),))
assert composer.requirements({"layers": [legacy]}) == {"CURVATURE"}
legacy_layer = composer._fill(ts, legacy, folder_id, 17)
assert all(entry.source == "GENERATOR" for entry in legacy_layer.masks)
assert [entry.operation for entry in legacy_layer.masks] == ["NORMAL", "MULTIPLY"]
assert [entry.seed for entry in legacy_layer.masks] == [17, 17 + 104729]
np.testing.assert_allclose(legacy_layer.masks[0].noise_strength, .35, atol=1e-7)

# Malformed fields fail before layer, selection, image or shader allocation.
invalid_fields = [
    ("opacity", float("nan")), ("height_value", 2),
    ("mask", mask("NOISE", procedural="not_a_procedural")),
    ("mask", mask("NOISE", generated_source={"id": "not_an_image"})),
    ("mask", mask("NOISE", procedural="FBM", generated_source={"id": "fine_wood"})),
    ("mask", mask("NOISE", black=.7, white=.2)),
    ("mask", mask("NOISE", seed=1.2)),
    ("mask", mask("NOISE", strength=float("nan"))),
    ("mask", mask("NOISE", noise_strength=-1)),
    ("mask", mask("NOISE", operation="bad")),
    ("mask", mask("NOISE", uv_scale=(1, 0))),
]
before = composer._state(ts)
before_images = {image.as_pointer() for image in bpy.data.images}
before_nodes = len(ts.material.node_tree.nodes)
for key, value in invalid_fields:
    bad = layer("Invalid", (.2, .3, .4), .5)
    bad[key] = value
    try:
        composer.apply_document(ts, {"layers": [bad]}, "Invalid document")
        raise AssertionError((key, value))
    except ValueError:
        pass
    assert composer._state(ts) == before
    assert {image.as_pointer() for image in bpy.data.images} == before_images
    assert len(ts.material.node_tree.nodes) == before_nodes
assert ts.layers[0].id == base_id

print("MATERIAL_RECIPE_MASKS_PASS: source coverage, shared seeds, constant depth, packed input reuse, legacy defaults, atomic validation")
open_painter.unregister()
