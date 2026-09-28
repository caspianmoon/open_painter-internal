"""Two-tile authoring acceptance: real pixels, source preservation and rollback."""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import udim, texture_set_manager as sets, layer_stack as stack, image_manager as images
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.geometry.snapshots import targets
from open_painter.export import exporter, plan
from open_painter.baking import bake_manager, diagnostics
from open_painter.painting import paint_session

open_painter.register()
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
project = scene.open_painter
checks = []


def check(label):
    checks.append(label)
    print('UDIM PASS:', label, flush=True)


def fixture(name='Two tiles', offset=0):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(-1, 0, 0), (0, 0, 0), (1, 0, 0), (-1, 1, 0), (0, 1, 0), (1, 1, 0)],
                     [], [(0, 1, 4, 3), (1, 2, 5, 4)])
    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)
    uv = mesh.uv_layers.new(name='UVMap')
    values = np.array([(0, 0), (1, 0), (1, 1), (0, 1), (1, 0), (2, 0), (2, 1), (1, 1)], np.float32)
    uv.data.foreach_set('uv', values.ravel())
    material = bpy.data.materials.new(name + ' original')
    mesh.materials.append(material)
    obj.location.x = offset
    return obj, values, material


obj, original_uv, original_material = fixture()
bpy.context.view_layer.objects.active = obj
obj.select_set(True)
created = udim.create(scene, [obj], 'Robot', 512)
for item in created:
    item.bake.device = 'CPU'  # deterministic bake acceptance; GPU policy is tested separately
ids = [ts.id for ts in created]
assert [ts.udim_tile for ts in created] == [1001, 1002]
assert [p.material_index for p in obj.data.polygons] == [1, 2]
assert obj.data.materials[0] == original_material
for ts in created:
    with targets(ts) as meshes:
        assert len(meshes) == 1
        uv = meshes[0]['uv'][meshes[0]['tri_loops']]
        assert uv.min() == 0 and uv.max() == 1
        assert len(meshes[0]['tri_indices']) == 2
    assert not [issue for issue in diagnostics.inspect(ts, evaluated=True) if 'outside' in issue.message]
source = np.empty((8, 2), np.float32)
obj.data.uv_layers['UVMap'].data.foreach_get('uv', source.ravel())
assert np.array_equal(source, original_uv)
check('tile discovery and local evaluated UVs preserve source UVs and original material')
from open_painter.core.shared_stacks import make_independent
assert created[0].shared_stack_id == created[1].shared_stack_id and created[0].shared_stack_id
make_independent(created[0])
assert not any(ts.shared_stack_id for ts in created)
check('new groups share recipes and Make Independent preserves tile authoring')

for index, ts in enumerate(created):
    layer = stack.add(ts, 'FILL', 'Independent color')
    color = (0.8, 0.05, 0.02, 1) if index == 0 else (0.02, 0.05, 0.8, 1)
    stack.channel_for(layer, 'BASE_COLOR').color = color
    stack.channel_for(layer, 'ROUGHNESS').value = .2 if index == 0 else .8
    stack.invalidate(ts)
    LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
    output = images.pixels(images.output(ts, 'BASE_COLOR').image)
    np.testing.assert_allclose(output[256, 256], color, atol=1e-5)
check('tile 1001 and 1002 evaluate independent PBR pixels')

# Mesh-map baking actually fills the second tile's texture rather than a blank first tile.
second = created[1]
second.bake.selected_to_active = False
second.bake.normal = False
second.bake.ao = False
second.bake.curvature = False
second.bake.world_normal = True
list(bake_manager.bake_steps(bpy.context, second))
world = next(m.image for m in second.baked_maps if m.kind == 'WORLD_NORMAL')
np.testing.assert_allclose(images.pixels(world)[256, 256, :3], (.5, .5, 1), atol=1e-5)
check('utility baking reaches tile 1002')

# Real Cycles target staging must translate only its owned evaluated mesh copy.
second.bake.world_normal = False
second.bake.normal = True
second.bake.samples = 1
list(bake_manager.bake_steps(bpy.context, second))
normal = next(m.image for m in second.baked_maps if m.kind == 'NORMAL')
np.testing.assert_allclose(images.pixels(normal)[256, 256, :3], (.5, .5, 1), atol=.01)
obj.data.uv_layers['UVMap'].data.foreach_get('uv', source.ravel())
assert np.array_equal(source, original_uv)
check('Cycles normal bake on tile 1002 leaves source UVs intact')

with tempfile.TemporaryDirectory(prefix='op-udim-acceptance-') as directory:
    for ts in created:
        ts.export.directory = directory
        ts.export.preset = 'BLENDER'
    resolved = plan.resolve(bpy.context, contents='TEXTURES', scope='ALL', directory=directory)
    assert not resolved.problems, resolved.problems
    names = [p.name for p in resolved.files]
    assert any('.1001.png' in name for name in names)
    assert any('.1002.png' in name for name in names)
    assert len(set(names)) == len(names)
    written = plan.execute(bpy.context, resolved)
    assert written and all(Path(path).exists() for path in written)
    for tile, component in ((1001, 0), (1002, 2)):
        path = next(Path(path) for path in written if f'.{tile}.png' in str(path) and 'BaseColor' in str(path))
        image = bpy.data.images.load(str(path), check_existing=False)
        pixel = images.pixels(image)[256, 256, :3]
        assert pixel[component] > max(np.delete(pixel, component)) * 3, pixel
        bpy.data.images.remove(image)
    check('planned and published tile-numbered exports contain distinct pixels')
    paint = stack.add(created[1], 'PAINT', 'Session survives reload')
    created[1].active_layer = stack.index_of(created[1], paint.id)
    project.active_texture_set = 1
    paint_session.begin(bpy.context, created[1], 'BASE_COLOR')
    assert project.paint_original_uv == 'UVMap'
    filepath = str(Path(directory) / 'udim.blend')
    bpy.ops.wm.save_as_mainfile(filepath=filepath)
    bpy.ops.wm.open_mainfile(filepath=filepath)
    scene, project = bpy.context.scene, bpy.context.scene.open_painter
    obj = bpy.data.objects['Two tiles']
    paint_session.finish(bpy.context, evaluate=False)
    assert obj.data.uv_layers.active.name == 'UVMap'
    assert not project.paint_original_uv
    created = [sets._set_by_id(project, identifier) for identifier in ids]
    assert [ts.udim_tile for ts in created] == [1001, 1002]
    assert created[0].udim_group == created[1].udim_group
    for index, ts in enumerate(created):
        # The engine renders in the tile's own texture space; the material's texture set
        # node reads the named source map translated by the tile origin.
        from open_painter.core.texture_set_node import TREE_PREFIX
        from open_painter.geometry.udim import tile_origin
        tree = bpy.data.node_groups[TREE_PREFIX + ts.id]
        roles = {node.get('role'): node for node in tree.nodes}
        assert roles['UDIM Source UV'].uv_map == ts.udim_source_uv
        assert tuple(roles['UDIM Local UV'].inputs[1].default_value)[:2] == tuple(tile_origin(ts.udim_tile))
        LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
        pixel = images.pixels(images.output(ts, 'BASE_COLOR').image)[256, 256, :3]
        assert np.argmax(pixel) == (0 if index == 0 else 2)
    check('save and reopen preserve tile identity, layers and packed images')
    check('finishing a restored paint session restores the original active UV map')

original_material = bpy.data.materials['Two tiles original']
index = next(i for i, ts in enumerate(project.texture_sets) if ts.id == ids[1])
sets.remove(scene, index)
assert [p.material_index for p in obj.data.polygons] == [1, 0]
assert obj.data.materials[0] == original_material
obj.data.uv_layers['UVMap'].data.foreach_get('uv', source.ravel())
assert np.array_equal(source, original_uv)
check('removing one tile restores only its original material assignment')

bad, _, _ = fixture('Invalid', 5)
bad.data.uv_layers.active.data[0].uv.x = -.1
before = len(project.texture_sets), len(bpy.data.materials)
try:
    udim.create(scene, [bad], 'Invalid', 512)
    raise AssertionError('Cross-tile face was accepted')
except ValueError:
    pass
assert before == (len(project.texture_sets), len(bpy.data.materials))
assert [p.material_index for p in bad.data.polygons] == [0, 0]
check('invalid UV layouts are rejected before mutation')

rollback, uv_before, material_before = fixture('Rollback', 10)
before = len(project.texture_sets), len(rollback.material_slots)
real_assign = sets.assign
calls = 0


def failing_assign(*args, **kwargs):
    global calls
    calls += 1
    if calls == 2:
        raise RuntimeError('injected assignment failure')
    return real_assign(*args, **kwargs)


with patch.object(sets, 'assign', failing_assign):
    try:
        udim.create(scene, [rollback], 'Rollback', 512)
        raise AssertionError('Injected failure was swallowed')
    except RuntimeError as exc:
        assert 'injected' in str(exc)
assert before == (len(project.texture_sets), len(rollback.material_slots))
assert [p.material_index for p in rollback.data.polygons] == [0, 0]
assert rollback.data.materials[0] == material_before
check('failed creation rolls back sets, material slots and face routing')

# A tile in another row and two objects with different original material slots.
group_a, _, _ = fixture('Group A', 20)
group_b, _, _ = fixture('Group B', 23)
group_a.data.materials.append(bpy.data.materials.new('Second original'))
group_a.data.polygons[1].material_index = 1
for mesh in (group_a.data, group_b.data):
    for loop in mesh.polygons[1].loop_indices:
        uv = mesh.uv_layers.active.data[loop].uv
        mesh.uv_layers.active.data[loop].uv = (uv.x - 1, uv.y + 1)
group = udim.create(scene, [group_a, group_b], 'Sparse', 512)
assert [ts.udim_tile for ts in group] == [1001, 1011]
assert all(len(ts.assignments) == 2 for ts in group)
for ts in group:
    with targets(ts) as meshes:
        assert len(meshes) == 2
        assert all(0 <= entry['uv'][entry['tri_loops']].min() <= entry['uv'][entry['tri_loops']].max() <= 1 for entry in meshes)
check('sparse second-row tiles group multiple objects independently of material names')

output = ROOT / '.blender-test/udim-workflow.json'
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps({'blender': bpy.app.version_string, 'checks': checks}, indent=2))
open_painter.unregister()
print('UDIM_WORKFLOW_PASS', len(checks), flush=True)
