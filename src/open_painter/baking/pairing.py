"""Explicit, scene-local high/low pairing for Cycles texture bakes.

Names are a convenience for the Match Sources action, never permission to
rename meshes or include an arbitrary object from another scene.
"""

from dataclasses import dataclass
import re
from types import SimpleNamespace


@dataclass(frozen=True)
class Group:
    low: object
    sources: tuple
    cage: object = None


def stem(name, role):
    """Recognize a complete role suffix, including Blender duplicate suffixes."""
    match = re.fullmatch(r"(.+)_" + re.escape(role) + r"(?:\.\d+)?", name)
    return match.group(1) if match else None


def _targets(ts):
    lows = tuple(dict.fromkeys(ref.object for ref in ts.assignments if ref.object))
    if not lows or any(obj.type != "MESH" for obj in lows):
        raise ValueError("Assign low-poly mesh objects before matching bake sources.")
    return lows


def reference_target(ref, lows):
    """Resolve one source row, rejecting ambiguous automatic name matches."""
    target = getattr(ref, "target", None)
    if target is not None:
        if target not in lows:
            raise ValueError(f"Bake target '{target.name}' is not assigned to this texture set.")
        return target
    source = ref.object
    key = stem(source.name, "high") if source else None
    candidates = [low for low in lows if key is not None and stem(low.name, "low") == key]
    if len(candidates) > 1:
        raise ValueError(f"Ambiguous bake source '{source.name}'; choose its target explicitly.")
    return candidates[0] if candidates else None


def groups(ts, *, validate_cages=True):
    """Resolve per-low sources without modifying authored state.

    ALL preserves legacy all-to-each projection. MATCHED requires every source
    row to resolve to exactly one assigned low. Explicit targets also provide
    per-object cages in either mode.
    """
    lows = _targets(ts)
    settings = ts.bake
    refs = tuple(settings.high_objects)
    matched = getattr(settings, "source_mode", "ALL") == "MATCHED"
    projecting = settings.selected_to_active
    if not projecting:
        return [Group(low, (), None) for low in lows]
    custom = settings.cage_mode == "CUSTOM"
    scene = getattr(ts, "id_data", None)
    resolved, all_sources = [], []
    for ref in refs:
        source = ref.object
        if source is None or source.type != "MESH":
            raise ValueError("Every high-poly source row must reference a valid mesh.")
        if source in lows:
            raise ValueError("High-poly sources must be separate from low-poly texture-set objects.")
        if hasattr(scene, "objects") and source.name not in scene.objects:
            raise ValueError(f"Bake source '{source.name}' is not in the texture set's scene.")
        explicit = getattr(ref, "target", None)
        if explicit is not None and explicit not in lows:
            raise ValueError(f"Bake target '{explicit.name}' is not assigned to this texture set.")
        # Legacy ALL mode does not require names, except when a cage must be
        # routed to one of several targets.
        needs_target = matched or explicit is not None or (
            custom and len(lows) > 1 and getattr(ref, "cage", None) is not None
        )
        target = reference_target(ref, lows) if needs_target else None
        if matched and projecting and target is None:
            raise ValueError(f"No low-poly target matches '{source.name}'; choose its target explicitly.")
        resolved.append((ref, target))
        if source not in all_sources:
            all_sources.append(source)
    result = []
    for low in lows:
        sources = tuple(dict.fromkeys(
            ref.object for ref, target in resolved if target == low
        )) if matched else tuple(all_sources)
        if projecting and not sources:
            raise ValueError(f"No high-poly source is assigned to '{low.name}'.")
        cages = tuple(dict.fromkeys(
            ref.cage for ref, target in resolved
            if getattr(ref, "cage", None) is not None and (target == low or len(lows) == 1)
        )) if custom and len(lows) > 1 else ()
        if len(cages) > 1:
            raise ValueError(f"Conflicting custom cages for '{low.name}'.")
        cage = (settings.cage_object if len(lows) == 1 else cages[0] if cages else None) if custom else None
        if validate_cages and custom:
            if cage is None or cage.type != "MESH":
                raise ValueError(f"Custom cage is missing for '{low.name}'.")
            if cage in lows or cage in all_sources:
                raise ValueError(f"Custom cage for '{low.name}' must be a separate mesh.")
            if hasattr(scene, "objects") and cage.name not in scene.objects:
                raise ValueError(f"Custom cage '{cage.name}' is not in the texture set's scene.")
        result.append(Group(low, sources if projecting else (), cage))
    return result


def match_sources(ts, scene):
    """Populate explicit mappings only after every assigned low has a match."""
    lows = _targets(ts)
    entries = []
    keys = {}
    for low in lows:
        if low.name not in scene.objects:
            raise ValueError(f"'{low.name}' is not in the current scene.")
        key = stem(low.name, "low")
        if key is None:
            raise ValueError(f"'{low.name}' needs a '_low' suffix for automatic matching; use explicit targets instead.")
        if key in keys:
            raise ValueError(f"Ambiguous low-poly stem '{key}'; use explicit targets for duplicate lows.")
        keys[key] = low
        sources = sorted((obj for obj in scene.objects if obj.type == "MESH"
                          and obj not in lows and stem(obj.name, "high") == key), key=lambda obj: obj.name)
        if not sources:
            raise ValueError(f"No scene mesh matches '{key}_high' for '{low.name}'.")
        cages = [obj for obj in scene.objects if obj.type == "MESH" and stem(obj.name, "cage") == key]
        if len(cages) > 1:
            raise ValueError(f"Several cages match '{key}'; choose the cage explicitly.")
        entries.extend((source, low, cages[0] if cages else None) for source in sources)
    # Validate using the same routing code before touching a Blender collection.
    planned = groups(SimpleNamespace(
        assignments=ts.assignments, id_data=getattr(ts, "id_data", scene),
        bake=SimpleNamespace(
            high_objects=[SimpleNamespace(object=source, target=low, cage=cage)
                          for source, low, cage in entries],
            source_mode="MATCHED", selected_to_active=True,
            cage_mode=ts.bake.cage_mode, cage_object=ts.bake.cage_object,
        ),
    ), validate_cages=False)
    old = [(ref.object, getattr(ref, "target", None), getattr(ref, "cage", None), ref.name,
            getattr(ref, "ignorable", False))
           for ref in ts.bake.high_objects]
    previous = (ts.bake.source_mode, ts.bake.selected_to_active, ts.bake.active_high, ts.bake.cage_object)
    # Ignore Brush Excludes is authored per source and lives only on the rows: a
    # re-match keeps it for sources that are listed again.
    flags = {source: ignorable for source, _, _, _, ignorable in old if source is not None}
    try:
        ts.bake.high_objects.clear()
        for source, low, cage in entries:
            ref = ts.bake.high_objects.add()
            ref.object, ref.target, ref.cage, ref.name = source, low, cage, source.name
            ref.ignorable = flags.get(source, False)
        ts.bake.source_mode = "MATCHED"
        ts.bake.selected_to_active = True
        ts.bake.active_high = 0
        # Only a found '_cage' partner replaces the visible cage: a hand-picked or
        # created cage ('<low> Cage') must not be cleared under cage_mode CUSTOM.
        if len(lows) == 1 and entries[0][2] is not None:
            ts.bake.cage_object = entries[0][2]
    except Exception:
        ts.bake.high_objects.clear()
        for source, low, cage, name, ignorable in old:
            ref = ts.bake.high_objects.add()
            ref.object, ref.target, ref.cage, ref.name, ref.ignorable = source, low, cage, name, ignorable
        ts.bake.source_mode, ts.bake.selected_to_active, ts.bake.active_high, ts.bake.cage_object = previous
        raise
    return planned
