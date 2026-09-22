"""Undo-consistent preview leaf lifetimes (0.13 stability S2, docs/quality-stability-0.13.md).

The open memfile-undo crash: after an undo, the relearn tick reconstructed the live graph and
allocated fresh leaf image datablocks bound into the persisted material tree *outside* any undo
step; a later undo freed them while the tree still pointed at them, and the next rehydration walked
a freed pointer (EEVEE `BKE_image_acquire_ibuf`, `Graph.unique_images`). The fix withholds new-leaf
creation from the timer between undo steps (a freeze set by `cache.on_undo`, released by the next
authored gesture): reconstruction may only regenerate content into the datablock a node already
binds. This asserts that invariant on the real modules with real `bpy.ops.ed.undo`.
"""
import json
import sys
from pathlib import Path
from time import monotonic

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.constants import OWNER
from open_painter.core import material_manager, texture_set_manager as sets
from open_painter.evaluation import cache, shader_preview, shader_resources
from open_painter.evaluation.shader_resources import RESOURCE, alive
from open_painter.smart_materials import composer
import numpy as np

report = {'checks': []}


def check(name, **facts):
    report['checks'].append({'name': name, **facts})
    print(f'UNDO_LEAF {name}: PASS {facts}', flush=True)


def active_set():
    scene = bpy.context.scene
    return scene.open_painter.texture_sets[scene.open_painter.active_texture_set]


def ready(scene, ts, timeout=60):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert not info['error'], info
        if shader_preview.current(scene, ts):
            return info
    raise AssertionError(shader_preview.diagnostics(scene, ts))


def resource_leaves():
    return {img.as_pointer() for img in bpy.data.images if img.get(RESOURCE)}


def bound_resource_images(graph):
    """Every RESOURCE leaf the persisted trees' texture nodes bind — the pointers a rehydration
    walks. Reading them must never dereference a freed datablock."""
    pointers = set()
    for node in graph.nodes():
        if node.bl_idname == 'ShaderNodeTexImage' and node.image is not None and node.image.get(RESOURCE):
            assert alive(node.image), f'a bound leaf is not alive: {node.name}'
            pointers.add(node.image.as_pointer())
    return pointers


open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_plane_add(size=2)
ts = sets.create(scene, 'Leaf lifetime', 512)
sets.assign(scene, ts, bpy.context.object)
n = 32
u, v = np.meshgrid((np.arange(n, dtype=np.float32) + .5) / n, (np.arange(n, dtype=np.float32) + .5) / n)
from open_painter.core import image_manager as images
for kind in ('CURVATURE', 'AO', 'POSITION', 'WORLD_NORMAL', 'THICKNESS', 'UV_BORDER'):
    entry = ts.baked_maps.add()
    entry.kind = kind
    entry.image = images.create('Leaf ' + kind, n)
    data = np.ones((n, n, 4), np.float32)
    data[..., 0], data[..., 1], data[..., 2] = .1 + .8 * u, .1 + .8 * v, v
    images.write(entry.image, data, pack=False)
composer.apply(ts, 'Worn Painted Metal')
material_manager.repair(ts)
scene = bpy.context.scene
ts = active_set()
info = ready(scene, ts)
graph = shader_preview.ensure(scene, ts)
assert graph.pending_resources or graph.unique_images(), 'the fixture must exercise leaf resources'
# Let every isolated resource finish so leaves are published and bound.
graph = shader_preview.flush(scene, ts)
before_bound = bound_resource_images(graph)
assert before_bound, 'the material must bind at least one leaf datablock'
check('fixture binds leaf datablocks', bound=len(before_bound), leaves=len(resource_leaves()))

# Two authored undo steps so there is a state to undo to.
bpy.ops.ed.undo_push(message='baseline')
layer = next(item for item in ts.layers if item.type != 'FOLDER' and item.masks)
layer_id = layer.id
layer.masks[0].amount = .5
ready(scene, ts)
bpy.ops.ed.undo_push(message='edit')

# ---- the transition: a real undo, then the relearn ticks that used to allocate the crash leaves ----
assert not shader_resources.new_leaves_frozen()
leaves_before_undo = resource_leaves()
bpy.ops.ed.undo()
scene = bpy.context.scene
ts = active_set()
assert shader_resources.new_leaves_frozen(), 'on_undo must freeze new-leaf creation'
# Reconstruction runs while frozen: it may regenerate content but must not create a new datablock
# bound into the persisted tree, and must not crash walking node images.
for _ in range(200):
    shader_preview.tick(scene, ts)
    if shader_preview.current(scene, ts):
        break
graph = shader_preview.ensure(scene, ts)
bound_after = bound_resource_images(graph)         # reads every node.image: no freed pointer
new_leaves = resource_leaves() - leaves_before_undo
assert not (new_leaves & bound_after), \
    f'a leaf created while frozen is bound into the persisted tree: {new_leaves & bound_after}'
info = shader_preview.diagnostics(scene, ts)
assert info['graph_rehydrations'] >= 1 and info['graph_builds'] == 0, info
check('post-undo reconstruction created no new bound leaf', frozen=True,
      new_resource_datablocks=len(new_leaves), bound=len(bound_after),
      rehydrations=info['graph_rehydrations'], builds=info['graph_builds'])

# ---- a second undo must not free a leaf the persisted tree still binds (the crash) ----
bpy.ops.ed.redo()
scene = bpy.context.scene
ts = active_set()
for _ in range(200):
    shader_preview.tick(scene, ts)
    if shader_preview.current(scene, ts):
        break
bpy.ops.ed.undo()
scene = bpy.context.scene
ts = active_set()
for _ in range(200):
    shader_preview.tick(scene, ts)
    if shader_preview.current(scene, ts):
        break
graph = shader_preview.ensure(scene, ts)
bound = bound_resource_images(graph)               # would SIGSEGV on the baseline if any is dangling
assert all(alive(img) for img in bpy.data.images if img.get(RESOURCE) and img.as_pointer() in bound)
check('repeated undo/redo leaves every bound leaf alive', bound=len(bound))

# ---- an authored gesture (its operator pushes) releases the freeze; building resumes ----
shader_resources.freeze_new_leaves(False)          # what vector_brush._begin does at pointer-down
assert not shader_resources.new_leaves_frozen()
target = next(item for item in ts.layers if item.id == layer_id)
target.blend_mode = 'MULTIPLY' if target.blend_mode != 'MULTIPLY' else 'SCREEN'
info = ready(scene, ts)
assert info['graph_builds'] >= 1, ('a structural edit after the freeze released must build', info)
check('freeze released, structural build resumes', builds=info['graph_builds'])

report['result'] = 'PASS'
out = ROOT / '.blender-test' / 'undo-leaf-lifetime.json'
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps(report, indent=2))
print('UNDO_LEAF_LIFETIME_PASS', json.dumps(report), flush=True)
open_painter.unregister()
