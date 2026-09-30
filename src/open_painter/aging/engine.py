"""Keeps a texture set's aging folder in step with its settings, nondestructively.

``apply`` simulates the history the settings describe, turns the state into a
recipe (``document``) and either builds the "Material Aging" folder from it or
re-drives the existing layers' parameters. Only the driven fields move
(opacities, colours, roughness, mask coverage); a field the artist changed since
the engine last wrote it is an override and is left alone until Reset Art
Direction. Layers the artist adds inside the folder are theirs.
"""
import json
from contextlib import contextmanager

import bpy

from . import document, model
from .environments import ENVIRONMENTS, MAINTENANCE, USAGE, Exposure, Maintenance, Usage
from .model import Construction, Event, Segment
from ..core.layer_store import project_of as _project_of

FOLDER_NAME = "Material Aging"
_suspended = 0


@contextmanager
def batch():
    """Suppress the per-property re-drive while several settings change at once."""
    global _suspended
    _suspended += 1
    try:
        yield
    finally:
        _suspended -= 1


def owning_set(owner):
    """The texture set whose aging settings (or period/event rows) ``owner`` is."""
    project = _project_of(owner)
    if project is None:
        return None
    pointer = owner.as_pointer()
    for ts in project.texture_sets:
        aging = ts.aging
        if aging.as_pointer() == pointer or any(p.as_pointer() == pointer for p in aging.periods) \
                or any(e.as_pointer() == pointer for e in aging.events):
            return ts
    return None


# ---- settings -> model ---------------------------------------------------------------

def construction_of(aging):
    coats = [c for c in (aging.conversion, aging.primer, aging.paint, aging.topcoat) if c != "NONE"]
    return Construction(aging.material, tuple(coats), tuple(float(v) for v in aging.paint_color), float(aging.coating_condition))


def exposure_of(aging):
    return Exposure(**{name: float(getattr(aging, name)) for name in Exposure().as_dict()})


def usage_of(aging):
    return Usage(**{name: float(getattr(aging, "use_" + name)) for name in Usage().as_dict()})


def maintenance_of(aging):
    return Maintenance(**{name: float(getattr(aging, "care_" + name)) for name in Maintenance().as_dict()})


def segments_of(aging):
    """The history: the periods when enabled (scaled to the age), else one segment."""
    if aging.use_history and len(aging.periods):
        total = sum(max(0.0, p.years) for p in aging.periods) or 1.0
        scale = float(aging.age_years) / total
        return [Segment(max(0.0, p.years) * scale, ENVIRONMENTS[p.environment], USAGE[p.usage], MAINTENANCE[p.maintenance], p.environment)
                for p in aging.periods]
    return [Segment(float(aging.age_years), exposure_of(aging), usage_of(aging), maintenance_of(aging), aging.environment)]


def events_of(aging):
    return [Event(float(e.year), e.kind) for e in aging.events]


def multipliers_of(aging):
    return {"corrosion": aging.direct_corrosion, "fading": aging.direct_fading, "damage": aging.direct_damage, "dirt": aging.direct_dirt}


def simulate(ts, until=None, samples=None):
    """(physical state, art-directed state) of the set's settings."""
    aging = ts.aging
    construction = construction_of(aging)
    state = model.simulate(construction, segments_of(aging), events_of(aging), seed=int(aging.seed), until=until, samples=samples)
    return state, model.directed(state, multipliers_of(aging))


def signature(aging):
    """Every setting the simulation reads, so equal settings share one prediction."""
    fields = ("material", "conversion", "primer", "paint", "topcoat", "coating_condition", "age_years", "use_history", "environment",
              "usage", "maintenance", "seed", "direct_corrosion", "direct_fading", "direct_damage", "direct_dirt")
    sliders = tuple(float(getattr(aging, name)) for name in
                    tuple(Exposure().as_dict()) + tuple("use_" + n for n in Usage().as_dict()) + tuple("care_" + n for n in Maintenance().as_dict()))
    return (tuple(getattr(aging, name) for name in fields), tuple(float(v) for v in aging.paint_color), sliders,
            tuple((float(p.years), p.environment, p.usage, p.maintenance) for p in aging.periods),
            tuple((float(e.year), e.kind) for e in aging.events))


_predictions = {}


def prediction(ts):
    """Label, readout lines and construction, for the panel (no layers touched).

    Memoised on the settings: the panel asks on every redraw and the simulation
    integrates monthly over the whole history.
    """
    aging = ts.aging
    key = signature(aging)
    cached = _predictions.get(key)
    if cached is not None:
        return cached
    construction = construction_of(aging)
    physical, directed = simulate(ts)
    result = {"label": model.label(directed, construction), "physical": model.readout(physical, construction),
              "directed": model.readout(directed, construction), "construction": construction, "state": directed}
    if len(_predictions) >= 32:
        _predictions.clear()
    _predictions[key] = result
    return result


def recipe_version(ts):
    stored = json.loads(ts.aging.state_data or "{}")
    return int(stored.get("recipe_version", 1)) if folder_of(ts) else document.VERSION


def _document(ts, latest=False):
    result = prediction(ts)
    builder = document
    if not latest and recipe_version(ts) < document.VERSION:
        from . import document_v1 as builder
    return builder.build(result["state"], result["construction"], seed=int(ts.aging.seed))


def _available(ts):
    return {m.kind for m in ts.baked_maps if m.image and all(m.image.size)}


def missing_maps(ts):
    """Mesh maps the aging folder would need but the set has not baked."""
    from ..smart_materials.composer import requirements
    return sorted(requirements(_document(ts)) - _available(ts))


def optional_maps(ts):
    """Mesh maps that would place optional outcomes by the geometry (dust, streaks, growth) but are not baked."""
    from ..smart_materials.composer import MAP_REQUIREMENTS, masks_for
    available = _available(ts)
    wanted = set()
    for spec in _document(ts)["layers"]:
        for settings in masks_for(spec):
            if settings.get("optional") and not settings.get("procedural") and not settings.get("generated_source"):
                wanted |= MAP_REQUIREMENTS.get(settings["kind"], set()) - available
    return sorted(wanted)


def rebuild_pending(ts):
    """Optional masks whose maps were baked after the folder was built: Rebuild adds them."""
    from ..smart_materials.composer import mask_available, masks_for
    folder = folder_of(ts)
    if folder is None:
        return []
    doc = _document(ts)
    by_role = roles_of(ts, folder)
    kinds = []
    for role, spec in zip(doc["roles"], doc["layers"]):
        layer = by_role.get(role)
        if layer is None:
            continue
        paired = {id(settings) for _, settings in mask_pairs(layer, spec)}
        for settings in masks_for(spec):
            if settings.get("optional") and id(settings) not in paired and mask_available(ts, settings) and settings["kind"] not in kinds:
                kinds.append(settings["kind"])
    return kinds


# ---- folder ---------------------------------------------------------------------------

def folder_of(ts):
    identifier = ts.aging.folder_id
    return next((layer for layer in ts.layers if layer.id == identifier and layer.type == "FOLDER"), None) if identifier else None


def members(ts, folder):
    from ..smart_materials.modules import subtree
    return [layer for layer in subtree(ts, folder.id) if layer.id != folder.id]


def roles_of(ts, folder):
    """{role: layer} for the layers the engine created (tagged by role)."""
    result = {}
    for layer in members(ts, folder):
        role = layer.aging_role
        if role and role not in result:
            result[role] = layer
    return result


def _mask_kind(mask):
    """The recipe kind a built mask answers to (procedural and texture masks are recipe NOISE masks)."""
    if mask.source == "BAKED":
        return mask.map_type
    if mask.source == "GENERATOR":
        return mask.generator
    return "NOISE" if mask.source in {"PROCEDURAL", "FILL", "NOISE"} else ""


def mask_pairs(layer, spec):
    """(layer mask index, spec settings) in order; optional spec masks the folder was built without
    are skipped, and a mask chain the artist restructured stops the pairing there."""
    from ..smart_materials.composer import masks_for
    masks = list(layer.masks)
    pairs, index = [], 0
    for settings in masks_for(spec):
        if index < len(masks) and _mask_kind(masks[index]) == settings["kind"]:
            pairs.append((index, settings))
            index += 1
        elif not settings.get("optional"):
            break
    return pairs


def _driven_values(layer, spec):
    """{field: value} the engine drives for one layer, in the document's terms."""
    values = {"opacity": float(spec.get("opacity", 1.0)), "BASE_COLOR.color": tuple(spec["color"]),
              "ROUGHNESS.value": float(spec["roughness"]), "METALLIC.value": float(spec["metallic"])}
    for index, settings in mask_pairs(layer, spec):
        values[f"mask{index}.amount"] = float(settings["amount"])
        if "seed" in settings:
            values[f"mask{index}.seed"] = int(settings["seed"])
        if "strength" in settings:
            values[f"mask{index}.strength"] = float(settings["strength"])
        if "black" in settings:
            values[f"mask{index}.black"] = float(settings["black"])
        if "white" in settings:
            values[f"mask{index}.white"] = float(settings["white"])
    return values


def _read(layer, field):
    from ..core.layer_stack import channel_for
    if field == "opacity":
        return float(layer.opacity)
    if field.startswith("mask"):
        index, name = field[4:].split(".", 1)
        masks = list(layer.masks)
        if int(index) >= len(masks):
            return None
        return float(getattr(masks[int(index)], name))
    channel, name = field.split(".", 1)
    prop = channel_for(layer, channel)
    if prop is None:
        return None
    value = getattr(prop, name)
    return tuple(float(v) for v in value)[:3] if name == "color" else float(value)


def _write(layer, field, value):
    from ..core.layer_stack import channel_for
    if field == "opacity":
        layer.opacity = value
        return
    if field.startswith("mask"):
        index, name = field[4:].split(".", 1)
        masks = list(layer.masks)
        if int(index) < len(masks):
            setattr(masks[int(index)], name, int(value) if name == "seed" else value)
        return
    channel, name = field.split(".", 1)
    prop = channel_for(layer, channel)
    if prop is None:
        return
    if name == "color":
        prop.color = (*value, 1.0)
    else:
        setattr(prop, name, value)


def _same(a, b):
    if a is None or b is None:
        return True
    if isinstance(a, tuple):
        return all(abs(x - y) < 2e-3 for x, y in zip(a, b))
    return abs(a - b) < 2e-3


def apply(ts, rebuild=False):
    """Simulate and drive the aging folder; returns the prediction used."""
    if _suspended:
        return None
    aging = ts.aging
    result = prediction(ts)
    doc = _document(ts, latest=rebuild)
    folder = folder_of(ts)
    stored = json.loads(aging.state_data or "{}")
    resolved, overrides = stored.get("resolved", {}), set(stored.get("overrides", []))
    if folder is None or rebuild or _stale(ts, folder, doc):
        parent_id = None
        if folder is not None:
            from ..core.layer_stack import remove_tree
            parent_id = folder.parent_id      # rebuilt where it was
            remove_tree(ts, folder.id)
        folder = _build(ts, doc, int(aging.seed), parent_id)
        aging.folder_id = folder.id
        resolved, overrides = {}, set()
    from ..evaluation.shader_preview import authoring_batch
    with authoring_batch(ts):
        by_role = roles_of(ts, folder)
        for role, spec in zip(doc["roles"], doc["layers"]):
            layer = by_role.get(role)
            if layer is None:
                continue
            for field, value in _driven_values(layer, spec).items():
                key = f"{role}:{field}"
                current = _read(layer, field)
                if key in resolved and not _same(current, _decode(resolved[key])):
                    overrides.add(key)      # the artist moved it since the engine wrote it
                if key in overrides:
                    continue
                if not _same(current, value):
                    _write(layer, field, value)
                resolved[key] = _encode(value)
    aging.state_data = json.dumps({"version": 1, "recipe_version": doc["version"], "resolved": resolved, "overrides": sorted(overrides),
                                   "label": result["label"], "years": float(aging.age_years)}, sort_keys=True)
    from ..core.layer_stack import invalidate
    invalidate(ts)
    from ..geometry_effects import manager as geometry
    geometry.schedule_follow(ts)     # rust flakes, peel and moss that follow the material catch up (0.10.1)
    return result


def _encode(value):
    return list(value) if isinstance(value, tuple) else value


def _decode(value):
    return tuple(value) if isinstance(value, list) else value


def _stale(ts, folder, doc):
    """The layer roster changed (another material family, coats added or removed): rebuild."""
    have = [layer.aging_role for layer in members(ts, folder) if layer.aging_role]
    return have != list(doc["roles"])


def _build(ts, doc, seed, parent_id=None):
    from ..core import layer_stack as stack
    from ..smart_materials.composer import MissingMeshMaps, _fill, _rollback, _seed, _state, requirements
    from ..smart_materials import generated_sources
    available = {m.kind for m in ts.baked_maps if m.image and all(m.image.size)}
    missing = requirements(doc) - available
    if missing:
        raise MissingMeshMaps(FOLDER_NAME, missing)
    before = _state(ts)
    before_images = {image.as_pointer() for image in bpy.data.images}
    try:
        if parent_id is None:
            active = stack.active(ts)
            parent_id = active.parent_id if active else ""
        if parent_id and not any(layer.id == parent_id and layer.type == "FOLDER" for layer in ts.layers):
            parent_id = ""
        folder = stack.add(ts, "FOLDER", FOLDER_NAME, parent_id)
        folder_id = folder.id
        folder.group_mode = "ISOLATED"
        for index, (role, spec) in enumerate(zip(doc["roles"], doc["layers"])):
            layer = _fill(ts, spec, folder_id, _seed(FOLDER_NAME, index, seed))
            layer.aging_role = role
        folder = next(layer for layer in ts.layers if layer.id == folder_id)
        folder.aging_role = "folder"
        ts.active_layer = stack.index_of(ts, folder_id)
        stack.invalidate(ts)
        return folder
    except Exception:
        _rollback(ts, before)
        generated_sources.discard_unused_new_images(before_images)
        raise


def reset_overrides(ts):
    """Forget the artist's overrides: every driven value is written again."""
    stored = json.loads(ts.aging.state_data or "{}")
    stored["overrides"], stored["resolved"] = [], {}
    ts.aging.state_data = json.dumps(stored, sort_keys=True)
    return apply(ts)


def randomize(ts):
    import random
    ts.aging.seed = random.randint(1, 99999)   # the update callback re-drives the folder


def remove(ts):
    folder = folder_of(ts)
    if folder is not None:
        from ..core.layer_stack import remove_tree
        remove_tree(ts, folder.id)
    ts.aging.folder_id = ""
    ts.aging.state_data = ""


def timeline(ts, years=None):
    """Yearly readouts up to ``years`` (default: the age), for the panel's history strip."""
    samples = []
    simulate(ts, until=years, samples=samples)
    return samples


# ---- portability -----------------------------------------------------------------------

SCALAR_SETTINGS = ("live", "material", "primer", "conversion", "paint", "topcoat", "coating_condition", "age_years", "use_history",
                   "environment", "usage", "maintenance", "seed", "direct_corrosion", "direct_fading", "direct_damage", "direct_dirt",
                   *Exposure().as_dict(), *("use_" + n for n in Usage().as_dict()), *("care_" + n for n in Maintenance().as_dict()))
PERIOD_FIELDS = ("years", "environment", "usage", "maintenance")
EVENT_FIELDS = ("year", "kind")


def export_settings(aging):
    """The settings and the driven-value record, for a material document."""
    data = {name: getattr(aging, name) for name in SCALAR_SETTINGS}
    data["paint_color"] = [float(v) for v in aging.paint_color]
    data["periods"] = [{name: getattr(p, name) for name in PERIOD_FIELDS} for p in aging.periods]
    data["events"] = [{name: getattr(e, name) for name in EVENT_FIELDS} for e in aging.events]
    data["folder_id"] = aging.folder_id
    data["state_data"] = aging.state_data
    data["version"] = 1
    return data


def _assign(owner, name, value):
    try:
        setattr(owner, name, value)
    except (TypeError, ValueError):
        pass      # a material or coating this build does not know keeps the default


def import_settings(aging, data, folder_id):
    """Restore exported settings without re-driving; the document's layers already carry the values."""
    with batch():
        for name in SCALAR_SETTINGS:
            if name in data:
                _assign(aging, name, data[name])
        if "paint_color" in data:
            _assign(aging, "paint_color", tuple(data["paint_color"])[:3])
        aging.periods.clear()
        for record in data.get("periods", []):
            period = aging.periods.add()
            for name in PERIOD_FIELDS:
                if name in record:
                    _assign(period, name, record[name])
        aging.events.clear()
        for record in data.get("events", []):
            event = aging.events.add()
            for name in EVENT_FIELDS:
                if name in record:
                    _assign(event, name, record[name])
        aging.active_period = aging.active_event = 0
        aging.folder_id = folder_id
        aging.state_data = data.get("state_data", "") if folder_id else ""


def follow_animation(scene):
    """Age keyframed on the timeline: re-drive sets whose applied age no longer matches."""
    for ts in scene.open_painter.texture_sets:
        aging = ts.aging
        if not aging.folder_id or not aging.live:
            continue
        stored = json.loads(aging.state_data or "{}")
        if abs(stored.get("years", -1.0) - float(aging.age_years)) > 1e-6:
            apply(ts)
