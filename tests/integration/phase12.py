"""Per-channel composition, true folder backdrops, fill sampling and persistence."""

import sys
from pathlib import Path
import bpy
import numpy as np

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "src"))
import open_painter
from open_painter.core import (
    texture_set_manager as sets,
    layer_stack as stack,
    image_manager as images,
)
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.smart_materials import serializer, loader
from open_painter.painting.projection import triplanar

open_painter.register()
scene = bpy.context.scene
ts = sets.create(scene, "Channel composition")
ts.resolution = "512"
sets.assign(scene, ts, bpy.context.object, 0)
e = LayerEvaluator(scene)
bottom = stack.add(ts, "FILL")
stack.channel_for(bottom, "ROUGHNESS").value = 0.8
stack.channel_for(bottom, "METALLIC").value = 0.2
folder = stack.add(ts, "FOLDER")
folder_id = folder.id
child = stack.add(ts, "FILL")
child_id = child.id
stack.parent(ts, child, folder_id)
rough = stack.channel_for(child, "ROUGHNESS")
metal = stack.channel_for(child, "METALLIC")
rough.value, metal.value = 0.25, 0.6
rough.blend_mode = "MULTIPLY"
metal.blend_mode = "SCREEN"


def pixel(channel):
    e.evaluate_texture_set(ts.id)
    return images.pixels(next(o.image for o in ts.outputs if o.channel == channel))[0, 0, 0]


# The first multiply child of an isolated folder has no backdrop to multiply against.
assert np.isclose(pixel("ROUGHNESS"), 0.25)
folder.group_mode = "PASSTHROUGH"
assert np.isclose(pixel("ROUGHNESS"), 0.2)
assert np.isclose(pixel("METALLIC"), 0.68)
rough.opacity = 0.5
assert np.isclose(pixel("ROUGHNESS"), 0.5)
folder.opacity = 0.5
assert np.isclose(pixel("ROUGHNESS"), 0.65)
assert np.isclose(pixel("METALLIC"), 0.44)
# Masks attenuate the completed group's change, not every child independently.
mask = folder.masks.add()
mask.source = "PAINT"
mask.image = images.create("Half mask", 512, (0.5, 0.5, 0.5, 1))
assert np.isclose(pixel("ROUGHNESS"), 0.725)
mask.enabled = False
stack.channel_for(folder, "ROUGHNESS").opacity = 0
assert np.isclose(pixel("ROUGHNESS"), 0.8)
stack.channel_for(folder, "ROUGHNESS").opacity = 1
# Only the edited channel is invalidated.
e.evaluate_texture_set(ts.id)
revisions = {o.channel: o.revision for o in ts.outputs}
metal.opacity = 0.5
assert [o.channel for o in ts.outputs if o.dirty] == ["METALLIC"]
assert np.isclose(pixel("METALLIC"), 0.32)
assert all(o.revision == revisions[o.channel] for o in ts.outputs if o.channel != "METALLIC")
rough.blend_mode = "DISABLE"
assert np.isclose(pixel("ROUGHNESS"), 0.8)
rough.blend_mode = "MULTIPLY"
# A non-repeating fill leaves the underlying pixels untouched outside its tile.
folder.visible = False
fill = stack.add(ts, "FILL")
fill_id = fill.id
p = stack.channel_for(fill, "ROUGHNESS")
p.image = images.create("Fill sample", 2, (0.9, 0.9, 0.9, 1))
p.offset = (1, 0)
p.filtering, p.tiling = "NEAREST", "NONE"
assert np.isclose(pixel("ROUGHNESS"), 0.8)
p.tiling = "REPEAT"
assert np.isclose(pixel("ROUGHNESS"), 0.9)
# Hardness changes projection transitions and remains finite at its maximum.
texture = np.array([[[0.0, 0, 0, 1], [1, 1, 1, 1]]], dtype=np.float32)
positions = np.array([[[0.75, 0.25, 0.25]]])
normals = np.array([[[0.8, 0.6, 0.0]]])
p.scale, p.offset = (1, 1), (0, 0)
p.triplanar_hardness = 1
soft = triplanar(texture, positions, normals, p)[0, 0, 0]
p.triplanar_hardness = 64
hard = triplanar(texture, positions, normals, p)[0, 0, 0]
assert np.isfinite(hard) and hard < soft - 0.2, (soft, hard)
# Persist new properties through .blend, then duplicate and portable-material export/import.
path = root / ".blender-test/channel_composition.blend"
bpy.ops.wm.save_as_mainfile(filepath=str(path))
bpy.ops.wm.open_mainfile(filepath=str(path))
scene = bpy.context.scene
ts = scene.open_painter.texture_sets[0]
assert ts.layers[1].group_mode == "PASSTHROUGH"
assert stack.channel_for(ts.layers[2], "ROUGHNESS").opacity == 0.5
assert stack.channel_for(ts.layers[2], "METALLIC").blend_mode == "SCREEN"
assert stack.channel_for(ts.layers[3], "ROUGHNESS").triplanar_hardness == 64
copy = stack.duplicate(ts, 1)
assert copy.group_mode == "PASSTHROUGH" and len(copy.channels) == 8
# Legacy folder defaults remain isolated and can acquire neutral channel settings.
legacy = ts.layers.add()
legacy.id, legacy.type = "legacy", "FOLDER"
assert legacy.group_mode == "ISOLATED"
stack.ensure_channels(legacy)
assert all(c.enabled and c.opacity == 1 and c.blend_mode == "INHERIT" for c in legacy.channels)
material = root / ".blender-test/channel_composition.openpaintmaterial"
material.unlink(missing_ok=True)
serializer.save(ts, material)
folder_index = next(i for i, item in enumerate(ts.layers) if item.id == folder_id)
child_index = next(i for i, item in enumerate(ts.layers) if item.id == child_id)
fill_index = next(i for i, item in enumerate(ts.layers) if item.id == fill_id)
target = sets.create(scene, "Imported composition")
loader.apply(target, material)
assert target.layers[folder_index].group_mode == "PASSTHROUGH"
assert target.layers[child_index].parent_id == target.layers[folder_index].id
assert stack.channel_for(target.layers[child_index], "METALLIC").blend_mode == "SCREEN"
assert stack.channel_for(target.layers[fill_index], "ROUGHNESS").filtering == "NEAREST"
# Nested passthrough groups and an empty group preserve the correct backdrop.
ts = sets.create(scene, "Nested composition")
ts.resolution = "512"
e = LayerEvaluator(scene)
base = stack.add(ts)
stack.channel_for(base, "ROUGHNESS").value = 0.8
outer = stack.add(ts, "FOLDER")
outer.group_mode, outer.opacity = "PASSTHROUGH", 0.5
inner = stack.add(ts, "FOLDER")
inner.group_mode, inner.opacity = "PASSTHROUGH", 0.5
stack.parent(ts, inner, outer.id)
child = stack.add(ts)
stack.parent(ts, child, inner.id)
prop = stack.channel_for(child, "ROUGHNESS")
prop.value, prop.opacity, prop.blend_mode = 0.25, 0.5, "MULTIPLY"
stack.add(ts, "FOLDER").group_mode = "PASSTHROUGH"
assert np.isclose(pixel("ROUGHNESS"), 0.725)
# Two partially transparent children: independent premultiplied reference is
# .17 coverage .75, so the .8 backdrop contributes .2, giving .37.
outer, inner = ts.layers[1], ts.layers[2]
outer.group_mode, outer.opacity = "ISOLATED", 1
inner.group_mode, inner.opacity = "PASSTHROUGH", 1
inner_id = inner.id
prop.value, prop.opacity, prop.blend_mode = 0.2, 0.5, "NORMAL"
second = stack.add(ts)
stack.parent(ts, second, inner_id)
prop = stack.channel_for(second, "ROUGHNESS")
prop.value, prop.opacity, prop.blend_mode = 0.4, 0.5, "MULTIPLY"
value = pixel("ROUGHNESS")
assert np.isclose(value, 0.37), value
open_painter.unregister()
print(
    "PHASE 12 PASS: independent channel blends/opacity, isolated and passthrough folder backdrops, masks, dirty routing, fill sampling/hardness, save/reload, duplication and smart-material roundtrip"
)
