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

SURFACE_DEFAULTS = {"anisotropy": 0, "coat_weight": 0, "coat_roughness": .03,
                    "height_distance": .05}


def _validate_projection(spec):
    if spec.get("projection", "UV") not in {"UV", "TRIPLANAR"}:
        raise ValueError("Unknown recipe projection.")
    hardness = spec.get("triplanar_hardness", 4)
    if not math.isfinite(hardness) or not 1 <= hardness <= 64:
        raise ValueError("Invalid recipe triplanar hardness.")


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
    # Baked-map mask sources and normal-map images name the map they read.
    "AO": {"AO"},
    "NORMAL": {"NORMAL"},
    "CURVATURE": {"CURVATURE"},
    "MEAN_CURVATURE": {"MEAN_CURVATURE"},
    "BEVEL_MASK": {"BEVEL_MASK"},
    "BEVEL_NORMAL": {"BEVEL_NORMAL"},
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
        **layer("Scratches", STEEL, .52, height_value=.48,
                mask=mask("NOISE", generated_source={"id": "abrasion_mask_v2"},
                          black=.035, white=.62, projection="TRIPLANAR")),
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
        **layer("Fingerprints", (.3, .3, .3), .34, opacity=.45,
                mask=mask("NOISE", procedural="FINGERPRINTS", scale=3)),
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
    # Damaged Variable Bevel, engine independent: the Cycles-only Bevel and AO
    # shader nodes are replaced by the baked Bevel Mask (edge amount), Bevel
    # Normal (rounded shading) and AO maps, so EEVEE, Cycles and export agree.
    # Chips are recessed height, exposing the steel substrate, where the edge
    # amount, a noise field (the variable radius) and, when AO exists, the
    # open-surface term coincide. Disable colour channels on the layer to keep
    # the reference script's normal-only behaviour.
    "Damaged Bevel": layer("Damaged Bevel", STEEL, .48, 1, height_value=.3,
                           mask=mask("BEVEL_MASK", baked=True, black=.04, white=.3),
                           mask_effects=(mask("NOISE", procedural="MULTI_FRACTAL", scale=7, black=.47, white=.56,
                                              operation="MULTIPLY"),
                                         mask("AO", baked=True, black=.3, white=.75,
                                              operation="MULTIPLY", optional=True))),
    "Rounded Edges": {
        **layer("Rounded Edges", STEEL, .5, normal_map="BEVEL_NORMAL", blend_mode="RNM"),
        "channels": ("NORMAL",),
    },
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
        if spec.get("normal_map"):
            result.update(MAP_REQUIREMENTS[spec["normal_map"]])
        for settings in masks_for(spec):
            if settings.get("optional"):
                continue
            if not settings.get("procedural") and not settings.get("generated_source"):
                result.update(MAP_REQUIREMENTS[settings["kind"]])
    return result


def validate(data):
    """Reject malformed recipes before allocating any layer or changing selection."""
    specs = data.get("layers", (data,))
    if not specs or len(specs) > 256:
        raise ValueError("A smart material must contain between 1 and 256 layers.")
    for spec in specs:
        _validate_projection(spec)
        if not isinstance(spec.get("name"), str) or not spec["name"].strip():
            raise ValueError("Every recipe layer requires a name.")
        color = spec.get("color", ())
        if len(color) != 3 or any(not math.isfinite(v) or not 0 <= v <= 1 for v in color):
            raise ValueError("Recipe colors must be finite linear RGB values between zero and one.")
        for key, lo, hi in (("roughness", 0, 1), ("metallic", 0, 1),
                            ("scale", .01, 512), ("detail", 0, 2), ("height", -1, 1),
                            ("height_value", 0, 1), ("opacity", 0, 1)):
            value = spec.get(key, 0)
            if not math.isfinite(value) or not lo <= value <= hi:
                raise ValueError("Invalid recipe parameter: " + key)
        source = spec.get("procedural")
        if source and ALIASES.get(source, source) not in PROCEDURALS:
            raise ValueError("Unknown texture procedural: " + source)
        if "generated_source" in spec:
            generated_sources.validate(spec["generated_source"])
        if "height_range" in spec:
            limits = spec["height_range"]
            if (len(limits) != 2 or any(not math.isfinite(v) or not 0 <= v <= 1 for v in limits)
                    or limits[0] >= limits[1] or "height_value" in spec
                    or not (spec.get("procedural") or spec.get("generated_source"))):
                raise ValueError("Height range needs an increasing 0–1 range and a surface source.")
        selected = set(spec.get("channels", ("BASE_COLOR", "ROUGHNESS", "METALLIC", "HEIGHT")))
        allowed = {"BASE_COLOR", "ROUGHNESS", "METALLIC", "HEIGHT"}
        if spec.get("normal_map"):
            if spec["normal_map"] not in {"BEVEL_NORMAL", "NORMAL"}:
                raise ValueError("Unknown recipe normal map: " + str(spec["normal_map"]))
            allowed.add("NORMAL")
        if not selected <= allowed:
            raise ValueError("Unknown recipe texture channel.")
        if "blend_mode" in spec:
            from ..constants import BLEND_ITEMS
            if spec["blend_mode"] not in {item[0] for item in BLEND_ITEMS}:
                raise ValueError("Unknown recipe blend mode: " + str(spec["blend_mode"]))
        for settings in masks_for(spec):
            _validate_projection(settings)
            if settings["kind"] not in MAP_REQUIREMENTS:
                raise ValueError("Unknown recipe mask: " + settings["kind"])
            if settings.get("baked") and MAP_REQUIREMENTS[settings["kind"]] != {settings["kind"]}:
                raise ValueError("A baked recipe mask must name a mesh map: " + settings["kind"])
            if settings.get("optional") and not settings.get("baked"):
                raise ValueError("Only baked recipe masks can be optional.")
            for key, lo, hi in (("amount", 0, 1), ("scale", 1, 512)):
                value = settings[key]
                if not math.isfinite(value) or not lo <= value <= hi:
                    raise ValueError("Invalid recipe mask parameter: " + key)
            for key, lo, hi in (("contrast", .01, 20), ("noise_strength", 0, 1),
                                ("black", 0, 1), ("white", 0, 1), ("strength", 0, 1)):
                value = settings.get(key, {"contrast": 3, "white": 1, "strength": 1}.get(key, 0))
                if not math.isfinite(value) or not lo <= value <= hi:
                    raise ValueError("Invalid recipe mask parameter: " + key)
            if settings.get("white", 1) <= settings.get("black", 0):
                raise ValueError("Recipe mask white level must exceed its black level.")
            if settings.get("operation", "NORMAL") not in {"NORMAL", "MULTIPLY", "ADD"}:
                raise ValueError("Unknown recipe mask combination.")
            if "seed" in settings and (type(settings["seed"]) is not int
                                       or not 0 <= settings["seed"] <= 0x7fffffff):
                raise ValueError("Recipe mask seed must be a nonnegative 32-bit integer.")
            if "uv_scale" in settings:
                scale = settings["uv_scale"]
                if len(scale) != 2 or any(not math.isfinite(v) or not .001 <= v <= 512 for v in scale):
                    raise ValueError("Invalid recipe mask UV scale.")
            procedural = settings.get("procedural")
            if procedural and ALIASES.get(procedural, procedural) not in PROCEDURALS:
                raise ValueError("Unknown mask procedural: " + procedural)
            if "generated_source" in settings:
                generated_sources.validate(settings["generated_source"])
                if procedural:
                    raise ValueError("A recipe mask must select one procedural or image source.")
    for key, value in data.get("surface", {}).items():
        if key not in SURFACE_DEFAULTS:
            raise ValueError("Unknown material surface parameter: " + key)
        if not math.isfinite(value) or value < 0 or (key != "height_distance" and value > 1):
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


def _baked_image(ts, kind):
    return next((entry.image for entry in ts.baked_maps
                 if entry.kind == kind and entry.image and all(entry.image.size)), None)


def _fill(ts, spec, parent_id, seed):
    item = stack.add(ts, "FILL", spec["name"], parent_id)
    item.opacity = spec.get("opacity", 1)
    if "blend_mode" in spec:
        item.blend_mode = spec["blend_mode"]
    detail = spec.get("generated_source")
    detail_image = generated_sources.load(detail["id"]) if detail else None
    selected = set(spec.get("channels", ("BASE_COLOR", "ROUGHNESS", "METALLIC", "HEIGHT")))
    for prop in item.channels:
        prop.projection = spec.get("projection", "UV")
        prop.triplanar_hardness = spec.get("triplanar_hardness", 4)
        prop.enabled = prop.channel in selected and (prop.channel != "HEIGHT"
                        or "height" in spec or "height_value" in spec or "height_range" in spec)
        if prop.channel == "BASE_COLOR":
            prop.color = (*spec["color"], 1)
        elif prop.channel == "ROUGHNESS":
            prop.value = spec["roughness"]
        elif prop.channel == "METALLIC":
            prop.value = spec["metallic"]
        elif prop.channel == "HEIGHT":
            prop.value = spec.get("height_value", .5)
            if "height_value" in spec:
                # A groove uses the same coverage as its mask. Applying a
                # second noise field here produces unrelated raised scratches.
                continue
        elif prop.channel == "NORMAL" and spec.get("normal_map"):
            image = _baked_image(ts, spec["normal_map"])
            if image is None:
                raise MissingMeshMaps(spec["name"], {spec["normal_map"]})
            prop.image = image
            continue
        if prop.enabled and prop.channel in {"BASE_COLOR", "ROUGHNESS", "HEIGHT"}:
            prop.procedural = ALIASES.get(spec.get("procedural"), spec.get("procedural", ""))
            prop.procedural_scale = spec["scale"]
            prop.procedural_seed = seed
            prop.procedural_detail = spec["detail"]
            prop.procedural_height = spec.get("height", 0)
            if detail:
                amplitude = detail.get({"BASE_COLOR": "color", "ROUGHNESS": "roughness",
                                        "HEIGHT": "height"}[prop.channel], 0)
                if amplitude or (prop.channel == "HEIGHT" and "height_range" in spec):
                    prop.detail_image = detail_image
                    prop.scale = detail.get("scale", (1, 1))
                    if prop.channel == "HEIGHT":
                        prop.procedural_height = amplitude
                    else:
                        prop.procedural_detail = (amplitude / .35 if prop.channel == "ROUGHNESS"
                                                  else amplitude)
            if prop.channel == "HEIGHT" and "height_range" in spec:
                # Encode elevated coatings with an ordinary editable Levels
                # effect, preserving the existing CPU/GPU source convention.
                prop.procedural_height = 1
                effect = prop.effects.add()
                effect.kind = "LEVELS"
                effect.output_black, effect.output_white = spec["height_range"]
    for index, settings in enumerate(masks_for(spec)):
        if settings.get("optional") and _baked_image(ts, settings["kind"]) is None:
            continue  # Declared optional: the effect degrades gracefully without this map.
        effect = item.masks.add()
        effect.id, effect.name, effect.source = new_id(), settings["kind"].replace("_", " ").title(), "GENERATOR"
        effect.projection = settings.get("projection", "UV")
        effect.triplanar_hardness = settings.get("triplanar_hardness", 4)
        if settings.get("baked"):
            effect.source = "BAKED"
            effect.map_type = settings["kind"]
        else:
            effect.generator = settings["kind"]
        effect.operation = settings.get("operation", "NORMAL" if index == 0 else "MULTIPLY")
        effect.amount = settings["amount"]
        effect.noise_scale = settings["scale"]
        effect.noise_strength = settings.get("noise_strength", .35)
        effect.contrast = settings.get("contrast", 1.25 if settings["kind"] in {
            "EDGE_WEAR", "CHIPS", "DUST", "WORLD_NORMAL"} else 3)
        effect.seed = settings.get("seed", (seed + index * 104729) & 0x7fffffff)
        effect.invert = settings.get("invert", False)
        effect.black = settings.get("black", 0)
        effect.white = settings.get("white", 1)
        effect.strength = settings.get("strength", 1)
        if settings.get("procedural"):
            effect.source = "PROCEDURAL"
            effect.procedural = ALIASES.get(settings["procedural"], settings["procedural"])
            effect.name = settings["procedural"].replace("_", " ").title()
        if settings.get("generated_source"):
            source = settings["generated_source"]
            effect.source = "FILL"
            effect.image = generated_sources.load(source["id"])
            effect.scale = source.get("scale", (1, 1))
            effect.name = source["id"].replace("_", " ").title()
        if "uv_scale" in settings:
            effect.scale = settings["uv_scale"]
    return item


def _state(ts):
    return {
        "ids": {item.id for item in ts.layers}, "active": ts.active_layer,
        "dirty": ts.dirty, "serial": ts.edit_serial,
        "surface": {key: getattr(ts, key) for key in SURFACE_DEFAULTS},
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
        for key, default in SURFACE_DEFAULTS.items():
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
    before_images = {image.as_pointer() for image in bpy.data.images}
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
        generated_sources.discard_unused_new_images(before_images)
        raise
