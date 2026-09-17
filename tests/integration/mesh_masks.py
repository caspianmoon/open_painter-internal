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
# Painting weights changes no vertex, but the mesh update it reports moves the signature.
weight_signature = mesh_masks.signature(ts, mask_of())
group.add(top_vertices, 0.0, 'REPLACE')
assert mesh_masks.signature(ts, mask_of()) == weight_signature, 'weights are not hashed'
dependencies._mesh_epochs[body.data.as_pointer()] = dependencies.mesh_epoch(body.data) + 1   # what the stroke's depsgraph update reports
assert mesh_masks.signature(ts, mask_of()) != weight_signature
assert cpu_mask(ts, mask_of(), n).max() < .01
group.add(top_vertices, 1.0, 'REPLACE')
dependencies._mesh_epochs[body.data.as_pointer()] = dependencies.mesh_epoch(body.data) + 1
mask_of().mesh.kind = 'NORMAL_DIRECTION'; mask_of().mesh.direction = (0, 0, 1)
# The defaults are radians (45 deg / 15 deg): a fresh mask covers the top face only, not the whole cube.
assert abs(mask_of().mesh.angle - math.radians(45)) < 1e-6 and abs(mask_of().mesh.softness - math.radians(15)) < 1e-6
default_cover = coverage_of(ts, mask_of(), body, n)
assert .1 < default_cover < .25, default_cover
mask_of().mesh.angle, mask_of().mesh.softness = math.radians(30), math.radians(10)
top_cover = coverage_of(ts, mask_of(), body, n)
assert .1 < top_cover < .25, top_cover     # one of six faces
mask_of().mesh.direction = (0, 0, -1)
assert abs(coverage_of(ts, mask_of(), body, n) - top_cover) < .03
# The mask reads the objects' orientation, not where they stand: a drag (or a uniform scale)
# leaves the selector signature, the raster and the set's geometry key alone; a rotation, and
# a non-uniform scale (which redirects the normals through the normal matrix), re-key both.
assert not dependencies.uses_placement(ts), 'a normal-direction mask must not key the set on placement'
oriented = mesh_masks.signature(ts, mask_of())
oriented_key = dependencies.geometry_key(scene, ts)
oriented_raster = cpu_mask(ts, mask_of(), n)
body.location.x += 2.5
bpy.context.view_layer.update()
assert mesh_masks.signature(ts, mask_of()) == oriented, 'a translation re-identified the raster'
assert dependencies.geometry_key(scene, ts) == oriented_key, 'a translation re-keyed the set'
assert np.array_equal(cpu_mask(ts, mask_of(), n), oriented_raster)
body.scale = (2., 2., 2.)
bpy.context.view_layer.update()
assert mesh_masks.signature(ts, mask_of()) == oriented, 'a uniform scale re-identified the raster'
body.scale = (2., 1., 1.)
bpy.context.view_layer.update()
assert mesh_masks.signature(ts, mask_of()) != oriented, 'a non-uniform scale redirects the normals'
body.scale = (1., 1., 1.)
body.rotation_euler.x = math.radians(90)
bpy.context.view_layer.update()
assert mesh_masks.signature(ts, mask_of()) != oriented, 'a rotation must re-identify the raster'
assert dependencies.geometry_key(scene, ts) != oriented_key, 'a rotation must re-key the set'
assert not np.array_equal(cpu_mask(ts, mask_of(), n), oriented_raster), 'the covered face moved'
body.location.x, body.rotation_euler.x = 0., 0.
bpy.context.view_layer.update()
assert mesh_masks.signature(ts, mask_of()) == oriented and dependencies.geometry_key(scene, ts) == oriented_key
print('MESH_MASK_NORMAL_ORIENTATION_OK', flush=True)
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

# The selection token is compared across sessions (persisted provenance): a
# digest of the flags, never Python's per-process salted hash().
import zlib
flags = np.empty(len(body.data.polygons), bool)
body.data.polygons.foreach_get('select', flags)
assert mesh_masks._selection_token(body)[2] == zlib.crc32(flags.tobytes())

# Persisted identities digest the painted values (0.9.7): undo/redo and a reopen
# restart the mesh epochs, so an epoch-tagged digest must not answer for other weights.
from open_painter.evaluation.resource_keys import stable_keys
mask_of().mesh.kind = 'VERTEX_GROUP'; mask_of().mesh.attribute = 'Worn'
pointer = body.data.as_pointer()
with stable_keys():
    painted = mesh_masks.signature(ts, mask_of())
group.add(top_vertices, 0.0, 'REPLACE')
dependencies.clear(); dependencies._mesh_epochs[pointer] = 1       # undo: the counter restarts and the stroke's update re-bumps it
with stable_keys():
    undone = mesh_masks.signature(ts, mask_of())
assert undone != painted
group.add(top_vertices, 1.0, 'REPLACE')
dependencies.clear(); dependencies._mesh_epochs[pointer] = 1       # redo lands on the same epoch with the painted weights
with stable_keys():
    redone = mesh_masks.signature(ts, mask_of())
assert redone == painted and redone != undone, 'the digest answered for the undone weights'
dependencies.clear()                                                # a reopened file: nothing changed, same identity
with stable_keys():
    assert mesh_masks.signature(ts, mask_of()) == painted
print('MESH_MASK_DIGEST_OK', flush=True)

# Reading a vertex group is a Python loop over every vertex. Above dependencies.WEIGHT_HASH_BOUND
# the persisted token is the session-salted mesh epoch instead of a digest (a re-evaluation
# after a reopen, never seconds after every weight stroke); below it the digest as before.
from unittest.mock import patch
looped = AssertionError('looped over the vertices of a large mesh')
with stable_keys(), patch.object(dependencies, 'WEIGHT_HASH_BOUND', len(body.data.vertices) - 1), \
        patch.object(dependencies, 'group_weights', side_effect=looped):
    large = mesh_masks._content_token(body, 'VERTEX_GROUP', 'Worn')
    assert large == dependencies.epoch_token(body.data) == f'epoch:{dependencies._SESSION}:{dependencies.mesh_epoch(body.data)}'
    large_signature = mesh_masks.signature(ts, mask_of())
    dependencies._mesh_epochs[pointer] = dependencies.mesh_epoch(body.data) + 1   # a weight stroke
    assert mesh_masks.signature(ts, mask_of()) != large_signature
    assert mesh_masks._content_token(body, 'ATTRIBUTE', 'Worn') != large, 'attributes are read vectorised: still digested'
assert mesh_masks._content_token(body, 'VERTEX_GROUP', 'Worn') == dependencies.mesh_epoch(body.data), 'the session token is the epoch'
with stable_keys():
    assert mesh_masks._content_token(body, 'VERTEX_GROUP', 'Worn') == painted[2][2][0][0], 'digested again below the bound'

# The coverage reads the evaluated weights once per mesh update and modifier stack: every
# strip, resolution and mask of that geometry state reuses the array.
reads = []
read_weights = dependencies.group_weights
with patch.object(dependencies, 'group_weights', side_effect=lambda *args: reads.append(args) or read_weights(*args)):
    mesh_masks.clear()
    full = cpu_mask(ts, mask_of(), n)
    assert len(reads) == 1 and full.max() > .99
    half = cpu_mask(ts, mask_of(), n // 2)
    assert len(reads) == 1 and half.max() > .99, 'another resolution re-read the weights'
    dependencies._mesh_epochs[pointer] = dependencies.mesh_epoch(body.data) + 1   # a weight stroke
    cpu_mask(ts, mask_of(), n)
    assert len(reads) == 2, 'a mesh update must re-read the weights'
    subdivide = body.modifiers.new('Subdivide', 'SUBSURF')   # the evaluated vertices change without a mesh update
    assert cpu_mask(ts, mask_of(), n).max() > .99 and len(reads) == 3, 'a modifier edit must re-read the weights'
    body.modifiers.remove(subdivide)
    cpu_mask(ts, mask_of(), n)
    assert len(reads) == 4
print('MESH_MASK_WEIGHT_BOUND_OK', flush=True)

# The raster keys on the mesh (0.9.7): the CPU backend and the export reuse the set's
# raster cache, so a re-unwrap or a modifier edit must miss the cached coverage.
from open_painter.evaluation.raster_cache import for_texture_set


def output_red(obj):
    from open_painter.geometry.snapshots import targets
    from open_painter.geometry.uv_rasterizer import rasterize
    pixels = images.pixels(images.output(ts, 'BASE_COLOR').image)[..., 0]
    with targets(ts) as meshes:
        target = next(t for t in meshes if t['object'] == obj)
        rows, cols, _, _ = rasterize(target['uv'][target['tri_loops']], n)
    return float(pixels[rows, cols].mean())


def shift_uvs(obj, dv):
    uv = obj.data.uv_layers.active
    coords = np.empty((len(obj.data.loops), 2), np.float32)
    uv.data.foreach_get('uv', coords.ravel())
    coords[:, 1] += dv
    uv.data.foreach_set('uv', coords.ravel())
    obj.data.update()
    bpy.context.view_layer.update()
    dependencies._mesh_epochs[obj.data.as_pointer()] = dependencies.mesh_epoch(obj.data) + 1   # the edit's depsgraph update


mask_of().mesh.kind = 'OBJECT'; mask_of().mesh.object = screw_a
LayerEvaluator(scene).evaluate_texture_set(ts.id)
rust_red, base_red = output_red(screw_a), output_red(screw_b)
assert rust_red > base_red + .2, (rust_red, base_red)
cached = [key for key in for_texture_set(scene, ts) if key[0] == mesh_masks.CACHE]
assert len(cached) == 1
shift_uvs(screw_a, -.35)                        # the island moves below its old place
LayerEvaluator(scene).evaluate_texture_set(ts.id)
assert [key for key in for_texture_set(scene, ts) if key[0] == mesh_masks.CACHE] != cached, 'the raster cache kept the old island'
assert abs(output_red(screw_a) - rust_red) < .05, (output_red(screw_a), rust_red)
shift_uvs(screw_a, .35)
unwrapped = mesh_masks.signature(ts, mask_of())
bevel = screw_a.modifiers.new('Bevel', 'BEVEL')
assert mesh_masks.signature(ts, mask_of()) != unwrapped, 'a modifier edit must re-identify the raster'
screw_a.modifiers.remove(bevel)
assert mesh_masks.signature(ts, mask_of()) == unwrapped
print('MESH_MASK_RASTER_CACHE_OK', flush=True)

# Edit Mode (0.9.7): the selection is read from the edit BMesh without flushing it,
# so the scheduler's signature no longer tags the mesh (and re-hashes it) every tick.
import bmesh
mask_of().mesh.kind = 'FACE_SELECTION'
bpy.context.view_layer.objects.active = body
bpy.ops.object.mode_set(mode='EDIT')
bm = bmesh.from_edit_mesh(body.data)
for face in bm.faces:
    face.select_set(False)                          # the object-mode selection came along
for face in bm.faces:
    if face.normal.y > .9:
        face.select_set(True)
bmesh.update_edit_mesh(body.data)
bpy.context.view_layer.update()
epochs = (dependencies.mesh_epoch(body.data), dependencies.epoch())
token = mesh_masks._selection_token(body)
edited = mesh_masks.signature(ts, mask_of())
bpy.context.view_layer.update()
assert (dependencies.mesh_epoch(body.data), dependencies.epoch()) == epochs, 'the signature flushed the edit mesh'
assert token[0] == sum(1 for face in bm.faces if face.select) > 0
assert .1 < coverage_of(ts, mask_of(), body, n) < .25, coverage_of(ts, mask_of(), body, n)
bpy.context.view_layer.update()
assert (dependencies.mesh_epoch(body.data), dependencies.epoch()) == epochs, 'the raster flushed the edit mesh'
for face in bm.faces:                               # a click-select in Edit Mode moves the token at once
    face.select_set(False)
for face in bm.faces:
    if face.normal.y < -.9:
        face.select_set(True)
assert mesh_masks._selection_token(body) != token
bpy.ops.object.mode_set(mode='OBJECT')
assert .1 < coverage_of(ts, mask_of(), body, n) < .25
for polygon in body.data.polygons:
    polygon.select = polygon.normal.x > .9
print('MESH_MASK_EDIT_MODE_OK', flush=True)

# A modifier that changes the face count (0.9.8): the selection cannot be mapped onto the
# evaluated polygons, so the mask reads as empty and the set's warnings say through what.
# The composed list is core.validation.warnings (what the Validate button and the shader
# report show); evaluation.references stays bpy-free and structural.
from open_painter.core import validation
from open_painter.evaluation import references


def selection_warnings():
    return [w for w in validation.warnings(ts) if 'face selection' in w]


assert not selection_warnings(), selection_warnings()
subdivide = body.modifiers.new('Subdivision', 'SUBSURF')
expected = 'Rust: face selection cannot be mapped through Body.Subdivision (face count changed)'
assert selection_warnings() == [expected], selection_warnings()
assert mesh_masks.warnings(ts) == [expected], mesh_masks.warnings(ts)
assert not references.warnings(ts), 'the structural warnings do not evaluate geometry'
bpy.ops.open_painter.texture_set(action='VALIDATE')
assert expected in scene.open_painter.status, scene.open_painter.status
assert coverage_of(ts, mask_of(), body, n) == 0, 'the unmappable selection still reads as empty'
mask_of().mesh.kind = 'UV_ISLAND'; mask_of().mesh.object = body; mask_of().mesh.island = -1
assert selection_warnings() == [expected], 'islands by selection read the selection too'
mask_of().mesh.object = screw_a
assert not selection_warnings(), 'islands of another object do not read the body selection'
mask_of().mesh.object = body; mask_of().mesh.island = 0
assert not selection_warnings(), 'an island by index does not read the selection'
mask_of().mesh.kind = 'FACE_SELECTION'
subdivide.show_viewport = False
assert not selection_warnings(), 'a modifier hidden in the viewport is not in the evaluated stack'
subdivide.show_viewport = True
mask_of().enabled = False
assert not selection_warnings(), 'a disabled mask reads nothing'
mask_of().enabled = True
# A deforming modifier keeps the face count and is never named: the constructive one is.
displace = body.modifiers.new('Displace', 'DISPLACE')
body.modifiers.move(len(body.modifiers) - 1, 0)
assert selection_warnings() == [expected], selection_warnings()
body.modifiers.remove(subdivide)
assert not selection_warnings(), 'a deforming stack maps the selection'
assert .1 < coverage_of(ts, mask_of(), body, n) < .25, 'the selection maps again'
body.modifiers.remove(displace)
assert not selection_warnings()
# In Edit Mode the face-count check reads the edit BMesh count alone: no flush (the
# scheduler would re-hash the mesh every tick) and no per-face flags (a Python walk over
# every BMesh face on each panel redraw). The count is the live one: an unflushed edit
# leaves ``mesh.polygons`` behind, and comparing that would blame a stack that only deforms.
subdivide = body.modifiers.new('Subdivision', 'SUBSURF')
bpy.ops.object.mode_set(mode='EDIT')
bpy.context.view_layer.update()
epochs = (dependencies.mesh_epoch(body.data), dependencies.epoch())
materialised = mesh_masks._selection_flags


def no_flags(obj):
    raise AssertionError('warnings materialised the selection flags')


mesh_masks._selection_flags = no_flags
try:
    assert selection_warnings() == [expected], selection_warnings()
    bpy.context.view_layer.update()
    assert (dependencies.mesh_epoch(body.data), dependencies.epoch()) == epochs, 'warnings flushed the edit mesh'
    subdivide.show_in_editmode = False              # (a modifier toggle is a geometry update itself)
    assert not selection_warnings(), 'a modifier hidden in Edit Mode is not in the evaluated stack'
finally:
    mesh_masks._selection_flags = materialised
bpy.ops.object.mode_set(mode='OBJECT')
body.modifiers.remove(subdivide)
from open_painter.geometry.snapshots import depsgraph_for
scratch_mesh = bpy.data.meshes.new('Scratch')
bm = bmesh.new()
bmesh.ops.create_cube(bm, size=1)
bm.to_mesh(scratch_mesh)
bm.free()
scratch = bpy.data.objects.new('Scratch', scratch_mesh)
scene.collection.objects.link(scratch)
bpy.context.view_layer.objects.active = scratch
scratch.select_set(True)
bpy.ops.object.mode_set(mode='EDIT')
bm = bmesh.from_edit_mesh(scratch_mesh)
bmesh.ops.poke(bm, faces=bm.faces[:])               # 6 quads become 24 triangles, unflushed
bmesh.update_edit_mesh(scratch_mesh)
bpy.context.view_layer.update()
assert len(scratch_mesh.polygons) == 6 and len(bm.faces) == 24
depsgraph = depsgraph_for(scene, (scratch,))
# A stack that only deforms evaluates, in Edit Mode, to a BMesh wrapper whose RNA arrays
# read as empty until materialised: the check must not name the deforming modifier.
scratch_displace = scratch.modifiers.new('Displace', 'DISPLACE')
bpy.context.view_layer.update()
depsgraph = depsgraph_for(scene, (scratch,))
assert len(scratch.evaluated_get(depsgraph).data.polygons) == 0, 'the wrapper reads as empty (Blender changed?)'
assert mesh_masks._unmappable_modifier(scratch, depsgraph) is None, 'an unmaterialised wrapper blamed the deforming stack'
assert len(scratch.evaluated_get(depsgraph).data.polygons) == 24, 'materialised in place'
assert len(mesh_masks._selection_flags(scratch)) == 24, 'the coverage path reads the live faces'
scratch.modifiers.remove(scratch_displace)
bpy.context.view_layer.update()
depsgraph = depsgraph_for(scene, (scratch,))
assert mesh_masks._unmappable_modifier(scratch, depsgraph) is None, 'the stale mesh count blamed an empty stack'
scratch_subdivide = scratch.modifiers.new('Subdivision', 'SUBSURF')
bpy.context.view_layer.update()
depsgraph = depsgraph_for(scene, (scratch,))
assert mesh_masks._unmappable_modifier(scratch, depsgraph) == scratch_subdivide
bpy.ops.object.mode_set(mode='OBJECT')
scratch.select_set(False)
bpy.data.objects.remove(scratch)
bpy.data.meshes.remove(scratch_mesh)
bpy.context.view_layer.objects.active = body
print('MESH_MASK_SELECTION_WARNING_OK', flush=True)

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
# The real loader: every selector field replays its callback after the names are restored.
from open_painter.smart_materials.loader import apply as load
other = sets.create(scene, 'Imported', 512)
load(other, archive)
imported = next(m for layer in other.layers for m in layer.masks if m.source == 'MESH')
assert imported.mesh.collection_name == 'Screws' and mesh_masks.resolve(imported.mesh, 'collection') == screws
# Clearing a pointer clears its name too, so the mask does not fall back to the old datablock.
imported.mesh.collection = None
assert imported.mesh.collection_name == '' and mesh_masks.resolve(imported.mesh, 'collection') is None

# ---- The evaluation view layer is the artist's, not the first (0.9.7) ------------------------
# A layout/texturing split: the props collection is excluded from the first view layer and the
# artist works in the second. The CPU reference used to evaluate view layer 0, where the prop has
# no depsgraph node, and silently read the base mesh without modifiers.
from open_painter.geometry import gn_queries, queries
from open_painter.geometry.snapshots import targets
props = bpy.data.collections.new('Props')
scene.collection.children.link(props)
prop = cube('Prop', 1.0, (.1, .1, .5, .5))
for owner in list(prop.users_collection):        # cube() links into the active collection
    owner.objects.unlink(prop)
props.objects.link(prop)
prop.location.x = 6
prop.modifiers.new('Array', 'ARRAY').count = 2   # the copy spans world x 6.5..7.5
texturing = scene.view_layers.new('Texturing')
scene.view_layers[0].layer_collection.children['Props'].exclude = True
bpy.context.window.view_layer = texturing
props_ts = sets.create(scene, 'Props', 512)
sets.assign(scene, props_ts, prop)


def evaluated_triangles():
    with targets(props_ts) as meshes:
        return len(meshes[0]['tri_indices'])


assert evaluated_triangles() == 24, 'the prop evaluates with its modifiers in the artist\'s layer'
assert len(gn_queries.combined_target(scene, [prop], {}, 1.0)[1]) == 24
with queries.acceleration(scene, [prop]) as trees:
    assert abs(trees[0].find_nearest((8., 0., 0.), 10.)[3] - .5) < 1e-4, 'influencers are evaluated too'
# The artist's layer excludes the prop but another layer evaluates it: that layer serves.
scene.view_layers[0].layer_collection.children['Props'].exclude = False
texturing.layer_collection.children['Props'].exclude = True
assert evaluated_triangles() == 24
# No layer evaluates it (excluded everywhere, or disabled in viewports): an error names the
# object rather than the base mesh standing in for the evaluated one.
scene.view_layers[0].layer_collection.children['Props'].exclude = True
for disable in (lambda: None, lambda: setattr(prop, 'hide_viewport', True)):
    disable()
    try:
        evaluated_triangles()
        raise AssertionError('an object no view layer evaluates must not fall back to its base mesh')
    except ValueError as exc:
        assert 'Prop' in str(exc) and 'view layer' in str(exc), exc
    texturing.layer_collection.children['Props'].exclude = False
prop.hide_viewport = False
assert evaluated_triangles() == 24
bpy.context.window.view_layer = scene.view_layers[0]
scene.view_layers.remove(texturing)
print('MESH_MASK_VIEW_LAYER_OK', flush=True)

# ---- Angles saved as degrees migrate to radians (0.9.7) ---------------------------------------
# The angle properties changed units in place (degrees, max 180 / 90 -> radians, max pi / pi/2).
# A file saved before that reads its raw 45 unclamped (2578 deg: every face covered), and a
# portable material or module base from then was clamped to pi on restore. Anything past a full
# turn can only be degrees; radians (never above pi) pass through every path untouched.
from open_painter.properties.mesh_selector import FORMAT_VERSION, degrees_to_radians
from open_painter.smart_materials import modules
assert degrees_to_radians({'angle': 45.0, 'softness': 15.0, 'kind': 'NORMAL_DIRECTION'}) == {
    'angle': math.radians(45), 'softness': math.radians(15), 'kind': 'NORMAL_DIRECTION'}
untouched = {'angle': 0.8, 'softness': 0.2}
assert degrees_to_radians(untouched) is untouched
# restore(): a snapshot in degrees lands as radians instead of the clamp.
mask_of().mesh.kind = 'NORMAL_DIRECTION'
stack.restore({'angle': 45.0, 'softness': 15.0}, mask_of().mesh)
assert abs(mask_of().mesh.angle - math.radians(45)) < 1e-6 and abs(mask_of().mesh.softness - math.radians(15)) < 1e-6
stack.restore({'angle': 0.8}, mask_of().mesh)
assert abs(mask_of().mesh.angle - 0.8) < 1e-6
# A portable material written with degree values applies as radians.
degrees_archive = ROOT / '.blender-test' / 'mesh-mask-material-degrees.zip'
if degrees_archive.exists():
    degrees_archive.unlink()
with zipfile.ZipFile(archive) as bundle, zipfile.ZipFile(degrees_archive, 'w') as legacy:
    for entry in bundle.infolist():
        payload = bundle.read(entry.filename)
        if entry.filename == 'material.json':
            document = json.loads(payload)
            for layer in document['layers']:
                for stored in layer.get('masks', []):
                    if stored.get('source') == 'MESH':
                        stored['mesh'].update(kind='NORMAL_DIRECTION', angle=45.0, softness=15.0)
            payload = json.dumps(document).encode()
        legacy.writestr(entry, payload)
legacy_set = sets.create(scene, 'Legacy import', 512)
load(legacy_set, degrees_archive)
legacy_mask = next(m for layer in legacy_set.layers for m in layer.masks if m.source == 'MESH')
assert abs(legacy_mask.mesh.angle - math.radians(45)) < 1e-6 and abs(legacy_mask.mesh.softness - math.radians(15)) < 1e-6
# A saved file: the raw degree values (past the RNA clamp, as the old file holds them), a
# selector already in radians, and a module base snapshot in degrees.
mask_of().mesh['angle'], mask_of().mesh['softness'] = 45.0, 15.0
assert mask_of().mesh.angle == 45.0, 'the stored value reads back unclamped (the reported symptom)'
radians_mask = layer_of().masks.add()
radians_mask.id, radians_mask.name, radians_mask.source = new_id(), 'Radians', 'MESH'
radians_mask.mesh.kind = 'NORMAL_DIRECTION'
radians_mask.mesh.angle = 0.8
radians_mask_id = radians_mask.id
modules.write_base('legacy-module', 1, [{'id': 'root', 'masks': [
    {'id': 'm', 'source': 'MESH', 'mesh': {'kind': 'NORMAL_DIRECTION', 'angle': 45.0, 'softness': 15.0}}]}])
assert scene.open_painter.format_version == FORMAT_VERSION, 'a project is stamped with the current format at its first set'
scene.open_painter.format_version = 1   # as a file saved before the stamp existed holds it
# Undo and redo relearn without the migration: the memfile they restore is never older than
# the loaded file, and a scan there would fight the step it just restored.
cache.on_undo(None)
assert mask_of().mesh.angle == 45.0 and scene.open_painter.format_version == 1, 'undo does not run the format migration'
ts_id, scene_name = ts.id, scene.name
legacy_blend = ROOT / '.blender-test' / 'mesh-mask-degrees.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(legacy_blend))
bpy.ops.wm.open_mainfile(filepath=str(legacy_blend))
scene = bpy.data.scenes[scene_name]
ts = next(t for t in scene.open_painter.texture_sets if t.id == ts_id)
body = bpy.data.objects['Body']
layer_of = lambda: next(l for l in ts.layers if l.id == rust_id)
mask_of = lambda: next(m for m in layer_of().masks if m.id == mask_id)
assert scene.open_painter.format_version == FORMAT_VERSION, 'the pass is recorded so it runs once'
assert abs(mask_of().mesh.angle - math.radians(45)) < 1e-6, mask_of().mesh.angle
assert abs(mask_of().mesh.softness - math.radians(15)) < 1e-6, mask_of().mesh.softness
reloaded_radians = next(m for m in layer_of().masks if m.id == radians_mask_id)
assert abs(reloaded_radians.mesh.angle - 0.8) < 1e-6, 'a value already in radians is not converted'
mask_of().mesh.direction = (0, 0, 1)
migrated_cover = coverage_of(ts, mask_of(), body, n)
assert .1 < migrated_cover < .25, migrated_cover     # the top face, as the defaults cover it
base_mesh = modules.read_base('legacy-module', 1)[0]['masks'][0]['mesh']
assert abs(base_mesh['angle'] - math.radians(45)) < 1e-6 and abs(base_mesh['softness'] - math.radians(15)) < 1e-6
# Once stamped, the pass leaves the file alone (a reload of a stamped file takes the same path).
mask_of().mesh['angle'] = 45.0
cache.on_load(None)
assert mask_of().mesh.angle == 45.0
mask_of().mesh.angle = math.radians(45)
print('MESH_MASK_DEGREES_MIGRATED_OK', flush=True)
print('MESH_MASKS_PASS', flush=True)
open_painter.unregister()
