"""Compose reusable catalog entries into editable texture layers atomically.

No texture evaluator, baker, shader graph or scene mutation is run here. The
normal output evaluator consumes the stored layer data after a successful edit.
"""

import math
from uuid import UUID, uuid5
from zlib import crc32

import bpy

from ..core import layer_stack as stack
from ..core.surface_settings import DEFAULTS as SURFACE_DEFAULTS
from ..core.surface_settings import valid as valid_surface
from ..texture_engine.procedurals import ALIASES
from ..texture_engine.procedurals import CATALOG as PROCEDURALS
from ..utils.ids import new_id
from . import generated_sources
from .catalog import CATALOG, DIRT, DUST, OIL, PATINA, RUST, STEEL, layer, mask
from .catalog import recipe as recipe_block


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
    "RUNOFF": {"POSITION", "AO"},
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
            if settings.get("optional") and not settings.get("baked") and (
                    settings.get("procedural") or settings.get("generated_source") or not MAP_REQUIREMENTS[settings["kind"]]):
                raise ValueError("Only recipe masks that read mesh maps can be optional.")
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
        if not valid_surface(key, value):
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


def mask_available(ts, settings):
    """Whether the mesh maps a recipe mask reads exist; an optional mask without them is skipped."""
    if settings.get("baked"):
        return _baked_image(ts, settings["kind"]) is not None
    if settings.get("procedural") or settings.get("generated_source"):
        return True
    return all(_baked_image(ts, kind) is not None for kind in MAP_REQUIREMENTS.get(settings["kind"], ()))


# Reusable catalog modules: recipes can nest them (recipe(..., modules=[(name, overrides)]))
# and artists insert them directly. Versioned like recipes; instances update in place.
MODULES = {
    "Damage": {**recipe_block("Modules", "Scratches, edge wear, cavity dirt and rust in one updatable folder",
                              EFFECTS["Scratches"], EFFECTS["Edge Wear"], EFFECTS["Dirt"], EFFECTS["Rust"]),
               "version": 1},
}

CATALOG_NAMESPACE = UUID("6d1c3d2e-7d5a-4f3a-9b0e-2f0e9a8b5c11")


def catalog_module_id(name):
    """Stable module id of a catalog recipe or catalog module."""
    return uuid5(CATALOG_NAMESPACE, "catalog:" + name).hex


def _stamp_module(ts, folder_id, name, data, token=None, nested=()):
    """Give a freshly built recipe folder deterministic module ids and an instance namespace.

    Ids derive from the module id and the item's name/position, so the same
    recipe version builds the same ids every time (the merge on update matches
    layers by id); the instance token keeps several instances apart. Layers of
    ``nested`` module instances keep their own ids.
    """
    from . import modules
    module_id = catalog_module_id(name)
    version = int(data.get("version", 1))
    token = token or new_id()[:8]
    members = modules.subtree(ts, folder_id)
    skip = set()
    for child in nested:
        skip.update(layer.id for layer in modules.subtree(ts, child))
    mapping, seen = {}, {}
    for layer in members:
        if layer.id in skip:
            continue
        key = "folder" if layer.id == folder_id else layer.name
        seen[key] = seen.get(key, 0) + 1
        label = key if seen[key] == 1 else f"{key}#{seen[key]}"
        mapping[layer.id] = uuid5(UUID(module_id), "layer:" + label).hex
        for index, item in enumerate(layer.masks):
            mapping[item.id] = uuid5(UUID(module_id), f"mask:{label}:{index}").hex
        for prop in layer.channels:
            for index, effect in enumerate(prop.effects):
                if not effect.id:
                    effect.id = new_id()
                mapping[effect.id] = uuid5(UUID(module_id), f"effect:{label}:{prop.channel}:{index}").hex
    from ..evaluation.anchors import derived_mapping
    instance = derived_mapping({old: f"{token}.{new}" for old, new in mapping.items()})
    for layer in members:
        if layer.id in skip:
            if layer.parent_id in instance:
                layer.parent_id = instance[layer.parent_id]
            continue
        layer.id = instance[layer.id]
        layer.parent_id = instance.get(layer.parent_id, layer.parent_id)
        for item in layer.masks:
            item.id = instance[item.id]
            for field in ("parent_id", "reference_layer", "reference_mask", "reference_anchor",
                          "detail_height_anchor", "detail_normal_anchor"):
                value = getattr(item, field)
                if value:
                    setattr(item, field, instance.get(value, value))
        for prop in layer.channels:
            prop.layer_id = layer.id
            if prop.anchor:
                prop.anchor = instance.get(prop.anchor, prop.anchor)
            for effect in prop.effects:
                effect.id = instance.get(effect.id, effect.id)
    folder = next(layer for layer in ts.layers if layer.id == instance[folder_id])
    folder.module_id, folder.module_version, folder.module_source = module_id, version, "catalog:" + name
    folder.module_instance, folder.module_name, folder.module_detached = token, name, False
    snapshots = [stack.snapshot(layer) for layer in modules.subtree(ts, folder.id)]
    modules.write_base(module_id, version,
                       modules.comparable(modules.remap(snapshots, modules.denamespace_mapping(snapshots, token))))
    return folder


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
            # Pigment mottling and physical grain need different spatial bands.
            # These remain ordinary editable channel procedural properties.
            channel_field = spec.get("channel_procedurals", {}).get(prop.channel)
            if channel_field:
                prop.procedural = channel_field["id"]
                prop.procedural_scale = channel_field.get("scale", spec["scale"])
                prop.procedural_detail = channel_field.get("detail", spec["detail"])
            if prop.channel == "HEIGHT" and "height_range" in spec:
                # Encode elevated coatings with an ordinary editable Levels
                # effect, preserving the existing CPU/GPU source convention.
                prop.procedural_height = 1
                effect = prop.effects.add()
                effect.kind = "LEVELS"
                effect.output_black, effect.output_white = spec["height_range"]
    for index, settings in enumerate(masks_for(spec)):
        if settings.get("optional") and not mask_available(ts, settings):
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
    """Insert an editable material folder above the active layer, transactionally.

    The set's Material Aging construction follows the recipe (canvas is canvas, automotive
    paint is steel under primer, base coat and clear coat), and a set not aged yet takes the
    history the recipe depicts, so *Age Material* continues the look (0.10.1)."""
    folder = apply_document(ts,CATALOG[name],name,seed)
    from ..aging import recipes
    try:
        recipes.adopt(ts, name, CATALOG[name])
    except Exception as exc:     # the material is in; an aging folder that could not follow says why
        from ..utils.logging import logger
        logger.exception("The aging construction could not follow %s", name)
        ts.id_data.open_painter.status = f"Material Aging did not follow {name}: {exc}"
    return folder


def apply_document(ts,data,name='Declarative Material',seed=1,token=None,parent_id=None,surface=True):
    """Apply validated data without installing code or modifying the catalog.

    The folder becomes a module instance of the recipe (deterministic ids under
    the instance ``token``). Nested catalog modules (``data['modules']``) are
    inserted inside it with their own tokens derived from this instance's.
    """
    preflight(ts, data, name)
    for module_name, _ in data.get("modules", ()):
        preflight(ts, MODULES[module_name], module_name)
    before = _state(ts)
    before_images = {image.as_pointer() for image in bpy.data.images}
    active = stack.active(ts)
    if parent_id is None:
        parent_id = active.parent_id if active else ""
    token = token or new_id()[:8]
    try:
        # One transaction: property callbacks skip their set-wide walks (owner
        # lookup, dependent channels, engine notification) for each of the
        # recipe's hundreds of writes; the set is invalidated, physically
        # resolved and notified once when the replay closes.
        with stack.replay(ts, created_only=True):   # it only writes the layers it adds
            folder = stack.add(ts, "FOLDER", name, parent_id)
            folder_id = folder.id
            # Isolation prevents this recipe's complete substrate from inheriting
            # arbitrary blend modes from a previously selected material.
            folder.group_mode = "ISOLATED"
            for index, spec in enumerate(data["layers"]):
                _fill(ts, spec, folder_id, _seed(name, index, seed))
            nested = []
            for module_name, overrides in data.get("modules", ()):
                child_token = nested_token(name, token, module_name)
                child = apply_document(ts, MODULES[module_name], module_name, seed, token=child_token,
                                       parent_id=folder_id, surface=False)
                _override(ts, child, overrides)
                nested.append(child.id)
            if surface:
                for key, default in SURFACE_DEFAULTS.items():
                    value = data.get("surface", {}).get(key, default)
                    # An unchanged value would still run its callback (the height distance's
                    # walks every channel of the set); the properties hold float32.
                    if not math.isclose(getattr(ts, key), value, rel_tol=1e-6, abs_tol=1e-9):
                        setattr(ts, key, value)
            folder = _stamp_module(ts, folder_id, name, data, token=token, nested=nested)
            ts.active_layer = next(i for i, item in enumerate(ts.layers) if item.id == folder.id)
        return ts.layers[ts.active_layer]
    except Exception:
        _rollback(ts, before)
        generated_sources.discard_unused_new_images(before_images)
        raise


def nested_token(name, token, module_name):
    """Instance token of a catalog module nested in recipe ``name``'s instance ``token``."""
    return uuid5(UUID(catalog_module_id(name)), f"{token}:{module_name}").hex[:8]


def _override(ts, folder, overrides):
    """Apply per-recipe parameter overrides to a nested module instance: {layer name: {field: value}}.

    They are ordinary local edits relative to the module's base, so updating the
    module keeps them.
    """
    from . import modules
    by_name = {layer.name: layer for layer in modules.subtree(ts, folder.id)}
    for layer_name, fields in (overrides or {}).items():
        target = by_name.get(layer_name)
        if target is None:
            raise ValueError(f"Module override names an unknown layer: {layer_name}")
        for field, value in fields.items():
            if field.startswith("mask."):
                for item in target.masks:
                    setattr(item, field[5:], value)
            elif "." in field:
                channel, attribute = field.split(".", 1)
                prop = stack.channel_for(target, channel)
                if prop is not None:
                    setattr(prop, attribute, value)
            else:
                setattr(target, field, value)


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
