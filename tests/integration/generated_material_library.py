"""Generated detail integrity, shared inputs, editable channels and rollback.

Run in isolated Blender user resources. Border metrics describe the actual
source pixels; this test does not turn a numeric threshold into a claim of
seamlessness or photorealism.
"""

import hashlib
import json
import struct
import sys
from pathlib import Path
from unittest.mock import patch

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import open_painter
from open_painter.core import image_manager as images, layer_stack as stack, texture_set_manager as sets
from open_painter.evaluation import channel_eval
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.smart_materials import composer, generated_sources
from open_painter.smart_materials.catalog import CATALOG, layer, surface_detail
from open_painter.texture_engine import procedurals


def pixel_digest(image):
    return hashlib.sha256(images.pixels(image).tobytes()).hexdigest()


def detail_spec(name, identifier):
    return layer(name, (.18, .32, .46), .37, .8,
                 procedural="FBM", scale=7.25, detail=.2, height=.024,
                 generated_source=surface_detail(identifier, color=.12,
                                                 roughness=.08, height=.024))


def node_signature(texture_set):
    tree = texture_set.material.node_tree
    return (tuple((node.name, node.bl_idname) for node in tree.nodes),
            tuple((link.from_node.name, link.from_socket.name,
                   link.to_node.name, link.to_socket.name) for link in tree.links))


open_painter.register()
scene = bpy.context.scene
report = {"authored_resolution": 512, "assets": [], "checks": {}}
original_catalog = json.loads((ROOT / "tests/fixtures/catalog_v025.json").read_text())
assert len(CATALOG) == 50 and CATALOG.keys() == original_catalog.keys(), "Keep all fifty material identities"
assert all(recipe["layers"] for recipe in CATALOG.values())
assert all(any(settings.get("generated_source") for spec in recipe["layers"]
               for settings in (spec, *composer.masks_for(spec)))
           for recipe in CATALOG.values()), "Every existing catalog material needs a suitable generated source"
catalog_sources = set()
for recipe in CATALOG.values():
    composer.validate(recipe)
    for spec in recipe["layers"]:
        for settings in [spec, *composer.masks_for(spec)]:
            if settings.get("generated_source"):
                generated_sources.validate(settings["generated_source"])
                catalog_sources.add(settings["generated_source"]["id"])
assert catalog_sources <= generated_sources.SOURCE_IDS
report["catalog_source_ids"] = sorted(catalog_sources)
report["retained_library_source_ids"] = sorted(generated_sources.SOURCE_IDS - catalog_sources)
report["generated_materials"] = len(CATALOG)
report["checks"]["all_fifty_existing_materials_use_generated_sources"] = "PASS"

# Failure after source loading must retain the user's existing image and layer,
# while removing a source that was allocated only for the failed composition.
rollback_ts = sets.create(scene, "Generated source rollback", 512)
sentinel = stack.add(rollback_ts, "FILL", "Existing user layer")
sentinel_id = sentinel.id
existing = generated_sources.load("brushed_metal")
stack.channel_for(sentinel, "BASE_COLOR").detail_image = existing
stack.channel_for(sentinel, "BASE_COLOR").color = (.11, .22, .33, 1)
existing_pointer, existing_digest = existing.as_pointer(), pixel_digest(existing)
before_state = composer._state(rollback_ts)
before_images = {image.as_pointer() for image in bpy.data.images}
before_nodes = node_signature(rollback_ts)
original_fill = composer._fill
calls = 0


def failing_fill(*args, **kwargs):
    global calls
    calls += 1
    result = original_fill(*args, **kwargs)
    if calls == 2:
        raise RuntimeError("Injected generated source authoring failure")
    return result


failed_document = {"layers": [detail_spec("New detail", "concrete_pores"),
                               detail_spec("Shared detail", "brushed_metal")]}
with patch.object(composer, "_fill", side_effect=failing_fill):
    try:
        composer.apply_document(rollback_ts, failed_document, "Failed material")
        raise AssertionError("Injected failure did not occur")
    except RuntimeError as error:
        assert str(error) == "Injected generated source authoring failure"
assert calls == 2
assert composer._state(rollback_ts) == before_state
assert {image.as_pointer() for image in bpy.data.images} == before_images, "Failed application leaked a source image"
assert node_signature(rollback_ts) == before_nodes
assert rollback_ts.layers[0].id == sentinel_id
assert stack.channel_for(rollback_ts.layers[0], "BASE_COLOR").detail_image.as_pointer() == existing_pointer
np.testing.assert_allclose(stack.channel_for(rollback_ts.layers[0], "BASE_COLOR").color,
                           (.11, .22, .33, 1), atol=1e-7)
assert pixel_digest(existing) == existing_digest
report["checks"]["failed_composition_preserves_user_state_and_discards_new_sources"] = "PASS"

# Verify the actual shipped files against their provenance, including the PNG
# header and packed payload. Source resolution is separate from authored output.
manifest = json.loads((generated_sources.ROOT / "provenance.json").read_text())
records = manifest["assets"]
assert len(records) == len(generated_sources.SOURCE_IDS)
assert {record["id"] for record in records} == generated_sources.SOURCE_IDS
loaded = {}
source_state = {}
for record in records:
    identifier = record["id"]
    path = generated_sources.ROOT / record["file"]
    assert path.parent == generated_sources.ROOT and path.name == identifier + ".png"
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR"
    width, height, bit_depth, color_type = struct.unpack(">IIBB", data[16:26])
    assert (width, height) == (record["width"], record["height"])
    assert bit_depth == record["bit_depth"] == 8
    assert color_type == 2 and record["color_type"] == "RGB"
    assert len(data) == record["bytes"]
    assert hashlib.sha256(data).hexdigest() == record["sha256"]
    image = generated_sources.load(identifier)
    assert image.colorspace_settings.name == "Non-Color"
    assert image.packed_file is not None and not image.is_float
    assert tuple(image.size) == (width, height)
    assert hashlib.sha256(bytes(image.packed_file.data)).hexdigest() == record["sha256"]
    assert generated_sources.load(identifier).as_pointer() == image.as_pointer()
    rgba = images.pixels(image)
    assert np.isfinite(rgba).all()
    np.testing.assert_allclose(rgba[..., 3], 1, atol=1e-7)
    scalar = rgba[..., :3] @ np.array((.2126, .7152, .0722), np.float32)
    # Adjacent-pixel differences supply texture context for boundary differences.
    # Both values are reported even if a seam is appreciable; no seam pass gate.
    border = {
        "left_right_mean_abs": float(np.mean(np.abs(scalar[:, 0] - scalar[:, -1]))),
        "left_right_max_abs": float(np.max(np.abs(scalar[:, 0] - scalar[:, -1]))),
        "top_bottom_mean_abs": float(np.mean(np.abs(scalar[0] - scalar[-1]))),
        "top_bottom_max_abs": float(np.max(np.abs(scalar[0] - scalar[-1]))),
        "interior_horizontal_mean_abs": float(np.mean(np.abs(np.diff(scalar, axis=1)))),
        "interior_vertical_mean_abs": float(np.mean(np.abs(np.diff(scalar, axis=0)))),
        "scalar_mean": float(scalar.mean()), "scalar_std": float(scalar.std()),
    }
    report["assets"].append({"id": identifier, "width": width, "height": height,
                             "bytes": len(data), "sha256": record["sha256"],
                             "border_metrics": border})
    loaded[identifier] = image
    source_state[identifier] = (image.as_pointer(), image.get("open_painter_pixel_revision", 0),
                                hashlib.sha256(rgba.tobytes()).hexdigest(),
                                path.stat().st_mtime_ns, record["sha256"])
    del rgba, scalar, data
report["checks"]["bundled_png_files_match_provenance_and_packed_payloads"] = "PASS"

# Reusing a detail in another layer, channel or texture set must not allocate a
# recolored or roughness-specific image. Only channel amplitudes are independent.
shared_document = {"layers": [detail_spec(identifier + " " + str(repeat), identifier)
                                for identifier in sorted(loaded) for repeat in range(2)]}
shared_sets = [rollback_ts, sets.create(scene, "Generated source sharing", 512)]
for texture_set in shared_sets:
    initial_nodes = node_signature(texture_set)
    folder = composer.apply_document(texture_set, shared_document, "Shared input material")
    children = [item for item in texture_set.layers if item.parent_id == folder.id]
    assert len(children) == len(loaded) * 2
    for child, spec in zip(children, shared_document["layers"]):
        expected = loaded[spec["generated_source"]["id"]].as_pointer()
        for channel in ("BASE_COLOR", "ROUGHNESS", "HEIGHT"):
            prop = stack.channel_for(child, channel)
            assert prop.detail_image.as_pointer() == expected
            assert prop.image is None, "A channel-specific baked texture replaced the editable input"
        assert stack.channel_for(child, "METALLIC").detail_image is None
    assert node_signature(texture_set) == initial_nodes
assert len([image for image in bpy.data.images if image.get("open_painter_generated_source")]) == len(loaded)
report["checks"]["shared_inputs_across_layers_channels_and_texture_sets"] = "PASS"

# Native color and roughness edits must affect authoritative 512-pixel output,
# leave source files/pixels alone, and reuse the already decoded detail image.
edit_ts = sets.create(scene, "Editable generated material", 512)
initial_nodes = node_signature(edit_ts)
folder = composer.apply_document(edit_ts, {"layers": [detail_spec("Editable grain", "fine_wood")]},
                                 "Editable generated material")
child = next(item for item in edit_ts.layers if item.parent_id == folder.id)
base = stack.channel_for(child, "BASE_COLOR")
rough = stack.channel_for(child, "ROUGHNESS")
evaluator = LayerEvaluator(scene)
base_before = images.pixels(evaluator.evaluate_channel(edit_ts.id, "BASE_COLOR"))
rough_before = images.pixels(evaluator.evaluate_channel(edit_ts.id, "ROUGHNESS"))
old_color = np.array(base.color[:3], np.float32)
old_roughness = rough.value
base.color = (.36, .16, .23, 1)
rough.value = .61
source_pointers = {image.as_pointer() for image in loaded.values()}
with patch.object(images, "pixels", wraps=images.pixels) as reads, \
        patch.object(images, "write", wraps=images.write) as writes:
    base_image = evaluator.evaluate_channel(edit_ts.id, "BASE_COLOR")
    rough_image = evaluator.evaluate_channel(edit_ts.id, "ROUGHNESS")
    assert not any(call.args[0].as_pointer() in source_pointers for call in reads.call_args_list), \
        "A material-only parameter edit decoded the shared source again"
    assert not any(call.args[0].as_pointer() in source_pointers for call in writes.call_args_list), \
        "A material-only parameter edit rewrote the shared source"
base_after, rough_after = images.pixels(base_image), images.pixels(rough_image)
assert int(edit_ts.resolution) == 512
assert tuple(base_image.size) == tuple(rough_image.size) == (512, 512)
assert base_after.shape == rough_after.shape == (512, 512, 4)
np.testing.assert_allclose(base_after[..., :3],
                           base_before[..., :3] * (np.array(base.color[:3]) / old_color), atol=2e-6)
np.testing.assert_allclose(rough_after[..., :3] - rough_before[..., :3],
                           rough.value - old_roughness, atol=2e-6)
assert np.max(np.abs(base_after - base_before)) > .1
assert np.max(np.abs(rough_after - rough_before)) > .2
assert node_signature(edit_ts) == initial_nodes
for identifier, image in loaded.items():
    pointer, revision, digest, mtime, file_digest = source_state[identifier]
    path = generated_sources.ROOT / (identifier + ".png")
    assert image.as_pointer() == pointer
    assert image.get("open_painter_pixel_revision", 0) == revision
    assert pixel_digest(image) == digest
    assert path.stat().st_mtime_ns == mtime
    assert hashlib.sha256(path.read_bytes()).hexdigest() == file_digest
report["checks"]["native_parameter_edits_change_full_resolution_output_without_source_writes"] = "PASS"
report["checks"]["native_parameter_edits_reuse_decoded_source"] = "PASS"
report["checks"]["fixed_display_material_nodes_and_links"] = "PASS"

# Removing an optional detail input returns to the authored procedural. Old
# files with no detail field receive precisely this default path on RNA load.
base.detail_image = None
assert not base.detail_image_name and base.procedural == "FBM"
n = int(edit_ts.resolution)
u, v = np.meshgrid((np.arange(n, dtype=np.float32) + .5) / n,
                   (np.arange(n, dtype=np.float32) + .5) / n)
field = procedurals.evaluate(base.procedural, u, v, seed=base.procedural_seed,
                             scale=base.procedural_scale)
expected = np.array(base.color[:3]) * (1 + (field - .5) * base.procedural_detail)[..., None]
actual = channel_eval.source(base, "FILL", n, 0, n, {}, edit_ts, {})
np.testing.assert_allclose(actual[..., :3], expected, atol=2e-7)
np.testing.assert_allclose(actual[..., 3], 1, atol=1e-7)
assert np.mean(np.abs(actual[..., :3] - base_after[..., :3])) > 1e-4
report["checks"]["optional_image_removal_restores_existing_procedural"] = "PASS"

report_path = ROOT / ".blender-test" / "generated-material-assets.json"
report_path.parent.mkdir(parents=True, exist_ok=True)
report_path.write_text(json.dumps(report, indent=2) + "\n")
open_painter.unregister()
print("GENERATED_MATERIAL_LIBRARY_PASS", json.dumps(report["checks"]), flush=True)
