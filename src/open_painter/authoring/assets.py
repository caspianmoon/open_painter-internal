"""Local catalog retrieval and expansion into ordinary editable layer records."""

import hashlib
import json

from .document import clone, defaults
from .schema import AuthoringError


def records():
    from ..smart_materials.composer import CATALOG, EFFECTS, MODULES
    return {**{f"material:{k}": v for k, v in CATALOG.items()},
            **{f"effect:{k}": dict(layers=[v]) for k, v in EFFECTS.items()},
            **{f"module:{k}": v for k, v in MODULES.items()}}


def search(query="", limit=8):
    from ..smart_materials.composer import requirements
    words = query.lower().replace("_", " ").split()
    ranked = []
    for identifier, data in records().items():
        text = f"{identifier} {data.get('description', '')} {data.get('category', '')}".lower()
        score = sum(word in text for word in words)
        if words and not score:
            continue
        ranked.append((score, identifier, dict(id=identifier, description=data.get("description", ""),
                      layers=len(data.get("layers", ())), required_maps=sorted(requirements(data)),
                      version=data.get("version", 1))))
    return [value for _, _, value in sorted(ranked, key=lambda item: (-item[0], item[1]))[:max(1, min(limit, 20))]]


def describe(identifier):
    from ..smart_materials.composer import requirements
    data = records().get(identifier)
    if data is None:
        raise AuthoringError("Unknown local material/effect ID")
    return dict(id=identifier, recipe=clone(data), required_maps=sorted(requirements(data)),
                note="BAKED preserves the catalog. LIVE substitutes supported geometry masks; unsupported maps still require baking.")


def semantic(name):
    name = name.lower()
    for terms, role in ((('rust', 'oxide', 'patina', 'oxidation'), 'OXIDATION'),
                        (('scratch', 'abrasion'), 'SCRATCHES'),
                        (('chip', 'edge wear', 'paint loss'), 'COATING_LOSS'),
                        (('dirt', 'grime', 'mud', 'soot'), 'DIRT'),
                        (('dust',), 'DUST'), (('fade',), 'FADE')):
        if any(term in name for term in terms):
            return role
    return 'NONE'


def new_layer(identifier, name, kind="FILL", parent_id=""):
    from ..constants import CHANNELS, DEFAULTS
    from ..core.layer_stack import FILL_DEFAULT_CHANNELS
    layer = defaults("OP_Layer")
    layer.update(id=identifier, name=name, type=kind, parent_id=parent_id)
    layer["channels"] = []
    for key in CHANNELS:
        prop = defaults("OP_Channel")
        prop.update(channel=key, layer_id=identifier, color=list(DEFAULTS[key]), value=DEFAULTS[key][0],
                    enabled=kind == "FOLDER" or (kind == "FILL" and key in FILL_DEFAULT_CHANNELS))
        layer["channels"].append(prop)
    return layer


def expand(identifier, root_id, parent_id, seed, map_mode, allocate, available, length=1):
    """Trusted catalog expansion; image sources resolve only during the commit."""
    from ..smart_materials.composer import validate, masks_for, _seed
    from ..texture_engine.procedurals import ALIASES
    data = records().get(identifier)
    if data is None:
        raise AuthoringError("Unknown local material/effect ID")
    validate(data)
    if data.get("modules"):
        raise AuthoringError("Nested catalog recipes are not yet exposed to Material Director")
    name = identifier.split(":", 1)[1]
    result = []
    is_effect = identifier.startswith("effect:")
    if not is_effect:
        root = new_layer(root_id, name, "FOLDER", parent_id)
        root["group_mode"] = "ISOLATED" if identifier.startswith("material:") else "PASSTHROUGH"
        root["director_recipe"] = json.dumps(dict(asset=identifier, version=data.get("version", 1), seed=seed,
            hash=hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()))
        result.append(root)
    for index, spec in enumerate(data["layers"]):
        own_id = root_id if is_effect else allocate(f"layer-{index}")
        layer = new_layer(own_id, spec["name"], parent_id=parent_id if is_effect else root_id)
        layer.update(opacity=spec.get("opacity", 1), effect_semantic=semantic(spec["name"]))
        if "blend_mode" in spec:
            layer["blend_mode"] = spec["blend_mode"]
        own_seed = _seed(name, index, seed)
        selected = set(spec.get("channels", ("BASE_COLOR", "ROUGHNESS", "METALLIC", "HEIGHT")))
        for prop in layer["channels"]:
            key = prop["channel"]
            prop.update(enabled=key in selected and (key != "HEIGHT" or any(k in spec for k in ("height", "height_value", "height_range"))),
                        projection=spec.get("projection", "UV"), triplanar_hardness=spec.get("triplanar_hardness", 4))
            if key == "BASE_COLOR":
                prop["color"] = [*spec["color"], 1]
            elif key in {"ROUGHNESS", "METALLIC"}:
                prop["value"] = spec[key.lower()]
            elif key == "HEIGHT":
                prop["value"] = spec.get("height_value", .5)
            elif key == "NORMAL" and spec.get("normal_map"):
                kind = spec["normal_map"]
                if kind not in available:
                    raise AuthoringError(f"Bake {kind} before inserting {name}")
                prop["image"] = available[kind]
            if not prop["enabled"] or key not in {"BASE_COLOR", "ROUGHNESS", "HEIGHT"}:
                continue
            if key == "HEIGHT" and "height_value" in spec:
                continue
            prop.update(procedural=ALIASES.get(spec.get("procedural"), spec.get("procedural", "")),
                        procedural_scale=spec["scale"], procedural_seed=own_seed,
                        procedural_detail=spec["detail"], procedural_height=spec.get("height", 0))
            detail = spec.get("generated_source")
            if detail:
                amplitude = detail.get({"BASE_COLOR": "color", "ROUGHNESS": "roughness", "HEIGHT": "height"}[key], 0)
                if amplitude or (key == "HEIGHT" and "height_range" in spec):
                    prop["detail_image"] = {"$source": detail["id"]}
                    prop["scale"] = list(detail.get("scale", (1, 1)))
                    prop["procedural_height" if key == "HEIGHT" else "procedural_detail"] = amplitude / .35 if key == "ROUGHNESS" else amplitude
            if key == "HEIGHT" and "height_range" in spec:
                prop["procedural_height"] = 1
                effect = defaults("OP_TextureEffect")
                effect.update(id=allocate(f"height-{index}"), kind="LEVELS",
                              output_black=spec["height_range"][0], output_white=spec["height_range"][1])
                prop["effects"].append(effect)
        for number, settings in enumerate(masks_for(spec)):
            if settings.get("optional") and settings["kind"] not in available:
                continue
            mask = defaults("OP_Mask")
            kind = settings["kind"]
            mask.update(id=allocate(f"mask-{index}-{number}"), name=kind.replace("_", " ").title(),
                        source="BAKED" if settings.get("baked") else "GENERATOR",
                        amount=settings["amount"], noise_scale=settings["scale"],
                        contrast=settings.get("contrast", 3), noise_strength=settings.get("noise_strength", .2),
                        seed=settings.get("seed", (own_seed + number * 104729) & 0x7fffffff),
                        invert=settings.get("invert", False), black=settings.get("black", 0),
                        white=settings.get("white", 1), strength=settings.get("strength", 1),
                        operation=settings.get("operation", "NORMAL"),
                        projection=settings.get("projection", "UV"),
                        triplanar_hardness=settings.get("triplanar_hardness", 4))
            mask["map_type" if settings.get("baked") else "generator"] = kind
            if settings.get("procedural"):
                mask.update(source="PROCEDURAL", procedural=ALIASES.get(settings["procedural"], settings["procedural"]))
            if settings.get("generated_source"):
                source = settings["generated_source"]
                mask.update(source="FILL", image={"$source": source["id"]}, scale=list(source.get("scale", (1, 1))))
            if "uv_scale" in settings:
                mask["scale"] = list(settings["uv_scale"])
            if map_mode == "LIVE" and mask["source"] == "GENERATOR":
                if kind in {"EDGE_WEAR", "CHIPS"}:
                    mask["edge_source"] = "LIVE"
                    mask["influence"].update(kind="EDGE", mode="LIVE", resolution="256", distance=max(.0001, length * .01))
                elif kind in {"CAVITY_DIRT", "RUST", "DIRT", "DUST"}:
                    mask.update(source="SCENE", black=max(0, .5 - settings["amount"] * .5), white=1)
                    mask["influence"].update(kind="UP" if kind == "DUST" else "CONCAVITY", mode="LIVE",
                                            resolution="256", distance=max(.0001, length * .02))
            layer["masks"].append(mask)
        result.append(layer)
    return result


def prepare(value, *, images=None):
    """Resolve trusted source IDs and request-owned textures during commit."""
    if isinstance(value, dict) and '$surface_field' in value:
        from .surface_field import load as load_field
        return load_field(value, images)
    if isinstance(value, dict) and '$surface_detail' in value:
        from .generated_surfaces import load_detail
        return load_detail(value, images)
    if isinstance(value, dict) and '$surface_replacement' in value:
        from .surface_replacement import load
        return load(value, images)
    if isinstance(value, dict) and "$surface_map" in value:
        from .surface_maps import load
        return load(value, images)
    if isinstance(value, dict) and set(value) == {"$bundle_image", "color_space"}:
        from .sources import prepare as prepare_bundle
        return prepare_bundle(value)
    if isinstance(value, dict) and set(value) == {"$generated", "color_space"}:
        from .generated_textures import load
        return load(value, images)
    if isinstance(value, dict) and set(value) == {"$source"}:
        from ..smart_materials.generated_sources import load
        return load(value["$source"])
    if isinstance(value, dict):
        return {k: prepare(v, images=images) for k, v in value.items()}
    if isinstance(value, list):
        return [prepare(v, images=images) for v in value]
    return value
