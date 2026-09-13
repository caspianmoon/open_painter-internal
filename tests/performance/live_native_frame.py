"""Time live edits through a completed *native* EEVEE viewport draw.

Unlike GPUOffScreen.draw_view3d(), this observes the ordinary VIEW_3D draw loop
with a POST_PIXEL handler. It does not force an all-samples viewport render.
Run this same file against another checkout with OP_BENCH_REPO=/path/to/checkout.
Use a fresh BLENDER_USER_RESOURCES directory and --factory-startup. Example::

    BLENDER_USER_RESOURCES=/private/tmp/op-native-user \
      /Applications/Blender.app/Contents/MacOS/Blender --factory-startup \
      --python tests/performance/live_native_frame.py -- \
      --report /private/tmp/op-native.json -ApplePersistenceIgnoreState YES

No texture or viewport resolution, precision, or EEVEE sampling setting is
lowered. A 3x3 framebuffer readback fences the actual native draw; its requested
144-byte result is not a measurement of driver transfer volume. It measures
neither physical display presentation nor OS input delivery. Explicitly driving
the evaluator excludes the add-on scheduler's debounce and cooperative yields.
Add --scheduled to retain production cache.tick and include its scheduling and
cooperative yields, accepting a native frame only after the requested revision
is complete. The default explicit measurement remains unchanged.
"""

import argparse
import hashlib
import json
import os
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
from mathutils import Vector

HARNESS = Path(__file__).resolve()
ROOT = Path(os.environ.get('OP_BENCH_REPO', HARNESS.parents[2])).resolve()
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tests' / 'performance'))

import open_painter
from fixtures import create as fixture
from open_painter.constants import OWNER
from open_painter.core import layer_stack, texture_set_manager
from open_painter.evaluation import cache, live_preview
from open_painter.evaluation.metrics import Metrics
from open_painter.evaluation.raster_cache import clear_shared


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, default=Path('/private/tmp/op-native-frame.json'))
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--timeout', type=float, default=300.)
    parser.add_argument('--scheduled', action='store_true',
                        help='Measure real cache.tick scheduling through the first current native frame')
    parser.add_argument('--procedural-edits', action='store_true',
                        help='Append five mask-strength and five shared source-noise-scale edits')
    parser.add_argument('--other-blender-pid', type=int)
    parser.add_argument('-ApplePersistenceIgnoreState', dest='apple_persistence_ignore_state')
    result = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    if result.repeats < 5:
        parser.error('--repeats must be at least 5')
    return result


ARGS = arguments()
REPORT = {'status': 'RUNNING', 'samples': []}
STATE = {'started': time.perf_counter(), 'handler': None, 'pre_handler': None, 'registered': False,
         'pending': None, 'finished': False, 'frames': 0}


def utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def source_digest():
    digest = hashlib.sha256()
    for path in sorted((ROOT / 'src' / 'open_painter').rglob('*.py')):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def structure(material):
    tree = material.node_tree
    return (tuple((node.as_pointer(), node.bl_idname, node.get(OWNER), node.get('role'))
                  for node in tree.nodes),
            tuple((link.from_node.as_pointer(), link.from_socket.identifier,
                   link.to_node.as_pointer(), link.to_socket.identifier) for link in tree.links))


def consume(scene, ts):
    job = live_preview.steps(scene, ts)
    count, maximum = 0, 0.
    while True:
        started = time.perf_counter()
        try:
            next(job)
        except StopIteration as completed:
            maximum = max(maximum, time.perf_counter() - started)
            assert completed.value and live_preview.current(scene, ts), 'Live revision incomplete'
            return dict(cooperative_steps=count, maximum_step_wall_s=maximum)
        count += 1
        maximum = max(maximum, time.perf_counter() - started)


def write_report():
    ARGS.report.parent.mkdir(parents=True, exist_ok=True)
    ARGS.report.write_text(json.dumps(REPORT, indent=2) + '\n')


def finish(error=None):
    if STATE['finished']:
        return
    STATE['finished'] = True
    observed = STATE.pop('sample_metrics', None)
    if observed is not None:
        observed.__exit__(None, None, None)
        REPORT['incomplete_sample'] = dict(STATE.get('pending') or {}, metrics=observed.report())
    REPORT['finished_at_utc'] = utc()
    REPORT['elapsed_wall_s'] = time.perf_counter() - STATE['started']
    REPORT['source_sha256_at_end'] = source_digest()
    if error:
        REPORT.update(status='FAIL', error=error)
    else:
        REPORT['status'] = 'PASS'
        REPORT['summary'] = {}
        kinds = ('cold', 'redraw_control', 'roughness', 'opacity')
        if ARGS.procedural_edits:
            kinds += ('mask_strength', 'noise_scale')
        for kind in kinds:
            samples = [item for item in REPORT['samples'] if item['kind'] == kind]
            REPORT['summary'][kind] = {
                'samples': len(samples),
                'median_edit_to_native_frame_s': statistics.median(
                    item['edit_to_native_frame_s'] for item in samples),
                'median_prepare_submit_s': statistics.median(
                    item['prepare_submit_wall_s'] for item in samples),
                'median_dependency_update_s': statistics.median(
                    item['dependency_update_wall_s'] for item in samples
                    if item['dependency_update_wall_s'] is not None)
                    if any(item['dependency_update_wall_s'] is not None for item in samples) else None,
                'median_wait_draw_and_fence_s': statistics.median(
                    item['wait_draw_and_fence_wall_s'] for item in samples),
            }
        REPORT['source_unchanged_during_measurement'] = (
            REPORT['source_sha256'] == REPORT['source_sha256_at_end'])
    # Removing draw handlers / owned GPU objects must happen outside a draw.
    handler = STATE.pop('handler', None)
    if handler is not None:
        bpy.types.SpaceView3D.draw_handler_remove(handler, 'WINDOW')
    pre_handler = STATE.pop('pre_handler', None)
    if pre_handler is not None:
        bpy.types.SpaceView3D.draw_handler_remove(pre_handler, 'WINDOW')
    try:
        if STATE['registered']:
            open_painter.unregister()
    except Exception:
        REPORT.update(status='FAIL', cleanup_error=traceback.format_exc())
    write_report()
    print('LIVE_NATIVE_FRAME_' + REPORT['status'], ARGS.report, flush=True)
    bpy.ops.wm.quit_blender()


def begin_draw():
    if (not STATE['finished'] and bpy.context.area == STATE.get('area')
            and bpy.context.region == STATE.get('region')):
        # Blender binds the native default (EEVEE color) framebuffer before
        # PRE_VIEW, then binds its transparent overlay framebuffer before
        # POST_VIEW / POST_PIXEL. Capture this per-frame, never retain across
        # frames or own/free Blender's framebuffer.
        STATE['color_framebuffer'] = gpu.state.active_framebuffer_get()
        STATE['color_viewport'] = gpu.state.viewport_get()
        if ARGS.scheduled:
            scene, ts = STATE['scene'], STATE['ts']
            STATE['draw_live_serial'] = int(ts.edit_serial) if live_preview.current(scene, ts) else None
            pending = STATE['pending']
            if pending is not None and STATE['draw_live_serial'] == pending['edit_serial']:
                observe_scheduled_ready(pending, 'native_draw_start')


def scheduled_ready(pending):
    """Observe production state without executing or advancing its evaluator."""
    if not bpy.app.timers.is_registered(cache.tick):
        raise RuntimeError('Production cache.tick timer stopped during scheduled measurement')
    scene, ts = STATE['scene'], STATE['ts']
    if int(ts.edit_serial) != pending['edit_serial']:
        raise RuntimeError(f'Revision changed during scheduled sample: expected {pending["edit_serial"]}, '
                           f'found {ts.edit_serial}')
    job = cache.job_state(scene, ts)
    pending['last_scheduler_state'] = job['state']
    if job['state'] == 'FAILED':
        raise RuntimeError('Scheduled live job failed: ' + job.get('error', ''))
    status = live_preview.status(scene, ts)
    if status.startswith('CPU preview:'):
        raise RuntimeError('Scheduled live job used a CPU fallback: ' + status)
    return live_preview.current(scene, ts)


def observe_scheduled_ready(pending, location):
    if 'scheduler_completion_observed_after_s' not in pending:
        pending['scheduler_completion_observed_after_s'] = time.perf_counter() - pending['started']
        pending['scheduler_completion_observation'] = location


def completed_draw():
    """Called after normal viewport rendering, never submits an evaluator job."""
    try:
        if (STATE['finished'] or bpy.context.area != STATE['area']
                or bpy.context.region != STATE['region']):
            return
        STATE['frames'] += 1
        pending = STATE['pending']
        if pending is None:
            return
        if ARGS.scheduled:
            if not scheduled_ready(pending) or STATE.get('draw_live_serial') != pending['edit_serial']:
                pending['incomplete_revision_draws'] += 1
                STATE.pop('color_framebuffer', None)
                STATE.pop('color_viewport', None)
                return
            assert structure(STATE['ts'].material) == STATE['structure'], 'Display graph structure changed'
            assert tuple((o.channel, o.image.as_pointer(), tuple(o.image.size))
                         for o in STATE['ts'].outputs) == STATE['authored']
        # Use native viewport coordinates; a readback orders completion of the
        # preceding EEVEE work. It is deliberately separate from prepare time.
        entered = time.perf_counter()
        fb = STATE.pop('color_framebuffer', None)
        assert fb is not None, 'PRE_VIEW did not capture the native EEVEE color framebuffer'
        x, y, width, height = STATE.pop('color_viewport')
        with fb.bind():
            patch = fb.read_color(x + max(0, width // 2 - 1),
                                  y + max(0, height // 2 - 1), 3, 3, 4, 0, 'FLOAT')
        completed = time.perf_counter()
        if ARGS.scheduled:
            observed = STATE.pop('sample_metrics')
            observed.__exit__(None, None, None)
            pending['metrics'] = observed.report()
            pending['completed_edit_serial'] = int(STATE['ts'].edit_serial)
            pending['scheduler_timer_registered'] = bpy.app.timers.is_registered(cache.tick)
        pixels = np.asarray(patch, dtype=np.float32)
        assert np.isfinite(pixels).all(), 'Native EEVEE framebuffer is not finite'
        has_content = bool(np.max(pixels.reshape(-1, 4)[..., :3]) > .001
                           and np.max(pixels.reshape(-1, 4)[..., 3]) > .5)
        if pending['kind'] != 'cold':
            assert has_content, ('No opaque shaded content at the framed cube center; '
                                 'native framebuffer evidence is invalid')
        pending.update(
            edit_to_native_frame_s=completed - pending.pop('started'),
            wait_draw_and_fence_wall_s=completed - pending.pop('submitted'),
            handler_readback_fence_wall_s=completed - entered,
            frame_patch_mean_rgba=pixels.reshape(-1, 4).mean(axis=0).tolist(),
            native_shaded_content_validated=has_content,
            frame_readback_requested_payload_bytes=3 * 3 * 4 * 4,
            gpu_viewport_rect=[x, y, width, height],
            actual_region_size=[STATE['region'].width, STATE['region'].height],
            completed_native_draw=STATE['frames'],
        )
        REPORT['samples'].append(pending)
        STATE['pending'] = None
        STATE['settle_until'] = completed + (.10 if pending['kind'] != 'cold' else 2.)
        STATE['settle_frames'] = STATE['frames'] + (
            2 if pending['kind'] != 'cold' else max(8, REPORT['viewport']['taa_samples'] + 4))
        print('LIVE_NATIVE_FRAME_SAMPLE', json.dumps(pending), flush=True)
    except Exception:
        STATE['error'] = traceback.format_exc()


def apply_procedural_edit(kind, index):
    """Change the authored generator/source parameters, never output pixels."""
    assert ARGS.procedural_edits and 0 <= index < 5
    if kind == 'mask_strength':
        STATE['coat_mask'].strength = STATE['base_mask_strength'] * (.95 - .05 * index)
    elif kind == 'noise_scale':
        value = STATE['base_procedural_scale'] * (1. + .1 * (index + 1))
        # Linked scale on all three coat channels keeps the source shared.
        # RNA invalidations finish in this callback before cache.tick can run.
        for prop in STATE['procedural_channels']:
            prop.procedural_scale = value
    else:
        raise ValueError('Unknown procedural edit phase: ' + kind)


def procedural_sample_values(kind):
    if kind not in {'mask_strength', 'noise_scale'}:
        return {}
    return dict(parameter_layer='Blue paint coat',
        mask_strength=float(STATE['coat_mask'].strength),
        source_noise_scales={prop.channel: float(prop.procedural_scale)
                             for prop in STATE['procedural_channels']})


def submit(kind, index):
    if ARGS.scheduled:
        submit_scheduled(kind, index)
        return
    scene, ts = STATE['scene'], STATE['ts']
    with Metrics() as observed:
        started = time.perf_counter()
        if kind == 'roughness':
            STATE['roughness'].value = STATE['base_roughness'] + .01 * (index + 1)
        elif kind == 'opacity':
            STATE['folder'].opacity = .95 - .03 * index
        elif kind in {'mask_strength', 'noise_scale'}:
            apply_procedural_edit(kind, index)
        if kind == 'redraw_control':
            detail = {}
        else:
            detail = consume(scene, ts)
        prepared = time.perf_counter()
        # No redundant material tag: publication already invalidates history.
        # Native event processing would do this update before the next draw;
        # doing it explicitly gives it a separate, comparable phase timer.
        bpy.context.view_layer.update()
        submitted = time.perf_counter()
    assert structure(ts.material) == STATE['structure'], 'Display graph structure changed'
    assert tuple((o.channel, o.image.as_pointer(), tuple(o.image.size)) for o in ts.outputs) == STATE['authored']
    STATE['pending'] = dict(kind=kind, index=index, started=started, submitted=submitted,
        prepare_submit_wall_s=prepared - started,
        dependency_update_wall_s=submitted - prepared,
        roughness=float(STATE['roughness'].value), opacity=float(STATE['folder'].opacity),
        metrics=observed.report(), display_node_count=len(ts.material.node_tree.nodes),
        edit_serial=int(ts.edit_serial), **detail, **procedural_sample_values(kind))
    STATE['area'].tag_redraw()


def submit_scheduled(kind, index):
    """Only mutate the requested property; let cache.tick do the real work."""
    scene, ts = STATE['scene'], STATE['ts']
    observed = Metrics()
    observed.__enter__()
    STATE['sample_metrics'] = observed
    started = time.perf_counter()
    if kind == 'roughness':
        STATE['roughness'].value = STATE['base_roughness'] + .01 * (index + 1)
    elif kind == 'opacity':
        STATE['folder'].opacity = .95 - .03 * index
    elif kind in {'mask_strength', 'noise_scale'}:
        apply_procedural_edit(kind, index)
    elif kind == 'cold':
        # Setup only held this flag to prevent the live timer from consuming
        # the cold fixture during initial viewport warmup. No recipe changed.
        ts.dirty = True
    elif kind != 'redraw_control':
        raise ValueError('Unknown scheduled native-frame phase: ' + kind)
    submitted = time.perf_counter()
    STATE['pending'] = dict(kind=kind, index=index, started=started, submitted=submitted,
        prepare_submit_wall_s=submitted - started,
        dependency_update_wall_s=None,
        dependency_update_scope='Native event processing, included in scheduler/draw wait',
        explicit_job_consumption=False, incomplete_revision_draws=0,
        roughness=float(STATE['roughness'].value), opacity=float(STATE['folder'].opacity),
        display_node_count=len(ts.material.node_tree.nodes), edit_serial=int(ts.edit_serial),
        **procedural_sample_values(kind))
    if kind == 'redraw_control':
        assert live_preview.current(scene, ts), 'Redraw control started before the previous live edit completed'
    STATE['area'].tag_redraw()


def setup():
    if bpy.app.background or bpy.context.window is None:
        raise RuntimeError('Native frame benchmark requires a Blender GUI window')
    REPORT.update(started_at_utc=utc(), source_root=str(ROOT), source_sha256=source_digest(),
        harness_sha256=hashlib.sha256(HARNESS.read_bytes()).hexdigest(),
        blender=bpy.app.version_string, blender_build=bpy.app.build_hash.decode(),
        platform=platform.platform(), numpy=np.__version__,
        gpu_backend=gpu.platform.backend_type_get(), gpu_renderer=gpu.platform.renderer_get(),
        command=sys.argv, user_resources=os.environ.get('BLENDER_USER_RESOURCES'),
        fixture='Committed Worn Painted Metal on assigned default cube', resolution=2048,
        precision='Full float32 authored and live GPU textures', mesh_maps='Synthetic 32x32 AO and curvature',
        repeats=ARGS.repeats, evaluation_mode='scheduled' if ARGS.scheduled else 'explicit',
        procedural_edits=ARGS.procedural_edits,
        timing_scope='Explicit live submission, dependency update, event scheduling, ordinary native '
                     'VIEW_3D rendering and 3x3 framebuffer completion fence; not GPUOffScreen, '
                     'not all-samples render time, not physical presentation or OS input latency.',
        limits=[
            'The add-on auto evaluator timer is disabled; explicit job consumption excludes debounce and cooperative scheduling latency.',
            'Cold starts with fixture and authored images created; fixture creation is reported separately.',
            'The cold result ends at the first native draw, which may still have asynchronous shader compilation pending.',
            'Warm edits follow at least two seconds and taa_samples+4 native warmup draws after cold publication.',
            'Each warm measurement fences the first subsequent native draw; later temporal refinement is excluded.',
            'The 3x3 patch verifies finite, nonempty opaque shaded color after warmup and fences GPU work, not full-image pixel parity.',
            'PRE_VIEW captures Blender\'s color framebuffer; POST_PIXEL reads that target explicitly because the active post-pixel target contains only overlays.',
            'Normal EEVEE temporal reprojection remains enabled if supplied by factory settings.',
            'The actual window/region size and ordinary Blender event-loop scheduling affect these timings.',
        ])
    if ARGS.scheduled:
        REPORT['timing_scope'] = ('Property mutation, production cache.tick event scheduling and cooperative '
            'yields, native dependency processing, first current-revision VIEW_3D rendering and 3x3 '
            'framebuffer completion fence; not physical presentation or OS input latency.')
        REPORT['limits'][0] = ('Production cache.tick remains registered and alone advances live jobs. '
            'The prepare phase measures property mutation; scheduler work, native dependency updates '
            'and draw/fence are combined in the following wait phase.')
        REPORT['limits'].append('Cold fixture evaluation is held during initial viewport warmup by '
            'temporarily clearing only ts.dirty, then released at the timed cold submission.')
        REPORT['limits'].append('Scheduler completion timestamps are observations at a harness poll or '
            'native draw start, not instrumentation inside cache.tick; total edit-to-frame includes '
            'all scheduling and cooperative-yield delay.')
    if ARGS.other_blender_pid:
        result = subprocess.run(['ps', '-p', str(ARGS.other_blender_pid), '-o', 'pid=,etime=,%cpu='],
                                capture_output=True, text=True)
        REPORT['other_blender_process'] = dict(pid=ARGS.other_blender_pid,
            observation=result.stdout.strip(), controlled=False)
    open_painter.register()
    STATE['registered'] = True
    if not ARGS.scheduled and bpy.app.timers.is_registered(cache.tick):
        bpy.app.timers.unregister(cache.tick)
    if ARGS.scheduled and not bpy.app.timers.is_registered(cache.tick):
        raise RuntimeError('The add-on did not register production cache.tick')
    cache.cancel_all()
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_EEVEE'
    scene.view_settings.view_transform = 'Standard'
    scene.view_settings.look = 'None'
    fixture_started = time.perf_counter()
    ts = fixture(scene, 'worn', 2048)[0]
    ts.preview_quality = 'FULL'
    ts.preview_backend = 'LIVE'
    cube = scene.objects.get('Cube')
    assert cube and cube.type == 'MESH'
    texture_set_manager.assign(scene, ts, cube)
    for output in ts.outputs:
        output.image.use_half_precision = False
    REPORT['fixture_creation_wall_s'] = time.perf_counter() - fixture_started
    coat = next(layer for layer in ts.layers if layer.name == 'Blue paint coat')
    roughness = layer_stack.channel_for(coat, 'ROUGHNESS')
    area = next(area for area in bpy.context.screen.areas if area.type == 'VIEW_3D')
    region = next(region for region in area.regions if region.type == 'WINDOW')
    space = area.spaces.active
    space.shading.type = 'MATERIAL'
    space.shading.use_scene_world = False
    space.shading.use_scene_lights = False
    space.overlay.show_overlays = False
    space.region_3d.view_location = (0, 0, 0)
    space.region_3d.view_distance = 7.5
    space.region_3d.view_rotation = Vector((4.2, -5.1, 3.5)).to_track_quat('Z', 'Y')
    space.region_3d.view_perspective = 'PERSP'
    STATE.update(scene=scene, ts=ts, area=area, region=region, roughness=roughness,
                 folder=next(layer for layer in ts.layers if layer.type == 'FOLDER'),
                 base_roughness=float(roughness.value), structure=structure(ts.material),
                 authored=tuple((o.channel, o.image.as_pointer(), tuple(o.image.size)) for o in ts.outputs),
                 queue=[('cold', 0)] + [(kind, i) for kind in ('redraw_control', 'roughness', 'opacity')
                                       for i in range(ARGS.repeats)])
    if ARGS.procedural_edits:
        mask = next(item for item in coat.masks if item.enabled
                    and item.source == 'GENERATOR' and item.generator == 'CHIPS')
        channels = tuple(layer_stack.channel_for(coat, name)
                         for name in ('BASE_COLOR', 'ROUGHNESS', 'HEIGHT'))
        assert all(prop and prop.enabled and prop.procedural == 'ORANGE_PEEL'
                   for prop in channels), 'Worn coat no longer has the expected active scalar channels'
        assert len({float(prop.procedural_scale) for prop in channels}) == 1
        assert len({int(prop.procedural_seed) for prop in channels}) == 1
        assert mask.strength > 0, 'Mask-strength benchmark requires an active mask'
        STATE.update(coat_mask=mask, procedural_channels=channels,
                     base_mask_strength=float(mask.strength),
                     base_procedural_scale=float(channels[0].procedural_scale))
        STATE['queue'].extend((kind, index) for kind in ('mask_strength', 'noise_scale')
                              for index in range(5))
        REPORT['procedural_edit_targets'] = dict(layer=coat.name,
            mask_generator=mask.generator, mask_invert=bool(mask.invert),
            mask_initial_strength=float(mask.strength), mask_noise_scale=float(mask.noise_scale),
            source_kind=channels[0].procedural, source_seed=int(channels[0].procedural_seed),
            source_initial_scale=float(channels[0].procedural_scale),
            source_channels=[prop.channel for prop in channels], repetitions_per_parameter=5,
            mask_strength_factors=[.95 - .05 * index for index in range(5)],
            source_scale_factors=[1. + .1 * (index + 1) for index in range(5)])
        REPORT['limits'].append('Procedural phases follow the uniform-edit phases with their final '
            'folder opacity and roughness retained. Mask strength changes the active inverted CHIPS '
            'mask; source scale changes the shared ORANGE_PEEL field on Base Color, Roughness and '
            'Height in one callback. Texture resolution and precision remain unchanged.')
    REPORT['viewport'] = dict(engine='EEVEE', mode=space.shading.type,
        taa_samples=int(scene.eevee.taa_samples),
        taa_render_samples=int(scene.eevee.taa_render_samples),
        taa_reprojection=bool(scene.eevee.use_taa_reprojection),
        preview_pixel_size=str(scene.render.preview_pixel_size),
        studio_light=space.shading.studio_light,
        environment_intensity=space.shading.studiolight_intensity,
        region_width=region.width, region_height=region.height,
        native_window_width=bpy.context.window.width, native_window_height=bpy.context.window.height,
        texture_quality=ts.preview_quality)
    live_preview.clear()
    clear_shared()
    if ARGS.scheduled:
        ts.dirty = False
    bpy.context.view_layer.update()
    STATE['handler'] = bpy.types.SpaceView3D.draw_handler_add(completed_draw, (), 'WINDOW', 'POST_PIXEL')
    STATE['pre_handler'] = bpy.types.SpaceView3D.draw_handler_add(begin_draw, (), 'WINDOW', 'PRE_VIEW')
    STATE['settle_until'], STATE['settle_frames'] = time.perf_counter() + 1., 3
    write_report()
    area.tag_redraw()


def tick():
    try:
        if STATE['finished']:
            return None
        if time.perf_counter() - STATE['started'] > ARGS.timeout:
            raise TimeoutError(f'Native frame benchmark exceeded {ARGS.timeout}s; state={STATE.get("pending")}')
        if STATE.get('error'):
            finish(STATE.pop('error'))
            return None
        if not STATE['registered']:
            setup()
            return .01
        if STATE['pending'] is not None:
            if ARGS.scheduled and scheduled_ready(STATE['pending']):
                observe_scheduled_ready(STATE['pending'], 'harness_timer_poll')
                STATE['area'].tag_redraw()
            return .01
        if (time.perf_counter() < STATE['settle_until']
                or STATE['frames'] < STATE['settle_frames']):
            STATE['area'].tag_redraw()
            return .01
        if not STATE['queue']:
            finish()
            return None
        submit(*STATE['queue'].pop(0))
        return .01
    except Exception:
        finish(traceback.format_exc())
        return None


bpy.app.timers.register(tick, first_interval=2.)
