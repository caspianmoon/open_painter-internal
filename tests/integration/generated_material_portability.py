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

    # A byte-sourced texture travels as half floats (every 8-bit step preserved; half the
    # bytes) while float canvases keep 32 bits, and a set whose textures exceed the import
    # budget is refused at save time rather than written as a file nothing can import (0.9.7).
    from open_painter.smart_materials import format as material_format
    copy_id = copy.id   # the sets added below reallocate the collection; resolve by id after
    byte_values = np.round(np.random.default_rng(7).random((32, 32, 4), np.float32) * 255) / 255
    bitmap = bpy.data.images.new("Byte portable bitmap", 32, 32, alpha=True)
    bitmap.pixels.foreach_set(byte_values.astype(np.float32).ravel())
    bitmap.filepath_raw, bitmap.file_format = str(directory / "byte_source.png"), "PNG"
    bitmap.save()
    byte_source = bpy.data.images.load(bitmap.filepath_raw, check_existing=False)
    byte_source.colorspace_settings.name = "Non-Color"
    byte_source.pack()
    assert not byte_source.is_float
    mixed = sets.create(scene, "Mixed depths", 512)
    mixed_layer = stack.add(mixed, "FILL", "Mixed sources")
    stack.channel_for(mixed_layer, "BASE_COLOR").detail_image = byte_source
    stack.channel_for(mixed_layer, "ROUGHNESS").detail_image = source
    mixed_path = serializer.save(mixed, directory / "mixed.openpaintmaterial")


    def exr_pixel_types(blob):
        # The chlist attribute: per channel a name, then pixel type (1 half, 2 float) and 12 more bytes.
        pos = blob.index(b"channels\x00chlist\x00") + len(b"channels\x00chlist\x00") + 4
        found = set()
        while blob[pos]:
            pos = blob.index(b"\x00", pos) + 1
            found.add(int.from_bytes(blob[pos:pos + 4], "little"))
            pos += 16
        return found

    with ZipFile(mixed_path) as archive:
        mixed_data = json.loads(archive.read("material.json"))
        by_channel = {c["channel"]: c["detail_image"]["asset"] for c in mixed_data["layers"][0]["channels"]
                      if c["channel"] in {"BASE_COLOR", "ROUGHNESS"}}
        depths = {key: exr_pixel_types(archive.read(asset)) for key, asset in by_channel.items()}
    assert depths == {"BASE_COLOR": {1}, "ROUGHNESS": {2}}, depths
    mixed_copy = sets.create(scene, "Mixed depths import", 512)
    loader.apply(mixed_copy, mixed_path)
    np.testing.assert_allclose(images.pixels(stack.channel_for(mixed_copy.layers[0], "BASE_COLOR").detail_image),
                               byte_values, atol=.5 / 255)
    np.testing.assert_allclose(images.pixels(stack.channel_for(mixed_copy.layers[0], "ROUGHNESS").detail_image),
                               values, atol=2e-6)
    limits = material_format.MAX_BYTES, material_format.MAX_FILES
    try:
        for material_format.MAX_BYTES, material_format.MAX_FILES in ((1024, limits[1]), (limits[0], 2)):
            try:
                serializer.save(mixed, directory / "oversized.openpaintmaterial")
                raise AssertionError("A material over the import budget was saved")
            except ValueError as error:
                assert "import limit" in str(error), error
            assert not (directory / "oversized.openpaintmaterial").exists()
    finally:
        material_format.MAX_BYTES, material_format.MAX_FILES = limits
    assert not any(name.startswith("OP Asset Encoding") for name in bpy.data.scenes.keys())
    copy = next(item for item in scene.open_painter.texture_sets if item.id == copy_id)

    # An 8-bit sRGB albedo comes back evaluating exactly as it left: save_render writes the
    # asset EXR scene-linear, so the reference records the linear space rather than the
    # source's own, and a file saved before 0.9.7 (tagged sRGB) loads the same way (0.9.7).
    albedo = bpy.data.images.new("Byte albedo", 32, 32, alpha=True)
    albedo.pixels.foreach_set(np.tile(np.array([.5, .5, .5, 1], np.float32), 32 * 32))
    albedo.pack()   # a colour space change reloads the image: from the packed bytes, not blank
    albedo.colorspace_settings.name = "sRGB"
    assert not albedo.is_float and not albedo.colorspace_settings.is_data
    np.testing.assert_allclose(images.pixels(albedo)[..., :3], 128 / 255, atol=1e-6)
    coloured = sets.create(scene, "Byte albedo source", 512)
    coloured_layer = stack.add(coloured, "FILL", "Albedo fill")
    for channel in coloured_layer.channels:
        channel.enabled = channel.channel == "BASE_COLOR"
    stack.channel_for(coloured_layer, "BASE_COLOR").image = albedo
    evaluator.evaluate_channel(coloured.id, "BASE_COLOR")
    expected = images.pixels(images.output(coloured, "BASE_COLOR").image).copy()
    assert abs(expected[..., 0].mean() - .2159) < 2e-3, expected[..., 0].mean()   # linearised once
    coloured_path = serializer.save(coloured, directory / "albedo.openpaintmaterial")
    with ZipFile(coloured_path) as archive:
        coloured_data = json.loads(archive.read("material.json"))
    recorded = next(c for c in coloured_data["layers"][0]["channels"] if c["channel"] == "BASE_COLOR")["image"]
    assert recorded["colorspace"] == material_format.LINEAR, recorded
    legacy_tagged = directory / "albedo-srgb-tagged.openpaintmaterial"
    with ZipFile(coloured_path) as source_archive, ZipFile(legacy_tagged, "w") as archive:
        for info in source_archive.infolist():
            archive.writestr(info, source_archive.read(info) if info.filename != "material.json"
                             else json.dumps(coloured_data).replace(material_format.LINEAR, "sRGB"))
    for tagged in (coloured_path, legacy_tagged):
        coloured_copy = sets.create(scene, "Byte albedo import", 512)
        loader.apply(coloured_copy, tagged)
        imported_albedo = stack.channel_for(coloured_copy.layers[0], "BASE_COLOR").image
        assert imported_albedo.colorspace_settings.name == material_format.LINEAR, imported_albedo.colorspace_settings.name
        evaluator.evaluate_channel(coloured_copy.id, "BASE_COLOR")
        np.testing.assert_allclose(images.pixels(images.output(coloured_copy, "BASE_COLOR").image), expected,
                                   atol=1e-3, err_msg=tagged.name)

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
    copy = next(item for item in scene.open_painter.texture_sets if item.id == copy_id)
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
