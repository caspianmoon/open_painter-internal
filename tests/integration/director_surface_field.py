"""Surface-space generation on real geometry: observation identity, reprojection, fusion.

Runs in background Blender. Only the shaded Eevee draw is substituted, by a
deterministic image that encodes each pixel's own observation coordinate, so the
reprojection assertions measure the real correspondence rather than a render.
"""
import base64
import json
import sys
import tempfile
import traceback
from pathlib import Path
from unittest.mock import patch

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.ai import candidates, generated_images, session, surface_observations
from open_painter.ai.viewport import png
from open_painter.authoring import assets, commands, schema, surface_field
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache
from open_painter.geometry import surface_fusion as fusion, surface_observation as observation


def coordinate_bytes(side):
    """A PNG whose pixels encode their own normalized top-left image coordinate."""
    rgba = np.zeros((side, side, 4), np.uint8)
    rgba[..., 0] = np.round((np.arange(side) + .5) / side * 255)[None, :]
    rgba[..., 1] = np.round((np.arange(side) + .5) / side * 255)[:, None]
    rgba[..., 3] = 255
    # Blender's PNG rows run top to bottom, which is this observation's own origin.
    return png(side, side, rgba.tobytes()), rgba


def fake_render(context, scene, frame, side):
    _, rgba = coordinate_bytes(side)
    return rgba


def scalar_bytes(side):
    """A grayscale PNG encoding the image x coordinate, for the data channels."""
    rgba = np.zeros((side, side, 4), np.uint8)
    rgba[..., :3] = np.round((np.arange(side) + .5) / side * 255)[None, :, None]
    rgba[..., 3] = 255
    return png(side, side, rgba.tobytes())


def scalar_entry(directory, side):
    entry = generated_images.store(directory, dict(result=base64.b64encode(scalar_bytes(side)).decode(),
                                                   revisedPrompt='Synthetic scalar fixture, not artwork'))
    entry['state_directory'] = directory
    return entry


def stored(directory, side):
    data, _ = coordinate_bytes(side)
    entry = generated_images.store(directory, dict(result=base64.b64encode(data).decode(),
                                                   revisedPrompt='Synthetic coordinate fixture, not artwork'))
    entry['state_directory'] = directory
    return entry


def build():
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_EEVEE'
    bpy.ops.mesh.primitive_cube_add(size=2)
    obj = bpy.context.object
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.smart_project(island_margin=.04)
    bpy.ops.object.mode_set(mode='OBJECT')
    other = sets.create(scene, 'Unobserved surface', 512)
    ts = sets.create(scene, 'Observed surface', 512)
    sets.assign(scene, ts, obj, 0)
    scene.open_painter.active_texture_set = list(scene.open_painter.texture_sets).index(ts)
    bpy.context.view_layer.update()
    return scene, obj, ts, other


def surface_of(ts, value):
    positions, triangles, uv, normals, seams = surface_field._surface(ts, value)
    return positions, triangles, uv, normals, seams


def run(directory):
    scene, obj, ts, other = build()
    area = next(a for a in bpy.context.window.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    area.spaces.active.shading.type = 'MATERIAL'
    side = 192
    entry = stored(directory, side)

    with bpy.context.temp_override(window=bpy.context.window, area=area, region=region):
        session.request = session.capture(bpy.context, mode='CREATE', scope='SET', prompt='Observe this surface')
    captured = session.request
    captured['context']['image_generation'] = dict(enabled=True)
    captured['images'][entry['image_id']] = entry

    def tool(name, **arguments):
        return session.tool_call(dict(request_id=captured['id'], tool=name, arguments=arguments))

    with patch.object(surface_observations, '_render', fake_render):
        result = tool('capture_surface_observations', texture_set_id=ts.id, max_observations=6, packet_limit=2)
        assert len(result['observations']) >= 6, result['observations']
        assert len(result['detailed_packets']) == 2
        shares = result['surface_coverage']['classes']
        assert abs(sum(item['area_fraction'] for item in shares.values()) - 1) < 1e-5, shares
        assert shares['GOOD']['area_fraction'] > .9, shares
        first = result['observations'][0]
        assert first['projection_kind'] == 'ORTHOGRAPHIC' and first['origin'] == 'TOP_LEFT'
        assert first['resolution'] == [side, side] or first['resolution'][0] > 0
        assert 'AO' in first.get('missing_geometry_maps', []), first.get('missing_geometry_maps')
        packet = tool('inspect_surface_observation', observation_id=result['observations'][-1]['observation_id'])
        roles = {item['id'].rsplit(':', 1)[-1] for item in packet['images']}
        assert {'view', 'mask', 'flow'} <= roles, roles
        assert packet['artist_prompt'] == 'Observe this surface'

        # One observation, one generated image: the fused atlas must reproduce the
        # exact observation coordinate of every texel it covered.
        front = next(item for item in result['observations'] if item['label'] == 'front')
        proposal = dict(version=1, summary='Fuse one observation', commands=[
            dict(op='add_layer', id='new:fused', name='Observed surface', kind='FILL'),
            dict(op='generate_surface_field', layer_id='new:fused', name='Observed appearance',
                 purpose='Appearance authored against the real surface',
                 observations=[dict(observation_id=front['observation_id'], base_color=entry['image_id'])],
                 roughness_range=[.25, .55], height_amplitude=.004)])
        compiled = commands.compile_proposal(ts, proposal, captured['id'], objects=session.objects_for(captured),
                                             images=captured['images'],
                                             observations=candidates.observations(captured))
        layer = compiled['after']['layers'][-1]
        descriptor = layer['channels'][0]['image']
        assert descriptor['$surface_field'] == surface_field.VERSION
        metadata = json.loads(layer['channels'][0]['source_metadata'])
        assert metadata['type'] == 'GENERATED_SURFACE_FIELD'
        assert metadata['observations'] == [front['observation_id']]
        assert metadata['generated_images'] == [entry['image_id']]
        assert metadata['derived_channels'] == ['HEIGHT', 'ROUGHNESS'], metadata['derived_channels']
        assert any(mask['source'] == 'FILL' and mask['image'].get('$surface_field')
                   for mask in layer['masks'])

        prepared = assets.prepare(compiled['after'], images=captured['images'])
        image = prepared['layers'][-1]['channels'][0]['image']
        assert image.packed_file and image.get('director_surface_field')
        assert json.loads(image['director_surface_field_observations']) == [front['observation_id']]
        rgba = np.empty(len(image.pixels), np.float32)
        image.pixels.foreach_get(rgba)
        rgba = rgba.reshape(image.size[1], image.size[0], 4)

        # Rebuild the same correspondence independently and compare coordinates.
        positions, triangles, uv, normals, _ = surface_of(ts, descriptor)
        resolution = int(descriptor['resolution'])
        texels = fusion.texels(uv, positions, triangles, normals, resolution)
        view = surface_observations.view(front['observation_id'], ts.id)
        expected, _ = observation.project(view['matrix'], view['projection'], texels['position'])
        weight, _ = fusion.weights_for(view, texels['position'], texels['normal'],
                                       texel_world=fusion.texel_world_size(texels, positions, triangles, uv))
        covered = weight > 0
        assert covered.mean() > .3, covered.mean()
        sampled = rgba[texels['rows'][covered], texels['cols'][covered]][:, :2]
        # The atlas is linear; the fixture PNG is sRGB, so compare in its own encoding.
        encoded = np.where(sampled <= .0031308, sampled * 12.92,
                           1.055 * np.maximum(sampled, 0) ** (1 / 2.4) - .055)
        difference = np.abs(encoded - expected[covered])
        assert float(np.percentile(difference, 99)) < .02, float(np.percentile(difference, 99))

        coverage_mask = prepared['layers'][-1]['masks'][0]['image']
        assert coverage_mask.get('director_surface_field_channel') == 'COVERAGE'

        # Explicit generated channels ride the same correspondence, with no drift.
        scalar = scalar_entry(directory, side)
        captured['images'][scalar['image_id']] = scalar
        every = dict(version=1, summary='All generated channels', commands=[
            dict(op='add_layer', id='new:all', name='Complete surface', kind='FILL'),
            dict(op='generate_surface_field', layer_id='new:all', name='Complete appearance',
                 purpose='Colour and its own generated data channels', height_amplitude=.01,
                 observations=[dict(observation_id=front['observation_id'], base_color=entry['image_id'],
                                    roughness=scalar['image_id'], height=scalar['image_id'],
                                    metallic=scalar['image_id'])])])
        every_compiled = commands.compile_proposal(ts, every, captured['id'],
            objects=session.objects_for(captured), images=captured['images'],
            observations=candidates.observations(captured))
        every_layer = every_compiled['after']['layers'][-1]
        placed = {prop['channel']: prop for prop in every_layer['channels']}
        assert placed['ROUGHNESS']['image'] and placed['ROUGHNESS']['detail_image'] is None
        assert placed['METALLIC']['image'] and placed['HEIGHT']['detail_image']
        assert abs(placed['HEIGHT']['procedural_height'] - .01) < 1e-9
        assert json.loads(placed['ROUGHNESS']['source_metadata'])['derived_channels'] == []
        keys = {surface_field._key(prop['image'] or prop['detail_image']) for prop in placed.values()
                if prop['image'] or prop['detail_image']}
        assert len(keys) == 1, keys
        every_prepared = assets.prepare(every_compiled['after'], images=captured['images'])
        placed_images = {prop['channel']: (prop['image'] or prop['detail_image'])
                         for prop in every_prepared['layers'][-1]['channels']
                         if prop['image'] or prop['detail_image']}
        rough = np.empty(len(placed_images['ROUGHNESS'].pixels), np.float32)
        placed_images['ROUGHNESS'].pixels.foreach_get(rough)
        rough = rough.reshape(resolution, resolution, 4)
        # Non-Color data is stored as generated; the fused scalar must land on the
        # same texels, at the same observation coordinate, as the colour channel.
        scalar_sample = rough[texels['rows'][covered], texels['cols'][covered], 0]
        assert float(np.percentile(np.abs(scalar_sample - expected[covered][:, 0]), 99)) < .02

        # One direction alone cannot see a closed cube; the rest must be reported.
        report = surface_field.reports()[metadata['field_id']]
        assert report['classes']['MISSING']['fraction'] > .3, report['classes']
        coverage_result = tool('inspect_surface_coverage', texture_set_id=ts.id)
        assert coverage_result['surface_coverage']['classes']['GOOD']['area_fraction'] > .9
        assert coverage_result['fused_fields'][metadata['field_id']]['classes']['MISSING']['fraction'] > .3
        surface_field.forget()

        # A bounded region only writes its own triangles.
        whole_region = tool('list_regions', texture_set_id=ts.id)['regions'][0]
        query = tool('query_region', texture_set_id=ts.id, object_id=whole_region['object_id'],
                     slot_index=0, space='WORLD', bounds_min=[-9, -9, -9], bounds_max=[9, 9, 9],
                     selection='CENTROID', normal_direction=[0, 0, 1], min_dot=.9)
        region_id = query['id']
        bounded = tool('capture_surface_observations', texture_set_id=ts.id, region_id=region_id,
                       directions=['front'], complete_coverage=False, packet_limit=1)
        bounded_id = bounded['observations'][0]['observation_id']
        # The same named direction over a different region is a different camera.
        assert bounded_id != front['observation_id']
        assert surface_observations.registry(captured['id'])[front['observation_id']]['region_id'] == ''
        region_proposal = dict(version=1, summary='Bounded fusion', commands=[
            dict(op='add_layer', id='new:bounded', name='Bounded surface', kind='FILL'),
            dict(op='generate_surface_field', layer_id='new:bounded', name='Bounded appearance',
                 purpose='Only the selected geometry', region_id=region_id,
                 observations=[dict(observation_id=bounded_id, base_color=entry['image_id'])])])
        bounded_compiled = commands.compile_proposal(ts, region_proposal, captured['id'],
            objects=session.objects_for(captured), images=captured['images'],
            observations=candidates.observations(captured))
        bounded_layer = bounded_compiled['after']['layers'][-1]
        assert any(mask['source'] == 'MESH' for mask in bounded_layer['masks'])
        bounded_descriptor = bounded_layer['channels'][0]['image']
        assert bounded_descriptor['region_id'] == region_id and bounded_descriptor['region_faces']
        whole = surface_of(ts, descriptor)[1]
        part = surface_of(ts, bounded_descriptor)[1]
        assert 0 < len(part) < len(whole), (len(part), len(whole))

        # A generated image must keep the observation's square framing.
        stretched = np.zeros((side // 2, side, 4), np.uint8)
        stretched[..., 3] = 255
        wide = generated_images.store(directory, dict(
            result=base64.b64encode(png(side, side // 2, stretched.tobytes())).decode(),
            revisedPrompt='Synthetic non-square fixture, not artwork'))
        wide['state_directory'] = directory
        captured['images'][wide['image_id']] = wide

        # A field cannot fuse an unknown, foreign or unregistered observation.
        for broken, message in (
                (dict(observation_id=front['observation_id'], base_color=wide['image_id']), 'square framing'),
                (dict(observation_id='observation:missing', base_color=entry['image_id']), 'Capture surface observations'),
                (dict(observation_id=front['observation_id'], base_color='generated:' + 'f' * 64), 'list_generated_images')):
            attempt = dict(version=1, summary='Invalid fusion', commands=[
                dict(op='add_layer', id='new:bad', name='Bad', kind='FILL'),
                dict(op='generate_surface_field', layer_id='new:bad', name='Bad', purpose='Invalid',
                     observations=[broken])])
            try:
                commands.compile_proposal(ts, attempt, captured['id'], objects=session.objects_for(captured),
                                          images=captured['images'], observations=candidates.observations(captured))
                raise AssertionError('Accepted an invalid observation: ' + json.dumps(broken))
            except schema.AuthoringError as exc:
                assert message in str(exc), str(exc)

        # A whole-surface field cannot silently consume a region observation.
        mixed = dict(version=1, summary='Region mismatch', commands=[
            dict(op='add_layer', id='new:mixed', name='Mixed', kind='FILL'),
            dict(op='generate_surface_field', layer_id='new:mixed', name='Mixed', purpose='Mismatch',
                 observations=[dict(observation_id=bounded_id, base_color=entry['image_id'])])])
        try:
            commands.compile_proposal(ts, mixed, captured['id'], objects=session.objects_for(captured),
                                      images=captured['images'], observations=candidates.observations(captured))
            raise AssertionError('Accepted an observation captured for another region')
        except schema.AuthoringError as exc:
            assert 'region' in str(exc), str(exc)

        # A corrective observation is framed on the requested surface only.
        corrective = tool('inspect_surface_area', texture_set_id=ts.id, center=[0., 0., -1.], radius=.9)
        assert corrective['observation_id'] in surface_observations.registry(captured['id'])
        assert corrective['world_per_pixel'] < first['world_per_pixel'], (corrective['world_per_pixel'],
                                                                         first['world_per_pixel'])
        assert 'correction' in corrective

        try:
            tool('capture_surface_observations', texture_set_id=other.id)
            raise AssertionError('Observed a material outside the instruction scope')
        except schema.AuthoringError as exc:
            assert 'scope' in str(exc), str(exc)

    # The fused field evaluates as an ordinary material source, with nothing
    # special in the evaluator: apply it and read the composed channel output.
    from open_painter.authoring import document
    from open_painter.core import image_manager
    from open_painter.evaluation.evaluator import LayerEvaluator
    document.apply(ts, prepared)
    LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
    composed = image_manager.pixels(image_manager.output(ts, 'BASE_COLOR').image)
    assert composed.shape[0] == int(ts.resolution)
    probe = np.argmax(weight)
    row = int(texels['rows'][probe] * composed.shape[0] / resolution)
    column = int(texels['cols'][probe] * composed.shape[1] / resolution)
    evaluated = composed[row, column, :2]
    encoded_probe = np.where(evaluated <= .0031308, evaluated * 12.92,
                 1.055 * np.maximum(evaluated, 0) ** (1 / 2.4) - .055)
    assert float(np.abs(encoded_probe - expected[probe]).max()) < .05, (encoded_probe, expected[probe])

    session.release(captured)
    assert not surface_observations.registry(captured['id'])
    print('director_surface_field: OK')


open_painter.register()
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)

try:
    with tempfile.TemporaryDirectory(prefix='painter-observations-') as directory:
        run(directory)
except BaseException:
    traceback.print_exc()
    sys.exit(1)
