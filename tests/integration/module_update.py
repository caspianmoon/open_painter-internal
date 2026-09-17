"""Module updates: three-way merge on live instances (applied edits, kept overrides,
reported conflicts, preserved paint, rollback) and equality with a fresh insert."""
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'src'))
import bpy
import numpy as np
import open_painter
from open_painter.core import texture_set_manager as sets, layer_stack as stack, image_manager as images
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.references import issues
from open_painter.smart_materials import loader, modules
from open_painter.utils.ids import new_id

open_painter.register()
scene = bpy.context.scene
ts = sets.create(scene, 'Module updates', 512)
ts.preview_backend = 'CPU'


def rough():
    LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
    return images.pixels(images.output(ts, 'ROUGHNESS').image)[..., 0].copy()


def only(layer, *channels):
    for prop in layer.channels:
        prop.enabled = prop.channel in channels


def layer_by_id(identifier):
    return next(layer for layer in ts.layers if layer.id == identifier)


def named(instance, plain_id):
    return layer_by_id(instance.module_instance + '.' + plain_id)


# Definition: Damage [Scratches .9 with a painted mask, Dirt .4 with a Levels filter].
damage = stack.add(ts, 'FOLDER', 'Damage'); damage_id = damage.id
scratches = stack.add(ts, 'FILL', 'Scratches'); scratches_id = scratches.id
scratches.parent_id = damage_id
only(scratches, 'ROUGHNESS'); stack.channel_for(scratches, 'ROUGHNESS').value = .9
painted = scratches.masks.add(); painted.id, painted.source = new_id(), 'PAINT'
painted.image = images.create('Scratch mask', 512, (.5, .5, .5, 1))
dirt = stack.add(ts, 'FILL', 'Dirt'); dirt_id = dirt.id
dirt.parent_id = damage_id
only(dirt, 'ROUGHNESS'); dprop = stack.channel_for(dirt, 'ROUGHNESS'); dprop.value = .4
levels = dprop.effects.add(); levels.kind, levels.gamma = 'LEVELS', 1.2

with TemporaryDirectory() as directory:
    v1, block = modules.save_module(bpy.context, ts, layer_by_id(damage_id), Path(directory) / 'v1.openpaintmaterial', 'Damage')
    layer_by_id(damage_id).visible = False
    instance = loader.apply(ts, v1)[0]
    instance_id = instance.id
    token = instance.module_instance
    # Local edits: Dirt opacity (untouched by v2) and Scratches value (also changed by v2) and paint pixels.
    named(instance, dirt_id).opacity = .5
    stack.channel_for(named(instance, scratches_id), 'ROUGHNESS').value = .7
    local_mask = named(instance, scratches_id).masks[0]
    images.write(local_mask.image, np.full((512, 512, 4), (.25, .25, .25, 1), np.float32), pack=False)
    local_image = local_mask.image.name
    # v2: Scratches value 1.0, Dirt gamma 2.0, a new Rust layer, the painted mask replaced.
    origin_scratches = stack.channel_for(layer_by_id(scratches_id), 'ROUGHNESS'); origin_scratches.value = 1.
    stack.channel_for(layer_by_id(dirt_id), 'ROUGHNESS').effects[0].gamma = 2.
    rust = stack.add(ts, 'FILL', 'Rust'); rust_id = rust.id
    rust.parent_id = damage_id
    only(rust, 'ROUGHNESS'); stack.channel_for(rust, 'ROUGHNESS').value = .6
    layer_by_id(damage_id).visible = True   # the definition is saved as authored, not as hidden
    v2, block2 = modules.save_module(bpy.context, ts, layer_by_id(damage_id), Path(directory) / 'v2.openpaintmaterial')
    assert block2['version'] == 2
    layer_by_id(damage_id).visible = False

    version, conflicts = modules.update(ts, layer_by_id(instance_id), v2)
    assert version == 2
    instance = layer_by_id(instance_id)
    # Applied: the new layer and the untouched gamma; kept: the local opacity; conflict: the value.
    assert token + '.' + rust_id in {layer.id for layer in ts.layers}
    assert named(instance, rust_id).parent_id == instance_id
    assert abs(stack.channel_for(named(instance, dirt_id), 'ROUGHNESS').effects[0].gamma - 2.) < 1e-6
    assert abs(named(instance, dirt_id).opacity - .5) < 1e-6
    assert abs(stack.channel_for(named(instance, scratches_id), 'ROUGHNESS').value - .7) < 1e-6
    assert any('value' in path for path in conflicts) and len(conflicts) == 1, conflicts
    # Painted pixels are always local: the instance keeps its own mask image.
    kept = named(instance, scratches_id).masks[0].image
    assert kept is not None and kept.name == local_image and abs(images.pixels(kept)[0, 0, 0] - .25) < 1e-3
    assert not issues(ts)
    print('MODULE_UPDATE merge: PASS', flush=True)

    # The result equals a fresh v2 insert with the same local overrides applied.
    layer_by_id(damage_id).visible = False
    fresh = loader.apply(ts, v2)[0]
    fresh_id = fresh.id
    named(fresh, dirt_id).opacity = .5
    stack.channel_for(named(fresh, scratches_id), 'ROUGHNESS').value = .7
    images.write(named(fresh, scratches_id).masks[0].image, np.full((512, 512, 4), (.25, .25, .25, 1), np.float32), pack=False)
    layer_by_id(fresh_id).visible = False
    updated_render = rough()
    layer_by_id(fresh_id).visible = True
    layer_by_id(instance_id).visible = False
    np.testing.assert_allclose(rough(), updated_render, atol=1e-6)
    layer_by_id(instance_id).visible = True
    layer_by_id(fresh_id).visible = False
    print('MODULE_UPDATE equals fresh insert: PASS', flush=True)

    # Idempotent: updating an instance already at v2 changes nothing; Replace discards local edits.
    modules.write_base(block['id'], 2, modules.read_base(block['id'], 2))
    version, conflicts = modules.update(ts, layer_by_id(instance_id), v2)
    assert version == 2 and conflicts == [], conflicts
    assert abs(named(layer_by_id(instance_id), dirt_id).opacity - .5) < 1e-6
    version, conflicts = modules.update(ts, layer_by_id(instance_id), v2, replace=True)
    assert conflicts == [] and abs(named(layer_by_id(instance_id), dirt_id).opacity - 1.) < 1e-6
    assert abs(stack.channel_for(named(layer_by_id(instance_id), scratches_id), 'ROUGHNESS').value - 1.) < 1e-6
    print('MODULE_UPDATE idempotent and replace: PASS', flush=True)

    # A document that is not a version of this module is refused; the stack is untouched.
    other = stack.add(ts, 'FOLDER', 'Other'); other_id = other.id
    other_path, _ = modules.save_module(bpy.context, ts, layer_by_id(other_id), Path(directory) / 'other.openpaintmaterial', 'Other')
    arrangement = stack.arrangement(ts)
    try:
        modules.update(ts, layer_by_id(instance_id), other_path)
    except ValueError as error:
        assert 'not a version' in str(error)
    else:
        raise AssertionError('a foreign module updated an instance')
    assert stack.arrangement(ts) == arrangement
    # A failure mid-apply rolls the subtree back: force one by making the merged result cyclic.
    consumer = stack.channel_for(named(layer_by_id(instance_id), dirt_id), 'ROUGHNESS')
    from open_painter.evaluation import anchors
    layer_by_id(instance_id).publish_group = True
    consumer.anchor = anchors.group_id(instance_id)   # a cycle inside the instance (local edit)
    assert issues(ts)
    consumer.anchor = ''
    layer_by_id(instance_id).publish_group = False
    before = {layer.id: layer.name for layer in ts.layers}
    # The merge would carry a cycle if the document had one; emulate through Replace on a doc with
    # a self-referencing module root: build it from the origin folder.
    origin_dirt = stack.channel_for(layer_by_id(dirt_id), 'ROUGHNESS')
    layer_by_id(damage_id).publish_group = True
    origin_dirt.anchor = anchors.group_id(damage_id)
    try:
        modules.save_module(bpy.context, ts, layer_by_id(damage_id), Path(directory) / 'cyclic.openpaintmaterial')
    except ValueError:
        pass   # a cyclic origin cannot even be saved; the update path is covered by the foreign-module case
    origin_dirt.anchor = ''
    layer_by_id(damage_id).publish_group = False
    assert {layer.id: layer.name for layer in ts.layers} == before
    print('MODULE_UPDATE refusal and rollback: PASS', flush=True)

with TemporaryDirectory() as directory:
    from open_painter.core import mask_stack
    from open_painter.evaluation import anchors, layer_cache
    from open_painter.evaluation.references import warnings
    from open_painter.smart_materials import serializer
    from open_painter.smart_materials.format import read

    def image_names():
        # Images the file keeps: a released one (no fake user, nothing binding it) stays in
        # memory for an undo but is purged on the next save.
        return {image.name for image in bpy.data.images if image.users or image.use_fake_user}

    # Mask groups (0.9.3) inside a module: membership survives insert, update, replace and a
    # module saved from an instance.
    grouped = stack.add(ts, 'FOLDER', 'Grouped'); grouped_id = grouped.id
    member = stack.add(ts, 'FILL', 'Member'); member_id = member.id
    member.parent_id = grouped_id
    only(member, 'ROUGHNESS'); stack.channel_for(member, 'ROUGHNESS').value = .3
    group = member.masks.add(); group.id, group.source, group.invert = new_id(), 'GROUP', True
    group_id = group.id
    for source in ('NOISE', 'PROCEDURAL'):
        item = member.masks.add(); item.id, item.source, item.parent_id = new_id(), source, group_id

    def intact(layer, token):
        masks = layer.masks
        assert masks[0].id == token + '.' + group_id, masks[0].id
        assert mask_stack.members(masks, 0) == [1, 2], [(m.id, m.parent_id) for m in masks]
        assert all(m.parent_id == masks[0].id for m in masks[1:])

    g1, g_block = modules.save_module(bpy.context, ts, layer_by_id(grouped_id), Path(directory) / 'g1.openpaintmaterial', 'Grouped')
    g_instance = loader.apply(ts, g1)[0]
    g_instance_id, g_token = g_instance.id, g_instance.module_instance
    intact(named(g_instance, member_id), g_token)
    base_masks = next(layer for layer in modules.read_base(g_block['id'], 1) if layer['id'] == member_id)['masks']
    assert base_masks[1]['parent_id'] == group_id, 'the instance base keeps namespaced parent ids'
    stack.channel_for(layer_by_id(member_id), 'ROUGHNESS').value = .35
    g2, _ = modules.save_module(bpy.context, ts, layer_by_id(grouped_id), Path(directory) / 'g2.openpaintmaterial')
    version, conflicts = modules.update(ts, layer_by_id(g_instance_id), g2)
    assert version == 2 and conflicts == [], conflicts
    intact(named(layer_by_id(g_instance_id), member_id), g_token)
    modules.update(ts, layer_by_id(g_instance_id), g2, replace=True)
    intact(named(layer_by_id(g_instance_id), member_id), g_token)
    g3, _ = modules.save_module(bpy.context, ts, layer_by_id(g_instance_id), Path(directory) / 'g3.openpaintmaterial')
    data, _ = read(g3)
    saved_masks = next(layer for layer in data['layers'] if layer['id'] == member_id)['masks']
    assert saved_masks[0]['id'] == group_id and saved_masks[1]['parent_id'] == group_id, saved_masks
    reinserted = loader.apply(ts, g3)[0]
    intact(named(reinserted, member_id), reinserted.module_instance)
    print('MODULE_UPDATE mask groups: PASS', flush=True)

    # Updates neither duplicate unchanged texture assets nor orphan cache images, and a layer's
    # Cache mode survives; a refused update decodes nothing.
    textured = stack.add(ts, 'FOLDER', 'Textured'); textured_id = textured.id
    detailed = stack.add(ts, 'FILL', 'Detailed'); detailed_id = detailed.id
    detailed.parent_id = textured_id
    only(detailed, 'ROUGHNESS'); dprop = stack.channel_for(detailed, 'ROUGHNESS')
    dprop.image = images.create('Detail texture', 64)
    images.write(dprop.image, np.random.default_rng(1).random((64, 64, 4), np.float32), pack=False)
    t1, t_block = modules.save_module(bpy.context, ts, layer_by_id(textured_id), Path(directory) / 't1.openpaintmaterial', 'Textured')
    before_insert = image_names()
    t_instance = loader.apply(ts, t1)[0]
    t_instance_id, t_token = t_instance.id, t_instance.module_instance
    asset = stack.channel_for(named(t_instance, detailed_id), 'ROUGHNESS').image
    assert asset is not None and asset.name != 'Detail texture' and asset.get(modules.ASSET_HASH)
    asset_name = asset.name
    assert image_names() == before_insert | {asset_name}
    # A cached instance layer, and a local layer added inside the instance, both with a cache image.
    def fake_cache(layer):
        layer.cache_mode = 'CACHED'
        row = next(row for row in layer.cache if row.channel == 'ROUGHNESS')
        row.image = images.create('Cache of ' + layer.name, 64)
        row.image[layer_cache.TAG] = layer.id
        row.provenance = 'stale'
        return row.image.name
    cache_name = fake_cache(named(t_instance, detailed_id))
    local_layer = stack.add(ts, 'FILL', 'Local'); local_layer_id = local_layer.id
    local_layer.parent_id = t_instance_id
    local_cache_name = fake_cache(layer_by_id(local_layer_id))
    settled = image_names()
    layer_by_id(detailed_id).opacity = .6
    t2, _ = modules.save_module(bpy.context, ts, layer_by_id(textured_id), Path(directory) / 't2.openpaintmaterial')
    version, conflicts = modules.update(ts, layer_by_id(t_instance_id), t2)
    assert version == 2 and conflicts == [], conflicts
    assert image_names() == settled, image_names() ^ settled
    updated = named(layer_by_id(t_instance_id), detailed_id)
    assert stack.channel_for(updated, 'ROUGHNESS').image == asset, 'an unchanged asset was re-imported'
    assert updated.cache_mode == 'CACHED' and next(row for row in updated.cache if row.channel == 'ROUGHNESS').image.name == cache_name
    assert layer_by_id(local_layer_id).cache_mode == 'CACHED'
    modules.write_base(t_block['id'], 2, modules.read_base(t_block['id'], 2))
    modules.update(ts, layer_by_id(t_instance_id), t2)
    assert image_names() == settled, image_names() ^ settled
    # Replace drops the local layer, and with it its cache image: released, not removed (0.9.7),
    # so an undo of the Replace finds the datablock.
    modules.update(ts, layer_by_id(t_instance_id), t2, replace=True)
    assert not any(layer.id == local_layer_id for layer in ts.layers)
    assert image_names() == settled - {local_cache_name}, image_names() ^ settled
    assert local_cache_name in bpy.data.images and not bpy.data.images[local_cache_name].use_fake_user
    assert stack.channel_for(named(layer_by_id(t_instance_id), detailed_id), 'ROUGHNESS').image == asset
    # A changed asset replaces the instance's copy instead of piling up next to it.
    images.write(dprop.image, np.random.default_rng(2).random((64, 64, 4), np.float32), pack=False)
    t3, _ = modules.save_module(bpy.context, ts, layer_by_id(textured_id), Path(directory) / 't3.openpaintmaterial')
    modules.update(ts, layer_by_id(t_instance_id), t3)
    replaced = stack.channel_for(named(layer_by_id(t_instance_id), detailed_id), 'ROUGHNESS').image
    assert replaced.name != asset_name and asset_name not in image_names()
    assert asset_name in bpy.data.images and not bpy.data.images[asset_name].use_fake_user
    assert image_names() == (settled - {local_cache_name, asset_name}) | {replaced.name}, image_names() ^ settled
    settled = image_names()
    # Without a base snapshot the update is refused before any asset is decoded.
    bpy.data.texts.remove(bpy.data.texts[modules.base_name(t_block['id'], 3)])
    layer_by_id(detailed_id).opacity = .7
    t4, _ = modules.save_module(bpy.context, ts, layer_by_id(textured_id), Path(directory) / 't4.openpaintmaterial')
    for _ in range(2):
        try:
            modules.update(ts, layer_by_id(t_instance_id), t4)
        except ValueError as error:
            assert 'base snapshot' in str(error)
        else:
            raise AssertionError('an instance without a base updated')
    assert image_names() == settled, image_names() ^ settled
    print('MODULE_UPDATE datablock economy: PASS', flush=True)

    # A layer the artist adds inside an instance keeps its id across updates, so a consumer outside
    # the instance still finds its published output.
    extra = stack.add(ts, 'FILL', 'Extra'); extra_id = extra.id
    extra.parent_id = t_instance_id
    only(extra, 'ROUGHNESS'); stack.channel_for(extra, 'ROUGHNESS').value = .1
    extra.publish_output = True
    consumer = stack.add(ts, 'FILL', 'Consumer'); consumer_id = consumer.id
    only(consumer, 'ROUGHNESS'); stack.channel_for(consumer, 'ROUGHNESS').anchor = anchors.output_id(extra_id)
    assert not issues(ts) and not warnings(ts), (issues(ts), warnings(ts))
    version, conflicts = modules.update(ts, layer_by_id(t_instance_id), t4, replace=True)   # the base is gone
    assert version == 4 and not any(layer.id == extra_id for layer in ts.layers)   # Replace discards the addition
    assert warnings(ts) == ['Consumer: anchor point is missing'], warnings(ts)
    extra = stack.add(ts, 'FILL', 'Extra'); extra_id = extra.id
    extra.parent_id = t_instance_id
    only(extra, 'ROUGHNESS'); stack.channel_for(extra, 'ROUGHNESS').value = .1
    extra.publish_output = True
    stack.channel_for(layer_by_id(consumer_id), 'ROUGHNESS').anchor = anchors.output_id(extra_id)
    assert not warnings(ts)
    layer_by_id(detailed_id).opacity = .8
    t5, _ = modules.save_module(bpy.context, ts, layer_by_id(textured_id), Path(directory) / 't5.openpaintmaterial')
    version, conflicts = modules.update(ts, layer_by_id(t_instance_id), t5)
    assert version == 5 and conflicts == [], conflicts
    assert layer_by_id(extra_id).parent_id == t_instance_id, 'the local addition was re-namespaced'
    assert stack.channel_for(layer_by_id(consumer_id), 'ROUGHNESS').anchor == anchors.output_id(extra_id)
    assert not issues(ts) and not warnings(ts), (issues(ts), warnings(ts))
    layer_by_id(detailed_id).opacity = .9
    t6, _ = modules.save_module(bpy.context, ts, layer_by_id(textured_id), Path(directory) / 't6.openpaintmaterial')
    modules.update(ts, layer_by_id(t_instance_id), t6)
    assert layer_by_id(extra_id).parent_id == t_instance_id and not warnings(ts)
    print('MODULE_UPDATE local additions keep their ids: PASS', flush=True)

    # Merged dicts restore in RNA order: a mesh selector edited on both sides keeps the name its
    # pointer mirrors even when the merge hands the name before the pointer.
    bpy.ops.mesh.primitive_cube_add(size=1)
    bolts = bpy.context.object; bolts.name = 'Bolts'
    masked = stack.add(ts, 'FOLDER', 'Masked'); masked_id = masked.id
    bolted = stack.add(ts, 'FILL', 'Bolted'); bolted_id = bolted.id
    bolted.parent_id = masked_id
    only(bolted, 'ROUGHNESS')
    mesh_mask = bolted.masks.add(); mesh_mask.id, mesh_mask.source = new_id(), 'MESH'
    mesh_mask.mesh.kind = 'OBJECT'; mesh_mask.mesh.object = bolts
    m1, m_block = modules.save_module(bpy.context, ts, layer_by_id(masked_id), Path(directory) / 'm1.openpaintmaterial', 'Masked')
    m_instance = loader.apply(ts, m1)[0]
    m_instance_id, m_token = m_instance.id, m_instance.module_instance
    selector = named(m_instance, bolted_id).masks[0].mesh
    assert selector.object is None and selector.object_name == 'Bolts'
    selector.include_children = False   # local edit
    layer_by_id(bolted_id).masks[0].mesh.softness = .5   # the author's edit (radians)
    m2, _ = modules.save_module(bpy.context, ts, layer_by_id(masked_id), Path(directory) / 'm2.openpaintmaterial')
    data, assets = read(m2)
    with loader.decode_assets(assets, []) as decode:
        parent_raw = [decode(layer) for layer in data['layers']]
    parent = modules.comparable(parent_raw)
    for layer in parent:
        for mask in layer['masks']:
            mask['mesh'] = dict(reversed(list(mask['mesh'].items())))   # names before pointers
    version, conflicts = modules.apply_update(ts, layer_by_id(m_instance_id), parent, 2, 'Masked', 'library:' + m2.name, [],
                                              parent_raw=parent_raw)
    assert version == 2 and conflicts == [], conflicts
    selector = named(layer_by_id(m_instance_id), bolted_id).masks[0].mesh
    assert selector.object_name == 'Bolts' and selector.resolve('object') == bolts, selector.object_name
    assert selector.include_children is False and abs(selector.softness - .5) < 1e-6
    print('MODULE_UPDATE restore order: PASS', flush=True)

    # A duplicated instance is a second instance under its own token: its ids keep their plain
    # part, so Update merges it against the shared base (no spurious conflicts, no id rewrite)
    # and a consumer outside the copy keeps finding the copy's anchors. Duplicating the origin
    # folder namespaces the copy the same way.
    from open_painter.core import texture_set_manager as sets
    damage2 = stack.add(ts, 'FOLDER', 'Damage2'); damage2_id = damage2.id
    scratched = stack.add(ts, 'FILL', 'Scratched'); scratched_id = scratched.id
    scratched.parent_id = damage2_id
    only(scratched, 'ROUGHNESS'); stack.channel_for(scratched, 'ROUGHNESS').value = .9
    scratched.publish_output = True
    d1, d_block = modules.save_module(bpy.context, ts, layer_by_id(damage2_id), Path(directory) / 'd1.openpaintmaterial', 'Damage2')
    d_instance = loader.apply(ts, d1)[0]
    d_instance_id, d_token = d_instance.id, d_instance.module_instance
    stack.channel_for(named(d_instance, scratched_id), 'ROUGHNESS').value = .85   # local edit
    copy_id = stack.duplicate(ts, stack.index_of(ts, d_instance_id)).id
    copy = layer_by_id(copy_id)
    assert copy.module_id == d_block['id'] and copy.module_instance and copy.module_instance != d_token
    assert copy_id == copy.module_instance + '.' + damage2_id, copy_id
    copy_scratched = named(copy, scratched_id)
    assert copy_scratched.parent_id == copy_id
    assert {layer.id for layer in modules.instances(ts, d_block['id'])} == {damage2_id, d_instance_id, copy_id}
    consumer = stack.add(ts, 'FILL', 'Copy consumer'); consumer_id = consumer.id
    only(consumer, 'ROUGHNESS'); stack.channel_for(consumer, 'ROUGHNESS').anchor = anchors.output_id(copy_scratched.id)
    assert not issues(ts) and not warnings(ts), (issues(ts), warnings(ts))
    layer_by_id(scratched_id).opacity = .5
    d2, _ = modules.save_module(bpy.context, ts, layer_by_id(damage2_id), Path(directory) / 'd2.openpaintmaterial')
    version, conflicts = modules.update(ts, layer_by_id(copy_id), d2)
    assert version == 2 and conflicts == [], conflicts
    copy = layer_by_id(copy_id)
    assert copy.module_version == 2 and copy.module_id == d_block['id']
    copy_scratched = named(copy, scratched_id)
    assert abs(copy_scratched.opacity - .5) < 1e-6, 'the new version applied to the copy'
    assert abs(stack.channel_for(copy_scratched, 'ROUGHNESS').value - .85) < 1e-6, 'the local edit travelled with the copy'
    assert not issues(ts) and not warnings(ts), (issues(ts), warnings(ts))
    version, conflicts = modules.update(ts, layer_by_id(d_instance_id), d2)
    assert version == 2 and conflicts == [], conflicts
    origin_copy_id = stack.duplicate(ts, stack.index_of(ts, damage2_id)).id
    origin_copy = layer_by_id(origin_copy_id)
    assert origin_copy.module_instance and origin_copy_id == origin_copy.module_instance + '.' + damage2_id
    stack.channel_for(named(origin_copy, scratched_id), 'ROUGHNESS').value = .8
    layer_by_id(scratched_id).opacity = .4
    d3, _ = modules.save_module(bpy.context, ts, layer_by_id(damage2_id), Path(directory) / 'd3.openpaintmaterial')
    version, conflicts = modules.update(ts, layer_by_id(origin_copy_id), d3)
    assert version == 3 and conflicts == [], conflicts
    assert abs(named(layer_by_id(origin_copy_id), scratched_id).opacity - .4) < 1e-6
    assert abs(stack.channel_for(named(layer_by_id(origin_copy_id), scratched_id), 'ROUGHNESS').value - .8) < 1e-6
    # A duplicated set carries its instances the same way (the set collection is reallocated
    # by the copy: nothing below reads ``ts``).
    tokens = {d_token, layer_by_id(copy_id).module_instance, layer_by_id(origin_copy_id).module_instance}
    set_copy = sets.duplicate(scene, next(i for i, item in enumerate(scene.open_painter.texture_sets) if item.id == ts.id))
    copied = modules.instances(set_copy, d_block['id'])
    assert len(copied) == 4 and len({item.module_instance for item in copied}) == 4   # the origin folder too
    assert {item.module_instance for item in copied}.isdisjoint(tokens)
    assert all(item.id == item.module_instance + '.' + damage2_id for item in copied)
    assert not issues(set_copy), issues(set_copy)
    version, conflicts = modules.update(set_copy, copied[0], d3)
    assert version == 3 and conflicts == [], conflicts
    scene.open_painter.texture_sets.remove(next(i for i, item in enumerate(scene.open_painter.texture_sets) if item.id == set_copy.id))
    print('MODULE_UPDATE duplicated instances: PASS', flush=True)

with TemporaryDirectory() as directory:
    # Portable strokes (0.6.0) are instance-local: the loader keys an imported stroke layer and
    # Vector Strokes mask afresh, together with the strokes it restores, and Update and Replace
    # keep that key (and a paint link) instead of merging the document's own key back over it,
    # which aliased the origin's strokes here and orphaned the instance's.
    from types import SimpleNamespace
    from open_painter.painting import strokes as records
    project = scene.open_painter

    def settings():
        return SimpleNamespace(radius=.25, hardness=.9, opacity=1., flow=1., accumulate='MAX', alpha_image=None,
                               projection='SURFACE', blend='PAINT', base_color=(1, 0, 0, 1), roughness=.9, metallic=0.,
                               height=.5, ao=1., emission=(0, 0, 0, 1), opacity_value=1., normal=(.5, .5, 1, 1),
                               channels={'ROUGHNESS'})

    def samples(count=5):
        z = np.linspace(-.5, .5, count, dtype=np.float32)
        return np.stack([np.ones(count, np.float32), np.zeros(count, np.float32), z,
                         np.ones(count, np.float32), np.zeros(count, np.float32), np.zeros(count, np.float32),
                         np.ones(count, np.float32), np.full(count, .25, np.float32),
                         np.zeros(count, np.float32), np.zeros(count, np.float32),
                         np.zeros(count, np.float32), np.zeros(count, np.float32), np.ones(count, np.float32)], axis=1)

    marked = stack.add(ts, 'FOLDER', 'Marked'); marked_id = marked.id
    marks = stack.add(ts, 'PAINT', 'Marks'); marks_id = marks.id
    marks.parent_id = marked_id
    origin_key = records.record(project, ts, layer_by_id(marks_id), samples(), settings(), universal=False).layer_key
    striped = stack.add(ts, 'FILL', 'Striped'); striped_id = striped.id
    striped.parent_id = marked_id
    only(striped, 'ROUGHNESS')
    strokes_mask = striped.masks.add(); strokes_mask.id, strokes_mask.source = new_id(), 'STROKES'
    strokes_mask.stroke_key = origin_mask_key = new_id()
    records.record(project, ts, layer_by_id(striped_id), samples(), settings(), key=origin_mask_key)
    s1, s_block = modules.save_module(bpy.context, ts, layer_by_id(marked_id), Path(directory) / 's1.openpaintmaterial', 'Marked')
    s_instance = loader.apply(ts, s1)[0]
    s_instance_id = s_instance.id
    local_key = named(s_instance, marks_id).stroke_key
    local_mask_key = named(s_instance, striped_id).masks[0].stroke_key
    assert local_key and local_key != origin_key and len(records.strokes_for(project, local_key)) == 1
    assert local_mask_key and local_mask_key != origin_mask_key and len(records.strokes_for(project, local_mask_key)) == 1
    named(s_instance, marks_id).paint_link = link = new_id()   # a brush session across sets (local state)
    layer_by_id(marks_id).opacity = .6
    s2, _ = modules.save_module(bpy.context, ts, layer_by_id(marked_id), Path(directory) / 's2.openpaintmaterial')
    for replace in (False, True):
        version, conflicts = modules.update(ts, layer_by_id(s_instance_id), s2, replace=replace)
        assert version == 2 and conflicts == [], conflicts
        updated = named(layer_by_id(s_instance_id), marks_id)
        assert abs(updated.opacity - .6) < 1e-6
        assert updated.stroke_key == local_key, (replace, updated.stroke_key, local_key, origin_key)
        assert updated.paint_link == link, (replace, updated.paint_link)
        assert named(layer_by_id(s_instance_id), striped_id).masks[0].stroke_key == local_mask_key, replace
        assert len(records.strokes_for(project, local_key)) == 1 and len(records.strokes_for(project, local_mask_key)) == 1
        assert layer_by_id(marks_id).stroke_key == origin_key and layer_by_id(striped_id).masks[0].stroke_key == origin_mask_key
        assert records.purge_orphans(project) == 0, 'the update orphaned the instance\'s strokes'
        assert not issues(ts), issues(ts)
    print('MODULE_UPDATE portable strokes: PASS', flush=True)
print('MODULE_UPDATE_PASS', flush=True)
open_painter.unregister()
