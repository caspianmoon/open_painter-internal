"""Module documents: subtree save keeps ids, instances are namespaced, older formats still load,
two instances evaluate like duplicates, and outside references survive an update."""
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'src'))
import bpy
import numpy as np
import open_painter
from open_painter.core import texture_set_manager as sets, layer_stack as stack, image_manager as images
from open_painter.evaluation import anchors
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.references import issues, warnings
from open_painter.smart_materials import loader, modules, serializer
from open_painter.smart_materials.format import read
from open_painter.utils.ids import new_id

open_painter.register()
scene = bpy.context.scene
ts = sets.create(scene, 'Modules', 512)
ts.preview_backend = 'CPU'


def rough():
    LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
    return images.pixels(images.output(ts, 'ROUGHNESS').image)[..., 0].copy()


def only(layer, *channels):
    for prop in layer.channels:
        prop.enabled = prop.channel in channels


def layer_by_id(identifier):
    return next(layer for layer in ts.layers if layer.id == identifier)


# A Damage module: folder [Scratches (levels, anchor effect), Dirt (reference mask on the anchor)].
base = stack.add(ts, 'FILL', 'Base'); base_id = base.id
only(base, 'ROUGHNESS'); stack.channel_for(base, 'ROUGHNESS').value = .2
damage = stack.add(ts, 'FOLDER', 'Damage'); damage_id = damage.id
scratches = stack.add(ts, 'FILL', 'Scratches'); scratches_id = scratches.id
scratches.parent_id = damage_id
only(scratches, 'ROUGHNESS'); prop = stack.channel_for(scratches, 'ROUGHNESS'); prop.value = .9
levels = prop.effects.add(); levels.kind = 'LEVELS'; levels.output_white = .8
anchor = prop.effects.add(); anchor.kind, anchor.id, anchor.name = 'ANCHOR', new_id(), 'Scratch anchor'
anchor_id = anchor.id
dirt = stack.add(ts, 'FILL', 'Dirt'); dirt_id = dirt.id
dirt.parent_id = damage_id
only(dirt, 'ROUGHNESS'); stack.channel_for(dirt, 'ROUGHNESS').value = .4
mask = dirt.masks.add(); mask.id, mask.source, mask.reference_anchor = new_id(), 'REFERENCE', anchor_id
mask_id = mask.id
before = rough()
assert not issues(ts) and not warnings(ts)

with TemporaryDirectory() as directory:
    # 1. Save the folder as a module: ids stay the module's own, the root has no parent.
    path, block = modules.save_module(bpy.context, ts, layer_by_id(damage_id), Path(directory) / 'Damage v1.openpaintmaterial', 'Damage')
    data, assets = read(path)
    assert data['format_version'] == 4 and data['module']['id'] == block['id'] and data['module']['version'] == 1
    assert data['module']['root'] == damage_id
    assert [layer['id'] for layer in data['layers']] == [damage_id, scratches_id, dirt_id]
    assert data['layers'][0]['parent_id'] == '' and data['layers'][1]['parent_id'] == damage_id
    assert data['layers'][2]['masks'][0]['reference_anchor'] == anchor_id
    assert all(effect['id'] for layer in data['layers'] for channel in layer['channels'] for effect in channel['effects'])
    assert not any(key.startswith('module_') for layer in data['layers'] for key in layer)
    origin = layer_by_id(damage_id)
    assert origin.module_id == block['id'] and origin.module_version == 1 and origin.module_instance == ''
    assert modules.read_base(block['id'], 1) is not None
    assert layer_by_id(base_id).module_id == ''
    np.testing.assert_allclose(rough(), before, atol=1e-6)   # saving changed nothing
    print('MODULE_DOCUMENTS save: PASS', flush=True)

    # 2. Insert two instances: namespaced ids, no collisions, each evaluates like a duplicate.
    first_id = loader.apply(ts, path)[0].id
    second_id = loader.apply(ts, path)[0].id
    first, second = layer_by_id(first_id), layer_by_id(second_id)
    first_token, second_token = first.module_instance, second.module_instance
    assert first.module_id == block['id'] and first.module_version == 1 and first.module_instance
    assert first.module_instance != second.module_instance
    assert first.id == first.module_instance + '.' + damage_id and second.id == second.module_instance + '.' + damage_id
    first_ids = {layer.id for layer in modules.subtree(ts, first.id)}
    second_ids = {layer.id for layer in modules.subtree(ts, second.id)}
    assert first_ids.isdisjoint(second_ids) and len(first_ids) == 3
    first_dirt = layer_by_id(first.module_instance + '.' + dirt_id)
    assert first_dirt.masks[0].reference_anchor == first.module_instance + '.' + anchor_id
    assert not issues(ts) and not warnings(ts), (issues(ts), warnings(ts))
    assert first.module_source == 'library:' + path.name
    # Hide the originals and one instance: one instance renders exactly like the original folder.
    layer_by_id(damage_id).visible = False
    layer_by_id(second_id).visible = False
    np.testing.assert_allclose(rough(), before, atol=1e-6)
    layer_by_id(damage_id).visible = True
    layer_by_id(second_id).visible = True
    print('MODULE_DOCUMENTS instances: PASS', flush=True)

    # 3. An outside reference into an instance (by anchor id) survives an update to v2.
    reader = stack.add(ts, 'FILL', 'Reader'); reader_id = reader.id
    only(reader, 'ROUGHNESS'); stack.channel_for(reader, 'ROUGHNESS').value = 0.
    reader_mask = reader.masks.add(); reader_mask.id, reader_mask.source = new_id(), 'REFERENCE'
    reader_mask.reference_anchor = first_token + '.' + anchor_id
    assert not issues(ts) and not warnings(ts)
    with_reader = rough()
    # v2 of the module: the origin folder's Scratches get a stronger value.
    stack.channel_for(layer_by_id(scratches_id), 'ROUGHNESS').value = 1.
    path2, block2 = modules.save_module(bpy.context, ts, layer_by_id(damage_id), Path(directory) / 'Damage v2.openpaintmaterial')
    assert block2['version'] == 2 and block2['id'] == block['id'] and layer_by_id(damage_id).module_version == 2
    version, conflicts = modules.update(ts, layer_by_id(first_id), path2)
    assert version == 2 and conflicts == [], conflicts
    updated = layer_by_id(first_id)
    assert updated.module_version == 2 and updated.module_instance == first_token
    assert {layer.id for layer in modules.subtree(ts, first_id)} == first_ids, 'ids changed across the update'
    assert stack.channel_for(layer_by_id(first_token + '.' + scratches_id), 'ROUGHNESS').value == 1.
    assert layer_by_id(reader_id).masks[0].reference_anchor == first_token + '.' + anchor_id
    assert not issues(ts) and not warnings(ts), (issues(ts), warnings(ts))
    assert not np.allclose(rough(), with_reader), 'the update changed nothing visible'
    print('MODULE_DOCUMENTS outside reference survives update: PASS', flush=True)

    # 4. Whole-set saves stay format 3 and load as before; a v4 document without a module block is refused? No: accepted.
    whole = serializer.save(ts, Path(directory) / 'whole.openpaintmaterial')
    data, _ = read(whole)
    assert data['format_version'] == 3 and 'module' not in data
    for fixture in sorted((root / 'src/open_painter/assets/smart_materials').glob('*.openpaintmaterial'))[:3]:
        read(fixture)
    # A module document whose root is not a folder is refused before any mutation.
    with ZipFile(path) as archive:
        broken = json.loads(archive.read('material.json'))
    broken['module']['root'] = scratches_id
    bad = Path(directory) / 'bad.json'
    bad.write_text(json.dumps(broken))
    try:
        read(bad)
    except ValueError as error:
        assert 'root' in str(error)
    else:
        raise AssertionError('a module whose root is not a folder was accepted')
    print('MODULE_DOCUMENTS formats: PASS', flush=True)

    # 5. The library index reports the newest version per module id.
    from open_painter.smart_materials import library
    target = library.directory(bpy.context)
    target.mkdir(parents=True, exist_ok=True)
    copies = []
    for source in (path, path2):
        copy = target / ('op-module-test-' + source.name)
        copy.write_bytes(source.read_bytes())
        copies.append(copy)
    try:
        index = modules.library_index(bpy.context)
        assert index[block['id']]['version'] == 2 and index[block['id']]['path'] == copies[1]
        assert modules.update_available(ts, layer_by_id(second_id), index)
        assert not modules.update_available(ts, layer_by_id(first_id), index)
        # Detached instances never report an update.
        modules.detach(layer_by_id(second_id))
        assert not modules.update_available(ts, layer_by_id(second_id), index)
    finally:
        for copy in copies:
            copy.unlink()
    print('MODULE_DOCUMENTS library index: PASS', flush=True)
print('MODULE_DOCUMENTS_PASS', flush=True)
open_painter.unregister()
