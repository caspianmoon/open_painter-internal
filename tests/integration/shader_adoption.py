"""Unit adoption across rebuilds (0.9.7): adopted closures read the live graph,
export shapes are part of the adoption contract, socket-less units survive the
orphan sweep, a failed rebuild leaves its predecessor intact, adopted images stay
under the eviction budget, a failed raster is inherited rather than re-run on every
rebuild, cache owners follow added inputs, discarded units leave no boundary work
behind, and float paint canvases read back straight."""
import sys
from pathlib import Path
from time import monotonic
from unittest.mock import patch

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import image_manager as images, layer_stack as stack, material_manager
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache, layer_cache, shader_preview
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.shader_graph import Graph, _band_rows
from open_painter.evaluation.shader_resources import RESOURCE, alive
from open_painter.painting import channel_paint
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


def binding_of(graph, prefix):
    return next(b for b in graph.resource_bindings if b.identifier.startswith(prefix))


def gradient(name, n=512):
    image = images.create(name, n)
    u = np.tile(((np.arange(n) + .5) / n).astype(np.float32), (n, 1))
    data = np.ones((n, n, 4), np.float32)
    data[..., 0] = data[..., 1] = data[..., 2] = u
    images.write(image, data, pack=False)
    return image


def run():
    open_painter.register()
    if bpy.app.timers.is_registered(cache.tick):
        bpy.app.timers.unregister(cache.tick)
    scene = bpy.context.scene
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_plane_add(size=2)
    plane = bpy.context.object
    ts = sets.create(scene, 'Adoption', 512)
    sets.assign(scene, ts, plane)
    for kind, value in (('CURVATURE', 0.), ('AO', 1.)):
        image = images.create('Flat ' + kind, 64)
        data = np.ones((64, 64, 4), np.float32)
        data[..., :3] = value
        images.write(image, data, pack=False)
        item = ts.baked_maps.add()
        item.kind, item.image = kind, image
    assert _band_rows(9, 512) == 16 and _band_rows(0, 512) == 16, 'small halos keep 16-row strips'
    assert _band_rows(96, 4096) == 192 and _band_rows(192, 4096) == 384, 'wide halos filter bands, not 16-row strips'

    base = stack.add(ts, 'FILL', 'Base'); base_id = base.id
    stack.channel_for(base, 'BASE_COLOR').color = (.2, .2, .2, 1.)
    # Relief: HEIGHT [Levels e1, Anchor X]; Wear: an Edge Wear mask reading X as micro height.
    relief = stack.add(ts, 'FILL', 'Relief'); relief_id = relief.id
    height = stack.channel_for(relief, 'HEIGHT')
    height.enabled, height.image = True, gradient('Relief height')
    e1 = height.effects.add(); e1.kind, e1.black, e1.white = 'LEVELS', .1, .9
    anchor = height.effects.add(); anchor.kind, anchor.id, anchor.name = 'ANCHOR', new_id(), 'X'
    anchor_id = anchor.id
    wear = stack.add(ts, 'FILL', 'Wear'); wear_id = wear.id
    stack.channel_for(wear, 'BASE_COLOR').color = (.8, .3, .1, 1.)
    edge = wear.masks.add()
    edge.id, edge.source, edge.generator, edge.detail_height_anchor = new_id(), 'GENERATOR', 'EDGE_WEAR', anchor_id
    third = stack.add(ts, 'FILL', 'Third'); third_id = third.id
    stack.channel_for(third, 'BASE_COLOR').color = (.1, .6, .2, 1.)
    # Producer/Reader: a channel anchor consumed as fill content.
    producer = stack.add(ts, 'FILL', 'Producer'); producer_id = producer.id
    stack.channel_for(producer, 'BASE_COLOR').color = (.9, .8, .1, 1.)
    published = stack.channel_for(producer, 'BASE_COLOR').effects.add()
    published.kind, published.id, published.name = 'ANCHOR', new_id(), 'Y'
    reader = stack.add(ts, 'FILL', 'Reader'); reader_id = reader.id
    stack.channel_for(reader, 'BASE_COLOR').anchor = published.id
    reader.opacity = .5
    # Grouped: a disabled mask group whose member is enabled compiles a socket-less MASK unit.
    grouped = stack.add(ts, 'FILL', 'Grouped'); grouped_id = grouped.id
    stack.channel_for(grouped, 'BASE_COLOR').color = (.3, .3, .9, 1.)
    group = grouped.masks.add(); group.id, group.source, group.enabled = new_id(), 'GROUP', False
    member = grouped.masks.add()
    member.id, member.source, member.procedural, member.parent_id = new_id(), 'PROCEDURAL', 'CHECKER', group.id
    # Noisy: a legacy value-noise mask, rastered on the CPU (a boundary that can be left pending;
    # catalog procedurals run in the engine's programs).
    noisy = stack.add(ts, 'FILL', 'Noisy'); noisy_id = noisy.id
    stack.channel_for(noisy, 'BASE_COLOR').color = (.7, .7, .7, 1.)
    voronoi = noisy.masks.add()
    voronoi.id, voronoi.source, voronoi.noise_scale = new_id(), 'NOISE', 4.
    voronoi_id = voronoi.id

    def noise_binding(graph):
        """The CPU input of the Noisy layer's value-noise mask."""
        mask = next(m for m in layer_by_id(ts, noisy_id).masks if m.id == voronoi_id)
        return next(b for b in graph.resource_bindings if mask.as_pointer() in b.owners)
    # Late: a layer that will receive an invalid mask while the session is live.
    late = stack.add(ts, 'FILL', 'Late'); late_id = late.id
    stack.channel_for(late, 'BASE_COLOR').color = (.5, .1, .5, 1.)
    material_manager.repair(ts)
    info = ready(scene, ts)
    graph = shader_preview.ensure(scene, ts)
    mask_key = ('MASK', wear_id, '', False)
    mask_tree = graph.units[mask_key].tree.as_pointer()

    # ---- Adopted closures read the live anchor index (twice adopted, origin graph closed).
    extra = layer_by_id(ts, third_id).masks.add()
    extra.id, extra.source, extra.procedural, extra.noise_scale = new_id(), 'PROCEDURAL', 'STRIPES', 2.
    stack.invalidate(ts)
    info = ready(scene, ts)
    graph = shader_preview.ensure(scene, ts)
    assert graph.units[mask_key].tree.as_pointer() == mask_tree and info['reused_units'], info
    height = stack.channel_for(layer_by_id(ts, relief_id), 'HEIGHT')
    e0 = height.effects.add(); e0.kind = 'GAMMA'
    height.effects.move(len(height.effects) - 1, 0)   # the anchor's position moves from 1 to 2
    stack.invalidate(ts)
    info = ready(scene, ts)
    graph = shader_preview.ensure(scene, ts)
    assert graph.units[mask_key].tree.as_pointer() == mask_tree, 'the mask unit was recompiled, not adopted'
    height = stack.channel_for(layer_by_id(ts, relief_id), 'HEIGHT')
    e1 = height.effects[1]
    assert e1.kind == 'LEVELS'
    micro = binding_of(graph, 'MicroDetails:')
    assert e1.as_pointer() in micro.owners, 'the adopted closure resolved the anchor through a frozen index'
    e1.white = .7
    info = shader_preview.diagnostics(scene, ts)
    assert info['raster_boundaries_queued'] >= 1 or info['pending_resources'], info
    ready(scene, ts)
    assert micro.signature == micro.identity()
    print('ADOPTION_LIVE_ANCHORS_OK', flush=True)

    # ---- Export shape is part of the contract: a producer that turns constant (or back).
    builds = revisions(ready(scene, ts))
    stack.channel_for(layer_by_id(ts, producer_id), 'BASE_COLOR').enabled = False
    info = ready(scene, ts)
    assert revisions(info) == builds + 1, info
    graph = shader_preview.ensure(scene, ts)
    reader_unit = graph.units[('CHANNEL', reader_id, 'BASE_COLOR')]
    assert not any(key[0] == 'SRC' for key, _, _ in reader_unit.imports), reader_unit.imports
    assert any(key[0] == 'SRC' for key, _, _ in reader_unit.const_imports), 'the baked literal is not recorded'
    stack.channel_for(layer_by_id(ts, producer_id), 'BASE_COLOR').enabled = True
    info = ready(scene, ts)
    graph = shader_preview.ensure(scene, ts)
    reader_unit = graph.units[('CHANNEL', reader_id, 'BASE_COLOR')]
    assert any(key == ('SRC', reader_id, 'BASE_COLOR', 0) and export == 'Color'
               for key, export, _ in reader_unit.imports), 'the reader kept the baked literal'
    assert ('SRC', reader_id, 'BASE_COLOR', 0) in graph.units
    print('ADOPTION_EXPORT_SHAPE_OK', flush=True)

    # ---- A socket-less unit is adopted by a rebuild like any other.
    unit = graph.units[('MASK', grouped_id, '', False)]
    assert unit.flags.get('empty') and not unit.outputs
    tree = unit.tree.as_pointer()
    stack.channel_for(layer_by_id(ts, grouped_id), 'BASE_COLOR').blend_mode = 'MULTIPLY'
    ready(scene, ts)
    graph = shader_preview.ensure(scene, ts)
    assert graph.units[('MASK', grouped_id, '', False)].tree.as_pointer() == tree
    print('ADOPTION_SOCKETLESS_OK', flush=True)

    # ---- A failed rebuild hands adopted units (and their pending images) back.
    next(m for m in layer_by_id(ts, noisy_id).masks if m.id == voronoi_id).noise_scale = 5.
    info = shader_preview.diagnostics(scene, ts)
    assert info['pending_resources'], info
    bad = layer_by_id(ts, late_id).masks.add()
    bad.id, bad.source, bad.generator = new_id(), 'GENERATOR', 'DRIPS'   # needs a baked Position map
    stack.invalidate(ts)
    previous = shader_preview.ensure(scene, ts)
    shader_preview.tick(scene, ts)
    info = shader_preview.diagnostics(scene, ts)
    assert 'Position' in info['error'], info
    retained = shader_preview.ensure(scene, ts)
    assert retained is previous, 'the failed candidate replaced its predecessor'
    noisy_key = ('MASK', noisy_id, '', False)
    assert noisy_key in retained.units
    pending = noise_binding(retained)
    image = pending.pending_image or pending.node.image
    image.users   # a removed image raises ReferenceError here
    while retained.pending_resources:
        retained.step_resources(.1)
    assert pending.pending_image is None and pending.node.image.get('op_shader_resource_ready')
    noisy_tree = retained.units[noisy_key].tree.as_pointer()
    layer_by_id(ts, late_id).masks.remove(len(layer_by_id(ts, late_id).masks) - 1)
    stack.invalidate(ts)
    shader_preview.retry(scene, ts)
    info = ready(scene, ts)
    graph = shader_preview.ensure(scene, ts)
    assert graph.units[noisy_key].tree.as_pointer() == noisy_tree, 'the intact predecessor should have been adopted'
    print('ADOPTION_ROLLBACK_OK', flush=True)

    # ---- Adopted finished images are registered with the new graph's Resources.
    voronoi_binding = noise_binding(graph)
    key = voronoi_binding.node.image.get(RESOURCE)
    assert key in graph.resources.entries, 'an adopted image no table knows is never evicted'

    # ---- A failed raster is inherited by every rebuilt graph, not run again (0.9.8):
    # only an edit that re-identifies the input, or Retry, runs its factory.
    calls = []
    real_factory = voronoi_binding.factory

    def failing(signature):
        calls.append(signature)
        raise ValueError('Injected raster failure')
        yield

    voronoi_binding.factory = failing
    settled = voronoi_binding.node.image
    builds = revisions(shader_preview.diagnostics(scene, ts))
    next(m for m in layer_by_id(ts, noisy_id).masks if m.id == voronoi_id).noise_scale = 6.
    shader_preview.tick(scene, ts)
    info = shader_preview.diagnostics(scene, ts)
    assert info['state'] == 'FAILED' and 'Injected raster failure' in info['error'] and len(calls) == 1, info
    failed = voronoi_binding.pending_image
    failed_key = failed.get(RESOURCE)
    assert failed is not None and not failed.get('op_shader_resource_ready') and voronoi_binding.node.image == settled
    for step in range(3):   # unrelated structural edits: the failed factory never re-runs
        added = layer_by_id(ts, third_id).masks.add()
        added.id, added.source, added.procedural = new_id(), 'PROCEDURAL', 'CHECKER'
        stack.invalidate(ts)
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert revisions(info) == builds + step + 1, info
        graph = shader_preview.ensure(scene, ts)
        assert graph.units[noisy_key].tree.as_pointer() == noisy_tree, 'the failed unit was recompiled, not adopted'
        assert len(calls) == 1, f'a structural rebuild ran a failed factory again ({len(calls)} calls)'
        assert info['state'] == 'FAILED' and 'Injected raster failure' in info['error'], info
        assert graph.resources.entries[failed_key].error, 'the inherited failure is not listed by the new graph'
        assert alive(failed) and voronoi_binding.pending_image == failed and voronoi_binding.node.image == settled
        assert settled.get(RESOURCE) in graph.resources.entries, 'the leaf the node still shows is not listed'
    # Re-identifying the input retries it once (and fails again, once).
    next(m for m in layer_by_id(ts, noisy_id).masks if m.id == voronoi_id).noise_scale = 7.
    shader_preview.tick(scene, ts)
    info = shader_preview.diagnostics(scene, ts)
    assert len(calls) == 2 and info['state'] == 'FAILED', (len(calls), info)
    retried = voronoi_binding.pending_image
    assert retried is not None and retried != failed
    # Retry forgets the failures: the input is rastered again and the failed
    # placeholders are released instead of lingering in bpy.data.
    voronoi_binding.factory = real_factory
    shader_preview.retry(scene, ts)
    info = ready(scene, ts)
    graph = shader_preview.ensure(scene, ts)
    voronoi_binding = noise_binding(graph)
    assert voronoi_binding.node.image.get('op_shader_resource_ready') and voronoi_binding.pending_image is None
    assert voronoi_binding.node.image != settled and voronoi_binding.signature == voronoi_binding.identity()
    # Released, not removed (0.13 hardening): a datablock a compiling material may still
    # sample keeps existing at zero users, stripped of its keys and pixels, until Blender
    # purges it; nothing can find or bind it again.
    from open_painter.evaluation.shader_resources import released
    assert released(failed) and released(retried), 'failed placeholders outlived Retry'
    assert not [i for i in bpy.data.images if i.get(RESOURCE) and not i.get('op_shader_resource_ready')], \
        'an interrupted placeholder was left in bpy.data'
    print('ADOPTION_FAILURE_INHERITED_OK', flush=True)

    # ---- Cache owners follow inputs added to a cached layer.
    layer_by_id(ts, third_id).cache_mode = 'CACHED'
    ready(scene, ts)
    layer_cache.flush(scene)
    ready(scene, ts)
    states = shader_preview.diagnostics(scene, ts)['cache_states']
    assert states[third_id + ':BASE_COLOR'] == 'CURRENT', states
    prop = stack.channel_for(layer_by_id(ts, third_id), 'BASE_COLOR')
    levels = prop.effects.add(); levels.kind = 'LEVELS'
    stack.invalidate(ts)
    ready(scene, ts)
    layer_cache.flush(scene)
    ready(scene, ts)
    states = shader_preview.diagnostics(scene, ts)['cache_states']
    assert states[third_id + ':BASE_COLOR'] == 'CURRENT', states
    prop = stack.channel_for(layer_by_id(ts, third_id), 'BASE_COLOR')
    prop.effects[-1].black = .3   # a drag on the new effect
    states = shader_preview.diagnostics(scene, ts)['cache_states']
    assert states[third_id + ':BASE_COLOR'] == 'STALE' and layer_cache.pending(scene, ts, third_id), states
    info = shader_preview.diagnostics(scene, ts)
    assert 'BASE_COLOR' in info['affected_channels'], info
    layer_cache.flush(scene)
    ready(scene, ts)
    layer_by_id(ts, third_id).cache_mode = 'LIVE'
    ready(scene, ts)
    print('ADOPTION_CACHE_OWNERS_OK', flush=True)

    # ---- A collection add() moves the layer structs: the numeric path re-reads them.
    graph = shader_preview.ensure(scene, ts)
    raw = ts.layers.add()
    raw.id = new_id()
    graph.update(owner=stack.channel_for(layer_by_id(ts, base_id), 'BASE_COLOR'), check_structure=False)
    assert graph._plan.layers[third_id].as_pointer() == layer_by_id(ts, third_id).as_pointer()
    ts.layers.remove(len(ts.layers) - 1)
    stack.invalidate(ts)
    ready(scene, ts)

    # ---- A discarded unit's boundary is not rastered (nor its error raised) on a fresh build.
    ts.preview_backend = 'CPU'
    shader_preview.clear()
    late = layer_by_id(ts, late_id)
    late.visible = False
    drips = late.masks.add()
    drips.id, drips.source, drips.generator = new_id(), 'GENERATOR', 'DRIPS'
    fresh = Graph(scene, ts, ts.material)
    fresh.build()
    assert any('not compiled' in w for w in fresh._last['warnings']), fresh._last['warnings']
    orphans = [k for k in fresh.resources.queue if k not in fresh.resources.used]
    assert len(orphans) == 1 and fresh._last['raster_boundaries_queued'] == len(fresh.resources.queue) - 1
    while fresh.pending_resources:
        fresh.step_resources(.1)   # the orphaned Drips job is dropped, not run
    assert orphans[0] not in fresh.resources.entries
    fresh.close()
    layer_by_id(ts, late_id).masks.remove(len(layer_by_id(ts, late_id).masks) - 1)
    material_manager.repair(ts)
    print('ADOPTION_ORPHAN_BOUNDARY_OK', flush=True)

    # ---- Float paint canvases: premultiplied texels read back straight, packed or not.
    for identifier in (relief_id, wear_id, producer_id, reader_id, grouped_id, noisy_id, late_id):
        layer_by_id(ts, identifier).visible = False
    layer_by_id(ts, third_id).visible = False
    paint = stack.add(ts, 'PAINT', 'Marks'); paint_id = paint.id
    canvas = channel_paint.target(ts, paint, 'BASE_COLOR')
    assert canvas.alpha_mode == 'CHANNEL_PACKED' and canvas.is_float
    data = np.zeros((512, 512, 4), np.float32)
    data[128:384, 128:384] = (.25, 0., 0., .5)   # straight red .5 at alpha .5
    images.write(canvas, data, pack=False)
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 1
    scene.render.bake.margin = 0
    scene.render.bake.use_clear = True
    target = bpy.data.images.new('Adoption readback', width=N, height=N, float_buffer=True)
    target.colorspace_settings.name = 'Non-Color'

    def bake(label):
        graph = Graph(scene, SampledSet(ts), ts.material)
        graph.build()
        while graph.pending_resources:
            graph.step_resources(.1)
        tree = ts.material.node_tree
        output = next(n for n in tree.nodes if n.bl_idname == 'ShaderNodeOutputMaterial')
        emission = tree.nodes.new('ShaderNodeEmission')
        node = tree.nodes.new('ShaderNodeTexImage'); node.image = target; tree.nodes.active = node
        tree.links.new(emission.outputs[0], output.inputs['Surface'])
        tree.links.new(graph.outputs['BASE_COLOR'], emission.inputs['Color'])
        bpy.ops.object.select_all(action='DESELECT'); plane.select_set(True); bpy.context.view_layer.objects.active = plane
        bpy.ops.object.bake(type='EMIT')
        actual = images.pixels(target)[..., :3].copy()
        steps = LayerEvaluator(scene).channel_steps(ts.id, 'BASE_COLOR', preview_resolution=N)
        try:
            while True:
                next(steps)
        except StopIteration as done:
            expected = images.pixels(done.value)[..., :3].copy()
        tree.nodes.remove(node); tree.nodes.remove(emission)
        graph.close(); material_manager.repair(ts)
        centre = actual[N // 2, N // 2]
        assert abs(centre[0] - .35) < 2e-3, (label, centre)   # .5 red at alpha .5 over the .2 base
        assert np.allclose(actual, expected, atol=3e-3), (label, float(np.abs(actual - expected).max()))
        print('ADOPTION_CANVAS_PARITY', label, flush=True)

    bake('unpacked')
    canvas.pack()
    assert canvas.packed_file is not None and canvas.alpha_mode == 'CHANNEL_PACKED'
    bake('packed')
    print('SHADER_ADOPTION_PASS', flush=True)
    open_painter.unregister()


run()
