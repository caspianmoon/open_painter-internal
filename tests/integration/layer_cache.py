"""Layer caches (0.9.0): Cached/Frozen layers sample a persisted image instead of compiling.

Covers parity against the live CPU reference, stale detection, the debounced
refresh engine, the explicit Refresh operator, undo/reload persistence,
resolution changes, the paint-time pause, folders, and lifecycle hygiene.
"""
import json
from pathlib import Path
import sys
from time import monotonic
from unittest.mock import patch

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.constants import OWNER
from open_painter.core import image_manager as images, layer_stack as stack, material_manager
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache, layer_cache, metrics, shader_preview
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.shader_graph import Graph
from open_painter.evaluation import shader_resources
from open_painter.utils.ids import new_id


def revisions(info):
    """Structural revisions of the session: a rebuild, or a patch of its own graph (EngineGraph.patch)."""
    return info['graph_builds'] + info['graph_patches']


N = 32


class SampledSet:
    def __init__(self, original):
        self.original = original
        self.resolution = str(N)

    def __getattr__(self, name):
        return getattr(self.original, name)


def ready(scene, ts):
    deadline = monotonic() + 60
    while monotonic() < deadline:
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert not info['error'], info
        if shader_preview.current(scene, ts):
            return info
    raise AssertionError(shader_preview.diagnostics(scene, ts))


def layer_by_id(ts, identifier):
    return next(layer for layer in ts.layers if layer.id == identifier)


def cpu_channel(scene, ts, channel):
    steps = LayerEvaluator(scene).channel_steps(ts.id, channel, preview_resolution=N)
    try:
        while True:
            next(steps)
    except StopIteration as done:
        image = done.value
    assert image is not None
    return images.pixels(image)[..., :3].copy()


def cache_nodes(graph):
    return [node for unit in graph.units.values() for node in unit.tree.nodes if node.get('op_cache')]


def states(scene, ts, layer_id):
    info = shader_preview.diagnostics(scene, ts)
    return {key.split(':')[1]: value for key, value in info['cache_states'].items() if key.startswith(layer_id + ':')}


def run():
    open_painter.register()
    if bpy.app.timers.is_registered(cache.tick):
        bpy.app.timers.unregister(cache.tick)
    scene = bpy.context.scene
    ts = sets.create(scene, 'Layer cache', 512)
    ts.preview_backend = 'CPU'
    shader_preview.clear()
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_plane_add(size=2)
    obj = bpy.context.object
    sets.assign(scene, ts, obj)
    bpy.context.view_layer.update()
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 1
    scene.render.bake.margin = 0
    scene.render.bake.use_clear = True
    target = bpy.data.images.new('Layer cache readback', width=N, height=N, float_buffer=True)
    target.colorspace_settings.name = 'Non-Color'
    report = {'cases': []}

    # Masks are painted gradients: Cycles jitters bake samples inside each
    # pixel, so a hard-edged analytic mask and its texel image disagree at
    # edges by construction. Linear content interpolates identically both ways.
    def gradient(name, direction):
        image = images.create(name, 512)
        u, v = np.meshgrid((np.arange(512) + .5) / 512, (np.arange(512) + .5) / 512)
        data = np.ones((512, 512, 4), np.float32)
        value = (u * direction[0] + v * direction[1]).astype(np.float32)
        data[..., 0] = data[..., 1] = data[..., 2] = value
        images.write(image, data, pack=False)
        return image

    base = stack.add(ts, 'FILL', 'Base')
    stack.channel_for(base, 'BASE_COLOR').color = (.8, .1, .1, 1.)
    stack.channel_for(base, 'ROUGHNESS').value = .3
    blue = stack.add(ts, 'FILL', 'Blue blur')
    blue_id = blue.id
    stack.channel_for(blue, 'BASE_COLOR').color = (.1, .1, .9, 1.)
    blur = stack.channel_for(blue, 'BASE_COLOR').effects.add()
    blur.kind, blur.radius = 'GAUSSIAN_BLUR', 3.
    checker = blue.masks.add()
    checker.id, checker.source, checker.image = new_id(), 'PAINT', gradient('Blue mask', (.8, .2))
    checker_id = checker.id
    folder = stack.add(ts, 'FOLDER', 'Folder')
    folder_id = folder.id
    green = stack.add(ts, 'FILL', 'Green child')
    green_id = green.id
    green.parent_id = folder_id
    stack.channel_for(green, 'BASE_COLOR').color = (.1, .8, .2, .6)
    stripes = green.masks.add()
    stripes.id, stripes.source, stripes.image = new_id(), 'PAINT', gradient('Green mask', (.3, .7))
    yellow = stack.add(ts, 'FILL', 'Yellow child')
    yellow_id = yellow.id
    yellow.parent_id = folder_id
    yellow.blend_mode = 'MULTIPLY'
    stack.channel_for(yellow, 'BASE_COLOR').color = (.9, .9, .2, .8)
    small = SampledSet(ts)

    # Rows are created once, all channels at a time.
    layer_by_id(ts, blue_id).cache_mode = 'CACHED'
    assert [row.channel for row in layer_by_id(ts, blue_id).cache] == list(
        ('BASE_COLOR', 'ROUGHNESS', 'METALLIC', 'NORMAL', 'HEIGHT', 'AO', 'EMISSION', 'OPACITY'))
    layer_by_id(ts, blue_id).cache_mode = 'LIVE'
    layer_by_id(ts, blue_id).cache_mode = 'CACHED'
    assert len(layer_by_id(ts, blue_id).cache) == 8

    def bake_graph(graph, channels):
        tree = ts.material.node_tree
        output = next(n for n in tree.nodes if n.bl_idname == 'ShaderNodeOutputMaterial')
        emission = tree.nodes.new('ShaderNodeEmission')
        bake_node = tree.nodes.new('ShaderNodeTexImage')
        bake_node.image = target
        tree.nodes.active = bake_node
        tree.links.new(emission.outputs[0], output.inputs['Surface'])
        result = {}
        for channel in channels:
            tree.links.new(graph.outputs[channel], emission.inputs['Color'])
            bpy.ops.object.bake(type='EMIT')
            result[channel] = images.pixels(target)[..., :3].copy()
        tree.nodes.remove(bake_node)
        tree.nodes.remove(emission)
        return result

    def parity(label, cached_ids, absent_ids=None, channels=('BASE_COLOR', 'ROUGHNESS')):
        """The cached graph renders what the live compile of the same layers renders.

        ``cached_ids`` are toggled to LIVE for the reference; ``absent_ids`` (default
        the same) must have no content/mask units in the cached graph.
        """
        absent_ids = cached_ids if absent_ids is None else absent_ids
        modes = {identifier: layer_by_id(ts, identifier).cache_mode for identifier in cached_ids}
        for identifier in cached_ids:
            layer_by_id(ts, identifier).cache_mode = 'LIVE'
        live = Graph(scene, small, ts.material)
        live.build()
        while live.pending_resources:
            live.step_resources(.1)
        expected = bake_graph(live, channels)
        live.close()
        for identifier, mode in modes.items():
            layer_by_id(ts, identifier).cache_mode = mode
        graph = Graph(scene, small, ts.material)
        with metrics.Metrics() as observed:
            graph.build()
            while graph.pending_resources:
                graph.step_resources(.1)
            layer_cache.flush(scene)
            graph.update()   # adopt the published images (a session does this through notify)
        counters = observed.report()['counters']
        for identifier in absent_ids:
            assert not any(key[0] in {'SRC', 'MASK'} and key[1] == identifier for key in graph.units), sorted(graph.units)
        assert all(value == 'CURRENT' for value in graph.cache_states().values()), graph.cache_states()
        actual = bake_graph(graph, channels)
        worst = 0.
        for channel in channels:
            error = float(np.abs(actual[channel] - expected[channel]).max())
            worst = max(worst, error)
            assert np.allclose(actual[channel], expected[channel], atol=3e-4), (label, channel, error)
            # The CPU reference at the same authored size agrees too (the
            # harness scales radii by 32/512, so only the coarse shape can match).
            assert np.abs(actual[channel] - cpu_channel(scene, ts, channel)).max() < 2e-2, (label, channel)
        record = {'case': label, 'max_error': worst, 'units': len(graph.units), 'nodes': graph.node_count,
                  'cache_publications': counters.get('layer_cache_publications', 0)}
        report['cases'].append(record)
        print('LAYER_CACHE_PARITY', json.dumps(record), flush=True)
        graph.close()
        material_manager.repair(ts)
        return record

    record = parity('cached_blur_layer', [blue_id])
    assert record['cache_publications'] >= 1
    # A bypassed child of a cached folder is left out of the cache, not of the export.
    layer_by_id(ts, folder_id).cache_mode = 'CACHED'
    parity('cached_folder', [folder_id], [green_id, yellow_id])
    layer_by_id(ts, green_id).bypass = True
    graph = Graph(scene, small, ts.material)
    graph.build()
    layer_cache.flush(scene)
    graph.update()
    row = next(r for r in layer_by_id(ts, folder_id).cache if r.channel == 'BASE_COLOR')
    cached_rgba = images.pixels(row.image)
    graph.close()
    layer_by_id(ts, green_id).bypass = False
    layer_by_id(ts, green_id).visible = False
    from open_painter.evaluation.composite import Composer
    hidden = Composer(ts, 'BASE_COLOR', N, {}, {}).group(folder_id, 0, N)
    layer_by_id(ts, green_id).visible = True
    assert np.allclose(cached_rgba, hidden, atol=1e-5), 'cache content must honour bypass'
    assert not np.allclose(cached_rgba, Composer(ts, 'BASE_COLOR', N, {}, {}).group(folder_id, 0, N))
    layer_by_id(ts, folder_id).cache_mode = 'LIVE'
    # Passthrough folders cannot be cached: a warning and live units.
    layer_by_id(ts, folder_id).group_mode = 'PASSTHROUGH'
    layer_by_id(ts, folder_id).cache_mode = 'CACHED'
    graph = Graph(scene, small, ts.material)
    graph.build()
    assert any('cannot be cached' in w for w in graph._last['warnings']), graph._last['warnings']
    assert ('SRC', green_id, 'BASE_COLOR', 0) in graph.units
    graph.close()
    layer_by_id(ts, folder_id).cache_mode = 'LIVE'
    layer_by_id(ts, folder_id).group_mode = 'ISOLATED'
    material_manager.repair(ts)
    print('LAYER_CACHE_FOLDERS_OK', flush=True)

    # ---- live session: the engine, staleness and modes -------------------
    ts.preview_backend = 'LIVE'
    material_manager.repair(ts)
    ready(scene, ts)
    layer_cache.flush(scene)
    ready(scene, ts)
    info = shader_preview.diagnostics(scene, ts)
    assert all(v == 'CURRENT' for v in states(scene, ts, blue_id).values()), info['cache_states']
    graph = shader_preview.ensure(scene, ts)
    nodes = cache_nodes(graph)
    assert len(nodes) == len(states(scene, ts, blue_id)) and all(n.image and n.image.get(OWNER) for n in nodes)
    row = next(r for r in layer_by_id(ts, blue_id).cache if r.channel == 'BASE_COLOR')
    assert row.image is not None and tuple(row.image.size) == (512, 512) and row.image.get('open_painter_layer_cache') == blue_id
    bound = next(n for n in nodes if json.loads(n['op_cache']) == [blue_id, 'BASE_COLOR'])
    assert bound.image == row.image
    # No requests for an unrelated edit, nor after the publication itself.
    with metrics.Metrics() as observed:
        layer_by_id(ts, ts.layers[0].id).opacity = .9
        shader_preview.notify(row, structural=False)
    assert observed.report()['counters'].get('layer_cache_requests', 0) == 0
    assert not layer_cache.pending(scene)
    # An edit inside the layer: stale, previous image still bound, debounced refresh.
    previous_image = row.image
    with metrics.Metrics() as observed:
        next(m for m in layer_by_id(ts, blue_id).masks if m.id == checker_id).strength = .4
    assert observed.report()['counters'].get('layer_cache_requests', 0) >= 1
    assert layer_cache.pending(scene, ts, blue_id)
    assert states(scene, ts, blue_id)['BASE_COLOR'] == 'STALE'
    assert bound.image == previous_image, 'the previous image stays bound while stale'
    assert not layer_cache.tick(scene), 'the debounce holds a fresh request'
    deadline = monotonic() + 5
    while not layer_cache.tick(scene) and monotonic() < deadline:
        pass
    while layer_cache.pending(scene, ts, blue_id):
        assert layer_cache.tick(scene) or layer_cache.pending(scene, ts, blue_id)
    row = next(r for r in layer_by_id(ts, blue_id).cache if r.channel == 'BASE_COLOR')
    assert row.image is not previous_image and row.provenance and states(scene, ts, blue_id)['BASE_COLOR'] == 'CURRENT'
    bound = next(n for n in cache_nodes(shader_preview.ensure(scene, ts)) if json.loads(n['op_cache']) == [blue_id, 'BASE_COLOR'])
    assert bound.image == row.image
    print('LAYER_CACHE_REFRESH_OK', flush=True)

    # FROZEN never refreshes on its own; the operator does.
    layer_by_id(ts, blue_id).cache_mode = 'FROZEN'
    ready(scene, ts)
    layer_cache.flush(scene)
    frozen_image = next(r for r in layer_by_id(ts, blue_id).cache if r.channel == 'BASE_COLOR').image
    next(m for m in layer_by_id(ts, blue_id).masks if m.id == checker_id).strength = .7
    assert states(scene, ts, blue_id)['BASE_COLOR'] == 'STALE'
    assert not layer_cache.pending(scene, ts, blue_id)
    for _ in range(20):
        layer_cache.tick(scene)
    assert next(r for r in layer_by_id(ts, blue_id).cache if r.channel == 'BASE_COLOR').image is frozen_image
    ts.active_layer = next(i for i, l in enumerate(ts.layers) if l.id == blue_id)
    with bpy.context.temp_override(scene=scene):
        bpy.ops.open_painter.layer_cache(action='REFRESH')
    assert states(scene, ts, blue_id)['BASE_COLOR'] == 'CURRENT'
    assert next(r for r in layer_by_id(ts, blue_id).cache if r.channel == 'BASE_COLOR').image is not frozen_image
    # Channel opacity is applied after the cache sample: the slider (through its
    # opacity_percent mirror) must not turn the frozen row stale.
    stack.channel_for(layer_by_id(ts, blue_id), 'BASE_COLOR').opacity_percent = 60
    shader_preview.tick(scene, ts)   # the engine renders the edit
    assert shader_preview.current(scene, ts), shader_preview.diagnostics(scene, ts)
    assert states(scene, ts, blue_id)['BASE_COLOR'] == 'CURRENT', states(scene, ts, blue_id)
    assert not layer_cache.pending(scene, ts, blue_id)
    stack.channel_for(layer_by_id(ts, blue_id), 'BASE_COLOR').opacity = 1.
    assert states(scene, ts, blue_id)['BASE_COLOR'] == 'CURRENT', states(scene, ts, blue_id)
    # Toggling the mode is structural and scoped to the layer's units.
    builds = revisions(shader_preview.diagnostics(scene, ts))
    layer_by_id(ts, blue_id).cache_mode = 'CACHED'
    info = shader_preview.diagnostics(scene, ts)
    assert info['rebuild_pending'] and isinstance(info['rebuild_scope'], list)
    assert all(key[1] == blue_id for key in info['rebuild_scope']), info['rebuild_scope']
    ready(scene, ts)
    assert revisions(shader_preview.diagnostics(scene, ts)) == builds + 1
    assert states(scene, ts, blue_id)['BASE_COLOR'] == 'CURRENT' and not layer_cache.pending(scene)
    # Folder cache: a child slider requests, the folder's own opacity does not.
    layer_by_id(ts, folder_id).cache_mode = 'CACHED'
    ready(scene, ts)
    layer_cache.flush(scene)
    ready(scene, ts)
    assert all(v == 'CURRENT' for v in states(scene, ts, folder_id).values()), states(scene, ts, folder_id)
    with metrics.Metrics() as observed:
        layer_by_id(ts, folder_id).opacity = .5
    assert observed.report()['counters'].get('layer_cache_requests', 0) == 0
    with metrics.Metrics() as observed:
        layer_by_id(ts, yellow_id).opacity = .5
    assert observed.report()['counters'].get('layer_cache_requests', 0) >= 1
    layer_cache.flush(scene)
    assert all(v == 'CURRENT' for v in states(scene, ts, folder_id).values())
    # An edit inside a cached folder stays pending for the export (0.9.7): the
    # child's units are sampled from the cache and hold no bindings to narrow by.
    output = images.output(ts, 'BASE_COLOR')
    output.dirty = False
    stack.channel_for(layer_by_id(ts, yellow_id), 'BASE_COLOR').color = (.2, .9, .9, .8)
    shader_preview.tick(scene, ts)
    assert shader_preview.current(scene, ts)
    assert output.dirty, 'an edit to a cached folder child was dropped from the export'
    layer_cache.flush(scene)
    print('LAYER_CACHE_MODES_OK', flush=True)

    # Paused while painting: no job starts, even past the debounce.
    next(m for m in layer_by_id(ts, blue_id).masks if m.id == checker_id).strength = .2
    assert layer_cache.pending(scene, ts, blue_id)
    with patch.object(cache, 'painting_active', lambda s: True):
        layer_cache.paused = cache.painting_active
        deadline = monotonic() + 1
        while monotonic() < deadline:
            assert not layer_cache.tick(scene)
    layer_cache.paused = None
    layer_cache.flush(scene)
    assert states(scene, ts, blue_id)['BASE_COLOR'] == 'CURRENT'

    # Saving/rendering completes an automatic refresh and binds its result.
    # A pending placeholder must not be packed/rendered as the final material.
    from open_painter.evaluation import field_jobs
    pointer = scene.as_pointer()
    next(m for m in layer_by_id(ts, blue_id).masks if m.id == checker_id).strength = .3
    assert layer_cache.pending(scene, ts, blue_id)
    field_jobs._rebakes[(pointer, ts.id)] = monotonic()
    with metrics.Metrics() as observed:
        cache.on_save(None)
    assert not layer_cache.pending(scene, ts, blue_id), 'save left a queued cache refresh unfinished'
    assert (pointer, ts.id) in field_jobs._rebakes, 'save dropped the scheduled edge rebake'
    assert states(scene, ts, blue_id)['BASE_COLOR'] == 'CURRENT'
    next(m for m in layer_by_id(ts, blue_id).masks if m.id == checker_id).strength = .4
    assert layer_cache.pending(scene, ts, blue_id)
    cache.on_render(None)
    assert not layer_cache.pending(scene, ts, blue_id) and (pointer, ts.id) not in field_jobs._rebakes, \
        'a render completes caches and cancels only the timer-driven bake'
    layer_cache.flush(scene)
    ready(scene, ts)
    assert states(scene, ts, blue_id)['BASE_COLOR'] == 'CURRENT'
    print('LAYER_CACHE_SAVE_OK', flush=True)

    # Adding or removing a set keeps the queued work of the others (0.9.7):
    # only the CPU evaluator jobs read the relocated structs by address. The
    # refresh of a moved graph is re-requested by the rehydration, a running
    # field analysis returns to its queue, and the scheduled edge rebake stays.
    identifier = ts.id
    next(m for m in layer_by_id(ts, blue_id).masks if m.id == checker_id).strength = .32
    assert layer_cache.pending(scene, ts, blue_id)
    field_jobs._rebakes[(pointer, ts.id)] = monotonic()
    field_jobs._requests[(pointer, ts.id, 'field-request')] = {'signature': 'request', 'since': monotonic()}
    field_jobs._jobs[(pointer, ts.id, 'field-job')] = {'signature': 'job', 'steps': iter(()), 'progress': 0., 'name': 'Job'}
    cache._jobs[(pointer, ts.id)] = ('BASE_COLOR', (strip for strip in ()))
    for step in ('add', 'remove'):
        if step == 'add':
            second = sets.create(scene, 'Second set', 512)
            second_id = second.id
        else:
            sets.remove(scene, next(i for i, t in enumerate(scene.open_painter.texture_sets) if t.id == second_id))
        ts = next(t for t in scene.open_painter.texture_sets if t.id == identifier)
        assert (pointer, ts.id) not in cache._jobs, f'the {step} kept a CPU evaluator job over the moved set'
        assert (pointer, ts.id) in field_jobs._rebakes, f'the {step} dropped the scheduled edge rebake'
        assert (pointer, ts.id, 'field-request') in field_jobs._requests, f'the {step} dropped a queued field analysis'
        assert (pointer, ts.id, 'field-job') in field_jobs._requests and not field_jobs._jobs, \
            f'the {step} did not return the running field analysis to its queue'
        ready(scene, ts)   # the moved graph rehydrates and re-requests its stale rows
        assert layer_cache.pending(scene, ts, blue_id), f'the {step} dropped the queued cache refresh'
        assert states(scene, ts, blue_id)['BASE_COLOR'] == 'STALE'
    for key in [k for k in field_jobs._requests if k[0] == pointer]:
        field_jobs._requests.pop(key)
    field_jobs._rebakes.pop((pointer, ts.id))
    with metrics.Metrics() as observed:
        layer_cache.flush(scene)
    assert observed.report()['counters'].get('layer_cache_publications', 0) >= 1
    ready(scene, ts)
    assert states(scene, ts, blue_id)['BASE_COLOR'] == 'CURRENT'
    print('LAYER_CACHE_SET_CHANGE_OK', flush=True)

    def start_job(layer_id, mask_id, strength):
        """Edit a mask so a refresh is requested, then tick (one strip a time) until it starts."""
        next(m for m in layer_by_id(ts, layer_id).masks if m.id == mask_id).strength = strength
        assert layer_cache.pending(scene, ts, layer_id)
        deadline = monotonic() + 5
        while not layer_cache.tick(scene, budget=0) and monotonic() < deadline:
            pass
        return next(k for k in layer_cache._jobs if k[2] == layer_id)

    # A publication while a structural rebuild is pending (0.9.7): notify cannot
    # rebind the sampler, so the superseded image keeps a user; it loses its
    # fake user at once and is removed once the rebuild lets go of it.
    row = next(r for r in layer_by_id(ts, blue_id).cache if r.channel == 'BASE_COLOR')
    previous_image = row.image
    previous_name = previous_image.name
    start_job(blue_id, checker_id, .35)
    stack.add(ts, 'FILL', 'Mid-job layer')
    assert shader_preview.diagnostics(scene, ts)['rebuild_pending']
    with metrics.Metrics() as observed:
        layer_cache.flush(scene, ts, blue_id)
    assert observed.report()['counters'].get('layer_cache_publications', 0) >= 1
    row = next(r for r in layer_by_id(ts, blue_id).cache if r.channel == 'BASE_COLOR')
    assert row.image is not previous_image and row.provenance
    assert bpy.data.images.get(previous_name) is previous_image
    assert not previous_image.use_fake_user and shader_resources.bound(previous_image), \
        'a superseded cache image the sampler still reads must not be persisted'
    ready(scene, ts)   # the rebuild binds the published image
    layer_cache.tick(scene)
    assert bpy.data.images.get(previous_name) is None, 'the superseded cache image leaked'
    assert row.image.use_fake_user and states(scene, ts, blue_id)['BASE_COLOR'] == 'CURRENT'
    # A job whose inputs moved on while a rebuild was pending never publishes:
    # the pending rebuild swallows the edit that would have superseded it.
    previous_image = row.image
    start_job(blue_id, checker_id, .45)
    stack.add(ts, 'FILL', 'Second mid-job layer')
    assert shader_preview.diagnostics(scene, ts)['rebuild_pending']
    with metrics.Metrics() as observed:
        next(m for m in layer_by_id(ts, blue_id).masks if m.id == checker_id).strength = .55
    assert observed.report()['counters'].get('layer_cache_requests', 0) == 0
    with metrics.Metrics() as observed:
        layer_cache.flush(scene, ts, blue_id)
    assert observed.report()['counters'].get('layer_cache_publications', 0) == 0, 'a torn cache was published'
    row = next(r for r in layer_by_id(ts, blue_id).cache if r.channel == 'BASE_COLOR')
    assert row.image is previous_image
    ready(scene, ts)
    assert states(scene, ts, blue_id)['BASE_COLOR'] == 'STALE' and layer_cache.pending(scene, ts, blue_id)
    layer_cache.flush(scene)
    ready(scene, ts)
    assert states(scene, ts, blue_id)['BASE_COLOR'] == 'CURRENT'
    for name in ('Mid-job layer', 'Second mid-job layer'):
        stack.remove_tree(ts, next(l for l in ts.layers if l.name == name).id)
    ready(scene, ts)
    print('LAYER_CACHE_RETIRE_OK', flush=True)

    # Back to Live (0.9.7): the switch releases the cache images (fake user
    # dropped, the datablock kept for an undo and purged by the next save),
    # forgets the rows' provenance and drops the running refresh; the rows
    # themselves stay for the compiled bindings. Before, only Clear released
    # them: a layer cached once carried its full-resolution images in the file
    # for good.
    key = start_job(blue_id, checker_id, .65)
    job = layer_cache._jobs[key]
    retired = {r.channel: (r.image, r.image.name) for r in layer_by_id(ts, blue_id).cache if r.image is not None}
    assert retired
    layer_by_id(ts, blue_id).cache_mode = 'LIVE'
    assert key not in layer_cache._jobs and not layer_cache.pending(scene, ts, blue_id), \
        'the switch to Live kept the refresh of a layer nobody samples'
    assert len(layer_by_id(ts, blue_id).cache) == 8, 'the switch to Live reallocated the cache rows'
    assert all(r.image is None and not r.provenance for r in layer_by_id(ts, blue_id).cache), \
        'the switch to Live kept the cache images bound to the rows'
    for image, name in retired.values():
        assert bpy.data.images.get(name) is image and not image.use_fake_user and shader_resources.bound(image), \
            'a retired cache image the sampler still reads must not be persisted'
        assert image not in layer_cache._retired, 'the switch to Live queued a cache image for removal'
    # A job the switch did not see (a layer removed around the engine) is dropped
    # on the next tick instead of holding the scheduler until its strips are done.
    layer_cache._jobs[key] = job
    assert not layer_cache.tick(scene, budget=0) and key not in layer_cache._jobs, 'an orphan job kept running'
    ready(scene, ts)   # the rebuild compiles the layer live and lets go of the images
    graph = shader_preview.ensure(scene, ts)
    assert any(key[:3] == ('SRC', blue_id, 'BASE_COLOR') for key in graph.units), sorted(graph.units)
    assert not any(json.loads(n['op_cache'])[0] == blue_id for n in cache_nodes(graph))
    layer_cache.tick(scene)
    # Never removed, only orphaned: the datablock (and its unpacked pixels) must
    # outlive the switch for an undo, so the sweep is not allowed to take it.
    for image, name in retired.values():
        assert bpy.data.images.get(name) is image and not shader_resources.bound(image) and not image.use_fake_user, \
            f'the released cache image {name} was removed or is still bound'
    # Cached again rebuilds from scratch: nothing trusts the provenance of an image that is gone.
    layer_by_id(ts, blue_id).cache_mode = 'CACHED'
    ready(scene, ts)
    assert states(scene, ts, blue_id)['BASE_COLOR'] == 'MISSING' and layer_cache.pending(scene, ts, blue_id), \
        states(scene, ts, blue_id)
    layer_cache.flush(scene)
    ready(scene, ts)
    assert all(v == 'CURRENT' for v in states(scene, ts, blue_id).values()), states(scene, ts, blue_id)
    row = next(r for r in layer_by_id(ts, blue_id).cache if r.channel == 'BASE_COLOR')
    assert row.image is not None and row.image.use_fake_user and tuple(row.image.size) == (512, 512) and row.provenance
    assert row.image.get('open_painter_layer_cache') == blue_id
    print('LAYER_CACHE_LIVE_RELEASE_OK', flush=True)
    # An explicit refresh runs one channel at a time: the next channel's buffer
    # is allocated only after the previous one has been published.
    alive = []
    original_start = layer_cache._start

    def counting_start(*args):
        alive.append(len(layer_cache._jobs))
        return original_start(*args)
    with patch.object(layer_cache, '_start', counting_start):
        layer_cache.refresh(scene, ts, folder_id)
    assert len(alive) >= 2 and max(alive) == 0, alive
    assert all(v == 'CURRENT' for v in states(scene, ts, folder_id).values())
    print('LAYER_CACHE_ORPHAN_OK', flush=True)

    # A running cache job must not starve the shader loop: a structural edit
    # rebuilds on the next tick while the job keeps its strips.
    from time import sleep

    def slow_strips():
        for _ in range(100000):
            sleep(.002)
            yield 0, np.zeros((1, 512, 4), np.float32)
    fake = (pointer, ts.id, blue_id, 'EMISSION')   # a bound channel's rebuild would discard a current job
    layer_cache._jobs[fake] = {'signature': 'fake', 'steps': slow_strips(), 'progress': 0., 'name': 'Slow',
                               'data': np.ones((512, 512, 4), np.float32), 'covered': np.zeros(512, np.bool_),
                               'ms': 0., 'size': 512, 'channel': 'ROUGHNESS'}
    builds = revisions(shader_preview.diagnostics(scene, ts))
    late = stack.add(ts, 'FILL', 'Late layer')
    late_id = late.id
    assert shader_preview.diagnostics(scene, ts)['rebuild_pending']
    interval = cache.tick()
    info = shader_preview.diagnostics(scene, ts)
    assert not info['rebuild_pending'] and revisions(info) == builds + 1, info
    assert abs(interval - .001) < 1e-9 and fake in layer_cache._jobs, 'the job still advances after the shader loop'
    while shader_preview.diagnostics(scene, ts)['pending_resources']:
        cache.tick()
    cache.tick()
    assert fake in layer_cache._jobs and scene.open_painter.status.startswith('Caching Slow'), scene.open_painter.status
    layer_cache._jobs.pop(fake)
    cache.tick()
    assert not scene.open_painter.status.startswith('Caching'), scene.open_painter.status
    stack.remove_tree(ts, late_id)
    ready(scene, ts)
    print('LAYER_CACHE_PRIORITY_OK', flush=True)

    # The graph an undo compiles binds the current cache image without a request.
    graph = shader_preview.ensure(scene, ts)
    from open_painter.evaluation.engine.compiler import EngineGraph
    probe = EngineGraph(scene, ts)
    with metrics.Metrics() as observed:
        probe.build()
    assert observed.report()['counters'].get('layer_cache_requests', 0) == 0
    assert all(v == 'CURRENT' for v in probe.cache_states().values()), probe.cache_states()
    assert any(n.image == row.image for n in cache_nodes(probe))
    probe.close()
    # Undoing the switch to Live brings the cache images back with their pixels:
    # unpacked generated pixels do not survive a memfile undo of a removed
    # datablock, so a removal here (immediate or by the sweep) would have bound
    # the rows to blank images and left the shader sampling nothing.
    identifier = ts.id
    cached = {r.channel: (r.image.name, images.pixels(r.image).copy()) for r in layer_by_id(ts, blue_id).cache
              if r.image is not None}
    assert cached and any(pixels.any() for _, pixels in cached.values())
    bpy.ops.ed.undo_push(message='cached')
    layer_by_id(ts, blue_id).cache_mode = 'LIVE'
    assert not layer_cache._retired
    for name, _ in cached.values():
        assert not bpy.data.images[name].use_fake_user and shader_resources.bound(bpy.data.images[name])
    layer_cache.tick(scene)
    for name, _ in cached.values():
        assert bpy.data.images.get(name) is not None, f'the released cache image {name} was removed'
    bpy.ops.ed.undo_push(message='live')
    assert bpy.ops.ed.undo() == {'FINISHED'}
    scene = bpy.context.scene
    ts = next(t for t in scene.open_painter.texture_sets if t.id == identifier)
    assert layer_by_id(ts, blue_id).cache_mode == 'CACHED'
    for channel, (name, pixels) in cached.items():
        row = next(r for r in layer_by_id(ts, blue_id).cache if r.channel == channel)
        image = bpy.data.images.get(name)
        assert image is not None and row.image == image and row.provenance and image.use_fake_user, \
            f'undo did not rebind the {channel} cache image'
        assert image.has_data and np.array_equal(images.pixels(image), pixels), \
            f'the {channel} cache image came back without its pixels'
    with metrics.Metrics() as observed:
        ready(scene, ts)
    assert observed.report()['counters'].get('layer_cache_requests', 0) == 0, observed.report()['counters']
    assert all(v == 'CURRENT' for v in states(scene, ts, blue_id).values()), states(scene, ts, blue_id)
    row = next(r for r in layer_by_id(ts, blue_id).cache if r.channel == 'BASE_COLOR')
    print('LAYER_CACHE_UNDO_RELEASE_OK', flush=True)
    # Save and reopen: the packed image survives and the cache is still current.
    path = ROOT / '.blender-test' / 'layer-cache.blend'
    identifier = ts.id
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    assert row.image.packed_file is not None, 'cache images are packed on save'
    bpy.ops.wm.open_mainfile(filepath=str(path))
    scene = bpy.context.scene
    ts = next(t for t in scene.open_painter.texture_sets if t.id == identifier)
    material_manager.repair(ts)
    with metrics.Metrics() as observed:
        ready(scene, ts)
    counters = observed.report()['counters']
    assert counters.get('layer_cache_requests', 0) == 0, counters
    assert all(v == 'CURRENT' for v in states(scene, ts, blue_id).values()), states(scene, ts, blue_id)
    row = next(r for r in layer_by_id(ts, blue_id).cache if r.channel == 'BASE_COLOR')
    assert row.image is not None and row.image.has_data and tuple(row.image.size) == (512, 512)
    print('LAYER_CACHE_PERSISTENCE_OK', flush=True)

    # Persisted provenance carries no session-local tokens (0.9.7): a modifier's
    # target object is named, and a mesh mask's painted weights are digested
    # instead of the per-process mesh epoch, so a reopened file stays CURRENT.
    from open_painter.evaluation import dependencies, mesh_masks
    obj = ts.assignments[0].object
    rig = bpy.data.objects.new('Rig Armature', bpy.data.armatures.new('Rig'))
    scene.collection.objects.link(rig)
    modifier = obj.modifiers.new('Rig', 'ARMATURE')
    modifier.object = rig
    weights = obj.vertex_groups.new(name='Weights')
    for vertex in obj.data.vertices:
        weights.add([vertex.index], (vertex.co.x + 1) / 2, 'REPLACE')
    rigged = stack.add(ts, 'FILL', 'Rigged'); rigged_id = rigged.id
    prop = stack.channel_for(rigged, 'BASE_COLOR')
    prop.color, prop.procedural, prop.projection = (.2, .6, .3, 1.), 'FBM', 'TRIPLANAR'
    mesh_mask = rigged.masks.add()
    mesh_mask.id, mesh_mask.source = new_id(), 'MESH'
    mesh_mask.mesh.kind, mesh_mask.mesh.attribute = 'VERTEX_GROUP', 'Weights'
    layer_by_id(ts, rigged_id).cache_mode = 'CACHED'
    ready(scene, ts)
    layer_cache.flush(scene)
    ready(scene, ts)
    assert states(scene, ts, rigged_id)['BASE_COLOR'] == 'CURRENT', states(scene, ts, rigged_id)
    row = next(r for r in layer_by_id(ts, rigged_id).cache if r.channel == 'BASE_COLOR')
    assert 'Rig Armature' in row.provenance and str(rig.as_pointer()) not in row.provenance, row.provenance
    dependencies._mesh_epochs.clear()   # a fresh process starts counting again
    mesh_masks._digests.clear()
    graph = shader_preview.ensure(scene, ts)
    with metrics.Metrics() as observed:
        graph.update()
    assert observed.report()['counters'].get('layer_cache_requests', 0) == 0, 'the provenance drifted across sessions'
    assert states(scene, ts, rigged_id)['BASE_COLOR'] == 'CURRENT', states(scene, ts, rigged_id)
    # Painting weights still re-identifies the mask (the digest follows the values).
    weights.add([0], .05, 'REPLACE')
    dependencies._mesh_epochs[obj.data.as_pointer()] = dependencies.mesh_epoch(obj.data) + 1
    graph.update()
    assert states(scene, ts, rigged_id)['BASE_COLOR'] == 'STALE', states(scene, ts, rigged_id)
    layer_cache.flush(scene)
    ready(scene, ts)
    # An edit to a cached layer reports its channel: the export re-rasters it.
    output = images.output(ts, 'BASE_COLOR')
    output.dirty = False
    stack.channel_for(layer_by_id(ts, rigged_id), 'BASE_COLOR').color = (.3, .6, .3, 1.)
    info = shader_preview.diagnostics(scene, ts)
    assert 'BASE_COLOR' in info['affected_channels'] and output.dirty, info['affected_channels']
    layer_cache.flush(scene)
    ready(scene, ts)
    stack.remove_tree(ts, rigged_id)
    obj.modifiers.remove(modifier)
    ready(scene, ts)
    print('LAYER_CACHE_PROVENANCE_OK', flush=True)

    # Resolution change: stale until refreshed at the new size.
    ts.resolution = '1024'
    ready(scene, ts)
    assert states(scene, ts, blue_id)['BASE_COLOR'] == 'MISSING'
    assert layer_cache.pending(scene, ts, blue_id)
    layer_cache.flush(scene)
    ready(scene, ts)
    row = next(r for r in layer_by_id(ts, blue_id).cache if r.channel == 'BASE_COLOR')
    assert tuple(row.image.size) == (1024, 1024) and states(scene, ts, blue_id)['BASE_COLOR'] == 'CURRENT'
    ts.resolution = '512'
    ready(scene, ts)
    layer_cache.flush(scene)

    # Lifecycle: duplicates own no image, portable materials carry no cache, removal drops images.
    copy = stack.duplicate(ts, next(i for i, l in enumerate(ts.layers) if l.id == blue_id))
    copy_id = copy.id
    assert all(r.image is None and not r.provenance for r in layer_by_id(ts, copy_id).cache)
    from open_painter.smart_materials.serializer import save
    import zipfile
    archive = ROOT / '.blender-test' / 'layer-cache-material.zip'
    if archive.exists():
        archive.unlink()
    save(ts, archive)
    with zipfile.ZipFile(archive) as bundle:
        text = bundle.read('material.json').decode()
    assert '"cache_mode": "CACHED"' not in text and '"cache_mode": "FROZEN"' not in text
    assert '"bypass": true' not in text and '"refresh_serial"' not in text and '"cache": [' not in text, \
        'portable materials carry no cache'
    stack.remove_tree(ts, copy_id)
    ready(scene, ts)
    cached_image = layer_by_id(ts, blue_id).cache[0].image
    name = cached_image.name
    start_job(blue_id, checker_id, .75)
    stack.remove_tree(ts, blue_id)
    assert bpy.data.images.get(name) is None, 'removing a layer drops its cache images'
    assert not layer_cache.pending(scene, ts, blue_id), 'removing a layer drops its running refresh'
    ready(scene, ts)
    print('LAYER_CACHE_PASS', flush=True)
    (ROOT / '.blender-test' / 'layer-cache.json').write_text(json.dumps(report, indent=2))
    open_painter.unregister()


run()
