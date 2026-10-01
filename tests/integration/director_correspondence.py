"""Real Eevee pixel lookup, paired reversed UV seam, source edit and re-review.

Synthetic generated PNGs isolate host correctness; no provider calls or aesthetic verdict.
Run in visible Blender --factory-startup --python tests/integration/director_correspondence.py.
"""
import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback

import bpy
import numpy as np
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'src'))
import open_painter
from open_painter.ai import candidates, correspondence, generated_images, session
from open_painter.ai.viewport import png
from open_painter.authoring import assets, commands, document, schema, surface_maps, surface_replacement, transactions
from open_painter.core import texture_set_manager as sets, image_manager
from open_painter.evaluation import cache
from open_painter.geometry.texture_correspondence import lookup

OUT = ROOT/'.blender-test/director-correspondence'
OUT.mkdir(parents=True, exist_ok=True)


def resources():
    return {key: {item.as_pointer() for item in getattr(bpy.data, key)} for key in candidates._STORES}


def expect_error(fn, message):
    try:
        fn()
    except (ValueError, schema.AuthoringError) as exc:
        assert message.lower() in str(exc).lower(), str(exc)
    else:
        raise AssertionError('Expected error: '+message)


def run():
    scene = bpy.context.scene
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    scene.render.engine = 'BLENDER_EEVEE'
    mesh = bpy.data.meshes.new('Bent continuous panel with reversed UV islands')
    mesh.from_pydata([(-1,-1,0),(0,-1,.2),(1,-1,0),(-1,1,0),(0,1,.2),(1,1,0)], [], [(0,1,4,3),(1,2,5,4)])
    mesh.uv_layers.new()
    uv = np.array([(.05,.15),(.4,.15),(.4,.85),(.05,.85),(.6,.85),(.95,.85),(.95,.15),(.6,.15)], np.float32)
    mesh.uv_layers.active.data.foreach_set('uv', uv.ravel())
    obj = bpy.data.objects.new('Continuous test surface', mesh); scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj; obj.select_set(True)
    ts = sets.create(scene, 'Paired UV test', 512); sets.assign(scene, ts, obj)
    scene.open_painter.active_texture_set = 0
    bpy.context.view_layer.update()
    from open_painter.baking.maps.utility import bake_utility
    for kind in ('WORLD_NORMAL', 'POSITION'):
        entry = ts.baked_maps.add(); entry.kind = kind
        entry.image = bake_utility(ts, kind)
    # Deliberately no baked AO: the packet must disclose this instead of fabricating it.
    area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    area.spaces.active.shading.type = 'MATERIAL'
    area.spaces.active.region_3d.view_rotation = Vector((0,-1,1)).to_track_quat('Z','Y')
    with bpy.context.temp_override(area=area, region=region):
        captured = session.capture(bpy.context, mode='CREATE', scope='SET', prompt='Synthetic geometry correspondence regression')
        captured['context']['image_generation'] = dict(enabled=True)
        session.request = captured
        def tool(name, **arguments):
            return session.tool_call(dict(tool=name, arguments=arguments, request_id=captured['id']))
        component = dict(name='Bent panel', texture_set_id=ts.id, substrate='A dielectric synthetic reference',
            history='Synthetic continuity validation only', macro='Two contrasting test chart regions',
            meso='A deliberately reversed stripe at the UV split', micro='Small source variations to expose the mapping',
            generated_source='Synthetic PNG reference used as an image-generation fixture', mapping='UV split across a connected physical edge', authored_detail='No markings needed for this regression')
        tool('plan_surface', object_interpretation='Two connected faces with mirrored UV direction',
             art_direction='Synthetic contrast for geometry inspection', components=[component])
        with tempfile.TemporaryDirectory() as directory:
            image = np.full((128, 128, 4), 255, np.uint8)
            image[:,:,:3] = [60,90,150]; image[:,64:,:3] = [150,60,90]
            image[::12,:,:3] = 220
            def register(rgba):
                metadata = generated_images.store(directory, dict(result=base64.b64encode(png(128,128,rgba.tobytes())).decode()))
                metadata['state_directory'] = directory
                captured['images'][metadata['image_id']] = metadata
                return metadata['image_id']
            initial_id = register(image)
            proposal = dict(version=1, summary='Synthetic seam candidate', commands=[
                dict(op='add_layer', id='new:surface', name='Generated test source', kind='FILL'),
                dict(op='assign_generated_surface', layer_id='new:surface', image_id=initial_id,
                     name='Test source', purpose='Expose mirrored UV correspondence', roughness_range=[.3,.7], height_amplitude=.01)])
            original = document.digest(ts); before = resources()
            preview = tool('preview_proposal', proposal_json=json.dumps(proposal))
            assert resources() == before and document.digest(ts) == original
            assert set(preview['review_views']) == {'front','back','left','right','top','bottom','close'}
            stored = captured['candidate']['correspondence']
            top = stored['views']['top']
            p = correspondence.project(top, [-.015, 0, .197], 512)/512
            args = dict(texture_set_id=ts.id, candidate_digest=preview['candidate_digest'], view='top', x=float(p[0]), y=float(p[1]))
            expect_error(lambda: tool('inspect_texture_correspondence', **dict(args, candidate_digest='0'*64)), 'superseded')
            packet = tool('inspect_texture_correspondence', **args)
            assert resources() == before and document.digest(ts) == original
            assert packet['seam'] and len(packet['sources']) == 1
            assert packet['seam']['side_a']['uv_island'] != packet['seam']['side_b']['uv_island']
            assert abs(abs(packet['seam']['orientation']['rotation_radians'])-np.pi) < 1e-5
            hit = packet['surface']
            assert abs(sum(hit['barycentric'])-1) < 1e-6 and hit['uv_island'] >= 0
            assert 'AO' in packet['missing_geometry_maps']
            assert any(p['id'].endswith(':geometry_POSITION') for p in packet['_images'])
            assert any(p['id'].endswith(':geometry_WORLD_NORMAL') for p in packet['_images'])
            # Orthographic picking and an unassigned foreground occluder use the
            # same lookup, without selecting a hidden material behind it.
            from open_painter.ai.viewport import framing_matrices
            from mathutils import Matrix
            from mathutils.bvhtree import BVHTree
            from types import SimpleNamespace
            geometry_record, _ = lookup(stored['geometry'], top, float(p[0]), float(p[1]), ts.id)
            matrix, projection = framing_matrices(SimpleNamespace(view_matrix=Matrix(top['matrix']),
                window_matrix=Matrix.Identity(4)), [Vector(v) for v in geometry_record['positions']], 1.)
            ortho = dict(matrix=[list(row) for row in matrix], projection=[list(row) for row in projection])
            xy = correspondence.project(ortho, [-.015,0,.197], 512)/512
            assert lookup(stored['geometry'], ortho, *xy, ts.id)[1]['texture_set_id'] == ts.id
            occluder = dict(geometry_record, sets={})
            elevated = geometry_record['positions']+[0,0,.5]
            occluder['bvh'] = BVHTree.FromPolygons(elevated.tolist(), geometry_record['vertices'].tolist(), all_triangles=True)
            expect_error(lambda: lookup([geometry_record, occluder], ortho, *xy, ts.id), 'occluder')
            again = tool('inspect_texture_correspondence', **args)
            assert again is packet and len(captured['correspondences']) == 1
            expect_error(lambda: lookup(stored['geometry'], top, .01, .01, ts.id), 'background')
            expect_error(lambda: lookup(stored['geometry'], top, float(p[0]), float(p[1]), 'wrong'), 'another material')
            for i, picture in enumerate(packet['_images']):
                (OUT/f'packet-{i}.png').write_bytes(base64.b64decode(picture['url'].split(',',1)[1]))
            (OUT/'packet.json').write_text(json.dumps({k:v for k,v in packet.items() if k != '_images'}, indent=2))
            weakness = dict(component='Bent panel', observation='The stripe changes direction at the same physical boundary',
                correction='Edit paired source windows to continue the stripe across the seam', evidence_image_id='candidate:top',
                category='UV_SEAM', scope='LOCAL', correction_type='TEXTURE_SOURCE', correspondence_id=packet['correspondence_id'])
            critique = dict(candidate_digest=preview['candidate_digest'], weaknesses=[weakness],
                generated_contribution='The generated color chart dominates the visible surface', preserved_decisions='Keep existing palette and scale beyond the inspected boundary')
            expect_error(lambda: tool('critique_candidate', **critique), 'front')
            tool('critique_candidate', **dict(critique, reviewed_views=preview['review_views']))
            edited = np.full_like(image, 255); edited[:,:,:3] = [210,180,40]
            new_id = register(edited)
            correction = dict(op='replace_surface_source', layer_id='new:surface', image_id=new_id,
                reference_image_id=initial_id, correspondence_id=packet['correspondence_id'], mode='LOCAL',
                reason='Continue the source pattern across the inspected physical boundary')
            revised = dict(proposal, summary='Locally corrected source', commands=proposal['commands']+[correction])
            expect_error(lambda: commands.compile_proposal(ts, dict(revised, commands=proposal['commands']+[
                dict(correction, mode='GLOBAL')]), captured['id'], images=captured['images'],
                correspondences=captured['correspondences']), 'scope')
            # Source windows follow the native projection families, not only UV charts.
            _, original_compiled = candidates.compile_proposal(scene, ts, captured, proposal)
            with candidates.scratch(bpy.context, original_compiled, captured['images']) as temporary:
                part = next(item for item in temporary.open_painter.texture_sets if item.id == ts.id)
                owner = part.layers[0]
                source_prop = next(p for p in owner.channels if p.channel == 'BASE_COLOR')
                for projection_kind in ('TRIPLANAR', 'DECAL', 'METRIC'):
                    source_prop.projection = 'DECAL' if projection_kind == 'DECAL' else 'TRIPLANAR'
                    owner.decal_inline = True; owner.decal_extent = [2,2]
                    owner.decal_origin = [0,0,.2]; owner.decal_axes = [1,0,0,0,1,0,0,0,1]
                    owner.decal_depth = .5
                    source_prop.material_mapping_version = 1 if projection_kind == 'METRIC' else 0
                    windows = correspondence.source_windows(part, owner, source_prop, hit,
                        packet['radius_world'], geometry_record)
                    assert windows and all(0 <= a < c <= 1 and 0 <= b < d <= 1 for a,b,c,d in windows), projection_kind
            # Exact host preservation, even when the simulated generator changes the entire canvas.
            compiled = commands.compile_proposal(ts, revised, captured['id'], images=captured['images'],
                correspondences=captured['correspondences'])
            resolved = assets.prepare(compiled['after'], images=captured['images'])
            prop = next(p for p in resolved['layers'][0]['channels'] if p['channel']=='BASE_COLOR')
            original_image = assets.prepare({'$generated':initial_id, 'color_space':'sRGB'}, images=captured['images'])
            changed, old = image_manager.pixels(prop['image']), image_manager.pixels(original_image)
            mask = surface_replacement.window_mask(changed.shape[:2], packet['sources'][0]['source_windows'])
            assert np.array_equal(changed[mask == 0], old[mask == 0])
            assert np.max(abs(changed[mask == 1]-old[mask == 1])) > .1
            baseline = commands.compile_proposal(ts, proposal, captured['id'], images=captured['images'])['after']
            initial = assets.prepare(baseline, images=captured['images'])
            old_rough = next(p for p in initial['layers'][0]['channels'] if p['channel']=='ROUGHNESS')
            new_rough = next(p for p in resolved['layers'][0]['channels'] if p['channel']=='ROUGHNESS')
            assert np.array_equal(image_manager.pixels(old_rough['detail_image'])[mask == 0],
                                  image_manager.pixels(new_rough['detail_image'])[mask == 0])
            # Reference-conditioned scalar edits preserve the same outside pixels too.
            scalar = np.full_like(image, 255); scalar[:,:,:3] = 70
            old_map = register(scalar)
            scalar[:,:,:3] = 180
            new_map = register(scalar)
            mapped = document.clone(baseline['layers'][0])
            surface_maps.assign(mapped, dict(reference_image_id=initial_id,
                maps={'ROUGHNESS':old_map, 'HEIGHT':old_map}, height_amplitude=.03), captured['images'])
            original_maps = assets.prepare(mapped, images=captured['images'])
            surface_replacement.assign(ts, mapped, dict(correction, maps={'ROUGHNESS':new_map, 'HEIGHT':new_map}),
                                       captured['images'], captured['correspondences'])
            corrected_maps = assets.prepare(mapped, images=captured['images'])
            for channel, field in [('ROUGHNESS','image'), ('HEIGHT','detail_image')]:
                a = image_manager.pixels(next(p for p in original_maps['channels'] if p['channel']==channel)[field])
                b = image_manager.pixels(next(p for p in corrected_maps['channels'] if p['channel']==channel)[field])
                assert np.array_equal(a[mask == 0], b[mask == 0]), channel
                assert np.max(abs(a[mask == 1]-b[mask == 1])) > .1, channel
            # Remove only these explicitly imported test images before checking candidate isolation again.
            for value in list(bpy.data.images):
                if value.as_pointer() not in before['images']:
                    bpy.data.images.remove(value, do_unlink=True)
            corrected = tool('preview_proposal', proposal_json=json.dumps(revised))
            assert captured['preview_count'] == 2 and corrected['candidate_digest'] != preview['candidate_digest']
            assert resources() == before and document.digest(ts) == original
            expect_error(lambda: tool('inspect_texture_correspondence', **args), 'superseded')
            tool('critique_candidate', candidate_digest=corrected['candidate_digest'], weaknesses=[],
                reviewed_views=corrected['review_views'], generated_contribution='The edited source remains visibly influential in all views',
                preserved_decisions='The host preserved all source pixels outside the inspected windows')
            tool('propose_changes', proposal_json=json.dumps(revised), candidate_digest=corrected['candidate_digest'],
                visual_review='Reviewed all seven synthetic test views; local source windows changed while the remaining source was preserved.')
            captured['running'] = False
            result = bpy.ops.open_painter.director_apply('EXEC_DEFAULT', True, request_id=captured['id'])
            assert 'FINISHED' in result and len(transactions.history(ts)) == 1
            assert np.array_equal(np.array([v.uv[:] for v in mesh.uv_layers.active.data]), uv)
            layer = ts.layers[0]
            base = next(p for p in layer.channels if p.channel == 'BASE_COLOR')
            assert base.image.packed_file and base.image['director_correction_mode'] == 'LOCAL'
            bpy.ops.wm.save_as_mainfile(filepath=str(OUT/'corrected.blend'))
            report = dict(status='PASS', candidate_views=corrected['review_views'], paired_reversed_seam=True,
                surface_lookup=True, candidate_staleness=True, source_preserved_outside_windows=True,
                scratch_cleanup=True, same_run_correction=True, single_commit=True, provider_used=False,
                aesthetic_verdict=None)
            (OUT/'result.json').write_text(json.dumps(report, indent=2))
            print('DIRECTOR_CORRESPONDENCE_PASS', json.dumps(report), flush=True)


open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)


def main():
    try:
        run()
    except BaseException:
        traceback.print_exc()
        os._exit(1)
    bpy.ops.wm.quit_blender()


bpy.app.timers.register(main, first_interval=2.)
