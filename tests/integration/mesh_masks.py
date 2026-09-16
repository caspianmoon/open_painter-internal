"""Mesh masks (0.9.2): Blender's objects, collections, materials, groups, selection, islands,
normals and attributes drive mask coverage exactly, and follow scene changes live."""
import math
import sys
from pathlib import Path
from time import monotonic

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import image_manager as images, layer_stack as stack, material_manager
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache, dependencies, mesh_masks, shader_preview
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


def cube(name, size, uv_box, subdivisions=0):
    bpy.ops.mesh.primitive_cube_add(size=size)
    obj = bpy.context.object
    obj.name = name
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    if subdivisions:
        bpy.ops.mesh.subdivide(number_cuts=subdivisions)
    bpy.ops.uv.smart_project(island_margin=.03)
    bpy.ops.mesh.select_all(action='DESELECT')
    bpy.ops.object.mode_set(mode='OBJECT')
    uv = obj.data.uv_layers.active
    coords = np.empty((len(obj.data.loops), 2), np.float32)
    uv.data.foreach_get('uv', coords.ravel())
    u0, v0, u1, v1 = uv_box
    coords[:, 0] = u0 + coords[:, 0] * (u1 - u0)
    coords[:, 1] = v0 + coords[:, 1] * (v1 - v0)
    uv.data.foreach_set('uv', coords.ravel())
    return obj


def cpu_mask(ts, mask, n):
    return mesh_masks.raster(ts, mask, n)


def coverage_of(ts, mask, obj, n):
    """Mean mask value over the texels the object's assigned faces cover."""
    from open_painter.geometry.snapshots import targets
    from open_painter.geometry.uv_rasterizer import rasterize
    values = cpu_mask(ts, mask, n)
    with targets(ts) as meshes:
        target = next(t for t in meshes if t['object'] == obj)
        rows, cols, _, _ = rasterize(target['uv'][target['tri_loops']], n)
    return float(values[rows, cols].mean()) if len(rows) else 0.0


open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
body = cube('Body', 2.0, (0., 0., .6, .6), 1)
screw_a = cube('Screw_01', .4, (.65, .65, .85, .85))
screw_b = cube('Screw_02', .4, (.65, .05, .85, .25))
screw_a.location.x, screw_b.location.x = 3, 4
steel = bpy.data.materials.new('Steel')
screw_b.data.materials.append(steel)      # slot 0 holds Steel before assignment
screws = bpy.data.collections.new('Screws')
scene.collection.children.link(screws)
screws.objects.link(screw_a)
ts = sets.create(scene, 'Mesh masks', 512)
for obj in (body, screw_a, screw_b):
    sets.assign(scene, ts, obj)
assert next(r for r in ts.assignments if r.object == screw_b).original_material == steel

base = stack.add(ts, 'FILL', 'Base')
stack.channel_for(base, 'BASE_COLOR').color = (.2, .2, .2, 1.)
rust = stack.add(ts, 'FILL', 'Rust')
rust_id = rust.id
stack.channel_for(rust, 'BASE_COLOR').color = (.8, .3, .1, 1.)
mask = rust.masks.add()
mask.id, mask.name, mask.source = new_id(), 'Screws', 'MESH'
mask.mesh.kind = 'COLLECTION'
mask.mesh.collection = screws
mask_id = mask.id
layer_of = lambda: next(l for l in ts.layers if l.id == rust_id)
mask_of = lambda: next(m for m in layer_of().masks if m.id == mask_id)
n = 512

# Collection: only Screw_01 is covered; link Screw_02 and the mask follows (new signature).
before = mesh_masks.signature(ts, mask_of())
assert coverage_of(ts, mask_of(), screw_a, n) > .99 and coverage_of(ts, mask_of(), body, n) == 0
assert coverage_of(ts, mask_of(), screw_b, n) == 0
screws.objects.link(screw_b)
after = mesh_masks.signature(ts, mask_of())
assert after != before and coverage_of(ts, mask_of(), screw_b, n) > .99
screws.objects.unlink(screw_b)
print('MESH_MASK_COLLECTION_OK', flush=True)

# Object, material (the slot's original material), vertex group, normal direction, selection, island, attribute.
mask_of().mesh.kind = 'OBJECT'; mask_of().mesh.object = body
assert coverage_of(ts, mask_of(), body, n) > .99 and coverage_of(ts, mask_of(), screw_a, n) == 0
mask_of().mesh.kind = 'MATERIAL'; mask_of().mesh.material = steel
assert coverage_of(ts, mask_of(), screw_b, n) > .99 and coverage_of(ts, mask_of(), screw_a, n) == 0
group = body.vertex_groups.new(name='Worn')
top_vertices = [v.index for v in body.data.vertices if v.co.z > .99]
group.add(top_vertices, 1.0, 'REPLACE')
mask_of().mesh.kind = 'VERTEX_GROUP'; mask_of().mesh.attribute = 'Worn'
values = cpu_mask(ts, mask_of(), n)
assert values.max() > .99 and 0 < coverage_of(ts, mask_of(), body, n) < .5, coverage_of(ts, mask_of(), body, n)
mask_of().mesh.kind = 'NORMAL_DIRECTION'; mask_of().mesh.direction = (0, 0, 1)
mask_of().mesh.angle, mask_of().mesh.softness = math.radians(30), math.radians(10)
top_cover = coverage_of(ts, mask_of(), body, n)
assert .1 < top_cover < .25, top_cover     # one of six faces
mask_of().mesh.direction = (0, 0, -1)
assert abs(coverage_of(ts, mask_of(), body, n) - top_cover) < .03
for polygon in body.data.polygons:
    polygon.select = polygon.normal.x > .9
mask_of().mesh.kind = 'FACE_SELECTION'
assert .1 < coverage_of(ts, mask_of(), body, n) < .25
mask_of().mesh.kind = 'UV_ISLAND'; mask_of().mesh.object = body; mask_of().mesh.island = -1
island_cover = coverage_of(ts, mask_of(), body, n)
assert .1 < island_cover < .25, island_cover
mask_of().mesh.island = 0
assert coverage_of(ts, mask_of(), body, n) > 0
attribute = body.data.attributes.new('wear', 'FLOAT', 'FACE')
attribute.data.foreach_set('value', np.linspace(0, 1, len(body.data.polygons)).astype(np.float32))
mask_of().mesh.kind = 'ATTRIBUTE'; mask_of().mesh.attribute = 'wear'
values = cpu_mask(ts, mask_of(), n)
assert values.max() > .95 and 0 < values[values > 0].mean() < 1
sculpt = body.data.attributes.new('.sculpt_face_set', 'INT', 'FACE')
ids = np.ones(len(body.data.polygons), np.int32); ids[:4] = 2
sculpt.data.foreach_set('value', ids)
mask_of().mesh.kind = 'FACE_SET'; mask_of().mesh.face_set = 2
assert 0 < coverage_of(ts, mask_of(), body, n) < .5
print('MESH_MASK_KINDS_OK', flush=True)

# Shader path: the boundary matches the CPU reference; a membership change re-identifies it.
mask_of().mesh.kind = 'COLLECTION'
mask_of().mesh.collection = screws
material_manager.repair(ts)
ready(scene, ts)
shader_preview.flush(scene, ts)
ts.preview_backend = 'CPU'
shader_preview.clear()
bpy.context.view_layer.update()
scene.render.engine = 'CYCLES'; scene.cycles.device = 'CPU'; scene.cycles.samples = 1
scene.render.bake.margin = 0; scene.render.bake.use_clear = True
target = bpy.data.images.new('Mesh mask readback', width=N, height=N, float_buffer=True)
target.colorspace_settings.name = 'Non-Color'
small = SampledSet(ts)


def bake_channel(graph):
    for obj in (body, screw_a, screw_b):
        obj.select_set(True)
    bpy.context.view_layer.objects.active = body
    tree = ts.material.node_tree
    output = next(node for node in tree.nodes if node.bl_idname == 'ShaderNodeOutputMaterial')
    emission = tree.nodes.new('ShaderNodeEmission')
    bake_node = tree.nodes.new('ShaderNodeTexImage')
    bake_node.image = target
    tree.nodes.active = bake_node
    tree.links.new(emission.outputs[0], output.inputs['Surface'])
    tree.links.new(graph.outputs['BASE_COLOR'], emission.inputs['Color'])
    bpy.ops.object.bake(type='EMIT')
    result = images.pixels(target)[..., :3].copy()
    tree.nodes.remove(bake_node); tree.nodes.remove(emission)
    return result


def cpu_channel():
    steps = LayerEvaluator(scene).channel_steps(ts.id, 'BASE_COLOR', preview_resolution=N)
    try:
        while True:
            next(steps)
    except StopIteration as done:
        return images.pixels(done.value)[..., :3].copy()


graph = Graph(scene, small, ts.material)
graph.build()
while graph.pending_resources:
    graph.step_resources(.1)
assert any(node.get('op_boundary', '').startswith('["mask_source"') for node in graph.nodes())
actual, expected = bake_channel(graph), cpu_channel()
# Cycles jitters bake samples inside each pixel; a hard-edged mask sampled from
# an image blends across its edge texels, so parity is exact off the edge band.
coverage = cpu_mask(ts, mask_of(), N)
edge = np.zeros_like(coverage, bool)
for axis in (0, 1):
    for step in (1, -1):
        edge |= np.abs(np.roll(coverage, step, axis=axis) - coverage) > .01
from open_painter.geometry.snapshots import targets
from open_painter.geometry.uv_rasterizer import rasterize
covered = np.zeros_like(coverage, bool)
with targets(ts) as meshes:
    for target_mesh in meshes:
        rows, cols, _, _ = rasterize(target_mesh['uv'][target_mesh['tri_loops']], N)
        covered[rows, cols] = True
for axis in (0, 1):
    for step in (1, -1):
        edge |= covered != np.roll(covered, step, axis=axis)
compare = covered & ~edge
assert compare.sum() > 100
error = float(np.abs(actual - expected)[compare].max())
assert error < 3e-4, error
assert (actual[compare] > .5).any() and (actual[compare] < .3).any(), 'both rust and base must be baked'
graph.close()
material_manager.repair(ts)
print('MESH_MASK_PARITY', error, flush=True)

# Live: the depsgraph handler notices the collection change and the session re-evaluates.
ts.preview_backend = 'LIVE'
material_manager.repair(ts)
ready(scene, ts)
shader_preview.flush(scene, ts)
serial = ts.edit_serial
graph = shader_preview.ensure(scene, ts)
node = next(node for node in graph.nodes() if node.get('op_boundary', '').startswith('["mask_source"'))
image_before = node.image
assert dependencies.uses_geometry(ts)
screws.objects.link(screw_b)
bpy.context.view_layer.update()
dependencies._candidates.add(scene.as_pointer())    # what on_depsgraph does for a collection update
dependencies.refresh(scene)
assert ts.edit_serial != serial, 'membership change must invalidate the set'
ready(scene, ts)
shader_preview.flush(scene, ts)
graph = shader_preview.ensure(scene, ts)
node = next(node for node in graph.nodes() if node.get('op_boundary', '').startswith('["mask_source"'))
assert node.image is not image_before or node.image.get('open_painter_shader_resource') != image_before.get('open_painter_shader_resource')
values = images.pixels(node.image)[..., 0]
assert coverage_of(ts, mask_of(), screw_b, 512) > .99
print('MESH_MASK_LIVE_OK', flush=True)

# Portable materials keep the names, not the pointers; the selector re-links by name.
from open_painter.smart_materials.serializer import save
import zipfile, json
archive = ROOT / '.blender-test' / 'mesh-mask-material.zip'
if archive.exists():
    archive.unlink()
save(ts, archive)
with zipfile.ZipFile(archive) as bundle:
    data = json.loads(bundle.read('material.json'))
stored = next(m for layer in data['layers'] for m in layer.get('masks', []) if m.get('source') == 'MESH')
assert stored['mesh']['collection'] is None and stored['mesh']['collection_name'] == 'Screws'
mask_of().mesh.collection = None            # as restore() does, pointer first...
mask_of().mesh.collection_name = 'Screws'   # ...then the mirrored name
assert mask_of().mesh.resolve('collection') == screws
assert coverage_of(ts, mask_of(), screw_a, 512) > .99
print('MESH_MASKS_PASS', flush=True)
open_painter.unregister()
