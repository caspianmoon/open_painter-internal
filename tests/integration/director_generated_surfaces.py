"""Real Eevee source coherence, bypass, critique, cleanup and packed persistence."""
import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback
from unittest.mock import patch

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.ai import candidates, generated_images, protocol, session, surface_diagnostics
from open_painter.ai.viewport import png
from open_painter.authoring import document, generated_surfaces, schema
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache, shader_preview


def resources():
    return {key: {v.as_pointer() for v in getattr(bpy.data, key)} for key in candidates._STORES}


def run(directory):
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_EEVEE'
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.object
    ts = sets.create(scene, 'Generated test metal', 512)
    sets.assign(scene, ts, obj, 0)
    scene.open_painter.active_texture_set = 0
    area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    area.spaces.active.shading.type = 'MATERIAL'
    bpy.context.view_layer.update()
    y, x = np.indices((128, 128))
    rgba = np.ones((128, 128, 4), np.uint8)*255
    rgba[..., :3] = (30 + (x // 9 % 2)*80 + (y // 17 % 2)*70)[..., None]
    data = png(128, 128, rgba.tobytes())
    entry = generated_images.store(directory, dict(result=base64.b64encode(data).decode(), revisedPrompt='Synthetic fixture, not AI artwork'))
    entry['state_directory'] = directory
    with bpy.context.temp_override(area=area, region=region):
        shader_preview.tick(scene, ts)
        session.request = session.capture(bpy.context, mode='REFINE', scope='SET', prompt='Test generated sources')
        captured = session.request
        captured['context']['image_generation'] = dict(enabled=True)
        captured['correction_enabled'] = True
        captured['images'][entry['image_id']] = entry
        original = document.canonical(document.snapshot(ts))
        counts = resources()
        def tool(name, **args):
            return session.tool_call(dict(request_id=captured['id'], tool=name, arguments=args))
        def proposal(roughness):
            return dict(version=1, summary='Generated machined metal', commands=[
                dict(op='add_layer', id='new:metal', name='Metal', kind='FILL'),
                dict(op='assign_generated_surface', layer_id='new:metal', image_id=entry['image_id'],
                     name='Machining', purpose='Aligned manufacturing detail', roughness_range=roughness,
                     height_amplitude=.002, projection='TRIPLANAR', scale=[2, 2]),
                dict(op='set_channel', layer_id='new:metal', channel='METALLIC', values=dict(value=.8))])
        # Generated fictional details are separate editable layers with real alpha/region masks.
        region_id = tool('list_regions', texture_set_id=ts.id)['regions'][0]['id']
        detail = dict(version=1, summary='Fictional detail', commands=[dict(op='add_generated_detail',
            id='new:mark', name='Fictional maintenance mark', image_id=entry['image_id'], region_id=region_id,
            purpose='Test editable source and placement')])
        _, compiled = candidates.compile_proposal(scene, ts, captured, detail)
        with candidates.scratch(bpy.context, compiled, captured['images']) as scratch:
            authored = scratch.open_painter.texture_sets[0].layers[-1]
            assert authored.masks[0].image.get('director_surface_derivation').split(':')[2] == 'ALPHA'
            assert authored.masks[1].source == 'MESH'
            assert authored.channels[0].tiling == 'NONE'
        assert resources() == counts
        first = proposal([.2, .6])
        try:
            tool('preview_proposal', proposal_json=json.dumps(first))
            raise AssertionError('Missing plan accepted')
        except schema.AuthoringError:
            pass
        tool('plan_surface', object_interpretation='Manufactured test component', art_direction='Worn industrial metal',
             components=[dict(name='Body', texture_set_id=ts.id, substrate='Machined steel substrate',
             history='Repeated handling and storage', macro='Uneven finish variation', meso='Machining and handling scuffs',
             micro='Fine surface grain variation', generated_source='One aligned surface source',
             mapping='Triplanar twice per world unit', authored_detail='No markings needed on this fixture')])
        # Failure after scratch authoring must not leak data or mutate the artist.
        with patch.object(candidates, 'views', side_effect=RuntimeError('injected failure')):
            try:
                tool('preview_proposal', proposal_json=json.dumps(first))
                raise AssertionError('Injected failure swallowed')
            except RuntimeError:
                pass
        assert resources() == counts
        assert document.canonical(document.snapshot(ts)) == original
        captured['preview_count'] = 0  # Test actor, no inference; restore the test budget.
        first_result = tool('preview_proposal', proposal_json=json.dumps(first))
        assert resources() == counts and document.canonical(document.snapshot(ts)) == original
        assert len(protocol.encode(dict(result=first_result))) < protocol.MAX_MESSAGE
        ids = {i['id'] for i in first_result['_images']}
        assert {'candidate:front', 'candidate:back', 'candidate:left', 'candidate:right', 'candidate:close',
                'candidate:bypass:front', 'candidate:bypass:close'} <= ids
        evidence = first_result['generated_contribution']
        assert evidence['unique_source_count'] == 1
        for key in ('BASE_COLOR', 'ROUGHNESS', 'HEIGHT'):
            assert evidence['materials'][ts.id]['channels'][key]['maximum_difference'] > 0, key
        critique = dict(candidate_digest=first_result['candidate_digest'], weaknesses=[dict(component='Body',
            observation='The alternating source has excessive roughness contrast',
            correction='Narrow the roughness range while preserving machining color', evidence_image_id=ts.id+':ROUGHNESS',
            category='APPEARANCE', scope='GLOBAL', correction_type='TEXTURE_SOURCE')],
            generated_contribution='Bypass removes visible color and roughness structure', preserved_decisions='Preserve color source and projected scale',
            reviewed_views=first_result['review_views'])
        tool('critique_candidate', **critique)
        try:
            tool('propose_changes', proposal_json=json.dumps(first), candidate_digest=first_result['candidate_digest'], visual_review='The contrast needs correction')
            raise AssertionError('Uncorrected candidate submitted')
        except schema.AuthoringError:
            pass
        second = proposal([.4, .55])
        result = tool('preview_proposal', proposal_json=json.dumps(second))
        tool('critique_candidate', **dict(critique, candidate_digest=result['candidate_digest'], weaknesses=[],
                                          reviewed_views=result['review_views']))
        tool('propose_changes', proposal_json=json.dumps(second), candidate_digest=result['candidate_digest'],
             visual_review='The evaluated roughness range is narrower; original color detail is retained in the Eevee preview')
        captured['running'] = False
        assert 'FINISHED' in bpy.ops.open_painter.director_apply('EXEC_DEFAULT', True, request_id=captured['id'])
        layer = ts.layers[-1]
        channels = {p.channel: p for p in layer.channels}
        assert channels['ROUGHNESS'].detail_image == channels['HEIGHT'].detail_image
        assert channels['BASE_COLOR'].image != channels['HEIGHT'].detail_image
        assert channels['BASE_COLOR'].scale[:] == channels['ROUGHNESS'].scale[:]
        assert not json.loads(channels['ROUGHNESS'].source_metadata)['calibrated_bundle']
        for image in (channels['BASE_COLOR'].image, channels['HEIGHT'].detail_image):
            assert image.packed_file
        out = ROOT / '.blender-test/director-generated-surfaces'
        out.mkdir(parents=True, exist_ok=True)
        for frame in result['_images']:
            (out / (frame['id'].replace(':', '_')+'.png')).write_bytes(base64.b64decode(frame['url'].split(',', 1)[1]))
        (out / 'result.json').write_text(json.dumps(dict(status='PASS', evidence=evidence), indent=2))
        final, _ = candidates.channel_diagnostics(scene, {ts.id}, time.monotonic()+60)
        identifier = ts.id
        bpy.ops.wm.save_as_mainfile(filepath=str(out / 'final.blend'))
        bpy.ops.wm.open_mainfile(filepath=str(out / 'final.blend'))
        reopened, _ = candidates.channel_diagnostics(bpy.context.scene, {identifier}, time.monotonic()+60)
        assert reopened == final, 'Packed aligned source changed after reopening'


def numerical():
    valid = np.ones((64, 64), bool)
    constant = np.ones((64, 64, 4), np.float32)*.4
    result = surface_diagnostics.variation(constant, valid)
    assert result['warnings'] and result['frequency_band_rms']['micro'] < 1e-6
    structured = constant.copy()
    structured[::3, :, :3] = .8
    assert surface_diagnostics.variation(structured, valid)['frequency_band_rms']['micro'] > .05
    assert surface_diagnostics.contribution(constant, constant, valid)['warnings']
    assert surface_diagnostics.contribution(structured, constant, valid)['changed_texel_fraction'] > .3
    np.testing.assert_allclose(generated_surfaces.scalar_detail(constant), .5, atol=1e-5)
    assert generated_surfaces.scalar_detail(structured).std() > .1
    # Tiny UV island missed by the initial downsampling grid remains finite.
    sparse = np.zeros((513, 513), bool)
    sparse[1, 1] = True
    assert np.isfinite(surface_diagnostics.variation(np.ones((513, 513, 4)), sparse)['frequency_band_rms']['micro'])


open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)


def main():
    try:
        numerical()
        with tempfile.TemporaryDirectory() as directory:
            run(directory)
        print('DIRECTOR_GENERATED_SURFACES_OK', flush=True)
    except BaseException:
        traceback.print_exc()
        os._exit(1)
    bpy.ops.wm.quit_blender()


bpy.app.timers.register(main, first_interval=2.)
