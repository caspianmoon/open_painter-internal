"""Cooperative refresh of cached layers (0.9.0): debounced, budgeted, published atomically.

A layer whose ``cache_mode`` is CACHED or FROZEN is sampled in the shader from
one managed image per channel (``OP_LayerCache`` rows) instead of compiling
its content, masks or — for an isolated folder — its whole subtree. The
compiler compares the row's ``provenance`` with the live identity of that
content; when they differ the layer is *stale*. CACHED layers request a job
here and keep showing the previous image; ``tick`` (called from the scheduler
timer) starts the job once the inputs have been quiet for ``DEBOUNCE`` seconds
and nobody is painting, advances it in strips under a time budget and publishes
the result: a new packed image, the pointer swap and the provenance token. A
FROZEN layer never refreshes on its own; ``refresh`` is the explicit path.

A job whose inputs changed underneath it never publishes. Publications touch
only cache rows, so they neither dirty authored outputs nor re-identify the
content they were built from.
"""
from time import monotonic, perf_counter

import bpy
import numpy as np

from ..constants import OWNER
from . import metrics, purpose

DEBOUNCE = .3
TAG = 'open_painter_layer_cache'
PLACEHOLDER = 'OP Cache Placeholder'
_requests = {}   # (scene pointer, set id, layer id, channel) -> request dict
_jobs = {}       # same key -> running job dict
_retired = []    # superseded cache images a sampler still referenced when they were replaced
_paused_at = 0.0
paused = None    # predicate(scene) -> bool, installed by the scheduler (freeze while painting)


def active():
    """The engine runs whenever the scheduler timer does (never in plain background scripts)."""
    from . import cache
    return bpy.app.timers.is_registered(cache.tick)


def key_for(scene, ts, layer_id, channel):
    return (scene.as_pointer(), ts.id, layer_id, channel)


def request(scene, ts, layer_id, channel, signature, size, factory, current, name='', immediate=False):
    """Ask for a cache image of ``layer_id``/``channel`` built from ``signature``.

    ``factory()`` returns an iterator of ``(low_row, rows)`` strips; ``current()``
    returns the identity the layer has right now, checked again before the job
    starts and before it publishes. A request with a different signature
    supersedes the previous one (and drops a running job); the same signature
    only refreshes the closures so a rebuilt graph keeps the debounce clock.
    """
    key = key_for(scene, ts, layer_id, channel)
    entry = _requests.get(key)
    since = float('-inf') if immediate else monotonic()
    if entry is not None and entry['signature'] == signature:
        entry.update(factory=factory, current=current, name=name or entry['name'])
        entry['since'] = min(entry['since'], since)
    else:
        _requests[key] = {'signature': signature, 'since': since, 'size': int(size),
                          'factory': factory, 'current': current, 'name': name}
        job = _jobs.get(key)
        if job is not None and job['signature'] != signature:
            _jobs.pop(key, None)   # a superseded cache is never published
        metrics.count('layer_cache_requests')
    from . import cache
    cache.wake()


def discard(scene, ts, layer_id, channel=None):
    """Drop requests and jobs of a layer (or one channel) whose cache is current again."""
    for table in (_requests, _jobs):
        for key in [k for k in table if k[:3] == (scene.as_pointer(), ts.id, layer_id)
                    and (channel is None or k[3] == channel)]:
            table.pop(key, None)


def pending(scene, ts=None, layer_id=None):
    pointer = scene.as_pointer()
    prefix = (pointer,) + ((ts.id,) if ts is not None else ()) + ((layer_id,) if layer_id is not None else ())
    return any(key[:len(prefix)] == prefix for key in _requests) or \
        any(key[:len(prefix)] == prefix for key in _jobs)


def progress(scene):
    """(name, fraction) of the running job of a scene, or None."""
    pointer = scene.as_pointer()
    for key, job in _jobs.items():
        if key[0] == pointer:
            return job['name'], job['progress']
    return None


def _resolve(scene, key):
    ts = next((item for item in scene.open_painter.texture_sets if item.id == key[1]), None)
    if ts is None:
        return None, None, None
    layer = next((item for item in ts.layers if item.id == key[2]), None)
    if layer is None:
        return ts, None, None
    row = next((item for item in layer.cache if item.channel == key[3]), None)
    return ts, layer, row


def _start(scene, key, entry):
    """Create the job for one request, or None when it must not start (yet)."""
    ts, layer, row = _resolve(scene, key)
    if ts is None or layer is None or row is None or layer.cache_mode == 'LIVE':
        return None
    try:
        if entry['current']() != entry['signature']:
            return None   # inputs moved on; the compiler re-requests
        with purpose.preview():
            steps = entry['factory']()
    except Exception as error:
        metrics.count('layer_cache_failures')
        _requests.pop(key, None)
        _set_error(scene, str(error))
        return None
    n = entry['size']
    return {'signature': entry['signature'], 'steps': steps, 'progress': 0.0, 'name': layer.name,
            'data': np.ones((n, n, 4), np.float32), 'covered': np.zeros(n, np.bool_), 'ms': 0.0,
            'size': n, 'channel': key[3], 'current': entry['current']}


def _orphaned(scene, key):
    """The job's layer was removed or set back to Live while its refresh ran.

    Neither path discards the job (the rebuilt graph holds no binding for the
    layer), and it would keep the tick busy until the strips of a layer nobody
    samples any more are complete.
    """
    ts, layer, row = _resolve(scene, key)
    return ts is None or layer is None or row is None or layer.cache_mode == 'LIVE'


def _advance(job):
    """One strip; True when the job is complete."""
    started = perf_counter()
    try:
        with purpose.preview():
            low, rows = next(job['steps'])
    except StopIteration:
        job['ms'] += (perf_counter() - started) * 1000
        return True
    rows = np.asarray(rows, np.float32)
    high = low + rows.shape[0]
    data = job['data']
    if rows.ndim == 2:
        data[low:high, :, :3] = rows[..., None]
    elif rows.shape[-1] == 4:
        data[low:high] = rows
    else:
        data[low:high, :, :3] = rows
    job['covered'][low:high] = True
    job['progress'] = float(job['covered'].mean())
    job['ms'] += (perf_counter() - started) * 1000
    return False


def tick(scene, budget=.008):
    """Advance this scene's cache work; returns True when something ran."""
    global _paused_at
    if paused is not None and paused(scene):
        _paused_at = monotonic()
        return False
    pointer = scene.as_pointer()
    now = monotonic()
    _sweep_retired()
    running = next((key for key in _jobs if key[0] == pointer), None)
    if running is not None and _orphaned(scene, running):
        _jobs.pop(running, None)
        running = None
    if running is None:
        for key, entry in list(_requests.items()):
            if key[0] != pointer or now - max(entry['since'], _paused_at) < DEBOUNCE:
                continue
            job = _start(scene, key, entry)
            _requests.pop(key, None)
            if job is None:
                continue
            _jobs[key] = job
            running = key
            break
        if running is None:
            return False
    job = _jobs[running]
    deadline = monotonic() + budget
    while True:
        try:
            done = _advance(job)
        except Exception as error:
            _jobs.pop(running, None)
            metrics.count('layer_cache_failures')
            _set_error(scene, str(error))
            break
        if done:
            _jobs.pop(running, None)
            _finish(scene, running, job)
            break
        if monotonic() >= deadline:
            break
    return True


def _set_error(scene, message):
    scene.open_painter.status = f'Layer cache failed: {message}'


def _finish(scene, key, job):
    """Publish a completed job: a new packed image, the row pointer and its provenance."""
    from ..core.image_manager import create
    from .shader_preview import notify
    ts, layer, row = _resolve(scene, key)
    if ts is None or layer is None or row is None or layer.cache_mode == 'LIVE':
        return
    try:
        if job['current']() != job['signature']:
            return   # inputs moved on while the strips ran; the compiler re-requests
    except Exception as error:
        metrics.count('layer_cache_failures')
        _set_error(scene, str(error))
        return
    if not job['covered'].all() or not np.isfinite(job['data']).all():
        _set_error(scene, f'incomplete cache for {layer.name}')
        return
    n = job['size']   # the requesting graph's size; a size mismatch re-requests through its identity
    name = f'{ts.name} · {layer.name} · {job["channel"]} Cache'
    image = create(name, n)
    image[TAG] = layer.id
    # Not image_manager.write: that reports an authored image edit (dirty
    # consumers, serial bump). A cache publication changes preview state only.
    image.pixels.foreach_set(job['data'].ravel())
    image.update()
    image['open_painter_pixel_revision'] = 1
    previous = row.image
    row.image = image
    row.provenance = job['signature']
    row.refresh_serial += 1
    metrics.count('layer_cache_publications')
    metrics.count('layer_cache_written_bytes', job['data'].nbytes)
    from . import profile
    profile.record_refresh(scene, ts, layer.id, job['ms'])
    notify(row, structural=False)   # the shader sampler now reads the new image
    if previous is not None and previous is not image and previous.get(TAG):
        _retire(previous)
        if image.name != name and bpy.data.images.get(name) is None:
            image.name = name


def _retire(image):
    """Release a superseded cache image once nothing samples it any more.

    ``notify`` rebinds the sampler at once only when the session can take an
    update: with a rebuild pending, inside a batch or in a standing error the
    node keeps its reference until the next build. A cache image is regenerable,
    so it loses its fake user now (an orphan is not written into the file) and
    is removed by the sweep when its last user is gone.
    """
    from .shader_resources import bound
    image.use_fake_user = False
    if bound(image):
        _retired.append(image)
        return
    bpy.data.images.remove(image)


def _sweep_retired():
    from .shader_resources import bound
    for image in list(_retired):
        try:
            if bound(image):
                continue
            bpy.data.images.remove(image)
        except (ReferenceError, RuntimeError):
            pass   # already freed (a file load, an undo)
        _retired.remove(image)


def flush(scene, ts=None, layer_id=None):
    """Run every pending request and job (of a scene, set or layer) to completion now."""
    pointer = scene.as_pointer()
    prefix = (pointer,) + ((ts.id,) if ts is not None else ()) + ((layer_id,) if layer_id is not None else ())
    # One job at a time: a request allocates its full-resolution buffer when it
    # starts, so starting every channel of a layer first would hold them all.
    for key, entry in list(_requests.items()):
        if key[:len(prefix)] != prefix:
            continue
        _requests.pop(key, None)
        job = _start(scene, key, entry)
        if job is not None:
            _jobs[key] = job
            _run(scene, key)
    for key in [k for k in _jobs if k[:len(prefix)] == prefix]:
        _run(scene, key)
    _sweep_retired()


def _run(scene, key):
    """Drain one job to completion and publish it."""
    job = _jobs.pop(key, None)
    if job is None or _orphaned(scene, key):
        return
    try:
        while not _advance(job):
            pass
    except Exception as error:
        metrics.count('layer_cache_failures')
        _set_error(scene, str(error))
        return
    _finish(scene, key, job)


def refresh(scene, ts, layer_id):
    """Explicit refresh of every channel cache of a layer, whatever its mode; waits for it."""
    from . import shader_preview
    graph = shader_preview.session_graph(scene, ts)
    if graph is None:
        raise RuntimeError('Shader graph was not prepared')
    layer = next((item for item in ts.layers if item.id == layer_id), None)
    if layer is None or layer.cache_mode == 'LIVE':
        raise ValueError('The layer is not cached')
    requested = graph.request_caches(layer_id, force=True)
    if not requested:
        raise ValueError('The layer has no cached channels in this shader')
    flush(scene, ts, layer_id)


def _drop_work(layer_id):
    """Forget the requests and running jobs of a layer nothing will sample the result of."""
    for table in (_requests, _jobs):
        for key in [k for k in table if k[2] == layer_id]:
            table.pop(key, None)


def _unbind_rows(layer):
    """Detach the images of a layer's cache rows and forget what they were built from.

    The rows themselves stay: compiled bindings may keep their addresses, and a
    row without an image samples the placeholder until the next publication.
    Returns the detached cache images.
    """
    detached = []
    for row in layer.cache:
        image = row.image
        row.image = None
        row.provenance = ''
        if image is not None and image.get(TAG):
            detached.append(image)
    return detached


def release_layer(layer):
    """Drop a layer's cache images (before the layer is removed or its cache cleared).

    The compiled sampler node may still reference the image; Blender unlinks
    it and the structural rebuild that follows recompiles the layer. A refresh
    of the layer still running is dropped: nothing will sample its result.
    """
    _drop_work(layer.id)
    for image in _unbind_rows(layer):
        try:
            bpy.data.images.remove(image)
        except (ReferenceError, RuntimeError):
            pass


def retire_layer(layer):
    """A layer set back to Live gives up its cache images (the mode's update callback).

    Only Clear released them before, so a layer cached once carried its
    full-resolution images in the file for good. They are released, never
    removed (``layer_stack.release_image``): unpacked generated pixels do not
    survive a memfile undo of a removed datablock, so Ctrl+Z after the switch
    would bind the rows to blank images and the shader would sample nothing
    until a refresh. Dropping the fake user keeps the datablock and its pixels
    for an undo while the sampler of the compiled graph still reads them; once
    the structural rebuild recompiles the layer live nothing binds them and
    the next save or reload purges them. Not ``_retire`` either: the sweep
    removes a user-less image on the next tick, which is the same removal.
    The rows lose their provenance too, so Cached again rebuilds instead of
    trusting an image that is gone.
    """
    from ..core.layer_stack import release_image
    _drop_work(layer.id)
    for image in _unbind_rows(layer):
        try:
            release_image(image)
        except (ReferenceError, RuntimeError):
            pass   # already freed (a file load, an undo)


def placeholder():
    """A shared transparent image sampled while a cache row has no valid image yet."""
    image = bpy.data.images.get(PLACEHOLDER)
    if image is None or not image.has_data:
        if image is not None:
            bpy.data.images.remove(image)
        image = bpy.data.images.new(PLACEHOLDER, width=4, height=4, alpha=True, float_buffer=True)
        image[OWNER] = True
        image.use_fake_user = True
        image.colorspace_settings.name = 'Non-Color'
        image.generated_color = (0, 0, 0, 0)
        image.pixels.foreach_set(np.zeros(4 * 4 * 4, np.float32))
        image.update()
    return image


def cancel_all():
    _requests.clear()
    _jobs.clear()


def clear():
    cancel_all()


def prune(live_scenes):
    for table in (_requests, _jobs):
        for key in [k for k in table if k[0] not in live_scenes]:
            table.pop(key, None)
