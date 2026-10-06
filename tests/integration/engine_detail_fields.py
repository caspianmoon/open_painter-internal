"""Triplanar image detail as FIELD units (``shader_graph.DETAIL_FIELDS``), end to end.

Headless (``gpu.init``). A fill layer whose channels modulate one triplanar detail image holds
the detail in a FIELD unit per channel: one samples the image and the others copy its texels
(``runtime.FIELD_COPIES``). The first evaluation samples it once (the scheduler reports the
new image to the depsgraph first). A colour edit samples no detail again; an edit of one channel's
projection samples that channel's detail alone. The published channels are bit-identical with
copies off and with the detail compiled into every channel, as before fields. The engine's units
carry no rehydration descriptors.
"""
import sys
from collections import Counter
from pathlib import Path
from time import monotonic

import bpy
import gpu
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import image_manager as images, layer_stack as stack
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache, shader_graph, shader_preview
from open_painter.evaluation.engine import runtime

N = 512
DETAILED = ('BASE_COLOR', 'ROUGHNESS', 'METALLIC', 'HEIGHT')


def ready(scene, ts, seconds=120):
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert not info['error'], info
        if shader_preview.current(scene, ts) and not info.get('pending_resources'):
            return info
    raise AssertionError(shader_preview.diagnostics(scene, ts))


def scheduled(scene, ts, seconds=120):
    """The scheduler's own tick (``cache.tick``), as Blender's timer runs it, until current."""
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        cache.tick()
        info = shader_preview.diagnostics(scene, ts)
        assert not info['error'], info
        if shader_preview.current(scene, ts) and not info.get('pending_resources'):
            return info
    raise AssertionError(shader_preview.diagnostics(scene, ts))


def session(scene, ts):
    return shader_preview._sessions[shader_preview.key_for(scene, ts)]


def counted(scene, ts):
    """Passes by kind of the evaluations the session recorded."""
    total = Counter()
    for row in session(scene, ts).get('history', ()):
        total.update(row['passes_by_kind'])
    return total


def passes(scene, ts, edit):
    """Passes by kind of the evaluations ``edit`` runs."""
    session(scene, ts).get('history', []).clear()
    edit()
    ready(scene, ts)
    return counted(scene, ts)


def published(ts):
    runtime.device().finish()
    return {out.channel: np.array(gpu.texture.from_image(out.image).read(), np.float32).copy()
            for out in ts.outputs if out.image is not None}


def fields(scene, ts):
    return sorted(key for key in session(scene, ts)['graph'].units if key[0] == 'FIELD')


def rebuilt(scene, ts):
    """Every session goes and the set evaluates again from scratch, under the current switches:
    what it publishes and its passes."""
    shader_preview.clear()
    stack.invalidate(ts)
    ready(scene, ts)
    return published(ts), counted(scene, ts)


def identical(label, expected, actual):
    assert expected.keys() == actual.keys(), (label, sorted(expected), sorted(actual))
    for channel, values in expected.items():
        differing = int(np.count_nonzero(values.view(np.uint32) != actual[channel].view(np.uint32)))
        assert differing == 0, f'{label}: {channel} differs in {differing} texels'


def main():
    gpu.init()
    open_painter.register()
    if bpy.app.timers.is_registered(cache.tick):
        bpy.app.timers.unregister(cache.tick)
    scene = bpy.context.scene
    for obj in tuple(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16)
    ts = sets.create(scene, 'Detail fields', N)
    sets.assign(scene, ts, bpy.context.object)

    y, x = np.indices((64, 64), dtype=np.float32) / 63
    data = np.ones((64, 64, 4), np.float32)
    data[..., 0] = .5 + .5 * np.sin(6.2831 * (3 * x + 2 * y))
    data[..., 1] = .5 + .5 * np.cos(6.2831 * 5 * y)
    data[..., 2] = x * y
    detail = images.create('Detail field', 64)
    images.write(detail, data)

    base = stack.add(ts, 'FILL', 'Base')
    stack.channel_for(base, 'BASE_COLOR').color = (.2, .4, .6, 1)
    layer = stack.add(ts, 'FILL', 'Detailed')
    for channel in DETAILED:
        prop = stack.channel_for(layer, channel)
        prop.enabled = True
        prop.detail_image = detail
        prop.projection = 'TRIPLANAR'
    # Another projection compiles its one sample into the channel: no field.
    planar = stack.add(ts, 'FILL', 'Planar')
    prop = stack.channel_for(planar, 'ROUGHNESS')
    prop.enabled, prop.detail_image, prop.opacity = True, detail, .5
    stack.invalidate(ts)
    # The detail image joins the depsgraph on its next evaluation, which reports it as updated:
    # the tick evaluates the depsgraph first, or every channel sampling it renders twice.
    scheduled(scene, ts)

    keys = fields(scene, ts)
    assert keys == sorted(('FIELD', layer.id, channel) for channel in DETAILED), keys
    # Headless trees are never rehydrated: the engine writes none of the descriptors that a
    # material's persisted trees carry for it (Graph.DESCRIPTORS).
    graph = session(scene, ts)['graph']
    described = [unit.key for unit in graph.units.values() if any(unit.tree.get(name) for name in ('op_flags', 'op_exports'))
                 or any(node.get('op_binding') or node.get('op_image') for node in unit.tree.nodes)]
    assert not described, described
    first = published(ts)
    evaluated = counted(scene, ts)
    assert evaluated['FIELD'] == 1 and evaluated['FIELD copy'] == len(DETAILED) - 1, evaluated
    print('FIELDS first evaluation', dict(evaluated), 'scheduler',
          session(scene, ts)['engine'].report.get('execution_backend'), flush=True)
    idle = passes(scene, ts, lambda: [cache.tick() for _ in range(3)])
    assert not idle, f'the first evaluation rendered twice: {dict(idle)}'

    def colour():
        stack.channel_for(layer, 'BASE_COLOR').color = (.9, .3, .1, 1)
    edited = passes(scene, ts, colour)
    assert edited['FIELD'] == 0, f'a colour edit sampled the detail again: {dict(edited)}'
    print('FIELDS colour edit', dict(edited), flush=True)

    def projection():
        stack.channel_for(layer, 'ROUGHNESS').triplanar_hardness = 7
    edited = passes(scene, ts, projection)
    assert edited['FIELD'] == 1, f'one channel\'s projection edit samples its detail alone: {dict(edited)}'
    print('FIELDS projection edit', dict(edited), flush=True)
    with_fields = published(ts)
    assert any(np.any(first[channel] != with_fields[channel]) for channel in ('BASE_COLOR', 'ROUGHNESS'))

    runtime.FIELD_COPIES = False
    try:
        uncopied, evaluated = rebuilt(scene, ts)
        assert evaluated['FIELD'] == len(DETAILED) and not evaluated['FIELD copy'], evaluated
        identical('copies off', with_fields, uncopied)
    finally:
        runtime.FIELD_COPIES = True
    shader_graph.DETAIL_FIELDS = False
    try:
        compiled, evaluated = rebuilt(scene, ts)
        assert not fields(scene, ts) and not evaluated['FIELD'], evaluated
        identical('detail compiled into every channel', with_fields, compiled)
    finally:
        shader_graph.DETAIL_FIELDS = True
    again, evaluated = rebuilt(scene, ts)
    assert fields(scene, ts) and evaluated['FIELD copy'], evaluated
    identical('fields again', with_fields, again)
    print('ENGINE_DETAIL_FIELDS_OK', flush=True)
    open_painter.unregister()


main()
