"""Real-window drag cadence: a simulated mouse drag on the native opacity slider.

Launch with --enable-event-simulate. Mouse moves are queued at about 120 Hz, like a
trackpad, onto the product's RNA widget. The test counts RNA value changes, engine
evaluations and viewport POST_PIXEL draws during the drag, and how long each value
change takes to reach a drawn revision. No screenshots or readbacks happen inside
the measured window. This is event queue to POST_PIXEL, not OS input or scanout.

    OP_DRAG_REPORT   JSON report path (required)
    OP_DRAG_RES      texture set resolution (default 1024)
    OP_DRAG_SECONDS  drag duration (default 10)
    OP_DRAG_LAYERS   extra fill layers above the fixture (default 0)
    OP_DRAG_STACK    fill (default): OP_DRAG_LAYERS plain fills; smart: smart materials from the
                     catalog until the set has at least OP_DRAG_LAYERS more layers
    OP_DRAG_UI       isolated (default): the slider alone in its own sidebar tab; product: the
                     area mounted as the product's editor, the slider first in its Painter tab
                     and every product panel drawn below it
    OP_DRAG_GEOMETRY 1: add a triplanar layer, so the set reads its geometry
    OP_DRAG_PROFILE  1: cProfile the scheduler ticks, the slider's callback and (product UI) the
                     sidebar's panels and lists into the report
    OP_DRAG_DEPSGRAPH 1: count depsgraph updates by datablock and flags during the drag
"""
import json
import math
import os
from pathlib import Path
import sys
import time
import traceback

import bpy
from mathutils import Quaternion
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shader_preview import fixture, render_setup, open_painter, shader_preview
from open_painter.core import layer_stack
from open_painter.evaluation.engine import runtime

REPORT = Path(os.environ['OP_DRAG_REPORT'])
RESOLUTION = int(os.environ.get('OP_DRAG_RES', '1024'))
SECONDS = float(os.environ.get('OP_DRAG_SECONDS', '10'))
EXTRA_LAYERS = int(os.environ.get('OP_DRAG_LAYERS', '0'))
PROFILE = os.environ.get('OP_DRAG_PROFILE') == '1'   # cProfile the scheduler ticks of the drag
DEPSGRAPH = os.environ.get('OP_DRAG_DEPSGRAPH') == '1'   # count depsgraph updates by kind during the drag
GEOMETRY = os.environ.get('OP_DRAG_GEOMETRY') == '1'   # one triplanar layer: the set reads its geometry
STACK = os.environ.get('OP_DRAG_STACK', 'fill')
assert STACK in {'fill', 'smart'}, STACK
UI = os.environ.get('OP_DRAG_UI', 'isolated')
assert UI in {'isolated', 'product'}, UI
CATEGORY = 'Painter' if UI == 'product' else 'Drag Cadence'
profiler = None
updates = {}
bpy.context.preferences.view.show_splash = False
deadline = time.monotonic() + 180
changes, evaluations, draws, ticks = [], [], [], []
measuring = False
ts = ids = area = region = sidebar = window = point = None
handler = None
original_evaluate = runtime.Engine.evaluate
original_notify = shader_preview.notify


class OP_PT_drag_cadence(bpy.types.Panel):
    """The product's native RNA slider, isolated so event coordinates are reliable."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = CATEGORY
    bl_label = 'Drag cadence'
    bl_options = {'HIDE_HEADER'}
    bl_order = -1000   # first in the tab: calibration presses near its top

    @classmethod
    def poll(cls, context):
        return ts is not None

    def draw(self, context):
        self.layout.prop(coat(), 'opacity', text='Coat Opacity', slider=True)


def coat():
    return next(layer for layer in ts.layers if layer.id == ids['coat'])


def evaluated(self, *args, **kwargs):
    started = time.perf_counter()
    value = float(coat().opacity) if ts is not None else None
    result = original_evaluate(self, *args, **kwargs)
    if measuring and result:
        evaluations.append(dict(start=started, end=time.perf_counter(), value=value, channels=len(result),
                                passes=self.report.get('passes')))
    return result


def notified(owner, *args, **kwargs):
    if measuring:
        changes.append(dict(time=time.perf_counter(), value=float(coat().opacity)))
    return original_notify(owner, *args, **kwargs)


def counted(scene, depsgraph):
    if measuring:
        for update in depsgraph.updates:
            flags = ''.join(flag for flag, on in (('G', update.is_updated_geometry), ('T', update.is_updated_transform),
                                                   ('S', update.is_updated_shading)) if on)
            name = f'{type(update.id.original).__name__}:{update.id.original.name}:{flags}'
            updates[name] = updates.get(name, 0) + 1


def drawn():
    if measuring:
        draws.append(dict(time=time.perf_counter(), evaluations=len(evaluations)))


def event(kind, value, xy):
    with bpy.context.temp_override(window=window, area=area, region=sidebar or region):
        window.event_simulate(type=kind, value=value, x=int(xy[0]), y=int(xy[1]))


def later(function, delay=.05):
    def run():
        try:
            assert time.monotonic() < deadline, 'Drag cadence test timed out'
            return function()
        except Exception:
            finish(traceback.format_exc())
            return None
    bpy.app.timers.register(run, first_interval=delay)


def percentile(values):
    return {f'p{p}': float(np.percentile(values, p)) for p in (50, 95, 99)} if values else {}


def summarize(start, end):
    seconds = end - start
    new_draws, last = 0, 0
    for draw in draws:
        if draw['evaluations'] != last:
            new_draws += 1
            last = draw['evaluations']
    # A revision's latency: from the first value change it includes to its first draw.
    latencies, undrawn, previous = [], 0, None
    for index, evaluation in enumerate(evaluations):
        since = previous['start'] if previous is not None else start
        first_change = next((c['time'] for c in changes if since <= c['time'] <= evaluation['start']), None)
        drawn_at = next((d['time'] for d in draws if d['evaluations'] > index and d['time'] >= evaluation['end']), None)
        if drawn_at is None:
            undrawn += 1
        elif first_change is not None:
            latencies.append(1000 * (drawn_at - first_change))
        previous = evaluation
    return dict(seconds=seconds, value_changes_per_s=len(changes) / seconds,
                evaluations_per_s=len(evaluations) / seconds, draws_per_s=len(draws) / seconds,
                new_revision_draws_per_s=new_draws / seconds, undrawn_evaluations=undrawn,
                change_to_draw_ms=percentile(latencies),
                evaluation_ms=percentile([1000 * (e['end'] - e['start']) for e in evaluations]),
                tick_ms=percentile([1000 * (b - a) for a, b in ticks]), ticks_per_s=len(ticks) / seconds,
                passes_per_evaluation=percentile([e['passes'] for e in evaluations if e['passes'] is not None]))


def finish(error=None, summary=None):
    global handler
    if handler is not None:
        bpy.types.SpaceView3D.draw_handler_remove(handler, 'WINDOW')
        handler = None
    profile_text = None
    if profiler is not None:
        import io
        import pstats
        stream = io.StringIO()
        pstats.Stats(profiler, stream=stream).sort_stats('tottime').print_stats(40)
        stream.write('\n==== cumulative\n')
        pstats.Stats(profiler, stream=stream).sort_stats('cumulative').print_stats(45)
        stream.write('\n==== sidebar\n')
        pstats.Stats(profiler, stream=stream).sort_stats('cumulative').print_stats('open_painter/ui/', 30)
        profile_text = stream.getvalue()
    report = dict(status='FAIL' if error else 'PASS', error=error, summary=summary, profile=profile_text, ui=UI,
                  depsgraph_updates=updates or None,
                  resolution=RESOLUTION, layers=len(ts.layers) if ts is not None else None,
                  blender=bpy.app.version_string, viewport=[region.width, region.height] if region else None,
                  measurement='Queued mouse moves on the native slider to POST_PIXEL; not OS input or scanout.')
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2) + '\n')
    print('DRAG_CADENCE_' + report['status'], json.dumps(summary) if summary else error, flush=True)
    bpy.ops.wm.quit_blender()


_depth = [0]


def profiled(function):
    """``function`` under the drag's profiler; nested calls keep the outer enable."""
    def wrapper(*args, **kwargs):
        active = measuring and profiler is not None and _depth[0] == 0
        _depth[0] += 1
        if active:
            profiler.enable()
        try:
            return function(*args, **kwargs)
        finally:
            _depth[0] -= 1
            if active:
                profiler.disable()
    return wrapper


def profiled_method(function):
    """``profiled`` with the method's own parameters: Blender calls a panel's or a list's
    method with as many arguments as its signature names."""
    code = function.__code__
    names = ', '.join(code.co_varnames[:code.co_argcount])
    namespace = {'call': profiled(function)}
    exec(f'def method({names}):\n    return call({names})\n', namespace)
    return namespace['method']


def smart_stack(ts, target):
    """Catalog smart materials above the fixture, over smooth synthetic mesh maps (as
    native_deep_stack.py builds them) so every recipe's mask finds its map."""
    from open_painter.core import image_manager as images
    from open_painter.smart_materials import composer
    from open_painter.smart_materials.catalog import CATALOG
    size = 512
    y, x = np.indices((size, size), dtype=np.float32) / (size - 1)
    for index, kind in enumerate(('CURVATURE', 'AO', 'WORLD_NORMAL', 'POSITION', 'NORMAL', 'MEAN_CURVATURE',
                                  'BEVEL_MASK', 'BEVEL_NORMAL')):
        data = np.ones((size, size, 4), np.float32)
        wave = .5 + .5 * np.sin(6.2831 * (x * (index + 2) + y * (3 - index % 3)))
        data[..., 0], data[..., 1], data[..., 2] = wave, .5 + .5 * np.cos(6.2831 * y * (index + 1)), 1 - wave * .5
        image = images.create(f'Cadence {kind}', size)
        images.write(image, data)
        entry = ts.baked_maps.add()
        entry.kind, entry.image = kind, image
    names, index = sorted(CATALOG), 0
    while len(ts.layers) < target:
        # Applied above the active layer: keep the coat selected so the stack grows over it.
        ts.active_layer = next(i for i, layer in enumerate(ts.layers) if layer.id == ids['coat'])
        composer.apply(ts, names[index % len(names)], seed=index + 1)
        index += 1


def setup():
    global ts, ids, area, region, window, handler
    open_painter.register()
    bpy.utils.register_class(OP_PT_drag_cadence)
    window = bpy.context.window
    assert hasattr(window, 'event_simulate'), 'Launch Blender with --enable-event-simulate'
    area = max((a for a in window.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=window, area=area, region=region):
        ts, _, ids, _ = fixture(bpy.context.scene, resolution=RESOLUTION)
        if STACK == 'smart':
            smart_stack(ts, len(ts.layers) + EXTRA_LAYERS)
        else:
            for index in range(EXTRA_LAYERS):
                layer = layer_stack.add(ts, 'FILL', f'Cadence fill {index}')
                layer_stack.channel_for(layer, 'BASE_COLOR').color = (.1 + .8 * (index % 5) / 5, .4, .6, 1)
                layer.opacity = .3
        if GEOMETRY:
            layer = layer_stack.add(ts, 'FILL', 'Cadence triplanar')
            layer_stack.channel_for(layer, 'ROUGHNESS').projection = 'TRIPLANAR'
            layer.opacity = .3
        # Edit the coat, now below any extra layers.
        ts.active_layer = next(i for i, layer in enumerate(ts.layers) if layer.id == ids['coat'])
        render_setup(bpy.context.scene)
        bpy.context.scene.render.engine = 'BLENDER_EEVEE'
        space = area.spaces.active
        space.shading.type = 'RENDERED'
        space.overlay.show_overlays = False
        space.show_gizmo = False
        space.show_region_ui = True
        space.region_3d.view_perspective = 'ORTHO'
        space.region_3d.view_rotation = Quaternion((1, 0, 0, 0))
        space.region_3d.view_location = (0, 0, 0)
        space.region_3d.view_distance = 3
    if UI == 'product':
        # The area as the product opens it: its Painter panels, toolbar, asset shelf and
        # material shading.
        from open_painter.ui import editor
        editor.mount(window, area)
    handler = bpy.types.SpaceView3D.draw_handler_add(drawn, (), 'WINDOW', 'POST_PIXEL')
    if DEPSGRAPH:
        bpy.app.handlers.depsgraph_update_post.append(counted)
    from open_painter.evaluation import cache
    original_tick = cache._tick

    ticked = profiled(original_tick)

    def traced_tick():
        started = time.perf_counter()
        try:
            return ticked()
        finally:
            if measuring:
                ticks.append((started, time.perf_counter()))
    cache._tick = traced_tick
    if PROFILE:
        # The slider's own RNA callback runs outside the scheduler tick: profile it too.
        from open_painter.properties import layer as layer_properties
        layer_properties._changed = profiled(layer_properties._changed)
        if UI == 'product':
            # Every edit redraws the sidebar: the product panels and the layer list too.
            for panel in bpy.types.Panel.__subclasses__():
                if getattr(panel, 'bl_category', '') == CATEGORY and panel is not OP_PT_drag_cadence:
                    panel.draw = profiled_method(panel.draw)
            for ui_list in bpy.types.UIList.__subclasses__():
                for name in ('filter_items', 'draw_item'):
                    if name in ui_list.__dict__:
                        setattr(ui_list, name, profiled_method(ui_list.__dict__[name]))
    later(wait_ready, .5)


def wait_ready():
    global sidebar, candidates
    area.tag_redraw()
    if not shader_preview.current(bpy.context.scene, ts):
        later(wait_ready, .1)
        return
    sidebar = next(r for r in area.regions if r.type == 'UI' and r.width > 20)
    with bpy.context.temp_override(window=window, area=area, region=sidebar):
        sidebar.active_panel_category = CATEGORY
    scale = bpy.context.preferences.system.ui_scale
    candidates = [(sidebar.x + int(sidebar.width * fraction), sidebar.y + sidebar.height - int(offset * scale))
                  for offset in (20, 30, 40, 50, 60) for fraction in (.55, .72)]
    later(lambda: calibrate(0), .3)


def calibrate(attempt):
    """The same sequence as shader_preview_ui: hover, press, then drag past the threshold."""
    global point
    assert attempt < len(candidates), 'Could not find the opacity slider'
    point = candidates[attempt]
    before = float(coat().opacity)
    event('ESC', 'PRESS', point)
    event('ESC', 'RELEASE', point)
    event('MOUSEMOVE', 'NOTHING', point)

    def press():
        event('LEFTMOUSE', 'PRESS', point)
        later(first_move, .1)

    def first_move():
        event('MOUSEMOVE', 'NOTHING', (point[0] + 20, point[1]))
        later(second_move, .15)

    def second_move():
        event('MOUSEMOVE', 'NOTHING', (point[0] + 60, point[1]))
        later(check, .15)

    def check():
        changed = abs(float(coat().opacity) - before) >= .005
        event('LEFTMOUSE', 'RELEASE', (point[0] + 60, point[1]))
        if changed:
            later(settle, .5)
        else:
            later(lambda: calibrate(attempt + 1), .2)
    later(press, .15)


def settle():
    if not shader_preview.current(bpy.context.scene, ts):
        later(settle, .1)
        return
    later(start_drag, .5)


def start_drag():
    global measuring, drag_start
    evaluations.clear()
    changes.clear()
    draws.clear()
    ticks.clear()
    event('MOUSEMOVE', 'NOTHING', point)
    event('LEFTMOUSE', 'PRESS', point)
    # Past the drag threshold before measuring, as a hand does.
    event('MOUSEMOVE', 'NOTHING', (point[0] + 20, point[1]))
    global profiler
    if PROFILE:
        import cProfile
        profiler = cProfile.Profile()
    measuring = True
    drag_start = time.perf_counter()
    print('DRAG_CADENCE_START', os.getpid(), flush=True)   # a whole-process sampler can attach here
    later(move, 1 / 120)


def move():
    global measuring
    elapsed = time.perf_counter() - drag_start
    if elapsed < SECONDS:
        # A steady back-and-forth drag across the slider.
        event('MOUSEMOVE', 'NOTHING', (point[0] + 40 + 35 * math.sin(elapsed * 2.4), point[1]))
        return 1 / 120
    end = time.perf_counter()
    measuring = False
    event('LEFTMOUSE', 'RELEASE', point)
    summary = summarize(drag_start, end)
    assert summary['value_changes_per_s'] > 10, summary
    later(lambda: finish(summary=summary), .3)
    return None


runtime.Engine.evaluate = evaluated
shader_preview.notify = notified
REPORT.parent.mkdir(parents=True, exist_ok=True)
later(setup, 1)
