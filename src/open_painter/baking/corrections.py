"""Paintable bake fixes (0.9.1): per-region cage distance, skew, direction, source and AO exclusion.

Each correction is a managed image at the texture-set resolution, painted with
Blender's native brush through a session that targets it instead of a layer.
At bake time the images become geometry and passes:

- Projection Distance, Skew Correction and Projection Direction are sampled at
  every vertex of the render-evaluated target and turned into one owned cage
  mesh per part (``corrected_cage``): the vertex moves along a direction that
  blends its smooth normal toward the dominant face normal (skew) and toward a
  chosen vector (direction), by the extrusion plus the painted signed distance.
  Cycles then bakes with that cage, so the fix has exactly the semantics of a
  hand-edited cage, painted instead of modelled.
- Ignore High Poly and AO Exclusion are second passes — without the sources
  marked ignorable, or with the part's own occluders only — composited into
  the first pass by the painted weight (``composite``).

``rebake_region`` rebakes only the faces a stroke touched: every other face is
routed to the small scratch image, the result is composited into the existing
map, and the maps stay registered under the same images.
"""
from time import monotonic

import bpy
import numpy as np

from ..constants import OWNER
from ..core.image_manager import create, pixels, write
from ..properties.bake import CORRECTION_KINDS, NEUTRAL

TAG = 'open_painter_bake_correction'
USED = 'open_painter_bake_correction_used'
OVERLAY = 'open_painter_correction_overlay'
FIELDS = {'OFFSET': 'offset_image', 'SKEW': 'skew_image', 'DIRECTION': 'direction_image',
          'IGNORE': 'ignore_image', 'AO_EXCLUDE': 'ao_exclude_image'}
LABELS = {key: label for key, label, _ in CORRECTION_KINDS}
_baked = {}       # (scene pointer, set id) -> {kind: {correction key: raster}} used by the last bake
_pending = {}     # scene pointer -> time the last correction stroke ended (live rebake settle)
_snapshots = {}   # (scene pointer, image pointer) -> raster at session start / last stroke
SETTLE = .25


# ---- images ------------------------------------------------------------------

def image_of(ts, key, create_missing=False):
    corrections = ts.bake.corrections
    image = getattr(corrections, FIELDS[key])
    n = int(ts.resolution)
    if image is not None and (tuple(image.size) != (n, n) or not image.has_data):
        image = None
    if image is None and create_missing:
        neutral = NEUTRAL[key]
        image = create(f'{ts.name} Bake {LABELS[key]}', n, (neutral, neutral, neutral, 1.))
        image[TAG] = key
        image['texture_set_id'] = ts.id
        image[USED] = False
        setattr(corrections, FIELDS[key], image)
    return image


def raster(ts, key):
    """(n, n) float32 values of a correction (its red channel), or None when unpainted."""
    image = image_of(ts, key)
    if image is None or not image.get(USED):
        return None
    return pixels(image)[..., 0].copy()


def refresh_used(image):
    """Flag whether the image deviates from neutral anywhere; returns the flag."""
    key = image.get(TAG)
    if key is None:
        return False
    values = pixels(image)[..., 0]
    used = bool(np.abs(values - NEUTRAL[key]).max() > 1e-6)
    if bool(image.get(USED)) != used:
        image[USED] = used
    return used


def active_keys(ts):
    return [key for key in FIELDS if raster(ts, key) is not None]


def clear(ts, key=None):
    corrections = ts.bake.corrections
    for item in (FIELDS if key is None else [key]):
        image = getattr(corrections, FIELDS[item])
        setattr(corrections, FIELDS[item], None)
        if image is not None and image.get(TAG):
            bpy.data.images.remove(image)
    _baked.pop((ts.id_data.as_pointer(), ts.id), None)


# ---- sampling ----------------------------------------------------------------

def _sample(values, uv):
    """Nearest-texel lookup of (n, n) values at (k, 2) UVs, wrapping like the bake's UV space."""
    n = values.shape[0]
    cols = np.mod(np.floor(uv[:, 0] * n).astype(np.int64), n)
    rows = np.mod(np.floor(uv[:, 1] * n).astype(np.int64), n)
    return values[rows, cols]


def vertex_weights(mesh, rasters):
    """Per-vertex averages of each correction over the vertex's UV corners.

    Each corner is sampled two texels inside its face (toward the face's UV
    centroid) so island borders and margins do not dilute the stroke. A vertex
    on a UV seam still averages its islands: a cage has one position per
    vertex, exactly as a hand-made cage does.
    Returns {key: (V,) float32}; keys absent from ``rasters`` are absent here.
    """
    uv_layer = next((uv for uv in mesh.uv_layers if uv.active_render), mesh.uv_layers.active)
    if uv_layer is None:
        raise ValueError('The bake target has no UV map for corrections')
    loops = len(mesh.loops)
    uv = np.empty((loops, 2), np.float32)
    uv_layer.data.foreach_get('uv', uv.ravel())
    vertex = np.empty(loops, np.int32)
    mesh.loops.foreach_get('vertex_index', vertex)
    if len(mesh.polygons) and rasters:
        n = next(iter(rasters.values())).shape[0]
        starts = np.empty(len(mesh.polygons), np.int32)
        totals = np.empty(len(mesh.polygons), np.int32)
        mesh.polygons.foreach_get('loop_start', starts)
        mesh.polygons.foreach_get('loop_total', totals)
        owner = np.repeat(np.arange(len(mesh.polygons)), totals)
        centroid = np.zeros((len(mesh.polygons), 2), np.float64)
        np.add.at(centroid, owner, uv)
        centroid /= np.maximum(totals, 1)[:, None]
        toward = centroid[owner] - uv
        distance = np.linalg.norm(toward, axis=1, keepdims=True)
        step = np.minimum(distance, 2.0 / n)
        uv = (uv + np.where(distance > 1e-12, toward / np.maximum(distance, 1e-12) * step, 0)).astype(np.float32)
    count = np.bincount(vertex, minlength=len(mesh.vertices)).astype(np.float32)
    result = {}
    for key, values in rasters.items():
        sampled = _sample(values, uv).astype(np.float64)
        total = np.bincount(vertex, weights=sampled, minlength=len(mesh.vertices))
        result[key] = np.where(count > 0, total / np.maximum(count, 1), NEUTRAL[key]).astype(np.float32)
    return result


def dominant_face_normals(mesh):
    """Per vertex, the normal of its largest adjacent face (world of the mesh's own space)."""
    normals = np.zeros((len(mesh.vertices), 3), np.float32)
    best = np.full(len(mesh.vertices), -1.0, np.float32)
    face_normals = np.empty((len(mesh.polygons), 3), np.float32)
    mesh.polygons.foreach_get('normal', face_normals.ravel())
    areas = np.empty(len(mesh.polygons), np.float32)
    mesh.polygons.foreach_get('area', areas)
    for polygon, area, normal in zip(mesh.polygons, areas, face_normals):
        for v in polygon.vertices:
            if area > best[v]:
                best[v] = area
                normals[v] = normal
    return normals


def cage_positions(mesh, matrix_world, rasters, extrusion, direction_vector, base_cage=None):
    """World-space cage vertices for a render-evaluated target under the painted corrections.

    ``base_cage`` (V, 3) world positions of an artist cage keep its shape; the
    corrections then apply on top of it.
    """
    count = len(mesh.vertices)
    local = np.empty((count, 3), np.float32)
    mesh.vertices.foreach_get('co', local.ravel())
    world = np.asarray(matrix_world, np.float64)
    positions = local @ world[:3, :3].T + world[:3, 3]
    normal_matrix = np.asarray(matrix_world.to_3x3().inverted_safe().transposed(), np.float64)
    normals = np.empty((count, 3), np.float32)
    mesh.vertices.foreach_get('normal', normals.ravel())
    smooth = normals @ normal_matrix.T
    smooth /= np.maximum(np.linalg.norm(smooth, axis=1, keepdims=True), 1e-12)
    if base_cage is not None:
        segment = np.asarray(base_cage, np.float64) - positions
        lengths = np.linalg.norm(segment, axis=1)
        direction = np.where(lengths[:, None] > 1e-12, segment / np.maximum(lengths[:, None], 1e-12), smooth)
    else:
        direction = smooth.copy()
        lengths = np.full(count, float(extrusion))
    weights = vertex_weights(mesh, rasters)
    if 'SKEW' in weights:
        faces = dominant_face_normals(mesh) @ normal_matrix.T
        faces /= np.maximum(np.linalg.norm(faces, axis=1, keepdims=True), 1e-12)
        w = weights['SKEW'][:, None]
        direction = direction * (1 - w) + faces * w
        direction /= np.maximum(np.linalg.norm(direction, axis=1, keepdims=True), 1e-12)
    if 'DIRECTION' in weights:
        target = np.asarray(direction_vector, np.float64)
        target /= max(np.linalg.norm(target), 1e-12)
        w = weights['DIRECTION'][:, None]
        direction = direction * (1 - w) + target[None, :] * w
        direction /= np.maximum(np.linalg.norm(direction, axis=1, keepdims=True), 1e-12)
    if 'OFFSET' in weights:
        lengths = lengths + (weights['OFFSET'] - NEUTRAL['OFFSET']) * 2
    return positions + direction * np.maximum(lengths, 0.0)[:, None]


def corrected_cage(ts, target, rasters, base_cage_object=None):
    """An owned cage object for a staged target copy, or None when no cage correction is painted.

    ``rasters`` holds the OFFSET/SKEW/DIRECTION rasters in world-unit form
    (OFFSET already scaled by the distance range).
    """
    geometric = {key: values for key, values in rasters.items() if key in {'OFFSET', 'SKEW', 'DIRECTION'}}
    if not geometric:
        return None
    settings = ts.bake
    extrusion = 0.0
    if settings.cage_mode == 'AUTO':
        extrusion = settings.cage_extrusion if settings.separate_cage_distance else settings.ray_distance
    base = None
    if base_cage_object is not None:
        cage_mesh = base_cage_object.data
        base_local = np.empty((len(cage_mesh.vertices), 3), np.float32)
        cage_mesh.vertices.foreach_get('co', base_local.ravel())
        cage_world = np.asarray(base_cage_object.matrix_world, np.float64)
        base = base_local @ cage_world[:3, :3].T + cage_world[:3, 3]
    positions = cage_positions(target.data, target.matrix_world, geometric, extrusion,
                               tuple(settings.corrections.direction_vector), base)
    mesh = target.data.copy()
    mesh[OWNER] = True
    mesh.materials.clear()
    mesh.vertices.foreach_set('co', positions.astype(np.float32).ravel())
    mesh.update()
    cage = bpy.data.objects.new(f'{target.name} Corrected Cage', mesh)
    cage[OWNER] = True
    cage.matrix_world.identity()
    cage.hide_render = True
    return cage


def world_rasters(ts, kinds=('OFFSET', 'SKEW', 'DIRECTION')):
    """Painted rasters converted to bake units: OFFSET in world distance around 0.5."""
    result = {}
    for key in kinds:
        values = raster(ts, key)
        if values is None:
            continue
        if key == 'OFFSET':
            values = NEUTRAL['OFFSET'] + (values - NEUTRAL['OFFSET']) * ts.bake.corrections.offset_range
        result[key] = values
    return result


# ---- compositing -------------------------------------------------------------

def resample(values, size):
    """Nearest resampling of an (n, n) raster to (size, size) (supersampled bakes)."""
    n = values.shape[0]
    if n == size:
        return values
    index = (np.arange(size) * n // size)
    return values[index][:, index]


def composite(base, alternative, weight):
    """lerp(base, alternative, weight) per texel, alpha included."""
    w = np.clip(weight, 0, 1)[..., None]
    return (base * (1 - w) + alternative * w).astype(np.float32)


# ---- painting session --------------------------------------------------------

def begin_brush(context, ts, key):
    """Start a native paint session on a correction map (creating it when missing)."""
    from ..painting import paint_session
    from ..painting.brush_bridge import ensure_brush, set_value
    project = context.scene.open_painter
    lows = [ref.object for ref in ts.assignments if ref.object]
    if not lows:
        raise ValueError('Assign a UV-unwrapped mesh to this texture set first')
    obj = context.object if context.object in lows else lows[0]
    if not obj.data.uv_layers:
        raise ValueError(f'No UV map found on {obj.name}')
    if obj.name not in context.view_layer.objects or not obj.visible_get():
        raise ValueError(f'Show {obj.name} in the active view layer before painting')
    refs = [r for r in ts.assignments if r.object == obj]
    if project.paint_image is not None and not project.paint_correction:
        paint_session.finish(context, evaluate=True)
    elif project.paint_image is not None:
        paint_session.finish(context, evaluate=False)
    image = image_of(ts, key, create_missing=True)
    if context.object and context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    context.view_layer.objects.active = obj
    obj.select_set(True)
    obj.active_material_index = refs[0].slot_index
    paint = context.scene.tool_settings.image_paint
    paint.mode = 'IMAGE'
    paint.canvas = image
    try:
        bpy.ops.object.mode_set(mode='TEXTURE_PAINT')
        ensure_brush(context)
        paint_session.restrict_faces(project, obj, ts)
    except Exception:
        paint_session.finish(context, evaluate=False)
        raise
    project.paint_image = image
    project.paint_set_id = ts.id
    project.paint_layer_id = ''
    project.paint_target_channel = ''
    project.paint_mask = False
    project.paint_mask_id = ''
    project.paint_correction = key
    ts.bake.corrections.active = key
    brush = paint.brush
    if brush is not None:
        brush.blend = 'MIX'
    if key == 'OFFSET':
        set_value(project, .5 + .25)   # a quarter of the range outward, a sensible first stroke
    else:
        set_value(project, 1.0)
    _snapshots[(context.scene.as_pointer(), image.as_pointer())] = pixels(image)
    paint_session.update_editors(context, image)
    update_overlay(context, ts)
    return image


def stroke_finished(scene, watch=True):
    """Called by the scheduler when a stroke ends; notices correction edits.

    ``watch`` opens the short idle re-check window (``poll``); the polls
    themselves pass False so the window closes on its own.
    """
    project = scene.open_painter
    key = project.paint_correction
    image = project.paint_image
    if not key or image is None:
        return False
    snapshot_key = (scene.as_pointer(), image.as_pointer())
    if watch:
        _watch[scene.as_pointer()] = monotonic()
    current = pixels(image)
    previous = _snapshots.get(snapshot_key)
    if previous is not None and previous.shape == current.shape and np.array_equal(previous, current):
        return False
    _snapshots[snapshot_key] = current
    image['open_painter_pixel_revision'] = image.get('open_painter_pixel_revision', 0) + 1
    refresh_used(image)
    ts = next((item for item in project.texture_sets if item.id == project.paint_set_id), None)
    if ts is None:
        return False
    update_overlay(bpy.context, ts)
    if ts.bake.corrections.live_rebake:
        _pending[scene.as_pointer()] = monotonic()
    return True


_polls = {}   # scene pointer -> last idle comparison
_watch = {}   # scene pointer -> when a stroke last ended (idle re-checks stop WATCH seconds later)
POLL = .4
WATCH = 2.0


def poll(scene):
    """Idle re-check of the correction canvas shortly after a stroke: brush pixels can land
    after the release tick. Reading a 4k canvas every tick forever would not be idle."""
    key = scene.as_pointer()
    now = monotonic()
    if now - _watch.get(key, -1e9) > WATCH or now - _polls.get(key, 0.0) < POLL:
        return False
    _polls[key] = now
    return stroke_finished(scene, watch=False)


def pending_rebake(scene):
    """The texture set whose painted region should be rebaked now, or None."""
    when = _pending.get(scene.as_pointer())
    if when is None or monotonic() - when < SETTLE:
        return None
    project = scene.open_painter
    ts = next((item for item in project.texture_sets if item.id == project.paint_set_id), None)
    if ts is None or not project.paint_correction:
        _pending.pop(scene.as_pointer(), None)
        return None
    return ts


def consume_pending(scene):
    _pending.pop(scene.as_pointer(), None)


def end_brush(context):
    from ..painting import paint_session
    project = context.scene.open_painter
    if project.paint_correction:
        paint_session.finish(context, evaluate=False)
    remove_overlay(context.scene)


# ---- overlay -----------------------------------------------------------------

def update_overlay(context, ts):
    """Rebuild the corrected-cage wire overlay for the assigned meshes (viewport feedback)."""
    scene = ts.id_data
    remove_overlay(scene, ts)
    if not ts.bake.corrections.show_cage:
        return None
    rasters = world_rasters(ts)
    if not rasters:
        return None
    from .pairing import groups
    try:
        parts = groups(ts, validate_cages=False)
    except ValueError:
        return None
    with context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
        depsgraph = context.evaluated_depsgraph_get()
    created = []
    for part in parts:
        evaluated = part.low.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
        try:
            if not len(mesh.vertices) or not mesh.uv_layers:
                continue
            base = None
            if ts.bake.cage_mode == 'CUSTOM' and part.cage is not None and len(part.cage.data.vertices) == len(mesh.vertices):
                cage_local = np.empty((len(mesh.vertices), 3), np.float32)
                part.cage.data.vertices.foreach_get('co', cage_local.ravel())
                cage_world = np.asarray(part.cage.matrix_world, np.float64)
                base = cage_local @ cage_world[:3, :3].T + cage_world[:3, 3]
            extrusion = 0.0
            if ts.bake.cage_mode == 'AUTO':
                extrusion = ts.bake.cage_extrusion if ts.bake.separate_cage_distance else ts.bake.ray_distance
            positions = cage_positions(mesh, evaluated.matrix_world, rasters, extrusion,
                                       tuple(ts.bake.corrections.direction_vector), base)
            overlay_mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=False, depsgraph=depsgraph)
            overlay_mesh[OWNER] = True
            overlay_mesh.materials.clear()
            overlay_mesh.vertices.foreach_set('co', positions.astype(np.float32).ravel())
            overlay_mesh.update()
            overlay = bpy.data.objects.new(f'{part.low.name} Correction Cage', overlay_mesh)
            overlay[OWNER] = True
            overlay[OVERLAY] = ts.id
            overlay.matrix_world.identity()
            overlay.display_type = 'WIRE'
            overlay.show_in_front = True
            overlay.hide_render = True
            overlay.hide_select = True
            scene.collection.objects.link(overlay)
            created.append(overlay)
        finally:
            evaluated.to_mesh_clear()
    return created


def remove_overlay(scene, ts=None):
    for obj in list(scene.objects):
        if obj.get(OVERLAY) and (ts is None or obj.get(OVERLAY) == ts.id):
            mesh = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh is not None and mesh.users == 0:
                bpy.data.meshes.remove(mesh)


# ---- region rebake -----------------------------------------------------------

def face_map(ts):
    """Per assigned object: (face id raster at the set resolution, evaluated→base face validity)."""
    from ..geometry.snapshots import targets
    from ..geometry.uv_rasterizer import rasterize
    n = int(ts.resolution)
    result = {}
    with targets(ts) as meshes:
        for target in meshes:
            uv = target['uv'][target['tri_loops']]
            rows, cols, tris, _ = rasterize(uv, n)
            ids = np.full((n, n), -1, np.int64)
            if len(rows):
                ids[rows, cols] = target['tri_polygons'][tris]
            base_ok = len(target['mesh'].polygons) == len(target['object'].data.polygons)
            result[target['object']] = (ids, base_ok)
    return result


def dilate(mask, radius):
    """Binary dilation by ``radius`` texels (separable square kernel; never across the image border)."""
    if radius <= 0:
        return mask
    from ..texture_engine.filters import shift
    out = mask.copy()
    for axis in (0, 1):
        acc = out.copy()
        for step in range(1, radius + 1):
            acc |= shift(out, step if axis == 0 else 0, step if axis == 1 else 0, False)
            acc |= shift(out, -step if axis == 0 else 0, -step if axis == 1 else 0, False)
        out = acc
    return out


def changed_texels(ts, kind):
    """Texels whose corrections differ from what the last bake of ``kind`` used, or None (no record)."""
    record = _baked.get((ts.id_data.as_pointer(), ts.id), {}).get(kind)
    if record is None:
        return None
    n = int(ts.resolution)
    dirty = np.zeros((n, n), bool)
    current = {key: raster(ts, key) for key in FIELDS}
    for key in FIELDS:
        before, now = record.get(key), current[key]
        if before is None and now is None:
            continue
        if before is None or now is None or before.shape != now.shape:
            return np.ones((n, n), bool)
        dirty |= np.abs(before - now) > 1e-6
    return dirty


def remember(ts, kind):
    """Record the correction rasters a bake of ``kind`` used."""
    _baked.setdefault((ts.id_data.as_pointer(), ts.id), {})[kind] = {key: raster(ts, key) for key in FIELDS}


def rebake_region(context, ts, kinds=None):
    """Rebake only the faces whose corrections changed since the last bake, into the existing maps.

    Returns {kind: faces rebaked}; a kind without a previous bake record is
    baked fully instead.
    """
    from .cycles_baker import bake, bake_faces
    from .bake_manager import register_map
    from ..core.layer_stack import invalidate
    settings = ts.bake
    kinds = [k for k in (kinds or ('NORMAL', 'AO')) if getattr(settings, k.lower())]
    result = {}
    maps = face_map(ts)
    for kind in kinds:
        prop = next((m for m in ts.baked_maps if m.kind == kind and m.image), None)
        dirty = changed_texels(ts, kind)
        if prop is None or dirty is None:
            image = bake(context, ts, kind)
            register_map(ts, kind, image)
            result[kind] = -1
            continue
        if not dirty.any():
            result[kind] = 0
            continue
        # Faces touching a changed texel, widened by the margin so the halo is rebaked too.
        grown = dilate(dirty, max(2, settings.margin))
        regions = {}
        for obj, (ids, base_ok) in maps.items():
            faces = np.unique(ids[grown & (ids >= 0)])
            if len(faces):
                regions[obj] = set(int(f) for f in faces)
        if not regions:
            result[kind] = 0
            continue
        fresh = bake_faces(context, ts, kind, regions)
        try:
            n = int(ts.resolution)
            current = pixels(prop.image)
            if current.shape[0] != n or fresh.size[0] != n:
                write(prop.image, pixels(fresh))
            else:
                cover = np.zeros((n, n), bool)
                for obj, faces in regions.items():
                    ids = maps[obj][0]
                    cover |= np.isin(ids, list(faces))
                cover = dilate(cover, settings.margin)
                updated = pixels(fresh)
                current[cover] = updated[cover]
                write(prop.image, current)
        finally:
            bpy.data.images.remove(fresh)
        result[kind] = sum(len(f) for f in regions.values())
        remember(ts, kind)
        from ..geometry.provenance import record
        prop.provenance = record(ts, kind)
    invalidate(ts)
    from ..evaluation.shader_preview import notify
    notify(ts)
    return result
