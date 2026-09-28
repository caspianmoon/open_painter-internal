"""Switching a set's resolution while a native session is live, then editing a boundary input.

Units are moved between graph builds when their signatures match; their boundary factories
are closures over the graph they were compiled in. After a resolution change such a moved
unit rastered at the old size and the next edit of one of its inputs failed to publish
("could not broadcast (16, 2048, 1) into (16, 4096, 3)"). Units are recompiled instead.
"""
import sys
from pathlib import Path
from time import monotonic

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
# Retain explicit coverage of CPU-boundary scheduling and invalidation.
from unittest.mock import patch
patch('open_painter.evaluation.engine.filters.supported', return_value=False).start()
from open_painter.core import image_manager as images, layer_stack as stack, material_manager
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache, shader_preview
from open_painter.utils.ids import new_id

open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_plane_add(size=2)
plane = bpy.context.object
ts = sets.create(scene, 'Resolution switch', 512)
sets.assign(scene, ts, plane)
base = stack.add(ts, 'FILL', 'Base')
stack.channel_for(base, 'BASE_COLOR').color = (.2, .4, .6, 1.)
noisy = stack.add(ts, 'FILL', 'Noisy')
prop = stack.channel_for(noisy, 'BASE_COLOR')
prop.procedural, prop.procedural_scale = 'FBM', 6.
blur = prop.effects.add()
blur.kind, blur.radius, blur.space = 'GAUSSIAN_BLUR', 3., 'UV'
mask = noisy.masks.add()
mask.id, mask.source, mask.generator = new_id(), 'GENERATOR', 'NOISE'
mask.noise_scale = 12.
material_manager.repair(ts)


def ready():
    deadline = monotonic() + 120
    while monotonic() < deadline:
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert not info.get('error'), info
        if shader_preview.current(scene, ts):
            return info
    raise AssertionError(shader_preview.diagnostics(scene, ts))


def boundary_sizes():
    graph = shader_preview.session_graph(scene, ts)
    shader_preview.flush(scene, ts)
    return {b.identifier: tuple(b.node.image.size) for b in graph.resource_bindings if b.node.image is not None}


ready()
sizes = boundary_sizes()
assert sizes and all(size == (512, 512) for size in sizes.values()), sizes
for resolution in ('1024', '512', '2048'):
    ts.resolution = resolution
    ready()
    n = int(resolution)
    assert shader_preview.session_graph(scene, ts).n == n
    # Edits of boundary inputs after the switch: the recomputed rasters have the new size.
    blur.radius += .5
    ready()
    mask.noise_scale += 1.
    ready()
    prop.procedural_scale += 1.
    info = ready()
    assert not info.get('error'), info
    sizes = boundary_sizes()
    assert sizes and all(size == (n, n) for size in sizes.values()), (resolution, sizes)
    # The engine's texture pool: every free texture is the size it is filed under (a switch
    # once released the previous resolution's textures under the new size).
    from open_painter.evaluation.engine import runtime
    misfiled = [(side, texture.width) for (side, _), free in runtime.device().pool.items() for texture in free
                if texture.width != side or texture.height != side]
    assert not misfiled, misfiled
    engine = shader_preview.session_engine(scene, ts)
    wrong = [(key[0], texture.width) for key, state in engine.states.items() for texture in (state.textures or ())
             if texture.width != n or texture.height != n]
    assert not wrong, ('units render into textures of another size', wrong)
    print('RESOLUTION_SWITCH_OK', resolution, len(sizes), flush=True)
print('RESOLUTION_SWITCH_PASS', flush=True)
open_painter.unregister()
