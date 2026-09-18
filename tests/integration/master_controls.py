"""Master controls of every built-in smart material: authored, bound, effective and durable.

For each of the fifty recipes: every control resolves to a real property of the
instance; labels are artist-facing; moving a control between its extremes changes the
composited outputs (and a depth control lowers height, an inverted one lowers its mask
amount); renaming and reordering the bound layers keeps the bindings; a duplicate binds to
its own layers only; a deleted target reports a broken binding; saving and reopening keeps
the bindings and the values; the computed vectors never enter snapshots or documents.

Visual correctness is not claimed here: scripts/render_master_controls.py renders the
extremes for inspection (docs/master-controls.md records the outcome).
"""
import json
import sys
import tempfile
from pathlib import Path

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests/integration"))
import open_painter
import ui_fixture
from open_painter.authoring import document
from open_painter.core import image_manager as images, layer_stack as stack, texture_set_manager as sets
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.smart_materials import composer, controls
from open_painter.smart_materials.catalog import CATALOG

GENERIC = {"damage", "age", "wear", "amount", "mask amount", "noise amount", "layer"}
IMPLEMENTATION_WORDS = ("Mask Amount", "Noise Amount", "Layer ", "procedural", "Levels", "Opacity")

open_painter.register()
scene = bpy.context.scene
bpy.ops.mesh.primitive_plane_add(size=2)
plane = bpy.context.object
ts = sets.create(scene, "Controls", 512)
sets.assign(scene, ts, plane)
bpy.context.view_layer.update()
ui_fixture.bake_synthetic(ts)
evaluator = LayerEvaluator(scene)


def folder_of(folder_id):
    return next(item for item in ts.layers if item.id == folder_id)


def get(binding):
    if binding.control.computed:
        return list(getattr(binding.owner, binding.field))[binding.index]
    value = getattr(binding.owner, binding.field)
    return tuple(value) if binding.control.kind == "color" else value   # a copy, not the live array


def put(binding, value):
    if binding.control.computed:
        vector = list(getattr(binding.owner, binding.field))
        vector[binding.index] = value
        setattr(binding.owner, binding.field, vector)
    else:
        setattr(binding.owner, binding.field, value)


def extremes(binding):
    control = binding.control
    if control.kind == "color":
        return (0.9, 0.05, 0.6, 1), (0.05, 0.9, 0.2, 1)
    if control.kind == "factor":
        return 0.0, 1.0
    if control.kind == "multiplier":
        return 0.25, 3.0
    prop = binding.owner.bl_rna.properties[binding.field]
    return prop.hard_min, prop.hard_max


def maps():
    evaluator.evaluate_texture_set(ts.id, force=True)
    return {out.channel: images.pixels(out.image)[..., :3].copy() for out in ts.outputs
            if out.channel in {"BASE_COLOR", "ROUGHNESS", "METALLIC", "HEIGHT"}}


# ---- every recipe: authored, resolved, effective -------------------------------------------------
summary = {}
for name in CATALOG:
    assert name in controls.CONTROLS, f"{name} has no authored master controls"
    authored = controls.CONTROLS[name]
    assert 4 <= len(authored) <= 8, (name, len(authored))
    labels = [control.label for control in authored]
    assert len(set(labels)) == len(labels), (name, labels)
    for label in labels:
        assert label.lower() not in GENERIC, (name, label)
        assert not any(word in label for word in IMPLEMENTATION_WORDS), (name, label)
        assert "Loss Black" not in label and "Loss White" not in label and "/" not in label, (name, label)
    for control in authored:
        # A generator amount stays a direct binding only where the generator is none at 0.
        if control.field == "amount" and not control.computed:
            spec = next(item for item in CATALOG[name]["layers"] if item["name"] == control.layer)
            kind = composer.masks_for(spec)[control.mask]["kind"]
            assert kind in {"EDGE_WEAR", "CHIPS", "DUST", "WORLD_NORMAL"}, (name, control.label, kind)
    ts.layers.clear()
    ts.active_layer = -1
    folder_id = composer.apply(ts, name).id
    folder = folder_of(folder_id)
    digest = document.digest(ts)
    bound = controls.bindings(ts, folder)
    assert all(binding.ok for binding in bound), (name, [(b.control.label, b.reason) for b in bound if not b.ok])
    assert document.digest(ts) == digest, f"{name}: resolving bindings changed the document"
    assert "master_factor" not in stack.snapshot(folder) and "master_scale" not in stack.snapshot(folder)
    baseline = maps()
    moved = []
    for binding in bound:
        control = binding.control
        before = get(binding)
        low, high = extremes(binding)
        put(binding, low)
        at_low = maps()
        if control.mode == "invert":
            owner, member, _ = binding.members[0]
            amount_low = getattr(owner, member.field)
        put(binding, high)
        at_high = maps()
        if control.mode == "invert":
            assert getattr(owner, member.field) < amount_low, (name, control.label, "inverted control must lower the amount")
        if control.mode == "depth":
            assert at_high["HEIGHT"].mean() < at_low["HEIGHT"].mean(), (name, control.label)
        if control.mode == "coverage":
            # 0 shows none of the layer (the composite equals the layer hidden); the authored value
            # restores the authored opacity and amount; 1 keeps the opacity and spreads the generator.
            layer_owner, mask_owner = binding.members[0][0], binding.members[1][0]
            assert abs(mask_owner.amount - 1) < 1e-6 and abs(layer_owner.opacity - binding.members[0][2]) < 1e-6
            put(binding, 0.0)
            assert layer_owner.opacity == 0 and mask_owner.amount == 0
            at_zero = maps()
            layer_owner.visible = False
            put(binding, before)
            hidden = maps()
            layer_owner.visible = True
            for channel in at_zero:
                assert np.abs(at_zero[channel] - hidden[channel]).max() < 1e-5, (name, control.label, channel, "0 must be none")
            assert abs(layer_owner.opacity - binding.members[0][2]) < 1e-6 and abs(mask_owner.amount - binding.members[1][2]) < 1e-6
            assert np.abs(maps()["BASE_COLOR"] - baseline["BASE_COLOR"]).max() < 1e-5, (name, control.label, "authored restored")
        if control.mode == "levels_center":
            # At 1 the ramp sits at the bottom of the image range (all lost), its width kept whole.
            owner = binding.members[0][0]
            assert owner.black < 0.01 and abs((owner.white - owner.black) - 0.08) < 1e-3, (name, control.label, owner.black, owner.white)
            assert at_high["BASE_COLOR"].mean() != at_low["BASE_COLOR"].mean()
        if control.channel == "ROUGHNESS" and control.field == "value":
            assert at_high["ROUGHNESS"].mean() > at_low["ROUGHNESS"].mean(), (name, control.label)
        change = max(float(np.abs(at_high[channel] - at_low[channel]).mean()) for channel in baseline)
        if control.scope == "set":
            # A surface field (anisotropy) reaches the output material, not the composited textures.
            assert abs(getattr(ts, control.field) - high) < 1e-6, (name, control.label)
        else:
            assert change > 1e-5, (name, control.label, "moving the control changed nothing")
        moved.append((control.label, round(change, 5)))
        put(binding, before)
        restored = get(binding)
        if control.kind == "color":
            assert tuple(restored) == tuple(before)
        else:
            assert abs(restored - before) < 1e-4, (name, control.label, before, restored)
    summary[name] = moved
    print("MASTER_CONTROLS", name, "PASS", len(bound), flush=True)

# ---- stability: rename, reorder, duplicate, delete, save and reopen ----------------------------------
ts.layers.clear()
ts.active_layer = -1
folder_id = composer.apply(ts, "Cast Iron").id
folder = folder_of(folder_id)
by_label = {b.control.label: b for b in controls.bindings(ts, folder)}
skin = by_label["Oxide Coverage"].layer
skin_id = skin.id
skin.name = "Renamed skin"
assert controls.resolve(ts, folder, by_label["Oxide Coverage"].control).ok, "renaming keeps the binding"
# Reorder: move the oxide skin to the top of the material (two steps: it stays inside the folder).
ts.active_layer = stack.index_of(ts, skin_id)
for _ in range(2):
    assert bpy.ops.open_painter.layer(action="UP") == {"FINISHED"}
folder = folder_of(folder_id)
skin = next(item for item in ts.layers if item.id == skin_id)
assert skin.parent_id == folder_id and controls.components(ts, folder)[0].id == skin_id, "moved to the top"
after = {b.control.label: b for b in controls.bindings(ts, folder)}
assert all(b.ok for b in after.values()), [(l, b.reason) for l, b in after.items() if not b.ok]
assert after["Oxide Coverage"].layer.id == skin_id, "reordering keeps the binding"
put(after["Oxide Coverage"], 0.35)
put(after["Surface Relief"], 1.6)
after["Casting Roughness"].owner.value = 0.71
print("MASTER_CONTROLS rename/reorder: PASS", flush=True)

# Duplicate: the copy binds to its own layers; edits stay apart.
ts.active_layer = stack.index_of(ts, folder_id)
assert bpy.ops.open_painter.layer(action="DUPLICATE") == {"FINISHED"}
folder = folder_of(folder_id)
copy = next(item for item in ts.layers if item.type == "FOLDER" and item.id != folder_id and item.module_source == folder.module_source)
assert copy.module_instance and copy.module_instance != folder.module_instance
copied = {b.control.label: b for b in controls.bindings(ts, copy)}
original = {b.control.label: b for b in controls.bindings(ts, folder)}
assert all(b.ok for b in copied.values()), [(l, b.reason) for l, b in copied.items() if not b.ok]
for label, binding in copied.items():
    assert binding.layer.id.startswith(copy.module_instance + "."), (label, binding.layer.id)
    assert binding.layer.id != original[label].layer.id
assert abs(get(copied["Oxide Coverage"]) - 0.35) < 1e-6 and abs(get(copied["Surface Relief"]) - 1.6) < 1e-6
put(copied["Oxide Coverage"], 0.8)
copied["Casting Roughness"].owner.value = 0.2
assert abs(get(original["Oxide Coverage"]) - 0.35) < 1e-6 and abs(original["Casting Roughness"].owner.value - 0.71) < 1e-6
assert abs(get(copied["Oxide Coverage"]) - 0.8) < 1e-6
print("MASTER_CONTROLS duplicate: PASS", flush=True)

# Delete a bound layer: that control is unavailable with a reason, the rest keep working.
ts.active_layer = stack.index_of(ts, copied["Rust Speckling"].layer.id)
assert bpy.ops.open_painter.layer(action="DELETE") == {"FINISHED"}
copy = next(item for item in ts.layers if item.id == copy.id)
broken = {b.control.label: b for b in controls.bindings(ts, copy)}
assert not broken["Rust Speckling"].ok and "deleted" in broken["Rust Speckling"].reason, broken["Rust Speckling"].reason
assert broken["Oxide Coverage"].ok and broken["Casting Grain"].ok
values = list(copy.master_factor)
assert abs(values[broken["Oxide Coverage"].index] - 0.8) < 1e-6
print("MASTER_CONTROLS delete: PASS", flush=True)

# Save and reopen: bindings and values survive the file.
path = Path(tempfile.mkdtemp()) / "master-controls.blend"
bpy.ops.wm.save_as_mainfile(filepath=str(path))
bpy.ops.wm.open_mainfile(filepath=str(path))
scene = bpy.context.scene
ts = next(item for item in scene.open_painter.texture_sets if item.name == "Controls")
folder = next(item for item in ts.layers if item.id == folder_id)
reopened = {b.control.label: b for b in controls.bindings(ts, folder)}
assert all(b.ok for b in reopened.values()), [(l, b.reason) for l, b in reopened.items() if not b.ok]
assert abs(get(reopened["Oxide Coverage"]) - 0.35) < 1e-6
assert abs(get(reopened["Surface Relief"]) - 1.6) < 1e-6
assert abs(reopened["Casting Roughness"].owner.value - 0.71) < 1e-6
assert reopened["Oxide Coverage"].layer.name == "Renamed skin"
print("MASTER_CONTROLS save/reopen: PASS", flush=True)

(ROOT / ".blender-test").mkdir(exist_ok=True)
(ROOT / ".blender-test/master-controls.json").write_text(json.dumps(summary, indent=1))
print("MASTER_CONTROLS_PASS", flush=True)
