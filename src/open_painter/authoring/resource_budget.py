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
    layers = value['layers']
    # A native auto-refresh cache samples composed channels, not the sources
    # inside it. Keep the editable descendants; never ask the model to delete
    # their detail solely to satisfy a sampler budget. Anchor consumers can
    # still read descendants live, so retain the conservative path for those.
    anchored = any(p.get('anchor') or any(e.get('kind') == 'ANCHOR' for e in p['effects'])
                   for layer in layers for p in layer['channels']) or any(
        m['source'] in {'ANCHOR', 'REFERENCE'} or m.get('detail_height_anchor') or m.get('detail_normal_anchor')
        for layer in layers for m in layer['masks'])
    cached, covered = {}, set()
    if not anchored:
        by_parent = {}
        for layer in layers:
            by_parent.setdefault(layer['parent_id'], []).append(layer)
        def children(identifier):
            pending = list(by_parent.get(identifier, ()))
            result, seen = [], set()
            while pending:
                child = pending.pop()
                if child['id'] in seen:
                    continue
                seen.add(child['id']); result.append(child)
                pending.extend(by_parent.get(child['id'], ()))
            return result
        for layer in layers:
            if layer.get('cache_mode') != 'CACHED' or not layer.get('cache'):
                continue
            if layer['type'] == 'FOLDER' and layer['group_mode'] != 'ISOLATED':
                continue
            descendants = children(layer['id']) if layer['type'] == 'FOLDER' else [layer]
            cached[layer['id']] = len({p['channel'] for child in descendants if child['type'] != 'FOLDER'
                                       for p in child['channels'] if p['enabled']})
            covered.update(child['id'] for child in descendants if child['id'] != layer['id'])
    # Include hidden publishers: a visible layer can read their anchors.
    for layer in value["layers"]:
        if layer['id'] in covered:
            continue
        if layer['id'] in cached:
            inputs += cached[layer['id']]
            if layer['type'] != 'FOLDER':
                continue  # A fill cache includes its masks and effects.
        for prop in layer["channels"]:
            if layer['id'] in cached:
                break
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
                if (mask["generator"] in {"EDGE_WEAR", "CHIPS"} and mask["edge_source"] == "BAKED"
                        and not mask.get("detail_height_anchor") and not mask.get("detail_normal_anchor")):
                    # shader_graph.generator resolves the complete baked-edge
                    # source (all curvature reaches, AO and noise) to one leaf.
                    inputs += 1
                    continue
                if mask["edge_source"] == "LIVE" and mask["generator"] in {"EDGE_WEAR", "CHIPS"}:
                    inputs += 1
                    maps.add("AO")  # Optional when absent; reserve conservatively.
                else:
                    maps.update(required_maps(mask["generator"]))
                    if mask["generator"] in {"EDGE_WEAR", "CHIPS"}:
                        maps.update({"AO", "CURVATURE_THIN", "CURVATURE_WIDE", "CURVATURE_XWIDE"})
                # Legacy generator noise is a separately cached texture input.
                inputs += 1
                if mask["generator"] == "CHIPS":
                    inputs += 1  # Separate Voronoi breakup field.
                if mask.get("detail_height_anchor") or mask.get("detail_normal_anchor"):
                    inputs += 1  # Derived anchor microdetail boundary.
    return int(inputs + len(maps))


def validate(before, after):
    from .schema import AuthoringError
    old, new = estimate(before), estimate(after)
    if new > LIMIT and new > old:
        raise AuthoringError(
            f"Shader texture budget exceeded: estimated {new} inputs, budget {LIMIT} "
            f"(current {old}). Submit a complete replacement proposal using fewer independent "
            "procedural/image/scene inputs, or put independent authored layers inside an "
            "ISOLATED folder and set_layer cache_mode=CACHED. Its composed channels refresh "
            "automatically while every source and layer stays editable. Preserve meaningful "
            "detail; do not discard it merely to fit the budget. Anchor consumers can still "
            "require live inputs. This avoids a pink viewport on Metal.")
