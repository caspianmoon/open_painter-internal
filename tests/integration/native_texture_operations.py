"""Native operators author real procedural/channel/mask stacks and catalog layers."""

import json
import sys
from pathlib import Path

import bpy
import numpy as np

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "src"))
import open_painter
from open_painter.core import layer_stack as stack, texture_set_manager as sets
from open_painter.evaluation.mask_eval import evaluate_masks
from open_painter.smart_materials.catalog import CATALOG
from open_painter.ui import native

open_painter.register()
ts = sets.create(bpy.context.scene, "Texture operations", 512)
sets.assign(bpy.context.scene, ts, bpy.context.object)
layer = stack.add(ts, "FILL", "Editable procedural")
prop = stack.channel_for(layer, "BASE_COLOR")
from open_painter.ui import shelf_library
native.prepare_assets()
# Built-in entries live in the shelf library, never as local images; every catalog material is there.
assert shelf_library.current()
assert set(CATALOG) <= set(shelf_library.manifest()["names"].values())
handles = [image for image in bpy.data.images if native.ASSET_KEY in image]
assert not any(shelf_library.is_builtin(e) for e in native.catalog() if native.categories.key(e) in {h[native.ASSET_KEY] for h in handles})
count = len(handles)
native.prepare_assets()
assert sum(native.ASSET_KEY in image for image in bpy.data.images) == count
assert all(image.asset_data and image.preview.image_size[0] for image in handles)

assert bpy.ops.open_painter.choose_procedural(choice="WOOD_GRAIN") == {"FINISHED"}
assert prop.procedural == "WOOD_GRAIN" and not prop.image
assert bpy.ops.open_painter.choose_filter(choice="LEVELS") == {"FINISHED"}
prop.effects[0].gamma = .6
assert bpy.ops.open_painter.choose_filter(choice="SLOPE_BLUR") == {"FINISHED"}
assert bpy.ops.open_painter.effect_noise(choice="PERLIN") == {"FINISHED"}
assert prop.effects[1].noise == "PERLIN"
assert bpy.ops.open_painter.channel_effect(action="UP") == {"FINISHED"}
assert [effect.kind for effect in prop.effects] == ["SLOPE_BLUR", "LEVELS"]
assert bpy.ops.open_painter.channel_effect(action="DELETE") == {"FINISHED"}
assert len(prop.effects) == 1 and abs(prop.effects[0].gamma - .6) < 1e-6
assert bpy.ops.open_painter.choose_filter(choice="HSL", create=False) == {"FINISHED"}
assert prop.effects[0].kind == "HSL"
assert bpy.ops.open_painter.choose_filter(choice="BRIGHTNESS_CONTRAST", create=False) == {"FINISHED"}
assert prop.effects[0].amount == 0
assert bpy.ops.open_painter.choose_filter(choice="NORMAL_STRENGTH", create=False) == {"FINISHED"}
assert prop.effects[0].amount == 1

assert bpy.ops.open_painter.choose_procedural(choice="SCRATCHES", target="MASK", create=True) == {"FINISHED"}
assert layer.masks[0].source == "PROCEDURAL" and layer.masks[0].procedural == "SCRATCHES"
before = evaluate_masks(ts, layer, 512, 0, 512, {}, {})
assert before.max() > before.min()
assert bpy.ops.open_painter.choose_filter(choice="LEVELS", target="MASK") == {"FINISHED"}
layer.masks[1].effect.output_black = .3
after = evaluate_masks(ts, layer, 512, 0, 512, {}, {})
assert after.min() >= .299 and not np.allclose(before, after)
assert bpy.ops.open_painter.mask(action="UP") == {"FINISHED"}
assert layer.masks[0].source == "FILTER"
assert bpy.ops.open_painter.mask(action="ADD", source="FILTER") == {"FINISHED"}
assert layer.masks[-1].effect.kind == "LEVELS"

# UI buttons run non-nested (undo=True here) and Blender refills unset operator
# properties from the previous run: a mask-side pick must not steer the
# channel-side buttons (which leave target unset) onto the active mask.
assert bpy.ops.open_painter.mask(action="ADD", source="PAINT") == {"FINISHED"}
painted_index, mask_count, effect_count = layer.active_mask, len(layer.masks), len(prop.effects)
painted = lambda: layer.masks[painted_index]  # masks.add() invalidates references
painted_name = painted().name
assert painted().source == "PAINT"
assert bpy.ops.open_painter.choose_filter(True, choice="BLUR", target="MASK", create=True) == {"FINISHED"}
layer.active_mask = painted_index
assert bpy.ops.open_painter.choose_filter(True, choice="HSL", create=False) == {"FINISHED"}
assert (painted().source, painted().name) == ("PAINT", painted_name) and prop.effects[prop.active_effect].kind == "HSL"
assert bpy.ops.open_painter.choose_filter(True, choice="INVERT") == {"FINISHED"}
assert len(prop.effects) == effect_count + 1 and prop.effects[-1].kind == "INVERT"
assert bpy.ops.open_painter.choose_procedural(True, choice="FBM", target="MASK", create=False) == {"FINISHED"}
assert painted().source == "PROCEDURAL"
painted().source, renamed = "PAINT", painted().name
assert bpy.ops.open_painter.choose_procedural(True, choice="VORONOI") == {"FINISHED"}
assert (painted().source, painted().name, prop.procedural) == ("PAINT", renamed, "VORONOI")
assert len(layer.masks) == mask_count + 1

assert bpy.ops.open_painter.catalog_material(choice="Clean Brushed Steel") == {"FINISHED"}
folder = stack.active(ts)
assert folder.type == "FOLDER" and folder.name == "Clean Brushed Steel"
assert any(item.parent_id == folder.id for item in ts.layers)
folder_id = folder.id
assert bpy.ops.open_painter.catalog_effect(choice="Fingerprints") == {"FINISHED"}
assert stack.active(ts).name == "Fingerprints"
assert stack.active(ts).parent_id == folder_id
assert ts.anisotropy > 0

# Named IDs come from actual baked target geometry, not a hardcoded color list.
from open_painter.baking.bake_manager import register_map
from open_painter.baking.maps.utility import bake_utility
from open_painter.core.image_manager import pixels
id_set = sets.create(bpy.context.scene, "Named ID selection", 512)
objects = []
for index, name in enumerate(("Copper Panel", "Steel Panel")):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(index, 0, 0), (index + 1, 0, 0), (index + 1, 1, 0), (index, 1, 0)], [], [(0, 1, 2, 3)])
    uv = mesh.uv_layers.new()
    x0, x1 = (.05, .45) if index == 0 else (.55, .95)
    for loop, coordinate in zip(mesh.polygons[0].loop_indices, ((x0,.1),(x1,.1),(x1,.9),(x0,.9))):
        uv.data[loop].uv = coordinate
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    mesh.materials.append(bpy.data.materials.new(name + " Material"))
    sets.assign(bpy.context.scene, id_set, obj)
    objects.append(obj)
bpy.context.view_layer.update()
id_layer = stack.add(id_set, "FILL", "ID Selection")
assert bpy.ops.open_painter.mask(action="ADD", source="GENERATOR") == {"FINISHED"}
for kind in ("MATERIAL_ID", "OBJECT_ID", "UV_ISLAND"):
    image = bake_utility(id_set, kind)
    register_map(id_set, kind, image)
    before_pixels = pixels(image)
    palette = json.loads(image["id_palette"])
    mask = id_layer.masks[0]
    mask.generator = kind
    mask.amount = .01
    choices = list(palette)
    assert len(choices) == 2
    assert bpy.ops.open_painter.choose_baked_id(choice=choices[0]) == {"FINISHED"}
    assert np.allclose(mask.id_color, palette[choices[0]])
    first = evaluate_masks(id_set, id_layer, 512, 0, 512, {}, {})
    assert first[256, 128] == 1 and first[256, 384] == 0
    assert bpy.ops.open_painter.choose_baked_id(choice=choices[1]) == {"FINISHED"}
    second = evaluate_masks(id_set, id_layer, 512, 0, 512, {}, {})
    assert second[256, 128] == 0 and second[256, 384] == 1
    assert np.array_equal(before_pixels, pixels(image))
open_painter.unregister()
print("NATIVE_TEXTURE_OPERATIONS_PASS: all50 asset handles; reusable filter/procedural operators; channel buttons ignore remembered mask targets; ordered mask filters alter pixels; searchable catalog applies editable layers; named material/object/island IDs select baked pixels without changing source maps")
