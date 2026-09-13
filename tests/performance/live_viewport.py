"""Completed EEVEE-frame comparison for the committed Worn Painted Metal fixture.

Run in an isolated Blender window (not --background)::

    BLENDER_USER_RESOURCES=/private/tmp/op-live-viewport-user \
      /Applications/Blender.app/Contents/MacOS/Blender --factory-startup \
      --python tests/performance/live_viewport.py -- \
      --report /private/tmp/op-live-viewport.json

The fixed order is CPU cold preparation, three CPU warm edits, GPU cold
preparation, then three GPU warm edits. Each measured edit ends at an actual
512-square EEVEE viewport framebuffer readback fence. This is neither OS input
latency nor physical display presentation latency. It does not measure baking.
"""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time
import traceback

import bpy
import gpu
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import open_painter
from fixtures import create as fixture
from open_painter.constants import OWNER
from open_painter.core import layer_stack, texture_set_manager
from open_painter.evaluation import live_preview
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.metrics import Metrics
from open_painter.evaluation.raster_cache import clear_shared


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, default=ROOT / '.blender-test/live-viewport.json')
    parser.add_argument('--other-blender-pid', type=int)
    parser.add_argument('-ApplePersistenceIgnoreState', dest='apple_persistence_ignore_state')
    return parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])


ARGS = arguments()
FRAME_SIZE = 512
REPORT = {'status': 'RUNNING'}


def source_digest():
    digest = hashlib.sha256()
    for path in sorted((ROOT / 'src/open_painter').rglob('*.py')):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def node_structure(material):
    tree = material.node_tree
    return (
        tuple((node.as_pointer(), node.bl_idname, node.get(OWNER), node.get('role')) for node in tree.nodes),
        tuple((link.from_node.as_pointer(), link.from_socket.identifier,
               link.to_node.as_pointer(), link.to_socket.identifier) for link in tree.links),
    )


def consume(scene, ts):
    job = live_preview.steps(scene, ts)
    maximum, count = 0., 0
    while True:
        started = time.perf_counter()
        try:
            next(job)
        except StopIteration as done:
            maximum = max(maximum, time.perf_counter() - started)
            assert done.value and live_preview.current(scene, ts), 'Live revision failed to complete'
            return {'cooperative_steps': count, 'maximum_step_wall_s': maximum}
        maximum = max(maximum, time.perf_counter() - started)
        count += 1


def run():
    offscreen = None
    registered = False
    try:
        REPORT['started_at_utc'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        if bpy.app.background or bpy.context.window is None:
            raise RuntimeError('This benchmark requires a real Blender window and EEVEE GPU context')
        if ARGS.other_blender_pid:
            try:
                observed = subprocess.check_output(
                    ['ps', '-p', str(ARGS.other_blender_pid), '-o', 'pid=,etime=,%cpu='],
                    text=True, stderr=subprocess.DEVNULL).strip()
            except (OSError, subprocess.CalledProcessError):
                observed = 'Not observed at benchmark start'
            REPORT['other_blender_process'] = dict(pid=ARGS.other_blender_pid,
                observation_pid_elapsed_cpu_percent=observed,
                context='Not launched or controlled by this benchmark; its concurrent activity was not controlled.')
        open_painter.register()
        registered = True
        scene = bpy.context.scene
        scene.render.engine = 'BLENDER_EEVEE'
        scene.view_settings.view_transform = 'Standard'
        scene.view_settings.look = 'None'
        ts = fixture(scene, 'worn', 2048)[0]
        ts.preview_quality = 'FULL'
        ts.preview_backend = 'CPU'
        cube = scene.objects.get('Cube')
        assert cube and cube.type == 'MESH'
        texture_set_manager.assign(scene, ts, cube)
        coat = next(layer for layer in ts.layers if layer.name == 'Blue paint coat')
        roughness = layer_stack.channel_for(coat, 'ROUGHNESS')
        base_value = float(roughness.value)
        edit_values = [base_value + delta for delta in (.01, .02, .03)]
        assert max(edit_values) <= 1
        scene.open_painter.blend_channel = 'ROUGHNESS'
        # Both paths use full float32 image storage and full-precision image GPU
        # textures. EEVEE's internal material buffers have their own precision.
        for output in ts.outputs:
            output.image.use_half_precision = False
        author_images = tuple((output.channel, output.image.as_pointer(), tuple(output.image.size))
                              for output in ts.outputs)
        structure = node_structure(ts.material)

        area = next(area for area in bpy.context.screen.areas if area.type == 'VIEW_3D')
        region = next(region for region in area.regions if region.type == 'WINDOW')
        space = area.spaces.active
        space.shading.type = 'MATERIAL'
        space.shading.use_scene_world = False
        space.shading.use_scene_lights = False
        space.overlay.show_overlays = False
        camera = bpy.data.cameras.new('Viewport Benchmark Camera')
        camera_object = bpy.data.objects.new('Viewport Benchmark Camera', camera)
        scene.collection.objects.link(camera_object)
        camera_object.location = (4.2, -5.1, 3.5)
        camera_object.rotation_euler = (-camera_object.location).to_track_quat('-Z', 'Y').to_euler()
        scene.camera = camera_object
        bpy.context.view_layer.update()
        view = camera_object.matrix_world.inverted()
        projection = camera_object.calc_matrix_camera(bpy.context.evaluated_depsgraph_get(),
                                                      x=FRAME_SIZE, y=FRAME_SIZE)
        offscreen = gpu.types.GPUOffScreen(FRAME_SIZE, FRAME_SIZE, format='RGBA32F')

        def draw():
            ts.material.update_tag()
            bpy.context.view_layer.update()
            with bpy.context.temp_override(area=area, region=region):
                offscreen.draw_view3d(scene, bpy.context.view_layer, space, region,
                                      view, projection, do_color_management=False)
            with offscreen.bind():
                frame = gpu.state.active_framebuffer_get().read_color(
                    0, 0, FRAME_SIZE, FRAME_SIZE, 4, 0, 'FLOAT')
            frame = np.asarray(frame, dtype=np.float32)
            assert np.isfinite(frame).all(), 'EEVEE produced non-finite frame values'
            return {'frame_mean_rgb': frame.reshape(FRAME_SIZE, FRAME_SIZE, 4)[..., :3].mean(axis=(0, 1)).tolist(),
                    'viewport_readback_bytes': FRAME_SIZE * FRAME_SIZE * 4 * 4}

        def measure(backend, value=None):
            with Metrics() as observed:
                started = time.perf_counter()
                if value is not None:
                    roughness.value = value
                detail = (consume(scene, ts) if backend == 'LIVE' else
                          (LayerEvaluator(scene).evaluate_texture_set(ts.id) or {}))
                submitted = time.perf_counter()
                frame = draw()
                completed = time.perf_counter()
            assert node_structure(ts.material) == structure, 'Display material node structure changed'
            assert tuple((output.channel, output.image.as_pointer(), tuple(output.image.size))
                         for output in ts.outputs) == author_images, 'Authoritative image identity/resolution changed'
            result = dict(backend=backend, roughness=float(roughness.value),
                          prepare_submit_wall_s=submitted - started,
                          dependency_update_draw_and_fence_wall_s=completed - submitted,
                          edit_to_completed_frame_wall_s=completed - started,
                          metrics=observed.report(), **detail, **frame)
            if backend == 'LIVE' and value is not None:
                counters = result['metrics']['counters']
                for name in ('image_read_bytes', 'image_write_bytes', 'pack_calls',
                             'live_input_upload_bytes', 'live_gpu_upload_bytes'):
                    assert counters.get(name, 0) == 0, f'Warm GPU edit transferred source/composite pixels: {name}'
            print('LIVE_VIEWPORT_SAMPLE', json.dumps(result), flush=True)
            return result

        live_preview.clear()
        clear_shared()
        cold_cpu = measure('CPU')
        for _ in range(3):
            draw()
        cpu = [measure('CPU', value) for value in edit_values]

        live_preview.clear()
        clear_shared()
        roughness.value = base_value
        ts.preview_backend = 'LIVE'
        cold_gpu = measure('LIVE')
        for _ in range(3):
            draw()
        gpu_samples = [measure('LIVE', value) for value in edit_values]
        cpu_median = statistics.median(item['edit_to_completed_frame_wall_s'] for item in cpu)
        gpu_median = statistics.median(item['edit_to_completed_frame_wall_s'] for item in gpu_samples)
        REPORT.update(
            status='PASS', measured_at_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            source_sha256=source_digest(), blender=bpy.app.version_string,
            blender_build=bpy.app.build_hash.decode(), platform=platform.platform(),
            gpu_backend=gpu.platform.backend_type_get(), gpu_renderer=gpu.platform.renderer_get(),
            numpy=np.__version__, fixture='Committed Worn Painted Metal 2K with assigned default cube',
            texture_resolution=2048, texture_format='float32 RGBA / RGBA32F', preview_quality=ts.preview_quality,
            channels=len(ts.outputs), layers=len(ts.layers), mesh_maps='Committed synthetic 32 x 32 AO and curvature',
            viewport=dict(width=FRAME_SIZE, height=FRAME_SIZE, engine='EEVEE', mode=space.shading.type,
                          studio_light=space.shading.studio_light,
                          environment_rotation=space.shading.studiolight_rotate_z,
                          environment_intensity=space.shading.studiolight_intensity,
                          color_management=False, output_format='RGBA32F', completed_readback_fence=True),
            order='CPU initial preparation + three draw warmups + three warm edits; clear caches; GPU initial preparation + three draw warmups + three warm edits',
            cpu_initial=cold_cpu, cpu_warm=cpu, gpu_initial=cold_gpu, gpu_warm=gpu_samples,
            median_completed_frame_s={'CPU': cpu_median, 'LIVE': gpu_median},
            median_completed_frame_speedup=cpu_median / gpu_median,
            display_node_count=len(ts.material.node_tree.nodes), node_structure_unchanged=True,
            authored_image_identity_and_resolution_unchanged=True,
            limits=[
                'One process, one fixed CPU-before-GPU ordering, three warm samples per path; driver shader caches were not purged.',
                'CPU source caches and GPU live state were cleared before each initial preparation. EEVEE shader setup was warmed before the three edits.',
                'Warm edit timing includes property mutation, evaluation/submission, dependency graph update, one EEVEE draw, framebuffer readback and finite-frame validation.',
                'Readback of the viewport frame is a completion fence and adds overhead; no OS input event, physical screen presentation or settled multi-frame convergence is measured.',
                'Metrics count managed source/composite image transfers separately from the explicit 4 MiB viewport frame readback.',
                'Fixture mesh maps are synthetic; this is neither mesh-bake timing nor a claim about arbitrary high-poly scenes or all materials.',
                'Initial source generation, complex edited source/filter recomputation, and CPU save/export evaluation remain separate costs.',
            ],
        )
    except Exception:
        REPORT.update(status='FAIL', error=traceback.format_exc())
        traceback.print_exc()
    finally:
        if offscreen is not None:
            offscreen.free()
        if registered:
            open_painter.unregister()
        ARGS.report.parent.mkdir(parents=True, exist_ok=True)
        ARGS.report.write_text(json.dumps(REPORT, indent=2) + '\n')
        print('LIVE_VIEWPORT_RESULT', json.dumps(REPORT), flush=True)
        bpy.ops.wm.quit_blender()


if bpy.app.background:
    raise RuntimeError('Use a Blender window; --background cannot measure an EEVEE viewport frame')
bpy.app.timers.register(run, first_interval=2)
