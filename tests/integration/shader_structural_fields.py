"""Every structural field flags a rebuild; every numeric field binds without one.

Guards the numeric fast path: a field that changes compiled topology must use
the structural callback, otherwise the shader would silently go stale.
"""
from pathlib import Path
import sys
from time import monotonic

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import image_manager as images, layer_stack as stack, material_manager
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache, shader_preview
from open_painter.evaluation.metrics import Metrics
from open_painter.utils.ids import new_id


def revisions(info):
    """Structural revisions of the session: a rebuild, or a patch of its own graph (EngineGraph.patch)."""
    return info['graph_builds'] + info['graph_patches']


def ready(scene, ts):
    deadline = monotonic() + 30
    while monotonic() < deadline:
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert not info['error'], info
        if shader_preview.current(scene, ts):
            return info
    raise AssertionError(shader_preview.diagnostics(scene, ts))


open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_plane_add(size=2)
ts = sets.create(scene, 'Structural fields', 512)
sets.assign(scene, ts, bpy.context.object)
n = 32
u, v = np.meshgrid((np.arange(n, dtype=np.float32) + .5) / n, (np.arange(n, dtype=np.float32) + .5) / n)
for kind in ('CURVATURE', 'AO', 'POSITION', 'WORLD_NORMAL', 'THICKNESS'):
    entry = ts.baked_maps.add()
    entry.kind = kind
    entry.image = images.create('Fields ' + kind, n)
    values = np.ones((n, n, 4), np.float32)
    values[..., 0], values[..., 1], values[..., 2] = .1 + .8 * u, .1 + .8 * v, v
    images.write(entry.image, values, pack=False)
texture = images.create('Fields texture', n)
images.write(texture, np.random.default_rng(1).random((n, n, 4)).astype(np.float32), pack=False)

source = stack.add(ts, 'FILL', 'Source')
source_id = source.id
layer = stack.add(ts, 'FILL', 'Layer')
layer_id = layer.id
prop = stack.channel_for(layer, 'ROUGHNESS')
effect = prop.effects.add()
effect.kind = 'LEVELS'
mask = layer.masks.add()
mask.id, mask.source, mask.generator = new_id(), 'GENERATOR', 'EDGE_WEAR'
folder = stack.add(ts, 'FOLDER', 'Folder')
folder_id = folder.id
material_manager.repair(ts)
ready(scene, ts)


def current():
    return (next(l for l in ts.layers if l.id == layer_id), next(l for l in ts.layers if l.id == folder_id))


def probe(label, assign, structural):
    graph = shader_preview.ensure(scene, ts)
    builds = revisions(shader_preview.diagnostics(scene, ts))
    assign()
    info = shader_preview.diagnostics(scene, ts)
    flagged = bool(info['rebuild_pending'] or info['error'])
    assert flagged == structural, (label, 'flagged' if flagged else 'not flagged', info.get('error'))
    ready(scene, ts)
    after = shader_preview.diagnostics(scene, ts)
    if structural:
        assert revisions(after) == builds + 1, (label, after)
    else:
        assert revisions(after) == builds and shader_preview.ensure(scene, ts) is graph, (label, after)
    print('STRUCTURAL_FIELD', label, 'structural' if structural else 'numeric', flush=True)


def field(owner, name, value, structural):
    def assign():
        setattr(owner(), name, value)
    probe(f'{owner.__name__}.{name}={value!r}', assign, structural)


layer_of = lambda: current()[0]
folder_of = lambda: current()[1]
prop_of = lambda: stack.channel_for(current()[0], 'ROUGHNESS')
effect_of = lambda: stack.channel_for(current()[0], 'ROUGHNESS').effects[0]
mask_of = lambda: current()[0].masks[0]
mask_effect_of = lambda: current()[0].masks[0].effect
layer_of.__name__, folder_of.__name__, prop_of.__name__ = 'layer', 'folder', 'channel'
effect_of.__name__, mask_of.__name__, mask_effect_of.__name__ = 'effect', 'mask', 'mask.effect'

# Numeric: bindings and resource identities, no rebuild.
field(layer_of, 'opacity', .7, False)
field(layer_of, 'visible', False, False)
field(layer_of, 'visible', True, False)
field(prop_of, 'opacity', .8, False)
field(prop_of, 'value', .3, False)
field(prop_of, 'scale', (2., 2.), False)
field(effect_of, 'gamma', 1.7, False)
field(effect_of, 'enabled', False, False)   # pointwise: a uniform
field(effect_of, 'enabled', True, False)
field(mask_of, 'amount', .4, False)
field(mask_of, 'black', .2, False)
field(mask_of, 'invert', True, False)
field(mask_of, 'edge_width', .5, False)
field(mask_of, 'seed', 7, False)

# Structural: topology, operation types, references, images.
field(layer_of, 'blend_mode', 'MULTIPLY', True)
field(prop_of, 'blend_mode', 'SCREEN', True)
field(prop_of, 'enabled', False, True)
field(prop_of, 'enabled', True, True)
field(prop_of, 'projection', 'TRIPLANAR', True)
field(prop_of, 'projection', 'UV', True)
field(prop_of, 'image', texture, True)
field(prop_of, 'tiling', 'NONE', True)
field(prop_of, 'filtering', 'NEAREST', True)
field(prop_of, 'image', None, True)
field(prop_of, 'procedural', 'CHECKER', True)
field(prop_of, 'procedural', '', True)
field(effect_of, 'kind', 'GAMMA', True)
field(effect_of, 'kind', 'GAUSSIAN_BLUR', True)
# The blur makes the channel prefix a raster boundary; the opacity slider (the
# opacity_percent mirror of the uniform) must not re-identify it per 1% step.
with Metrics() as observed:
    prop_of().opacity_percent = 60
    info = shader_preview.diagnostics(scene, ts)
    assert not info['rebuild_pending'] and not info['pending_resources'], info
    shader_preview.tick(scene, ts)   # the engine renders the edit
    assert shader_preview.current(scene, ts), shader_preview.diagnostics(scene, ts)
    assert observed.counts['shader_resource_requests'] == 0, observed.report()
    assert abs(prop_of().opacity - .6) < 1e-6
print('STRUCTURAL_FIELD channel.opacity_percent=60 numeric (boundary kept)', flush=True)
field(effect_of, 'enabled', False, True)     # spatial: structural
field(effect_of, 'kind', 'LEVELS', True)
field(mask_of, 'generator', 'DIRT', True)
field(mask_of, 'source', 'PROCEDURAL', True)
field(mask_of, 'procedural', 'CHECKER', True)
field(mask_of, 'projection', 'TRIPLANAR', True)
field(mask_of, 'operation', 'ADD', True)
field(mask_of, 'enabled', False, True)
field(mask_of, 'enabled', True, True)
field(mask_of, 'source', 'REFERENCE', True)
field(mask_of, 'reference_layer', source_id, True)
field(mask_of, 'reference_channel', 'BASE_COLOR', True)
field(mask_of, 'reference_stage', 'PRE_FILTER', True)
field(mask_of, 'source', 'FILTER', True)
field(mask_effect_of, 'kind', 'GAUSSIAN_BLUR', True)
field(mask_effect_of, 'enabled', False, True)
field(mask_effect_of, 'kind', 'INVERT', True)
field(mask_effect_of, 'enabled', True, False)  # pointwise mask filter: a uniform
field(folder_of, 'group_mode', 'PASSTHROUGH', True)
field(layer_of, 'parent_id', folder_id, True)
field(layer_of, 'parent_id', '', True)
# Layer performance (0.9.0): preview-only state restructures the layer's units.
field(layer_of, 'bypass', True, True)
field(layer_of, 'bypass', False, True)
field(layer_of, 'cache_mode', 'CACHED', True)
field(layer_of, 'cache_mode', 'FROZEN', True)
field(layer_of, 'cache_mode', 'LIVE', True)
field(folder_of, 'bypass', True, True)
field(folder_of, 'bypass', False, True)
print('SHADER_STRUCTURAL_FIELDS_PASS', flush=True)
open_painter.unregister()
