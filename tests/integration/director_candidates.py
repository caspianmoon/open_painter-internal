"""Real Eevee candidate, correction, isolation and commit acceptance checks.

Run with visible Blender (GPUOffScreen needs a graphics context). No provider.
The scripted actor deliberately proposes metallic rubber, inspects actual pixels
and channel diagnostics, corrects it, and commits once through the public tools.
"""
import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback
import time
from unittest.mock import patch

import bpy
from mathutils import Quaternion

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.ai import candidates, session
from open_painter.authoring import document, schema, transactions
from open_painter.core import texture_set_manager as sets, layer_stack
from open_painter.evaluation import cache, shader_preview


def proposal(metallic):
    return dict(version=1, summary='Editable yellow rubber', commands=[
        dict(op='add_layer', id='new:rubber', name='Rubber', kind='FILL'),
        dict(op='set_channel', layer_id='new:rubber', channel='BASE_COLOR', values=dict(color=[.55,.27,.012,1.])),
        dict(op='set_channel', layer_id='new:rubber', channel='ROUGHNESS', values=dict(value=.66)),
        dict(op='set_channel', layer_id='new:rubber', channel='METALLIC', values=dict(value=metallic))])


def resources():
    return {key: {value.as_pointer() for value in getattr(bpy.data, key)} for key in candidates._STORES}


def run():
    scene = bpy.data.scenes.new('Candidate acceptance')
    bpy.context.window.scene = scene
    scene.render.engine = 'BLENDER_EEVEE'
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.object
    ts = sets.create(scene, 'Artist material', 512)
    sets.assign(scene, ts, obj, 0)
    scene.open_painter.active_texture_set = 0
    area = next(area for area in bpy.context.screen.areas if area.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    space = area.spaces.active
    space.shading.type = 'MATERIAL'
    space.region_3d.view_rotation = Quaternion((.927, .2, .12, .25)).normalized()
    space.region_3d.view_distance = 6
    space.region_3d.view_perspective = 'PERSP'
    bpy.context.view_layer.update()
    with bpy.context.temp_override(area=area, region=region):
        space.region_3d.update()
        shader_preview.tick(scene, ts)
        session.request = session.capture(bpy.context, mode='REFINE', scope='SET', prompt='Make this yellow rubber')
        captured = session.request
        original = document.canonical(document.snapshot(ts))
        mesh = obj.data.as_pointer()
        slot = obj.material_slots[0].material.as_pointer()
        initial_resources = resources()

        def tool(name, **arguments):
            return session.tool_call(dict(request_id=captured['id'], tool=name, arguments=arguments))

        wrong = tool('preview_proposal', proposal_json=json.dumps(proposal(.95)))
        assert wrong['channels'][ts.id]['channels']['METALLIC']['mean'][0] > .94
        assert len(wrong['_images']) == 13
        assert {'candidate:top', 'candidate:bottom'} <= {image['id'] for image in wrong['_images']}
        out = ROOT / '.blender-test' / 'director-candidate-acceptance'
        out.mkdir(parents=True, exist_ok=True)
        for image in wrong['_images'][:2]:
            (out / (image['id'].replace(':','_') + '_wrong.png')).write_bytes(base64.b64decode(image['url'].split(',',1)[1]))
        assert document.canonical(document.snapshot(ts)) == original
        assert resources() == initial_resources, 'Candidate leaked Blender IDs'
        assert obj.data.as_pointer() == mesh and obj.material_slots[0].material.as_pointer() == slot
        assert not transactions.history(ts), 'Candidate created artist history'
        try:
            tool('propose_changes', proposal_json=json.dumps(proposal(0)), candidate_digest=wrong['candidate_digest'], visual_review='The rubber was incorrectly metallic; this is a changed unreviewed proposal.')
            raise AssertionError('Unreviewed changed material accepted')
        except schema.AuthoringError:
            pass
        corrected = tool('preview_proposal', proposal_json=json.dumps(proposal(0)))
        assert corrected['channels'][ts.id]['channels']['METALLIC']['max'] == 0
        assert corrected['candidate_digest'] != wrong['candidate_digest']
        assert resources() == initial_resources
        comparison = tool('compare_candidates', previous_digest=wrong['candidate_digest'], current_digest=corrected['candidate_digest'])
        assert len(comparison['_images']) == 4
        assert comparison['previous']['channels'][ts.id]['channels']['METALLIC']['mean'][0] > .94
        assert comparison['current']['channels'][ts.id]['channels']['METALLIC']['max'] == 0
        # The previous candidate can never be mistaken for the current preview.
        try:
            tool('inspect_candidate', candidate_digest=wrong['candidate_digest'])
            raise AssertionError('Superseded candidate accepted')
        except schema.AuthoringError:
            pass
        for image in corrected['_images'][:2]:
            (out / (image['id'].replace(':','_') + '_correct.png')).write_bytes(base64.b64decode(image['url'].split(',',1)[1]))
        tool('propose_changes', proposal_json=json.dumps(proposal(0)), candidate_digest=corrected['candidate_digest'],
             visual_review='Eevee views show yellow rubber, roughness is 0.66 and metallic is now exactly zero. Geometry and non-target state remain unchanged.')
        captured['running'] = False
        result = bpy.ops.open_painter.director_apply('EXEC_DEFAULT', True, request_id=captured['id'])
        assert 'FINISHED' in result and len(transactions.history(ts)) == 1
        assert session.state['outcome']['execution'] == 'APPLIED'
        assert obj.data.as_pointer() == mesh and obj.material_slots[0].material.as_pointer() == slot
        # The final authored channels equal the inspected candidate, including
        # exact valid-UV float buffers, before and after save/reopen.
        final, _ = candidates.channel_diagnostics(scene, {ts.id}, time.monotonic() + 30)
        assert final == corrected['channels'], (final, corrected['channels'])
        bpy.ops.wm.save_as_mainfile(filepath=str(out / 'final.blend'))
        saved_id = ts.id
        bpy.ops.wm.open_mainfile(filepath=str(out / 'final.blend'))
        scene = bpy.context.scene
        ts = next(item for item in scene.open_painter.texture_sets if item.id == saved_id)
        reopened, _ = candidates.channel_diagnostics(scene, {ts.id}, time.monotonic() + 30)
        assert reopened == final, 'Reopened channels differ from reviewed candidate'
        records = dict(status='PASS', candidate_count=2, commits=1, wrong=wrong['channels'], corrected=corrected['channels'])
        (out / 'result.json').write_text(json.dumps(records, indent=2))
        print('DIRECTOR_CANDIDATE_CORRECTION_OK', str(out), flush=True)


def lifecycle():
    window = bpy.context.window_manager.windows[0]
    scene = window.scene
    ts = scene.open_painter.texture_sets[0]
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=window, scene=scene, area=area, region=region):
        layer = ts.layers[-1]
        image = bpy.data.images.new('Artist painted source', width=8, height=8)
        image.pixels[:] = [.3,.2,.1,1.] * 64
        channel = layer_stack.channel_for(layer, 'BASE_COLOR')
        channel.image = image
        shader_preview.tick(scene, ts)
        change = dict(version=1, summary='Adjust existing roughness', commands=[
            dict(op='set_channel', layer_id=layer.id, channel='ROUGHNESS', values=dict(value=.7))])
        session.request = session.capture(bpy.context, mode='PLAN', scope='SET', prompt='Plan a roughness change')
        captured = session.request
        before = document.canonical(document.snapshot(ts))
        counts = resources()
        # Any exception after scratch mutation cleans IDs and leaves artist data.
        with patch.object(candidates, 'views', side_effect=RuntimeError('injected render failure')):
            try:
                candidates.preview(bpy.context, ts, captured, change)
                raise AssertionError('Injected preview failure did not propagate')
            except RuntimeError as exc:
                assert str(exc) == 'injected render failure'
        assert resources() == counts and document.canonical(document.snapshot(ts)) == before
        assert not captured['candidate']
        reviewed = candidates.preview(bpy.context, ts, captured, change)
        pixels = image.pixels[:]
        image.pixels[0] = .8
        try:
            candidates.reviewed(bpy.context, ts, captured, change, reviewed['candidate_digest'])
            raise AssertionError('Untracked source-pixel edit did not invalidate candidate')
        except schema.AuthoringError:
            pass
        image.pixels[:] = pixels
        # A view/environment change invalidates review even without a material edit.
        exposure = scene.view_settings.exposure
        scene.view_settings.exposure = exposure + .5
        try:
            candidates.reviewed(bpy.context, ts, captured, change, reviewed['candidate_digest'])
            raise AssertionError('Changed viewing conditions did not invalidate candidate')
        except schema.AuthoringError:
            pass
        scene.view_settings.exposure = exposure
        event = dict(request_id=captured['id'], tool='propose_changes', arguments=dict(
            proposal_json=json.dumps(change), candidate_digest=reviewed['candidate_digest'],
            visual_review='The artist color is preserved; roughness channel and Eevee response reflect the planned 0.7 value.'))
        session.tool_call(event)
        session.completed(dict(request_id=captured['id'], status='completed'))
        assert document.canonical(document.snapshot(ts)) == before, 'PLAN applied a material'
        assert session.request['draft'] and not session.request['running']
        session.stop()
        assert session.request is None and session.state['outcome']['execution'] == 'CANCELLED'
        assert resources() == counts and document.canonical(document.snapshot(ts)) == before
        session.request = session.capture(bpy.context, mode='EXPLAIN', scope='SET', prompt='Explain rubber')
        try:
            session.tool_call(dict(request_id=session.request['id'], tool='preview_proposal', arguments=dict(proposal_json=json.dumps(change))))
            raise AssertionError('EXPLAIN authored a candidate')
        except schema.AuthoringError:
            pass
        session.stop()
        print('DIRECTOR_CANDIDATE_LIFECYCLE_OK', flush=True)


def shared_slots():
    window = bpy.context.window_manager.windows[0]
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=window, area=area, region=region):
        scene = window.scene
        original = scene.open_painter.texture_sets[0]
        obj = original.assignments[0].object
        original_id = original.id
        original_layer = original.layers[-1].id
        obj.data.materials.append(bpy.data.materials.new('Artist second slot'))
        for face in obj.data.polygons:
            face.material_index = face.index % 2
        other = sets.create(scene, 'Second assigned finish', 512)
        other_id = other.id
        sets.assign(scene, other, obj, 1)
        original = next(ts for ts in scene.open_painter.texture_sets if ts.id == original_id)
        collection = bpy.data.collections.new('Artist material region collection')
        scene.collection.children.link(collection)
        collection.objects.link(obj)
        layer = next(layer for layer in original.layers if layer.id == original_layer)
        mask = layer.masks.add()
        mask.id, mask.source = 'artist-collection-mask', 'MESH'
        mask.mesh.kind, mask.mesh.collection = 'COLLECTION', collection
        scene.open_painter.active_texture_set = 0
        for item in scene.open_painter.texture_sets:
            shader_preview.tick(scene, item)
        before = {item.id: document.canonical(document.snapshot(item)) for item in scene.open_painter.texture_sets}
        slots = [slot.material.as_pointer() for slot in obj.material_slots]
        counts = resources()
        session.request = session.capture(bpy.context, mode='CREATE', scope='SCENE', prompt='Coordinate both finishes')
        captured = session.request
        captured['visual_settings'] = dict(send_view=False, send_maps=False)
        for item in scene.open_painter.texture_sets:
            session.tool_call(dict(request_id=captured['id'], tool='get_material_context', arguments=dict(texture_set_id=item.id)))
        coordinated = dict(version=1, summary='Both assigned finishes', targets=[
            dict(texture_set_id=original_id, proposal=dict(version=1, summary='Rubber roughness', commands=[
                dict(op='set_channel', layer_id=original_layer, channel='ROUGHNESS', values=dict(value=.72))])),
            dict(texture_set_id=other_id, proposal=proposal(.0))])
        # Cross-set layer IDs cannot escape a per-target proposal scope.
        bad = json.loads(json.dumps(coordinated))
        bad['targets'][1]['proposal'] = bad['targets'][0]['proposal']
        try:
            candidates.preview(bpy.context, original, captured, bad)
            raise AssertionError('Foreign layer ID accepted in another texture set')
        except schema.AuthoringError:
            pass
        result = candidates.preview(bpy.context, original, captured, coordinated)
        assert set(result['channels']) == {original_id, other_id}
        assert {i['id'] for i in result['_images'] if i['id'].startswith('candidate:target:')} == {
            'candidate:target:' + original_id, 'candidate:target:' + other_id}
        assert resources() == counts
        assert [slot.material.as_pointer() for slot in obj.material_slots] == slots
        assert {item.id: document.canonical(document.snapshot(item)) for item in scene.open_painter.texture_sets} == before
        assert result['channels'][original_id]['channels']['ROUGHNESS']['max'] >= .719, 'Collection mask lost candidate object membership'
        captured['preview_count'] = candidates.MAX_PREVIEWS
        try:
            candidates.preview(bpy.context, original, captured, coordinated)
            raise AssertionError('Preview cap ignored')
        except schema.AuthoringError as exc:
            assert 'budget' in str(exc)
        session.stop()
        print('DIRECTOR_CANDIDATE_SHARED_SLOTS_OK', flush=True)


def scratch_scene_inputs():
    """Animated non-target geometry and non-mesh IDs stay isolated and invalidate review."""
    window = bpy.context.window_manager.windows[0]
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=window, area=area, region=region):
        scene = window.scene
        rig = bpy.data.objects.new('Artist rig', bpy.data.armatures.new('Artist armature data'))
        scene.collection.objects.link(rig)
        rig.location.z = 0
        rig.keyframe_insert('location', frame=10)
        rig.location.z = 2
        rig.keyframe_insert('location', frame=30)
        bpy.ops.mesh.primitive_cube_add()
        floor = bpy.context.object
        floor.name = 'Unmanaged animated occluder'
        constraint = floor.constraints.new('COPY_LOCATION')
        constraint.target = rig
        collection = bpy.data.collections.new('Artist visibility group')
        scene.collection.children.link(collection)
        scene.frame_set(20, subframe=.25)
        bpy.context.view_layer.update()
        captured = session.capture(bpy.context, mode='PLAN', scope='SET', prompt='Inspect material')
        before = resources()
        original_matrix = floor.matrix_world.copy()
        with candidates.scratch(bpy.context, [], {}) as temporary:
            assert temporary.frame_current == 20 and abs(temporary.frame_subframe-.25) < 1e-6
            copied_floor = next(o for o in temporary.objects if o.name.startswith(floor.name))
            copied_rig = next(o for o in temporary.objects if o.type == 'ARMATURE')
            assert copied_rig.data != rig.data
            assert copied_floor.constraints[0].target == copied_rig
            assert max(abs(a-b) for row_a,row_b in zip(copied_floor.matrix_world, original_matrix)
                       for a,b in zip(row_a,row_b)) < 1e-6, 'Scratch preview evaluated another animation frame'
        assert resources() == before, 'Non-mesh object data leaked from candidate'
        baseline = candidates.identity(bpy.context, captured, [])
        layer = next(c for c in bpy.context.view_layer.layer_collection.children if c.collection == collection)
        layer.holdout = True
        assert candidates.identity(bpy.context, captured, []) != baseline
        layer.holdout = False
        assert candidates.identity(bpy.context, captured, []) == baseline
        floor.hide_render = True
        assert candidates.identity(bpy.context, captured, []) != baseline
        floor.hide_render = False
        floor.data.vertices[0].co.x += .5
        floor.data.update()
        bpy.context.view_layer.update()
        assert candidates.identity(bpy.context, captured, []) != baseline, 'Unmanaged geometry change did not invalidate candidate'
        assert scene.frame_current == 20 and abs(scene.frame_subframe-.25) < 1e-6
        print('DIRECTOR_CANDIDATE_SCENE_INPUTS_OK', flush=True)


def generated_review_recovery():
    """A stale generated preview must be replaceable without approving stale images."""
    import numpy as np
    from open_painter.ai import generated_images
    from open_painter.ai.viewport import png
    scene = bpy.data.scenes.new('Generated review recovery')
    bpy.context.window.scene = scene
    scene.render.engine = 'BLENDER_EEVEE'
    bpy.ops.mesh.primitive_cube_add()
    ts = sets.create(scene, 'Recovery material', 512)
    sets.assign(scene, ts, bpy.context.object, 0)
    area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    area.spaces.active.shading.type = 'MATERIAL'
    with tempfile.TemporaryDirectory() as directory, bpy.context.temp_override(area=area, region=region):
        camera = bpy.data.objects.new('Review camera', bpy.data.cameras.new('Review camera'))
        scene.collection.objects.link(camera)
        camera.location = (4, -6, 3)
        camera.rotation_euler = (-camera.location).to_track_quat('-Z', 'Y').to_euler()
        scene.camera = camera
        view = area.spaces.active.region_3d
        view.view_perspective = 'CAMERA'
        bpy.context.view_layer.update()
        view.update()
        shader_preview.flush(scene, ts)
        captured = session.capture(bpy.context, mode='CREATE', scope='SET', prompt='Generated review regression')
        session.request = captured
        captured['context']['image_generation'] = dict(enabled=True)
        captured['surface_plan'] = dict(components=[])
        pixels = np.ones((32, 32, 4), np.uint8) * 255
        pixels[:, :16, :3] = (30, 120, 60)
        pixels[:, 16:, :3] = (80, 20, 100)
        entry = generated_images.store(directory, dict(result=base64.b64encode(png(32, 32, pixels.tobytes())).decode()))
        entry['state_directory'] = directory
        captured['images'][entry['image_id']] = entry
        change = dict(version=1, summary='Generated review recovery', commands=[
            dict(op='add_layer', id='new:skin', name='Skin', kind='FILL'),
            dict(op='assign_generated_surface', layer_id='new:skin', image_id=entry['image_id'],
                 name='Skin detail', purpose='Synthetic generated surface regression',
                 roughness_range=[.4, .6], height_amplitude=0, projection='UV')])

        def tool(name, **arguments):
            return session.tool_call(dict(request_id=captured['id'], tool=name, arguments=arguments))

        first = tool('preview_proposal', proposal_json=json.dumps(change))
        # Camera-border zoom updates Blender's display projection cache, but
        # not the independently framed evidence camera. The old identity hashed
        # this cache and could reject the dragon after its final allowed preview.
        old_projection = view.window_matrix.copy()
        view.view_camera_zoom += 17
        view.update()
        assert view.window_matrix != old_projection
        tool('inspect_candidate', candidate_digest=first['candidate_digest'])
        # A real camera lens edit still invalidates the reviewed projection.
        old_lens = camera.data.lens
        camera.data.lens = old_lens * 1.1
        try:
            tool('inspect_candidate', candidate_digest=first['candidate_digest'])
            raise AssertionError('Changed evidence lens accepted')
        except schema.AuthoringError as exc:
            assert 'projection' in str(exc)
        camera.data.lens = old_lens
        tool('inspect_candidate', candidate_digest=first['candidate_digest'])
        # Wrong IDs and edited proposals must not invalidate a good candidate.
        for name, arguments in (
            ('inspect_candidate', dict(candidate_digest='0' * 64)),
        ):
            try:
                tool(name, **arguments)
                raise AssertionError('Wrong candidate accepted')
            except schema.AuthoringError as exc:
                assert 'current candidate_digest' in str(exc)
        assert not captured['candidate'].get('invalidated')
        renamed = dict(change, summary='Equivalent material with a revised description')
        candidates.reviewed(bpy.context, ts, captured, renamed, first['candidate_digest'])
        edited = dict(change, commands=[*change['commands'], dict(op='set_channel',
            layer_id='new:skin', channel='METALLIC', values=dict(value=.8))])
        try:
            candidates.reviewed(bpy.context, ts, captured, edited, first['candidate_digest'])
            raise AssertionError('Changed material accepted')
        except schema.AuthoringError as exc:
            assert 'Proposal differs' in str(exc)
        assert not captured['candidate'].get('invalidated')
        # Change a real visual input, without changing the authoring document.
        scene.view_settings.exposure += .25
        try:
            tool('inspect_candidate', candidate_digest=first['candidate_digest'])
            raise AssertionError('Changed lighting accepted')
        except schema.AuthoringError as exc:
            assert 'color' in str(exc) and 'preview_proposal' in str(exc)
        assert captured['candidate']['invalidated'] == ['color']
        # No critique of stale images; the identical material can be re-rendered.
        fresh = tool('preview_proposal', proposal_json=json.dumps(change))
        assert fresh['candidate_digest'] != first['candidate_digest']
        assert fresh['material_digest'] == first['material_digest']
        tool('inspect_candidate', candidate_digest=fresh['candidate_digest'])
        tool('critique_candidate', candidate_digest=fresh['candidate_digest'], weaknesses=[],
             reviewed_views=fresh['review_views'],
             generated_contribution='Generated color bands are visible in the fresh render.',
             preserved_decisions='Preserve both color bands and the rough dielectric response.')
        tool('propose_changes', proposal_json=json.dumps(change), candidate_digest=fresh['candidate_digest'],
             visual_review='Fresh Eevee evidence includes the changed exposure and generated color bands.')
        captured['running'] = False
        result = bpy.ops.open_painter.director_apply('EXEC_DEFAULT', True, request_id=captured['id'])
        assert 'FINISHED' in result and len(transactions.history(ts)) == 1
        print('DIRECTOR_GENERATED_REVIEW_RECOVERY_OK', flush=True)


open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)


def main():
    try:
        run()
        lifecycle()
        shared_slots()
        scratch_scene_inputs()
        with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
            generated_review_recovery()
    except BaseException:
        traceback.print_exc()
        os._exit(1)
    bpy.ops.wm.quit_blender()


bpy.app.timers.register(main, first_interval=2.)
