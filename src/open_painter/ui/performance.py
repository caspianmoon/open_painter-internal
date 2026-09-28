"""Layer performance (0.9.0): profiling, bypass, layer caches and the paint-time freeze."""
import bpy

from ..core.layer_stack import active
from ..core.project_manager import require_set
from .operators import SafeOperator

COST_MIN_MS = 2.0     # a badge needs at least this much
COST_SHARE = .05      # ...and at least this share of the set's total
HEAVY_SHARE = .25     # ERROR icon from this share on, or when CPU boundaries re-render after edits

CACHE_ICONS = {'CACHED': 'FILE_CACHE', 'FROZEN': 'FREEZE', 'STALE': 'TIME', 'PENDING': 'SORTTIME'}


def badge(scene, ts, layer, rows=None, total=None):
    """(text, icon) for a profiled layer worth pointing at, else None."""
    from ..evaluation import profile
    rows = rows if rows is not None else {row['layer_id']: row for row in profile.rows(scene, ts)}
    row = rows.get(layer.id)
    if row is None or not row['profiled']:
        return None
    total = total if total is not None else sum(item['cost_ms'] for item in rows.values())
    cost = row['cost_ms']
    if cost < max(COST_MIN_MS, COST_SHARE * total):
        return None
    heavy = cost >= HEAVY_SHARE * total or row['boundary_ms'] > 0
    return f'{cost:.0f} ms', 'ERROR' if heavy else 'TIME'


def cache_icon(scene, ts, layer):
    """Icon of a cached layer's state, or None for a live layer."""
    from ..evaluation import profile
    if getattr(layer, 'cache_mode', 'LIVE') == 'LIVE':
        return None
    return CACHE_ICONS.get(profile.state(scene, ts, layer), 'FILE_CACHE')


def set_bypass(ts, identifiers, value):
    """Toggle bypass on several layers as one shader rebuild."""
    from ..evaluation.shader_preview import authoring_batch
    with authoring_batch(ts):
        for layer in ts.layers:
            if layer.id in identifiers and layer.bypass != value:
                layer.bypass = value


def _selection(context, ts):
    from .dock import state
    ui = state.get(context.area) if context.area is not None else None
    selected = set(getattr(ui, 'selected_layers', ()) or ())
    layer = active(ts)
    if layer is not None:
        selected.add(layer.id)
    return selected


class OP_OT_profile_layers(SafeOperator, bpy.types.Operator):
    bl_idname = 'open_painter.profile_layers'
    bl_label = 'Profile Layers'
    bl_description = ('Measure what each layer costs the preview: shader compile time, node and sampler '
                      'counts, CPU input work and a bounded CPU composite pass')
    bl_options = {'REGISTER'}
    action: bpy.props.EnumProperty(items=[('PROFILE', 'Profile', 'Run the profile pass'),
                                          ('CLEAR', 'Clear', 'Forget recorded costs')], default='PROFILE')
    resolution: bpy.props.EnumProperty(name='Resolution', default='512',
                                       items=[(v, v, f'Compose at {v} pixels') for v in ('256', '512', '1024')])

    def run(self, context):
        from ..evaluation import profile
        ts = require_set(context)
        scene = context.scene
        if self.action == 'CLEAR':
            profile.clear(scene, ts)
            scene.open_painter.status = 'Layer profile cleared'
        else:
            rows = profile.run(scene, ts, int(self.resolution))
            total = sum(row['cost_ms'] for row in rows)
            scene.open_painter.status = f'Profiled {len(rows)} layers · {total:.0f} ms'
        if context.area is not None:
            context.area.tag_redraw()


class OP_OT_engine_report(SafeOperator, bpy.types.Operator):
    bl_idname = 'open_painter.engine_report'
    bl_label = 'Engine Report'
    bl_description = ('Copy where the Open Painter engine spends its time on this computer: each stage '
                      '(build, update, CPU inputs, translate, compile, render, publish) of the recent updates, '
                      'and with Profile, every pass measured on the GPU')
    bl_options = {'REGISTER'}
    action: bpy.props.EnumProperty(items=[
        ('COPY', 'Copy Engine Report', 'Copy the report of the recent updates'),
        ('PROFILE', 'Profile Engine', 'Render every pass once more, timed on the GPU, then copy the report')],
        default='COPY')

    @classmethod
    def description(cls, context, properties):
        if properties.action == 'PROFILE':
            return ('Render the active set once more with every pass timed on the GPU (each layer, mask and '
                    'Mask Graph on its own), then copy the report to the clipboard')
        return cls.bl_description

    def run(self, context):
        from ..evaluation import shader_preview
        from ..evaluation.engine import stages
        ts = require_set(context)
        if not shader_preview.requested(ts):
            raise ValueError(f'"{ts.name}" uses the CPU texture reference, not the Open Painter engine')
        if self.action == 'PROFILE':
            shader_preview.profile_engine(context.scene, ts)
        text = stages.report(context.scene, ts)
        context.window_manager.clipboard = text
        print(text, flush=True)
        self.report({'INFO'}, 'Engine report copied to the clipboard' + (' (profiled)' if self.action == 'PROFILE' else ''))


class OP_OT_layer_bypass(SafeOperator, bpy.types.Operator):
    bl_idname = 'open_painter.layer_bypass'
    bl_label = 'Bypass Layers'
    bl_options = {'REGISTER', 'UNDO'}
    action: bpy.props.EnumProperty(items=[
        ('SELECTED', 'Bypass Selected', 'Skip the selected layers in the shader preview'),
        ('CLEAR_SELECTED', 'Restore Selected', 'Compile the selected layers again'),
        ('CLEAR_ALL', 'Restore All', 'Compile every bypassed layer again')], default='SELECTED')

    @classmethod
    def description(cls, context, properties):
        return {
            'SELECTED': 'Skip the selected layers in the shader preview only (export still includes them)',
            'CLEAR_SELECTED': 'Compile the selected layers into the preview again',
            'CLEAR_ALL': 'Compile every bypassed layer into the preview again'}[properties.action]

    def run(self, context):
        ts = require_set(context)
        if self.action == 'CLEAR_ALL':
            identifiers = {layer.id for layer in ts.layers if layer.bypass}
            set_bypass(ts, identifiers, False)
            context.scene.open_painter.status = f'Restored {len(identifiers)} bypassed layers'
            return
        identifiers = _selection(context, ts)
        set_bypass(ts, identifiers, self.action == 'SELECTED')
        context.scene.open_painter.status = (f'Bypassed {len(identifiers)} layers in the preview'
                                             if self.action == 'SELECTED' else
                                             f'Restored {len(identifiers)} layers')


class OP_OT_layer_cache(SafeOperator, bpy.types.Operator):
    bl_idname = 'open_painter.layer_cache'
    bl_label = 'Layer Cache'
    bl_options = {'REGISTER', 'UNDO'}
    action: bpy.props.EnumProperty(items=[
        ('REFRESH', 'Refresh Cache', 'Recompute the cached image of the active layer now'),
        ('CLEAR', 'Clear Cache', 'Compile the active layer live again and drop its cached images'),
        ('REFRESH_ALL', 'Refresh All Caches', 'Recompute every cached layer of the set now'),
        ('CLEAR_ALL', 'Clear All Caches', 'Compile every cached layer live again')], default='REFRESH')
    layer_id: bpy.props.StringProperty()

    @classmethod
    def description(cls, context, properties):
        return {'REFRESH': 'Recompute the cached image of the layer now',
                'CLEAR': 'Compile the layer live again and drop its cached images',
                'REFRESH_ALL': 'Recompute every cached layer of the texture set now',
                'CLEAR_ALL': 'Compile every cached layer of the texture set live again'}[properties.action]

    def run(self, context):
        from ..evaluation import layer_cache, shader_preview
        ts = require_set(context)
        scene = context.scene
        if self.action in {'REFRESH_ALL', 'CLEAR_ALL'}:
            targets = [layer for layer in ts.layers if layer.cache_mode != 'LIVE']
        else:
            layer = (next((item for item in ts.layers if item.id == self.layer_id), None)
                     if self.layer_id else active(ts))
            if layer is None:
                raise ValueError('Select a layer first')
            targets = [layer]
        if self.action in {'CLEAR', 'CLEAR_ALL'}:
            with shader_preview.authoring_batch(ts):
                for layer in targets:
                    layer_cache.release_layer(layer)
                    layer.cache_mode = 'LIVE'
            scene.open_painter.status = f'Cleared {len(targets)} layer caches'
            return
        if not shader_preview.requested(ts):
            raise ValueError('Layer caches serve the shader-native preview; switch the backend to Shader Native')
        shader_preview.tick(scene, ts)
        for layer in targets:
            if layer.cache_mode == 'LIVE':
                raise ValueError(f'"{layer.name}" is not cached; set its Cache mode first')
            layer_cache.refresh(scene, ts, layer.id)
        scene.open_painter.status = f'Refreshed {len(targets)} layer caches'
        if context.area is not None:
            context.area.tag_redraw()


class OP_MT_native_performance(bpy.types.Menu):
    bl_idname = 'OP_MT_native_performance'
    bl_label = 'Performance'

    def draw(self, context):
        layout = self.layout
        layout.operator('open_painter.profile_layers', icon='SORTTIME').action = 'PROFILE'
        layout.operator('open_painter.profile_layers', text='Clear Profile').action = 'CLEAR'
        layout.operator('open_painter.engine_report', text='Profile Engine', icon='SORTTIME').action = 'PROFILE'
        layout.operator('open_painter.engine_report', text='Copy Engine Report', icon='COPYDOWN').action = 'COPY'
        layout.operator('open_painter.offline_analysis', text='Performance Inventory', icon='INFO').action = 'REPORT'
        layout.separator()
        layout.operator('open_painter.layer_bypass', text='Bypass Selected', icon='RESTRICT_VIEW_ON').action = 'SELECTED'
        layout.operator('open_painter.layer_bypass', text='Restore Selected').action = 'CLEAR_SELECTED'
        layout.operator('open_painter.layer_bypass', text='Restore All Bypassed', icon='RESTRICT_VIEW_OFF').action = 'CLEAR_ALL'
        layout.separator()
        layout.operator('open_painter.layer_cache', text='Refresh All Caches', icon='FILE_REFRESH').action = 'REFRESH_ALL'
        layout.operator('open_painter.layer_cache', text='Clear All Caches', icon='X').action = 'CLEAR_ALL'
        layout.separator()
        layout.prop(context.scene.open_painter, 'freeze_while_painting')


def draw_freeze_status(layout, context, ts):
    """The set's shader status line, marking a stroke-time freeze, plus the toggle."""
    from ..evaluation.shader_preview import diagnostics, status
    info = diagnostics(context.scene, ts, details=False)
    layout.label(text=status(context.scene, ts), icon='TIME' if info.get('frozen') else 'INFO')
    layout.prop(context.scene.open_painter, 'freeze_while_painting')


def draw_performance_section(layout, context, ts, layer):
    """Collapsible Performance section of the layer properties panel."""
    from ..evaluation import profile, layer_cache
    from .native import _profile_rows
    scene = context.scene
    # The layer list of the same redraw already built the set's table.
    rows, total = _profile_rows(scene, ts)
    mark = badge(scene, ts, layer, rows, total)
    header, panel = layout.panel('painter_layer_performance', default_closed=True)
    header.label(text='Performance' + (f' · {mark[0]}' if mark else ''), icon=mark[1] if mark else 'SORTTIME')
    if not panel:
        return
    panel.use_property_split = True
    panel.use_property_decorate = False
    # Cache states move without an edit (a refresh landing), so this layer's
    # is read afresh rather than from the table.
    state_name = profile.state(scene, ts, layer)
    panel.prop(layer, 'bypass', text='Bypass in Preview')
    if layer.bypass:
        panel.label(text='Skipped in the live view; export and bakes still include it', icon='INFO')
    if layer.type == 'FOLDER' and layer.group_mode == 'PASSTHROUGH':
        panel.prop(layer, 'cache_mode', text='Cache')
        panel.label(text='A passthrough folder cannot be cached: set it to Isolated or cache its children', icon='ERROR')
    else:
        panel.prop(layer, 'cache_mode', text='Cache')
    if layer.cache_mode != 'LIVE':
        row = panel.row(align=True)
        op = row.operator('open_painter.layer_cache', text='Refresh Cache', icon='FILE_REFRESH')
        op.action, op.layer_id = 'REFRESH', layer.id
        op = row.operator('open_painter.layer_cache', text='Clear', icon='X')
        op.action, op.layer_id = 'CLEAR', layer.id
        from ..evaluation import cache as scheduler
        if state_name == 'PENDING':
            running = layer_cache.progress(scene)
            text = (f'Rendering cache… {int(running[1] * 100)}%' if running and running[0] == layer.name
                    else 'Cache refresh queued')
            panel.label(text=text, icon='SORTTIME')
        elif scheduler.painting_active(scene):
            panel.label(text='Cache paused while painting', icon='TIME')
        elif state_name == 'STALE':
            panel.label(text='Out of date · refreshes when edits settle' if layer.cache_mode == 'CACHED'
                        else 'Out of date · Refresh Cache to update', icon='TIME')
        elif state_name == 'BYPASSED':
            panel.label(text='Bypassed: the cache is not sampled', icon='INFO')
        else:
            images = [row.image for row in layer.cache if row.image is not None and row.image.has_data]
            size = sum(img.size[0] * img.size[1] * 16 for img in images) / (1024 * 1024)
            panel.label(text=f'Cached at {ts.resolution} · {len(images)} channels · {size:.0f} MiB', icon='CHECKMARK')
    row = rows.get(layer.id)
    if row is not None and (row['profiled'] or row['compile_ms'] or row['boundary_ms'] or row['gpu_ms']):
        col = panel.column(align=True)
        col.label(text=f"Compile {row['compile_ms']:.1f} ms · {row['nodes']} nodes · {row['samplers']} samplers")
        if row['boundary_ms'] > 0:
            col.label(text=f"CPU inputs {row['boundary_ms']:.0f} ms (re-render after edits)")
        if row['refresh_ms'] > 0:
            col.label(text=f"Cache refresh {row['refresh_ms']:.0f} ms")
        if row['gpu_ms'] > 0:
            col.label(text=f"GPU {row['gpu_ms']:.1f} ms per full update" + ('' if row['gpu_current'] else ' · out of date'))
        if row['profiled']:
            share = 100 * row['cost_ms'] / total if total else 0
            col.label(text=f"CPU reference {row['cpu_ms']:.0f} ms · {share:.0f}% of {total:.0f} ms"
                      + ('' if row['current'] else ' · out of date'))
        col.label(text=f"State {state_name.title()}")
    if row is None or not row['profiled']:
        panel.operator('open_painter.profile_layers', icon='SORTTIME').action = 'PROFILE'


CLASSES = (OP_OT_profile_layers, OP_OT_engine_report, OP_OT_layer_bypass, OP_OT_layer_cache,
           OP_MT_native_performance)
