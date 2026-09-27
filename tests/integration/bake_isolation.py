"""Real Cycles projection, AO isolation and exception-safe staging regressions."""
import sys
from pathlib import Path
from unittest.mock import patch

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.baking import cycles_baker
from open_painter.core import image_manager as images, texture_set_manager as sets


def plane(name, z, slope=0.0, uv_min=0.05, uv_max=0.45, reverse=False):
    mesh = bpy.data.meshes.new(name)
    vertices = [(x, y, z + slope * y) for x, y in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    mesh.from_pydata(vertices, [], [(3, 2, 1, 0) if reverse else (0, 1, 2, 3)])
    uv = mesh.uv_layers.new(name='Bake UV')
    for loop in mesh.loops:
        x, y, _ = vertices[loop.vertex_index]
        uv.data[loop.index].uv = (uv_min + (x + 1) * (uv_max - uv_min) / 2, 0.1 + (y + 1) * 0.4)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def texture_set(name, low):
    ts = sets.create(bpy.context.scene, name, 512)
    for obj in low:
        sets.assign(bpy.context.scene, ts, obj)
    ts.bake.samples = 8
    ts.bake.device = 'CPU'   # the pixel regressions here are CPU Cycles; the policy block below checks devices
    ts.bake.margin = 0
    ts.bake.ray_distance = 1.0
    ts.bake.cage_mode = 'AUTO'
    return ts


def bake_pixels(ts, kind):
    bpy.context.view_layer.update()
    result = cycles_baker.bake(bpy.context, ts, kind)
    try:
        pixels = images.pixels(result).copy()
        assert pixels.shape == (512, 512, 4) and np.isfinite(pixels).all()
        return pixels
    finally:
        bpy.data.images.remove(result)


def left(pixels):
    # Interior samples avoid UV island margins and boundary ray intersections.
    return pixels[180:330, 65:185, :3]


def resources():
    return {name: frozenset(x.as_pointer() for x in getattr(bpy.data, name))
            for name in ('objects', 'meshes', 'materials', 'images', 'scenes', 'collections')}


def state(scene, objects):
    bake = scene.render.bake
    bake_attributes = ('use_selected_to_active', 'use_clear', 'margin', 'normal_space',
                       'normal_r', 'normal_g', 'normal_b', 'target', 'use_cage',
                       'cage_extrusion', 'max_ray_distance', 'cage_object')
    material_ids = {slot.material for obj in objects for slot in obj.material_slots if slot.material}
    return {
        'render': (scene.render.engine, scene.cycles.samples, scene.cycles.device,
                   tuple((key, getattr(bake, key)) for key in bake_attributes)),
        'selection': (bpy.context.view_layer.objects.active,
                      frozenset(bpy.context.selected_objects), bpy.context.mode),
        'objects': tuple((obj, obj.data, tuple(tuple(row) for row in obj.matrix_world),
                          obj.hide_get(), obj.hide_render, obj.hide_viewport, obj.hide_select,
                          tuple((slot.link, slot.material) for slot in obj.material_slots),
                          tuple(tuple(v.co) for v in obj.data.vertices),
                          tuple(tuple(item.uv) for item in obj.data.uv_layers.active.data))
                         for obj in objects),
        'nodes': {mat.as_pointer(): (
            mat.use_nodes, mat.node_tree.nodes.active,
            tuple((n.as_pointer(), n.name, n.select, getattr(n, 'image', None)) for n in mat.node_tree.nodes),
            tuple((link.from_socket.as_pointer(), link.to_socket.as_pointer()) for link in mat.node_tree.links))
            for mat in material_ids if mat.use_nodes},
    }


open_painter.register()
scene = bpy.context.scene
for obj in list(scene.objects):
    bpy.data.objects.remove(obj, do_unlink=True)

# The upper high-poly surface is encountered first by rays from both targets.
# Pair isolation must keep its opposite slope off the lower target's normal map.
low_a = plane('Panel_low', 0.0)
low_b = plane('Cap_low', 0.35, uv_min=0.55, uv_max=0.95)
high_a = plane('Panel_high', 0.10, slope=-0.045)
high_b = plane('Cap_high.001', 0.45, slope=0.045)
ts = texture_set('Paired normals', [low_a, low_b])
ts.bake.selected_to_active = True
ref_a = ts.bake.high_objects.add()
ref_a.object = high_a
ref_b = ts.bake.high_objects.add()
ref_b.object = high_b

# One-target/one-source control does not require any pairing implementation.
ts.assignments.remove(1)
ts.bake.high_objects.remove(1)
control = bake_pixels(ts, 'NORMAL')
assert float(left(control)[..., 1].mean()) > 0.51, 'Control did not capture the tilted high surface'
sets.assign(scene, ts, low_b)
ref_b = ts.bake.high_objects.add()
ref_b.object = high_b
ts.bake.source_mode = 'ALL'
unpaired = bake_pixels(ts, 'NORMAL')
assert float(np.abs(left(unpaired) - left(control)).max()) > 0.025, 'Fixture did not reproduce cross projection'
ts.bake.source_mode = 'MATCHED'
matched = bake_pixels(ts, 'NORMAL')
np.testing.assert_allclose(left(matched), left(control), atol=3e-5, rtol=0)

# Opt-in 2x supersampling bakes a 1024 working image with doubled margin and
# box-filters it to the authored 512; normals stay unit length and the result
# agrees with the 1x bake away from edges. Off remains byte-identical to before.
from open_painter.baking.cycles_baker import supersample_preflight, downsample
ts.bake.supersample = '2'
supersampled = bake_pixels(ts, 'NORMAL')
ts.bake.supersample = '1'
assert supersampled.shape == (512, 512, 4)
vectors = left(supersampled) * 2 - 1  # left() already selects interior RGB samples
np.testing.assert_allclose(np.linalg.norm(vectors, axis=-1), 1, atol=2e-3)
assert float(np.abs(left(supersampled) - left(matched)).mean()) < 0.01
try:
    supersample_preflight(8192, 2)
    raise AssertionError('8K supersampling was not refused')
except ValueError as exc:
    assert 'GiB' in str(exc)
assert supersample_preflight(2048, 2) == 4096 and supersample_preflight(4096, 1) == 4096
tilted = np.zeros((4, 4, 4), np.float32); tilted[..., :3] = (.5, .5, 1); tilted[..., 3] = 1
tilted[0, 0, :3] = (1, .5, .5)  # one supersample leaning +X; the box filter renormalizes
reduced = downsample(tilted, 2, 'NORMAL')
assert reduced.shape == (2, 2, 4) and np.isclose(np.linalg.norm(reduced[0, 0, :3] * 2 - 1), 1, atol=1e-6)
assert reduced[0, 0, 0] > .5 and np.allclose(reduced[1, 1, :3], (.5, .5, 1))

# Explicit pointer pairing must work independently of naming conventions.
high_a.name = 'Detailed source without suffix'
high_b.name = 'Another arbitrary source'
ts.bake.high_objects[0].target = low_a
ts.bake.high_objects[1].target = low_b
explicit = bake_pixels(ts, 'NORMAL')
np.testing.assert_allclose(explicit, matched, atol=3e-5, rtol=0)

# Hostile artist settings must not redirect our output to vertex colors or flip Y.
# Keep a real active material node, OBJECT material overrides, hidden source and
# Edit Mode selected target. Copied OBJECT slots must not redirect the staging
# image target back into the artist's active texture node.
texture = ts.material.node_tree.nodes.new('ShaderNodeTexImage')
texture.name = 'Artist active image node'
texture.image = images.create('Artist untouched image', 512, (0.13, 0.27, 0.41, 1))
ts.material.node_tree.nodes.active = texture
artist_pixels = images.pixels(texture.image).copy()
artist_material = ts.material.copy()
artist_material.name = 'Artist object-linked material'
artist_material.node_tree.nodes.active = artist_material.node_tree.nodes[texture.name]
assert artist_material.node_tree.nodes.active.image == texture.image
for obj in (low_a, low_b):
    obj.material_slots[0].link = 'OBJECT'
    obj.material_slots[0].material = artist_material
    assert obj.data.materials[0] == ts.material
high_b.hide_render = True
high_b.hide_set(True)
high_b.hide_select = True
scene.render.bake.normal_g = 'NEG_Y'
scene.render.bake.target = 'VERTEX_COLORS'
scene.render.bake.margin = 7
scene.render.bake.use_clear = False
scene.cycles.samples = 3
for obj in bpy.context.selected_objects:
    obj.select_set(False)
low_a.select_set(True)
bpy.context.view_layer.objects.active = low_a
bpy.ops.object.mode_set(mode='EDIT')
tracked = [low_a, low_b, high_a, high_b]
before = state(scene, tracked)
hostile = bake_pixels(ts, 'NORMAL')
assert state(scene, tracked) == before, 'Bake modified author scene, material or object state'
assert all(obj.data.materials[0] == ts.material for obj in (low_a, low_b))
np.testing.assert_allclose(hostile, matched, atol=3e-5, rtol=0)
np.testing.assert_array_equal(images.pixels(texture.image), artist_pixels)

# Failure after one completed target must release every temporary datablock and
# preserve the same author state, including Edit Mode and active image node.
before_resources = resources()
real_bake = cycles_baker._run_bake
calls = []


def fail_second(**kwargs):
    calls.append(kwargs)
    if len(calls) == 2:
        raise RuntimeError('Injected second-target bake failure')
    return real_bake(**kwargs)


with patch.object(cycles_baker, '_run_bake', side_effect=fail_second):
    try:
        cycles_baker.bake(bpy.context, ts, 'NORMAL')
    except RuntimeError as exc:
        assert 'Injected second-target' in str(exc)
    else:
        raise AssertionError('Injected bake failure was swallowed')
assert len(calls) == 2
assert resources() == before_resources, 'Temporary bake datablocks leaked on failure'
assert state(scene, tracked) == before, 'Failed bake modified author state'
np.testing.assert_array_equal(images.pixels(texture.image), artist_pixels)
bpy.ops.object.mode_set(mode='OBJECT')

# Device policy belongs to the staging scene, never the artist's preferences.
# Observe the configured device at the backend boundary without requiring a GPU
# on the test machine; the earlier cases exercise actual CPU Cycles rendering.
from open_painter.baking import devices
has_gpu = devices.gpu_available()
preferences = bpy.context.preferences.addons['cycles'].preferences
device_preferences = (preferences.compute_device_type,
                      tuple((item.id, item.use) for item in preferences.devices))
scene.cycles.device = 'GPU'
for policy, expected, fell_back in (('CPU', 'CPU', False),
                                    ('SCENE', 'GPU' if has_gpu else 'CPU', not has_gpu),
                                    ('AUTO', 'GPU' if has_gpu else 'CPU', False)):
    ts.bake.device = policy
    before_device_state = state(scene, tracked)
    before_device_resources = resources()

    def observe_device(**kwargs):
        assert bpy.context.scene != scene
        assert bpy.context.scene.cycles.device == expected
        raise RuntimeError('Device policy observed')

    with patch.object(cycles_baker, '_run_bake', side_effect=observe_device):
        try:
            cycles_baker.bake(bpy.context, ts, 'NORMAL')
        except RuntimeError as exc:
            assert 'Device policy observed' in str(exc)
        else:
            raise AssertionError('Device observation did not reach the backend')
    assert state(scene, tracked) == before_device_state
    assert resources() == before_device_resources
    assert bool(devices.resolve(scene, policy)[1]) == fell_back
# A scene device that is already CPU is followed without a fallback note, while
# Auto still prefers a GPU when one is enabled.
scene.cycles.device = 'CPU'
assert devices.resolve(scene, 'SCENE') == ('CPU', None)
assert devices.resolve(scene, 'AUTO')[0] == ('GPU' if has_gpu else 'CPU')
assert device_preferences == (preferences.compute_device_type,
                              tuple((item.id, item.use) for item in preferences.devices))
ts.bake.device = 'CPU'

# Bake geometry follows render modifiers even when the viewport is coarse or a
# modifier is disabled there. A peaked control grid changes shape under Subsurf,
# so this catches freezing the ordinary viewport depsgraph by mistake.
render_low = plane('Render_low', 6.0)
render_ts = texture_set('Render modifier evaluation', [render_low])
render_mesh = bpy.data.meshes.new('Render detail grid')
grid = [(x, y, 6.15 + (0.4 if x == 0 and y == 0 else 0))
        for y in (-1, 0, 1) for x in (-1, 0, 1)]
faces = [(y * 3 + x, y * 3 + x + 1, (y + 1) * 3 + x + 1, (y + 1) * 3 + x)
         for y in range(2) for x in range(2)]
render_mesh.from_pydata(grid, [], faces)
render_high = bpy.data.objects.new('Render_high', render_mesh)
scene.collection.objects.link(render_high)
subdivision = render_high.modifiers.new('Render subdivision', 'SUBSURF')
subdivision.levels = subdivision.render_levels = 2
render_ts.bake.high_objects.add().object = render_high
render_ts.bake.selected_to_active = True
render_reference = bake_pixels(render_ts, 'NORMAL')
assert float(np.ptp(left(render_reference)[..., 1])) > 0.03, 'Render modifier fixture lacks normal variation'
subdivision.levels = 0
bpy.context.view_layer.update()
render_coarse = bake_pixels(render_ts, 'NORMAL')
assert subdivision.levels == 0 and subdivision.render_levels == 2
np.testing.assert_allclose(render_coarse, render_reference, atol=3e-5, rtol=0)
subdivision.levels = 2
subdivision.show_viewport = False
bpy.context.view_layer.update()
render_hidden = bake_pixels(render_ts, 'NORMAL')
assert not subdivision.show_viewport and subdivision.show_render
np.testing.assert_allclose(render_hidden, render_reference, atol=3e-5, rtol=0)

# Base meshes and viewport meshes match, but only the target subdivides for
# render. Cage preflight must reject the rendered topology before calling Cycles.
render_cage = bpy.data.objects.new('Render topology cage', render_low.data.copy())
scene.collection.objects.link(render_cage)
render_cage.location.z = 0.8
low_subdivision = render_low.modifiers.new('Target render subdivision', 'SUBSURF')
low_subdivision.levels = 0
low_subdivision.render_levels = 1
render_ts.bake.cage_mode = 'CUSTOM'
render_ts.bake.cage_object = render_cage
bpy.context.view_layer.update()
before_cage_resources = resources()
with patch.object(cycles_baker, '_run_bake', side_effect=AssertionError('Invalid render cage reached Cycles')) as backend:
    try:
        cycles_baker.bake(bpy.context, render_ts, 'NORMAL')
    except ValueError as exc:
        assert 'topology' in str(exc).lower()
    else:
        raise AssertionError('Render-only cage topology mismatch was accepted')
    assert not backend.called
assert resources() == before_cage_resources
assert low_subdivision.levels == 0 and low_subdivision.render_levels == 1

# A matching cage is checked once, on the staged copies Cycles receives: no extra
# render-evaluation scope per map (a render-subdivided target paid it three times).
from open_painter.baking import cages
render_low.modifiers.remove(low_subdivision)
bpy.context.view_layer.update()
with patch.object(cages, 'validate_topology', wraps=cages.validate_topology) as validated, \
        patch.object(cages, 'topology_signature', wraps=cages.topology_signature) as signed:
    render_caged = bake_pixels(render_ts, 'NORMAL')
assert not validated.called, 'Cycles map re-validated the cage against a fresh render evaluation'
assert signed.call_count == 2, signed.call_count
np.testing.assert_allclose(render_caged, render_reference, atol=3e-5, rtol=0)
render_ts.bake.cage_mode = 'AUTO'
render_ts.bake.cage_object = None

# Separate fixture: the roof occludes the floor strongly, but is initially an
# unrelated author object and must not affect its AO. Once assigned, SET includes
# contact between targets while PAIR deliberately removes it.
floor = plane('AO floor', 3.0)
roof = plane('AO roof', 3.15, uv_min=0.55, uv_max=0.95, reverse=True)
ao_ts = texture_set('AO scope', [floor])
ao_ts.bake.selected_to_active = False
ao_ts.bake.ao_scope = 'SET'
roof.hide_render = True
without_roof = bake_pixels(ao_ts, 'AO')
roof.hide_render = False
unrelated = bake_pixels(ao_ts, 'AO')
np.testing.assert_allclose(left(unrelated), left(without_roof), atol=1e-5, rtol=0)
assert float(left(unrelated).mean()) > 0.98, 'Unrelated author geometry contaminated isolated AO'
sets.assign(scene, ao_ts, roof)
contact = bake_pixels(ao_ts, 'AO')
assert float(left(contact).mean()) < float(left(unrelated).mean()) - 0.2, 'SET scope lost contact between targets'
ao_ts.bake.ao_scope = 'PAIR'
isolated = bake_pixels(ao_ts, 'AO')
assert float(left(isolated).mean()) > 0.98, 'PAIR scope retained other target AO'
# AO Exclusion is the PAIR pass composited where painted. A set baked from its
# lows sees the same contact under SET scope, so the stroke must work there too.
from open_painter.baking import corrections
ao_ts.bake.ao_scope = 'SET'
exclude = corrections.image_of(ao_ts, 'AO_EXCLUDE', create_missing=True)
weights = images.pixels(exclude)
weights[:, :256, :3] = 1.0   # the floor's island: the roof's, to the right, keeps its contact
images.write(exclude, weights, pack=False)
assert corrections.refresh_used(exclude) and corrections.active_keys(ao_ts) == ['AO_EXCLUDE']
excluded = bake_pixels(ao_ts, 'AO')
assert float(left(excluded).mean()) > 0.98, 'AO Exclusion was dropped for a set baked from its lows'
np.testing.assert_allclose(left(excluded), left(isolated), atol=1e-5, rtol=0)
corrections.clear(ao_ts)
assert not corrections.active_keys(ao_ts)
# AO distance is an explicit setting through an owned staging World. The roof
# sits 0.15 above the floor: a reach shorter than that gap removes its contact
# occlusion, a reach beyond it restores it, and the World never leaks.
ao_ts.bake.ao_scope = 'SET'
worlds_before = len(bpy.data.worlds)
ao_ts.bake.ao_distance = 0.05
short = bake_pixels(ao_ts, 'AO')
ao_ts.bake.ao_distance = 10.0
long = bake_pixels(ao_ts, 'AO')
assert len(bpy.data.worlds) == worlds_before, 'Owned bake World leaked'
assert float(left(short).mean()) > float(left(long).mean()) + 0.2, (float(left(short).mean()), float(left(long).mean()))
np.testing.assert_allclose(left(long), left(contact), atol=1e-5, rtol=0)  # default distance reproduces earlier bakes
from open_painter.geometry.provenance import record, status
from open_painter.baking.bake_manager import register_map
registered = cycles_baker.bake(bpy.context, ao_ts, 'AO')
register_map(ao_ts, 'AO', registered)
ao_prop = next(m for m in ao_ts.baked_maps if m.kind == 'AO')
assert status(ao_ts, ao_prop) == 'Ready'
assert 'ao_distance' not in record(ao_ts, 'AO'), 'Default distance must not change provenance of existing maps'
ao_ts.bake.ao_distance = 2.0
assert 'ao_distance' in record(ao_ts, 'AO') and status(ao_ts, ao_prop).startswith('Stale'), status(ao_ts, ao_prop)
ao_ts.bake.ao_distance = 10.0
assert status(ao_ts, ao_prop) == 'Ready'

print('BAKE_ISOLATION_STATS', {
    'unpaired_normal_max_error': float(np.abs(left(unpaired) - left(control)).max()),
    'matched_normal_max_error': float(np.abs(left(matched) - left(control)).max()),
    'unrelated_ao': float(left(unrelated).mean()),
    'set_contact_ao': float(left(contact).mean()),
    'pair_ao': float(left(isolated).mean()),
    'short_distance_ao': float(left(short).mean()),
}, flush=True)
open_painter.unregister()
print('BAKE_ISOLATION_PASS', flush=True)
