"""Material Aging (0.10.0): the history drives a nondestructive folder that stays the artist's.

The engine builds the "Material Aging" folder from the simulated state, re-drives
only the values it owns when any setting moves (age, environment, use, care,
events, seed, art direction), keeps what the artist changed inside it, rebuilds
when the construction changes the roster, follows a keyframed age, and travels
with the material document, the set duplicate and undo.
"""
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import open_painter
from open_painter.aging import engine, model
from open_painter.core import image_manager as images, layer_stack as stack, texture_set_manager as sets
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.smart_materials import composer

open_painter.register()
scene = bpy.context.scene
N = 96


def bake_synthetic(ts):
    u, v = np.meshgrid((np.arange(32) + .5) / 32, (np.arange(32) + .5) / 32)
    for kind in ("CURVATURE", "AO", "POSITION", "WORLD_NORMAL"):
        entry = ts.baked_maps.add()
        entry.kind = kind
        entry.image = images.create(f"{ts.name} {kind}", 32)
        value = np.ones((32, 32, 4), np.float32)
        if kind == "CURVATURE":
            value[..., :3] = np.clip((.16 - np.abs(u - .5)) * 8, 0, 1)[..., None]
        elif kind == "AO":
            value[..., :3] = (.25 + .75 * v)[..., None]
        elif kind == "POSITION":
            value[..., 0], value[..., 1], value[..., 2] = u, v, v
        else:
            z = 2 * v - 1
            value[..., 0] = .5 + .5 * np.sqrt(np.maximum(0, 1 - z * z))
            value[..., 1], value[..., 2] = .5, v
        images.write(entry.image, value)


def cpu(ts, channel="BASE_COLOR"):
    steps = LayerEvaluator(scene).channel_steps(ts.id, channel, preview_resolution=N)
    try:
        while True:
            next(steps)
    except StopIteration as done:
        return images.pixels(done.value)[..., :3].copy()


def opacities(ts):
    folder = engine.folder_of(ts)
    return {layer.aging_role: round(layer.opacity, 4) for layer in engine.members(ts, folder)}


ts = sets.create(scene, "Aged prop", 512)
aging = ts.aging
assert aging.material == "CARBON_STEEL" and aging.primer == "EPOXY_PRIMER" and aging.paint == "POLYURETHANE_PAINT"
assert aging.environment == "OUTDOOR_URBAN" and aging.age_years == 10 and aging.live
assert "OP_PT_native_aging" in dir(bpy.types) and hasattr(bpy.ops.open_painter, "aging")
assert "OP_OT_material_state" not in dir(bpy.types), "Age Response must be gone"

# The prediction needs no layers and no maps; the folder needs mesh maps.
prediction = engine.prediction(ts)
assert prediction["label"] == model.label(prediction["state"], prediction["construction"])
assert dict(prediction["directed"])["Coating loss"] > 0
assert engine.missing_maps(ts) == ["AO", "CURVATURE"], engine.missing_maps(ts)
before = (len(ts.layers), len(bpy.data.images))
try:
    bpy.ops.open_painter.aging(action="APPLY")
    raise AssertionError("the folder was built without mesh maps")
except RuntimeError as error:
    assert "Bake Mesh Maps first" in str(error), error
assert (len(ts.layers), len(bpy.data.images)) == before and not aging.folder_id
bake_synthetic(ts)
assert engine.missing_maps(ts) == []

# Age Material builds the folder in causal order, tagged by role, isolated, nothing else touched.
sentinel = stack.add(ts, "FILL", "Artist base")
stack.channel_for(sentinel, "BASE_COLOR").color = (.1, .2, .3, 1)
sentinel_id = sentinel.id
bpy.ops.open_painter.aging(action="APPLY")
folder = engine.folder_of(ts)
assert folder is not None and folder.name == engine.FOLDER_NAME and folder.group_mode == "ISOLATED"
folder_id = folder.id
roles = engine.roles_of(ts, folder)
assert list(roles)[:4] == ["substrate", "oxidation", "primer", "paint"], list(roles)
assert set(roles) >= {"corrosion", "cracking", "dust", "soil", "handling_polish"}
assert all(layer.type == "FILL" and layer.parent_id == folder_id for layer in roles.values())


def role(ts, name):
    """Layers are re-resolved by id: collection edits invalidate earlier references."""
    return engine.roles_of(ts, engine.folder_of(ts))[name]
assert next(l for l in ts.layers if l.id == sentinel_id).name == "Artist base"
stored = json.loads(aging.state_data)
assert stored["years"] == 10 and stored["overrides"] == [] and stored["resolved"]["paint:opacity"] == 1.0
print("MATERIAL_AGING build", len(roles), "roles", prediction["label"], flush=True)

# Age is a nondestructive parameter: 0 years is the artist's paint; 25 coastal years are not.
aging.age_years = 0
assert opacities(ts)["corrosion"] == 0 and opacities(ts)["dust"] == 0
new_color = cpu(ts)
paint = np.array(aging.paint_color, np.float32)
new_deviation = float(np.abs(new_color - paint).mean())
assert new_deviation < .03, new_deviation
assert engine.prediction(ts)["label"] == "PRISTINE"
aging.age_years = 25
aging.environment = "COASTAL"
assert aging.salt > .9 and abs(aging.humidity - .8) < 1e-6, "the preset expands into the exposure sliders"
aging.usage = "NONE"
aging.maintenance = "NONE"
old_color = cpu(ts)
old_deviation = float(np.abs(old_color - paint).mean())
assert old_deviation > .1 and old_deviation > 4 * new_deviation, (old_deviation, new_deviation)
assert engine.prediction(ts)["label"] == "DERELICT"
old_roughness = cpu(ts, "ROUGHNESS")[..., 0]
assert old_roughness.mean() > .5
assert opacities(ts)["corrosion"] > .8 and opacities(ts)["salt"] > .2
assert json.loads(aging.state_data)["years"] == 25
# The same years indoors are a different object.
aging.environment = "INDOOR"
indoor = opacities(ts)
assert indoor["corrosion"] < .1 and indoor["salt"] == 0 and indoor["water_staining"] < .05
assert engine.prediction(ts)["label"] in {"LIGHTLY USED", "WEATHERED"}, engine.prediction(ts)["label"]
# A slider moved off the preset makes the exposure custom; the preset can be chosen again.
aging.salt = .5
assert aging.environment == "CUSTOM" and opacities(ts)["salt"] > 0
aging.environment = "INDOOR"
assert aging.salt == 0 and aging.environment == "INDOOR"

# Overrides: a value the artist moved inside the folder is theirs until Reset Art.
aging.environment = "COASTAL"
role(ts, "corrosion").opacity = .123
stack.channel_for(role(ts, "paint"), "BASE_COLOR").color = (.9, .1, .1, 1)
aging.age_years = 30
assert abs(role(ts, "corrosion").opacity - .123) < 1e-6
assert abs(stack.channel_for(role(ts, "paint"), "BASE_COLOR").color[0] - .9) < 1e-6
assert set(json.loads(aging.state_data)["overrides"]) == {"corrosion:opacity", "paint:BASE_COLOR.color"}
assert opacities(ts)["dust"] == round(role(ts, "dust").opacity, 4)   # everything else still follows
bpy.ops.open_painter.aging(action="RESET_ART")
assert json.loads(aging.state_data)["overrides"] == []
assert role(ts, "corrosion").opacity > .8
assert stack.channel_for(role(ts, "paint"), "BASE_COLOR").color[0] < .9
# A layer the artist adds inside the folder is theirs and survives re-drives.
mine = stack.add(ts, "FILL", "My decal", folder_id)
mine_id = mine.id
aging.age_years = 12
assert any(l.id == mine_id for l in ts.layers) and engine.folder_of(ts).id == folder_id

# Art direction multiplies after the physics; the readout shows both.
physical = dict(engine.prediction(ts)["physical"])
aging.direct_corrosion = 0
assert opacities(ts)["corrosion"] == 0 and opacities(ts)["oxidation"] == 0
directed = dict(engine.prediction(ts)["directed"])
assert directed["Corrosion at damage"] == 0 and physical["Corrosion at damage"] > 0
assert abs(directed["Dust"] - physical["Dust"]) < 1e-9
aging.direct_corrosion = 1
aging.direct_dirt = 3
assert opacities(ts)["dust"] >= min(1.0, round(physical["Dust"] * 3, 4)) - 1e-3
aging.direct_dirt = 1

# Use and care are not age: usage sliders, maintenance events, environment history.
base = opacities(ts)
aging.usage = "ABUSED"
assert opacities(ts)["handling_polish"] > base["handling_polish"] and opacities(ts)["impact"] > base["impact"]
assert abs(aging.use_impact - .85) < 1e-6
aging.use_impact = .2
assert aging.usage == "CUSTOM"
aging.usage = "NONE"
assert opacities(ts)["handling_polish"] == 0
untouched = opacities(ts)
bpy.ops.open_painter.aging(action="ADD_EVENT")
event = aging.events[0]
assert event.year == aging.age_years, "a new event has just happened"
event.kind = "RUST_REMOVAL"
assert opacities(ts)["corrosion"] < untouched["corrosion"] * .5 and opacities(ts)["oxidation"] < untouched["oxidation"] * .5
event.year = aging.age_years + 10
assert opacities(ts) == untouched, "an event after the age has not happened"
event.kind, event.year = "REPAINT", 11.0
repainted = opacities(ts)
assert repainted["cracking"] < base["cracking"] and json.loads(aging.state_data)["years"] == 12
bpy.ops.open_painter.aging(action="REMOVE_EVENT")
assert len(aging.events) == 0
coastal12 = opacities(ts)
assert coastal12["cracking"] == base["cracking"]
bpy.ops.open_painter.aging(action="ADD_PERIOD")
bpy.ops.open_painter.aging(action="ADD_PERIOD")
assert aging.use_history and len(aging.periods) == 2
aging.periods[0].environment, aging.periods[0].years = "INDOOR", 9
aging.periods[1].environment, aging.periods[1].years = "COASTAL", 3
history = opacities(ts)
assert 0 < history["corrosion"] < coastal12["corrosion"], (history["corrosion"], coastal12["corrosion"])
segments = engine.segments_of(aging)
assert abs(sum(s.years for s in segments) - aging.age_years) < 1e-6 and [s.label for s in segments] == ["INDOOR", "COASTAL"]
aging.use_history = False

# Randomize History: another history of the same kind.
seed = aging.seed
bpy.ops.open_painter.aging(action="RANDOMIZE")
assert aging.seed != seed
randomized = opacities(ts)
assert randomized != history and engine.prediction(ts)["label"] == engine.prediction(ts)["label"]
aging.seed = seed
assert opacities(ts) == coastal12

# The construction changes the roster: the folder is rebuilt; the artist's layer inside it goes with it.
aging.material = "OAK"
aging.primer = aging.paint = "NONE"
oak = engine.folder_of(ts)
assert oak is not None and oak.id != folder_id
assert "wood_greying" in engine.roles_of(ts, oak) and "oxidation" not in engine.roles_of(ts, oak)
assert not any(l.id == mine_id for l in ts.layers)
assert json.loads(aging.state_data)["overrides"] == []
aging.material, aging.primer, aging.paint = "CARBON_STEEL", "EPOXY_PRIMER", "POLYURETHANE_PAINT"
folder_id = engine.folder_of(ts).id
assert list(engine.roles_of(ts, engine.folder_of(ts)))[:4] == ["substrate", "oxidation", "primer", "paint"]
# Live off: settings change the prediction, not the layers, until Age Material.
aging.live = False
frozen = opacities(ts)
aging.age_years = 40
assert opacities(ts) == frozen and json.loads(aging.state_data)["years"] == 12
bpy.ops.open_painter.aging(action="APPLY")
assert json.loads(aging.state_data)["years"] == 40 and opacities(ts) != frozen
aging.live = True
aging.age_years = 12

# A smart material says what it is: the construction follows the recipe, the history stays.
from open_painter.aging import recipes
from open_painter.smart_materials import composer
composer.apply(ts, "Canvas Fabric")
ts = next(s for s in scene.open_painter.texture_sets if s.name == "Aged prop")
aging = ts.aging
assert (aging.material, aging.primer, aging.paint) == ("CANVAS", "NONE", "NONE") and aging.environment == "COASTAL" and aging.age_years == 12
roles_now = list(engine.roles_of(ts, engine.folder_of(ts)))
assert "oxidation" not in roles_now and roles_now[:2] == ["substrate", "micro_scratching"], roles_now
assert engine.folder_of(ts).id != folder_id, "another family rebuilds the folder"
assert dict(engine.prediction(ts)["directed"]).get("Fraying") is not None
composer.apply(ts, "Automotive Painted Metal")
ts = next(s for s in scene.open_painter.texture_sets if s.name == "Aged prop")
aging = ts.aging
assert (aging.material, aging.primer, aging.paint, aging.topcoat) == ("CARBON_STEEL", "EPOXY_PRIMER", "POLYURETHANE_PAINT", "CLEAR_COAT")
assert abs(aging.paint_color[0] - .31) < 1e-3 and "topcoat" in engine.roles_of(ts, engine.folder_of(ts))
fresh = sets.create(scene, "Fresh set", 512)
bake_synthetic(fresh)
composer.apply(fresh, "Heavy Chipped Paint")
fresh = next(s for s in scene.open_painter.texture_sets if s.name == "Fresh set")
assert fresh.aging.material == "CARBON_STEEL" and fresh.aging.coating_condition < .5, "a recipe that depicts damage starts worn"
assert (fresh.aging.environment, fresh.aging.age_years, fresh.aging.usage) == ("INDUSTRIAL", 20, "HEAVY"), "a set not aged yet takes the recipe's history"
from open_painter.ui.dock import assets as shelf_assets
scene.open_painter.active_texture_set = list(scene.open_painter.texture_sets).index(fresh)
shelf_assets.apply(bpy.context, next(e for e in shelf_assets.MATERIALS if e[0] == "Copper"))
fresh = next(s for s in scene.open_painter.texture_sets if s.name == "Fresh set")
assert fresh.aging.material == "COPPER" and fresh.aging.paint == "NONE"
sets.remove(scene, list(scene.open_painter.texture_sets).index(fresh))
ts = next(s for s in scene.open_painter.texture_sets if s.name == "Aged prop")
aging = ts.aging
scene.open_painter.active_texture_set = list(scene.open_painter.texture_sets).index(ts)
# Back to the plain painted steel the rest of the suite reads.
aging.topcoat = "NONE"
aging.paint_color = (0.18, 0.32, 0.58)
folder_id = engine.folder_of(ts).id
for name in ("Canvas Fabric", "Automotive Painted Metal"):
    stack.remove_tree(ts, next(l.id for l in ts.layers if l.name == name and l.type == "FOLDER"))
assert engine.folder_of(ts) is not None and engine.folder_of(ts).id == folder_id

# A keyframed age follows the timeline through the dependency refresh.
from open_painter.evaluation import dependencies
path = f'open_painter.texture_sets[{list(scene.open_painter.texture_sets).index(ts)}].aging.age_years'
aging.age_years = 0
scene.keyframe_insert(path, frame=1)
aging.age_years = 30
scene.keyframe_insert(path, frame=30)
scene.frame_set(1)
assert abs(aging.age_years) < 1e-4, aging.age_years
dependencies.refresh(scene)
assert json.loads(aging.state_data)["years"] < 1e-4 and opacities(ts)["corrosion"] == 0
scene.frame_set(30)
dependencies.refresh(scene)
assert abs(json.loads(aging.state_data)["years"] - 30) < 1e-4 and opacities(ts)["corrosion"] > .5
scene.frame_set(15)
dependencies.refresh(scene)
mid = json.loads(aging.state_data)["years"]
assert 0 < mid < 30 and 0 < opacities(ts)["corrosion"] < 1
scene.animation_data_clear()
aging.age_years = 12

# Portability: the document carries the history and the driven-value record.
role(ts, "corrosion").opacity = .222
aging.age_years = 13
assert abs(role(ts, "corrosion").opacity - .222) < 1e-6
with TemporaryDirectory(prefix="op-aging-") as directory:
    from open_painter.smart_materials.serializer import save
    from open_painter.smart_materials.loader import apply
    saved = save(ts, Path(directory) / "aged.openpaintmaterial")
    imported = sets.create(scene, "Imported aged prop", 512)
    bake_synthetic(imported)
    apply(imported, saved)
    twin = imported.aging
    assert twin.material == "CARBON_STEEL" and twin.age_years == 13 and twin.environment == "COASTAL"
    assert engine.folder_of(imported) is not None and engine.folder_of(imported).id != folder_id
    assert set(json.loads(twin.state_data)["overrides"]) == {"corrosion:opacity"}
    assert abs(role(imported, "corrosion").opacity - .222) < 1e-6
    twin.age_years = 20
    assert abs(role(imported, "corrosion").opacity - .222) < 1e-6
    assert engine.folder_of(ts).id == folder_id and json.loads(aging.state_data)["years"] == 13
    # A module document of another folder carries no aging block.
    other = stack.add(ts, "FOLDER", "Plain folder")
    other_id = other.id
    stack.add(ts, "FILL", "Plain fill", other_id)
    from open_painter.smart_materials import modules
    module_path, _ = modules.save_module(bpy.context, ts, next(l for l in ts.layers if l.id == other_id),
                                         Path(directory) / "Plain.openpaintmaterial", "Plain")
    from open_painter.smart_materials.format import read
    assert "aging" not in read(module_path)[0] and "aging" in read(saved)[0]
    stack.remove_tree(ts, other_id)

# Duplicate: the copy ages on its own.
copy = sets.duplicate(scene, list(scene.open_painter.texture_sets).index(ts))
assert copy.aging.folder_id and copy.aging.folder_id != aging.folder_id and engine.folder_of(copy) is not None
assert copy.aging.age_years == 13 and set(json.loads(copy.aging.state_data)["overrides"]) == {"corrosion:opacity"}
copy.aging.age_years = 3
assert json.loads(aging.state_data)["years"] == 13 and json.loads(copy.aging.state_data)["years"] == 3

# Undo restores the settings with the layers.
bpy.ops.ed.undo_push(message="before aging edit")
aging.age_years = 45
before_undo = opacities(ts)
bpy.ops.ed.undo_push(message="aging edit")
bpy.ops.ed.undo()
scene = bpy.context.scene   # undo swaps the datablocks
ts = next(s for s in scene.open_painter.texture_sets if s.name == "Aged prop")
aging = ts.aging
assert aging.age_years == 13 and opacities(ts) != before_undo and engine.folder_of(ts) is not None
assert json.loads(aging.state_data)["years"] == 13

# Remove drops the folder and forgets the record; the prediction still works.
scene.open_painter.active_texture_set = list(scene.open_painter.texture_sets).index(ts)
copy = next(s for s in scene.open_painter.texture_sets if s.name == "Aged prop Copy")
assert engine.folder_of(copy) is not None
bpy.ops.open_painter.aging(action="REMOVE")
assert engine.folder_of(copy) is not None, "another set's folder is not the active set's"
assert engine.folder_of(ts) is None and not aging.folder_id and not aging.state_data
assert not any(layer.aging_role for layer in ts.layers)
assert engine.prediction(ts)["label"]
aging.age_years = 50   # no folder: nothing to drive, no error
assert engine.folder_of(ts) is None

# A set with only the default bakes (AO, Curvature) still ages: dust, streaks, growth and soil
# fall back to their procedural bases; the geometry masks join on Rebuild once the maps exist.
two = sets.create(scene, "Two-map prop", 512)
bake_synthetic(two)
for index in reversed(range(len(two.baked_maps))):
    if two.baked_maps[index].kind in {"POSITION", "WORLD_NORMAL"}:
        two.baked_maps.remove(index)
assert engine.missing_maps(two) == [] and engine.optional_maps(two) == ["POSITION", "WORLD_NORMAL"]
two.aging.environment = "TROPICAL"
two.aging.age_years = 20
scene.open_painter.active_texture_set = list(scene.open_painter.texture_sets).index(two)
bpy.ops.open_painter.aging(action="APPLY")
two = next(s for s in scene.open_painter.texture_sets if s.name == "Two-map prop")
kinds = lambda layer: [(m.source, m.generator if m.source == "GENERATOR" else "") for m in layer.masks]
assert kinds(role(two, "dust")) == [("PROCEDURAL", "")] and role(two, "dust").masks[0].procedural == "WEATHERING_FRONT", kinds(role(two, "dust"))
assert [k for k, _ in kinds(role(two, "water_staining"))] == ["PROCEDURAL"]
assert [g for s_, g in kinds(role(two, "soil"))] == ["", "", "CAVITY_DIRT"] and len(role(two, "soil").masks) == 3, kinds(role(two, "soil"))
assert engine.rebuild_pending(two) == []
black = role(two, "dust").masks[0].black
two.aging.age_years = 0
assert role(two, "dust").masks[0].black > black, "the procedural base still opens and closes with the state"
assert engine.mask_pairs(role(two, "dust"), dict(zip(engine._document(two)["roles"], engine._document(two)["layers"]))["dust"]) == \
    [(0, engine._document(two)["layers"][engine._document(two)["roles"].index("dust")]["mask"])]
two.aging.age_years = 20
bake_synthetic(two)   # adds Position and World Normal (the AO / Curvature entries are duplicates, harmless)
assert engine.optional_maps(two) == [] and set(engine.rebuild_pending(two)) == {"DUST", "RUNOFF", "POSITION"}, engine.rebuild_pending(two)
bpy.ops.open_painter.aging(action="REBUILD")
two = next(s for s in scene.open_painter.texture_sets if s.name == "Two-map prop")
assert engine.rebuild_pending(two) == []
assert [g for s_, g in kinds(role(two, "dust"))] == ["", "DUST"] and [g for s_, g in kinds(role(two, "soil"))] == ["", "", "CAVITY_DIRT", "POSITION"]
assert [g for s_, g in kinds(role(two, "water_staining"))] == ["", "RUNOFF"]
drips_amount = role(two, "water_staining").masks[1].amount
two.aging.age_years = 5
assert role(two, "water_staining").masks[1].amount < drips_amount, "the geometry mask is driven once it exists"

# Pigment variation is independent of the fine relief and roughness field.
assert stack.channel_for(role(two, 'corrosion'), 'BASE_COLOR').procedural == 'WEATHERING_FRONT'
assert stack.channel_for(role(two, 'corrosion'), 'HEIGHT').procedural == 'CORROSION_GRAIN'
assert stack.channel_for(role(two, 'corrosion'), 'ROUGHNESS').procedural == 'CORROSION_GRAIN'

# New recipes keep paint/primer damage nested and corrosion off intact primer.
from open_painter.evaluation.mask_eval import evaluate_masks
for years in (10, 20, 30):
    two.aging.age_years = years
    values = {name: evaluate_masks(two, role(two, name), 64, 0, 64, {})
              for name in ('paint', 'primer', 'corrosion')}
    assert np.max(values['paint'] - values['primer']) < 1e-5
    assert np.max(values['corrosion'] - (1 - values['primer'])) < 1e-5
old_seed = role(two, 'paint').masks[1].seed
two.aging.seed += 1
assert role(two, 'paint').masks[1].seed != old_seed
assert role(two, 'paint').masks[1].seed == role(two, 'primer').masks[1].seed

# Saved v1 folders retain their original recipe and artist content until Rebuild.
from open_painter.aging import document_v1
legacy = sets.create(scene, 'Existing aging project', 512)
bake_synthetic(legacy)
result = engine.prediction(legacy)
old_doc = document_v1.build(result['state'], result['construction'], seed=legacy.aging.seed)
folder = engine._build(legacy, old_doc, legacy.aging.seed)
legacy.aging.folder_id = folder.id
legacy.aging.state_data = json.dumps({'version': 1})
artist = stack.add(legacy, 'FILL', 'Preserved artist paint', folder.id)
artist_id = artist.id
engine.apply(legacy)
assert engine.recipe_version(legacy) == 1
legacy.aging.age_years = 12
assert engine._document(legacy)['version'] == 1
assert role(legacy, 'paint').masks[1].procedural == 'GRUNGE_HEAVY'
assert any(l.id == artist_id for l in legacy.layers)
engine.apply(legacy, rebuild=True)
assert engine.recipe_version(legacy) == 2
assert role(legacy, 'paint').masks[1].procedural == 'WEATHERING_FRONT'
assert role(legacy, 'water_staining').masks[1].generator == 'RUNOFF'

open_painter.unregister()
print("MATERIAL_AGING_PASS", flush=True)
