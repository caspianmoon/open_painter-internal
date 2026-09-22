"""Conservative proposal budget for shader texture inputs, without compiling a draft.

Metal permits 16 fragment samplers; Blender's viewport also needs its own inputs.
This estimate reserves four slots. It is a proposal guard, not a GPU compiler or
a guarantee for arbitrary pre-existing materials and renderer configurations.
"""

LIMIT = 12
ANALYTIC = frozenset({"SOLID", "GRADIENT", "LINEAR_GRADIENT", "RADIAL_GRADIENT",
                     "CHECKER", "STRIPES", "DOTS", "PATTERN", "GRID", "TILE_PATTERN", "BRICK_PATTERN"})


def estimate(value):
    from ..evaluation.shader_effects import is_pointwise
    from ..generators.base import required_maps
    inputs, maps = 0, set()
    physical_fields = set()
    # Include hidden publishers: a visible layer can read their anchors.
    for layer in value["layers"]:
        for prop in layer["channels"]:
            if not prop["enabled"]:
                continue
            if prop.get("image"):
                inputs += 1
            physical = prop.get("material_mapping_version") == 1 and prop["procedural"] and not prop.get("detail_image")
            if physical:
                # The native shader's material-space scalar boundary shares one
                # actual image across coordinated channels. Deduplicate only
                # when every value in its resource identity agrees.
                from ..evaluation.material_space import FIELDS
                fields = (*FIELDS, "procedural", "procedural_scale", "procedural_seed",
                          "projection", "scale", "rotation", "offset", "triplanar_hardness")
                key = tuple(tuple(prop.get(k)) if isinstance(prop.get(k), list) else prop.get(k) for k in fields)
                if key not in physical_fields:
                    physical_fields.add(key)
                    inputs += 1
            elif prop.get("detail_image") or (prop["procedural"] and prop["procedural"] not in ANALYTIC):
                inputs += 1
            inputs += sum(1 for e in prop["effects"] if e["enabled"] and e["kind"] != "ANCHOR"
                          and (e.get("image") or not is_pointwise(e["kind"])))
        for mask in layer["masks"]:
            if not mask["enabled"]:
                continue
            source = mask["source"]
            if source in {"SCENE", "STROKES", "FILL", "PAINT", "NOISE", "MESH", "GRAPH"}:
                inputs += 1
            elif source == "PROCEDURAL":
                inputs += mask["procedural"] not in ANALYTIC or mask.get("material_mapping_version") == 1
            elif source == "FILTER":
                e = mask["effect"]
                inputs += e["enabled"] and (bool(e.get("image")) or not is_pointwise(e["kind"]))
            elif source == "BAKED":
                maps.add(mask["map_type"])
            elif source == "GENERATOR":
                if mask["edge_source"] == "LIVE" and mask["generator"] in {"EDGE_WEAR", "CHIPS"}:
                    inputs += 1
                else:
                    maps.update(required_maps(mask["generator"]))
                # Legacy generator noise is a separately cached texture input.
                inputs += 1
    return int(inputs + len(maps))


def validate(before, after):
    from .schema import AuthoringError
    old, new = estimate(before), estimate(after)
    if new > LIMIT and new > old:
        raise AuthoringError(
            f"Shader texture budget exceeded: estimated {new} inputs, budget {LIMIT} "
            f"(current {old}). Submit a complete replacement proposal using fewer independent "
            "procedural/image/scene inputs. Reuse anchors or omit low-priority microdetail; "
            "keep the requested spatial masks. This avoids a pink viewport on Metal.")
