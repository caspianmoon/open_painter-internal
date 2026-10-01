"""Read-only source/repair/fixture audit for the fixed Eevee primary benchmark.

Replays deterministic geometry/UV preparation; never calls AI or saves a blend.
"""
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import prepare_fixtures as prep
from assets import DEFAULT_MANIFEST, DEFAULT_ROOT, digest


def verify_fixture(path, manifest_path=DEFAULT_MANIFEST, asset_root=DEFAULT_ROOT):
    import bpy
    import open_painter
    from open_painter.core import texture_set_manager as sets
    from open_painter.baking import doctor
    from open_painter.geometry.provenance import status
    from open_painter.geometry.texel_density import measure
    path = Path(path)
    audit = json.loads(path.with_suffix('.json').read_text(encoding="utf-8"))
    result = dict(fixture=path.name, status='PASS', checks=[], failures=[])
    def check(name, passed):
        result['checks'].append(dict(name=name, passed=bool(passed)))
        if not passed:
            result['failures'].append(name)
            result['status'] = 'FAIL'
    check('fixture_file_checksum', digest(path) == audit['sha256'])
    check('setup_report_ready', audit['status'] == 'READY' and not audit['blockers'])
    check('new_eevee_protocol', audit.get('protocol') == prep.RIG_VERSION)
    check('no_cycles', audit.get('cycles_used') is False)
    rig_path = Path(audit['fixed_rig_file']['path'])
    check('original_fixed_rig_file', digest(rig_path) == audit['fixed_rig_file']['sha256'])
    if not hasattr(bpy.types.Scene, 'open_painter'):
        open_painter.register()
    bpy.ops.wm.open_mainfile(filepath=str(path.resolve()), load_ui=False, use_scripts=False)
    scene = bpy.context.scene
    config = json.loads(scene['md_benchmark_config'])
    result['rig_signature'] = prep.rig_signature(scene, config)
    check('fixed_rig_signature', result['rig_signature'] == config['rig_signature'])
    check('rig_record_matches_preparation', prep.fixed_rig_record(scene, config) == audit['rig_record'])
    check('one_benchmark_scene', len(bpy.data.scenes) == 1 and scene.name == 'BENCHMARK_SCENE')
    check('only_two_infrastructure_collections', {c.name for c in bpy.data.collections} == {'TEST_MODEL', 'BENCHMARK_LIGHTS'})
    check('eevee_1024_opaque_no_postprocessing', scene.render.engine == 'BLENDER_EEVEE'
          and scene.render.resolution_x == scene.render.resolution_y == 1024
          and scene.render.resolution_percentage == 100 and not scene.render.film_transparent
          and not scene.render.use_compositing and not scene.render.use_sequencer)
    check('one_camera_three_lights_only', {o.name for o in scene.objects if o.type == 'CAMERA'} == {'BENCHMARK_CAMERA'}
          and {o.name for o in scene.objects if o.type == 'LIGHT'} == {'Key', 'Fill', 'Rim'}
          and all(o.type in {'MESH', 'CAMERA', 'LIGHT'} for o in scene.objects))
    check('no_unlinked_source_rig_datablocks', len(bpy.data.cameras) == 1 and len(bpy.data.lights) == 3
          and len(bpy.data.worlds) == 1 and scene.world.name == 'BENCHMARK_WORLD')
    model = bpy.data.collections['TEST_MODEL']
    check('all_and_only_model_meshes', all(o.type == 'MESH' for o in model.all_objects)
          and set(model.all_objects) == {o for o in scene.objects if o.type == 'MESH'})
    actual = {obj.name: prep.object_snapshot(obj) for obj in model.all_objects}
    check('declared_set_ids', [t.id for t in scene.open_painter.texture_sets] == config['target_set_ids'])
    source_image_names = {item['name'] for item in audit['contamination_removal']['removed_images']}
    check('no_source_image_datablocks', not source_image_names.intersection(image.name for image in bpy.data.images))
    check('no_source_modifiers_or_scripts', all(not obj.modifiers for obj in model.all_objects) and not bpy.data.texts)
    check('no_source_appearance_attributes', all(item['color_attribute_count'] == item['vertex_group_count'] == 0
                                                 for item in actual.values()))
    maps = {m['name']: m for t in audit['texture_sets'] for m in t['maps']}
    for ts in scene.open_painter.texture_sets:
        check(ts.name + ':1024', int(ts.resolution) == 1024)
        check(ts.name + ':strict_geometry_uv_preflight', not prep._strict_findings(doctor.examine(ts, 'FULL')))
        check(ts.name + ':usable_atlas_occupancy', measure(ts)['sampled_occupancy'] >= prep.MIN_ATLAS_OCCUPANCY)
        check(ts.name + ':fresh_maps_after_reopen', all(status(ts, m) == 'Ready' for m in ts.baked_maps))
        required = {'AO', 'CURVATURE', 'POSITION', 'WORLD_NORMAL', 'THICKNESS'}
        check(ts.name + ':required_geometry_maps', required <= {m.kind for m in ts.baked_maps})
        check(ts.name + ':packed_map_pixels_after_reopen',
              all(m.image and m.image.packed_file and m.image.name in maps
                  and prep._image_hash(m.image) == maps[m.image.name]['decoded_sha256']
                  and list(m.image.size) == [1024, 1024] for m in ts.baked_maps))
        ao = next(m.image for m in ts.baked_maps if m.kind == 'AO')
        check(ts.name + ':renderer_independent_fresh_ao', ao.get('benchmark_bake_method') == 'CPU_BVH_COSINE_HEMISPHERE_V1'
              and ao.get('sampling_resolution') == 1024 and ao.get('samples') == 32)
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if len(audit['asset_ids']) != 1:
        raise ValueError('Legacy multi-asset fixture cannot enter this benchmark')
    entry = next(item for item in manifest['assets'] if item['id'] == audit['asset_ids'][0])
    # Replay from the exact original source. Repairs intentionally change only
    # invalid geometry/UVs; parity is against the replayed clean source, not the
    # known defective unmodified source or a hand-authored expected mesh.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    collection = bpy.data.collections.new('TEST_MODEL')
    bpy.context.scene.collection.children.link(collection)
    objects, _ = prep._load_geometry(entry, asset_root, collection)
    normalization = prep.normalize_model(objects)
    geometry_repairs = prep._repair_geometry(objects)
    bindings, _ = prep._neutralize(objects)
    for refs in bindings.values():
        name = refs[0][0].material_slots[refs[0][1]].material.name.removeprefix('BENCH_NEUTRAL_')
        ts = sets.create(bpy.context.scene, name, prep.MAP_RESOLUTION)
        for obj, index in refs:
            sets.assign(bpy.context.scene, ts, obj, slot_index=index)
        ts.bake.selected_to_active = False
        ts.bake.normal = ts.bake.ao = False
    uv_repairs = prep.repair_preflight(bpy.context.scene.open_painter.texture_sets)
    bpy.context.view_layer.update()
    expected = {obj.name: prep.object_snapshot(obj) for obj in objects}
    check('normalization_reproduces_exactly', normalization == audit['normalization'])
    def operations(records):
        # Optional report-only fields were added while evaluating setup. Exact
        # mesh/UV replay below remains the authoritative result comparison.
        return [{key: value for key, value in record.items() if key != 'final_atlas_occupancy'
                 and (key != 'repaired_uv_faces' or value)} for record in records]
    check('deterministic_repair_log', prep._json_hash(operations(geometry_repairs + uv_repairs))
          == prep._json_hash(operations(audit['repairs'])))
    check('actual_source_material_boundaries_only', len(bindings) == len(config['target_set_ids']))
    check('exact_source_object_selection', set(actual) == set(expected))
    invariants = ('vertices', 'polygons', 'triangles', 'geometry_sha256', 'uv_sha256',
                  'material_partition_sha256', 'corner_normals_sha256', 'geometry_attributes', 'transform_sha256')
    for name in sorted(set(actual) & set(expected)):
        for field in invariants:
            check(name + ':' + field, actual[name][field] == expected[name][field])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', type=Path, required=True)
    parser.add_argument('--asset-manifest', type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument('--assets-root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--assets', help='Limit by fixture name prefix, e.g. A01,A04')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else sys.argv[1:])
    results = []
    for path in sorted(args.fixtures.glob('*.blend')):
        if args.assets and path.name[:3] not in args.assets.split(','):
            continue
        try:
            result = verify_fixture(path, args.asset_manifest, args.assets_root)
        except Exception as exc:
            result = dict(fixture=path.name, status='FAIL', failures=[f'{type(exc).__name__}: {exc}'])
        results.append(result)
        print('AUDIT', result['fixture'], result['status'], result['failures'], flush=True)
    signatures = {result.get('rig_signature') for result in results}
    same_rig = bool(results) and len(signatures) == 1 and None not in signatures
    output = args.output or args.fixtures / 'source_fixture_audit.json'
    good = same_rig and all(result['status'] == 'PASS' for result in results)
    output.write_text(json.dumps(dict(status='PASS' if good else 'FAIL', same_fixed_rig=same_rig,
                                     results=results), indent=2) + '\n', encoding="utf-8")
    if not good:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
