"""Downloaded smart modules use native controls, recipient bakes and module updates."""
import copy
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid5
from zipfile import ZipFile

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tests/integration'))
import open_painter
import ui_fixture
from open_painter.core import layer_stack as stack, texture_set_manager as sets, image_manager as images
from open_painter.smart_materials import controls, composer, loader, modules, portable, serializer, library
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.ui import native, texture_operations, library_categories

open_painter.register()
scene = bpy.context.scene
scene.open_painter.auto_evaluate = False
ts = sets.create(scene, 'Recipient', 512)
ts.preview_backend = 'CPU'
uid = UUID('c0f420d9-ffac-47ea-b364-957cc957634f')
identity = lambda label: uuid5(uid, 'layer:' + label).hex
profile = dict(version=1, required_maps=['NORMAL', 'AO', 'CURVATURE', 'WORLD_NORMAL'], controls=[
    dict(label='Finish Roughness', kind='multiplier', mode='scale', members=[
        dict(layer='Surface', field='output_white', channel='ROUGHNESS', effect=0)]),
    dict(label='Finish Color', layer='Surface', field='color', channel='BASE_COLOR', kind='color'),
    dict(label='Anisotropy', scope='set', field='anisotropy'),
])
data = dict(format_version=4, name='Portable Test Finish', surface={'anisotropy': .35},
    module=dict(id=str(uid), version=1, root=identity('folder'), name='Portable Test Finish'), layers=[
        dict(id=identity('folder'), type='FOLDER', name='Portable Test Finish', group_mode='ISOLATED',
             master_controls=json.dumps(profile), channels=[], masks=[]),
        dict(id=identity('Bake'), type='FILL', name='Bake', parent_id=identity('folder'), masks=[],
             channels=[dict(channel='NORMAL', enabled=True, mesh_map='NORMAL', image=None)]),
        dict(id=identity('Surface'), type='FILL', name='Surface', parent_id=identity('folder'), masks=[],
             channels=[dict(channel='BASE_COLOR', enabled=True, color=[.2, .3, .4, 1]),
                       dict(channel='ROUGHNESS', enabled=True, value=.4, effects=[dict(kind='LEVELS',
                            enabled=True, output_white=1, output_black=0,
                            id=uuid5(uid, 'effect:Surface:ROUGHNESS:0').hex)])]),
    ])

def bake_normal(texture_set, name):
    image = bpy.data.images.new(name, width=32, height=32, float_buffer=True)
    image.colorspace_settings.name = 'Non-Color'
    image.generated_color = (.65, .5, .9769696, 1)
    item = next((m for m in texture_set.baked_maps if m.kind == 'NORMAL'), None)
    if item is None:
        item = texture_set.baked_maps.add(); item.kind = 'NORMAL'
    item.image = image
    return image

def find(identifier):
    return next(layer for layer in ts.layers if layer.id == identifier)

def bound(folder):
    result = {b.control.label: b for b in controls.bindings(ts, folder)}
    assert all(b.ok for b in result.values()), [(k, b.reason) for k, b in result.items()]
    return result

def set_value(binding, value):
    if binding.control.computed:
        vector = list(getattr(binding.owner, binding.field)); vector[binding.index] = value
        setattr(binding.owner, binding.field, vector)
    else:
        setattr(binding.owner, binding.field, value)

with TemporaryDirectory() as directory:
    path = Path(directory) / 'Portable.openpaintmaterial'
    with ZipFile(path, 'w') as archive:
        archive.writestr('material.json', json.dumps(data))
    assert portable.entries() == [], 'clean install contains no downloaded smart profiles'
    before = len(ts.layers), len(bpy.data.images)
    try:
        loader.apply(ts, path)
    except composer.MissingMeshMaps:
        pass
    else:
        raise AssertionError('missing maps allowed')
    assert before == (len(ts.layers), len(bpy.data.images)), 'preflight must be atomic'
    ui_fixture.bake_synthetic(ts)
    first_bake = bake_normal(ts, 'RecipientNormal')
    root_id = loader.apply(ts, path)[0].id
    folder = find(root_id)
    assert folder.module_source.startswith('library:') and folder.group_mode == 'ISOLATED'
    assert abs(ts.anisotropy - .35) < 1e-6
    normal = next(c for l in ts.layers for c in l.channels if c.mesh_map)
    assert normal.image == first_bake
    evaluator = LayerEvaluator(scene)
    evaluator.evaluate_channel(ts.id, 'NORMAL')
    np.testing.assert_allclose(images.pixels(images.output(ts, 'NORMAL').image)[0, 0, :3], [.65, .5, .9769696], atol=1e-5)
    binding = bound(folder)['Finish Roughness']
    set_value(binding, .25)
    evaluator.evaluate_channel(ts.id, 'ROUGHNESS')
    low = images.pixels(images.output(ts, 'ROUGHNESS').image)[..., 0].mean()
    set_value(binding, 1)
    evaluator.evaluate_channel(ts.id, 'ROUGHNESS')
    high = images.pixels(images.output(ts, 'ROUGHNESS').image)[..., 0].mean()
    assert high > low + .2, (low, high)
    binding.layer.name = 'Renamed by artist'
    assert bound(folder)['Finish Roughness'].layer.name == 'Renamed by artist'
    set_value(bound(folder)['Finish Roughness'], .5)
    second_bake = bake_normal(ts, 'RebakedRecipientNormal')
    assert normal.image == second_bake, 'rebake did not replace bound pointer'
    ts.active_layer = stack.index_of(ts, root_id)
    assert bpy.ops.open_painter.layer(action='DUPLICATE') == {'FINISHED'}
    folder = find(root_id)
    copied = next(l for l in ts.layers if l.module_id and l.id != root_id)
    set_value(bound(copied)['Finish Roughness'], .8)
    assert abs(bound(folder)['Finish Roughness'].value - .5) < 1e-5
    assert abs(bound(copied)['Finish Roughness'].value - .8) < 1e-5
    saved = serializer.save(ts, Path(directory) / 'saved.openpaintmaterial')
    with ZipFile(saved) as archive:
        saved_data = json.loads(archive.read('material.json'))
        assert not any(name.startswith('assets/') for name in archive.namelist()), 'donor normal was embedded'
        assert all(c.get('image') is None for l in saved_data['layers'] for c in l['channels'] if c.get('mesh_map'))
    recipient = sets.create(scene, 'Other recipient', 512)
    ui_fixture.bake_synthetic(recipient)
    other_bake = bake_normal(recipient, 'OtherNormal')
    imported = loader.apply(recipient, saved)
    assert all(c.image == other_bake for l in recipient.layers for c in l.channels if c.mesh_map)
    assert all(b.ok for l in imported for b in controls.bindings(recipient, l))
    updated = copy.deepcopy(data)
    updated['module']['version'] = 2
    updated['layers'][2]['channels'][0]['color'] = [.8, .1, .2, 1]
    next_path = Path(directory) / 'PortableV2.openpaintmaterial'
    with ZipFile(next_path, 'w') as archive:
        archive.writestr('material.json', json.dumps(updated))
    version, conflicts = modules.update(ts, find(root_id), next_path)
    assert version == 2 and not conflicts, conflicts
    after = bound(find(root_id))
    assert abs(after['Finish Roughness'].value - .5) < 1e-5, 'update lost local control edit'
    assert abs(after['Finish Color'].value[0] - .8) < 1e-5
    assert after['Finish Roughness'].layer.name == 'Renamed by artist'
    target = library.directory(bpy.context) / 'PortableTest.openpaintmaterial'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(next_path.read_bytes())
    try:
        entry = next(e for e in portable.entries() if e[0] == 'Portable Test Finish')
        assert entry in native.catalog()
        assert entry not in native.catalog(local=False)
        assert library_categories.KIND_CATEGORY[entry[1]] == 'SMART_MATERIALS'
        assert any(item[0] == 'library:' + str(target) for item in texture_operations.material_items(None, bpy.context))
        assert native.missing_requirements(bpy.context, entry) == []
        portable.refresh_installed()
        assert any(handle.get(native.ASSET_KEY) == library_categories.key(entry) for handle in native.handles())
    finally:
        target.unlink()
    portable.refresh_installed()
    assert not any(handle.asset_data and handle.get(native.ASSET_KEY) == library_categories.key(entry)
                   for handle in native.handles()), 'removed library material remains in the shelf'
    # Save/reopen preserves master bindings and the live destination-bake pointer.
    blend = Path(directory) / 'portable.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    ts = next(t for t in bpy.context.scene.open_painter.texture_sets if t.name == 'Recipient')
    assert abs(bound(find(root_id))['Finish Roughness'].value - .5) < 1e-5
    assert all(c.image.name == 'RebakedRecipientNormal' for l in ts.layers for c in l.channels if c.mesh_map)
print('PORTABLE_SMART_MATERIALS PASS: preflight, controls, bake binding/rebinding, duplicate, save/import, update, shelf, reopen', flush=True)
