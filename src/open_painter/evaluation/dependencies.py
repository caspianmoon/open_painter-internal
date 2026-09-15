"""Geometry lifetimes independent of material edits; handlers only mark candidates.

Hash base authoring data only after Blender reports a geometry update, never
on each tile/timer tick. Projection acquires evaluated geometry in an owned
scope; no evaluated mesh is retained. Modifier RNA also contributes to keys.
"""
import hashlib
import bpy
import numpy as np
from bpy.app.handlers import persistent
from .resource_keys import key_for
from .metrics import measured

_mesh_epochs = {}
_epoch = 0
_mesh_fingerprints = {}
_known = {}
_candidates = set()


def live_fields(ts):
    """Masks whose scene field is analysed live: LIVE scene masks and live-edge generators."""
    return [m for l in ts.layers for m in l.masks
            if (m.source == 'SCENE' and m.influence.mode == 'LIVE')
            or (m.source == 'GENERATOR' and getattr(m, 'edge_source', 'BAKED') == 'LIVE')]


def uses_geometry(ts):
    return any(p.projection == 'TRIPLANAR' for l in ts.layers for p in (*l.channels, *l.masks)) \
        or bool(live_fields(ts)) or bool(ts.bake.auto_edge_rebake)


@measured('geometry_signature')
def mesh_signature(mesh):
    pointer = mesh.as_pointer()
    epoch = _mesh_epochs.get(pointer, 0)
    cached = _mesh_fingerprints.get(pointer)
    if cached and cached[0] == epoch:
        return cached[1]
    h = hashlib.sha256()
    for collection, prop, width, dtype in (
        (mesh.vertices, 'co', 3, np.float32), (mesh.vertices, 'normal', 3, np.float32),
        (mesh.loops, 'vertex_index', 1, np.int32),
        (mesh.polygons, 'loop_start', 1, np.int32), (mesh.polygons, 'loop_total', 1, np.int32),
        (mesh.polygons, 'material_index', 1, np.int32), (mesh.polygons, 'use_smooth', 1, np.bool_),
        (mesh.corner_normals, 'vector', 3, np.float32),
    ):
        values = np.empty(len(collection)*width, dtype=dtype)
        collection.foreach_get(prop, values)
        h.update(str((prop, len(collection))).encode()); h.update(values.tobytes())
    for uv in mesh.uv_layers:
        h.update(str((uv.name, uv.active_render, uv == mesh.uv_layers.active)).encode())
        values = np.empty(len(uv.data)*2, np.float32)
        uv.data.foreach_get('uv', values); h.update(values.tobytes())
    signature = h.hexdigest()
    _mesh_fingerprints[pointer] = epoch, signature
    return signature


def modifier_signature(obj):
    # Core RNA values capture ordinary modifier edits without retaining modifiers.
    result = []
    for modifier in obj.modifiers:
        values = []
        for prop in modifier.bl_rna.properties:
            if prop.identifier == 'rna_type' or prop.is_readonly:
                continue
            value = getattr(modifier, prop.identifier, None)
            if prop.type in {'BOOLEAN','INT','FLOAT','STRING','ENUM'}:
                values.append((prop.identifier, tuple(value) if getattr(prop, "is_array", False) else value))
            elif prop.type == 'POINTER':
                values.append((prop.identifier, value.as_pointer() if value else None))
        result.append((modifier.type, tuple(values)))
    return tuple(result)


def geometry_key(scene, ts):
    if not uses_geometry(ts):
        return None
    objects = []
    for ref in ts.assignments:
        obj = ref.object
        if obj is None or obj.type != 'MESH':
            objects.append((None, ref.slot_index))
            continue
        mesh = obj.data
        objects.append((obj.as_pointer(), mesh.as_pointer(), ref.slot_index,
                        tuple(v for row in obj.matrix_world for v in row),
                        mesh_signature(mesh), modifier_signature(obj)))
    from ..geometry.influencers import signature
    fields = tuple(signature(ts, m.influence) for m in live_fields(ts))
    return (int(ts.resolution), scene.frame_current, scene.frame_subframe, tuple(objects), fields)


def remember(scene, ts, signature):
    _known[key_for(scene, ts)] = signature


@persistent
def epoch():
    """Counts geometry/transform depsgraph updates; a cheap cache token for mesh-derived state."""
    return _epoch


def on_depsgraph(scene, depsgraph):
    global _epoch
    relevant = False
    for update in depsgraph.updates:
        item = update.id.original
        if isinstance(item, bpy.types.Mesh) and update.is_updated_geometry:
            pointer = item.as_pointer()
            _mesh_epochs[pointer] = _mesh_epochs.get(pointer, 0)+1
            relevant = True
        elif isinstance(item, bpy.types.Object) and (update.is_updated_geometry or update.is_updated_transform):
            relevant = True
    if relevant:
        _epoch += 1
        _candidates.add(scene.as_pointer())


@persistent
def on_frame(scene, *args):
    _candidates.add(scene.as_pointer())


def refresh(scene):
    if scene.as_pointer() not in _candidates:
        return
    _candidates.discard(scene.as_pointer())
    from ..core.layer_stack import invalidate
    from . import field_jobs
    for ts in scene.open_painter.texture_sets:
        key = key_for(scene, ts)
        if key in _known and uses_geometry(ts):
            current = geometry_key(scene, ts)
            previous = _known[key]
            if current == previous:
                continue
            _known[key] = current
            if previous is None or current is None:
                invalidate(ts)
                continue
            targets_changed = current[:4] != previous[:4]
            if targets_changed and field_jobs.active() and ts.bake.auto_edge_rebake and _mesh_edited(previous[3], current[3]):
                from ..baking.bake_manager import owned_curvature
                if owned_curvature(ts) is not None:
                    field_jobs.schedule_rebake(scene, ts)
            if not field_jobs.active():
                invalidate(ts)   # synchronous analysis re-runs inside the next evaluation
                continue
            if targets_changed and (any(p.projection == 'TRIPLANAR' for l in ts.layers for p in (*l.channels, *l.masks))
                                    or not live_fields(ts)):
                invalidate(ts)
            # Live fields never block: request their analyses; publication invalidates.
            from ..generators.scene_fields import signature as field_signature
            for mask in live_fields(ts):
                desired = repr(field_signature(ts, mask.influence))
                if desired != mask.influence.provenance:
                    field_jobs.request(scene, ts, mask, desired)
                else:
                    field_jobs.discard(scene, ts, mask)   # moved back: the published raster is current


def _mesh_edited(previous, current):
    """True when an assigned mesh's own geometry changed (not only its transform or modifiers)."""
    before = {entry[:2]: entry[4] for entry in previous if entry[0] is not None and len(entry) > 4}
    after = {entry[:2]: entry[4] for entry in current if entry[0] is not None and len(entry) > 4}
    return any(before.get(key) != value for key, value in after.items() if key in before)


def map_dependencies(mask):
    if mask.source == 'BAKED':
        return {mask.map_type}
    if mask.source == 'NOISE':
        return set()
    if mask.source != 'GENERATOR':
        return set()
    from ..generators.base import REQUIRED_MAPS
    maps = REQUIRED_MAPS.get(mask.generator)
    if maps is None:
        return None  # Unknown/custom generators conservatively depend on all maps.
    if mask.generator in {'EDGE_WEAR', 'CHIPS'}:
        if getattr(mask, 'edge_source', 'BAKED') == 'LIVE':
            return (set(maps) | {'AO'}) - {'CURVATURE'}   # edges come from the live field
        return set(maps) | {'AO', 'CURVATURE_THIN', 'CURVATURE_WIDE', 'CURVATURE_XWIDE'}
    return set(maps)


def image_written(image):
    """Managed writes dirty consuming channels, never unrelated display outputs."""
    pointer = image.as_pointer()
    for scene in bpy.data.scenes:
        if not hasattr(scene, 'open_painter'):
            continue
        for ts in scene.open_painter.texture_sets:
            from .references import dependent_channels
            kinds = {m.kind for m in ts.baked_maps if m.image and m.image.as_pointer() == pointer}
            channels = set()
            for layer in ts.layers:
                direct = {p.channel for p in layer.channels
                          if p.image == image or getattr(p, 'detail_image', None) == image
                          or any(e.image == image for e in p.effects)}
                channels.update(direct)
                if direct:
                    # Published channel inputs can drive other channel masks.
                    # Save evaluates dirty outputs only, so their consumers must
                    # be dirtied even when they do not own the changed image.
                    for channel in direct:
                        channels.update(dependent_channels(ts, layer.id, channel))
                masks = any(m.enabled and (m.image == image or m.effect.image == image or
                            (kinds and (map_dependencies(m) is None or kinds & map_dependencies(m))))
                            for m in layer.masks)
                if masks:
                    # Native recipes need no allocated baked outputs. Derive
                    # consumers from the recipe, including published masks.
                    channels.update(prop.channel for prop in layer.channels if prop.enabled)
                    channels.update(dependent_channels(ts, layer.id, 'MASK'))
                if direct or masks:
                    layer.dirty = True
            if channels:
                previous_dirty = {output.channel: output.dirty for output in ts.outputs}
                ts.dirty = True
                ts.edit_serial += 1
                for output in ts.outputs:
                    if output.channel in channels:
                        output.dirty = True
                from .shader_preview import notify
                notify(ts, previous_dirty=previous_dirty)


def clear():
    _mesh_epochs.clear(); _mesh_fingerprints.clear(); _known.clear(); _candidates.clear()


def prune(live):
    for key in set(_known)-set(live):
        _known.pop(key, None)
    meshes = {m.as_pointer() for m in bpy.data.meshes}
    for values in (_mesh_epochs, _mesh_fingerprints):
        for key in set(values)-meshes:
            values.pop(key, None)
