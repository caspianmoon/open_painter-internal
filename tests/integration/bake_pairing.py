"""Per-part routing, scene isolation and editable cage correspondence."""
import sys
from pathlib import Path
import bpy
from mathutils import Matrix

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import open_painter
from open_painter.core import texture_set_manager as sets
from open_painter.baking import pairing, cages


def rejects(call, text):
    try:
        call()
    except ValueError as exc:
        assert text.lower() in str(exc).lower(), str(exc)
    else:
        raise AssertionError(f"Expected rejection: {text}")


open_painter.register()
scene = bpy.context.scene
low_a = bpy.context.active_object
low_a.name = "Panel_low"
low_b = low_a.copy(); low_b.data = low_a.data.copy(); low_b.name = "Bolt_low"
scene.collection.objects.link(low_b)
low_b.location.x = 4
ts = sets.create(scene, "Paired bake", 512)
sets.assign(scene, ts, low_a); sets.assign(scene, ts, low_b)
highs = []
for name, low in (("Panel_high", low_a), ("Panel_high.001", low_a), ("Bolt_high", low_b)):
    obj = low.copy(); obj.data = low.data.copy(); obj.name = name
    scene.collection.objects.link(obj); highs.append(obj)
foreign_scene = bpy.data.scenes.new("Unrelated bake sources")
foreign = low_a.copy(); foreign.data = low_a.data.copy(); foreign.name = "Panel_high.002"
foreign_scene.collection.objects.link(foreign)
names = tuple(obj.name for obj in bpy.data.objects)
matched = pairing.match_sources(ts, scene)
assert matched[0].sources == tuple(highs[:2]) and matched[1].sources == (highs[2],)
assert all(foreign not in group.sources for group in matched)
assert tuple(obj.name for obj in bpy.data.objects) == names
assert ts.bake.source_mode == "MATCHED"

# Ignore Brush Excludes lives on the rows: a re-match keeps it per source.
next(ref for ref in ts.bake.high_objects if ref.object == highs[2]).ignorable = True
pairing.match_sources(ts, scene)
assert [(ref.object, ref.ignorable) for ref in ts.bake.high_objects] == [
    (highs[0], False), (highs[1], False), (highs[2], True)]

# Missing sources do not discard a previously valid explicit source list.
old = [(ref.object, ref.target, ref.cage, ref.ignorable) for ref in ts.bake.high_objects]
highs[2].name = "Unmatched"
rejects(lambda: pairing.match_sources(ts, scene), "No scene mesh")
assert [(ref.object, ref.target, ref.cage, ref.ignorable) for ref in ts.bake.high_objects] == old
highs[2].name = "Bolt_high"
assert len(pairing.groups(ts)[0].sources) == 2

# Name inference accepts multiple highs but never arbitrarily picks between lows.
for ref in ts.bake.high_objects:
    ref.target = None
assert pairing.groups(ts)[1].sources == (highs[2],)
low_b.name = "Panel_low.001"
rejects(lambda: pairing.groups(ts), "Ambiguous")
low_b.name = "Bolt_low"
ts.bake.high_objects[0].target = foreign
rejects(lambda: pairing.groups(ts), "not assigned")
pairing.match_sources(ts, scene)
ts.bake.high_objects[0].object = foreign
rejects(lambda: pairing.groups(ts), "not in the texture set's scene")
ts.bake.high_objects[0].object = highs[0]

# ALL remains all-to-each; low-only never projects high meshes.
ts.bake.source_mode = "ALL"
assert all(group.sources == tuple(highs) for group in pairing.groups(ts))
ts.bake.selected_to_active = False
assert all(group.sources == () for group in pairing.groups(ts))
ts.bake.high_objects[0].object = None
assert all(group.sources == () and group.cage is None for group in pairing.groups(ts))
ts.bake.high_objects[0].object = highs[0]
ts.bake.selected_to_active = True
ts.bake.source_mode = "MATCHED"

# Evaluated subdivision, nonuniform scale and transforms survive cage creation.
low_a.scale = (2, 1, 0.5)
low_a.rotation_euler.z = 0.37
modifier = low_a.modifiers.new("Evaluated detail", "SUBSURF")
modifier.levels = 2; modifier.render_levels = 2
# Build an independent expected surface, then restore a cheaper hidden viewport
# modifier. Cage creation must still use the detailed render configuration.
bpy.context.view_layer.update()
graph = bpy.context.evaluated_depsgraph_get()
evaluated = low_a.evaluated_get(graph)
source_mesh = evaluated.to_mesh()
world = evaluated.matrix_world.copy()
normal_matrix = world.to_3x3().inverted().transposed()
expected = [(world @ vertex.co) + (normal_matrix @ vertex.normal).normalized() * 0.075
            for vertex in source_mesh.vertices]
evaluated.to_mesh_clear()
modifier.levels = 1; modifier.show_viewport = False
bpy.context.view_layer.update()
source_coords = tuple(vertex.co[:] for vertex in low_a.data.vertices)
source_world = low_a.matrix_world.copy()
selected = tuple(bpy.context.selected_objects)
active = bpy.context.view_layer.objects.active
created = cages.create(bpy.context, ts, 0.075)
assert len(created) == 2 and ts.bake.cage_mode == "CUSTOM"
assert ts.bake.cage_object is None
assert tuple(vertex.co[:] for vertex in low_a.data.vertices) == source_coords
assert modifier.levels == 1 and modifier.render_levels == 2 and not modifier.show_viewport
assert low_a.matrix_world == source_world
assert tuple(bpy.context.selected_objects) == selected and bpy.context.view_layer.objects.active == active
assert created[0].matrix_world == Matrix.Identity(4)
assert all(cage.hide_render and cage.display_type == "WIRE" for cage in created)
assert len(created[0].data.vertices) == len(expected) > len(low_a.data.vertices)
assert all((vertex.co - target).length < 1e-5 for vertex, target in zip(created[0].data.vertices, expected))
assert cages.validate_topology(low_a, created[0])
assert pairing.groups(ts)[0].cage == created[0] and pairing.groups(ts)[1].cage == created[1]
ts.bake.cage_mode = "AUTO"
assert all(group.cage is None for group in pairing.groups(ts))
ts.bake.cage_mode = "CUSTOM"
for ref in ts.bake.high_objects:
    if ref.target == low_a:
        ref.cage = foreign
rejects(lambda: pairing.groups(ts), "not in the texture set's scene")
for ref in ts.bake.high_objects:
    if ref.target == low_a:
        ref.cage = created[0]

# Equal vertex counts cannot conceal incompatible ordered polygon correspondence.
wrong_mesh = bpy.data.meshes.new("Wrong polygon mapping")
vertices = [vertex.co[:] for vertex in created[1].data.vertices]
faces = [tuple(face.vertices) for face in created[1].data.polygons]
faces[0] = tuple(reversed(faces[0]))
wrong_mesh.from_pydata(vertices, [], faces)
wrong = bpy.data.objects.new("Wrong cage", wrong_mesh); scene.collection.objects.link(wrong)
bpy.context.view_layer.update()
rejects(lambda: cages.validate_topology(low_b, wrong), "topology")
# The bulk signature keeps the exact ordered-corner comparison: a rotated corner
# list, a differing corner count and a differing vertex count all differ.
def signature(vertices, polygon):
    mesh = bpy.data.meshes.new("Signature probe")
    mesh.from_pydata(vertices, [], [polygon])
    try:
        return cages.topology_signature(mesh)
    finally:
        bpy.data.meshes.remove(mesh)
square = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]
quad = signature(square, (0, 1, 2, 3))
assert quad == signature([(x + 5, y, z) for x, y, z in square], (0, 1, 2, 3))
assert quad != signature(square, (1, 2, 3, 0))
assert quad != signature(square, (0, 1, 2))
assert quad != signature(square + [(2, 2, 0)], (0, 1, 2, 3))
ts.bake.high_objects[1].cage = wrong
rejects(lambda: pairing.groups(ts), "Conflicting")
ts.bake.high_objects[1].cage = created[0]

# A failure during a later cage allocation cleans staged copies, keeps prior cages.
objects_before, meshes_before = set(bpy.data.objects), set(bpy.data.meshes)
prior = [(ref.target, ref.cage) for ref in ts.bake.high_objects]
low_b.scale.z = 0; bpy.context.view_layer.update()
rejects(lambda: cages.create(bpy.context, ts, 0.1), "singular")
assert set(bpy.data.objects) == objects_before and set(bpy.data.meshes) == meshes_before
assert [(ref.target, ref.cage) for ref in ts.bake.high_objects] == prior
low_b.scale.z = 1

# Single-target projects retain the existing cage_object representation.
single = sets.create(scene, "Single cage", 512)
other = low_a.copy(); other.data = low_a.data.copy(); other.name = "Single target"
scene.collection.objects.link(other)
sets.assign(scene, single, other)
bpy.context.view_layer.update()
single.bake.selected_to_active = False
single_cage = cages.create(bpy.context, single, 0.02)[0]
assert single.bake.cage_object == single_cage
assert cages.validate_topology(other, single_cage)
# The visible single-target cage control is authoritative even if source rows
# retain older per-part pointers. Clearing it must not secretly use an old cage.
single.bake.selected_to_active = True
ref = single.bake.high_objects.add(); ref.object = low_a; ref.cage = wrong
assert pairing.groups(single)[0].cage == single_cage
single.bake.cage_object = None
rejects(lambda: pairing.groups(single), 'missing')
single.bake.cage_object = single_cage
# Match Sources replaces the visible cage only with a found '_cage' partner: a
# created cage ('<low> Cage') stays, so the set still bakes under CUSTOM.
other.name = "Single_low"
single_high = low_a.copy(); single_high.data = low_a.data.copy(); single_high.name = "Single_high"
scene.collection.objects.link(single_high)
pairing.match_sources(single, scene)
assert single.bake.cage_mode == "CUSTOM" and single.bake.cage_object == single_cage
assert pairing.groups(single)[0].cage == single_cage
partner = low_a.copy(); partner.data = low_a.data.copy(); partner.name = "Single_cage"
scene.collection.objects.link(partner)
pairing.match_sources(single, scene)
assert single.bake.cage_object == partner and pairing.groups(single)[0].cage == partner
# Routing, cage edits and AO scope participate in map staleness; all authored
# references and new settings survive a normal .blend save/reopen.
from open_painter.baking.bake_manager import register_map
from open_painter.core import image_manager as images
from open_painter.geometry.provenance import status
from tempfile import TemporaryDirectory
ts = scene.open_painter.texture_sets[0]
register_map(ts, 'NORMAL', images.create('Pair provenance', 512, (.5,.5,1,1)))
normal = next(m for m in ts.baked_maps if m.kind == 'NORMAL')
assert status(ts, normal) == 'Ready'
ts.bake.ao_scope = 'PAIR'
assert status(ts, normal) == 'Ready'
saved_mode = ts.bake.source_mode
ts.bake.source_mode = 'ALL'
assert status(ts, normal).startswith('Stale')
ts.bake.source_mode = saved_mode
assert status(ts, normal) == 'Ready'
created[0].data.vertices[0].co.x += .01
created[0].data.update(); bpy.context.view_layer.update()
assert status(ts, normal).startswith('Stale')
expected = [(ref.object.name, ref.target.name, ref.cage.name) for ref in ts.bake.high_objects]
identifier = ts.id
ts.bake.separate_cage_distance = True
ts.bake.cage_extrusion = .27
with TemporaryDirectory(prefix='op-bake-pair-save-') as directory:
    path = directory + '/pairs.blend'
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    ts = next(t for t in bpy.context.scene.open_painter.texture_sets if t.id == identifier)
    assert [(ref.object.name, ref.target.name, ref.cage.name) for ref in ts.bake.high_objects] == expected
    assert ts.bake.ao_scope == 'PAIR' and ts.bake.source_mode == saved_mode
    assert ts.bake.separate_cage_distance and abs(ts.bake.cage_extrusion-.27)<1e-6
open_painter.unregister()
print("BAKE_PAIRING_PASS", flush=True)
