"""Shared scalar detail survives material export, duplication and blend reload."""

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
import open_painter
from open_painter.analysis.analyzer import report
from open_painter.core import image_manager as images, layer_stack as stack, texture_set_manager as sets
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.smart_materials import loader, serializer


open_painter.register()
scene = bpy.context.scene
ts = sets.create(scene, "Portable generated detail", 512)
source = images.create("Shared portable scalar source", 32)
values = np.ones((32, 32, 4), np.float32)
u, v = np.meshgrid(np.arange(32) / 32, np.arange(32) / 32)
values[..., :3] = (.5 + .2 * np.sin(u * 2 * np.pi) * np.cos(v * 4 * np.pi))[..., None]
images.write(source, values)
source.colorspace_settings.name = "Non-Color"
source.pack()
layer = stack.add(ts, "FILL", "Editable source")
for channel in layer.channels:
    channel.enabled = channel.channel in {"BASE_COLOR", "ROUGHNESS", "HEIGHT"}
    if channel.enabled:
        channel.detail_image = source
        channel.procedural_detail = .3
        channel.procedural_height = .04
        channel.scale = (2, 1)
stack.channel_for(layer, "BASE_COLOR").color = (.22, .11, .035, 1)
stack.channel_for(layer, "ROUGHNESS").value = .37
duplicate = stack.duplicate(ts, 0)
assert all(c.detail_image == source for c in duplicate.channels if c.enabled)
duplicate.opacity = .25
assert report(ts)["image_count"] == len(ts.outputs) + 1, "Shared detail was counted per channel"

with TemporaryDirectory(prefix="open-painter-generated-portable-") as directory:
    directory = Path(directory)
    path = serializer.save(ts, directory / "generated.openpaintmaterial")
    with ZipFile(path) as archive:
        data = json.loads(archive.read("material.json"))
        from open_painter.smart_materials.format import VERSION
        assert data["format_version"] == VERSION
        assets = [name for name in archive.namelist() if name.startswith("assets/")]
        assert len(assets) == 1, "Shared scalar source was encoded more than once"
        for item in data["layers"]:
            for channel in item["channels"]:
                assert "detail_image_name" not in channel
                if channel["enabled"]:
                    assert channel["detail_image"]["asset"] == assets[0]

    copy = sets.create(scene, "Imported scalar detail", 512)
    before = len(bpy.data.images)
    loader.apply(copy, path)
    assert len(bpy.data.images) == before + 1, "Shared source was decoded per binding"
    imported = stack.channel_for(copy.layers[0], "BASE_COLOR").detail_image
    assert imported != source and imported.packed_file
    assert imported.colorspace_settings.name == "Non-Color"
    assert all(c.detail_image == imported for item in copy.layers for c in item.channels if c.enabled)
    assert all(c.detail_image_name == imported.name for item in copy.layers for c in item.channels if c.enabled)
    np.testing.assert_allclose(images.pixels(imported), values, atol=2e-6)

    evaluator = LayerEvaluator(scene)
    for key in ("BASE_COLOR", "ROUGHNESS", "HEIGHT"):
        evaluator.evaluate_channel(ts.id, key)
        evaluator.evaluate_channel(copy.id, key)
        np.testing.assert_allclose(images.pixels(images.output(ts, key).image),
                                   images.pixels(images.output(copy, key).image), atol=2e-6)
    before_roughness = images.pixels(images.output(copy, "ROUGHNESS").image).copy()
    for item in copy.layers:
        stack.channel_for(item, "ROUGHNESS").value += .1
    evaluator.evaluate_channel(copy.id, "ROUGHNESS")
    assert np.mean(images.pixels(images.output(copy, "ROUGHNESS").image)[..., 0] -
                   before_roughness[..., 0]) > .095, "Surface detail replaced editable roughness"
    np.testing.assert_allclose(images.pixels(imported), values, atol=2e-6)

    broken = directory / "missing-source.openpaintmaterial"
    with ZipFile(broken, "w") as archive:
        archive.writestr("material.json", json.dumps(data))
    before = (len(copy.layers), len(bpy.data.images), copy.edit_serial)
    try:
        loader.apply(copy, broken)
        raise AssertionError("Missing detail asset was accepted")
    except ValueError as error:
        assert "Missing smart material texture" in str(error)
    assert before == (len(copy.layers), len(bpy.data.images), copy.edit_serial)

    # Missing persistent pointers must not silently produce incomplete exports.
    channel = stack.channel_for(copy.layers[0], "ROUGHNESS")
    channel.detail_image = None
    channel.detail_image_name = "Missing source"
    try:
        serializer.save(copy, directory / "incomplete.openpaintmaterial")
        raise AssertionError("Missing detail image was silently dropped")
    except ValueError as error:
        assert "Missing texture" in str(error)
    assert not (directory / "incomplete.openpaintmaterial").exists()
    channel.detail_image = imported

    # Existing version-1/version-2 files need no optional detail fields.
    for version in (1, 2):
        legacy = directory / f"legacy-{version}.openpaintmaterial"
        legacy.write_text(json.dumps({"format_version": version, "layers": [{
            "id": "legacy", "type": "FILL", "name": "Legacy constant", "channels": [{
                "channel": "ROUGHNESS", "enabled": True, "value": .42, "image": None,
            }],
        }]}))
        old = sets.create(scene, f"Legacy {version}", 512)
        loader.apply(old, legacy)
        old_channel = stack.channel_for(old.layers[0], "ROUGHNESS")
        assert old_channel.detail_image is None and not old_channel.detail_image_name
        evaluator.evaluate_channel(old.id, "ROUGHNESS")
        np.testing.assert_allclose(images.pixels(images.output(old, "ROUGHNESS").image)[..., 0], .42, atol=2e-6)

    # Packed detail must survive a .blend save independently of the import ZIP.
    copy_id = copy.id
    blend = directory / "generated-detail.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    path.unlink()
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    copy = next(item for item in bpy.context.scene.open_painter.texture_sets if item.id == copy_id)
    restored = stack.channel_for(copy.layers[0], "BASE_COLOR").detail_image
    assert restored.packed_file
    assert all(c.detail_image == restored for item in copy.layers for c in item.channels if c.enabled)
    np.testing.assert_allclose(images.pixels(restored), values, atol=2e-6)

open_painter.unregister()
print("GENERATED_MATERIAL_PORTABILITY_PASS: shared EXR asset, editable constants, independent import, packed blend reload, missing-source rejection, v1/v2 compatibility")
