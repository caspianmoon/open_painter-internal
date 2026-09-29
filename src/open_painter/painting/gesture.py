"""One material gesture: resampled input, explicit scope and a live preview of the stroke.

The Vector Brush records a gesture as stroke data (``strokes.record``); this module gives
the gesture the properties the everyday material brush needs (add-on quality remedy, M1):

* ``resample`` — the path between two pointer events is walked at the brush spacing in
  screen space, each interpolated point re-projected onto the surface by the operator,
  so a fast drag leaves no holes and the coverage does not depend on the event rate. A
  miss or an ineligible surface breaks the segment: coming back never bridges the gap.
* ``Scope`` — which texture sets, objects and material slots may receive the gesture,
  decided before the first sample: the active set by default, every set of the project
  when the artist asks to paint across sets (and then only the sets the pointer crossed
  link the stroke).
* ``Preview`` — while the pointer is down, the enabled channels of the layer show the
  stroke on the model: bounded preview images (at most ``PREVIEW_LIMIT``²) take the
  place of the layer's shader inputs for the duration of the gesture, updated in the rows
  the new samples touch with the same kernel and compositing the committed stroke uses.
  On release the recorded stroke replaces the preview through the ordinary evaluation:
  the preview stays bound until that leaf publishes, so nothing flickers.
"""
from dataclasses import dataclass, field
from time import perf_counter

import numpy as np

PREVIEW_LIMIT = 1024       # preview side for one or two channels; four channels share the same budget
PREVIEW_TEXELS = 2 * 1024 * 1024
PREVIEW_INTERVAL = .016    # seconds between preview publications (coalesces pointer events within a frame)
RETIRE_TIMEOUT = 20.0      # seconds a retired preview may stay bound waiting for its leaf
RETIRE_PREVIEWS = True     # keep the preview bound after release until the committed leaf publishes
ENTRY_KEYS = ('tri_vertices', 'tri_loops', 'positions', 'world_normals', 'uv',
              'all_tri_vertices', 'all_tri_polygons', 'tri_indices')   # the last three: surface reach


# ---- input resampling ------------------------------------------------------------------------

def resample(start, end, step, attributes_start=(), attributes_end=()):
    """Points from ``start`` (exclusive) to ``end`` (inclusive) every ``step`` pixels along
    the screen segment, with linearly interpolated attributes (pressure, tilt): a gesture
    sampled at 20 Hz and one at 200 Hz walk the same path."""
    start, end = np.asarray(start, np.float64), np.asarray(end, np.float64)
    length = float(np.linalg.norm(end - start))
    count = max(1, int(np.ceil(length / max(step, 1e-6))))
    t = np.arange(1, count + 1, dtype=np.float64) / count
    points = start[None] + (end - start)[None] * t[:, None]
    a0, a1 = np.asarray(attributes_start, np.float64), np.asarray(attributes_end, np.float64)
    attributes = a0[None] + (a1 - a0)[None] * t[:, None] if len(a0) else np.zeros((count, 0))
    return points, attributes


@dataclass
class Scope:
    """Objects and material slots a gesture may paint, per participating texture set."""
    sets: list                      # texture set ids, the active set first
    slots: dict = field(default_factory=dict)   # object pointer -> {slot index -> set id}

    @classmethod
    def of(cls, project, ts, across_sets=False):
        members = [ts] + ([other for other in project.texture_sets if other.id != ts.id and other.assignments]
                          if across_sets else [])
        slots = {}
        for member in members:
            for ref in member.assignments:
                if ref.object is not None:
                    slots.setdefault(ref.object.as_pointer(), {})[ref.slot_index] = member.id
        return cls([member.id for member in members], slots)

    def owner(self, original, material_index):
        """The set id owning faces of this material slot of the (original) object, or None."""
        table = self.slots.get(original.as_pointer())
        return table.get(int(material_index)) if table else None

    def names(self, project):
        by_id = {ts.id: ts.name for ts in project.texture_sets}
        return [by_id.get(identifier, identifier) for identifier in self.sets]


def geometry_signature(scope, project=None):
    """What a gesture's surface references depend on, read from the existing dependency
    revisions rather than by hashing the meshes: for every assigned object its mesh's and
    its own evaluated-geometry epochs (UV edits, vertex motion, modifier changes and their
    inputs all report there), its modifier values, transform, mode and visibility, and the
    participating sets' slot assignments. A change discards the gesture rather than
    redirecting it."""
    import bpy
    from ..evaluation import dependencies
    parts = []
    for obj in bpy.data.objects:
        if obj.as_pointer() not in scope.slots:
            continue
        mesh = obj.data
        parts.append((obj.as_pointer(), mesh.as_pointer() if mesh else 0, obj.mode,
                      dependencies.mesh_epoch(mesh) if mesh else 0, dependencies.object_epoch(obj),
                      dependencies.modifier_signature(obj),
                      len(mesh.vertices) if mesh else 0, len(mesh.polygons) if mesh else 0,
                      obj.hide_viewport, obj.hide_get(),
                      tuple(round(v, 6) for row in obj.matrix_world for v in row)))
    if project is not None:
        for ts in project.texture_sets:
            if ts.id in scope.sets:
                parts.append((ts.id, tuple((ref.object.as_pointer() if ref.object is not None else 0, ref.slot_index)
                                           for ref in ts.assignments)))
    return tuple(parts)


def brush_snapshot(settings):
    """The brush values frozen for one gesture (a plain namespace): the preview, the
    re-projection and the committed record read these, so a setting changed while the pointer
    is down applies to the next gesture."""
    from types import SimpleNamespace
    values = {}
    for prop in settings.bl_rna.properties:
        key = prop.identifier
        if key == 'rna_type':
            continue
        value = getattr(settings, key)
        if isinstance(value, (set, frozenset)):
            value = frozenset(value)
        elif getattr(prop, 'is_array', False):
            value = tuple(value)
        values[key] = value
    return SimpleNamespace(**values)


def target_state(scene, ts_id, layer_id, mask_id=''):
    """(ts, layer, mask, reason): the material destination resolved from ids, or why it is not
    paintable now — gone, hidden, under a hidden folder, or a mask that no longer renders
    strokes. Shared by the tool's pointer-down and the running gesture's checks."""
    project = scene.open_painter if scene is not None else None
    ts = next((item for item in project.texture_sets if item.id == ts_id), None) if project is not None else None
    if ts is None:
        return None, None, None, 'the texture set is gone'
    layer = next((item for item in ts.layers if item.id == layer_id), None)
    if layer is None:
        return ts, None, None, 'the target layer is gone'
    if not ts.assignments or any(ref.object is None for ref in ts.assignments):
        return ts, layer, None, 'the texture set is not assigned to a mesh'
    visible = layer.visible
    by_id = {item.id: item for item in ts.layers}
    cursor, seen = layer.parent_id, {layer.id}
    while visible and cursor:
        parent = by_id.get(cursor)
        if parent is None or cursor in seen or not parent.visible:
            visible = False
            break
        seen.add(cursor)
        cursor = parent.parent_id
    if not visible:
        return ts, layer, None, 'the target layer is hidden (or lies under a hidden folder)'
    if mask_id:
        mask = next((item for item in layer.masks if item.id == mask_id), None)
        if mask is None:
            return ts, layer, None, 'the target mask is gone'
        if mask.source != 'STROKES' or not mask.stroke_key:
            return ts, layer, mask, 'the target mask no longer renders vector strokes'
        return ts, layer, mask, ''
    if layer.type != 'PAINT':
        return ts, layer, None, 'material strokes live on paint layers'
    return ts, layer, None, ''


def snapshot_entries(ts):
    """The set's evaluated geometry arrays, copied out of the temporary meshes so the
    gesture never reads an evaluated mesh the depsgraph may free under it."""
    from ..geometry.snapshots import targets
    with targets(ts) as raw:
        return [{key: np.ascontiguousarray(target[key]) for key in ENTRY_KEYS} for target in raw]


# ---- live preview --------------------------------------------------------------------------------

def preview_size(n, channels):
    """Preview side: at most PREVIEW_LIMIT, and PREVIEW_TEXELS across all channels (a power of two)."""
    size = min(int(n), PREVIEW_LIMIT)
    while size > 128 and size * size * max(1, channels) > PREVIEW_TEXELS:
        size //= 2
    return size


def _native_metal():
    from ..native_bridge import lifecycle
    if not lifecycle.diagnostics()['native_loaded']:
        return False
    from ..evaluation.engine.runtime import device
    return lifecycle.execution_library(device().backend) is not None


def _shrink(data, factor):
    """Point-sampled reduction: a preview, not a filter, so it costs a strided copy."""
    return np.ascontiguousarray(data[::factor, ::factor])


def _straight(rgba):
    """Straight colour from premultiplied pixels (a native float canvas)."""
    out = np.array(rgba, np.float32, copy=True)
    alpha = out[..., 3:4]
    np.divide(out[..., :3], alpha, out=out[..., :3], where=alpha > 1e-8)
    out[..., :3][alpha[..., 0] <= 1e-8] = 0
    return out


class Preview:
    """Bounded preview images bound to the layer's channel inputs for the gesture's duration."""

    def __init__(self, scene, ts, layer, channels, entries, *, gpu=True):
        """``entries`` are the set's evaluated geometry arrays (``snapshot_entries``)."""
        import bpy
        from ..core import image_manager as images
        from ..evaluation import shader_preview
        from ..evaluation.shader_resources import RESOURCE
        self.scene, self.ts_id, self.layer_id = scene, ts.id, layer.id
        self.n = int(ts.resolution)
        self.size = self.n if _native_metal() else preview_size(self.n, len(channels))
        self.factor = max(1, self.n // self.size)
        self.entries = entries
        self.channels = list(channels)
        self.images, self.bases, self.nodes, self.premultiplied, self.buffers = {}, {}, {}, {}, {}
        self.resource_sources = {}
        self.coverage = np.zeros((self.size, self.size), np.float32)
        self.covered = np.zeros((self.size, self.size), bool)
        self.last_publish = 0.0
        self.dirty = None
        self.publications = 0
        self.profile = {'stamp_ms': [], 'publish_ms': [], 'init_ms': 0.0}
        self.active = False
        self.gpu = None
        self.retired_at = None
        self.resolved_samples, self.resolved_dabs, self.resolved_anchors = [], [], []
        opened = perf_counter()
        graph = shader_preview.session_graph(scene, ts) if shader_preview.requested(ts) else None
        if graph is None:
            return                      # no live shader: the committed stroke shows after release
        if gpu:
            candidates = {}
            for node in graph.nodes():
                resource = getattr(node, 'gpu_resource', None)
                channel = self._channel_of(node, layer) if resource is not None else None
                if channel in self.channels and tuple(resource.dimensions) == (self.n, self.n):
                    candidates.setdefault(channel, (node, resource))
            if len(candidates) == len(self.channels):
                from .gpu_strokes import Canvas, UnsupportedSurface
                from .gpu_sources import preview_surface
                from ..evaluation.engine.resources import TextureResource
                try:
                    bases = {c: TextureResource(r.identity, r.texture, r.format, r.mapping) for c, (_, r) in candidates.items()}
                    self.gpu = Canvas(entries, self.size, bases, {c: False for c in candidates},
                                      surface=preview_surface(scene, ts, self.size, entries), precise=True, set_id=ts.id)
                except UnsupportedSurface:
                    pass
                else:
                    for channel, (node, resource) in candidates.items():
                        self.resource_sources[channel] = resource
                        self.nodes[channel] = node, node.image
                        self.premultiplied[channel] = False
                        node.gpu_resource = self.gpu.outputs[channel]
                    self.active = True
                    self._inputs_changed()
                    self.profile['init_ms'] = (perf_counter() - opened) * 1000
                    return
        for node in graph.nodes():
            resource = getattr(node, 'gpu_resource', None)
            if node.bl_idname != 'ShaderNodeTexImage' or (node.image is None and resource is None):
                continue
            channel = self._channel_of(node, layer)
            if channel is None or channel not in self.channels or channel in self.images:
                continue
            source = node.image
            if tuple(resource.dimensions if resource is not None else source.size) != (self.n, self.n):
                continue
            if resource is not None:
                from ..evaluation.engine import runtime
                # The bounded preview uses exact point samples. Download only
                # those texels, instead of expanding and copying the full canvas
                # before discarding most of it in _shrink.
                base = np.array(runtime.device().readback_sampled(resource.texture, self.size)).reshape(self.size, self.size, 4)
                if resource.mapping == 'rg':
                    base = base[..., [0, 0, 0, 1]]
                self.resource_sources[channel] = resource
                node.gpu_resource = None
            else:
                base = images.pixels(source)
                if self.factor > 1:
                    base = _shrink(base, self.factor)
            premultiplied = resource is None and bool(source.is_float and not source.get(RESOURCE))
            if premultiplied:
                base = _straight(base)
            preview = _new_preview_image(self.size)
            preview.colorspace_settings.name = source.colorspace_settings.name if source is not None else 'Non-Color'
            preview.alpha_mode = source.alpha_mode if source is not None else 'STRAIGHT'
            initial = base
            if premultiplied:
                initial = np.array(base, copy=True)
                initial[..., :3] *= initial[..., 3:4]
            preview.pixels.foreach_set(np.ascontiguousarray(initial).ravel())
            preview.update()
            self.images[channel], self.bases[channel], self.nodes[channel] = preview, base, (node, source)
            self.premultiplied[channel] = premultiplied
            node.image = preview
        self.active = bool(self.images)
        if self.active and gpu:
            from .gpu_strokes import Canvas, UnsupportedSurface
            from ..evaluation.engine.headless import Node
            if all(isinstance(node, Node) for node, _ in self.nodes.values()):
                try:
                    from .gpu_sources import preview_surface
                    self.gpu = Canvas(entries, self.size, self.bases, self.premultiplied,
                                      surface=preview_surface(scene, ts, self.size, entries), precise=True, set_id=ts.id)
                    for channel, (node, _) in self.nodes.items():
                        node.gpu_resource = self.gpu.outputs[channel]
                except UnsupportedSurface:
                    pass
                except Exception:
                    from ..utils.logging import logger
                    logger.exception('GPU brush unavailable; using CPU preview')
        if self.active:
            self._inputs_changed()
        self.profile['init_ms'] = (perf_counter() - opened) * 1000

    def _inputs_changed(self):
        """The engine samples the nodes' images: have it re-render what reads them."""
        from ..evaluation import shader_preview
        try:
            ts = next((item for item in self.scene.open_painter.texture_sets if item.id == self.ts_id), None)
        except ReferenceError:
            return
        if ts is not None:
            shader_preview.inputs_changed(self.scene, ts)

    @staticmethod
    def _channel_of(node, layer):
        """The layer channel a shader image node stands for: its strokes boundary, its paint canvas."""
        import json
        if node.get('op_stroke_layer') == layer.id:
            return node.get('op_stroke_channel')
        resource = getattr(node, 'gpu_resource', None)
        if resource is not None and getattr(resource, 'layer_id', None) == layer.id:
            return resource.channel
        descriptor = node.get('op_boundary', '')
        if descriptor:
            try:
                parts = json.loads(descriptor)
            except ValueError:
                return None
            if len(parts) >= 3 and parts[0] == 'strokes' and parts[1] == layer.id:
                return parts[2]
            return None
        for prop in layer.channels:
            if prop.image is not None and node.image == prop.image:
                return prop.channel
        return None

    def stamp(self, samples, stroke_settings, anchors=None):
        """Rasterise new ``samples`` (with their ``anchors``) into the coverage and publish the
        rows they touch; a SURFACE gesture reaches the same surface here as its record will."""
        if not self.active or not len(samples):
            return
        started = perf_counter()
        if stroke_settings['blend'] == 'SMUDGE' and self.gpu is None or any(
                'values' in dab or 'planar' in dab or 'alpha' in dab for dab in stroke_settings.get('dabs', ())):
            self._resolved_stamp(samples, stroke_settings, anchors)
            self.profile['stamp_ms'].append((perf_counter() - started) * 1000)
            return
        if self.gpu is not None:
            self.gpu.stamp(samples, stroke_settings, anchors)
            self.profile['stamp_ms'].append((perf_counter() - started) * 1000)
            self.publish(stroke_settings)
            return
        from types import SimpleNamespace
        from .stroke_raster import _batches, bounds, eligibility, kernel, reach_for, uv_window
        from ..geometry.uv_rasterizer import interpolate, rasterize
        samples = np.asarray(samples, np.float32)
        lo, hi = bounds(samples)
        size = self.size
        flat = self.coverage.reshape(-1)
        rows_touched = []
        pseudo = SimpleNamespace(reach=stroke_settings.get('reach', 'DEPTH'), depth_limit=stroke_settings['depth_limit'],
                                 anchors='')
        reaches = reach_for(self.entries, pseudo, samples, stroke_settings['planar'], anchors=anchors)
        for target, sheets in zip(self.entries, reaches):
            tri_vertices, tri_loops = target['tri_vertices'], target['tri_loops']
            corners = target['positions'][tri_vertices]
            chosen = np.flatnonzero(np.all(corners.max(axis=1) >= lo, axis=1) & np.all(corners.min(axis=1) <= hi, axis=1))
            if not len(chosen):
                continue
            uv = target['uv'][tri_loops[chosen]]
            window = uv_window(corners[chosen], uv, lo, hi, margin=1.5 / size)
            for batch in _batches(uv, size):
                rows, cols, tri, weights = rasterize(uv[batch], size, unique=False, clip=window[batch])
                if not len(rows):
                    continue
                picked = chosen[batch][tri]
                positions = interpolate(weights, target['positions'][tri_vertices[picked]])
                normals = interpolate(weights, target['world_normals'][tri_loops[picked]])
                alpha = kernel(positions, normals, samples, stroke_settings['hardness'], stroke_settings['flow'],
                               stroke_settings['accumulate'], stroke_settings['planar'], stroke_settings['alpha_texture'],
                               stroke_settings['alpha_rotation'], stroke_settings['alpha_angle'],
                               stroke_settings['depth_limit'], eligible=eligibility(sheets, target, picked))
                self.covered[rows, cols] = True
                hit = alpha > 0
                if not hit.any():
                    continue
                index = rows[hit] * size + cols[hit]
                if stroke_settings['accumulate'] == 'FLOW':
                    union = np.zeros(len(flat), np.float32)
                    np.maximum.at(union, index, alpha[hit])
                    touched = np.unique(index)
                    flat[touched] = 1.0 - (1.0 - flat[touched]) * (1.0 - union[touched])
                else:
                    np.maximum.at(flat, index, alpha[hit])
                rows_touched.append((int(rows[hit].min()), int(rows[hit].max()) + 1))
        if rows_touched:
            low = max(0, min(r[0] for r in rows_touched) - 1)
            high = min(size, max(r[1] for r in rows_touched) + 1)
            self.dirty = (low, high) if self.dirty is None else (min(self.dirty[0], low), max(self.dirty[1], high))
        self.profile['stamp_ms'].append((perf_counter() - started) * 1000)
        if rows_touched:
            self.publish(stroke_settings)

    def _resolved_stamp(self, samples, settings, anchors):
        """Exact bounded CPU preview for unsupported resolved-dab combinations."""
        from types import SimpleNamespace
        from . import payload, stroke_raster
        from .surface_reach import pack_anchors
        if self.gpu is not None:
            for channel, (node, _) in self.nodes.items():
                node.gpu_resource = None
            self.gpu.close()
            self.gpu = None
        self.resolved_samples.extend(np.asarray(samples).tolist())
        self.resolved_dabs.extend(settings.get('dabs', [{} for _ in samples]))
        if anchors is not None:
            self.resolved_anchors.extend(np.asarray(anchors).tolist())
        values = settings['values']
        data = dict(vars(values)) if hasattr(values, '__dict__') else vars(brush_snapshot(values))
        data.update(samples=stroke_raster.pack_samples(self.resolved_samples),
                    dab_payload=payload.encode(self.resolved_dabs), anchors=pack_anchors(self.resolved_anchors),
                    plane_normal=settings['planar'] or (0, 0, 1), alpha_angle=settings['alpha_angle'],
                    alpha_rotation=settings['alpha_rotation'])
        record = SimpleNamespace(**data)
        for channel, image in self.images.items():
            pixels = payload.replay_entries(self.entries, channel, [record], self.size, self.bases[channel])
            if self.premultiplied[channel]:
                pixels[..., :3] *= pixels[..., 3:4]
            image.pixels.foreach_set(pixels.ravel())
            image.update()
            image['open_painter_pixel_revision'] = image.get('open_painter_pixel_revision', 0) + 1
        self._inputs_changed()
        self.publications += 1

    def publish(self, stroke_settings, force=False):
        """Composite the gesture over the base rows it touched and hand the images to Blender."""
        if self.gpu is not None:
            now = perf_counter()
            if not self.active or (not force and now - self.last_publish < PREVIEW_INTERVAL):
                return False
            if not self.gpu.publish(stroke_settings):
                return False
            self._inputs_changed()
            self.publications += 1
            self.last_publish = perf_counter()
            self.profile['publish_ms'].append((self.last_publish - now) * 1000)
            return True
        if not self.active or self.dirty is None:
            return False
        now = perf_counter()
        if not force and now - self.last_publish < PREVIEW_INTERVAL:
            return False
        from .stroke_raster import dilate
        from .strokes import channel_value
        low, high = self.dirty
        # One texel into uncovered neighbours, as the committed raster does at UV seams.
        coverage = dilate(self.coverage[low:high], self.covered[low:high])
        alpha = (coverage * float(stroke_settings['opacity']))[..., None]
        for channel, image in self.images.items():
            rows = np.array(self.bases[channel][low:high], copy=True)
            if stroke_settings['blend'] == 'ERASE':
                rows[..., 3:4] *= 1.0 - alpha
                rows[..., :3][rows[..., 3] <= 1e-8] = 0.0   # as the committed rows: no colour under nothing
            else:
                color = np.asarray(channel_value(stroke_settings['values'], channel), np.float32)
                out_alpha = alpha + rows[..., 3:4] * (1.0 - alpha)
                premultiplied = color * alpha + rows[..., :3] * rows[..., 3:4] * (1.0 - alpha)
                rows[..., :3] = np.divide(premultiplied, out_alpha, out=np.zeros_like(premultiplied), where=out_alpha > 1e-8)
                rows[..., 3:4] = out_alpha
            if self.premultiplied[channel]:
                rows[..., :3] *= rows[..., 3:4]
            buffer = self._buffer(channel)
            buffer[low:high] = rows
            image.pixels.foreach_set(buffer.ravel())
            image.update()
            image['open_painter_pixel_revision'] = image.get('open_painter_pixel_revision', 0) + 1
        self._inputs_changed()
        self.publications += 1
        self.last_publish = perf_counter()
        self.profile['publish_ms'].append((self.last_publish - now) * 1000)
        return True

    def _buffer(self, channel):
        """The full preview array of a channel: the base with the gesture's rows composited in."""
        if channel not in self.buffers:
            base = np.array(self.bases[channel], copy=True)
            if self.premultiplied[channel]:
                base[..., :3] *= base[..., 3:4]
            self.buffers[channel] = base
        return self.buffers[channel]

    def restore(self):
        """Put the layer's own inputs back (a discarded gesture) and let the preview images go."""
        for channel, (node, source) in list(self.nodes.items()):
            try:
                if channel in self.resource_sources:
                    node.gpu_resource = self.resource_sources.get(channel)
                if channel in self.images and node.image == self.images[channel]:
                    node.image = source
            except ReferenceError:
                pass
        if self.nodes:
            self._inputs_changed()
        self._remove()

    def retire(self):
        """Hand each node back to the leaf datablock it owned before the gesture, then let the
        preview go. The committed stroke regenerates into that same leaf in place, so the node
        binds one persistent datablock across the gesture — a following undo push captures that
        leaf, never the transient preview (whose later removal would strand the node after undo;
        0.13 stability S2). A node whose owned leaf is gone keeps the preview until its
        replacement publishes, as before."""
        from ..evaluation.shader_resources import RESOURCE, alive
        if not self.nodes:
            return
        stranded = False
        for channel, (node, source) in list(self.nodes.items()):
            try:
                if channel in self.resource_sources:
                    node.gpu_resource = self.resource_sources.get(channel)
                if channel in self.resource_sources:
                    node.image = source
                    continue
                if node.image != self.images[channel]:
                    continue                      # already moved on to its published leaf
                if source is not None and alive(source) and source.get(RESOURCE):
                    node.image = source           # the persistent leaf, refreshed in place next
                else:
                    stranded = True               # nothing safe to bind: keep the preview a while
            except ReferenceError:
                continue
        self._inputs_changed()
        self.active = False
        self.retired_at = perf_counter()
        _retired.append(self)                     # reaped once nothing binds the preview images
        _watch()
        if stranded:
            return

    def _still_bound(self):
        for channel, (node, source) in self.nodes.items():
            try:
                if channel in self.images and node.image == self.images[channel]:
                    return True
            except ReferenceError:
                continue
        return False

    def _remove(self):
        if self.gpu is not None:
            for channel, (node, _) in self.nodes.items():
                if node.gpu_resource is self.gpu.outputs.get(channel):
                    node.gpu_resource = self.resource_sources.get(channel)
            self.gpu.close()
            self.gpu = None
        for image in self.images.values():
            _orphans.append((image, perf_counter()))
        self.images.clear()
        self.nodes.clear()
        self.buffers.clear()
        self.active = False
        _watch()


# ---- the gesture state machine ------------------------------------------------------------------

MAX_STEPS_PER_EVENT = 512   # re-projections between two pointer events (a fast drag across the window)
STEP_FRACTION = .25         # re-projection step as a fraction of the brush spacing
JUMP_STEPS = 8              # re-projection steps a surface may advance per step before it counts as a jump


class Gesture:
    """One pointer-down-to-release stroke: eligible hits, resampled path, live preview, commit.

    ``projector`` maps window positions to rays: ``ray(xy) -> (origin, direction)`` or None
    outside the view, and ``world_per_pixel(xy, point)`` the scene size of one pixel at a
    point's depth. The 3D viewport provides one; a replay test provides a synthetic view.
    """

    def __init__(self, scene, ts, layer, settings, scope, projector, key='', preview=True, mask_id=''):
        self.scene_name, self.ts_id, self.layer_id, self.key = scene.name, ts.id, layer.id, key
        self.mask_id = mask_id
        self.settings_group = settings
        self.brush = None       # the brush snapshot taken at start (``brush_snapshot``)
        self.scope, self.projector = scope, projector
        self.wants_preview = preview
        self.samples, self.segments, self.new = [], [[]], []
        self.anchors, self.new_anchors = [], []   # (x, y, z, polygon) surface hits, one per sample
        self.last, self.last_screen, self.last_attributes = None, None, None
        self.previous = None    # the last re-projection since the last sample (point, normal, owner)
        self.hit_sets = set()
        self.preview, self.hold = None, None
        self.prepared_source = None
        self.settings, self.geometry = None, None
        self.plane = (0.0, 0.0, 1.0)
        self.active = False
        self.result = None      # the recorded stroke after a committed end
        self.reason = ''        # why the gesture was discarded, if it was
        self.new_source = False  # the target had no strokes yet: this gesture adds a source unit
        self.profile = {'advance_ms': [], 'start_ms': 0.0, 'end_ms': 0.0}
        self.symmetry = []
        self.dabs, self.new_dabs = [], []
        self.graph_program = None
        self.distance = 0.
        self.started_at = 0.

    # ---- resolution -----------------------------------------------------------------------

    @property
    def scene(self):
        """Resolved by name: an undo between two events replaces the scene datablock."""
        import bpy
        return bpy.data.scenes.get(self.scene_name)

    def _set(self):
        scene = self.scene
        if scene is None:
            return None
        return next((item for item in scene.open_painter.texture_sets if item.id == self.ts_id), None)

    def _layer(self, ts):
        return next((item for item in ts.layers if item.id == self.layer_id), None) if ts is not None else None

    def target(self):
        """(ts, layer, mask, reason) of the captured destination as it stands now (``target_state``)."""
        return target_state(self.scene, self.ts_id, self.layer_id, self.mask_id)

    def describe(self, project=None):
        """'Body > Mud Paint | Color, Roughness' for the captured target and enabled channels."""
        from ..constants import CHANNEL_ITEMS
        ts, layer, mask, _ = self.target()
        labels = {key: label for key, label, *_ in CHANNEL_ITEMS}
        brush = self.brush if self.brush is not None else self.settings_group
        channels = [brush.channels] if isinstance(brush.channels, str) else sorted(brush.channels)
        names = ', '.join(labels.get(c, c) for c in channels if c in labels) or 'no channels'
        parts = [ts.name if ts is not None else '?']
        if layer is not None:
            parts.append(layer.name)
        if mask is not None:
            parts.append(mask.name or 'Vector Strokes mask')
        return f"{' > '.join(parts)} | {names}"

    def hit(self, xy):
        """(point, normal, direction, set id, polygon, object) of an eligible surface under
        ``xy``, or None; ``polygon`` is the evaluated face the ray hit (the sample's anchor)."""
        import bpy
        from mathutils import Vector
        ray = self.projector.ray(xy)
        if ray is None:
            return None
        origin, direction = ray
        depsgraph = bpy.context.evaluated_depsgraph_get()
        hit, point, normal, index, obj, _ = self.scene.ray_cast(depsgraph, origin, direction)
        if not hit or obj is None:
            return None
        original = obj.original
        if original.type != 'MESH' or original.hide_viewport or original.hide_get():
            return None
        try:
            mesh = obj.evaluated_get(depsgraph).data
            material_index = mesh.polygons[index].material_index if 0 <= index < len(mesh.polygons) else -1
        except (AttributeError, IndexError, ReferenceError):
            return None
        owner = self.scope.owner(original, material_index)
        if owner is None:
            return None   # not an eligible face of a participating set: the stroke lifts here
        return Vector(point), Vector(normal).normalized(), direction, owner, int(index), original.as_pointer()

    def base_radius(self, xy, point):
        settings = self.brush or self.settings_group
        if settings.radius_mode == 'WORLD':
            return settings.radius
        return max(settings.screen_radius * self.projector.world_per_pixel(xy, point), 1e-5)

    def step_pixels(self, xy):
        """Screen distance between re-projections: a quarter of the brush spacing at the
        last hit's depth, so a sample lands within a quarter spacing of where it belongs
        before ``place`` interpolates it onto the exact spacing."""
        settings = self.brush or self.settings_group
        if settings.radius_mode == 'SCREEN':
            return max(1.0, settings.spacing * settings.screen_radius * STEP_FRACTION)
        if self.last is None:
            return 4.0
        return max(1.0, settings.spacing * settings.radius * STEP_FRACTION / self.projector.world_per_pixel(xy, self.last))

    # ---- lifecycle ------------------------------------------------------------------------

    def start(self, xy, attributes, hit, plane_normal=None):
        """Begin at a hit: snapshot the brush, capture the target and geometry, hold the
        shader, open the preview. False (with ``reason``) when the target is not paintable."""
        from ..evaluation import shader_preview
        from ..utils.logging import logger
        ts, layer, mask, reason = self.target()
        if reason:
            self.reason = reason
            return False
        # First stroke on this target: its source unit does not exist in the live graph yet,
        # so committing it is a structural change the operator must build before its undo push
        # (see ``ui/vector_brush.OP_OT_vector_brush._end``; 0.13 hardening, H4).
        holder = mask if (self.key and mask is not None) else layer
        self.new_source = holder is not None and not getattr(holder, 'stroke_key', '')
        opened = perf_counter()
        sweep()
        settings = self.brush = brush_snapshot(self.settings_group)
        self.started_at = perf_counter()
        if getattr(settings, 'node_group', None) is not None:
            from .brush_graph import compile
            try:
                self.graph_program = compile(settings.node_group)
            except ValueError as error:
                self.reason = str(error)
                return False
        if any(getattr(settings, 'symmetry', ())):
            import bpy
            from .symmetry import transforms
            obj = bpy.context.view_layer.objects.active
            if obj is not None:
                try:
                    self.symmetry = list(transforms(obj.matrix_world, settings.symmetry))
                except np.linalg.LinAlgError:
                    self.reason = 'The symmetry object has a zero scale'
                    return False
        self.active = True
        self.result, self.reason = None, ''
        self.plane = tuple(plane_normal) if (plane_normal is not None and settings.projection == 'PLANAR') else (0.0, 0.0, 1.0)
        self.settings = stroke_settings(settings, self.plane if settings.projection == 'PLANAR' else None)
        if self.wants_preview and not self.key and self.new_source and _native_metal():
            # An empty paint layer otherwise has no source binding until release.
            # Prepare its empty runtime source before holding structural updates;
            # cancellation restores the recipe, without creating a stroke record.
            from .strokes import ensure_key
            from ..core.layer_stack import channel_for
            channels = [settings.channels] if isinstance(settings.channels, str) else sorted(settings.channels)
            previous = {c: channel_for(layer, c).enabled for c in channels}
            self.prepared_source = ensure_key(layer), previous
            for channel in channels:
                channel_for(layer, channel).enabled = True
            shader_preview.ensure(self.scene, ts)
        self.geometry = geometry_signature(self.scope, self.scene.open_painter)
        self.hold = shader_preview.hold(self.scene, ts)
        if self.wants_preview and not self.key:
            try:
                entries = snapshot_entries(ts)
                channels = [settings.channels] if isinstance(settings.channels, str) else sorted(settings.channels)
                direct = self.graph_program is None and not (self.symmetry and settings.projection == 'PLANAR')
                self.preview = Preview(self.scene, ts, layer, channels, entries, gpu=direct)
            except Exception:
                logger.exception('Paint Material: no live preview for this gesture')
                self.preview = None
        self.place(xy, attributes, hit)
        self.last_screen, self.last_attributes = xy, attributes
        self.flush()
        self.profile['start_ms'] = (perf_counter() - opened) * 1000
        return True

    def invalidated(self):
        """Why the captured target can no longer take this gesture ('' while it can): the
        destination went away or hid, or the assigned geometry changed under the stroke."""
        if not self.active:
            return ''
        _, _, mask, reason = self.target()
        if reason:
            return reason
        if self.key and mask is not None and mask.stroke_key != self.key:
            return 'the target mask was re-keyed during the stroke'
        if geometry_signature(self.scope, self.scene.open_painter) != self.geometry:
            return 'the geometry changed during the stroke'
        return ''

    def advance(self, xy, attributes):
        """Walk from the last pointer position to ``xy`` at the brush spacing, re-projecting
        every step; a step that misses an eligible surface breaks the segment."""
        if not self.active:
            return
        started = perf_counter()
        if self.last_screen is None:
            hit = self.hit(xy)
            if hit is None:
                self.lift()
            else:
                self.place(xy, attributes, hit)
        else:
            length = float(np.hypot(xy[0] - self.last_screen[0], xy[1] - self.last_screen[1]))
            if length > 0:
                step = max(self.step_pixels(xy), length / MAX_STEPS_PER_EVENT)
                points, values = resample(self.last_screen, xy, step, self.last_attributes, attributes)
                for point, value in zip(points, values):
                    at = (float(point[0]), float(point[1]))
                    hit = self.hit(at)
                    if hit is None:
                        self.lift()
                        continue
                    self.place(at, tuple(value), hit)
        self.last_screen, self.last_attributes = xy, attributes
        self.flush()
        self.profile['advance_ms'].append((perf_counter() - started) * 1000)

    def place(self, xy, attributes, hit):
        """A sample at ``hit`` once the surface path since the last sample reaches the
        spacing; the sample is interpolated back onto the exact spacing between the previous
        re-projection and this one, so the spacing does not depend on the step or event rate."""
        from mathutils import Vector
        settings = self.brush or self.settings_group
        point, normal, direction, owner, polygon, _ = hit
        anchor = [float(point[0]), float(point[1]), float(point[2]), float(polygon)]
        pressure, tilt_x, tilt_y, tablet = attributes
        radius = self.base_radius(xy, point)
        if tablet and settings.pressure_size:
            radius *= max(pressure, .05)
        flow_pressure = pressure if (tablet and settings.pressure_flow) else 1.0
        spacing = settings.spacing * radius
        if self.last is not None and self.previous is not None:
            # Consecutive re-projections a quarter spacing apart on screen land close
            # together on a continuous surface; a jump means the ray fell off a silhouette
            # onto another surface, and that is a lift, not a stroke across the gap.
            step_world = self.step_pixels(xy) * self.projector.world_per_pixel(xy, point)
            if (point - self.previous[0]).length > max(JUMP_STEPS * step_world, radius):
                self.lift()
        if self.last is not None:
            distance = (point - self.last).length
            if distance < spacing:
                self.previous = (point, normal, owner)
                return False
            previous = self.previous
            if previous is not None and previous[2] == owner:
                before = (previous[0] - self.last).length
                if before < spacing < distance:
                    t = (spacing - before) / max(distance - before, 1e-9)
                    point = previous[0] + (point - previous[0]) * t
                    normal = (previous[1] * (1.0 - t) + normal * t)
                    normal = normal.normalized() if normal.length > 1e-9 else hit[1]
        self.previous = (hit[0], hit[1], owner)
        tangent = (point - self.last) if self.last is not None else Vector((0, 0, 0))
        if tangent.length > 1e-9:
            tangent.normalize()
        if settings.jitter:
            rng = np.random.default_rng(settings.seed + len(self.samples))
            offset = Vector(rng.normal(0, settings.jitter * radius * .5, 3))
            offset -= normal * offset.dot(normal)
            point = point + offset
        row = [*point, *normal, flow_pressure, radius, tilt_x, tilt_y, *tangent]
        if getattr(settings, 'particles', False):
            from .particles import contacts
            emitted = contacts(point, normal, radius, settings, settings.seed + len(self.samples), self._particle_collision)
            for _, _, at, axis, set_id, face, _, velocity in emitted:
                path = velocity / max(np.linalg.norm(velocity), 1e-20)
                contact = [*at, *axis, flow_pressure, radius, tilt_x, tilt_y, *path]
                self._emit(contact, [*at, float(face)], set_id, pressure, settings)
        else:
            self._emit(row, anchor, owner, pressure, settings)
        self.last = point
        return True

    def _particle_collision(self, start, delta):
        import bpy
        from mathutils import Vector
        length = float(np.linalg.norm(delta))
        if length < 1e-10:
            return None
        depsgraph = bpy.context.evaluated_depsgraph_get()
        hit, at, axis, face, obj, _ = self.scene.ray_cast(depsgraph, Vector(start), Vector(delta / length), distance=length)
        if not hit or obj is None:
            return None
        original = obj.original
        if original.type != 'MESH' or original.hide_viewport or original.hide_get():
            return None
        mesh = obj.evaluated_get(depsgraph).data
        if not 0 <= face < len(mesh.polygons):
            return None
        owner = self.scope.owner(original, mesh.polygons[face].material_index)
        return (at, axis, owner, face, original.as_pointer()) if owner is not None else None

    def _emit(self, row, anchor, owner, pressure, settings):
        from mathutils import Vector
        point, normal = Vector(row[:3]), Vector(row[3:6])
        radius, tilt_x, tilt_y = row[7:10]
        dab = {'donor': list(self.last if self.last is not None else point)} if settings.blend == 'SMUDGE' else {}
        if self.last is not None:
            self.distance += (point - self.last).length
        if self.graph_program is not None:
            from .brush_graph import evaluate, FIELDS
            output = evaluate(self.graph_program, {'Pressure': pressure, 'Tilt': (tilt_x, tilt_y, 0),
                'Position': tuple(point), 'Normal': tuple(normal), 'Distance': self.distance,
                'Time': perf_counter() - self.started_at, 'Sample Index': len(self.samples)}, settings.seed)
            values = {}
            for name, field in FIELDS.items():
                if name not in output:
                    continue
                value = output[name]
                if name in {'Radius', 'Flow', 'Opacity'}:
                    value = float(value) * (radius if name == 'Radius' else getattr(settings, field))
                if np.ndim(value):
                    values[field] = np.clip(value, 0, 1).tolist()
                else:
                    values[field] = float(np.clip(value, .00001 if name == 'Radius' else (-1e6 if name == 'Angle' else 0),
                                                 10 if name == 'Radius' else (1e6 if name == 'Angle' else 1)))
            if 'Alpha' in output:
                values['flow'] = values.get('flow', settings.flow) * float(np.clip(output['Alpha'], 0, 1))
            row[7] = values.get('radius', radius)
            if 'Offset' in output:
                row[:3] = (np.asarray(row[:3]) + output['Offset']).tolist()
                resolved = self._symmetry_hit(row)
                if resolved is None:
                    self.last = point
                    return False
                row[:3], row[3:6] = list(resolved[0]), list(resolved[1])
                anchor = [*resolved[0], float(resolved[4])]
                owner = resolved[3]
            dab['values'] = values
        self.samples.append(row)
        self.new.append(row)
        self.anchors.append(anchor)
        self.new_anchors.append(anchor)
        self.segments[-1].append(point.copy())
        self.hit_sets.add(owner)
        self.dabs.append(dab)
        self.new_dabs.append(dab)
        if self.symmetry:
            from .symmetry import reflect
            for mirrored, matrix in reflect(row, self.symmetry, with_transform=True):
                resolved = self._symmetry_hit(mirrored)
                if resolved is None:
                    continue
                at, face_normal, _, set_id, face, _ = resolved
                mirrored[:3], mirrored[3:6] = at, face_normal
                mirror_anchor = [*at, float(face)]
                self.samples.append(mirrored.tolist())
                self.new.append(mirrored.tolist())
                self.anchors.append(mirror_anchor)
                self.new_anchors.append(mirror_anchor)
                self.hit_sets.add(set_id)
                mirror_dab = {'values': dict(dab['values'])} if 'values' in dab else {}
                if 'donor' in dab:
                    mirror_dab['donor'] = (matrix @ np.r_[dab['donor'], 1.])[:3].tolist()
                if settings.projection == 'PLANAR':
                    axis = np.linalg.inv(matrix[:3, :3]).T @ np.asarray(self.plane)
                    mirror_dab['planar'] = (axis / max(np.linalg.norm(axis), 1e-20)).tolist()
                self.dabs.append(mirror_dab)
                self.new_dabs.append(mirror_dab)
        return True

    def _symmetry_hit(self, row):
        """Confirm reflected dabs against evaluated, visible, permitted geometry."""
        import bpy
        from mathutils import Vector
        point, normal = Vector(row[:3]), Vector(row[3:6])
        reach = max(float(row[7]) * .25, 1e-5)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        hit, at, face_normal, face, obj, _ = self.scene.ray_cast(
            depsgraph, point + normal * reach, -normal, distance=reach * 2)
        if not hit or obj is None:
            return None
        original = obj.original
        if original.type != 'MESH' or original.hide_viewport or original.hide_get():
            return None
        mesh = obj.evaluated_get(depsgraph).data
        if not 0 <= face < len(mesh.polygons):
            return None
        owner = self.scope.owner(original, mesh.polygons[face].material_index)
        if owner is None or face_normal.dot(normal) <= 0:
            return None
        return at, face_normal.normalized(), -normal, owner, face, original.as_pointer()

    def lift(self):
        """The pointer left the eligible surfaces: the next hit starts a new segment."""
        if self.last is not None:
            self.segments.append([])
        self.last, self.previous = None, None

    def flush(self):
        """Show the new samples in the live preview and keep the shader held."""
        from ..evaluation import shader_preview
        from ..utils.logging import logger
        if self.hold is not None:
            shader_preview.renew_hold(self.hold)
        if self.preview is not None and self.new:
            from .stroke_raster import SAMPLE_WIDTH
            try:
                self.settings['dabs'] = self.new_dabs
                self.preview.stamp(np.asarray(self.new, np.float32).reshape(-1, SAMPLE_WIDTH), self.settings,
                                   np.asarray(self.new_anchors, np.float32).reshape(-1, 4))
            except Exception:
                logger.exception('Paint Material: the live preview failed; the stroke commits on release')
                self.preview.restore()
                self.preview = None
        self.new, self.new_anchors = [], []
        self.new_dabs = []

    def end(self, commit, reason=''):
        """Close the gesture: one stroke record (``self.result``) or nothing."""
        from ..evaluation import shader_preview
        if not self.active:
            return None
        opened = perf_counter()
        stroke = None
        if commit and self.samples:
            invalid = self.invalidated()
            if invalid:
                commit, reason = False, invalid
        self.active = False
        if commit and self.samples:
            stroke = self.commit()
        if self.preview is not None:
            if stroke is not None and RETIRE_PREVIEWS:
                self.preview.publish(self.settings, force=True)
                self.preview.retire()
            else:
                self.preview.restore()
            self.preview = None
        if self.prepared_source is not None:
            if stroke is None:
                ts = self._set()
                layer = self._layer(ts) if ts is not None else None
                if layer is not None and layer.stroke_key == self.prepared_source[0]:
                    from ..core.layer_stack import channel_for
                    layer.stroke_key = ''
                    for channel, enabled in self.prepared_source[1].items():
                        channel_for(layer, channel).enabled = enabled
            self.prepared_source = None
        if self.hold is not None:
            shader_preview.release_hold(self.hold)
            self.hold = None
        self.result, self.reason = stroke, ('' if stroke is not None else (reason or 'nothing painted'))
        self.samples, self.segments, self.new = [], [[]], []
        self.anchors, self.new_anchors = [], []
        self.last, self.last_screen, self.previous = None, None, None
        self.profile['end_ms'] = (perf_counter() - opened) * 1000
        return stroke

    def commit(self):
        from . import strokes as records
        from .stroke_raster import SAMPLE_WIDTH
        ts = self._set()
        layer = self._layer(ts)
        if ts is None or layer is None:
            return None
        samples = np.asarray(self.samples, np.float32).reshape(-1, SAMPLE_WIDTH)
        anchors = np.asarray(self.anchors, np.float32).reshape(-1, 4)
        settings = self.brush or brush_snapshot(self.settings_group)
        result = records.record(self.scene.open_painter, ts, layer, samples, settings, plane_normal=self.plane,
                              universal=settings.universal, key=self.key, sets=set(self.hit_sets), anchors=anchors,
                              dabs=self.dabs if any(self.dabs) else None)
        if self.graph_program is not None:
            import json
            result.brush_graph_data = json.dumps(self.graph_program, allow_nan=False)
        if self.preview is not None and self.preview.gpu is not None:
            try:
                self.preview.publish(self.settings, force=True)
                from .gpu_sources import adopt
                adopt(self.preview, result)
            except Exception:
                from ..utils.logging import logger
                logger.exception('GPU gesture adoption failed; replaying the saved stroke')
                self.preview.restore()
                self.preview = None
        return result


# Preview images are made for one gesture and removed a while after nothing binds them: a
# preview re-bound in a later gesture crashed the undo of a step that captured it, and one
# removed right after its node moved on crashed the draw engine still holding it.
_retired = []       # previews bound until their committed leaf publishes
_orphans = []       # (image, unbound at) preview images waiting for removal
ORPHAN_GRACE = 2.0  # seconds an unbound preview image stays before removal


def _new_preview_image(size):
    import bpy
    image = bpy.data.images.new('OP Gesture Preview', width=size, height=size, alpha=True, float_buffer=True)
    image['open_painter_owner'] = True
    image['op_gesture_preview'] = True
    return image


def _watch():
    import bpy
    if (_retired or _orphans) and not bpy.app.timers.is_registered(_reap):
        bpy.app.timers.register(_reap, first_interval=.1, persistent=True)


def _reap():
    """Drop retired previews whose nodes moved on to a published leaf (after ``RETIRE_TIMEOUT``
    a preview still bound is restored to the layer's own input), then remove unbound preview
    images that have been idle for ``ORPHAN_GRACE``."""
    import bpy
    from ..evaluation.shader_resources import alive, bound
    sweep()
    keep = []
    for preview in _retired:
        if preview._still_bound():
            if perf_counter() - preview.retired_at < RETIRE_TIMEOUT:
                keep.append(preview)
                continue
            preview.restore()
        else:
            preview._remove()
    _retired[:] = keep
    waiting = []
    for image, since in _orphans:
        if not alive(image):
            continue
        if bound(image) or perf_counter() - since < ORPHAN_GRACE:
            waiting.append((image, perf_counter() if bound(image) else since))
            continue
        # Released, not removed: the material that sampled the preview may still be compiling
        # its replacement and sampling the old one (see shader_resources.release_image).
        from ..evaluation.shader_resources import release_image
        release_image(image)
    _orphans[:] = waiting
    from ..evaluation.shader_resources import sweep_graveyard
    sweep_graveyard()
    return .25 if (_retired or _orphans) else None


def sweep():
    """Adopt preview images nothing tracks any more (left behind by an undo that restored a
    step captured while a preview was bound) so the reaper removes them once unbound."""
    import bpy
    from ..evaluation.shader_resources import alive, bound
    known = {image.as_pointer() for image, _ in _orphans if alive(image)}
    known |= {image.as_pointer() for preview in _retired for image in preview.images.values() if alive(image)}
    found = False
    for image in bpy.data.images:
        if image.get('op_gesture_preview') and image.as_pointer() not in known and not bound(image):
            _orphans.append((image, perf_counter()))
            found = True
    if found:
        _watch()


def discard_all(unbind=True):
    """Forget the retired previews: unbound first when the caller may touch the material (a
    save), left as they are when an undo or a file load is about to replace the data."""
    for preview in _retired:
        if unbind:
            preview.restore()
        else:
            preview._remove()
    _retired.clear()


def release_all():
    """Remove every preview image now (unregister)."""
    import bpy
    discard_all()
    from ..evaluation.shader_resources import release_image
    for image, _ in _orphans:
        try:
            if not image.users:
                release_image(image)
        except (ReferenceError, RuntimeError):
            pass
    _orphans.clear()
    if bpy.app.timers.is_registered(_reap):
        bpy.app.timers.unregister(_reap)


def stroke_settings(settings, plane_normal=None):
    """The kernel and compositing parameters of a gesture, read once at its start."""
    from .stroke_raster import alpha_pixels
    return {'hardness': float(settings.hardness), 'flow': float(settings.flow), 'opacity': float(settings.opacity),
            'accumulate': settings.accumulate, 'blend': settings.blend, 'planar': plane_normal,
            'alpha_texture': alpha_pixels(settings.alpha_image), 'alpha_rotation': 'RAKE', 'alpha_angle': 0.0,
            'depth_limit': float(getattr(settings, 'depth_limit', 1.0)), 'reach': getattr(settings, 'reach', 'DEPTH'),
            'values': settings}
