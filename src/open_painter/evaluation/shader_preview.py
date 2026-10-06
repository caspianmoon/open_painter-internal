"""Open Painter engine sessions: each texture set's compiled graph and GPU evaluator.

RNA edits update the compiled graph's bindings on the calling thread (``notify``);
the scheduler timer (``cache.tick`` → ``tick``) rebuilds changed topology, advances
CPU boundary inputs and has the engine render the changed channels into the set's
images. Materials read those images through the Open Painter Texture Set node,
which does not change when layers, masks or filters do: no edit recompiles a
material. (The module keeps its historical name; the API is the one the rest of
the add-on has always called.)
"""
from time import perf_counter
import json
import logging
import os
from contextlib import contextmanager, nullcontext

import bpy

from . import metrics
from .engine import stages
from .resource_keys import key_for
from ..utils.logging import logger

_sessions = {}
_busy = set()
_batches = {}
_deferred = {}   # session key -> {owner pointer: owner} touched by a stroke while frozen (0.9.0)
_holds = {}      # session key -> perf_counter of the last renewal by a gesture rewriting it (a layer drag) (0.9.7)
# Structural edits recompile their units in the session's own graph (``EngineGraph.patch``);
# OP_NO_PATCH=1 rebuilds instead, for comparisons.
PATCH = os.environ.get('OP_NO_PATCH') != '1'
# After an undo, re-point the retired graph's units instead of compiling them (0: compile all).
UNDO_ADOPTION = os.environ.get('OP_UNDO_ADOPTION', '1') != '0'
VERIFY_UNDO = os.environ.get('OP_VERIFY_UNDO') == '1'   # compare them with a fresh compile
# A publication tags the set's material, not its images (True: the images, for comparisons).
TAG_IMAGES = os.environ.get('OP_TAG_IMAGES') == '1'
_retiring = {}   # session key -> graphs an undo dropped, still counting their leaves until a rebuild adopts them
_carried = {}    # session key -> the engine an undo dropped: its rebuild starts from its results
# A memfile undo that restored only numeric values of a set (``undo_follow``): its retired graph
# follows the restored structs and keeps its engine instead of a rebuild (0: rebuild, for comparisons).
FOLLOW_NUMERIC_UNDO = os.environ.get('OP_FOLLOW_NUMERIC_UNDO', '1') != '0'
_followable = {}   # session key -> the layers the last undo changed other than numerically (undo_follow)
HOLD_SECONDS = 5.0   # a hold no gesture renewed for this long was lost with its modal: release it (0.9.8)


def authoring_structure_changed(scene):
    """Collection mutations have no RNA update callback of their own."""
    for key, state in _batches.items():
        if key[0] == scene.as_pointer():
            state['structural'] = True
            _touch(_sessions.get(key), None)


def _touch(entry, identifiers):
    """The layers a structural edit reached, for the graph's next structure check (None: any)."""
    graph = entry.get('graph') if entry else None
    if graph is not None and hasattr(graph, 'touch_layers'):
        graph.touch_layers(identifiers)


def _known(entry, ts):
    """Every edit up to the set's serial reached the session (a callback or a named
    invalidation). A serial bump nobody reported leaves this behind: the next structure
    check then plans every layer."""
    if entry is not None and entry.get('known') == ts.edit_serial - 1:
        entry['known'] = ts.edit_serial


def _unknown_edits(entry, ts):
    if entry.get('known') != ts.edit_serial:
        _touch(entry, None)


def _handle(owner, layer):
    """How a pending patch finds an edit's owner again: its address, or a layer's id (a layer's
    struct moves when layers are added or removed below it); None when unknown."""
    if layer is None or owner is None:
        return None
    pointer = owner.as_pointer()
    return ('layer', layer.id) if pointer == layer.as_pointer() else pointer


def _drop(entry, owner, layer):
    """An edit the pending patch reads again (``_handle``)."""
    handle = _handle(owner, layer)
    _drop_handles(entry, None if handle is None else {handle})


def _drop_handles(entry, handles):
    """Edits the pending patch reads again; ``handles`` None when unknown (a rebuild, or a patch
    reading every binding)."""
    dropped = entry.get('dropped', set())
    if dropped is not True:
        entry['dropped'] = True if handles is None else dropped | handles


def invalidated(ts, identifiers):
    """``layer_stack.invalidate`` naming the layers it changed."""
    entry = _sessions.get(key_for(ts.id_data, ts))
    if entry is not None:
        _touch(entry, identifiers)
        _known(entry, ts)


@contextmanager
def authoring_batch(ts):
    """Coalesce a bounded authoring transaction; ordinary slider edits stay immediate."""
    key = key_for(ts.id_data, ts)
    # ``owners``: the handles of the edits the batch coalesced (``_handle``); None once one was unknown.
    state = _batches.setdefault(key, dict(depth=0, structural=False, owners=set()))
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
                _busy.add(key)
                try:
                    _update(ts.id_data, ts, entry, check_structure=state['structural'], edited=state['owners'])
                finally:
                    _busy.discard(key)
            elif entry and entry.get('rebuild'):
                _drop_handles(entry, state['owners'])   # the rebuild queued before the batch reads them again


def hold(scene, ts):
    """Keep the current result while a gesture rewrites the stack; returns the key for ``release_hold``.

    A layer drag places the layer at every row it crosses, and each placement
    is a structural edit: without the hold every row would rebuild the graph.
    ``release_hold`` lets the next tick rebuild once for the final arrangement.
    """
    key = key_for(scene, ts)
    _holds[key] = perf_counter()
    return key


def renew_hold(key):
    """Keep a gesture's hold alive: a drag renews on every event it handles."""
    if key in _holds:
        _holds[key] = perf_counter()


def release_hold(key):
    if key in _holds:
        del _holds[key]
        _recheck(key)
        from . import cache
        cache.wake()


def _recheck(key):
    """Notifications were dropped while held (a first stroke links its layer and enables its
    channels): the next update re-derives the structure, whatever edit arrives first."""
    entry = _sessions.get(key)
    if entry is not None:
        entry['recheck'] = True


def _held(key):
    """Whether ``key`` is held, releasing a hold nobody renewed for ``HOLD_SECONDS``."""
    since = _holds.get(key)
    if since is None:
        return False
    if perf_counter() - since < HOLD_SECONDS:
        return True
    logger.warning('Engine hold on %s outlived its gesture (%g s); releasing it', key, HOLD_SECONDS)
    del _holds[key]
    _recheck(key)
    return False


def held(scene, ts):
    return _held(key_for(scene, ts))


def requested(ts):
    """The set is evaluated by the Open Painter engine (the stored identifier is historical)."""
    return getattr(ts, 'preview_backend', 'LIVE') == 'LIVE'


def _store(ts):
    store = ts.layer_store
    return store.as_pointer() if store is not None else None


def _moved(entry, ts):
    """The graph compiled against a set struct that ``texture_sets.add()``/``remove()``
    has since relocated (its ``ts`` reads another set or freed memory), or against the layer
    store the set has since left (a copied scene's set took a copy of it)."""
    graph = entry.get('graph')
    if graph is None:
        return False
    try:
        if graph.ts.as_pointer() != ts.as_pointer():
            return True
        return 'store' in entry and entry['store'] != _store(ts)   # sessions record it when built
    except ReferenceError:
        return True


def _alive(entry, ts):
    try:
        engine = entry.get('engine')
        if engine is not None and engine.scene_uid != getattr(ts.id_data, 'session_uid', None):
            return False
        return not _moved(entry, ts)
    except (ReferenceError, AttributeError):
        return False


def _release(scene, ts, entry):
    """Forget a moved graph (its set struct was relocated); the next tick rebuilds."""
    from . import layer_cache
    graph = entry.pop('graph')
    for binding in graph.cache_bindings:
        layer_cache.discard(scene, ts, binding.layer_id, binding.channel)
    try:
        graph.detach()
    except ReferenceError:
        pass
    entry['rebuild'] = True


def texture_sets_changed(scene):
    """``texture_sets.add()``/``remove()`` relocated the set structs (0.9.7)."""
    from . import cache
    cache.discard_evaluators()
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
    """The set's images show its current stack: built, evaluated, no input pending."""
    if not requested(ts):
        return False
    entry = _sessions.get(key_for(scene, ts))
    return bool(entry and entry.get('graph') and _alive(entry, ts)
                and entry.get('attached', False)
                and not entry.get('rebuild') and not entry.get('recheck') and not entry.get('error')
                and entry.get('serial') == ts.edit_serial
                and not _pending(entry['graph']) and not entry.get('dirty'))


def pending_work(scene, ts):
    """True while the timer still has topology, inputs or channels to finish."""
    if not requested(ts):
        return False
    entry = _sessions.get(key_for(scene, ts))
    if not entry:
        return True
    if entry.get('error') and entry.get('serial') == ts.edit_serial:
        return False
    return bool(entry.get('rebuild') or entry.get('recheck') or not entry.get('attached', False)
                or entry.get('serial') != ts.edit_serial or entry.get('dirty')
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


def session_engine(scene, ts):
    entry = _sessions.get(key_for(scene, ts))
    return entry.get('engine') if entry else None


def diagnostics(scene, ts, *, details=True):
    """Session state; ``details=False`` skips per-layer and engine report walks.

    Timer/status callers need only readiness and errors. Cache-state lookup
    resolves live RNA layers per binding and belongs to explicit diagnostics.
    """
    key = key_for(scene, ts)
    entry = _sessions.get(key, {})
    graph = entry.get('graph')
    pending = _pending(graph) if graph is not None else False
    state = ('CPU' if not requested(ts) else 'FAILED' if entry.get('error') else
             'BOUNDARY' if pending else 'SHADER' if current(scene, ts) else 'QUEUED')
    engine = entry.get('engine')
    recorded = entry.get('diagnostics', {})
    if details and graph is not None and 'unique_images' in recorded and recorded['unique_images'] is None:
        recorded['unique_images'] = len(graph.unique_images())   # left for explicit diagnostics by the build
    return {**recorded, 'state': state,
            'serial': ts.edit_serial, 'bound_serial': entry.get('serial', -1),
            'pending_resources': pending, 'error': entry.get('error', ''),
            'attached': bool(entry.get('attached', False)),
            'rebuild_pending': bool(entry.get('rebuild')),
            'rebuild_scope': entry.get('rebuild_scope'),
            'graph_builds': entry.get('graph_builds', 0),
            'graph_patches': entry.get('graph_patches', 0),   # structural edits applied in place
            'undo_follows': entry.get('undo_follows', 0),   # memfile undos the graph followed (undo_follow)
            'graph_rehydrations': 0,
            'binding_updates': entry.get('binding_updates', 0),
            'update_calls': entry.get('update_calls', 0),
            'resource_steps': entry.get('resource_steps', 0),
            'evaluations': entry.get('evaluations', 0),
            'engine': dict(engine.report) if details and engine is not None else {},
            'frozen': frozen(scene, ts),
            'held': key in _holds,
            'deferred_owners': len(_deferred.get(key, ())),
            'cache_states': (graph.cache_states() if details and graph is not None and hasattr(graph, 'cache_states')
                             and not _moved(entry, ts) else {})}


def mark_attached(scene, ts, attached=True):
    """Record whether the set's material reads the engine's images (the texture set node)."""
    entry = _sessions.setdefault(key_for(scene, ts), {})
    entry['attached'] = bool(attached)


def status(scene, ts):
    info = diagnostics(scene, ts, details=False)
    if info['error']:
        return 'Open Painter engine: ' + info['error']
    if info['frozen']:
        return 'Frozen while painting · catches up after the stroke'
    state = info['state']
    if (state == 'QUEUED' and info['attached'] and not info['rebuild_pending']
            and info['bound_serial'] == info['serial']):
        state = 'SHADER'   # an edit the next tick renders: still live (a slider drag must not flicker)
    return {'CPU': 'CPU texture reference', 'SHADER': 'Open Painter engine · live',
            'BOUNDARY': 'Preparing isolated texture input…',
            'QUEUED': 'Updating textures…'}[state]


def _publish_status(scene, ts, entry):
    """Write the status line when the engine's text changes, never on every tick."""
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
    graph = entry.get('graph')
    error = graph.resource_error() if graph is not None else ''
    entry.update(serial=ts.edit_serial, known=ts.edit_serial, error=error, rebuild=False)


def _log(ts, event, report):
    if logger.isEnabledFor(logging.DEBUG):
        fields = ('parameter_changes', 'affected_channels', 'updated_bindings', 'raster_boundaries_queued',
                  'pending_resources', 'resource_publications', 'submission_ms', 'evaluate_ms', 'channels',
                  'passes', 'error')
        detail = {name: report[name] for name in fields if name in report}
        logger.debug('Engine %s %s revision=%d %s', event, ts.name, ts.edit_serial, json.dumps(detail, sort_keys=True))


def _redraw(scene):
    for window in bpy.context.window_manager.windows:
        if window.scene == scene:
            for area in window.screen.areas:
                if area.type in {'VIEW_3D', 'IMAGE_EDITOR'}:
                    area.tag_redraw()


@contextmanager
def _inputs(entry):
    """CPU inputs stepped between evaluations count toward the next one's stages."""
    started = perf_counter()
    try:
        yield
    finally:
        stage = entry.setdefault('stage', stages.begin())
        stage['inputs_ms'] += (perf_counter() - started) * 1000


def _update(scene, ts, entry, owner=None, check_structure=True, resources=True, values_known=False, edited=None):
    """``edited``: with no ``owner``, the handles of the edits this update applies (a batch's),
    which a patch this update requires reads again; None when unknown."""
    from .shader_graph import RebuildRequired
    if resources and entry.pop('recheck', False):
        check_structure, owner, edited = True, None, None   # edits were dropped while held: re-read everything
        _touch(entry, None)
    started = perf_counter()
    try:
        result = entry['graph'].update(owner, check_structure=check_structure, resources=resources)
    except RebuildRequired as exc:
        entry.update(rebuild=True, error='', rebuild_scope=exc.scope)
        if owner is None and not values_known:
            _drop_handles(entry, edited)   # a batch's edits; unknown ones make the patch read every value
        metrics.count('shader_topology_invalidations')
        from . import cache
        cache.wake()
        return False
    except Exception as exc:
        # Keep the last result on screen and expose a recoverable error.
        entry.update(error=str(exc), serial=ts.edit_serial)
        metrics.count('shader_update_failures')
        _log(ts, 'update failed', {'error': str(exc)})
        return False
    finally:
        stage = entry.setdefault('stage', stages.begin())
        stage['update_ms'] += (perf_counter() - started) * 1000
        stage['updates'] += 1
    _record(entry, ts, result)
    entry['binding_updates'] = (entry.get('binding_updates', 0)
                                + (result.get('updated_bindings', 0) if isinstance(result, dict) else 0))
    entry['update_calls'] = entry.get('update_calls', 0) + 1
    entry['dirty'] = True
    metrics.count('shader_update_calls')
    _log(ts, 'update', result if isinstance(result, dict) else {})
    from . import cache
    cache.wake()
    return True


def ensure(scene, ts):
    """Return the set's compiled graph, (re)building it when the structure changed.

    A failed replacement keeps its predecessor (and the images it produced) on screen.
    """
    if not requested(ts):
        return None
    key = key_for(scene, ts)
    entry = _sessions.get(key)
    if key in _busy:
        return entry.get('graph') if entry else None
    _busy.add(key)
    try:
        if entry and entry.get('graph') and _moved(entry, ts):
            _release(scene, ts, entry)
        if entry and entry.get('graph') and _alive(entry, ts):
            if (entry.get('serial') != ts.edit_serial or entry.get('recheck')) and not entry.get('rebuild'):
                known = entry.get('known') == ts.edit_serial   # every edit was reported, its values applied
                _unknown_edits(entry, ts)   # an edit no callback reported (the set was invalidated)
                _update(scene, ts, entry, values_known=known)
            if entry.get('rebuild') and _patch(scene, ts, entry):
                return entry['graph']
            if not entry.get('rebuild'):
                return entry['graph']
        from .engine.compiler import EngineGraph
        from .engine.runtime import Engine
        previous = entry.get('graph') if entry else None
        # After an undo, the graph it retired: its units are re-pointed to the restored structs
        # (``Graph._follow_undo``) instead of compiled again.
        retired = _retiring.get(key, [None])[-1] if previous is None and UNDO_ADOPTION else None
        undo = retired.undo_index() if retired is not None and hasattr(retired, 'undo_index') else None
        if undo is not None and key in _followable:
            followed = _follow_undo(scene, ts, key, retired, undo)
            if followed is not None:
                return followed
        candidate = EngineGraph(scene, ts)
        from . import gc_policy
        try:
            # Units whose structure and owners are unchanged move over intact.
            with gc_policy.paused():
                if undo is not None:
                    candidate.build(reuse=retired, undo=undo)
                    metrics.count('shader_undo_adopted_units', len(candidate._reused))
                    if VERIFY_UNDO:
                        candidate.verify_undo()
                else:
                    candidate.build(reuse=previous)
        except Exception as exc:
            try:
                candidate.close()
            except Exception:
                pass
            entry = _sessions.setdefault(key, {})
            # The retained predecessor no longer matches the stack: the next
            # revision must retry the build, not bind numeric edits onto it.
            entry.update(error=str(exc), serial=ts.edit_serial, rebuild=previous is not None)
            metrics.count('shader_build_failures')
            if previous is not None:
                return previous
            raise
        entry = _sessions.setdefault(key, {})
        engine = entry.get('engine') or _carried.pop(key, None)
        if engine is None or engine.set_id != ts.id or engine.scene_uid != getattr(scene, 'session_uid', None):
            if engine is not None:
                engine.close()
            engine = Engine(scene, ts)
        else:
            engine.scene = scene   # an undo restored the Scene: its old reference is stale
        engine.adopt(candidate)
        entry.pop('dropped', None)   # the build read every value again
        entry.update(graph=candidate, engine=engine, dirty=True, store=_store(ts),
                     graph_builds=entry.get('graph_builds', 0) + 1, rebuild_scope=None)
        built = getattr(candidate, '_last', {})
        stage = entry.setdefault('stage', stages.begin())
        stage['build_ms'] += float(built.get('build_ms', 0.0))
        stage['builds'] += 1
        stage['rebuilt'] += int(built.get('rebuilt_units', 0))
        stage['reused'] += int(built.get('reused_units', 0))
        _record(entry, ts, dict(getattr(candidate, '_last', {})))
        if previous is not None:
            previous.close()
        for retired in _retiring.pop(key, ()):
            retired.unbind()   # the leaves the rebuilt graph still needs are bound by it now
        metrics.count('shader_runtime_builds')
        from . import gc_policy, profile
        profile.record_graph(scene, ts, candidate)
        gc_policy.allocated()   # frozen once edits pause, so collections stop walking it
        return candidate
    finally:
        _busy.discard(key)


def _patch(scene, ts, entry):
    """Apply a pending structural edit to the session's own graph and engine instead of a
    rebuild (``EngineGraph.patch``). False, leaving the rebuild to ``ensure``, when the graph
    declines or its compile fails: the rebuild then reports the failure as it always has."""
    graph, engine = entry.get('graph'), entry.get('engine')
    if (not PATCH or engine is None or engine.graph is not graph or not hasattr(graph, 'patch')
            or entry.get('rebuild_scope') == 'global'):
        return False
    reconcile = True if entry.get('recheck') else (entry.get('dropped') or False)
    _unknown_edits(entry, ts)
    try:
        report = graph.patch(reconcile=reconcile)
    except Exception as exc:
        from .shader_graph import VERIFY_STRUCTURE
        if VERIFY_STRUCTURE and isinstance(exc, AssertionError):
            raise   # a verified patch disagreed with a full check
        logger.debug('Engine patch of %s fell back to a rebuild: %s', ts.name, exc)
        return False
    if report is None:
        return False
    engine.adopt(graph)
    entry.pop('dropped', None)
    entry.pop('recheck', None)
    entry.update(dirty=True, rebuild_scope=None, graph_patches=entry.get('graph_patches', 0) + 1)
    stage = entry.setdefault('stage', stages.begin())
    stage['build_ms'] += float(report.get('build_ms', 0.0))
    stage['builds'] += 1
    stage['rebuilt'] += int(report.get('rebuilt_units', 0))
    stage['reused'] += int(report.get('reused_units', 0))
    _record(entry, ts, dict(report))
    metrics.count('shader_graph_patches')
    from . import gc_policy, profile
    profile.record_graph(scene, ts, graph, layers={key[1] for key in (*report['patched_units'],
                                                                      *report['retired_units'])})
    gc_policy.allocated()
    return True


def _owners(owner):
    from ..core.layer_store import scene_of
    try:
        scene = scene_of(owner)   # a layer's structs live in its set's store, not on the Scene
    except AttributeError:
        return
    if getattr(scene, 'open_painter', None) is None:
        return
    from ..core.owners import locate
    found = locate(owner)
    if found is not None:
        yield (scene, *found)


def _pass_through(graph, ts, layer):
    """The graph holds no bindings of a bypassed layer, a cached layer or a cached
    folder's child, so it cannot narrow what the CPU reference (export) must redo."""
    layers = ts.layers if layer is None else (layer,)
    return any(graph._skipped(item) or graph._cache_effective(item) != 'LIVE' for item in layers)


def notify(owner, context=None, *, previous_dirty=None, structural=True):
    """Apply a property edit to the compiled graph; the engine renders it on the next tick.

    Initializing RNA collections may fire many callbacks. Those callbacks never
    create partial graphs; their completed operation is picked up by tick.
    ``structural=False`` marks a numeric edit: bindings are written without
    re-deriving the structural signature.
    """
    started = perf_counter()
    result = None
    for scene, ts, layer in _owners(owner):
        from ..core.shared_stacks import publish
        publish(ts)
        if not requested(ts):
            continue
        key = key_for(scene, ts)
        entry = _sessions.get(key)
        if entry and entry.get('graph') is not None and (structural or entry['graph']._stubs):
            _touch(entry, None if layer is None else (layer.id,))
        if key in _batches:
            _known(entry, ts)   # the batch's end applies it
            state = _batches[key]
            state['structural'] |= structural
            if state['owners'] is not None:
                handle = _handle(owner, layer)
                if handle is None:
                    state['owners'] = None
                else:
                    state['owners'].add(handle)
            continue
        if key in _holds:
            continue   # the rebuild after release re-reads structure and values
        entry = _sessions.get(key)
        if key in _busy or not entry or not entry.get('graph') or not _alive(entry, ts):
            continue
        _busy.add(key)
        _known(entry, ts)   # applied below, or recorded for the pending patch
        try:
            if entry.get('rebuild'):
                _drop(entry, owner, layer)
            else:
                updated = _update(scene, ts, entry, owner, check_structure=structural)
                if not updated and entry.get('rebuild'):
                    _drop(entry, owner, layer)   # the edit that needs the patch: its values too
                affected = entry.get('diagnostics', {}).get('affected_channels')
                if (updated and previous_dirty is not None and affected is not None
                        and not _pass_through(entry['graph'], ts, layer)):
                    # The compiler's dependencies may prove that a generic RNA
                    # callback touched fewer channels. Never clear a bake
                    # invalidation from an earlier edit.
                    for output in ts.outputs:
                        output.dirty = bool(previous_dirty.get(output.channel, False)
                                            or output.channel in affected)
            _publish_status(scene, ts, entry)
            result = diagnostics(scene, ts, details=False)   # no walk over every node per edit
        finally:
            _busy.discard(key)
    metrics.sample('shader_callback_seconds', perf_counter() - started)
    return result


def _evaluate(scene, ts, entry, budget=None):
    """Render the set's changed channels into its images; returns the channels written. Through
    pixels (Vulkan) the engine may hold channels back for the next tick (``budget``: see
    ``Engine.evaluate``); the session stays dirty, so not current, until they are published."""
    engine = entry.get('engine')
    if engine is None or entry.get('graph') is None:
        return set()
    from . import dependencies
    from .engine.runtime import EngineUnavailable
    n = int(ts.resolution)
    if any(out.image is not None and tuple(out.image.size) != (n, n) for out in ts.outputs):
        # A resolution switch: the channel images follow the set before the engine writes them.
        from ..core import image_manager
        for out in list(ts.outputs):
            image_manager.output(ts, out.channel)
    from ..core import texture_set_node
    texture_set_node.sync(ts)   # the node samples the images the engine writes (a replaced one too)
    from . import gc_policy
    try:
        # The first evaluation of a graph built from scratch translates every program: the
        # collector waits for the settle, as it did for the build.
        with gc_policy.paused() if gc_policy.unsettled() else nullcontext():
            written = engine.evaluate(epochs=dependencies.image_epochs(), budget=budget)
    except EngineUnavailable as exc:
        entry.update(error=str(exc), serial=ts.edit_serial)
        return set()
    except Exception as exc:
        logger.exception('Open Painter engine evaluation')
        entry.update(error=f'Evaluation failed: {exc}', serial=ts.edit_serial)
        return set()
    if bpy.app.background:
        from .engine.runtime import device
        device().settle()   # no frame ends: let the GPU release what this evaluation let go
    entry['dirty'] = bool(engine.deferred)
    entry['evaluated_at'] = perf_counter()
    from . import gc_policy
    gc_policy.evaluated()
    if engine.deferred:
        from . import cache
        cache.wake()
    entry['evaluations'] = entry.get('evaluations', 0) + 1
    if written:
        publishing = perf_counter()
        _published(scene, ts, written)
        engine.stages['publish_ms'] += (perf_counter() - publishing) * 1000
        if _cpu_viewport(scene):
            engine.stages['publish_path'] += ' + pixels for Cycles'
        _log(ts, 'evaluate', engine.report)
    if written or entry.get('stage', {}).get('builds'):
        stages.record(entry, engine.stages)
    return written


def profile_engine(scene, ts):
    """Evaluate the set once more with every pass timed on the GPU (``Engine.profile``); the
    images end up as they were. Returns the recorded row (its units in ``entry['profile']``)."""
    tick(scene, ts, force_repair=False, ignore_gate=True)
    entry = _sessions.get(key_for(scene, ts))
    if not entry or entry.get('engine') is None or entry.get('graph') is None:
        raise RuntimeError('The Open Painter engine has not built this texture set')
    if entry.get('error'):
        raise RuntimeError(entry['error'])
    from . import dependencies, profile
    engine = entry['engine']
    written = engine.profile(epochs=dependencies.image_epochs())
    if written:
        _published(scene, ts, written)
    row = stages.record(entry, engine.stages)
    profile.record_gpu(scene, ts, entry['profile']['units'])
    return row


def _published(scene, ts, channels):
    """The images changed on the GPU only: have the viewport redraw and restart accumulation."""
    if _cpu_viewport(scene):
        # Cycles reads image pixels on the CPU, never the GPU texture the engine wrote.
        engine = session_engine(scene, ts)
        if engine is not None:
            engine.sync_pixels(channels)
    # EEVEE keeps its temporal history when only a GPU texture changed: a material update
    # restarts it (as live_display's does). An update of the images would too, but Blender's
    # undo then counts every published image as changed since its last step: each undo read
    # them again and rebuilt their textures from CPU pixels that lag behind the GPU result
    # (7-10 ms per 1K channel), before publishing every channel over them anyway.
    if TAG_IMAGES:
        for output in ts.outputs:
            if output.channel in channels and output.image is not None:
                output.image.update_tag()
    else:
        material = getattr(ts, 'material', None)
        if material is not None:
            material.update_tag()
    _redraw(scene)


def _cpu_viewport(scene):
    """A 3D view renders the scene with an engine that samples images on the CPU (Cycles)."""
    if scene.render.engine in {'BLENDER_EEVEE', 'BLENDER_EEVEE_NEXT', 'BLENDER_WORKBENCH'}:
        return False
    for window in bpy.context.window_manager.windows:
        if window.scene == scene and any(area.type == 'VIEW_3D' and area.spaces.active.shading.type == 'RENDERED'
                                         for area in window.screen.areas):
            return True
    return False


def sync_pixels():
    """Copy every published result into its image's pixels (a bake samples them on the CPU).
    Channels a pixel budget held back (Vulkan) are published first: a bake needs every one."""
    for key, entry in list(_sessions.items()):
        engine = entry.get('engine')
        if engine is None or not (engine.dirty_cpu or engine.deferred):
            continue
        try:
            if engine.deferred:
                _evaluate(engine.scene, engine.ts(), entry, budget=False)
            if engine.dirty_cpu:
                engine.sync_pixels()
        except (ReferenceError, StopIteration):
            pass   # the set is gone; prune forgets the session


def tick(scene, ts, *, force_repair=False, ignore_gate=False):
    """Rebuild changed topology, advance isolated inputs, render changed channels."""
    if not requested(ts):
        return False
    key = key_for(scene, ts)
    entry = _sessions.get(key)
    if not force_repair and entry and entry.get('error') and entry.get('serial') == ts.edit_serial:
        return False
    from . import cache
    if not force_repair and not ignore_gate and _held(key):
        # Held by a gesture (see ``hold``): the last result stays on screen.
        return current(scene, ts)
    painting = (not force_repair and not ignore_gate and cache.painting_active(scene))
    if (force_repair or not entry or not entry.get('graph') or not _alive(entry, ts)
            or entry.get('rebuild') or entry.get('recheck') or entry.get('serial') != ts.edit_serial):
        try:
            ensure(scene, ts)
        except Exception as exc:
            entry = _sessions.setdefault(key, {})
            entry.update(error=str(exc), serial=ts.edit_serial, rebuild=False)
            _publish_status(scene, ts, entry)
            return False
        entry = _sessions.get(key)
    if entry is None or entry.get('graph') is None:
        return False
    if not entry.get('attached', False):
        from ..core import material_manager
        try:
            material_manager.repair(ts)
        except Exception as exc:
            entry.update(error=str(exc), serial=ts.edit_serial)
            _publish_status(scene, ts, entry)
            return False
    engine = entry.get('engine')
    restore = getattr(engine, 'restore_evicted', None)   # what a session holds as its engine may not publish
    if restore is not None:
        try:
            restore()
        except Exception as exc:
            entry.update(error='GPU presentation recovery failed: ' + str(exc), serial=ts.edit_serial)
            _publish_status(scene, ts, entry)
            return False
    graph = entry['graph']
    if _pending(graph) and not painting:
        # Frozen while painting: no isolated-input steps until release.
        try:
            with _inputs(entry):
                result = graph.step_resources(budget=.006)
            entry['resource_steps'] = entry.get('resource_steps', 0) + 1
            if isinstance(result, dict):
                from . import profile
                profile.record_boundary(scene, ts, result.pop('owner_timings', None) or {})
                entry['diagnostics'] = {**entry.get('diagnostics', {}), **result}
                if result.get('resource_publications'):
                    entry['dirty'] = True
                _log(ts, 'resource step', result)
            metrics.count('shader_resource_steps')
        except Exception as exc:
            entry.update(error=str(exc), serial=ts.edit_serial)
            metrics.count('shader_resource_failures')
    from . import dependencies
    if entry.get('dirty') or entry.get('image_epoch') != dependencies.image_epoch():
        entry['image_epoch'] = dependencies.image_epoch()
        _evaluate(scene, ts, entry)
    elif not painting and not _pending(graph):
        _prearm(ts, entry)
        from . import gc_policy
        gc_policy.settle()
    _publish_status(scene, ts, entry)
    return current(scene, ts)


def _prearm(ts, entry):
    """Idle: arm the suffix transforms an edit of the selected layer would use."""
    from .engine.suffix import PREARM_DELAY
    engine = entry.get('engine')
    if (getattr(engine, 'prearm', None) is None
            or perf_counter() - entry.get('evaluated_at', 0.0) < PREARM_DELAY):
        return
    if not 0 <= ts.active_layer < len(ts.layers):
        return
    from . import dependencies
    try:
        if engine.prearm(ts.layers[ts.active_layer].id, dependencies.image_epochs()):
            from . import cache
            cache.wake()   # more runs to arm on the next idle tick
    except Exception:
        logger.exception('Open Painter idle suffix arming')


def inputs_changed(scene, ts):
    """Images the set's units bind were swapped or rewritten outside RNA (a gesture preview):
    the next tick re-renders the channels reading them (their stamps see the new identity
    or pixel revision)."""
    entry = _sessions.get(key_for(scene, ts))
    if entry and entry.get('graph') is not None:
        entry['dirty'] = True
        from . import cache
        cache.wake()


def retry(scene, ts):
    entry = _sessions.get(key_for(scene, ts))
    if entry:
        if entry.get('graph') is not None and _alive(entry, ts):
            # A failed isolated input is inherited by every rebuilt graph until
            # an edit re-identifies it; Retry alone requests it again.
            entry['graph'].retry_failures()
        entry.update(error='', rebuild=True)


def flush(scene, ts, pixels=True):
    """Finish isolated inputs, caches and channels; with ``pixels``, copy the result into the
    images' pixel buffers (render and export read pixels on the CPU; the results live on the GPU).
    """
    _resume_set(scene, ts, key_for(scene, ts))
    tick(scene, ts, force_repair=False, ignore_gate=True)
    entry = _sessions.get(key_for(scene, ts))
    if not entry or not entry.get('graph'):
        raise RuntimeError('The Open Painter engine has not built this texture set')
    if entry.get('error'):
        raise RuntimeError(entry['error'])
    from . import profile
    while _pending(entry['graph']):
        try:
            with _inputs(entry):
                result = entry['graph'].step_resources(budget=.006)
        except Exception as exc:
            entry.update(error=str(exc), serial=ts.edit_serial)
            metrics.count('shader_resource_failures')
            raise
        entry['resource_steps'] = entry.get('resource_steps', 0) + 1
        entry['dirty'] = True
        if isinstance(result, dict):
            profile.record_boundary(scene, ts, result.pop('owner_timings', None) or {})
            entry['diagnostics'] = {**entry.get('diagnostics', {}), **result}
    from . import layer_cache
    layer_cache.flush(scene, ts)
    # Cache publication swaps image pointers after the graph was prepared.
    tick(scene, ts, force_repair=False, ignore_gate=True)
    if entry.get('error'):
        raise RuntimeError(entry['error'])
    _evaluate(scene, ts, entry, budget=False)   # render, export and bakes need every channel now
    engine = entry.get('engine')
    if engine is not None and pixels:
        engine.sync_pixels()
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
    """Native image painting: the engine re-renders the channels the painted canvas feeds."""
    project = scene.open_painter
    image = project.paint_image
    if image is None:
        return False
    if project.paint_correction:
        return True   # bake corrections are read at bake time, not by the engine
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
    from ..painting.gpu_sources import prune as prune_strokes
    prune_strokes(live)
    for key in set(_deferred) - set(live):
        _deferred.pop(key, None)
    for key in set(_retiring) - set(live):
        for retired in _retiring.pop(key):
            retired.unbind()
    for key in set(_sessions) - set(live):
        entry = _sessions.pop(key)
        if entry.get('graph'):
            try:
                entry['graph'].close()
            except ReferenceError:
                pass
        if entry.get('engine'):
            entry['engine'].close()


def followable(scene, ts):
    """Whether the set's session is current in structure, so that an undo restoring only numeric
    values can be followed (``cache.on_undo_pre`` takes the set's snapshot then)."""
    if not FOLLOW_NUMERIC_UNDO or not UNDO_ADOPTION or not requested(ts):
        return False
    key = key_for(scene, ts)
    entry = _sessions.get(key)
    graph = entry.get('graph') if entry else None
    if (graph is None or entry.get('engine') is None or entry.get('rebuild') or entry.get('recheck')
            or entry.get('error') or entry.get('known') != ts.edit_serial   # an edit no callback reported
            or key in _holds or key in _batches or not hasattr(graph, 'follow_restored')):
        return False
    return not graph._stubs and graph._dirty_layers is not None and not graph._dirty_layers


def follow_undos(changes):
    """The sessions the undo that just ran can follow (``undo_follow``): key -> the layers it
    changed other than numerically, which the followed graph patches (empty: none)."""
    _followable.update(changes)


def _follow_undo(scene, ts, key, graph, undo):
    """A memfile undo restored only numeric values of the set, or other values of some layers with
    their collections in place: its graph follows the restored structs and keeps its engine
    (``EngineGraph.follow_restored``), patching those layers as a structural edit is applied. None
    when it cannot: the set is built as after any undo."""
    changed = _followable.pop(key, frozenset())
    engine = _carried.get(key)
    follow = getattr(graph, 'follow_restored', None)
    if (follow is None or engine is None or engine.graph is not graph or engine.set_id != ts.id
            or engine.scene_uid != getattr(scene, 'session_uid', None)):
        return None
    from . import gc_policy, profile
    try:
        with gc_policy.paused():
            if not follow(scene, ts, undo, changed):
                metrics.count('shader_undo_follow_refusals')
                return None
    except Exception:
        logger.exception('Open Painter: the set is built again after an undo')
        return None
    if VERIFY_UNDO:
        graph.verify_undo()   # a mismatch raises, as the rebuild's check does
    _carried.pop(key)
    engine.scene = scene   # an undo restored the Scene: its old reference is stale
    engine.adopt(graph)
    entry = _sessions.setdefault(key, {})
    entry.pop('dropped', None)   # every binding was read again
    entry.update(graph=graph, engine=engine, dirty=True, store=_store(ts), rebuild_scope=None,
                 undo_follows=entry.get('undo_follows', 0) + 1,
                 graph_patches=entry.get('graph_patches', 0) + bool(changed))
    built = dict(getattr(graph, '_last', {}))
    stage = entry.setdefault('stage', stages.begin())
    stage['build_ms'] += float(built.get('build_ms', 0.0))
    stage['reused'] += int(built.get('reused_units', 0))
    _record(entry, ts, built)
    for retired in _retiring.pop(key, ()):
        if retired is not graph:
            retired.unbind()
    metrics.count('shader_undo_followed_graphs')
    # A numeric follow keeps every unit and tree: the set's record stands as it is.
    profile.record_graph(scene, ts, graph, layers=frozenset() if not changed and profile.recorded(scene, ts) else None)
    gc_policy.allocated()
    return graph


def clear(undo=False):
    """Forget runtime sessions; the set images keep their last result.

    After an ``undo`` the dropped graphs keep counting the leaves they bind until the
    rebuilt session has adopted them (``ensure``), so no orphan sweep — another set's
    publication included — removes a leaf the rebuild is about to reuse. A file load
    or unregistration forgets them.
    """
    _followable.clear()   # what an earlier undo marked: its graphs answer for another state now
    for key, entry in _sessions.items():
        graph = entry.get('graph')
        if graph is not None:
            try:
                graph.detach()
            except ReferenceError:
                pass
            if undo:
                _retiring.setdefault(key, []).append(graph)
            else:
                graph.unbind()
        engine = entry.get('engine')
        if engine is not None:
            if undo and key not in _carried:
                # Unit results are GPU textures with content stamps, which nothing the undo
                # restored can make stale: the rebuilt graph renders only what differs.
                _carried[key] = engine
                continue
            try:
                engine.close()
            except Exception:
                pass
    if not undo:
        for graphs in _retiring.values():
            for retired in graphs:
                retired.unbind()
        _retiring.clear()
        for engine in _carried.values():
            try:
                engine.close()
            except Exception:
                pass
        _carried.clear()
    _sessions.clear()
    _busy.clear()
    _batches.clear()
    _deferred.clear()
    _holds.clear()
