"""Small typed recipes compiled to the ordinary editable layer graph.

There is no shadow material state: controls read and patch native channels and
masks. Metadata records identity/provenance only. Artist edits remain visible.
"""
import hashlib
import json

from . import assets, document, physical_space
from .schema import AuthoringError

# Restrained construction defaults; no asset names, benchmark IDs or prompts.
FAMILIES = {
    "PAINTED_METAL": dict(color=(.035, .13, .17, 1), roughness=(.3, .42), metallic=0, field="ORANGE_PEEL", period=.0008, height=.000008, detail=.035),
    "BARE_METAL": dict(color=(.45, .43, .38, 1), roughness=(.23, .36), metallic=1, field="BRUSHED_METAL_LINES", period=.001, height=.000006, detail=.035),
    "WOOD": dict(color=(.12, .047, .016, 1), roughness=(.3, .42), metallic=0, field="WOOD_GRAIN", period=.003, height=.000018, detail=.34),
    "WOVEN_FABRIC": dict(color=(.42, .1, .025, 1), roughness=(.72, .8), metallic=0, field="FABRIC_WEAVE", period=.001, height=.000035, detail=.085),
    "RUBBER": dict(color=(.5, .28, .025, 1), roughness=(.47, .57), metallic=0, field="RUBBER_GRAIN", period=.0007, height=.000006, detail=.025),
    "CERAMIC_GLAZE": dict(color=(.78, .72, .58, 1), roughness=(.16, .2), metallic=0, field="ORANGE_PEEL", period=.001, height=.000002, detail=.012),
    "MUD": dict(color=(.047, .025, .012, 1), roughness=(.16, .25), metallic=0, field="GRUNGE_DIRT", period=.004, height=.00025, detail=.18),
    "STONE": dict(color=(.56, .47, .32, 1), roughness=(.7, .83), metallic=0, field="STONE_GRAIN", period=.003, height=.00013, detail=.2),
    "MOSS": dict(color=(.11, .19, .025, 1), roughness=(.75, .86), metallic=0, field="FIBERS", period=.002, height=.0002, detail=.18),
}
PATCH_FIELDS = {
    "COLOR_ONLY": {"color"},
    "PERIOD_ONLY": {"period_m"},
    "APPEARANCE_ONLY": {"color", "roughness_range", "height_m", "color_variation"},
    "OPACITY_ONLY": {"opacity", "paint_loss_opacity"},
    "PLACEMENT_ONLY": {"band_translation"},
    "COVERAGE_ONLY": {"coverage_ratio"},
}
CUSTOM_PATCH_MODES = ("COLOR_ONLY", "APPEARANCE_ONLY", "OPACITY_ONLY", "COVERAGE_ONLY")


def _channel(layer, name):
    return next(item for item in layer["channels"] if item["channel"] == name)


def _metadata(identifier, field, channels, frame, extent, provenance="PROCEDURAL"):
    return json.dumps(dict(version=1, source_id=identifier, purpose="Structural surface field",
        channels=channels, mapping_frame=frame, physical_extent_m=extent,
        provenance=provenance, calibrated_bundle=provenance == "PROCEDURAL",
        revision_identity=hashlib.sha256(f"{identifier}:{field}".encode()).hexdigest(),
        note="Shared structure with separate channel transfer functions; not color-derived roughness or height"), sort_keys=True)


def _configure(ts, layer, spec, values, source_id, *, role="SURFACE"):
    color = values.get("color", spec["color"])
    roughness = values.get("roughness_range", spec["roughness"])
    if roughness[0] > roughness[1] or roughness[1] - roughness[0] > .7:
        raise AuthoringError("Roughness range must increase and span at most 0.7")
    seed = values.get("seed", 1)
    frame = values.get("frame", "COMPONENT" if layer["surface_family"] == "WOOD" else "OBJECT")
    period = values.get("period_m", spec["period"])
    known = physical_space.capture(ts)["status"] == "KNOWN"
    if known and "relative_period" in values:
        raise AuthoringError("Physical size is declared; use period_m instead of relative_period")
    if not known:
        defaults = {"frame": "WORLD", "origin": [0, 0, 0],
                    "axis_u": [1, 0, 0], "axis_v": [0, 1, 0]}
        if any(key in values and values[key] != default for key, default in defaults.items()):
            raise AuthoringError("Physical size is unknown; relative surfaces use the default world frame. "
                                 "Declare physical size before requesting object/member frames or custom origins/axes")
        frame = "LEGACY_RELATIVE_WORLD"
    if "period_m" in values or "height_m" in values:
        physical_space.require(ts)
    mpu = physical_space.require(ts) if known else 1
    field = spec["field"]
    channels = ["BASE_COLOR", "ROUGHNESS", "HEIGHT"]
    for prop in layer["channels"]:
        key = prop["channel"]
        prop.update(enabled=key in {*channels, "METALLIC"}, projection="TRIPLANAR")
        if key == "BASE_COLOR":
            prop["color"] = list(color)
        elif key == "ROUGHNESS":
            prop["value"] = sum(roughness) / 2
        elif key == "METALLIC":
            prop["value"] = spec["metallic"]
        if key not in channels:
            continue
        prop.update(procedural=field, procedural_seed=seed,
                    procedural_detail=values.get("color_variation", spec["detail"]) if key == "BASE_COLOR" else (roughness[1] - roughness[0]) / .35,
                    material_source_id=source_id,
                    source_metadata=_metadata(source_id, field, channels, frame, period if known else None))
        if known:
            prop.update(material_mapping_version=1, material_meters_per_unit=mpu,
                        material_period_m=period, material_frame=frame,
                        material_height_m=values.get("height_m", spec["height"]),
                        material_origin=values.get("origin", [0, 0, 0]),
                        material_axis_u=values.get("axis_u", [1, 0, 0]),
                        material_axis_v=values.get("axis_v", [0, 1, 0]),
                        material_directional=layer["surface_family"] == "WOOD")
        else:
            # Relative scale is explicitly disclosed and never called metric.
            length = max((max(ref.object.dimensions) for ref in ts.assignments if ref.object), default=1)
            relative = values.get("relative_period", .015)
            prop.update(procedural_scale=min(512, 1 / max(length * relative, 1e-5)),
                        procedural_height=.001 if key == "HEIGHT" else .0)
        if key == "HEIGHT":
            prop["value"] = .5
    layer["surface_role"] = role


def _mask(identifier, name, source, **values):
    result = document.defaults("OP_Mask")
    result.update(id=identifier, name=name, source=source, **values)
    return result


def expand(ts, cmd, root_id, parent_id, allocate, objects=None):
    family, values = cmd["family"], cmd.get("values", {})
    if family != "PAINTED_METAL" and any(k in values for k in ("chip_radius_m", "effect_distance_m")):
        raise AuthoringError("Chip radius and edge-effect distance belong to painted-metal loss; use set_mask/set_field for independent effects")
    spec = FAMILIES[family]
    root = assets.new_layer(root_id, cmd["name"], "FOLDER", parent_id)
    root.update(surface_id=root_id, surface_family=family, surface_role="SURFACE", group_mode="ISOLATED")
    root["opacity"] = values.get("opacity", 1)
    if cmd.get("region_id"):
        from . import regions
        mask = _mask(allocate("region"), "Surface region", "MESH")
        mask["mesh"].update(regions.selector(ts, cmd["region_id"], objects or {}))
        root["masks"].append(mask)
    root["director_recipe"] = json.dumps(dict(version=1, family=family,
        source="native_surface_recipe", physical_space=physical_space.capture(ts)), sort_keys=True)
    result = [root]
    if family == "PAINTED_METAL":
        substrate = assets.new_layer(allocate("substrate"), "Metal substrate", parent_id=root_id)
        substrate.update(surface_id=substrate["id"], surface_family="BARE_METAL")
        _configure(ts, substrate, FAMILIES["BARE_METAL"], {"seed": values.get("seed", 1)}, allocate("substrate-source"), role="SUBSTRATE")
        result.append(substrate)
    fill = assets.new_layer(allocate("finish"), "Coating" if family == "PAINTED_METAL" else cmd["name"] + " finish", parent_id=root_id)
    fill.update(surface_id=root_id, surface_family=family)
    _configure(ts, fill, spec, values, allocate("structure"), role="COATING" if family == "PAINTED_METAL" else "FINISH")
    if family == "PAINTED_METAL":
        # The one inverted mask removes the dielectric coating and exposes the
        # underlying metal. Strength is paint-loss opacity, not coverage area.
        wear = _mask(allocate("paint-loss"), "Paint loss (reveals substrate)", "GENERATOR",
                     generator="EDGE_WEAR", amount=values.get("wear_coverage", .2), noise_strength=.55,
                     noise_scale=64, seed=values.get("seed", 1), invert=True,
                     strength=values.get("paint_loss_opacity", 1))
        available = {m.kind for m in ts.baked_maps if m.image}
        if "CURVATURE" not in available:
            wear["edge_source"] = "LIVE"
            wear["influence"].update(kind="EDGE", mode="LIVE", resolution="256", distance=.01)
        if "effect_distance_m" in values:
            # Baked curvature reach cannot be retrospectively calibrated. An
            # explicit metric reach therefore uses evaluated live geometry.
            wear["edge_source"] = "LIVE"
            wear["influence"].update(kind="EDGE", mode="LIVE", resolution="512")
            wear["influence"].update(physical_space.field_values(ts, wear["influence"],
                {"distance_m": values["effect_distance_m"]}))
        wear["surface_role"] = "PAINT_LOSS"
        if "chip_radius_m" in values:
            # Invert only after geometry wear × circular breakup. Inverting
            # either member first would remove paint away from worn areas.
            group = _mask(allocate("paint-loss"), "Paint loss (reveals substrate)", "GROUP",
                surface_role="PAINT_LOSS", invert=True, strength=wear["strength"])
            wear.update(id=allocate("wear-placement"), parent_id=group["id"], invert=False,
                        strength=1, surface_role="WEAR_PLACEMENT")
            chips = _mask(allocate("chips"), "Physical circular chip breakup", "PROCEDURAL",
                parent_id=group["id"], procedural="DOTS", surface_role="CHIP_PATTERN",
                material_source_id=allocate("chip-source"), operation="MULTIPLY",
                material_frame=values.get("frame", "OBJECT"),
                material_origin=values.get("origin", [0, 0, 0]),
                material_axis_u=values.get("axis_u", [1, 0, 0]),
                material_axis_v=values.get("axis_v", [0, 1, 0]))
            chips.update(physical_space.mask_values(ts, chips, {"chip_radius_m": values["chip_radius_m"]}))
            fill["masks"].extend((group, wear, chips))
        else:
            fill["masks"].append(wear)
    result.append(fill)
    if family == "WOOD":
        # Broad color rings and fine pores use coordinated but different fields.
        for key in ("ROUGHNESS", "HEIGHT"):
            prop = _channel(fill, key)
            prop["procedural"] = "WOOD_PORES"
            prop["material_period_m"] *= .25
            prop["material_source_id"] = allocate("pores")
            prop["source_metadata"] = _metadata(allocate("pores"), "WOOD_PORES", ["ROUGHNESS", "HEIGHT"],
                prop["material_frame"] if prop["material_mapping_version"] else "LEGACY_RELATIVE_WORLD",
                prop["material_period_m"] if prop["material_mapping_version"] else None)
    return result


def _owned(after, identifier):
    root = next((l for l in after["layers"] if l["id"] == identifier), None)
    if root is None:
        raise AuthoringError("Select an existing stable surface ID from context")
    if not root.get("surface_id"):
        if root["type"] != "FILL":
            raise AuthoringError("A custom surface patch requires an existing fill layer")
        return root, [root]
    owned = [l for l in after["layers"] if l.get("surface_id") == root["surface_id"]]
    return root, owned


def patch(ts, after, cmd, identifier):
    mode, values = cmd["mode"], cmd["values"]
    if not values or set(values) - PATCH_FIELDS[mode]:
        raise AuthoringError(f"{mode} permits only {sorted(PATCH_FIELDS[mode])}")
    root, owned = _owned(after, identifier)
    if not root.get("surface_id") and mode not in CUSTOM_PATCH_MODES:
        raise AuthoringError("Custom fill surfaces support appearance, opacity and coverage patches; metric period requires an explicitly mapped native surface")
    allowed, diagnostics = [], {}
    if "coverage_ratio" in values:
        from . import coverage
        previous = document.clone(after)
        solved = coverage.reduction(ts, root["id"], values["coverage_ratio"])
        mask = solved["mask"]
        diagnostics["coverage"] = solved["measurements"]
        matches = [i for i, item in enumerate(root["masks"]) if item["id"] == mask["id"]]
        if matches and matches[0] != len(root["masks"]) - 1:
            raise AuthoringError("Coverage adjustment must remain the final mask operation")
        if matches:
            root["masks"][matches[0]] = mask
        else:
            root["masks"].append(mask)
        allowed.extend(path for path, _, _ in document.changes(previous, after))
    for layer in owned:
        if layer["type"] != "FILL":
            continue
        prefix = ("layers", layer["id"], "channels")
        for key, value in values.items():
            if key == "color":
                if abs(value[3] - _channel(layer, "BASE_COLOR")["color"][3]) > 1e-6:
                    raise AuthoringError("Palette edits preserve alpha; use a separate opacity patch")
                previous = document.clone(after)
                base = _channel(layer, "BASE_COLOR")
                if base.get("image"):
                    transfer_id = layer["id"] + ".palette"
                    transfer = next((e for e in base["effects"] if e["id"] == transfer_id), None)
                    if transfer is None:
                        transfer = document.defaults("OP_TextureEffect")
                        transfer.update(id=transfer_id, name="Editable image palette", kind="GRADIENT_MAP")
                        base["effects"].append(transfer)
                    if transfer["kind"] != "GRADIENT_MAP":
                        raise AuthoringError("The image palette transfer was changed; inspect it before recoloring")
                    transfer["color"] = [v * .875 for v in value[:3]]
                    transfer["color2"] = [min(1, v * 1.125) for v in value[:3]]
                else:
                    base["color"] = list(value)
                allowed.extend(path for path, _, _ in document.changes(previous, after))
            elif key == "roughness_range":
                if value[0] > value[1] or value[1] - value[0] > .7:
                    raise AuthoringError("Roughness range must increase and span at most 0.7")
                prop = _channel(layer, "ROUGHNESS")
                previous = document.clone(after)
                if prop.get("image"):
                    transfer_id = layer["id"] + ".roughness-range"
                    transfer = next((e for e in prop["effects"] if e["id"] == transfer_id), None)
                    if transfer is None:
                        transfer = document.defaults("OP_TextureEffect")
                        transfer.update(id=transfer_id, name="Editable roughness range", kind="LEVELS")
                        prop["effects"].append(transfer)
                    if transfer["kind"] != "LEVELS":
                        raise AuthoringError("The roughness transfer was changed; inspect it before editing")
                    transfer.update(output_black=value[0], output_white=value[1])
                else:
                    prop.update(value=sum(value) / 2, procedural_detail=(value[1] - value[0]) / .35)
                allowed.extend(path for path, _, _ in document.changes(previous, after))
            elif key == "period_m":
                physical_space.require(ts)
                # Each channel keeps its relative frequency, e.g. fine wood pores.
                initial = _channel(layer, "BASE_COLOR")["material_period_m"]
                for prop in layer["channels"]:
                    if not (prop.get("procedural") or prop.get("image") or prop.get("detail_image")):
                        continue
                    if prop["material_mapping_version"] != 1:
                        raise AuthoringError("A relative legacy surface cannot claim metric resizing; recreate or explicitly migrate it")
                    ratio = float(value) / initial
                    prop["material_period_m"] *= ratio
                    allowed.append((*prefix, prop["channel"], "material_period_m"))
                    if prop.get("image") or prop.get("detail_image"):
                        prop["material_image_extent_m"] = [v * ratio for v in prop["material_image_extent_m"]]
                        allowed.append((*prefix, prop["channel"], "material_image_extent_m"))
            elif key == "height_m":
                physical_space.require(ts)
                if _channel(layer, "HEIGHT")["material_mapping_version"] != 1:
                    raise AuthoringError("A relative legacy height cannot claim metric amplitude; explicitly map it first")
                _channel(layer, "HEIGHT")["material_height_m"] = value
                allowed.append((*prefix, "HEIGHT", "material_height_m"))
            elif key == "color_variation":
                base = _channel(layer, "BASE_COLOR")
                if not (base.get("procedural") or base.get("detail_image")):
                    raise AuthoringError("Color variation requires a procedural or scalar detail source; "
                                         "image content and constant colors do not have that control. "
                                         "Use COLOR_ONLY for an editable palette change")
                base["procedural_detail"] = value
                allowed.append((*prefix, "BASE_COLOR", "procedural_detail"))
            elif key == "band_translation":
                if layer.get("surface_role") != "BAND":
                    raise AuthoringError("Band translation requires a geometric band group")
                band = next((m for m in layer["masks"] if m["source"] == "FILTER" and m["effect"]["kind"] == "HISTOGRAM_RANGE"), None)
                if band is None:
                    raise AuthoringError("Original geometric band mask is missing")
                for field in ("black", "white"):
                    shifted = band["effect"][field] + value
                    if not 0 <= shifted <= 1:
                        raise AuthoringError("Translated bands would leave the upright frame")
                    band["effect"][field] = shifted
                    allowed.append(("layers", layer["id"], "masks", band["id"], "effect", field))
            elif key == "paint_loss_opacity":
                if layer.get("surface_role") != "COATING":
                    raise AuthoringError("Paint-loss opacity belongs to a painted-metal coating")
                wear = next((m for m in layer["masks"] if m.get("surface_role") == "PAINT_LOSS"), None)
                if wear is None:
                    raise AuthoringError("The surface's original paint-loss mask is missing")
                wear["strength"] = value
                allowed.append(("layers", layer["id"], "masks", wear["id"], "strength"))
    if "opacity" in values:
        root["opacity"] = values["opacity"]
        allowed.append(("layers", root["id"], "opacity"))
    values_after = document.comparable(after)
    return dict(surface_id=identifier, mode=mode, allowed_paths=allowed, diagnostics=diagnostics,
                expected_values=[dict(path=path, value=values_after.get(tuple(path))) for path in allowed])


def validate_patches(before, after, patches):
    """A constrained patch cannot be bypassed by extra low-level commands."""
    if not patches:
        return
    allowed = {tuple(path) for item in patches for path in item["allowed_paths"]}
    for path, _, _ in document.changes(before, after):
        if path not in allowed:
            raise AuthoringError("Constrained surface revision changes an invariant: " + "/".join(path))
    # Low-level commands must not overwrite a requested patch afterward, even
    # on the same allowed path (for example changing palette alpha to hide it).
    import math
    expected = {tuple(item["path"]): item["value"] for patch in patches for item in patch.get("expected_values", [])}
    actual = document.comparable(after)
    def equal(a, b):
        if type(a) in (int, float) and type(b) in (int, float):
            return math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-8)
        if isinstance(a, list) and isinstance(b, list):
            return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
        return a == b
    if any(not equal(value, actual.get(path)) for path, value in expected.items()):
        raise AuthoringError("A later command overwrote the constrained surface patch")


def describe(ts):
    snap = document.snapshot(ts)
    rows = []
    for layer in snap["layers"]:
        custom = not layer.get("surface_id") and layer["type"] == "FILL"
        if not custom and (not layer.get("surface_id") or layer["id"] != layer["surface_id"]):
            continue
        root, owned = _owned(snap, layer["id"])
        fills = [item for item in owned if item["type"] == "FILL"]
        if not fills:
            continue
        fill = fills[-1]
        color, roughness, height = (_channel(fill, key) for key in ("BASE_COLOR", "ROUGHNESS", "HEIGHT"))
        period = color["material_period_m"] if color["material_mapping_version"] == 1 else None
        wear = next((m for m in fill["masks"] if m.get("surface_role") == "PAINT_LOSS"), None)
        chips = next((m for m in fill["masks"] if m.get("surface_role") == "CHIP_PATTERN"), None)
        placement = next((m for m in fill["masks"] if m.get("surface_role") == "WEAR_PLACEMENT"), wear)
        influence = placement["influence"] if placement else {}
        if root["surface_family"] == "BANDS":
            bands = [next((m for m in l["masks"] if m["source"] == "FILTER" and m["effect"]["kind"] == "HISTOGRAM_RANGE"), None) for l in fills]
            upright = next((m["influence"] for m in fills[0]["masks"] if m["source"] == "SCENE"), None)
            if any(m is None for m in bands) or upright is None:
                rows.append(dict(id=root["id"], family="BANDS", name=root["name"],
                    status="MODIFIED_NATIVE_GRAPH", note="Original band control is missing; inspect native masks", patch_modes=["COLOR_ONLY", "OPACITY_ONLY"]))
                continue
            rows.append(dict(id=root["id"], family="BANDS", name=root["name"],
                controls=dict(color=color["color"], centers=[(m["effect"]["black"] + m["effect"]["white"]) / 2 for m in bands],
                              widths=[m["effect"]["white"] - m["effect"]["black"] for m in bands], opacity=root["opacity"]),
                frame=dict(direction=upright["direction"], bounds_world=upright["bounds"], units="fraction_of_upright_height"),
                mask_ids=[m["id"] for l in owned for m in l["masks"]],
                patch_modes=["COLOR_ONLY", "PLACEMENT_ONLY", "OPACITY_ONLY"]))
            continue
        palette = next((e for e in color["effects"] if e["id"] == fill["id"] + ".palette" and e["kind"] == "GRADIENT_MAP"), None)
        palette_value = [sum(pair) / 2 for pair in zip(palette["color"], palette["color2"])] + [color["color"][3]] if palette else color["color"]
        rough_transfer = next((e for e in roughness["effects"] if e["id"] == fill["id"] + ".roughness-range" and e["kind"] == "LEVELS"), None)
        rough_range = [rough_transfer["output_black"], rough_transfer["output_white"]] if rough_transfer else None
        if not rough_range and not roughness["procedural"] and not roughness.get("image") and not roughness.get("detail_image"):
            rough_range = [roughness["value"], roughness["value"]]
        rows.append(dict(id=root["id"], family="CUSTOM" if custom else root["surface_family"], name=root["name"],
            controls=dict(color=palette_value, roughness_range=rough_range or [max(0, roughness["value"] - roughness["procedural_detail"] * .175), min(1, roughness["value"] + roughness["procedural_detail"] * .175)],
                          period_m=period, height_m=height["material_height_m"] if period else None,
                          opacity=root["opacity"], paint_loss_opacity=wear["strength"] if wear else None,
                          chip_radius_m=chips["material_period_m"] * .28 if chips and chips["material_mapping_version"] and chips["procedural"] == "DOTS" else None,
                          effect_distance_m=influence["distance"] * influence["physical_meters_per_unit"] if influence.get("physical_mapping_version") else None),
            frame=color["material_frame"] if period else ("LEGACY_UV" if color["projection"] == "UV" else "LEGACY_RELATIVE_WORLD"),
            directional_mapping=bool(color.get("material_directional")) if period else False,
            source_ids=sorted({p["material_source_id"] for l in owned for p in l["channels"] if p.get("material_source_id")}),
            mask_ids=[m["id"] for l in owned for m in l["masks"]],
            sampling=physical_space.resolution_diagnostic(ts, period) if period else {"status": "UNKNOWN_PHYSICAL_SIZE"},
            patch_modes=list(CUSTOM_PATCH_MODES) if custom else list(PATCH_FIELDS)))
    return rows


def catalog():
    return {name: dict(defaults=data,
                       aliases={"finish": "new:your_id:finish", "substrate": "new:your_id:substrate (painted metal only)", "paint_loss": "new:your_id:paint_loss (mask on finish)"},
                       controls=["color", "roughness_range", "period_m", "height_m", "frame", "seed", "opacity"] +
                                (["chip_radius_m", "effect_distance_m"] if name == "PAINTED_METAL" else []),
                       chip_radius_definition="Native circular DOTS radius at 50% coverage, before geometry clipping; regular breakup" if name == "PAINTED_METAL" else None,
                       requires_known_scale_for_metric_values=True) for name, data in FAMILIES.items()}


def expand_bands(ts, cmd, root_id, parent_id, allocate, objects=None):
    """Geometric ring graphics in a declared upright frame, ordinary scene masks."""
    import numpy as np
    from ..geometry.snapshots import targets
    axis = np.asarray(cmd.get("direction", [0, 0, 1]), np.float64)
    if np.linalg.norm(axis) < 1e-8:
        raise AuthoringError("Band upright direction cannot be zero")
    axis /= np.linalg.norm(axis)
    with targets(ts) as entries:
        points = np.concatenate([e["positions"][np.unique(e["tri_vertices"])] @ axis for e in entries])
    bounds = [float(points.min()), float(points.max())]
    if bounds[1] - bounds[0] <= 1e-8:
        raise AuthoringError("Band frame has no measurable height")
    centers, width = cmd["centers"], cmd["width"]
    if any(c - width / 2 < 0 or c + width / 2 > 1 for c in centers):
        raise AuthoringError("Every band must fit within the declared upright height")
    if any(abs(a - b) <= width for i, a in enumerate(centers) for b in centers[i + 1:]):
        raise AuthoringError("Bands need a positive clean gap")
    root = assets.new_layer(root_id, cmd["name"], "FOLDER", parent_id)
    root.update(surface_id=root_id, surface_family="BANDS", surface_role="BAND_GROUP", group_mode="ISOLATED")
    root["director_recipe"] = json.dumps(dict(version=1, family="BANDS", source="native_geometric_bands", units="fraction_of_upright_height"))
    result = [root]
    for index, center in enumerate(centers):
        layer = assets.new_layer(allocate(f"band-{index}"), f"Band {index + 1}", parent_id=root_id)
        layer.update(surface_id=root_id, surface_family="BANDS", surface_role="BAND")
        for prop in layer["channels"]:
            prop["enabled"] = prop["channel"] in {"BASE_COLOR", "METALLIC", "ROUGHNESS"}
        _channel(layer, "BASE_COLOR")["color"] = list(cmd["color"])
        _channel(layer, "ROUGHNESS")["value"] = cmd.get("roughness", .25)
        _channel(layer, "METALLIC")["value"] = cmd.get("metallic", 0)
        field = _mask(allocate(f"height-{index}"), "Upright height", "SCENE")
        field["influence"].update(kind="WORLD_HEIGHT", direction=axis.tolist(), bounds=bounds, mode="LIVE", resolution="512", falloff="LINEAR")
        band = _mask(allocate(f"range-{index}"), "Band position and width", "FILTER")
        band["effect"].update(kind="HISTOGRAM_RANGE", black=center - width / 2,
                             white=center + width / 2, softness=min(.0002, width / 20), contrast=1)
        layer["masks"] = [field, band]
        result.append(layer)
    return result
