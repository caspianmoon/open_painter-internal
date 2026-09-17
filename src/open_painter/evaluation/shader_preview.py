"""Shader-native authoring lifecycle, separate from explicit texture baking.

RNA edits update existing shader bindings on the calling thread. The timer only
constructs changed topology and advances isolated unsupported source boundaries;
it never composites the material into channel images for a supported edit.
"""
from time import perf_counter
import json
import logging
from contextlib import contextmanager

import bpy

from . import metrics
from .resource_keys import key_for
from ..utils.logging import logger

_sessions = {}
_busy = set()
_batches = {}
_deferred = {}   # session key -> {owner pointer: owner} touched by a stroke while frozen (0.9.0)
_holds = set()   # session keys a gesture is rewriting (a layer drag): no repair until it ends (0.9.7)


def authoring_structure_changed(scene):
    """Collection mutations have no RNA update callback of their own."""
    for key, state in _batches.items():
        if key[0] == scene.as_pointer():
            state['structural'] = True


@contextmanager
def authoring_batch(ts):
    """Coalesce a bounded authoring transaction; ordinary slider edits stay immediate."""
    key = key_for(ts.id_data, ts)
    state = _batches.setdefault(key, dict(depth=0, structural=False))
    state['depth'] += 1
    try:
        yield
    finally:
        state['depth'] -= 1
        if not state['depth']:
            _batches.pop(key, None)
            entry = _sessions.get(key)
            if (entry and entry.get('graph') and _alive(entry, ts) and key not in _busy
                    and not entry.get('rebuild')):
                # A rebuild queued before the batch already re-reads every value.
                _busy.add(key)
                try:
                    _update(ts.id_data, ts, entry, check_structure=state['structural'])
                finally:
                    _busy.discard(key)


def hold(scene, ts):
    """Keep the attached shader while a gesture rewrites the stack; returns the key for ``release_hold``.

    A layer drag places the layer at every row it crosses, and each placement
    is a structural edit: without the hold the timer rebuilds the graph and
    recompiles the material per row while the mouse is still down. A held
    session skips callbacks and repairs; ``release_hold`` lets the next tick
    rebuild once for the final arrangement (or none when nothing changed).
    """
    key = key_for(scene, ts)
    _holds.add(key)
    return key


def release_hold(key):
    if key in _holds:
        _holds.discard(key)
        from . import cache
        cache.wake()


def held(scene, ts):
    return key_for(scene, ts) in _holds


def requested(ts):
    # Preserve the stored enum identifier used by existing .blend projects.
    return getattr(ts, 'preview_backend', 'LIVE') == 'LIVE'


def _alive(entry, ts):
    try:
        return (entry['material'] == ts.material and ts.material.as_pointer() != 0
                and not _moved(entry, ts))
    except (ReferenceError, AttributeError):
        return False


def _moved(entry, ts):
    """The graph compiled against a set struct that ``texture_sets.add()``/``remove()``
    has since relocated: its ``ts`` reads another set or freed memory."""
    graph = entry.get('graph')
    if graph is None:
        return False
    try:
        return graph.ts.as_pointer() != ts.as_pointer()
    except ReferenceError:
        return True


def _release(scene, ts, entry):
    """Forget a moved graph without closing it: its trees stay on the material,
    where ``ensure`` rehydrates them against the live set."""
    from . import layer_cache
    graph = entry.pop('graph')
    for binding in graph.cache_bindings:
        # Cache jobs check their identity through the graph's closures.
        layer_cache.discard(scene, ts, binding.layer_id, binding.channel)
    try:
        graph.detach()
    except ReferenceError:
        pass
    entry['attached'] = False


def texture_sets_changed(scene):
    """``texture_sets.add()``/``remove()`` relocated the set structs (0.9.7).

    Graphs and cooperative jobs compiled against the old addresses are dropped
    now, not when a tick first dereferences them; sessions rehydrate from their
    materials. Sessions of a removed set are closed by ``prune``.
    """
    from . import cache
    cache.cancel_all()
    pointer = scene.as_pointer()
    for key, entry in _sessions.items():
        if key[0] != pointer or not entry.get('graph'):
            continue
        ts = next((item for item in scene.open_painter.texture_sets if item.id == key[1]), None)
        if ts is not None and _moved(entry, ts):
            _release(scene, ts, entry)


def _pending(graph):
    return bool(getattr(graph, 'pending_resources', False))


def current(scene, ts):
    if not requested(ts):
        return False
    entry = _sessions.get(key_for(scene, ts))
    return bool(entry and _alive(entry, ts) and entry.get('graph')
                and entry.get('attached', False)
                and not entry.get('rebuild') and not entry.get('error')
                and entry.get('serial') == ts.edit_serial
                and not _pending(entry['graph']))


def pending_work(scene, ts):
    """True while the timer still has topology or isolated inputs to finish."""
    if not requested(ts):
        return False
    entry = _sessions.get(key_for(scene, ts))
    if not entry:
        return True
    if entry.get('error') and entry.get('serial') == ts.edit_serial:
        return False
    return bool(entry.get('rebuild') or not entry.get('attached', False)
                or entry.get('serial') != ts.edit_serial
                or (entry.get('graph') is not None and _pending(entry['graph']))
                or bool(_deferred.get(key_for(scene, ts))))


def frozen(scene, ts):
    """A stroke is deferring this set's boundary work (freeze while painting)."""
    from . import cache
    return bool(_deferred.get(key_for(scene, ts))) and cache.painting_active(scene)


def session_graph(scene, ts):
    """The live compiled graph of a set, or None."""
    entry = _sessions.get(key_for(scene, ts))
    if entry and entry.get('graph') and _alive(entry, ts):
        return entry['graph']
    return None


def diagnostics(scene, ts):
    key = key_for(scene, ts)
    entry = _sessions.get(key, {})
    graph = entry.get('graph')
    pending = _pending(graph) if graph is not None else False
    state = ('CPU' if not requested(ts) else 'FAILED' if entry.get('error') else
             'BOUNDARY' if pending else 'SHADER' if current(scene, ts) else 'QUEUED')
    return {**entry.get('diagnostics', {}), 'state': state,
            'serial': ts.edit_serial, 'bound_serial': entry.get('serial', -1),
            'pending_resources': pending, 'error': entry.get('error', ''),
            'attached': bool(entry.get('attached', False)),
            'rebuild_pending': bool(entry.get('rebuild')),
            'rebuild_scope': entry.get('rebuild_scope'),
            'graph_builds': entry.get('graph_builds', 0),
            'graph_rehydrations': entry.get('graph_rehydrations', 0),
            'binding_updates': entry.get('binding_updates', 0),
            'update_calls': entry.get('update_calls', 0),
            'resource_steps': entry.get('resource_steps', 0),
            'frozen': frozen(scene, ts),
            'held': key in _holds,
            'deferred_owners': len(_deferred.get(key, ())),
            'cache_states': (graph.cache_states() if graph is not None and hasattr(graph, 'cache_states')
                             and not _moved(entry, ts) else {})}


def mark_attached(scene, ts, graph=None):
    """Record the graph actually connected by the material surface adapter.

    Compilation alone does not make a graph visible. Passing no graph records
    that an explicit raster adapter temporarily owns the surface connections.
    """
    entry = _sessions.get(key_for(scene, ts))
    if entry and _alive(entry, ts):
        entry['attached'] = graph is not None and entry.get('graph') is graph


def status(scene, ts):
    info = diagnostics(scene, ts)
    if info['error']:
        return 'Shader preview: ' + info['error']
    if info['frozen']:
        return 'Frozen while painting · catches up after the stroke'
    return {'CPU': 'CPU texture reference', 'SHADER': 'Shader native · textures bake on request',
            'BOUNDARY': 'Preparing isolated texture input…',
            'QUEUED': 'Preparing shader graph…'}[info['state']]


def _publish_status(scene, ts, entry):
    """Write the status line when the shader's text changes, never on every tick:
    messages other subsystems leave there (a failed cache job, a rebake, an
    export) must outlive the timer interval."""
    text = status(scene, ts)
    if entry.get('status_text') != text:
        entry['status_text'] = text
        scene.open_painter.status = text


def restate(scene):
    """The next tick writes each session's status again (a transient message ended)."""
    for key, entry in _sessions.items():
        if key[0] == scene.as_pointer():
            entry['status_text'] = None


def _record(entry, ts, result):
    if isinstance(result, dict):
        entry['diagnostics'] = result
    # An isolated input whose raster raised keeps its placeholder bound: a later
    # unrelated edit must not report the graph current over it.
    graph = entry.get('graph')
    error = graph.resource_error() if graph is not None else ''
    entry.update(serial=ts.edit_serial, error=error, rebuild=False)


def _log(ts, event, report):
    if logger.isEnabledFor(logging.DEBUG):
        fields = ('parameter_changes', 'invalidated_nodes', 'affected_channels',
                  'updated_bindings', 'raster_boundaries_queued', 'pending_resources',
                  'resource_publications', 'submission_ms', 'texture_bake_triggered',
                  'gpu_execution_scope', 'error')
        detail = {name: report[name] for name in fields if name in report}
        logger.debug('Shader %s %s revision=%d %s', event, ts.name, ts.edit_serial,
                     json.dumps(detail, sort_keys=True))


def _redraw(scene):
    for window in bpy.context.window_manager.windows:
        if window.scene == scene:
            for area in window.screen.areas:
                if area.type in {'VIEW_3D', 'IMAGE_EDITOR'}:
                    area.tag_redraw()


def _update(scene, ts, entry, owner=None, check_structure=True, resources=True):
    from .shader_graph import RebuildRequired
    try:
        result = entry['graph'].update(owner, check_structure=check_structure, resources=resources)
    except RebuildRequired as exc:
        entry.update(rebuild=True, error='', rebuild_scope=exc.scope)
        metrics.count('shader_topology_invalidations')
        from . import cache
        cache.wake()
        return False
    except Exception as exc:
        # Keep the last valid shader attached, and expose a recoverable error.
        entry.update(error=str(exc), serial=ts.edit_serial)
        metrics.count('shader_update_failures')
        _log(ts, 'update failed', {'error': str(exc), 'texture_bake_triggered': False})
        return False
    _record(entry, ts, result)
    entry['binding_updates'] = (entry.get('binding_updates', 0)
                                + (result.get('updated_bindings', 0) if isinstance(result, dict) else 0))
    entry['update_calls'] = entry.get('update_calls', 0) + 1
    metrics.count('shader_update_calls')
    _log(ts, 'update', result if isinstance(result, dict) else {})
    _redraw(scene)
    return True


def ensure(scene, ts):
    """Return the current graph; building is reserved for material repair.

    A failed replacement retains its predecessor. It does not silently enter
    whole-material CPU composition or delete unrelated material nodes.
    """
    if not requested(ts):
        return None
    key = key_for(scene, ts)
    entry = _sessions.get(key)
    if key in _busy:
        return entry.get('graph') if entry else None
    _busy.add(key)
    try:
        if entry and _moved(entry, ts):
            _release(scene, ts, entry)
        if entry and _alive(entry, ts) and entry.get('graph'):
            if entry.get('serial') != ts.edit_serial and not entry.get('rebuild'):
                _update(scene, ts, entry)
            if not entry.get('rebuild'):
                return entry['graph']
        from .shader_graph import Graph, RebuildRequired
        previous = entry.get('graph') if entry else None
        candidate = None
        rehydrated = False
        failure, scope = None, None
        try:
            if previous is None:
                # After undo/redo or reload the material still carries the last
                # compiled graph: adopt it instead of rebuilding and recompiling.
                candidate = Graph(scene, ts, ts.material)
                rehydrated = candidate.rehydrate()
                if rehydrated:
                    report = dict(candidate._last)
                    # Any uniform that drifted from the authored value is corrected here.
                    try:
                        corrections = candidate.update()
                    except RebuildRequired as exc:
                        corrections, scope = {}, exc.scope
                    except Exception as exc:
                        # The adopted trees are the last valid shader: they stay
                        # attached with a recoverable error, as on a live session,
                        # instead of being deleted for an invalid slider range.
                        corrections, failure = {}, exc
                    candidate._last = {**report, **corrections,
                                       'rehydration_corrections': corrections.get('updated_bindings', 0)}
            if not rehydrated:
                candidate = Graph(scene, ts, ts.material)
                # Units whose structure and owners are unchanged move over intact.
                candidate.build(reuse=previous)
        except Exception as exc:
            if candidate is not None:
                try:
                    candidate.close()
                except Exception:
                    pass
            entry = _sessions.setdefault(key, {'material': ts.material})
            # The retained predecessor no longer matches the stack: the next
            # revision must retry the build, not bind numeric edits onto it.
            entry.update(error=str(exc), serial=ts.edit_serial, rebuild=previous is not None)
            metrics.count('shader_build_failures')
            if previous is not None:
                return previous
            raise
        entry = {'material': ts.material, 'graph': candidate, 'diagnostics': {}, 'attached': False,
                 'status_text': (entry or {}).get('status_text'),
                 'graph_builds': (entry or {}).get('graph_builds', 0) + (0 if rehydrated else 1),
                 'graph_rehydrations': (entry or {}).get('graph_rehydrations', 0) + (1 if rehydrated else 0),
                 'binding_updates': (entry or {}).get('binding_updates', 0),
                 'update_calls': (entry or {}).get('update_calls', 0),
                 'resource_steps': (entry or {}).get('resource_steps', 0)}
        _record(entry, ts, dict(getattr(candidate, '_last', {})))
        if failure is not None:
            entry['error'] = str(failure)
            metrics.count('shader_update_failures')
            _log(ts, 'update failed', {'error': str(failure), 'texture_bake_triggered': False})
        elif scope is not None:
            entry.update(rebuild=True, rebuild_scope=scope)
        _sessions[key] = entry
        if previous is not None:
            previous.close()
        metrics.count('shader_runtime_builds')
        from . import profile
        profile.record_graph(scene, ts, candidate)
        return candidate
    finally:
        _busy.discard(key)


def _owners(owner):
    scene = getattr(owner, 'id_data', None)
    project = getattr(scene, 'open_painter', None)
    if project is None:
        return
    pointer = owner.as_pointer()
    for ts in project.texture_sets:
        if ts.as_pointer() == pointer:
            yield scene, ts, None
            return
        for layer in ts.layers:
            if layer.as_pointer() == pointer:
                yield scene, ts, layer
                return
            if any(prop.as_pointer() == pointer or
                   any(effect.as_pointer() == pointer for effect in prop.effects)
                   for prop in layer.channels):
                yield scene, ts, layer
                return
            if any(mask.as_pointer() == pointer or mask.effect.as_pointer() == pointer
                   or mask.influence.as_pointer() == pointer or mask.mesh.as_pointer() == pointer
                   for mask in layer.masks):
                yield scene, ts, layer
                return
            if any(row.as_pointer() == pointer for row in layer.cache):
                yield scene, ts, layer
                return


def _pass_through(graph, ts, layer):
    """The shader holds no bindings of a bypassed layer, a cached layer or a cached
    folder's child, so it cannot narrow what the CPU reference (export) must redo."""
    layers = ts.layers if layer is None else (layer,)
    return any(graph._skipped(item) or graph._cache_effective(item) != 'LIVE' for item in layers)


def notify(owner, context=None, *, previous_dirty=None, structural=True):
    """Apply a property edit immediately if a shader has already been built.

    Initializing RNA collections may fire many callbacks. Those callbacks never
    create partial graphs; their completed operation is picked up by tick/repair.
    ``structural=False`` marks a numeric edit: the graph writes its bindings
    without re-deriving the structural signature.
    """
    started = perf_counter()
    result = None
    for scene, ts, layer in _owners(owner):
        if not requested(ts):
            continue
        key = key_for(scene, ts)
        if key in _batches:
            _batches[key]['structural'] |= structural
            continue
        if key in _holds:
            # The repair after release re-reads structure and values (ensure).
            continue
        entry = _sessions.get(key)
        if key in _busy or not entry or not entry.get('graph') or not _alive(entry, ts):
            continue
        _busy.add(key)
        try:
            if not entry.get('rebuild'):
                updated = _update(scene, ts, entry, owner, check_structure=structural)
                affected = entry.get('diagnostics', {}).get('affected_channels')
                if (updated and previous_dirty is not None and affected is not None
                        and not _pass_through(entry['graph'], ts, layer)):
                    # Compiler dependencies may prove that a generic RNA
                    # callback touched fewer channels (or no active input).
                    # Never clear a bake invalidation from an earlier edit.
                    for output in ts.outputs:
                        output.dirty = bool(previous_dirty.get(output.channel, False)
                                            or output.channel in affected)
            _publish_status(scene, ts, entry)
            result = diagnostics(scene, ts)
        finally:
            _busy.discard(key)
    metrics.sample('shader_callback_seconds', perf_counter() - started)
    return result


def tick(scene, ts, *, force_repair=False, ignore_gate=False):
    """Advance topology/source preparation only, never full channel rasters."""
    if not requested(ts):
        return False
    key = key_for(scene, ts)
    entry = _sessions.get(key)
    if not force_repair and entry and entry.get('error') and entry.get('serial') == ts.edit_serial:
        return False
    from . import cache
    if (not force_repair and not ignore_gate and cache.painting_active(scene) and entry and entry.get('graph')
            and _alive(entry, ts) and entry.get('attached', False) and not entry.get('rebuild')
            and not entry.get('error') and entry.get('serial') == ts.edit_serial):
        # Frozen while painting: no repair, no isolated-input steps until release.
        _publish_status(scene, ts, entry)
        return current(scene, ts)
    if not force_repair and not ignore_gate and key in _holds:
        # Held by a gesture (see ``hold``): the last attached shader stays as is.
        return current(scene, ts)
    if (force_repair or not entry or not entry.get('graph') or not _alive(entry, ts)
            or not entry.get('attached', False)
            or entry.get('rebuild') or entry.get('serial') != ts.edit_serial):
        from ..core import material_manager
        try:
            material_manager.repair(ts)
        except Exception as exc:
            entry = _sessions.setdefault(key, {'material': ts.material})
            entry.update(error=str(exc), serial=ts.edit_serial, rebuild=False)
            _publish_status(scene, ts, entry)
            return False
        entry = _sessions.get(key)
    if entry and entry.get('graph') and _pending(entry['graph']):
        try:
            result = entry['graph'].step_resources(budget=.006)
            entry['resource_steps'] = entry.get('resource_steps', 0) + 1
            if isinstance(result, dict):
                from . import profile
                profile.record_boundary(scene, ts, result.pop('owner_timings', None) or {})
                entry['diagnostics'] = {**entry.get('diagnostics', {}), **result}
                _log(ts, 'resource step', result)
            metrics.count('shader_resource_steps')
        except Exception as exc:
            entry.update(error=str(exc), serial=ts.edit_serial)
            metrics.count('shader_resource_failures')
        _redraw(scene)
    if entry is not None:
        _publish_status(scene, ts, entry)
    return current(scene, ts)


def retry(scene, ts):
    entry = _sessions.get(key_for(scene, ts))
    if entry:
        entry.update(error='', rebuild=True)


def flush(scene, ts):
    """Finish isolated resource jobs before save/render; never bake the stack."""
    _resume_set(scene, ts, key_for(scene, ts))
    tick(scene, ts, force_repair=False, ignore_gate=True)
    entry = _sessions.get(key_for(scene, ts))
    if not entry:
        raise RuntimeError('Shader graph was not prepared')
    if entry.get('error'):
        raise RuntimeError(entry['error'])
    from . import profile
    while _pending(entry['graph']):
        try:
            result = entry['graph'].step_resources(budget=.006)
        except Exception as exc:
            entry.update(error=str(exc), serial=ts.edit_serial)
            metrics.count('shader_resource_failures')
            raise
        entry['resource_steps'] = entry.get('resource_steps', 0) + 1
        if isinstance(result, dict):
            profile.record_boundary(scene, ts, result.pop('owner_timings', None) or {})
            entry['diagnostics'] = {**entry.get('diagnostics', {}), **result}
    return entry['graph']


def paint_touch(owner):
    """A native brush wrote ``owner``'s image while the project freezes (0.9.0).

    The image binding is refreshed so the canvas shows, but no structural walk,
    boundary re-identification or resource request happens; the owner is
    remembered for one catch-up update after the stroke (``resume``).
    """
    for scene, ts, _layer in _owners(owner):
        if not requested(ts):
            continue
        key = key_for(scene, ts)
        entry = _sessions.get(key)
        if key in _busy or not entry or not entry.get('graph') or not _alive(entry, ts):
            continue
        _busy.add(key)
        try:
            if not entry.get('rebuild'):
                _update(scene, ts, entry, owner, check_structure=False, resources=False)
            _deferred.setdefault(key, {})[owner.as_pointer()] = owner
            metrics.count('shader_paint_deferrals')
            _publish_status(scene, ts, entry)
        finally:
            _busy.discard(key)


def _resume_set(scene, ts, key):
    owners = _deferred.pop(key, None)
    if not owners or not requested(ts):
        return False
    entry = _sessions.get(key)
    if key in _busy or not entry or not entry.get('graph') or not _alive(entry, ts) or entry.get('rebuild'):
        return False
    targets = list(owners.values())
    try:
        for owner in targets:
            owner.as_pointer()
    except ReferenceError:
        targets = [None]   # a removed owner: one full binding pass instead
    _busy.add(key)
    try:
        for owner in targets:
            _update(scene, ts, entry, owner, check_structure=False)
        metrics.count('shader_paint_resumes')
        _publish_status(scene, ts, entry)
    finally:
        _busy.discard(key)
    return True


def resume(scene):
    """Apply every update deferred during a stroke: one boundary regeneration per changed input."""
    project = getattr(scene, 'open_painter', None)
    if project is None:
        return
    for ts in project.texture_sets:
        _resume_set(scene, ts, key_for(scene, ts))


def native_paint_input(scene):
    """Native image painting updates shader Image nodes without canvas readback."""
    project = scene.open_painter
    image = project.paint_image
    if image is None:
        return False
    if project.paint_correction:
        return True   # bake corrections are read at bake time, not by the shader
    ts = next((item for item in project.texture_sets if item.id == project.paint_set_id), None)
    if ts is None or not requested(ts):
        return False
    layer = next((item for item in ts.layers if item.id == project.paint_layer_id), None)
    if layer is None:
        return False
    from ..painting.channel_paint import mark_changed
    from ..core.layer_stack import channel_for
    is_mask = bool(project.paint_mask_id or project.paint_mask)
    channel = project.paint_target_channel or project.paint_channel
    owner = (next((mask for mask in layer.masks if mask.id == project.paint_mask_id), None)
             if is_mask else channel_for(layer, channel))
    if owner is None or getattr(owner, 'image', None) != image:
        return False
    from . import cache
    touch = paint_touch if cache.painting_active(scene) else notify
    image['open_painter_pixel_revision'] = image.get('open_painter_pixel_revision', 0) + 1
    mark_changed(ts, layer, channel, mask=is_mask)
    touch(owner)
    for entry in list(project.paint_canvases)[1:]:
        member = next((item for item in project.texture_sets if item.id == entry.set_id), None)
        member_layer = next((item for item in member.layers if item.id == entry.layer_id), None) if member else None
        if member is None or member_layer is None or entry.image is None or not entry.image.is_dirty:
            continue
        if not requested(member):
            continue
        prop = channel_for(member_layer, channel)
        if prop is None or prop.image != entry.image:
            continue
        entry.image['open_painter_pixel_revision'] = entry.image.get('open_painter_pixel_revision', 0) + 1
        mark_changed(member, member_layer, channel, mask=False)
        touch(prop)
    metrics.count('shader_native_paint_scan_skips')
    return True


def prune(live):
    for key in set(_deferred) - set(live):
        _deferred.pop(key, None)
    for key in set(_sessions) - set(live):
        entry = _sessions.pop(key)
        if entry.get('graph'):
            try:
                entry['graph'].close()
            except ReferenceError:
                # Loading a file can invalidate the previous scene's RNA before
                # Blender invokes our persistent load handler.
                pass


def clear():
    """Forget runtime bindings while retaining the last material in Blender.

    Disabling the extension must not disconnect an otherwise usable material.
    The next successful graph build replaces detached owned groups. Deleted
    texture-set owners still use prune/close for ordinary resource cleanup.
    """
    for entry in _sessions.values():
        graph = entry.get('graph')
        if graph is not None:
            try:
                graph.detach()
            except ReferenceError:
                pass
    _sessions.clear()
    _busy.clear()
    _batches.clear()
    _deferred.clear()
    _holds.clear()
