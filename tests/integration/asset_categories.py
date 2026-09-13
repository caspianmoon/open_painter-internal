"""Asset type isolation and real shelf activation into channels, masks and brushes."""

import sys
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
    assert set.union(*(values for name, values in groups.items() if name != "ALL")) == groups["ALL"]
    assert sum(len(values) for name, values in groups.items() if name != "ALL") == len(groups["ALL"])
    assert project.bl_rna.properties["asset_category"].enum_items["MASKS"].value == 2

    apply("FILTER:LEVELS")
    assert stack.channel_for(layer, "ROUGHNESS").effects[0].kind == "LEVELS"
    assert not stack.channel_for(layer, "BASE_COLOR").effects
    apply("PROCEDURAL:FBM")
    assert stack.channel_for(layer, "ROUGHNESS").procedural == "FBM"
    apply("TEXTURE:Library source")
    assert stack.channel_for(layer, "ROUGHNESS").image == source
    assert not stack.channel_for(layer, "ROUGHNESS").procedural
    native.state.get(bpy.context.area).mask_selected = True
    apply("PROCEDURAL:FBM")
    apply("FILTER:INVERT")
    apply("TEXTURE:Library source")
    assert [mask.source for mask in layer.masks] == ["PROCEDURAL", "FILTER", "FILL"]
    assert layer.masks[1].effect.kind == "INVERT" and layer.masks[2].image == source

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
    open_painter.unregister()
    open_painter.register()
    open_painter.unregister()
    print("ASSET_CATEGORIES_PASS: seven disjoint categories; channel/mask filter and texture activation; brush/alpha activation; source rename/delete; idempotent refresh and lifecycle")


area = next(area for area in bpy.context.screen.areas if area.type == "VIEW_3D")
region = next(region for region in area.regions if region.type == "WINDOW")
with bpy.context.temp_override(area=area, region=region):
    run()
