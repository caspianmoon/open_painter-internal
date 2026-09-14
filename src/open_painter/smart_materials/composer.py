"""Compose reusable catalog entries into editable texture layers atomically.

No texture evaluator, baker, shader graph or scene mutation is run here. The
normal output evaluator consumes the stored layer data after a successful edit.
"""

import math
from zlib import crc32

import bpy

from ..core import layer_stack as stack
from ..texture_engine.procedurals import ALIASES, CATALOG as PROCEDURALS
from ..utils.ids import new_id
from .catalog import CATALOG, DIRT, DUST, OIL, PATINA, RUST, STEEL, layer, mask
from . import generated_sources


MAP_REQUIREMENTS = {
    "EDGE_WEAR": {"CURVATURE"},
    "CHIPS": {"CURVATURE"},
    "CAVITY_DIRT": {"AO"},
    "RUST": {"AO"},
    "DUST": {"WORLD_NORMAL"},
    "WORLD_NORMAL": {"WORLD_NORMAL"},
    "POSITION": {"POSITION"},
    "DRIPS": {"POSITION", "AO"},
    "SCRATCHES": set(),
    "NOISE": set(),
}

# Effects add one independently masked layer rather than a complete material.
# Channel-only polish/scratches preserve the color and metalness below them.
EFFECTS = {
    "Rust": layer("Rust", RUST, .92, procedural="RUST_FLAKES", scale=31, detail=.8,
                  height=.07, mask=mask("RUST", .4, 35)),
    "Dirt": layer("Dirt", DIRT, .92, procedural="GRUNGE", scale=29, detail=.7,
                  height=.024, mask=mask("CAVITY_DIRT", .45, 29)),
    "Dust": layer("Dust", DUST, .97, procedural="FBM", scale=45, detail=.45,
                  height=.009, mask=mask("DUST", .4, 45)),
    "Edge Wear": layer("Edge Wear", STEEL, .36, 1, procedural="BRUSHED", scale=100,
                       detail=.12, height=-.024, mask=mask("EDGE_WEAR", .36, 65)),
    "Chipping": layer("Chipping", STEEL, .51, 1, procedural="PITS", scale=48,
                      detail=.18, height=-.065, mask=mask("CHIPS", .38, 41)),
    "Scratches": {
        **layer("Scratches", STEEL, .62, procedural="SCRATCHES", scale=83,
                detail=.18, height=-.031, mask=mask("SCRATCHES", .22, 83)),
        "channels": ("ROUGHNESS", "HEIGHT"),
    },
    "Grease": layer("Grease", OIL, .21, procedural="GRUNGE", scale=17, detail=.6,
                    height=.032, mask=mask("CAVITY_DIRT", .48, 23)),
    "Water Stains": layer("Water Stains", (.13, .11, .068), .56,
                          procedural="STREAKS", scale=23, detail=.6,
                          mask=mask("DRIPS", .35, 23)),
    "Edge Polish": {
        **layer("Edge Polish", STEEL, .16, mask=mask("EDGE_WEAR", .3, 85)),
        "channels": ("ROUGHNESS",),
    },
    "Oil": layer("Oil", (.075, .055, .026), .11, procedural="FBM", scale=9,
                 detail=.35, mask=mask("NOISE", .28, 9)),
    "Fingerprints": {
        **layer("Fingerprints", (.3, .3, .3), .34, procedural="FINGERPRINTS", scale=7,
                detail=.65, mask=mask("NOISE", .17, 7)),
        "channels": ("ROUGHNESS",),
    },
    "Copper Patina": layer("Copper Patina", PATINA, .87, procedural="OXIDATION_SPOTS",
                           scale=35, detail=.75, height=.036,
                           mask=mask("CAVITY_DIRT", .43, 35)),
    "Soot": layer("Soot", (.018, .018, .017), .98, procedural="GRUNGE", scale=15,
                  detail=.55, mask=mask("NOISE", .38, 12)),
    "Wet Surface": {
        **layer("Wet Surface", (.2, .2, .2), .09, procedural="FBM", scale=8,
                detail=.12, mask=mask("NOISE", .5, 8)),
        "channels": ("ROUGHNESS",),
    },
    "Rust Streaks": layer("Rust Streaks", (.22, .051, .015), .81, procedural="STREAKS",
                          scale=27, detail=.7, mask=mask("DRIPS", .38, 27)),
    "Moss": layer("Moss", (.057, .11, .019), .95, procedural="FIBERS", scale=68,
                  detail=.75, height=.052, mask=mask("CAVITY_DIRT", .35, 39)),
}


def masks_for(spec):
    return ([spec["mask"]] if spec.get("mask") else []) + list(spec.get("mask_effects", ()))


class MissingMeshMaps(ValueError):
    def __init__(self, name, missing):
        self.material_name = name
        self.missing = tuple(sorted(missing))
        super().__init__("Bake Mesh Maps first: " + ", ".join(self.missing)
                         + " required for " + name + ".")


def requirements(recipe_or_name):
    """Required mesh maps, without touching Blender or authoring data."""
    data = CATALOG[recipe_or_name] if isinstance(recipe_or_name, str) else recipe_or_name
    result = set()
    for spec in data.get("layers", (data,)):
        for settings in masks_for(spec):
            result.update(MAP_REQUIREMENTS[settings["kind"]])
    return result


def validate(data):
    """Reject malformed recipes before allocating any layer or changing selection."""
    specs = data.get("layers", (data,))
    if not specs or len(specs) > 256:
        raise ValueError("A smart material must contain between 1 and 256 layers.")
    for spec in specs:
        if not isinstance(spec.get("name"), str) or not spec["name"].strip():
            raise ValueError("Every recipe layer requires a name.")
        color = spec.get("color", ())
        if len(color) != 3 or any(not math.isfinite(v) or not 0 <= v <= 1 for v in color):
            raise ValueError("Recipe colors must be finite linear RGB values between zero and one.")
        for key, lo, hi in (("roughness", 0, 1), ("metallic", 0, 1),
                            ("scale", .01, 512), ("detail", 0, 2), ("height", -1, 1)):
            value = spec.get(key, 0)
            if not math.isfinite(value) or not lo <= value <= hi:
                raise ValueError("Invalid recipe parameter: " + key)
        source = spec.get("procedural")
        if source and ALIASES.get(source, source) not in PROCEDURALS:
            raise ValueError("Unknown texture procedural: " + source)
        if "generated_source" in spec:
            generated_sources.validate(spec["generated_source"])
        selected = set(spec.get("channels", ("BASE_COLOR", "ROUGHNESS", "METALLIC", "HEIGHT")))
        if not selected <= {"BASE_COLOR", "ROUGHNESS", "METALLIC", "HEIGHT"}:
            raise ValueError("Unknown recipe texture channel.")
        for settings in masks_for(spec):
            if settings["kind"] not in MAP_REQUIREMENTS:
                raise ValueError("Unknown recipe mask: " + settings["kind"])
            for key, lo, hi in (("amount", 0, 1), ("scale", 1, 512)):
                value = settings[key]
                if not math.isfinite(value) or not lo <= value <= hi:
                    raise ValueError("Invalid recipe mask parameter: " + key)
            if not .01 <= settings.get("contrast", 3) <= 20:
                raise ValueError("Invalid recipe mask contrast.")
    for key, value in data.get("surface", {}).items():
        if key not in {"anisotropy", "coat_weight", "coat_roughness"}:
            raise ValueError("Unknown material surface parameter: " + key)
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("Invalid material surface parameter: " + key)


def preflight(ts, data, name):
    validate(data)
    available = {entry.kind for entry in ts.baked_maps
                 if entry.image and all(entry.image.size)}
    missing = requirements(data) - available
    if missing:
        raise MissingMeshMaps(name, missing)


def _seed(name, number, seed):
    return (crc32(name.encode("utf8")) + number * 104729 + int(seed)) & 0x7fffffff


def _fill(ts, spec, parent_id, seed):
    item = stack.add(ts, "FILL", spec["name"], parent_id)
    detail = spec.get("generated_source")
    detail_image = generated_sources.load(detail["id"]) if detail else None
    selected = set(spec.get("channels", ("BASE_COLOR", "ROUGHNESS", "METALLIC", "HEIGHT")))
    for prop in item.channels:
        prop.enabled = prop.channel in selected and (prop.channel != "HEIGHT" or "height" in spec)
        if prop.channel == "BASE_COLOR":
            prop.color = (*spec["color"], 1)
        elif prop.channel == "ROUGHNESS":
            prop.value = spec["roughness"]
        elif prop.channel == "METALLIC":
            prop.value = spec["metallic"]
        elif prop.channel == "HEIGHT":
            prop.value = .5
        if prop.enabled and prop.channel in {"BASE_COLOR", "ROUGHNESS", "HEIGHT"}:
            prop.procedural = ALIASES.get(spec.get("procedural"), spec.get("procedural", ""))
            prop.procedural_scale = spec["scale"]
            prop.procedural_seed = seed
            prop.procedural_detail = spec["detail"]
            prop.procedural_height = spec.get("height", 0)
            if detail:
                amplitude = detail.get({"BASE_COLOR": "color", "ROUGHNESS": "roughness",
                                        "HEIGHT": "height"}[prop.channel], 0)
                if amplitude:
                    prop.detail_image = detail_image
                    prop.scale = detail.get("scale", (1, 1))
                    if prop.channel == "HEIGHT":
                        prop.procedural_height = amplitude
                    else:
                        prop.procedural_detail = (amplitude / .35 if prop.channel == "ROUGHNESS"
                                                  else amplitude)
    for index, settings in enumerate(masks_for(spec)):
        effect = item.masks.add()
        effect.id, effect.name, effect.source = new_id(), settings["kind"].replace("_", " ").title(), "GENERATOR"
        effect.generator = settings["kind"]
        effect.operation = "NORMAL" if index == 0 else "MULTIPLY"
        effect.amount = settings["amount"]
        effect.noise_scale = settings["scale"]
        effect.noise_strength = .35
        effect.contrast = settings.get("contrast", 1.25 if settings["kind"] in {
            "EDGE_WEAR", "CHIPS", "DUST", "WORLD_NORMAL"} else 3)
        effect.seed = (seed + index * 104729) & 0x7fffffff
        effect.invert = settings.get("invert", False)
    return item


def _state(ts):
    return {
        "ids": {item.id for item in ts.layers}, "active": ts.active_layer,
        "dirty": ts.dirty, "serial": ts.edit_serial,
        "surface": {key: getattr(ts, key) for key in ("anisotropy", "coat_weight", "coat_roughness")},
        "outputs": {out.channel: out.dirty for out in ts.outputs},
    }


def _rollback(ts, before):
    for index in reversed(range(len(ts.layers))):
        if ts.layers[index].id not in before["ids"]:
            ts.layers.remove(index)
    for i, item in enumerate(ts.layers):
        item.order = i
    ts.active_layer = before["active"]
    for key, value in before["surface"].items():
        setattr(ts, key, value)
    ts.dirty, ts.edit_serial = before["dirty"], before["serial"]
    for out in ts.outputs:
        out.dirty = before["outputs"].get(out.channel, out.dirty)


def apply(ts, name, seed=1):
    """Insert an editable material folder above the active layer, transactionally."""
    return apply_document(ts,CATALOG[name],name,seed)


def apply_document(ts,data,name='Declarative Material',seed=1):
    """Apply validated data without installing code or modifying the catalog."""
    preflight(ts, data, name)
    before = _state(ts)
    before_images = {image.as_pointer() for image in bpy.data.images}
    active = stack.active(ts)
    parent_id = active.parent_id if active else ""
    try:
        folder = stack.add(ts, "FOLDER", name, parent_id)
        folder_id = folder.id
        # Isolation prevents this recipe's complete substrate from inheriting
        # arbitrary blend modes from a previously selected material.
        folder.group_mode = "ISOLATED"
        for index, spec in enumerate(data["layers"]):
            _fill(ts, spec, folder_id, _seed(name, index, seed))
        for key, default in (("anisotropy", 0), ("coat_weight", 0), ("coat_roughness", .03)):
            setattr(ts, key, data.get("surface", {}).get(key, default))
        ts.active_layer = next(i for i, item in enumerate(ts.layers) if item.id == folder_id)
        stack.invalidate(ts)
        return ts.layers[ts.active_layer]
    except Exception:
        _rollback(ts, before)
        generated_sources.discard_unused_new_images(before_images)
        raise


def apply_effect(ts, name, seed=1):
    """Insert one reusable effect above the active layer, or inside its folder."""
    data = EFFECTS[name]
    preflight(ts, data, name)
    before = _state(ts)
    selected = stack.active(ts)
    parent_id = (selected.id if selected.type == "FOLDER" else selected.parent_id) if selected else ""
    try:
        if selected and selected.type == "FOLDER":
            # Put a new effect over all immediate children, not under the base.
            child_indices = [i for i, item in enumerate(ts.layers) if item.parent_id == parent_id]
            if child_indices:
                ts.active_layer = max(child_indices)
        result = _fill(ts, data, parent_id, _seed(name, 0, seed))
        stack.invalidate(ts)
        return result
    except Exception:
        _rollback(ts, before)
        raise
