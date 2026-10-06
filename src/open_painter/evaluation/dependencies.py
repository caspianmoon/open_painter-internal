"""Geometry lifetimes independent of material edits; handlers only mark candidates.

Hash base authoring data only after Blender reports a geometry update, never
on each tile/timer tick. Projection acquires evaluated geometry in an owned
scope; no evaluated mesh is retained. Modifier RNA also contributes to keys.
"""
import ctypes
import secrets
import bpy
import numpy as np
from bpy.app.handlers import persistent
from .resource_keys import datablock_key, key_for
from .metrics import measured
from . import structure_memo
from .structure_memo import memoised

_mesh_epochs = {}
_object_epochs = {}   # object pointer -> the counter value of its last evaluated-geometry update
_epoch = 0
_mesh_fingerprints = {}
# Mesh session id -> (what mesh_signature hashed, its digest). It outlives ``clear``: an undo
# drops the pointer cache above, and an unchanged mesh is then recognised by its content.
_mesh_contents = {}
CONTENT_BYTES = 128 << 20   # kept per mesh (about 950K vertices); a larger one is hashed again after every undo
_known = {}
_candidates = set()
_image_epochs = {}   # image pointer -> counter value of its last reported update
_image_counter = [0]

_uses = structure_memo.store()   # whether anything but auto edge rebakes reads the geometry
_placed = structure_memo.store()   # (whether the stack reads placement, whether it has Mask Graphs)
_readers = structure_memo.store()   # where the stack's geometry readers sit: ``_reader_walk``


def _is_live(mask):
    return (mask.source == 'SCENE' and mask.influence.mode == 'LIVE') \
        or (mask.source == 'GENERATOR' and getattr(mask, 'edge_source', 'BAKED') == 'LIVE')


def _is_mesh(mask):
    return mask.source == 'MESH' and mask.enabled


def _is_decal(layer):
    return layer.type == 'FILL' and (layer.decal_object is not None or bool(layer.decal_object_name))


def live_fields(ts):
    """Masks whose scene field is analysed live: LIVE scene masks and live-edge generators."""
    return [m for l in ts.layers for m in l.masks if _is_live(m)]


def uses_strokes(ts):
    return any(getattr(l, 'stroke_key', '') for l in ts.layers) or any(
        m.source == 'STROKES' and m.stroke_key for l in ts.layers for m in l.masks)


def mesh_masks(ts):
    """Mesh masks (0.9.2) whose coverage follows the scene structure."""
    return [m for l in ts.layers for m in l.masks if _is_mesh(m)]


def graph_masks(ts):
    """Custom Graph masks (0.9.3): the CPU reference bakes the group over the assigned meshes."""
    return [m for l in ts.layers for m in l.masks if m.source == 'GRAPH' and m.enabled and m.node_group]


def _reader_walk(ts):
    """Where the stack's geometry readers sit: (layer count, live-field masks, mesh masks of
    any state, decal layers, layers holding a decal object) as stack positions."""
    live, mesh, decal, held = [], [], [], []
    for li, layer in enumerate(ts.layers):
        if layer.decal_object is not None:
            held.append(li)
        if _is_decal(layer):
            decal.append(li)
        for mi, mask in enumerate(layer.masks):
            if _is_live(mask):
                live.append((li, mi))
            if mask.source == 'MESH':
                mesh.append((li, mi))
    return len(ts.layers), tuple(live), tuple(mesh), tuple(decal), tuple(held)


def _readers_of(ts, part, predicate, walk):
    """One kind of reader, from the per-structure index instead of a walk over the stack.
    Each is checked again: Blender nulls a deleted Empty without a callback, and a lost
    decal pointer clears its name mirror. A stack whose length moved under an unchanged
    serial is walked afresh (``walk``)."""
    readers = memoised(_readers, ts, lambda: _reader_walk(ts))
    layers = ts.layers
    if readers[0] != len(layers):
        return walk(ts)
    found = []
    try:
        for at in readers[part]:
            item = layers[at] if isinstance(at, int) else layers[at[0]].masks[at[1]]
            if predicate(item):
                found.append(item)
    except IndexError:
        return walk(ts)
    return found


def decal_layers(ts):
    """``decals.layers_of`` from the per-structure index."""
    from ..painting import decals
    return _readers_of(ts, 3, _is_decal, decals.layers_of)


def _live_readers(ts):
    return _readers_of(ts, 1, _is_live, live_fields)


def _mesh_readers(ts):
    return _readers_of(ts, 2, _is_mesh, mesh_masks)


def _named_readers(ts):
    """(layers holding a decal object, mesh masks of any state): the name mirrors."""
    held = _readers_of(ts, 4, lambda layer: layer.decal_object is not None,
                       lambda ts: [layer for layer in ts.layers if layer.decal_object is not None])
    mesh = _readers_of(ts, 2, lambda mask: mask.source == 'MESH',
                       lambda ts: [m for layer in ts.layers for m in layer.masks if m.source == 'MESH'])
    return held, mesh


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
    and decals (authored in world space), live scene fields, custom graphs with a Position
    or Normal input. Physical sizes, seam maps, edge rebakes and mesh masks see the surface
    alone: a normal-direction mask reads the objects' orientation, which its own selector
    signature carries (``mesh_masks._orientation``), so a drag leaves the set's key alone.

    The walk over the stack is memoised per structure. A Mask Graph's own nodes are read
    each time: editing them moves no serial of the set."""
    placed, graphs = memoised(_placed, ts, lambda: _placement_walk(ts))
    if placed or not graphs:
        return placed
    from .custom_graph import reads_placement
    return any(reads_placement(m.node_group) for m in graph_masks(ts))


def _placement_walk(ts):
    """(whether the stack reads placement, whether it has Mask Graphs that may)."""
    from ..painting import decals
    placed = any(p.projection == 'TRIPLANAR' or getattr(p, 'material_mapping_version', 0) == 1
                 for l in ts.layers for p in (*l.channels, *l.masks)) \
        or bool(live_fields(ts)) or uses_strokes(ts) or bool(decals.layers_of(ts))
    return placed, bool(graph_masks(ts))


def uses_geometry(ts):
    """Whether anything on the set reads the assigned geometry. The walk over every channel,
    effect and mask is memoised per structure: the scheduler asks on each tick of a drag."""
    return memoised(_uses, ts, lambda: _geometry_walk(ts)) or bool(ts.bake.auto_edge_rebake)


def _geometry_walk(ts):
    from . import physical
    return uses_placement(ts) or bool(mesh_masks(ts)) or bool(graph_masks(ts)) \
        or uses_surface_effects(ts) or physical.uses(ts)


@measured('geometry_signature')
def mesh_signature(mesh):
    """SHA-256 of the mesh's geometry: positions and normals, topology, materials, smooth
    shading, corner normals and authoring UVs.

    The fastest SHA-256 at hand (``utils.digest``) hashes the bytes the RNA collections give,
    read where Blender keeps them (attributes) and derived exactly where they are not stored
    (a face's corner count, smooth shading from ``sharp_face``): the same digest as hashing
    the collections' own reads with hashlib, about three times sooner. Every memfile undo
    drops the cache (``clear``); a mesh whose content is the one last hashed, byte for byte,
    keeps its digest without hashing again."""
    pointer = mesh.as_pointer()
    epoch = _mesh_epochs.get(pointer, 0)
    cached = _mesh_fingerprints.get(pointer)
    # A mesh created where a freed one lived reuses its address, never its session id.
    if cached and cached[0] == epoch and cached[2] == mesh.session_uid:
        return cached[1]
    parts = [(str((prop, count)).encode(), values) for prop, count, values in _mesh_arrays(mesh)]
    attributes = mesh.attributes
    for uv in mesh.uv_layers:
        if uv.name == mesh.get('open_painter_udim_paint_uv', ''):
            continue   # derived native-paint coordinates, never authoring geometry
        original_uv = mesh.get('open_painter_udim_original_uv')
        active = uv.name == original_uv if original_uv is not None else uv == mesh.uv_layers.active
        values = np.empty(len(uv.data)*2, np.float32)
        stored = attributes.get(uv.name)
        if stored is not None and stored.data_type == 'FLOAT2' and stored.domain == 'CORNER':
            stored.data.foreach_get('vector', values)
        else:
            uv.data.foreach_get('uv', values)
        parts.append((str((uv.name, uv.active_render, active)).encode(), values))
    kept = _mesh_contents.get(mesh.session_uid)
    if kept is not None and _same_parts(kept[0], parts):
        signature = kept[1]
    else:
        from ..utils.digest import sha256
        h = sha256()
        for header, values in parts:
            h.update(header); h.update(values)
        signature = h.hexdigest()
        if sum(values.nbytes for _, values in parts) <= CONTENT_BYTES:
            _mesh_contents[mesh.session_uid] = parts, signature
        else:
            _mesh_contents.pop(mesh.session_uid, None)
    _mesh_fingerprints[pointer] = epoch, signature, mesh.session_uid
    return signature


def _same_parts(a, b):
    """Whether two ``mesh_signature`` inputs are the same bytes."""
    return len(a) == len(b) and all(
        x[0] == y[0] and x[1].dtype == y[1].dtype and x[1].shape == y[1].shape
        and np.array_equal(x[1].view(np.uint8), y[1].view(np.uint8)) for x, y in zip(a, b))


def _direct(collection, count, dtype, width, read):
    """``count`` elements of an RNA collection of numbers, copied from the array Blender keeps
    them in, or None. RNA reads many of these element by element (``foreach_get`` takes 46 ns
    an element for an integer attribute). Only when the elements' addresses are those of one
    back-to-back array (the first and the last say so) and the first, middle and last element
    read through RNA (``read``) hold the same values."""
    if not count:
        return None
    try:
        size = np.dtype(dtype).itemsize * width
        first, last = collection[0].as_pointer(), collection[count - 1].as_pointer()
        if not first or last - first != size * (count - 1):
            return None
        values = np.frombuffer(ctypes.string_at(first, size * count), dtype).copy()
        for index in (0, count // 2, count - 1):
            expected = read(collection[index])
            if tuple(np.atleast_1d(values[index * width:(index + 1) * width]).tolist()) != (
                    tuple(expected) if width > 1 else (expected,)):
                return None
        return values
    except (AttributeError, TypeError, ValueError, IndexError, ReferenceError):
        return None


def _attribute(mesh, name, data_type, domain):
    stored = mesh.attributes.get(name)
    return stored if stored is not None and stored.data_type == data_type and stored.domain == domain else None


def _mesh_arrays(mesh):
    """(name, element count, array) in ``mesh_signature``'s order, each the bytes the RNA
    collection's ``foreach_get`` gives: vertices' ``co`` and ``normal``, loops' ``vertex_index``,
    polygons' ``loop_start``, ``loop_total``, ``material_index`` and ``use_smooth``, and the
    corner normals. Most RNA reads go element by element; the attributes they read are copied
    whole, and what no attribute stores is derived."""
    vertices, loops, faces = len(mesh.vertices), len(mesh.loops), len(mesh.polygons)
    positions = np.empty(vertices * 3, np.float32)
    stored = _attribute(mesh, 'position', 'FLOAT_VECTOR', 'POINT')
    if stored is not None:
        stored.data.foreach_get('vector', positions)
    else:
        mesh.vertices.foreach_get('co', positions)
    yield 'co', vertices, positions
    normals = np.empty(vertices * 3, np.float32)
    mesh.vertex_normals.foreach_get('vector', normals)
    yield 'normal', vertices, normals
    stored = _attribute(mesh, '.corner_vert', 'INT', 'CORNER')
    corners = _direct(stored.data, loops, np.int32, 1, lambda item: item.value) if stored is not None else None
    if corners is None:
        corners = np.empty(loops, np.int32)
        if stored is not None:
            stored.data.foreach_get('value', corners)
        else:
            mesh.loops.foreach_get('vertex_index', corners)
    yield 'vertex_index', loops, corners
    starts = _direct(mesh.polygons, faces, np.int32, 1, lambda item: item.loop_start)
    if starts is None:
        starts = np.empty(faces, np.int32)
        mesh.polygons.foreach_get('loop_start', starts)
    yield 'loop_start', faces, starts
    yield 'loop_total', faces, np.diff(np.append(starts, np.int32(loops))).astype(np.int32)
    stored = _attribute(mesh, 'material_index', 'INT', 'FACE')
    materials = _direct(stored.data, faces, np.int32, 1, lambda item: item.value) if stored is not None else None
    if materials is None:
        materials = np.zeros(faces, np.int32)   # no attribute: every face uses the first slot
        if stored is not None:
            stored.data.foreach_get('value', materials)
    yield 'material_index', faces, materials
    smooth = np.ones(faces, np.bool_)       # no attribute: every face is smooth
    stored = _attribute(mesh, 'sharp_face', 'BOOLEAN', 'FACE')
    if stored is not None:
        stored.data.foreach_get('value', smooth)
        np.logical_not(smooth, out=smooth)
    yield 'use_smooth', faces, smooth
    corner_normals = mesh.corner_normals
    values = np.empty(len(corner_normals) * 3, np.float32)
    corner_normals.foreach_get('vector', values)
    yield 'vector', len(corner_normals), values


def mesh_epoch(mesh):
    """A session-unique stamp of the mesh's last reported geometry update (0 before the first).
    Stamps are drawn from the scene-wide update counter, which no undo or redo resets, so a
    redo never lands on the value an undo's update carried."""
    return _mesh_epochs.get(mesh.as_pointer(), 0)


def object_epoch(obj):
    """The stamp of the object's last reported evaluated-geometry update (0 before the first)."""
    return _object_epochs.get(obj.as_pointer(), 0)


_layer_digests = {}   # (mesh pointer, layer name) -> (mesh epoch, digest)

# Attributes Subdivision and Bevel read besides positions and topology.
_CREASE_LAYERS = ('crease_edge', 'crease_vert', 'bevel_weight_edge', 'bevel_weight_vert')

# RNA exposes the deform layer only per vertex, so reading a vertex group's weights is a
# Python loop over every vertex (about a second per million vertices with one group, more
# on a rigged asset). Meshes above this many vertices identify their weights by the mesh
# update epoch instead of a digest: a weight stroke or an Edit Mode exit then costs nothing
# here, at the price of re-evaluating after an undo or a reopen rather than on a changed
# weight alone.
WEIGHT_HASH_BOUND = 250_000

# Salts the epoch stand-ins: a persisted provenance carrying one must never compare
# equal to the same epoch value of another session, whose weights it knows nothing of.
# A session is one run of the epochs: ``clear`` restarts them at 0 for every mesh on a
# load, undo or redo, so it draws a fresh salt too, else the provenance a big mesh got
# before the reload (say ``epoch:<salt>:0``, untouched) would compare equal to the
# reopened mesh at epoch 0, whose weights the file may have changed in between.
_SESSION = secrets.token_hex(4)


def weights_hashed(mesh):
    """Whether the mesh is small enough for its vertex-group weights to be digested."""
    return len(mesh.vertices) <= WEIGHT_HASH_BOUND


def epoch_token(mesh):
    """The stand-in for a weight digest above ``WEIGHT_HASH_BOUND``: the mesh's update epoch,
    unique for the session and never equal to another session's, nor to one of the same
    process before its last load, undo or redo (a provenance carrying it re-evaluates once
    per such restart: the price of not looping over the vertices)."""
    return 'epoch:%s:%d' % (_SESSION, mesh_epoch(mesh))


def group_weights(obj, mesh, name):
    """Per-vertex weights of the object's vertex group ``name`` read off ``mesh`` (its own
    data, or an evaluated copy: those keep the deform layer), or None without such a group.
    The per-vertex loop is the only way RNA offers; callers bound it by ``weights_hashed``
    or cache the result per mesh epoch."""
    group = obj.vertex_groups.get(name)
    if group is None:
        return None
    weights = np.zeros(len(mesh.vertices), np.float32)
    for vertex in mesh.vertices:
        for entry in vertex.groups:
            if entry.group == group.index:
                weights[vertex.index] = entry.weight
                break
    return weights


def _layer_digest(obj, name, weights):
    """Digest of one deform layer (vertex-group weights) or crease attribute of the mesh,
    recomputed only after a reported geometry update; None when the mesh lacks it. Above
    ``WEIGHT_HASH_BOUND`` the weights are stood in for by the epoch token."""
    mesh = obj.data
    key = (mesh.as_pointer(), name)
    epoch = _mesh_epochs.get(mesh.as_pointer(), 0)
    cached = _layer_digests.get(key)
    if cached is not None and cached[0] == epoch and cached[2] == mesh.session_uid:
        return cached[1]
    from ..utils.digest import sha256
    digest = None
    if weights:
        if name in obj.vertex_groups:
            digest = epoch_token(mesh) if not weights_hashed(mesh) \
                else sha256(group_weights(obj, mesh, name).tobytes()).hexdigest()[:16]
    else:
        attribute = mesh.attributes.get(name)
        if attribute is not None and attribute.data_type == 'FLOAT':
            values = np.empty(len(attribute.data), np.float32)
            attribute.data.foreach_get('value', values)
            digest = sha256(values.tobytes()).hexdigest()[:16]
    _layer_digests[key] = (epoch, digest, mesh.session_uid)
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


# Modifiers whose evaluated surface follows another object's pose or the clock, which the
# key cannot read off the owner: it hashes the base mesh and the modifier's RNA values,
# not a bone's rotation, a hook target's drag, a simulation or point-cache state, an
# emitter's particles or an Alembic, .mdd or .pc2 frame.
_TIME_MODIFIERS = frozenset({'ARMATURE', 'HOOK', 'LATTICE', 'MESH_DEFORM', 'SURFACE_DEFORM', 'SHRINKWRAP',
                             'WARP', 'CAST', 'SIMPLE_DEFORM', 'WAVE', 'CLOTH', 'SOFT_BODY',
                             'MESH_SEQUENCE_CACHE', 'MESH_CACHE', 'FLUID', 'DYNAMIC_PAINT',
                             'EXPLODE', 'PARTICLE_INSTANCE', 'NODES'})


def _keyed(datablock):
    """Whether the datablock's animation data (an action, drivers, NLA strips) can change it
    with the frame. Layered actions of Blender 5 still hang off ``animation_data.action``."""
    data = getattr(datablock, 'animation_data', None)
    return data is not None and bool(data.action or len(data.drivers) or len(data.nla_tracks))


def _modifier_objects(modifier):
    """The objects a modifier reads besides its owner: a Boolean cutter, a Shrinkwrap or
    Data Transfer target, a UV projector, a Geometry Nodes object input, the members of a
    Boolean's cutter collection. Their animation moves the owner's evaluated surface just
    as the owner's own would, and the owner's RNA says nothing about it."""
    for prop in modifier.bl_rna.properties:
        if prop.type != 'POINTER' or prop.identifier == 'rna_type':
            continue
        value = getattr(modifier, prop.identifier, None)
        if isinstance(value, bpy.types.Object):
            yield value
        elif isinstance(value, bpy.types.Collection):
            yield from value.all_objects
    for projector in getattr(modifier, 'projectors', ()):
        if projector.object is not None:
            yield projector.object
    if modifier.type != 'NODES':
        return
    inputs = getattr(getattr(modifier, 'properties', None), 'inputs', None)
    for socket in (inputs.keys() if inputs is not None else ()):
        if socket == '_RNA_UI':
            continue
        try:
            fields = inputs[socket].to_dict()
        except (AttributeError, TypeError):
            continue
        for value in fields.values():
            if isinstance(value, bpy.types.Object):
                yield value


def _time_dependent(obj, seen):
    """The object, its data or shape keys, or an object up its parent chain is animated, or
    one of its modifiers deforms it from time or from another object that is. ``seen``
    bounds the walk: a cutter that reads its owner back, or a target shared by every
    assignment, is visited once."""
    while obj is not None and obj.as_pointer() not in seen:
        seen.add(obj.as_pointer())
        data = obj.data
        if _keyed(obj) or _keyed(data) or _keyed(getattr(data, 'shape_keys', None)):
            return True
        for modifier in obj.modifiers:
            if modifier.type in _TIME_MODIFIERS \
                    or (modifier.type == 'DISPLACE' and modifier.texture is not None):
                return True
            # A static cutter keeps the owner static; an animated one (its own keys, a
            # parent's, a deformer of its own) carries the frame into the owner's key.
            if any(_time_dependent(target, seen) for target in _modifier_objects(modifier)):
                return True
        obj = obj.parent
    return False


def animated(ts):
    """Whether an assigned object's evaluated geometry can differ between frames. Only
    then does the frame belong in the set's keys: a static prop would otherwise re-raster
    every CPU boundary and invalidate the set on each scrub of the timeline."""
    seen = set()
    return any(_time_dependent(ref.object, seen) for ref in ts.assignments if ref.object is not None)


# (*key_for(scene, ts), stable) -> (token, geometry key). The key is asked for once per binding
# identity while a shader graph compiles, and the walk over modifiers, deform layers and
# selectors is not free; it can only change with a reported depsgraph update, an authoring
# edit or a frame. A key built under ``stable_keys`` names datablocks instead of tokenising
# them (a persisted cache provenance), so the two forms are memoised apart.
_keys = {}


def _key_token(scene, ts):
    # Assignments and the auto-rebake toggle carry no update callback, so an edit of theirs
    # bumps neither the epoch nor the serial: they are read directly, they are cheap. The
    # assigned meshes' own epochs come along: ``on_depsgraph`` moves them with the counter,
    # but a stroke reported through ``mesh_epoch`` alone (as the suites simulate) must not
    # be served the key of the mesh before it.
    return (_epoch, structure_memo.token(ts), scene.frame_current, scene.frame_subframe, bool(ts.bake.auto_edge_rebake),
            getattr(ts, 'udim_tile', 0), getattr(ts, 'udim_source_uv', ''),
            tuple((ref.object.as_pointer() if ref.object is not None else None, ref.slot_index,
                   _mesh_epochs.get(ref.object.data.as_pointer(), 0)
                   if ref.object is not None and ref.object.type == 'MESH' else None)
                  for ref in ts.assignments))


def geometry_key(scene, ts):
    """(resolution, time, objects, live fields, mesh-mask selectors, decal frames), or None
    when nothing on the set reads the assigned geometry. ``time`` is the frame and subframe
    for an animated set (``animated``) and None for a static one, so scrubbing leaves its
    key, and every identity stamped with the key, alone."""
    if not uses_geometry(ts):
        return None
    from .resource_keys import stable
    slot = (*key_for(scene, ts), stable())
    token = _key_token(scene, ts)
    cached = _keys.get(slot)
    if cached is not None and cached[0] == token:
        return cached[1]
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
    fields = tuple(signature(ts, m.influence) for m in _live_readers(ts))
    from .mesh_masks import signature as mesh_mask_signature
    selectors = tuple(mesh_mask_signature(ts, m) for m in _mesh_readers(ts))
    from ..painting import decals
    frames = tuple(decals.signature(layer) for layer in decal_layers(ts))
    time = (scene.frame_current, scene.frame_subframe) if animated(ts) else None
    key = (int(ts.resolution), time, tuple(objects), fields, selectors, frames)
    if getattr(ts, 'udim_tile', 0):
        key += (ts.udim_tile, ts.udim_source_uv)
    _keys[slot] = (token, key)
    return key


def remember(scene, ts, signature):
    _known[key_for(scene, ts)] = signature


def epoch():
    """Counts geometry/transform depsgraph updates; a cheap cache token for mesh-derived state."""
    return _epoch


def image_epoch():
    """Counts reported image updates (painting, reloads) of images the engine may sample."""
    return _image_counter[0]


def image_epochs():
    """image pointer -> the counter value of its last reported update."""
    return _image_epochs


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
            if update.is_updated_geometry:
                # The evaluated surface changed without the mesh datablock (a modifier or
                # its inputs): a running material gesture reads this to cancel safely.
                _object_epochs[item.as_pointer()] = _epoch + 1
            relevant = True
        elif isinstance(item, (bpy.types.Collection, bpy.types.Material, bpy.types.Object)):
            # Collection membership, material slots and selection feed mesh masks (0.9.2).
            relevant = True
        elif isinstance(item, bpy.types.Image) and not item.get('op_engine_output'):
            # A painted or reloaded image the engine may sample: its stamp moves (the
            # engine's own published images report updates too, and are not inputs).
            # Only an update of its content does: a paint stroke, an image undo and
            # update_tag() tag the whole image. A memfile undo re-syncs the images it
            # restores (the tags pending when its step was pushed) with no geometry,
            # shading or transform flag, and Blender keeps their pixels: moving the stamp
            # then rendered every unit sampling them again for nothing.
            if update.is_updated_geometry or update.is_updated_shading or update.is_updated_transform:
                _image_counter[0] += 1
                _image_epochs[item.as_pointer()] = _image_counter[0]
    if relevant:
        _epoch += 1
        _candidates.add(scene.as_pointer())


def report_updates():
    """Evaluate the pending depsgraph updates now, so ``on_depsgraph`` reports them before the
    scheduler samples any image. An image an operator just referenced (a smart material's detail,
    a picked texture) joins the depsgraph on its next evaluation, which reports it as updated:
    Blender runs that evaluation after the timers, so the tick sampled the image first and the
    next tick rendered every unit sampling it again. Nothing pending costs nothing. A render or
    bake job may own the scene (a locked interface): Blender's loop alone evaluates then."""
    try:
        if bpy.app.is_job_running('RENDER') or bpy.app.is_job_running('OBJECT_BAKE'):
            return
        bpy.context.evaluated_depsgraph_get()
    except (AttributeError, RuntimeError, ReferenceError, TypeError):
        pass   # no view layer to evaluate


@persistent
def on_frame(scene, *args):
    _candidates.add(scene.as_pointer())


def refresh_names(ts):
    """Name mirrors follow renamed datablocks: they are the re-link keys of portable materials
    and the labels shown once a datablock is gone."""
    for ref in ts.assignments:
        if ref.object is not None and ref.object_name != ref.object.name:
            ref.object_name = ref.object.name
    held, mesh = _named_readers(ts)
    for layer in held:
        obj = layer.decal_object
        if obj is not None and layer.decal_object_name != obj.name:
            layer.decal_object_name = obj.name
    for mask in mesh:
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
    decals.adopt_duplicates(scene, layers=decal_layers)
    from ..aging import engine as aging_engine
    aging_engine.follow_animation(scene)   # a keyframed age re-drives its folder (0.10.0)
    for ts in scene.open_painter.texture_sets:
        refresh_names(ts)
        decals.show_images(ts, decal_layers(ts))   # the picture's aspect is part of the frame (0.9.7): swap it before keying
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
            if current[:5] == previous[:5] and current[5:] != previous[5:] \
                    and _decal_shapes(current[5]) == _decal_shapes(previous[5]):
                # Only decal frames moved: the shader reads them natively, so a numeric
                # notification (no structural repair per drag frame) keeps the session current
                # while outputs and CPU caches are marked stale. (The Empty's name, display
                # size and image offset are compiled constants: those still restructure.)
                from .shader_preview import notify
                ts.dirty = True
                ts.edit_serial += 1
                for output in ts.outputs:
                    output.dirty = True
                for layer in decal_layers(ts):
                    notify(layer, structural=False)
                continue
            targets_changed = current[:3] != previous[:3]   # resolution, time, objects
            if targets_changed and field_jobs.active() and ts.bake.auto_edge_rebake and _mesh_edited(previous[2], current[2]):
                from ..baking.bake_manager import owned_curvature
                if owned_curvature(ts) is not None:
                    field_jobs.schedule_rebake(scene, ts)
            if not field_jobs.active():
                invalidate(ts)   # synchronous analysis re-runs inside the next evaluation
                continue
            # A mesh mask's membership, selection, attribute or mesh changed (its
            # signature carries the mesh, so an edit re-rasters it without
            # skipping the edge rebake and live-field requests above and below).
            if current[4:] != previous[4:] or (targets_changed and (
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


def clear(undo=False):
    """What load_post, undo_post and redo_post run: the epochs restart for every mesh.

    An ``undo`` (or redo) keeps the image epochs. A memfile undo leaves images where they were,
    with their pixel caches (Blender preserves them for re-read datablocks too), and an image
    undo tags every image it restores, which moves its epoch (``on_depsgraph``). Restarting
    them made every unit sampling an image render again after every undo."""
    global _SESSION
    from . import mesh_masks
    from ..painting import decals
    _mesh_epochs.clear(); _object_epochs.clear(); _mesh_fingerprints.clear(); _layer_digests.clear(); _known.clear(); _keys.clear(); _candidates.clear()
    if not undo:
        _image_epochs.clear()
        _mesh_contents.clear()   # another file: its meshes are new
    structure_memo.clear()
    _SESSION = secrets.token_hex(4)   # the restarted epochs must not answer for the tokens issued before
    mesh_masks.clear()   # its digests are tagged with the epochs
    decals.forget()      # which Empties this session's layers held: another file's layers start over


def prune(live):
    from . import mesh_masks
    for key in set(_known)-set(live):
        _known.pop(key, None)
    for key in [k for k in _keys if k[:2] not in live]:
        _keys.pop(key, None)
    structure_memo.prune({key[1] for key in live})
    meshes = {m.as_pointer() for m in bpy.data.meshes}
    for values in (_mesh_epochs, _mesh_fingerprints):
        for key in set(values)-meshes:
            values.pop(key, None)
    uids = {m.session_uid for m in bpy.data.meshes}
    for key in set(_mesh_contents)-uids:
        _mesh_contents.pop(key, None)
    for key in [k for k in _layer_digests if k[0] not in meshes]:
        _layer_digests.pop(key, None)
    mesh_masks.prune(meshes)
