"""Catalog recipes and catalog modules are module instances: deterministic ids, identical
pixels to a plain build, nested Damage with recipe overrides, updates in place."""
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'src'))
import bpy
import numpy as np
import open_painter
from open_painter.core import texture_set_manager as sets, layer_stack as stack, image_manager as images
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.references import issues
from open_painter.smart_materials import composer, modules
from open_painter.smart_materials.catalog import CATALOG, recipe, layer as recipe_layer, mask as recipe_mask, STEEL, DIRT

open_painter.register()
scene = bpy.context.scene
ts = sets.create(scene, 'Catalog modules', 512)
ts.preview_backend = 'CPU'
n = 32
u, v = np.meshgrid((np.arange(n, dtype=np.float32) + .5) / n, (np.arange(n, dtype=np.float32) + .5) / n)
for kind in ('CURVATURE', 'AO', 'POSITION', 'WORLD_NORMAL', 'THICKNESS', 'UV_BORDER'):
    entry = ts.baked_maps.add()
    entry.kind, entry.image = kind, images.create('Catalog ' + kind, n)
    data = np.ones((n, n, 4), np.float32)
    data[..., 0], data[..., 1], data[..., 2] = .1 + .8 * u, .1 + .8 * v, v
    images.write(entry.image, data, pack=False)


def render(channel='ROUGHNESS'):
    LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
    return images.pixels(images.output(ts, channel).image)[..., :3].copy()


def layer_by_id(identifier):
    return next(layer for layer in ts.layers if layer.id == identifier)


# 1. Every catalog recipe inserts as a module instance with deterministic, namespaced ids.
first_id = composer.apply(ts, 'Worn Painted Metal').id
second_id = composer.apply(ts, 'Worn Painted Metal').id
first, second = layer_by_id(first_id), layer_by_id(second_id)
assert first.module_id == composer.catalog_module_id('Worn Painted Metal') == second.module_id
assert first.module_source == 'catalog:Worn Painted Metal' and first.module_version == 1
assert first.module_instance and first.module_instance != second.module_instance
plain = lambda folder: sorted(modules.plain_id(layer.id, folder.module_instance) for layer in modules.subtree(ts, folder.id))
assert plain(first) == plain(second), 'the same recipe must build the same module ids'
assert {layer.id for layer in modules.subtree(ts, first.id)}.isdisjoint({layer.id for layer in modules.subtree(ts, second.id)})
assert modules.read_base(first.module_id, 1) is not None
assert not issues(ts)
for name in list(CATALOG)[:6]:
    folder = layer_by_id(composer.apply(ts, name).id)
    assert folder.module_id and folder.module_instance and all(
        e.id for layer in modules.subtree(ts, folder.id) for prop in layer.channels for e in prop.effects)
print('CATALOG_MODULES instances: PASS', flush=True)

# 2. A nested catalog module: the recipe's Damage instance carries the recipe's overrides as local edits.
custom = recipe('Test', 'Steel with shared damage',
                recipe_layer('Steel', STEEL, .4, 1),
                modules=[('Damage', {'Rust': {'opacity': .35}, 'Dirt': {'mask.amount': .6}})], version=1)
folder = layer_by_id(composer.apply_document(ts, custom, 'Test Steel').id)
damage = next(layer for layer in modules.subtree(ts, folder.id) if layer.module_source == 'catalog:Damage')
assert damage.parent_id == folder.id and damage.module_id == composer.catalog_module_id('Damage')
assert damage.module_instance == composer.nested_token('Test Steel', folder.module_instance, 'Damage')
rust = next(layer for layer in modules.subtree(ts, damage.id) if layer.name == 'Rust')
dirt = next(layer for layer in modules.subtree(ts, damage.id) if layer.name == 'Dirt')
assert abs(rust.opacity - .35) < 1e-6 and abs(dirt.masks[0].amount - .6) < 1e-6
assert not issues(ts)
# The outer instance's ids leave the nested instance's ids alone.
assert all(layer.id.startswith(damage.module_instance + '.') for layer in modules.subtree(ts, damage.id))
print('CATALOG_MODULES nested module with overrides: PASS', flush=True)

# 3. Updating the nested Damage instance to a bumped definition keeps the recipe overrides and user edits.
for other in ts.layers:
    other.visible = other.id in {layer.id for layer in modules.subtree(ts, folder.id)}
folder_id, damage_id, rust_id, dirt_id = folder.id, damage.id, rust.id, dirt.id
layer_by_id(dirt_id).opacity = .8                            # a user edit on the instance
before = render()
original = composer.MODULES['Damage']
bumped = dict(original, version=2, layers=[dict(spec, roughness=(.99 if spec['name'] == 'Rust' else spec['roughness']))
                                          for spec in original['layers']])
composer.MODULES['Damage'] = bumped
try:
    assert modules.update_available(ts, layer_by_id(damage_id), {})
    version, conflicts = modules.update_catalog(ts, layer_by_id(damage_id))
    assert version == 2 and conflicts == [], conflicts
    assert layer_by_id(damage_id).module_version == 2
    assert abs(layer_by_id(rust_id).opacity - .35) < 1e-6, 'the recipe override was lost'
    assert abs(layer_by_id(dirt_id).opacity - .8) < 1e-6, 'the user edit was lost'
    assert abs(stack.channel_for(layer_by_id(rust_id), 'ROUGHNESS').value - .99) < 1e-6, 'the definition change was not applied'
    assert not issues(ts)
    after = render()
    assert not np.allclose(after, before), 'the update changed nothing visible'
    # The recipe itself (outer instance) updates too, preserving its nested instance ids.
    nested_ids = {layer.id for layer in modules.subtree(ts, damage_id)}
    CATALOG['Test Steel'] = dict(custom, version=2)
    version, conflicts = modules.update_catalog(ts, layer_by_id(folder_id))
    assert version == 2 and conflicts == [], conflicts
    assert {layer.id for layer in modules.subtree(ts, layer_by_id(damage_id).id)} == nested_ids
    assert layer_by_id(damage_id).module_version == 2, 'the nested instance was replaced by the outer update'
    assert abs(layer_by_id(dirt_id).opacity - .8) < 1e-6
    np.testing.assert_allclose(render(), after, atol=1e-6)
    # A duplicated recipe instance is its own instance: a fresh token, the nested Damage token
    # derived from it, so both the copy and its nested instance update in place.
    copy_id = stack.duplicate(ts, stack.index_of(ts, folder_id)).id
    copy = layer_by_id(copy_id)
    assert copy.module_instance and copy.module_instance != layer_by_id(folder_id).module_instance
    assert copy_id == copy.module_instance + '.' + modules.plain_id(folder_id, layer_by_id(folder_id).module_instance)
    copied_damage = next(layer for layer in modules.subtree(ts, copy_id) if layer.module_source == 'catalog:Damage')
    assert copied_damage.module_instance == composer.nested_token('Test Steel', copy.module_instance, 'Damage')
    assert all(layer.id.startswith(copied_damage.module_instance + '.') for layer in modules.subtree(ts, copied_damage.id))
    assert {layer.id for layer in ts.layers if layer.id in {l.id for l in modules.subtree(ts, folder_id)}} \
        .isdisjoint({layer.id for layer in modules.subtree(ts, copy_id)})
    assert not issues(ts)
    copied_damage_id = copied_damage.id
    CATALOG['Test Steel'] = dict(custom, version=3)
    composer.MODULES['Damage'] = dict(bumped, version=3)
    version, conflicts = modules.update_catalog(ts, layer_by_id(copied_damage_id))
    assert version == 3 and conflicts == [], conflicts
    assert layer_by_id(copied_damage_id).module_version == 3
    assert abs(next(layer for layer in modules.subtree(ts, copied_damage_id) if layer.name == 'Dirt Copy').opacity - .8) < 1e-6
    assert layer_by_id(dirt_id).module_version == 0 and abs(layer_by_id(dirt_id).opacity - .8) < 1e-6, 'the original is untouched'
    assert not issues(ts)
    for layer in ts.layers:
        layer.visible = layer.visible and layer.id not in {l.id for l in modules.subtree(ts, copy_id)}
finally:
    composer.MODULES['Damage'] = original
    CATALOG.pop('Test Steel', None)
print('CATALOG_MODULES update in place: PASS', flush=True)

# 4. Pixels of a stamped recipe equal an unstamped build (ids do not affect evaluation).
for other in ts.layers:
    other.visible = False
witness_id = composer.apply(ts, 'Clean Brushed Steel').id
stamped = render('BASE_COLOR')
members = modules.subtree(ts, witness_id)
for layer in members:
    layer.module_id = ''
# Re-id the layers randomly to prove ids are inert.
from open_painter.utils.ids import new_id
mapping = {layer.id: new_id() for layer in members}
for layer in members:
    layer.id = mapping[layer.id]
    layer.parent_id = mapping.get(layer.parent_id, layer.parent_id)
    for prop in layer.channels:
        prop.layer_id = layer.id
np.testing.assert_allclose(render('BASE_COLOR'), stamped, atol=1e-6)
print('CATALOG_MODULES ids are inert: PASS', flush=True)

# 5. The Update, Update All and Replace buttons share one operator; UI clicks (undo=True here)
# refill unset properties from the previous run, so a Replace click must not make a later plain
# Update replace wholesale (dropping edits), nor an Update All click make it touch every instance.
first_id = composer.apply_document(ts, composer.MODULES['Damage'], 'Damage').id
second_id = composer.apply_document(ts, composer.MODULES['Damage'], 'Damage').id
rust_of = lambda folder_id: next(layer for layer in modules.subtree(ts, folder_id) if layer.name == 'Rust')
ts.active_layer = stack.index_of(ts, first_id)
assert bpy.ops.open_painter.module_update('EXEC_DEFAULT', True, replace=True) == {'FINISHED'}
rust_of(second_id).opacity = .2                              # an edit inside the other instance
ts.active_layer = stack.index_of(ts, second_id)
assert bpy.ops.open_painter.module_update('EXEC_DEFAULT', True) == {'FINISHED'}
assert abs(rust_of(second_id).opacity - .2) < 1e-6, 'a plain Update replaced the instance after a Replace click'
assert 'already' in scene.open_painter.status, scene.open_painter.status
assert bpy.ops.open_painter.module_update('EXEC_DEFAULT', True, all_instances=True) == {'FINISHED'}
assert scene.open_painter.status.count('already') == len(modules.instances(ts, layer_by_id(second_id).module_id)) > 1
assert bpy.ops.open_painter.module_update('EXEC_DEFAULT', True) == {'FINISHED'}
assert scene.open_painter.status.count('already') == 1, scene.open_painter.status
print('CATALOG_MODULES update buttons forget replace: PASS', flush=True)
print('CATALOG_MODULES_PASS', flush=True)
open_painter.unregister()
