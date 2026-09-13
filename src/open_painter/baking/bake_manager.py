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
    from ..geometry.provenance import record
    prop.provenance = record(ts, kind)


def bake_steps(context, ts):
    """Yield between maps; each individual Cycles bake is still synchronous."""
    from .diagnostics import inspect
    errors = [i.message for i in inspect(ts) if i.severity == "ERROR"]
    if errors:
        raise ValueError(" | ".join(errors))
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
    tasks = []
    if settings.normal:
        tasks.append(("NORMAL", lambda: bake(context, ts, "NORMAL")))
    if settings.ao:
        tasks.append(("AO", lambda: bake(context, ts, "AO")))
    if settings.curvature:
        tasks.append(("CURVATURE", lambda: bake_curvature(ts)))
    tasks.extend((kind, lambda kind=kind: bake_utility(ts, kind)) for kind in selected_utility)
    from ..geometry.provenance import record
    expected = {kind: record(ts, kind) for kind, _ in tasks}
    for kind, run in tasks:
        yield kind  # Cancellation boundary before allocating/baking the next map.
        if record(ts, kind) != expected[kind]:
            raise ValueError("Bake inputs changed; restart the bake queue")
        image = run()
        if record(ts, kind) != expected[kind]:
            import bpy
            bpy.data.images.remove(image)
            raise ValueError("Bake inputs changed; discarded stale map")
        register_map(ts, kind, image)
    invalidate(ts)


def bake_selected(context, ts):
    for _ in bake_steps(context, ts):
        pass
