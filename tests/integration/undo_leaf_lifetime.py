"""Undo-consistent leaf lifetimes under the Open Painter engine (0.13 stability S2).

The 0.13 crash: a leaf datablock created between undo steps was bound into the persisted
material tree; a later undo freed it while the tree still pointed at it, and the next
rehydration walked a freed pointer. Under the engine the material binds only the set's
channel images (through the Open Painter Texture Set node): leaves are read by the engine's
in-memory units, which are compiled again from the restored stack after every undo. This
asserts on the real modules with real ``bpy.ops.ed.undo`` that the material never binds a
leaf, that every leaf the rebuilt session binds is alive across undo, redo and repeated
undo, and that a rebuild adopts the leaves the restored state needs instead of making them
again.
"""
import json
import sys
from pathlib import Path
from time import monotonic

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import material_manager, texture_set_manager as sets
from open_painter.evaluation import cache, shader_preview, shader_resources
from open_painter.evaluation.metrics import Metrics
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


def material_leaves(material):
    """RESOURCE leaves any node of the material (or of a group it uses) binds: none may."""
    found, seen = set(), set()

    def visit(tree):
        if tree is None or tree.as_pointer() in seen:
            return
        seen.add(tree.as_pointer())
        for node in tree.nodes:
            image = getattr(node, 'image', None)
            if image is not None and image.get(RESOURCE):
                found.add(image.name)
            visit(getattr(node, 'node_tree', None))

    visit(material.node_tree)
    return found


def bound_resource_images(graph):
    """Every RESOURCE leaf the engine's units bind. Reading them must never dereference a freed datablock."""
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
# Catalog procedurals and baked-edge wear run in the engine's programs: the leaves are the CPU
# inputs, here a spatial filter and a legacy value-noise mask.
from open_painter.utils.ids import new_id
holder = next(item for item in ts.layers if item.type != 'FOLDER' and item.masks)
blur = holder.masks.add()
blur.id, blur.source = new_id(), 'FILTER'
blur.effect.kind, blur.effect.radius = 'GAUSSIAN_BLUR', 2
noise = holder.masks.add()
noise.id, noise.source = new_id(), 'NOISE'
material_manager.repair(ts)
scene = bpy.context.scene
ts = active_set()
ready(scene, ts)
# Let every isolated resource finish so leaves are published and bound.
graph = shader_preview.flush(scene, ts)
before_bound = bound_resource_images(graph)
assert before_bound, 'the fixture must exercise leaf resources'
assert not material_leaves(ts.material), material_leaves(ts.material)
check('the engine binds leaves, the material none', bound=len(before_bound), leaves=len(resource_leaves()))

# Two authored undo steps so there is a state to undo to.
bpy.ops.ed.undo_push(message='baseline')
layer = next(item for item in ts.layers if item.type != 'FOLDER' and item.masks)
layer_id = layer.id
layer.masks[0].amount = .5
ready(scene, ts)
bpy.ops.ed.undo_push(message='edit')

# ---- the transition: a real undo, then the ticks that compile the restored stack ----
leaves_before_undo = resource_leaves()
bpy.ops.ed.undo()
scene = bpy.context.scene
ts = active_set()
assert shader_resources.new_leaves_frozen(), 'on_undo must freeze new material datablocks'
with Metrics() as observed:
    ready(scene, ts)
graph = shader_preview.ensure(scene, ts)
bound_after = bound_resource_images(graph)         # reads every node.image: no freed pointer
# A generated leaf comes back from the memfile without its pixels: the rebuild produces it
# again, into the datablock the undone state had, never beside it.
assert bound_after <= leaves_before_undo, 'the rebuild bound leaves the undone state did not have'
assert observed.counts['shader_resource_requests'] == observed.counts['shader_resource_reused_datablocks'], observed.report()
assert not material_leaves(ts.material)
info = shader_preview.diagnostics(scene, ts)
check('post-undo rebuild keeps its leaf datablocks', bound=len(bound_after), builds=info['graph_builds'],
      regenerated=observed.counts['shader_resource_requests'])

# ---- a second undo after a redo leaves every bound leaf alive ----
bpy.ops.ed.redo()
scene = bpy.context.scene
ts = active_set()
ready(scene, ts)
bpy.ops.ed.undo()
scene = bpy.context.scene
ts = active_set()
ready(scene, ts)
graph = shader_preview.ensure(scene, ts)
bound = bound_resource_images(graph)
assert all(alive(img) for img in bpy.data.images if img.get(RESOURCE) and img.as_pointer() in bound)
assert not material_leaves(ts.material)
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
