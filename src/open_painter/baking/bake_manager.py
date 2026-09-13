"""Preflight and register texture mesh maps."""

from ..core.layer_stack import invalidate
from ..core.validation import validate
from .cycles_baker import bake
from .maps.curvature import bake_curvature
from .maps.utility import UTILITY_KINDS, bake_utility


def register_map(ts, kind, image):
    prop = next((m for m in ts.baked_maps if m.kind == kind), None)
    if prop is None:
        prop = ts.baked_maps.add()
        prop.kind = kind
    prop.image = image


def bake_selected(context, ts):
    issues = [
        issue
        for issue in validate(ts)
        if "requires a baked" not in issue
        and "Paint image missing" not in issue
        and "Painted mask image" not in issue
    ]
    if issues:
        raise ValueError(" | ".join(issues))
    settings = ts.bake
    low = {ref.object for ref in ts.assignments}
    high = {ref.object for ref in settings.high_objects if ref.object}
    cycles_maps = settings.normal or settings.ao
    if cycles_maps and settings.selected_to_active and not high:
        raise ValueError(
            "High-poly source list is empty. Add sources or disable Bake from High Poly."
        )
    if cycles_maps and low & high:
        raise ValueError("High-poly sources must be separate from low-poly texture-set objects.")
    bake_objects = low | high if cycles_maps else low
    if any(o.name not in context.view_layer.objects for o in bake_objects):
        raise ValueError("All bake objects must be present in the active view layer.")
    if cycles_maps and settings.cage_mode == "CUSTOM":
        if len(low) != 1 or not settings.cage_object or settings.cage_object.type != "MESH":
            raise ValueError("Custom cage requires one low-poly object and a mesh cage.")
        if len(settings.cage_object.data.vertices) != len(next(iter(low)).data.vertices):
            raise ValueError("Custom cage topology must match the low-poly mesh.")
    selected_utility = [kind for kind in UTILITY_KINDS if getattr(settings, kind.lower())]
    if not any((settings.normal, settings.ao, settings.curvature, selected_utility)):
        raise ValueError("Select at least one map to bake.")
    if settings.normal:
        register_map(ts, "NORMAL", bake(context, ts, "NORMAL"))
    if settings.ao:
        register_map(ts, "AO", bake(context, ts, "AO"))
    if settings.curvature:
        register_map(ts, "CURVATURE", bake_curvature(ts))
    for kind in selected_utility:
        register_map(ts, kind, bake_utility(ts, kind))
    invalidate(ts)
