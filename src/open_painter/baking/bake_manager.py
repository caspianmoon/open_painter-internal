"""Preflight and register texture mesh maps."""

from ..core.layer_stack import invalidate
from ..core.validation import validate
from ..utils.logging import logger
from .cycles_baker import bake
from .maps.curvature import EDGE_MAP_KINDS, bake_curvature_set, edge_map_set_complete
from .maps.utility import UTILITY_KINDS, bake_utility


# The map each (set id, kind) released at its last rebake, by image name. A
# released map stays resident for an undo of that rebake; the generation before
# it goes when the next rebake supersedes it (``register_map``) or at the next
# save (``sweep_released``), so a session of rebakes keeps at most one
# superseded map per kind instead of every generation until the file is saved
# and reopened. Names are only meaningful within one file: ``forget`` runs on
# load.
_released = {}


def _released_image(key, image, previous):
    """The map ``key`` released before ``previous``, if it is still ours to remove.

    A name can be taken by a later image once Blender frees the datablock it
    named, so the image must still carry the owner tag and be neither of the
    maps the set holds now; a user (a map property holds one, so does a shader
    node or the artist's material) or a fake user keeps it.
    """
    name = _released.get(key)
    if name is None:
        return None
    import bpy
    from ..constants import OWNER
    older = bpy.data.images.get(name)
    if older is None or older == image or older == previous:
        return None
    if not older.get(OWNER) or older.users or older.use_fake_user:
        return None
    return older


def register_map(ts, kind, image):
    prop = next((m for m in ts.baked_maps if m.kind == kind), None)
    if prop is None:
        prop = ts.baked_maps.add()
        prop.kind = kind
    previous = prop.image
    # Build in scratch images; publish only after the bake and provenance check.
    # Retain the old packed datablock for undo, while this set's explicit image
    # bindings follow the new map. Other texture sets keep their own bindings.
    if previous is not None and previous != image and image.get('op_bake_result') == ts.id:
        from ..constants import OWNER
        from ..core.layer_stack import replay
        if previous.get(OWNER):
            with replay(ts):
                for layer in ts.layers:
                    for channel in layer.channels:
                        for field in ('image', 'detail_image'):
                            if getattr(channel, field) == previous:
                                setattr(channel, field, image)
                    for mask in layer.masks:
                        if mask.image == previous:
                            mask.image = image
                from ..graph import mirror
                tree = mirror.find_tree(ts)
                if tree is not None:
                    with mirror.syncing():
                        for node in tree.nodes:
                            if getattr(node, 'op_kind', '') == 'IMAGE' and node.image == previous:
                                node.image = image
            canonical = f"{ts.name}_Baked{kind.title().replace('_', '')}"
            if previous.name == canonical:
                previous.name = canonical + ' Previous'
            image.name = canonical
            image['open_painter_pixel_revision'] = max(
                image.get('open_painter_pixel_revision', 0),
                previous.get('open_painter_pixel_revision', 0) + 1)
    prop.image = image
    if previous is not None and previous != image:
        # The superseded map would otherwise stay in the file through its fake
        # user and be packed on every save. Only the fake user goes: an undo of
        # the rebake finds the datablock with its pixels, and Blender purges it
        # on the next save once the graph no longer binds it. A user-supplied
        # image is never ours to drop.
        from ..constants import OWNER
        from ..core.layer_stack import release_image
        release_image(previous)
        if previous.get(OWNER):
            key = (ts.id, kind)
            # Every rebake makes fresh datablocks (the edge-map set alone is four
            # float RGBA images), and a zero-user image is resident until the file
            # is reopened, so the generation before ``previous`` is removed now:
            # one undo of the rebake still finds its map with the pixels, only a
            # second undo lands on a map a rebake restores.
            older = _released_image(key, image, previous)
            if older is not None:
                import bpy
                bpy.data.images.remove(older)
            _released[key] = previous.name
    from ..geometry.provenance import record
    prop.provenance = record(ts, kind)


def sweep_released():
    """Remove the released maps nothing binds any more (the save handler, before packing).

    A save writes only bound images, so the released ones would be dropped by
    the next reload either way; removing them here bounds the session's memory
    to the live maps. A released map a shader node or the artist's material
    still binds stays, and stays remembered for a later sweep.
    """
    import bpy
    from ..constants import OWNER
    for key, name in list(_released.items()):
        older = bpy.data.images.get(name)
        if older is None or not older.get(OWNER):
            del _released[key]   # freed already, or the name went to an artist's image
            continue
        if older.users or older.use_fake_user:
            continue   # bound again (an undo, a node, the artist's material): live, not ours
        bpy.data.images.remove(older)
        del _released[key]


def forget():
    """Another file's images may reuse the remembered names."""
    _released.clear()


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
    # Device preflight: the bake device is resolved before any map allocates,
    # and a GPU request Cycles cannot satisfy is reported here and staged as
    # CPU rather than failing inside the first bake call.
    from . import devices
    _device, device_note = devices.resolve(context.scene, settings.device)
    if device_note:
        logger.warning(device_note)
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
    # A user action starts this queue (the bake operator, Bake Back), so each Cycles map
    # flushes the originals' Edit Mode geometry before staging it; the doctor flushed once
    # above, but the queue yields between maps and the artist may edit in between.
    if settings.normal:
        tasks.append(("NORMAL", lambda: bake(context, ts, "NORMAL", flush=True)))
    if settings.ao:
        tasks.append(("AO", lambda: bake(context, ts, "AO", flush=True)))
    tasks.extend((kind, lambda kind=kind: bake(context, ts, kind, flush=True)) for kind in bevel_kinds)
    if settings.curvature:
        tasks.append(("CURVATURE", lambda: bake_curvature_set(ts, context=context)))
    tasks.extend((kind, lambda kind=kind: bake_utility(ts, kind)) for kind in selected_utility)
    from ..geometry.provenance import record
    from . import history
    expected = {kind: record(ts, kind) for kind, _ in tasks}
    history.start(ts, [kind for kind, _ in tasks])
    for kind, run in tasks:
        yield kind  # Cancellation boundary before allocating/baking the next map.
        history.step(ts, kind)
        if record(ts, kind) != expected[kind]:
            raise ValueError("Bake inputs changed; restart the bake queue")
        image = run()
        produced = image if isinstance(image, dict) else {kind: image}
        if record(ts, kind) != expected[kind]:
            import bpy
            from ..core.image_manager import live_bake_pointers
            live = live_bake_pointers(ts)
            for item in produced.values():
                try:
                    # Defensive: cleanup may only remove unpublished images.
                    if item.as_pointer() in live:
                        continue
                    bpy.data.images.remove(item)
                except ReferenceError:
                    pass
            raise ValueError("Bake inputs changed; discarded stale map")
        register_maps(ts, produced)
        history.landed(ts, kind)
    invalidate(ts)


def bake_selected(context, ts):
    from . import history
    try:
        for _ in bake_steps(context, ts):
            pass
    except Exception as exc:
        history.finish(ts, error=str(exc))
        raise
    history.finish(ts)


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

    Stale means pre-Cycles (baked before CYCLES_BEVEL) or missing the
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
    """Rebake a pre-Cycles Curvature map when an edge-driven recipe uses it.

    Maps baked before CYCLES_BEVEL predate the Bevel-shader edge set, so an
    effect that reads them sees stale edges. Nothing happens when the map
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
