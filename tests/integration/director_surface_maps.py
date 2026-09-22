"""Scalar-map registration, relief normals, persistence and exact refinement undo."""
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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.ai import candidates, generated_images, session
from open_painter.ai.viewport import png
from open_painter.authoring import document, regions, schema, surface_maps, surface_projection, transactions
from open_painter.core import image_manager, texture_set_manager as sets
from open_painter.evaluation import channel_eval, shader_preview
from open_painter.evaluation.snapshots import capture
from open_painter.export.surface_normal import combined
from open_painter.painting.projection import geometry


def run():
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.object
    scene = bpy.context.scene
    ts = sets.create(scene, 'Map regression', 512)
    sets.assign(scene, ts, obj, 0)
    bpy.context.view_layer.update()
    captured = session.capture(bpy.context, mode='REFINE', scope='SET', prompt='Synthetic map test')
    objects = session.objects_for(captured)
    region = regions.capture(ts, objects)[0]['id']
    frame = surface_projection.inspect(ts, region, objects, (0, 0, 1), (0, 1, 0))['frame']
    with tempfile.TemporaryDirectory() as directory:
        images = {}

        def store(pixels):
            entry = generated_images.store(directory, dict(result=base64.b64encode(
                png(pixels.shape[1], pixels.shape[0], pixels.tobytes())).decode()))
            entry['state_directory'] = directory
            images[entry['image_id']] = entry
            return entry['image_id']

        color = np.full((128, 128, 4), 255, np.uint8)
        color[..., :3] = (70, 100, 140)
        color[:, :16, 3] = 0
        color_id = store(color)
        proposal = dict(version=1, summary='Projected reference', commands=[
            dict(op='add_layer', id='new:surface', name='Reference', kind='FILL'),
            dict(op='assign_projected_surface', layer_id='new:surface', image_id=color_id,
                 region_id=region, frame=frame, name='Reference', purpose='Regression reference',
                 roughness_range=[.4, .6], height_amplitude=0)])
        transactions.apply(ts, proposal, operation_id='map-reference', expected_digest=document.digest(ts), objects=objects, images=images)
        layer = ts.layers[-1]
        original = document.snapshot(ts)
        original_canonical = document.canonical(original)
        base = next(p for p in layer.channels if p.channel == 'BASE_COLOR')
        color_pixels = image_manager.pixels(base.image).copy()
        reference = surface_maps.inspect(ts, layer.id)
        assert reference['reference_image_id'] == color_id
        assert reference['_images'][0]['url'].startswith('data:image/png;base64,')
        y, x = np.mgrid[:128, :128]
        height = .5 + .4 * np.exp(-((x-70)**2 + (y-55)**2) / 70.)
        maps = {}
        for key, field in dict(HEIGHT=height, ROUGHNESS=np.where(x > 64, .8, .2),
                               METALLIC=np.where(y > 64, 1., 0.)).items():
            rgba = np.ones((128, 128, 4), np.uint8) * 255
            rgba[..., :3] = np.round(field[..., None] * 255).astype(np.uint8)
            maps[key] = store(rgba)
        cmd = dict(op='assign_surface_maps', layer_id=layer.id, reference_image_id=color_id,
                   maps=maps, height_amplitude=.5)
        # Detached validation must reject albedo reuse and a stale reference.
        for invalid in (dict(cmd, reference_image_id='generated:wrong'),
                        dict(cmd, maps=dict(HEIGHT=color_id))):
            try:
                surface_maps.assign(document.clone(original['layers'][-1]), invalid, images)
                raise AssertionError('Invalid map accepted')
            except schema.AuthoringError:
                pass
        transactions.apply(ts, dict(version=1, summary='Matching maps', commands=[cmd]),
                           operation_id='map-refinement', expected_digest=document.digest(ts), objects=objects, images=images)
        shader_preview.flush(scene, ts)
        layer = ts.layers[-1]
        assert np.array_equal(color_pixels, image_manager.pixels(base.image))
        _, normals, valid = geometry(ts, 512, coverage=True)
        top = valid & (normals[..., 2] > .9)
        channels = {}
        for key in ('BASE_COLOR', 'ROUGHNESS', 'METALLIC', 'HEIGHT'):
            prop = next(p for p in layer.channels if p.channel == key)
            assert prop.projection == base.projection
            channels[key] = channel_eval.source(capture(prop), 'FILL', 512, 0, 512, {}, ts, {})
            if key != 'BASE_COLOR':
                assert np.max(np.abs(channels[key][..., 3] - channels['BASE_COLOR'][..., 3])) < 1e-6
                assert channels[key][top, 0].std() > .005
                image = prop.image or prop.detail_image
                assert image.packed_file and image.colorspace_settings.name == 'Non-Color'
        flat = np.ones((512, 512, 4), np.float32)
        flat[..., :2] = .5
        normal = combined(ts, flat, channels['HEIGHT'])
        assert np.max(np.abs(normal[top, :2] - .5)) > .01, 'Height did not create normal relief'
        diagnostics, _ = candidates.channel_diagnostics(scene, [ts.id], time.monotonic()+60)
        assert diagnostics[ts.id]['channels']['NORMAL']['min'] < .49
        after = document.canonical(document.snapshot(ts))
        out = ROOT / '.blender-test/director-surface-maps'
        out.mkdir(exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(out/'maps.blend'))
    ident = ts.id
    bpy.ops.wm.open_mainfile(filepath=str(out/'maps.blend'), use_scripts=False)
    ts = next(item for item in bpy.context.scene.open_painter.texture_sets if item.id == ident)
    shader_preview.flush(bpy.context.scene, ts)
    assert document.canonical(document.snapshot(ts)) == after
    transactions.undo(ts)
    assert document.canonical(document.snapshot(ts)) == original_canonical
    print('DIRECTOR_SURFACE_MAPS_PASS', flush=True)


open_painter.register()
def main():
    try:
        run()
    except BaseException:
        traceback.print_exc()
        os._exit(1)
    bpy.ops.wm.quit_blender()
bpy.app.timers.register(main, first_interval=1)
