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
from open_painter.utils.ids import new_id

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
    # Toggling the mode is structural and scoped to the layer's units.
    builds = shader_preview.diagnostics(scene, ts)['graph_builds']
    layer_by_id(ts, blue_id).cache_mode = 'CACHED'
    info = shader_preview.diagnostics(scene, ts)
    assert info['rebuild_pending'] and isinstance(info['rebuild_scope'], list)
    assert all(key[1] == blue_id for key in info['rebuild_scope']), info['rebuild_scope']
    ready(scene, ts)
    assert shader_preview.diagnostics(scene, ts)['graph_builds'] == builds + 1
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

    # Undo rehydration keeps the image bound without a request.
    graph = shader_preview.ensure(scene, ts)
    probe = Graph(scene, ts, ts.material)
    with metrics.Metrics() as observed:
        assert probe.rehydrate(), probe._last
    assert observed.report()['counters'].get('layer_cache_requests', 0) == 0
    assert all(v == 'CURRENT' for v in probe.cache_states().values()), probe.cache_states()
    assert any(n.image == row.image for n in cache_nodes(probe))
    probe.detach()
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
    cached_image = layer_by_id(ts, blue_id).cache[0].image
    name = cached_image.name
    stack.remove_tree(ts, blue_id)
    assert bpy.data.images.get(name) is None, 'removing a layer drops its cache images'
    ready(scene, ts)
    print('LAYER_CACHE_PASS', flush=True)
    (ROOT / '.blender-test' / 'layer-cache.json').write_text(json.dumps(report, indent=2))
    open_painter.unregister()


run()
