"""Geometry lifetimes independent of material edits; handlers only mark candidates.

Hash base authoring data only after Blender reports a geometry update, never
on each tile/timer tick. Projection acquires evaluated geometry in an owned
scope; no evaluated mesh is retained. Modifier RNA also contributes to keys.
"""
import hashlib
import bpy
import numpy as np
from bpy.app.handlers import persistent
from .resource_keys import datablock_key, key_for
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


def uses_strokes(ts):
    return any(getattr(l, 'stroke_key', '') for l in ts.layers) or any(
        m.source == 'STROKES' and m.stroke_key for l in ts.layers for m in l.masks)


def mesh_masks(ts):
    """Mesh masks (0.9.2) whose coverage follows the scene structure."""
    return [m for l in ts.layers for m in l.masks if m.source == 'MESH' and m.enabled]


def graph_masks(ts):
    """Custom Graph masks (0.9.3): the CPU reference bakes the group over the assigned meshes."""
    return [m for l in ts.layers for m in l.masks if m.source == 'GRAPH' and m.enabled and m.node_group]


def uses_surface_effects(ts):
    """Surface-space (seam-aware) effects read the mesh through the seam map (0.9.4)."""
    from .operation_contracts import surface
    for layer in ts.layers:
        for prop in layer.channels:
            if any(surface(e) for e in prop.effects):
                return True
        for mask in layer.masks:
            if mask.source == 'FILTER' and surface(mask.effect):
                return True
    return False


def uses_placement(ts):
    """Consumers that read where the assigned objects stand: triplanar projection, strokes
    and decals (authored in world space), live scene fields, normal-direction mesh masks,
    custom graphs with a Position or Normal input. Physical sizes, seam maps, edge rebakes
    and the other mesh masks and graphs see the surface alone."""
    from ..painting import decals
    from .custom_graph import reads_placement
    return any(p.projection == 'TRIPLANAR' for l in ts.layers for p in (*l.channels, *l.masks)) \
        or bool(live_fields(ts)) or uses_strokes(ts) or bool(decals.layers_of(ts)) \
        or any(m.mesh.kind == 'NORMAL_DIRECTION' for m in mesh_masks(ts)) \
        or any(reads_placement(m.node_group) for m in graph_masks(ts))


def uses_geometry(ts):
    from . import physical
    return uses_placement(ts) or bool(ts.bake.auto_edge_rebake) or bool(mesh_masks(ts)) \
        or bool(graph_masks(ts)) or uses_surface_effects(ts) or physical.uses(ts)


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


def mesh_epoch(mesh):
    """A session-unique stamp of the mesh's last reported geometry update (0 before the first).
    Stamps are drawn from the scene-wide update counter, which no undo or redo resets, so a
    redo never lands on the value an undo's update carried."""
    return _mesh_epochs.get(mesh.as_pointer(), 0)


_layer_digests = {}   # (mesh pointer, layer name) -> (mesh epoch, digest)

# Attributes Subdivision and Bevel read besides positions and topology.
_CREASE_LAYERS = ('crease_edge', 'crease_vert', 'bevel_weight_edge', 'bevel_weight_vert')


def _layer_digest(obj, name, weights):
    """Digest of one deform layer (vertex-group weights) or crease attribute of the mesh,
    recomputed only after a reported geometry update; None when the mesh lacks it."""
    mesh = obj.data
    key = (mesh.as_pointer(), name)
    epoch = _mesh_epochs.get(mesh.as_pointer(), 0)
    cached = _layer_digests.get(key)
    if cached is not None and cached[0] == epoch:
        return cached[1]
    digest = None
    if weights:
        group = obj.vertex_groups.get(name)
        if group is not None:
            values = np.zeros(len(mesh.vertices), np.float32)
            for vertex in mesh.vertices:
                for entry in vertex.groups:
                    if entry.group == group.index:
                        values[vertex.index] = entry.weight
                        break
            digest = hashlib.sha256(values.tobytes()).hexdigest()[:16]
    else:
        attribute = mesh.attributes.get(name)
        if attribute is not None and attribute.data_type == 'FLOAT':
            values = np.empty(len(attribute.data), np.float32)
            attribute.data.foreach_get('value', values)
            digest = hashlib.sha256(values.tobytes()).hexdigest()[:16]
    _layer_digests[key] = (epoch, digest)
    return digest


def mesh_inputs(obj):
    """Mesh data the evaluated surface reads beyond positions, topology and UVs: shape-key
    values, the vertex groups its modifiers name, creases and bevel weights under a
    Subdivision or Bevel modifier. Empty for a plain mesh, so its records keep their form."""
    result = []
    keys = getattr(obj.data, 'shape_keys', None)
    if keys is not None:
        result.append(('shape_keys', tuple((block.name, round(float(block.value), 6), block.mute)
                                           for block in keys.key_blocks)))
    if obj.type != 'MESH':
        return tuple(result)
    groups, layers = set(), set()
    for modifier in obj.modifiers:
        for prop in modifier.bl_rna.properties:
            if prop.type == 'STRING' and not prop.is_readonly:
                name = getattr(modifier, prop.identifier, '')
                if name and name in obj.vertex_groups:
                    groups.add(name)
        if modifier.type in {'SUBSURF', 'BEVEL'}:
            layers.update(_CREASE_LAYERS)
    for name in sorted(groups):
        result.append(('vertex_group', name, _layer_digest(obj, name, weights=True)))
    for name in sorted(layers):
        digest = _layer_digest(obj, name, weights=False)
        if digest is not None:
            result.append(('attribute', name, digest))
    return tuple(result)


def _plain(value, ids):
    if isinstance(value, bpy.types.ID):
        return ids(value)
    if isinstance(value, dict):
        return tuple((key, _plain(item, ids)) for key, item in sorted(value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_plain(item, ids) for item in value)
    return value


def node_inputs(modifier, ids):
    """A Geometry Nodes modifier's group inputs, sorted by socket. Blender 5.2 keeps them
    under ``properties.inputs`` (the modifier itself carries no ID properties); ``ids``
    names the datablocks object, collection and image sockets point to."""
    inputs = getattr(getattr(modifier, 'properties', None), 'inputs', None)
    if inputs is None:
        return ()
    entries = []
    for socket in inputs.keys():
        if socket == '_RNA_UI':
            continue
        try:
            fields = inputs[socket].to_dict()
        except (AttributeError, TypeError):
            continue
        entries.append((socket, _plain(fields, ids)))
    return tuple(sorted(entries))


def dependency_key(owner, value, ids=datablock_key):
    """A modifier's referenced datablock. An object the modifier reads (a Boolean cutter, a
    Shrinkwrap target, a lattice, a Geometry Nodes object input) contributes its placement
    relative to the owner and its mesh, so moving or editing it re-keys the evaluated
    geometry while moving the assembly as a whole does not."""
    key = ids(value)
    if not isinstance(value, bpy.types.Object):
        return key
    relative = owner.matrix_world.inverted_safe() @ value.matrix_world
    return (key, tuple(round(float(v), 6) + 0.0 for row in relative for v in row),
            mesh_signature(value.data) if value.type == 'MESH' else None)


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
                if isinstance(value, (set, frozenset)):
                    value = tuple(sorted(value))   # enum flags: unordered and unhashable otherwise
                values.append((prop.identifier, tuple(value) if getattr(prop, "is_array", False) else value))
            elif prop.type == 'POINTER':
                # Name-based under stable_keys: a persisted cache provenance
                # must not carry another session's datablock address.
                values.append((prop.identifier, dependency_key(obj, value)))
        if modifier.type == 'NODES':
            values.append(('inputs', node_inputs(modifier, lambda value: dependency_key(obj, value))))
        result.append((modifier.type, tuple(values)))
    return tuple(result)


def placement_key(obj, full):
    """The object's world matrix, or only its world sizes (scale and mirroring) when nothing
    on the set reads where it stands: a drag then leaves every key alone."""
    matrix = obj.matrix_world
    if full:
        return tuple(v for row in matrix for v in row)
    return (tuple(round(float(v), 6) for v in matrix.to_scale()), matrix.to_3x3().determinant() < 0)


def geometry_key(scene, ts):
    if not uses_geometry(ts):
        return None
    objects = []
    placed = uses_placement(ts)
    for ref in ts.assignments:
        obj = ref.object
        if obj is None or obj.type != 'MESH':
            objects.append((None, ref.slot_index))
            continue
        mesh = obj.data
        objects.append((datablock_key(obj), datablock_key(mesh), ref.slot_index, placement_key(obj, placed),
                        mesh_signature(mesh), modifier_signature(obj), mesh_inputs(obj)))
    from ..geometry.influencers import signature
    fields = tuple(signature(ts, m.influence) for m in live_fields(ts))
    from .mesh_masks import signature as mesh_mask_signature
    selectors = tuple(mesh_mask_signature(ts, m) for m in mesh_masks(ts))
    from ..painting import decals
    frames = tuple(decals.signature(layer) for layer in decals.layers_of(ts))
    return (int(ts.resolution), scene.frame_current, scene.frame_subframe, tuple(objects), fields, selectors, frames)


def remember(scene, ts, signature):
    _known[key_for(scene, ts)] = signature


def epoch():
    """Counts geometry/transform depsgraph updates; a cheap cache token for mesh-derived state."""
    return _epoch


@persistent   # opening a file drops non-persistent handlers: the artist's session must keep tracking
def on_depsgraph(scene, depsgraph):
    global _epoch
    relevant = False
    for update in depsgraph.updates:
        item = update.id.original
        if isinstance(item, bpy.types.Mesh) and update.is_updated_geometry:
            pointer = item.as_pointer()
            _mesh_epochs[pointer] = _epoch+1   # unique for the session: ``clear`` empties the map, never the counter
            relevant = True
        elif isinstance(item, bpy.types.Object) and (update.is_updated_geometry or update.is_updated_transform):
            relevant = True
        elif isinstance(item, (bpy.types.Collection, bpy.types.Material, bpy.types.Object)):
            # Collection membership, material slots and selection feed mesh masks (0.9.2).
            relevant = True
    if relevant:
        _epoch += 1
        _candidates.add(scene.as_pointer())


@persistent
def on_frame(scene, *args):
    _candidates.add(scene.as_pointer())


def refresh_names(ts):
    """Name mirrors follow renamed datablocks: they are the re-link keys of portable materials
    and the labels shown once a datablock is gone."""
    for ref in ts.assignments:
        if ref.object is not None and ref.object_name != ref.object.name:
            ref.object_name = ref.object.name
    for layer in ts.layers:
        obj = layer.decal_object
        if obj is not None and layer.decal_object_name != obj.name:
            layer.decal_object_name = obj.name
        for mask in layer.masks:
            if mask.source != 'MESH':
                continue
            selector = mask.mesh
            for name in ('object', 'collection', 'material'):
                value = getattr(selector, name)
                if value is not None and getattr(selector, name + '_name') != value.name:
                    setattr(selector, name + '_name', value.name)


def refresh(scene):
    if scene.as_pointer() not in _candidates:
        return
    _candidates.discard(scene.as_pointer())
    from ..core.layer_stack import invalidate
    from . import field_jobs
    from ..painting import decals
    decals.adopt_duplicates(scene)
    for ts in scene.open_painter.texture_sets:
        refresh_names(ts)
        decals.show_images(ts)   # the picture's aspect is part of the frame (0.9.7): swap it before keying
        key = key_for(scene, ts)
        if key in _known and uses_geometry(ts):
            current = geometry_key(scene, ts)
            previous = _known[key]
            if current == previous:
                continue
            _known[key] = current
            from . import physical
            physical.sync(ts)   # metre sizes follow the surface (0.9.5); its cache keys on mesh and scale
            decals.sync(ts)     # decal frames follow their objects (0.9.6)
            if previous is None or current is None:
                invalidate(ts)
                continue
            if current[:6] == previous[:6] and current[6:] != previous[6:] \
                    and _decal_shapes(current[6]) == _decal_shapes(previous[6]):
                # Only decal frames moved: the shader reads them natively, so a numeric
                # notification (no structural repair per drag frame) keeps the session current
                # while outputs and CPU caches are marked stale. (The Empty's name, display
                # size and image offset are compiled constants: those still restructure.)
                from .shader_preview import notify
                ts.dirty = True
                ts.edit_serial += 1
                for output in ts.outputs:
                    output.dirty = True
                for layer in decals.layers_of(ts):
                    notify(layer, structural=False)
                continue
            targets_changed = current[:4] != previous[:4]
            if targets_changed and field_jobs.active() and ts.bake.auto_edge_rebake and _mesh_edited(previous[3], current[3]):
                from ..baking.bake_manager import owned_curvature
                if owned_curvature(ts) is not None:
                    field_jobs.schedule_rebake(scene, ts)
            if not field_jobs.active():
                invalidate(ts)   # synchronous analysis re-runs inside the next evaluation
                continue
            # A mesh mask's membership, selection, attribute or mesh changed (its
            # signature carries the mesh, so an edit re-rasters it without
            # skipping the edge rebake and live-field requests above and below).
            if current[5:] != previous[5:] or (targets_changed and (
                    any(p.projection == 'TRIPLANAR' for l in ts.layers for p in (*l.channels, *l.masks))
                    or graph_masks(ts) or not live_fields(ts))):
                invalidate(ts)
            # Live fields never block: request their analyses; publication invalidates.
            from ..generators.scene_fields import signature as field_signature
            for mask in live_fields(ts):
                desired = repr(field_signature(ts, mask.influence))
                if desired != mask.influence.provenance:
                    field_jobs.request(scene, ts, mask, desired)
                else:
                    field_jobs.discard(scene, ts, mask)   # moved back: the published raster is current


def _decal_shapes(frames):
    """The compiled part of decal signatures: object name, display size, image offset and aspect."""
    return tuple((frame[0], frame[2], frame[3], frame[7]) if frame else None for frame in frames)


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
    from . import mesh_masks
    from ..painting import decals
    _mesh_epochs.clear(); _mesh_fingerprints.clear(); _layer_digests.clear(); _known.clear(); _candidates.clear()
    mesh_masks.clear()   # its digests are tagged with the epochs
    decals.forget()      # which Empties this session's layers held: another file's layers start over


def prune(live):
    from . import mesh_masks
    for key in set(_known)-set(live):
        _known.pop(key, None)
    meshes = {m.as_pointer() for m in bpy.data.meshes}
    for values in (_mesh_epochs, _mesh_fingerprints):
        for key in set(values)-meshes:
            values.pop(key, None)
    for key in [k for k in _layer_digests if k[0] not in meshes]:
        _layer_digests.pop(key, None)
    mesh_masks.prune(meshes)
