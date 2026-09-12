"""Preflight and register Normal, AO and derived Curvature maps."""

from ..core.layer_stack import invalidate
from ..core.validation import validate
from .cycles_baker import bake
from .maps.curvature import bake_curvature


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
    if settings.selected_to_active and not high:
        raise ValueError(
            "High-poly source list is empty. Add sources or disable Bake from High Poly."
        )
    if low & high:
        raise ValueError("High-poly sources must be separate from low-poly texture-set objects.")
    if any(o.name not in context.view_layer.objects for o in low | high):
        raise ValueError("All bake objects must be present in the active view layer.")
    if settings.cage_mode == "CUSTOM":
        if len(low) != 1 or not settings.cage_object or settings.cage_object.type != "MESH":
            raise ValueError("Custom cage requires one low-poly object and a mesh cage.")
        if len(settings.cage_object.data.vertices) != len(next(iter(low)).data.vertices):
            raise ValueError("Custom cage topology must match the low-poly mesh.")
    if not any((settings.normal, settings.ao, settings.curvature)):
        raise ValueError("Select at least one map to bake.")
    if settings.normal or settings.curvature:
        register_map(ts, "NORMAL", bake(context, ts, "NORMAL"))
    if settings.ao:
        register_map(ts, "AO", bake(context, ts, "AO"))
    if settings.curvature:
        register_map(ts, "CURVATURE", bake_curvature(ts))
    invalidate(ts)
