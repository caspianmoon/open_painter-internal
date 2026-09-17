"""Preflight and register texture mesh maps."""

from ..constants import OWNER
from ..core.layer_stack import invalidate
from ..core.validation import validate
from .cycles_baker import bake
from .maps.curvature import EDGE_MAP_KINDS, bake_curvature_set, edge_map_set_complete
from .maps.utility import UTILITY_KINDS, bake_utility


def register_map(ts, kind, image):
    prop = next((m for m in ts.baked_maps if m.kind == kind), None)
    if prop is None:
        prop = ts.baked_maps.add()
        prop.kind = kind
    previous = prop.image
    prop.image = image
    if previous is not None and previous != image and previous.get(OWNER):
        # The superseded map would otherwise stay in the file through its fake
        # user and be packed on every save. A user-supplied image is never
        # ours to drop; a live shader node may still bind an owned one, which
        # then goes once the graph rebinds.
        previous.use_fake_user = False
        if not previous.users:
            import bpy
            bpy.data.images.remove(previous)
    from ..geometry.provenance import record
    prop.provenance = record(ts, kind)


def register_maps(ts, produced):
    for kind, image in produced.items():
        register_map(ts, kind, image)


def bake_steps(context, ts):
    """Yield between maps; each individual Cycles bake is still synchronous.

    The Bake Doctor's pre-bake tier runs first: errors stop the bake with the
    report retained for the panel, warnings do not.
    """
    from . import doctor
    report = doctor.examine(ts, 'PRE_BAKE')
    if report.errors:
        raise ValueError("Bake check failed: " + " | ".join(
            (f.object_name + ': ' if f.object_name else '') + f.message for f in report.errors))
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
    if cycles_maps:
        from .pairing import groups
        from .cages import validate_topology
        graph = context.evaluated_depsgraph_get()
        for part in groups(ts):
            if part.cage:
                if part.cage.name not in context.view_layer.objects:
                    raise ValueError('Custom cage must be present in the active view layer')
                validate_topology(part.low, part.cage, graph)
    selected_utility = [kind for kind in UTILITY_KINDS if getattr(settings, kind.lower())]
    bevel_kinds = [kind for kind in ("BEVEL_NORMAL", "BEVEL_MASK") if getattr(settings, kind.lower())]
    if not any((settings.normal, settings.ao, settings.curvature, selected_utility, bevel_kinds)):
        raise ValueError("Select at least one map to bake.")
    tasks = []
    if settings.normal:
        tasks.append(("NORMAL", lambda: bake(context, ts, "NORMAL")))
    if settings.ao:
        tasks.append(("AO", lambda: bake(context, ts, "AO")))
    tasks.extend((kind, lambda kind=kind: bake(context, ts, kind)) for kind in bevel_kinds)
    if settings.curvature:
        tasks.append(("CURVATURE", lambda: bake_curvature_set(ts)))
    tasks.extend((kind, lambda kind=kind: bake_utility(ts, kind)) for kind in selected_utility)
    from ..geometry.provenance import record
    expected = {kind: record(ts, kind) for kind, _ in tasks}
    for kind, run in tasks:
        yield kind  # Cancellation boundary before allocating/baking the next map.
        if record(ts, kind) != expected[kind]:
            raise ValueError("Bake inputs changed; restart the bake queue")
        image = run()
        produced = image if isinstance(image, dict) else {kind: image}
        if record(ts, kind) != expected[kind]:
            import bpy
            for item in produced.values():
                bpy.data.images.remove(item)
            raise ValueError("Bake inputs changed; discarded stale map")
        register_maps(ts, produced)
    invalidate(ts)


def bake_selected(context, ts):
    for _ in bake_steps(context, ts):
        pass


def owned_curvature(ts):
    """The Curvature map this add-on baked (never a user-supplied image), or None."""
    prop = next((m for m in ts.baked_maps if m.kind == "CURVATURE" and m.image), None)
    if prop is None:
        return None
    if prop.image.get("open_painter_curvature_method") is None and "BakedCurvature" not in prop.image.name:
        return None
    return prop


def stale_curvature(ts):
    """The owned Curvature map an edge-driven recipe should replace, or None.

    Stale means crease-only (baked before GEOMETRIC_CREASES_V2) or missing the
    thin/wide/extreme companions that Edge Size blends between.
    """
    prop = next((m for m in ts.baked_maps if m.kind == "CURVATURE" and m.image), None)
    if prop is None:
        return None
    from .maps.curvature import METHOD
    method = prop.image.get("open_painter_curvature_method")
    if method is None and "BakedCurvature" not in prop.image.name:
        return None  # a user-supplied image: never replace it
    if method == METHOD and edge_map_set_complete(ts):
        return None
    return prop


def refresh_stale_curvature(ts, required, context=None):
    """Rebake a crease-only Curvature map when an edge-driven recipe uses it.

    Maps baked before GEOMETRIC_CREASES_V2 are empty on smooth geometry, so an
    effect that reads them appears to do nothing. Nothing happens when the map
    is missing (the recipe preflight reports that) or already current. With a
    window context the bake runs as a cooperative modal job and 'QUEUED' is
    returned; the recipe is added right away and reads the new map when it
    lands. Without one (background scripts) it bakes synchronously and returns True.
    """
    if "CURVATURE" not in required or stale_curvature(ts) is None:
        return False
    import bpy
    if context is not None and context.window is not None and not bpy.app.background:
        if queue_edge_refresh(ts, context.scene):
            return 'QUEUED'
    register_maps(ts, bake_curvature_set(ts))
    invalidate(ts)
    return True


def queue_edge_refresh(ts, scene):
    """Start the cooperative edge-map rebake of ``ts``, or defer it behind the
    running one; False when it has to bake here.

    The modal job serves one set at a time and its ``running`` flag is global:
    a job started for another set (auto rebake, the panel button) must not
    pass for this set's rebake, or the recipe added on that promise reads the
    crease-only map for good. The scheduler's rebake tick starts the deferred
    one once the job ends; a synchronous bake under a live job would stall
    the interface the job exists to keep responsive.
    """
    import bpy
    from .jobs import OP_OT_edge_map_refresh
    if not OP_OT_edge_map_refresh.running:
        return bpy.ops.open_painter.edge_map_refresh('INVOKE_DEFAULT', set_id=ts.id) == {'RUNNING_MODAL'}
    if OP_OT_edge_map_refresh.running_set_id == ts.id:
        return True
    from ..evaluation import field_jobs
    if not field_jobs.active():
        return False
    field_jobs.schedule_rebake(scene, ts)
    return True
