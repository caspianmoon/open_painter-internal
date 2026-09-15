"""Asset type isolation and real shelf activation into channels, masks and brushes."""

import sys
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import bpy
import numpy as np

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "src"))
import open_painter
from open_painter.ui import native
from open_painter.ui import library_categories as categories
from open_painter.core import layer_stack as stack, texture_set_manager as sets

open_painter.register()


def verify_fingerprint_preview(proxy):
    """The shelf must show a readable print, not separated aliased X marks."""
    width, height = proxy.preview.image_size
    assert (width, height) == (48, 48), (width, height)
    rgba = np.asarray(proxy.preview.image_pixels_float, dtype=np.float32).reshape(height, width, 4)
    assert np.isfinite(rgba).all() and rgba.min() >= 0 and rgba.max() <= 1
    np.testing.assert_allclose(rgba[..., 0], rgba[..., 1], atol=1e-6)
    np.testing.assert_allclose(rgba[..., 0], rgba[..., 2], atol=1e-6)
    np.testing.assert_allclose(rgba[..., 3], 1, atol=1e-6)
    gray = rgba[..., 0]

    # A line through the center of a print crosses multiple visible ridges.
    # The former six-repetition preview left this line empty at shelf size.
    ridges = gray[height // 2] > .4
    ridge_starts = ridges & ~np.r_[False, ridges[:-1]]
    assert np.count_nonzero(ridge_starts) >= 4, "Fingerprint ridges vanished into thumbnail aliasing"

    # Ignore the spaces between individual ridges when examining the overall
    # print footprint. It should form one connected shape, rather than dozens
    # of separate tiny marks distributed across the thumbnail.
    footprint = gray.reshape(12, 4, 12, 4).mean(axis=(1, 3)) > .07
    remaining = footprint.copy()
    components = []
    for y, x in np.argwhere(footprint):
        if not remaining[y, x]:
            continue
        queue = deque(((int(y), int(x)),))
        remaining[y, x] = False
        count = 0
        while queue:
            qy, qx = queue.popleft()
            count += 1
            for ny, nx in ((qy-1, qx), (qy+1, qx), (qy, qx-1), (qy, qx+1)):
                if 0 <= ny < 12 and 0 <= nx < 12 and remaining[ny, nx]:
                    remaining[ny, nx] = False
                    queue.append((ny, nx))
        components.append(count)
    assert components and max(components) / footprint.sum() > .9, "Fingerprint footprint is fragmented"
    y, x = np.indices(gray.shape)
    central = (x >= width*.2) & (x < width*.8) & (y >= height*.1) & (y < height*.9)
    assert gray[central].sum() / gray.sum() > .9, "Fingerprint preview is a field of tiny stamps"
    return rgba.copy()


def run():
    project = bpy.context.scene.open_painter
    ts = sets.create(bpy.context.scene, "Asset category tests", 512)
    sets.assign(bpy.context.scene, ts, bpy.context.object)
    layer = stack.add(ts, "FILL", "Asset target")
    project.properties_channel = "ROUGHNESS"
    source = bpy.data.images.new("Library source", width=16, height=16)
    source.generated_color = (.2, .4, .6, 1)
    source["open_painter_library_texture"] = True
    alpha = bpy.data.textures.new("Library alpha", type="IMAGE")
    alpha.image = source
    native.prepare_assets()


    def handle(key):
        return next(image for image in bpy.data.images if image.get(native.ASSET_KEY) == key)


    def apply(key):
        image = handle(key)
        result = bpy.ops.open_painter.native_asset(relative_asset_identifier="Image/" + image.name)
        assert result == {"FINISHED"}, (key, result)


    def visible():
        return {image.get(native.ASSET_KEY) for image in bpy.data.images
                if image.asset_data and native.VIEW3D_AST_open_painter.asset_poll(SimpleNamespace(local_id=image))}


    fingerprint = handle("PROCEDURAL:FINGERPRINTS")
    fingerprint_pixels = verify_fingerprint_preview(fingerprint)
    initial_serial = ts.edit_serial
    groups = {}
    for identifier, *_ in categories.CATEGORY_ITEMS:
        project.asset_category = identifier
        groups[identifier] = visible()
        assert groups[identifier], identifier
    assert ts.edit_serial == initial_serial, "Browsing must not recalculate material textures"
    assert groups["MATERIALS"].isdisjoint(groups["SMART_MATERIALS"])
    assert "Copper" in groups["MATERIALS"]
    assert "Painted Steel" in groups["SMART_MATERIALS"]
    assert "Worn Painted Metal" in groups["SMART_MATERIALS"]
    assert "Edge Broken" in groups["MASKS"]
    assert "FILTER:LEVELS" in groups["FILTERS"]
    assert "BRUSH:Paint Hard" in groups["BRUSHES"]
    assert "ALPHA:Library alpha" in groups["ALPHAS"]
    assert "TEXTURE:Library source" in groups["TEXTURES"], (source.name, source.type, dict(source.items()), sorted(groups["TEXTURES"]))
    assert "PROCEDURAL:FBM" in groups["TEXTURES"]
    surface_sources = ("TAPERED_SCRATCHES", "FINE_FISSURES", "FRACTURED_PAINT")
    for identifier in ("FINGERPRINTS", "SCRATCHES", "CRACKS") + surface_sources:
        assert "PROCEDURAL:" + identifier in groups["TEXTURES"], identifier
    assert set.union(*(values for name, values in groups.items() if name != "ALL")) == groups["ALL"]
    assert sum(len(values) for name, values in groups.items() if name != "ALL") == len(groups["ALL"])
    expected_category_values = {"ALL": 0, "MATERIALS": 1, "MASKS": 2, "SMART_MATERIALS": 3,
                                "FILTERS": 4, "BRUSHES": 5, "ALPHAS": 6, "TEXTURES": 7}
    assert {item.identifier: item.value for item in project.bl_rna.properties["asset_category"].enum_items} == expected_category_values

    apply("FILTER:LEVELS")
    assert stack.channel_for(layer, "ROUGHNESS").effects[0].kind == "LEVELS"
    assert not stack.channel_for(layer, "BASE_COLOR").effects
    channel = stack.channel_for(layer, "ROUGHNESS")
    channel.detail_image = source
    channel.image = source
    assert channel.detail_image == source, "Direct base-image assignment discarded scalar detail"
    apply("PROCEDURAL:FBM")
    assert stack.channel_for(layer, "ROUGHNESS").procedural == "FBM"
    assert channel.detail_image is None and not channel.detail_image_name
    for identifier in surface_sources:
        apply("PROCEDURAL:" + identifier)
        assert channel.procedural == identifier and channel.enabled
    channel.detail_image = source
    apply("TEXTURE:Library source")
    assert stack.channel_for(layer, "ROUGHNESS").image == source
    assert not stack.channel_for(layer, "ROUGHNESS").procedural
    assert channel.detail_image is None and not channel.detail_image_name
    channel.detail_image = source
    native.state.get(bpy.context.area).mask_selected = True
    apply("PROCEDURAL:FBM")
    apply("FILTER:INVERT")
    apply("TEXTURE:Library source")
    assert [mask.source for mask in layer.masks] == ["PROCEDURAL", "FILTER", "FILL"]
    assert layer.masks[1].effect.kind == "INVERT" and layer.masks[2].image == source
    assert channel.detail_image == source, "Mask texture activation changed the channel source"
    channel.detail_image = None

    # Renaming source IDs reuses their shelf proxy; deleting a source removes it from the shelf.
    proxy = handle("TEXTURE:Library source")
    count = len(bpy.data.images)
    source.name = "Renamed library source"
    native.prepare_assets()
    assert handle("TEXTURE:Renamed library source") == proxy
    assert len(bpy.data.images) == count
    assert proxy[categories.SOURCE_KEY] == source
    native.prepare_assets()
    assert len(bpy.data.images) == count
    assert handle("PROCEDURAL:FINGERPRINTS") == fingerprint
    np.testing.assert_array_equal(verify_fingerprint_preview(fingerprint), fingerprint_pixels)
    source.pixels.foreach_set(np.tile(np.array((.8, .4, .2, 1), dtype=np.float32), 16 * 16))
    source.update()
    assert bpy.ops.open_painter.refresh_assets() == {"FINISHED"}
    assert abs(proxy.preview.image_pixels_float[0] - .8) < .01

    essentials = Path(bpy.utils.system_resource("DATAFILES")) / "assets/brushes/essentials_brushes-mesh_texture.blend"
    with bpy.data.libraries.load(str(essentials)) as (available, _loaded):
        assert all(entry[2] in available.brushes for entry in native.assets.BRUSHES), available.brushes

    native.state.get(bpy.context.area).mask_selected = False
    paint = stack.add(ts, "PAINT", "Brush target")
    project.paint_channel = "BASE_COLOR"
    apply("BRUSH:Paint Hard")
    assert project.paint_image is not None
    apply("ALPHA:Library alpha")
    assert bpy.context.scene.tool_settings.image_paint.brush.mask_texture == alpha
    apply("ALPHA:")
    assert bpy.context.scene.tool_settings.image_paint.brush.mask_texture is None
    bpy.ops.open_painter.paint(stop=True)

    source.filepath_raw = str(root / ".blender-test/category-source.png")
    Path(source.filepath_raw).parent.mkdir(exist_ok=True)
    source.file_format = "PNG"
    source.save()
    layer_count, serial = len(ts.layers), ts.edit_serial
    assert bpy.ops.open_painter.import_texture(filepath=source.filepath_raw) == {"FINISHED"}
    imported = bpy.data.images.get("category-source.png")
    assert imported and imported.packed_file and not imported.asset_data
    assert project.asset_category == "TEXTURES"
    assert handle("TEXTURE:" + imported.name)[categories.SOURCE_KEY] == imported
    assert len(ts.layers) == layer_count and ts.edit_serial == serial

    alpha_proxy = handle("ALPHA:Library alpha")
    bpy.data.textures.remove(alpha)
    native.prepare_assets()
    assert not alpha_proxy.asset_data

    # Shelf handles are runtime state: saving must not write hundreds of
    # library images into the artist's file, and the imported texture stays.
    assert len(native.handles()) > 100
    saved = Path(bpy.app.tempdir) / "asset-handles.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(saved))
    assert not any(image.use_fake_user or image.asset_data for image in native.handles())
    bpy.ops.wm.open_mainfile(filepath=str(saved))
    assert not native.handles(), [image.name for image in native.handles()][:5]
    assert bpy.data.images.get("category-source.png").packed_file
    native.prepare_assets()
    assert len(native.handles()) > 100
    open_painter.unregister()
    open_painter.register()
    open_painter.unregister()
    print("ASSET_CATEGORIES_PASS: seven disjoint categories; connected fingerprint ridges; opt-in surface source activation; stable category IDs; channel/mask filter and texture activation; brush/alpha activation; source rename/delete; idempotent refresh and lifecycle")


area = next(area for area in bpy.context.screen.areas if area.type == "VIEW_3D")
region = next(region for region in area.regions if region.type == "WINDOW")
with bpy.context.temp_override(area=area, region=region):
    run()
