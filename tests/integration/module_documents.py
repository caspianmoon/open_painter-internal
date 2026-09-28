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
    assert data['format_version'] == 5 and data['module']['id'] == block['id'] and data['module']['version'] == 1
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
    assert data['format_version'] == 5 and 'module' not in data
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

    # 6. Datablock pointers (a decal Empty, a mesh mask's Object) are no obstacle to saving a
    # folder as a module or to inserting it where those datablocks exist (0.9.7).
    from open_painter.painting import decals
    bpy.ops.mesh.primitive_cube_add(size=1)
    bolts = bpy.context.object; bolts.name = 'Bolts'
    deco = stack.add(ts, 'FOLDER', 'Deco'); deco_id = deco.id
    sticker = stack.add(ts, 'FILL', 'Sticker'); sticker_id = sticker.id
    sticker.parent_id = deco_id
    empty = decals.create_object(bpy.context, 'OP_Decal_Sticker')
    sticker.decal_object = empty
    empty[decals.MARKER] = sticker_id
    assert sticker.decal_object_name == 'OP_Decal_Sticker'
    bolted = stack.add(ts, 'FILL', 'Bolted'); bolted_id = bolted.id
    bolted.parent_id = deco_id
    mesh_mask = bolted.masks.add(); mesh_mask.id, mesh_mask.source = new_id(), 'MESH'
    mesh_mask.mesh.kind = 'OBJECT'; mesh_mask.mesh.object = bolts
    assert mesh_mask.mesh.object_name == 'Bolts'
    deco_path, deco_block = modules.save_module(bpy.context, ts, layer_by_id(deco_id), Path(directory) / 'Deco v1.openpaintmaterial', 'Deco')
    origin = layer_by_id(deco_id)
    assert origin.module_id == deco_block['id'] and origin.module_version == 1
    deco_base = modules.read_base(deco_block['id'], 1)
    assert deco_base is not None, 'the base snapshot was not written'
    assert next(layer for layer in deco_base if layer['id'] == sticker_id)['decal_object'] == \
        {'__datablock__': 'OBJECT', 'name': 'OP_Decal_Sticker'}
    count = len(ts.layers)
    deco_instance = loader.apply(ts, deco_path)[0]
    deco_instance_id, deco_token = deco_instance.id, deco_instance.module_instance
    assert len(ts.layers) == count + 3
    inserted_sticker = layer_by_id(deco_token + '.' + sticker_id)
    assert inserted_sticker.decal_object == empty and inserted_sticker.decal_object_name == 'OP_Decal_Sticker'
    inserted_mask = layer_by_id(deco_token + '.' + bolted_id).masks[0]
    assert inserted_mask.mesh.object is None and inserted_mask.mesh.object_name == 'Bolts'
    assert inserted_mask.mesh.resolve('object') == bolts
    # Updating the instance keeps the links: the pointer values merge as markers.
    layer_by_id(bolted_id).opacity = .5
    deco_path2, _ = modules.save_module(bpy.context, ts, layer_by_id(deco_id), Path(directory) / 'Deco v2.openpaintmaterial')
    version, conflicts = modules.update(ts, layer_by_id(deco_instance_id), deco_path2)
    assert version == 2 and conflicts == [], conflicts
    decals.sync(ts)
    inserted_sticker = layer_by_id(deco_token + '.' + sticker_id)
    assert inserted_sticker.decal_object == empty and inserted_sticker.decal_object_name == 'OP_Decal_Sticker'
    inserted_mask = layer_by_id(deco_token + '.' + bolted_id).masks[0]
    assert inserted_mask.mesh.object_name == 'Bolts' and inserted_mask.mesh.resolve('object') == bolts
    assert abs(layer_by_id(deco_token + '.' + bolted_id).opacity - .5) < 1e-6
    assert empty.name in bpy.data.objects, 'the update released an Empty a rebuilt layer still projects from'
    print('MODULE_DOCUMENTS datablock pointers: PASS', flush=True)

    # 7. Material Director ids ('md.' + hash) are plain ids: a folder holding them saves and
    # inserts as a module like any other, and two instances do not collide.
    director = stack.add(ts, 'FOLDER', 'Directed'); director_id = director.id
    made = stack.add(ts, 'FILL', 'Made'); made.parent_id = director_id
    made.id = 'md.' + new_id(); made_id = made.id
    for prop in made.channels:
        prop.layer_id = made_id
    only(made, 'ROUGHNESS')
    made_mask = made.masks.add(); made_mask.id, made_mask.source = 'md.' + new_id(), 'NOISE'
    made_effect = stack.channel_for(made, 'ROUGHNESS').effects.add()
    made_effect.kind, made_effect.id = 'LEVELS', 'md.' + new_id()
    director_path, director_block = modules.save_module(bpy.context, ts, layer_by_id(director_id), Path(directory) / 'Directed.openpaintmaterial', 'Directed')
    first_directed = loader.apply(ts, director_path)[0]
    second_directed = loader.apply(ts, director_path)[0]
    for instance in (first_directed, second_directed):
        made_copy = layer_by_id(instance.module_instance + '.' + made_id)
        assert made_copy.parent_id == instance.id
        assert made_copy.masks[0].id == instance.module_instance + '.' + made_mask.id
        assert stack.channel_for(made_copy, 'ROUGHNESS').effects[0].id == instance.module_instance + '.' + made_effect.id
    assert first_directed.module_instance != second_directed.module_instance
    assert modules.read_base(director_block['id'], 1) is not None
    assert not issues(ts), issues(ts)
    print('MODULE_DOCUMENTS director ids: PASS', flush=True)

    # 8. A legacy layer/mask reference (no anchor) pointing outside the folder is refused
    # before anything is written or stamped: the set resolves it, a module document could
    # not (0.9.7: the save succeeded and every insert or update of that version failed).
    leaky = stack.add(ts, 'FOLDER', 'Leaky'); leaky_id = leaky.id
    inside = stack.add(ts, 'FILL', 'Inside'); inside_id = inside.id
    inside.parent_id = leaky_id
    only(inside, 'ROUGHNESS')
    inside_mask = inside.masks.add(); inside_mask.id, inside_mask.source = new_id(), 'NOISE'
    inside_mask_id = inside_mask.id
    reader2 = stack.add(ts, 'FILL', 'Outside reader'); reader2_id = reader2.id
    reader2.parent_id = leaky_id
    only(reader2, 'ROUGHNESS')
    legacy = reader2.masks.add(); legacy.id, legacy.source = new_id(), 'REFERENCE'
    legacy.reference_layer, legacy.reference_channel = base_id, 'ROUGHNESS'
    assert not issues(ts), issues(ts)
    leaky_path = Path(directory) / 'Leaky.openpaintmaterial'
    try:
        modules.save_module(bpy.context, ts, layer_by_id(leaky_id), leaky_path, 'Leaky')
    except ValueError as error:
        assert 'outside Leaky' in str(error) and 'Outside reader' in str(error), error
    else:
        raise AssertionError('a module reading a layer outside the folder was saved')
    assert not leaky_path.exists() and layer_by_id(leaky_id).module_id == '', 'the folder was stamped'
    # Through a mask that the member source layer does not own: refused the same way.
    legacy = layer_by_id(reader2_id).masks[0]
    legacy.reference_layer, legacy.reference_mask = inside_id, mask_id
    try:
        modules.save_module(bpy.context, ts, layer_by_id(leaky_id), leaky_path, 'Leaky')
    except ValueError as error:
        assert 'outside Leaky' in str(error), error
    else:
        raise AssertionError('a module reading a mask outside the folder was saved')
    assert not leaky_path.exists() and layer_by_id(leaky_id).module_id == ''
    # A reference within the folder saves and inserts as before.
    layer_by_id(reader2_id).masks[0].reference_mask = inside_mask_id
    leaky_path, leaky_block = modules.save_module(bpy.context, ts, layer_by_id(leaky_id), leaky_path, 'Leaky')
    read(leaky_path)
    assert layer_by_id(leaky_id).module_id == leaky_block['id']
    token = loader.apply(ts, leaky_path)[0].module_instance
    inserted_legacy = layer_by_id(token + '.' + reader2_id).masks[0]
    assert (inserted_legacy.reference_layer, inserted_legacy.reference_mask) == (token + '.' + inside_id, token + '.' + inside_mask_id)
    assert not issues(ts) and not warnings(ts), (issues(ts), warnings(ts))
    print('MODULE_DOCUMENTS legacy references: PASS', flush=True)

    # 8. A module's id space is its own: a folder holding another instance (live or detached)
    # is refused as a module, and a document that nests one is refused on insert, both before
    # anything changes (0.9.7). Inserting such a document used to collide with the original.
    weathering = stack.add(ts, 'FOLDER', 'Weathering'); weathering_id = weathering.id
    nested_id = loader.apply(ts, path2, parent_id=weathering_id)[0].id
    assert layer_by_id(nested_id).parent_id == weathering_id
    for detached in (False, True):
        if detached:
            modules.detach(layer_by_id(nested_id))
        try:
            modules.save_module(bpy.context, ts, layer_by_id(weathering_id), Path(directory) / 'Weathering.openpaintmaterial', 'Weathering')
        except ValueError as error:
            assert 'module instance inside' in str(error), error
        else:
            raise AssertionError('a folder nesting a module instance was saved as a module')
    assert not (Path(directory) / 'Weathering.openpaintmaterial').exists()
    assert layer_by_id(weathering_id).module_id == ''
    with ZipFile(path) as archive:
        nesting = json.loads(archive.read('material.json'))
    nesting['layers'].append({'id': 'abcd1234.' + new_id(), 'type': 'FILL', 'name': 'Nested',
                              'parent_id': damage_id, 'masks': [], 'channels': []})
    nesting_path = Path(directory) / 'nesting.json'
    nesting_path.write_text(json.dumps(nesting))
    before_ids = [layer.id for layer in ts.layers]
    try:
        loader.apply(ts, nesting_path)
    except ValueError as error:
        assert 'nests another module instance' in str(error), error
    else:
        raise AssertionError('a module document with namespaced ids was inserted')
    assert [layer.id for layer in ts.layers] == before_ids
    print('MODULE_DOCUMENTS nested instances refused: PASS', flush=True)

    # 9. A whole-set document imported elsewhere keeps its instances updatable: each lands
    # under a fresh token with its ids namespaced (like Duplicate), so Update and Replace merge
    # it instead of committing an unstamped folder and failing afterwards. A stamped folder
    # whose ids are not under its token (a copy made before 0.9.7) is refused untouched.
    whole = serializer.save(ts, Path(directory) / 'whole2.openpaintmaterial')
    other = sets.create(scene, 'Imported set', 512)
    other.preview_backend = 'CPU'
    loader.apply(other, whole)
    imported = {layer.module_instance: layer for layer in other.layers
                if layer.module_id == block['id'] and not layer.module_detached}
    assert len(imported) == 2 and not any(token in {'', first_token} for token in imported), list(imported)
    for token, root in imported.items():
        assert root.id == token + '.' + damage_id, (root.id, token)
        assert root.module_id == block['id'] and root.module_version == 2
        assert {layer.id for layer in modules.subtree(other, root.id)} == {token + '.' + i for i in (damage_id, scratches_id, dirt_id)}
    imported_reader = next(layer for layer in other.layers if layer.name == 'Reader')
    assert imported_reader.masks[0].reference_anchor in {token + '.' + anchor_id for token in imported}
    assert not issues(other) and not warnings(other), (issues(other), warnings(other))
    imported_root_id, imported_token = next((root.id, token) for token, root in imported.items()
                                            if imported_reader.masks[0].reference_anchor.startswith(token + '.'))
    imported_ids = [layer.id for layer in other.layers]
    for replace in (False, True):
        version, conflicts = modules.update(other, next(l for l in other.layers if l.id == imported_root_id), path2, replace=replace)
        assert version == 2 and conflicts == [], conflicts
        assert sorted(layer.id for layer in other.layers) == sorted(imported_ids), 'the update rewrote the imported instance ids'
        updated = next(l for l in other.layers if l.id == imported_root_id)
        assert updated.module_id == block['id'] and updated.module_instance == imported_token
        assert not issues(other) and not warnings(other), (issues(other), warnings(other))
    unstamped = stack.add(other, 'FOLDER', 'Old copy'); unstamped_id = unstamped.id
    unstamped.module_id, unstamped.module_version, unstamped.module_instance = block['id'], 2, 'deadbeef'
    unstamped_ids = [layer.id for layer in other.layers]
    try:
        modules.update(other, next(l for l in other.layers if l.id == unstamped_id), path2)
    except ValueError as error:
        assert 'not namespaced' in str(error), error
    else:
        raise AssertionError('an instance whose ids are not under its token was updated')
    assert [layer.id for layer in other.layers] == unstamped_ids
    print('MODULE_DOCUMENTS whole-set import keeps instances: PASS', flush=True)
print('MODULE_DOCUMENTS_PASS', flush=True)
open_painter.unregister()
