"""Compile editable authoring data into a persistent Blender material graph.

Numeric edits update existing uniform sockets. Images are immutable inputs, not
the output mechanism. Legacy hashed fields and neighborhood filters are explicit
compatibility boundaries; each has its own transitive input identity and jobs.

The compiled material is a small top-level group tree that instances one node
group per compilation unit: a layer channel (blend and coverage), a channel
source prefix (content after the first ``count`` effects) or a mask prefix
(coverage through one mask). Blender re-validates a node tree on every node or
link insertion, so construction cost is the sum of the squared unit sizes rather
than the square of the whole graph. Blender inlines groups when it generates
shader code, so the GPU work is unchanged. Units are keyed by stable authoring
ids; a structural edit rebuilds only the units whose signature changed.
"""
from ast import literal_eval
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import os
import re
from time import perf_counter
from types import SimpleNamespace

import bpy
import numpy as np

from ..constants import CHANNELS, DEFAULTS, OWNER
from ..core.layer_stack import channel_for
from .shader_math import Math, Signal, blend, is_socket, luminance, normal_combine, srgb_encode
from .shader_effects import apply_effect, is_pointwise, UnsupportedEffect
from .shader_resources import Resources, RESOURCE, alive, bound, values
from .resource_keys import image_key
from .image_snapshots import stored_encoding
from .anchors import COMPOSITE_KINDS, channel_of
from . import metrics


class RebuildRequired(Exception):
    """A structural edit changed links or operation types, not just uniforms."""

    def __init__(self, message, scope='global'):
        super().__init__(message)
        self.scope = scope


def _effect_image_signature(image):
    # Which transfer effect_image compiles to sample the map as stored is wired
    # into the graph, so a retagged or swapped image rebuilds rather than
    # riding the image binding alone; None keeps "no image" distinct.
    return stored_encoding(image) if image else None


def _effects_signature(items):
    # A pointwise effect's enabled flag is a shader uniform, not structure.
    return tuple((e.kind, None if is_pointwise(e.kind) else bool(e.enabled), _effect_image_signature(e.image), e.id)
                 for e in items)


def _prop_signature(prop, count=None):
    effects = list(prop.effects)
    if count is not None:
        effects = effects[:count]
    # enabled and blend_mode belong to the channel unit that consumes this content.
    return (getattr(prop, "material_mapping_version", 0), prop.channel, bool(prop.image), bool(prop.detail_image), prop.procedural,
            prop.projection, prop.filtering, prop.tiling, prop.anchor, getattr(prop, 'anchor_channel', ''),
            _effects_signature(effects))


def _mask_signature(m):
    graph = getattr(m, 'node_group', None)
    from .text_source import signature as text_signature
    return ('baked-edge-leaf-v1', m.id, m.enabled, m.source, getattr(m, 'parent_id', ''), getattr(m, 'material_mapping_version', 0),
            graph.name if graph is not None and m.source == 'GRAPH' else None,
            text_signature(m.text) if m.source == 'TEXT' else None,
            m.generator, getattr(m, 'edge_source', 'BAKED'), getattr(m, 'stroke_key', ''),
            m.procedural, m.map_type, m.operation,
            m.projection, m.filtering, m.tiling, bool(m.image), m.reference_layer, m.reference_mask,
            m.reference_channel, m.reference_stage, m.reference_anchor, m.detail_height_anchor,
            m.detail_normal_anchor, m.effect.kind,
            None if is_pointwise(m.effect.kind) else m.effect.enabled, _effect_image_signature(m.effect.image))


def global_signature(ts):
    """Inputs every unit depends on: a change here rebuilds the whole graph."""
    return (int(ts.resolution), tuple((m.kind, bool(m.image)) for m in ts.baked_maps),
            getattr(ts, 'udim_tile', 0), getattr(ts, 'udim_source_uv', ''))


def structural_signature(ts):
    """Complete structural signature of a texture set (global, wiring and units)."""
    return Graph(ts.id_data, ts, None).structural_signature()


def _band_rows(halo, n):
    """Rows per cooperative strip of a bounded-footprint filter: the halo is
    filtered again for every strip, so strips grow with it (at most doubling
    the work) while each ``next()`` stays a fraction of the image."""
    if not halo:
        return 16
    return max(16, min(n, (2 * int(halo) // 16) * 16))


def _same_literal(a, b):
    """Baked export values compare equal across the JSON round trip of persisted trees."""
    try:
        return json.loads(json.dumps(a)) == json.loads(json.dumps(b))
    except (TypeError, ValueError):
        return a == b


def remove_orphan_units(ts_id):
    """Delete unit trees of this texture set that no top-level tree references."""
    for tree in list(bpy.data.node_groups):
        if (tree.get('op_shader_graph') == ts_id and tree.get('op_unit') and not tree.users
                and tree.get(OWNER)):
            bpy.data.node_groups.remove(tree)


def owned_tops(ts_id):
    """Live top-level graphs of one texture set, resolved without node references.

    Iterating ``bpy.data`` only ever yields live datablocks, so this is safe
    where reading ``SomeGroupNode.node_tree`` is not: that getter refines a
    raw ``bNode.id`` pointer, and Blender refines it without a validity check
    (``rna_NodeTree_refine`` dereferences ``ntree->typeinfo``), so a group
    node whose tree was freed access-violates Blender instead of raising.
    """
    return [tree for tree in bpy.data.node_groups
            if tree.get('op_shader_graph') == ts_id and not tree.get('op_unit')
            and tree.get(OWNER)]


def owned_units(ts_id):
    """Live unit trees of one texture set (same pointer-safe resolution)."""
    return [tree for tree in bpy.data.node_groups
            if tree.get('op_shader_graph') == ts_id and tree.get('op_unit')
            and tree.get(OWNER)]


def remove_orphan_tops(ts_id):
    """Delete top-level graphs of this texture set that no node references.

    The tag/user-count sweep mirrors ``remove_orphan_units``: it never reads
    a group node's ``node_tree`` pointer, so it cannot touch freed memory.
    """
    for tree in owned_tops(ts_id):
        if not tree.users:
            try:
                bpy.data.node_groups.remove(tree)
            except (ReferenceError, RuntimeError):
                pass


def _adoptable_top(ts_id, instance):
    """The single live top-level tree a material instance node can adopt.

    Never reads ``instance.node_tree`` (see ``owned_tops``): the tree is
    resolved by its owner tags over live datablocks, then checked for a
    one-to-one correspondence with the instance (exactly one signed,
    singly-referenced candidate, and an output interface that covers the
    channels the instance exposes). Anything ambiguous rebuilds instead of
    adopting.
    """
    candidates = [tree for tree in owned_tops(ts_id)
                  if tree.get('op_signature') and tree.users == 1
                  and instance.get('op_tree_identity')
                  and tree.get('op_tree_identity') == instance.get('op_tree_identity')]
    if len(candidates) != 1:
        # No graph, a copied material still sharing the original's tree
        # (users > 1), or several live graphs: compile a separate one
        # instead of putting two sessions on one graph.
        return None
    top = candidates[0]
    outputs = {item.name for item in top.interface.items_tree
               if item.item_type == 'SOCKET' and item.in_out == 'OUTPUT'}
    if any(channel not in outputs for channel in CHANNELS):
        return None
    try:
        names = {socket.name for socket in instance.outputs}
    except (ReferenceError, RuntimeError):
        return None
    if any(channel not in names for channel in CHANNELS):
        # The instance was wired for another tree: adopt nothing, rebuild.
        return None
    return top


def _adoptable_unit(ts_id, key_repr, instance):
    """The live unit tree for one top-tree instance node, resolved by tags.

    Never reads ``node.node_tree`` (see ``owned_tops``). Unit tags are minted
    per texture set, so exactly one referenced candidate may exist; anything
    else rebuilds the unit instead of adopting a stranger's tree.
    """
    candidates = [tree for tree in owned_units(ts_id)
                  if tree.get('op_unit') == key_repr and tree.users > 0
                  and instance.get('op_tree_identity')
                  and tree.get('op_tree_identity') == instance.get('op_tree_identity')]
    if len(candidates) != 1:
        return None
    return candidates[0]


UNKNOWN_UNIT = object()   # a binding registered without its unit: every unit counts as touched


def _repoint(value, old, new, seen):
    """``value`` with every captured RNA struct at address ``old`` replaced by ``new``. A
    function's closure cells and defaults are rewritten in place (nested functions too), lists
    and dict values changed in place, tuples rebuilt. Addresses are compared, never read through
    the stale struct."""
    import types
    if isinstance(value, bpy.types.bpy_struct):
        try:
            return new if value.as_pointer() == old else value
        except ReferenceError:
            return value
    if isinstance(value, tuple):
        items = tuple(_repoint(item, old, new, seen) for item in value)
        return items if any(a is not b for a, b in zip(items, value)) else value
    if isinstance(value, list):
        for index, item in enumerate(value):
            value[index] = _repoint(item, old, new, seen)
        return value
    if isinstance(value, dict):
        for name, item in list(value.items()):
            value[name] = _repoint(item, old, new, seen)
        return value
    if isinstance(value, types.FunctionType) and id(value) not in seen:
        seen.add(id(value))
        for cell in value.__closure__ or ():
            try:
                content = cell.cell_contents
            except ValueError:
                continue   # a cell not filled yet
            replaced = _repoint(content, old, new, seen)
            if replaced is not content:
                cell.cell_contents = replaced
        if value.__defaults__:
            value.__defaults__ = tuple(_repoint(item, old, new, seen) for item in value.__defaults__)
    return value


def _captures(value, old, seen, unfilled=False):
    """Whether ``value`` still captures an RNA struct at address ``old`` (``_repoint``'s reach).
    ``unfilled``: what a closure cell not filled yet answers (it may hold the struct later)."""
    import types
    if isinstance(value, bpy.types.bpy_struct):
        try:
            return value.as_pointer() == old
        except ReferenceError:
            return False
    if isinstance(value, (tuple, list)):
        return any(_captures(item, old, seen, unfilled) for item in value)
    if isinstance(value, dict):
        return any(_captures(item, old, seen, unfilled) for item in value.values())
    if isinstance(value, types.FunctionType) and id(value) not in seen:
        seen.add(id(value))
        for cell in value.__closure__ or ():
            try:
                content = cell.cell_contents
            except ValueError:
                if unfilled:
                    return True
                continue
            if _captures(content, old, seen, unfilled):
                return True
        return any(_captures(item, old, seen, unfilled) for item in value.__defaults__ or ())
    return False


def _plan_token(ts):
    """What a plan of the stack holds for: its structure (numeric edits carried) and length.
    None while a replay is open, whose edits move no serial until it ends."""
    from ..core.layer_stack import replaying
    if replaying():
        return None
    from .structure_memo import token
    return token(ts), len(ts.layers)


def path_index(ts, owners=None, cells=None):
    """RNA paths of a texture set's structs by address, exactly as ``path_from_id``
    spells them, in one pass over the set. ``owners``: filled with each layer's addresses."""
    base = ts.path_from_id()
    return {pointer: _spelled(base, entry) for pointer, entry in _struct_index(ts, owners, cells).items()}


def _struct_index(ts, owners=None, cells=None):
    """``path_index`` as (cell, suffix) per address: a layer's structs share a one-item list
    holding the layer's index (``_spelled``), so a layer that moved in the stack's array is
    indexed again by its cell and its own address. ``cells``: filled with each layer's cell."""
    index = {ts.as_pointer(): (None, ''), ts.bake.as_pointer(): (None, '.bake')}
    for position, entry in enumerate(ts.baked_maps):
        index[entry.as_pointer()] = (None, f'.baked_maps[{position}]')
    for position, layer in enumerate(ts.layers):
        cell = [position]
        found = _layer_entries(cell, layer)
        index.update(found)
        if owners is not None:
            owners[layer.id] = tuple(found)
        if cells is not None:
            cells[layer.id] = cell
    return index


def _spelled(base, entry):
    cell, suffix = entry
    return base + suffix if cell is None else f'{base}.layers[{cell[0]}]{suffix}'


def _layer_entries(cell, layer):
    """``_struct_index`` of one layer's structs; the layer's own address comes first."""
    return {struct.as_pointer(): (cell, suffix) for suffix, struct in _layer_structs(layer)}


def _layer_structs(layer):
    """A layer's structs with their paths within it, the layer itself ('') first."""
    yield '', layer
    for c, prop in enumerate(layer.channels):
        channel = f'.channels[{c}]'
        yield channel, prop
        for e, effect in enumerate(prop.effects):
            yield f'{channel}.effects[{e}]', effect
    for m, mask in enumerate(layer.masks):
        item = f'.masks[{m}]'
        yield item, mask
        for name in ('effect', 'influence', 'mesh'):
            yield f'{item}.{name}', getattr(mask, name)
    for r, row in enumerate(layer.cache):
        yield f'.cache[{r}]', row


def _addresses(moves):
    """``moves`` (old address -> the struct now at its path) as old -> new address. A struct gone
    since is left out: following it then fails (KeyError), and its unit is compiled again."""
    moved = {}
    for pointer, struct in moves.items():
        try:
            moved[pointer] = struct.as_pointer()
        except ReferenceError:
            pass
    return moved


def _repoint_many(value, moves, seen):
    """``_repoint`` for many structs at once: ``moves`` maps old addresses to new structs."""
    import types
    if isinstance(value, bpy.types.bpy_struct):
        try:
            return moves.get(value.as_pointer(), value)
        except ReferenceError:
            return value
    if isinstance(value, tuple):
        items = tuple(_repoint_many(item, moves, seen) for item in value)
        return items if any(a is not b for a, b in zip(items, value)) else value
    if isinstance(value, list):
        for index, item in enumerate(value):
            value[index] = _repoint_many(item, moves, seen)
        return value
    if isinstance(value, dict):
        for name, item in list(value.items()):
            value[name] = _repoint_many(item, moves, seen)
        return value
    if isinstance(value, types.FunctionType) and id(value) not in seen:
        seen.add(id(value))
        for cell in value.__closure__ or ():
            try:
                content = cell.cell_contents
            except ValueError:
                continue
            replaced = _repoint_many(content, moves, seen)
            if replaced is not content:
                cell.cell_contents = replaced
        if value.__defaults__:
            value.__defaults__ = tuple(_repoint_many(item, moves, seen) for item in value.__defaults__)
    return value


def _holds_foreign(value, fresh, seen):
    """Whether ``value`` captures an RNA struct other than the ``fresh`` wrappers (by id), or a
    closure cell not filled yet (it may hold one later)."""
    import types
    if isinstance(value, bpy.types.bpy_struct):
        return id(value) not in fresh
    if isinstance(value, (tuple, list)):
        return any(_holds_foreign(item, fresh, seen) for item in value)
    if isinstance(value, dict):
        return any(_holds_foreign(item, fresh, seen) for item in value.values())
    if isinstance(value, types.FunctionType) and id(value) not in seen:
        seen.add(id(value))
        for cell in value.__closure__ or ():
            try:
                content = cell.cell_contents
            except ValueError:
                return True
            if _holds_foreign(content, fresh, seen):
                return True
        return any(_holds_foreign(item, fresh, seen) for item in value.__defaults__ or ())
    return False


@dataclass
class Binding:
    owner: object
    field: str
    index: object
    socket: object
    value: float
    identifier: str
    aliases: tuple = ()   # further owners whose edits update it (a Mask Graph node's mask)
    native: tuple = ()    # a Mask Graph binding: (unit key, tree address, (owner address, owner RNA type))
    unit: object = UNKNOWN_UNIT   # key of the unit whose tree holds the socket (None: the top tree)

    def read(self):
        value = getattr(self.owner, self.field)
        return float(value if self.index is None else value[self.index])

    def pointers(self):
        return (self.owner.as_pointer(), *self.aliases)


@dataclass
class ResourceBinding:
    identifier: str
    owners: set
    identity: object
    factory: object
    node: object
    signature: object
    pending_image: object = None
    owner_factory: object = None
    layer_id: str = ''   # profile attribution of the CPU work (0.9.0)
    placeholder: object = None   # colour of an unpublished leaf; None for the neutral grey
    interactive: bool = False    # the leaf finishes a gesture: steps spend a larger budget on it
    stable: bool = False         # regenerate content into the bound datablock, never a new one
                                 # (undo-consistent: a stroke boundary keeps one leaf for its life,
                                 # so a memfile undo never frees a leaf the material still binds)
    stable_image: object = None  # the RESOURCE leaf this stable binding owns, so its content is
                                 # regenerated in place even while a gesture preview stands on the node


# Diagnostics: compare every incremental plan with a full one (``Graph._verify_plan``).
VERIFY_STRUCTURE = os.environ.get('OP_VERIFY_STRUCTURE') == '1'
# Diagnostics: compare every unit adopted after an undo with a fresh compile (``verify_undo``).
VERIFY_UNDO = os.environ.get('OP_VERIFY_UNDO') == '1'
# A fill channel's image detail (its projected luminance, ``Graph.detail``) is a unit of its own,
# a FIELD: rendered once into a full-float texture and kept while the channel's colour, opacity,
# blend or masks change, instead of sampled again in every pass of the channel. 0 compiles it into
# the channel, as before.
DETAIL_FIELDS = os.environ.get('OP_DETAIL_FIELDS', '1') == '1'


def _plain(value):
    """A comparable copy of a socket value or node attribute (an RNA struct by its address)."""
    if isinstance(value, (bool, int, float, str, type(None))):
        return value
    if isinstance(value, bpy.types.bpy_struct):
        try:
            return ('rna', value.as_pointer())
        except ReferenceError:
            return ('rna', 'removed')
    if isinstance(value, (tuple, list)):
        return tuple(_plain(item) for item in value)
    return type(value).__name__


# Headless node attributes holding what is not a node's own RNA: its tree and image (followed apart).
_NODE_REFERENCES = {'id_data', '_image'}

# Not compared: references, image bookkeeping and the name (a per-graph sequence number).
_UNSHAPED = {'id_data', 'inputs', 'outputs', '_props', '_image', '_image_key', '_image_assigned', '_tree',
             '_gpu_resource', 'name'}


def _unit_shape(unit):
    """What a unit compiled to, by position, for comparing an adopted unit with a fresh compile."""
    nodes = list(unit.tree.nodes)
    position, sockets, shape = {id(node): i for i, node in enumerate(nodes)}, {}, []
    for i, node in enumerate(nodes):
        for j, socket in enumerate(node.inputs):
            sockets[id(socket)] = (i, 'in', j)
        for j, socket in enumerate(node.outputs):
            sockets[id(socket)] = (i, 'out', j)
        shape.append((node.bl_idname,
                      tuple((s.identifier, s.type, None if s.links else _plain(s.default_value)) for s in node.inputs),
                      tuple((s.identifier, s.type, _plain(s.default_value)) for s in node.outputs),
                      tuple(sorted((k, _plain(v)) for k, v in node.__dict__.items() if k not in _UNSHAPED)),
                      tuple(sorted((k, _plain(v)) for k, v in node._props.items()))))
    links = sorted((sockets[id(link.from_socket)], sockets[id(link.to_socket)]) for link in unit.tree.links)
    bindings = sorted((repr(key), b.field, b.index, b.value, sockets.get(id(b.socket)), b.owner.as_pointer(), b.aliases)
                      for key, b in unit.bindings.items())
    images = [(entry[0], entry[1], position.get(id(entry[3])), entry[4]) for entry in unit.image_bindings]
    return dict(nodes=shape, links=links, bindings=bindings, images=images, exports=_plain(sorted(unit.exports.items())),
                imports=_plain(unit.imports), const_imports=_plain(unit.const_imports), flags=repr(unit.flags),
                owners=unit.owner_pointers, signature=repr(unit.signature), effects=sorted(unit.compiled_effects),
                interface=(sorted(unit.inputs.items()), sorted(unit.outputs.items())))


def _owner_token(owner):
    """A binding key's owner: a layer by its id (its struct moves when a layer is added or removed
    below it, and ``EngineGraph._patch`` moves that layer's bindings in one step), any other
    struct by address."""
    return ('layer', owner.id) if type(owner).__name__ == 'OP_Layer' else owner.as_pointer()


def _removed_graph_units(bindings):
    """Units with a Mask Graph binding whose node, socket, ramp stop or tree is gone. Reading
    it would dereference freed memory (no callback reaches us when a tree is deleted or
    stops are reallocated), so the unit is rebuilt instead; 'global' when a unit is unknown."""
    live, removed = {}, []
    for binding in bindings:
        if not binding.native:
            continue
        unit, tree, item = binding.native
        if tree not in live:
            from ..procedural_graph.program import live_items
            live[tree] = live_items(tree)
        if item not in live[tree]:
            if unit is None:
                return 'global'
            if unit not in removed:
                removed.append(unit)
    return removed


def _datablocks():
    """Session ids of the datablocks layer fields point at (images, Mask Graphs, decal
    objects, text sources)."""
    data = bpy.data
    return frozenset(item.session_uid for collection in (data.images, data.node_groups, data.objects, data.texts)
                     for item in collection)


def _reordered(before, after):
    """Layers added, removed, or whose order among the others changed, from ``before`` to
    ``after`` (layer ids in stack order); None when that is more than a quarter of the stack,
    for which planning every layer costs no more."""
    if before == after:
        return set()
    kept_before, kept_after = set(before), set(after)
    changed = (kept_before ^ kept_after)
    common_before = [identifier for identifier in before if identifier in kept_after]
    common_after = [identifier for identifier in after if identifier in kept_before]
    if common_before != common_after:
        differ = [k for k, (a, b) in enumerate(zip(common_before, common_after)) if a != b]
        changed.update(common_before[differ[0]:differ[-1] + 1])
        changed.update(common_after[differ[0]:differ[-1] + 1])
    return changed if len(changed) * 4 <= max(len(after), 1) else None


def _image_pointer(node):
    """The address of a node's image; None without one, or for one Blender removed."""
    image = node.image
    if image is None:
        return None
    try:
        return image.as_pointer()
    except ReferenceError:
        return None


def _layer_memo(memo, identifier):
    """A plan memo's answers about one layer (plans share those of layers no edit reached)."""
    found = memo.get(identifier)
    if found is None:
        found = memo[identifier] = {}
    return found


def _collection_print(layer):
    """Sizes of a layer's collections: they differ when one was added to or removed from.
    Read from the layer's stored properties (the same arrays RNA's collections are), which
    is cheaper than an RNA struct per channel; RNA when the layer stores none."""
    group = layer.bl_system_properties_get() if hasattr(layer, 'bl_system_properties_get') else None
    if group is None:
        return (len(layer.masks), len(layer.cache), tuple(len(prop.effects) for prop in layer.channels))
    return (len(group.get('masks') or ()), len(group.get('cache') or ()),
            tuple(len(channel.get('effects') or ()) for channel in group.get('channels') or ()))


def _layer_links(layer):
    """(what the layer reads, what it publishes), by the handles authoring names them with:
    anchor ids its channels and masks consume and ``('layer', id)`` for a reference to a
    layer's own stages; the anchor ids of its effects and masks and its composite anchors.
    A link holds whether or not it resolves right now (a disabled channel reads as a constant)."""
    from .anchors import consumed_channels, group_id, output_id
    reads, owns = set(), {output_id(layer.id), group_id(layer.id)}
    for prop in layer.channels:
        reads.update(identifier for identifier, _ in consumed_channels(prop))
        owns.update(effect.id for effect in prop.effects if effect.kind == 'ANCHOR' and effect.id)
    for mask in layer.masks:
        reads.update(identifier for identifier, _ in consumed_channels(mask))
        if mask.source == 'REFERENCE' and not mask.reference_anchor:
            # A reference with no target yet reads nothing, but it is reported (missing source).
            reads.add(('layer', mask.reference_layer))
        if mask.source == 'ANCHOR' and mask.id:
            owns.add(mask.id)
    return frozenset(reads), frozenset(owns)


def _link_target(handle, owners):
    """The layer a read handle names (``_layer_links``), or None."""
    from .anchors import GROUP_SUFFIX, OUTPUT_SUFFIX
    if isinstance(handle, tuple):
        return handle[1] or None
    for suffix in (OUTPUT_SUFFIX, GROUP_SUFFIX):
        if handle.endswith(suffix):
            return handle[:-len(suffix)]
    return owners.get(handle)


def _composite_handle(handle):
    from .anchors import GROUP_SUFFIX, OUTPUT_SUFFIX
    return isinstance(handle, str) and handle.endswith((OUTPUT_SUFFIX, GROUP_SUFFIX))


def _previous_strokes_leaf(n, key, channel, base_key, stamp):
    """The published strokes leaf of the same key, channel, canvas and geometry that
    shares the most strokes with what is requested now, with its recorded state."""
    from .shader_resources import RESOURCE
    best = None
    for image in bpy.data.images:
        if (not image.get(RESOURCE) or not image.get('op_shader_resource_ready') or tuple(image.size) != (n, n)
                or not image.has_data):
            continue   # a leaf an undo or the image cache regenerated blank is no base
        text = image.get('op_resource_state')
        if not text:
            continue
        try:
            state = json.loads(text)
        except ValueError:
            continue
        if (state.get('key') != key or state.get('channel') != channel or state.get('base') != repr(base_key)
                or state.get('geometry') != repr(stamp)):
            continue
        score = len(state.get('identity', []))
        if best is None or score > best[0]:
            best = (score, image, state)
    return None if best is None else (best[1], best[2])


def _dirty_rows(before, now, n):
    """Row ranges that differ between two strokes states: the rows of every stroke
    present in one and not the other, or present in both at another revision."""
    old = {identifier: revision for identifier, revision in before.get('identity', [])}
    new = {identifier: revision for identifier, revision in now.get('identity', [])}
    changed = {identifier for identifier in set(old) | set(new) if old.get(identifier) != new.get(identifier)}
    bands = []
    for identifier in changed:
        for rows in (before.get('rows', {}).get(identifier), now.get('rows', {}).get(identifier)):
            if rows and rows[1] > rows[0]:
                bands.append((max(0, int(rows[0])), min(n, int(rows[1]))))
    bands.sort()
    merged = []
    for low, high in bands:
        if merged and low <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], high))
        else:
            merged.append((low, high))
    return merged


def _descriptor_layer(descriptor):
    """The layer id a boundary descriptor belongs to, for per-layer profiling."""
    if not descriptor:
        return ''
    site, *args = descriptor
    if site == 'field':
        owner_key = args[0] if args else None
        return owner_key[1] if isinstance(owner_key, (list, tuple)) and len(owner_key) > 1 else ''
    return args[0] if args else ''


@dataclass
class CacheBinding:
    """A cached layer's channel sampler (0.9.0): the content identity it must match."""
    layer_id: str
    channel: str
    identifier: str
    owners: set
    identity: object
    node: object
    signature: object
    owner_factory: object = None
    stale: bool = False


@dataclass
class Unit:
    """One compiled node group: its interface, bindings and dependency roles."""
    key: tuple
    tree: object = None
    group_input: object = None
    group_output: object = None
    instance: object = None
    signature: object = None
    inputs: dict = field(default_factory=dict)      # interface input name -> socket type
    outputs: dict = field(default_factory=dict)     # interface output name -> socket type
    imports: list = field(default_factory=list)     # (producer key, export name, input name)
    const_imports: list = field(default_factory=list)   # (producer key, export name, baked value)
    exports: dict = field(default_factory=dict)     # export name -> ('socket', name) | ('const', value)
    flags: dict = field(default_factory=dict)
    bindings: dict = field(default_factory=dict)
    resource_bindings: list = field(default_factory=list)
    image_bindings: list = field(default_factory=list)
    cache_bindings: list = field(default_factory=list)
    compiled_effects: dict = field(default_factory=dict)
    input_consumers: dict = field(default_factory=dict)   # input name -> roles reading it
    output_producer: dict = field(default_factory=dict)   # output name -> role or ('input', name)
    owner_pointers: tuple = ()                            # RNA addresses captured by this unit
    build_ms: float = 0.0
    warnings: list = field(default_factory=list)          # what compiling it reported (a stub's error)
    holds_layer: bool = True   # a binding or closure of it refers to its layer's struct (``_follow_moved_layer``)
    captures_layer: bool = True   # a resource, cache or image closure may hold its layer's struct (``_patch``)
    rna_nodes: tuple = ()   # headless nodes whose attributes hold RNA (a filter's effect, a decal object)

    def input(self, name, socket_type):
        if name not in self.inputs:
            self.tree.interface.new_socket(name=name, in_out='INPUT', socket_type=socket_type)
            self.inputs[name] = socket_type
        return self.group_input.outputs[name]

    def imported(self, producer_key, export_name):
        for key, name, input_name in self.imports:
            if key == producer_key and name == export_name:
                return self.group_input.outputs[input_name]
        return None

    @property
    def node_count(self):
        return len(self.tree.nodes) if self.tree else 0


class Graph(Math):
    # Nodes and unit trees carry JSON descriptors (owners by stable ids) that ``rehydrate``
    # reads to rebuild a session from the trees a material persisted; the engine's headless
    # trees are never rehydrated, so it writes none.
    DESCRIPTORS = True

    def __init__(self, scene, ts, material):
        self.scene, self._ts, self.material = scene, ts, material
        self.n = int(ts.resolution)
        self.tree = None
        self.instance = None
        self.unit = None
        self.units = {}
        self.outputs = {}
        self.bindings = {}
        self.owner_bindings = defaultdict(list)
        # Units whose uniform sockets update() wrote since the evaluator last looked
        # (take_touched); None when an edit's unit is unknown and every unit counts.
        self.touched = set()
        self._paths = None   # struct address -> RNA path, valid for one build (_path)
        self._undo_moves = self._undo_images = self._undo_fresh = None   # during a build after an undo (_follow_undo)
        self._undo_moved = None   # _undo_moves' new structs' addresses
        self._global = None   # global_signature of the last build, read after an undo
        self.resource_bindings = []
        self.image_bindings = []
        self.cache_bindings = []
        self.resources = Resources(self.n)
        self.reverse = defaultdict(set)
        self.channels = {}
        self._role = 'coordinates'
        self._compiled_effects = {}
        self._building = set()
        self._building_units = set()
        self._structure = None
        self._structure_plan = None   # the plan ``_structure`` was made from
        self._dirty_layers = set()    # layers structurally edited since; None: any of them
        self._last = {}
        self._anchors = None
        self._plan = None
        self._previous = None
        self._reused, self._warnings = [], []
        self._stubs = False
        self._adopted = []
        # Graphs of one session share this cell; it names the newest of them.
        self._head = [self]

    @property
    def _live(self):
        """The newest graph of the session (0.9.7).

        Boundary and cache closures are bound methods of the graph that compiled
        their unit. Adopted units carry them into every later graph, while the
        graph they belong to stops re-planning once it is superseded, so the
        authoring state they read comes from the live graph instead.
        """
        return self._head[0]

    @property
    def ts(self):
        live = self._live
        return self._ts if live is self else live._ts

    @ts.setter
    def ts(self, value):
        self._ts = value

    @property
    def anchors(self):
        # Indexed once per planning pass: anchor placement is structural, so
        # every structural check re-plans the live graph, and adopted units'
        # closures read the index refreshed by the latest plan.
        live = self._live
        if live is not self:
            return live.anchors
        if self._anchors is None:
            from .anchors import index
            self._anchors = index(self.ts)
        return self._anchors

    @contextmanager
    def role(self, identifier):
        previous, self._role = self._role, identifier
        try:
            yield
        finally:
            self._role = previous

    @contextmanager
    def enter(self, unit):
        previous, self.unit = self.unit, unit
        role, self._role = self._role, 'UnitInterface'
        try:
            yield unit
        finally:
            self.unit, self._role = previous, role

    def _target(self):
        return self.unit.tree if self.unit is not None else self.tree

    def node(self, kind):
        tree = self._target()
        node = tree.nodes.new(kind)
        # Free Blender's default type name immediately. Repeatedly allocating
        # thousands of "Math.001" siblings spends seconds searching suffixes.
        sequence = getattr(self, '_node_sequence', 0) + 1
        self._node_sequence = sequence
        node.name = f'OP_{sequence:06d}'
        node[OWNER] = True
        node['op_dependency'] = self._role
        node.label = self._role
        return node

    def link(self, value, target):
        if is_socket(value):
            tree = self._target()
            if value.id_data != tree or target.id_data != tree:
                raise RuntimeError('Shader compiler linked across node trees in ' + self._role)
            tree.links.new(value, target)
            return target
        return super().link(value, target)

    # Coordinates come from the top-level tree; a unit reads them through its
    # own interface so the same nodes serve every instance.
    def coord_uv(self):
        return self.unit.input('UV', 'NodeSocketVector') if self.unit is not None else self.uv

    def coord_position(self):
        return (self.unit.input('Position', 'NodeSocketVector') if self.unit is not None
                else self.geometry.outputs['Position'])

    def coord_normal(self):
        return (self.unit.input('Normal', 'NodeSocketVector') if self.unit is not None
                else self.geometry.outputs['Normal'])

    # ---- persistent descriptors -----------------------------------------
    # Nodes carry JSON descriptors keyed by stable ids so a session can be
    # rebuilt from the persisted trees after undo/redo without recompiling.

    _PATH = re.compile(r'(\w+)(?:\[(\d+)\])?')

    def _path(self, owner):
        """``owner.path_from_id()``, from an index of this set's structs built once per build.

        RNA finds a collection item's path by searching the collection, so asking for
        every bound parameter made compiling a stack quadratic in its size. Structs
        outside the index (node trees, text settings) ask RNA as before.
        """
        paths = self._paths
        if paths is None:
            self._path_owners, self._path_cells, self._path_base = {}, {}, self.ts.path_from_id()
            paths = self._paths = _struct_index(self.ts, self._path_owners, self._path_cells)
        entry = paths.get(owner.as_pointer())
        if entry is not None:
            return _spelled(self._path_base, entry)
        from ..core.layer_store import scene_path
        return scene_path(owner)

    def undo_index(self):
        """Each struct address ``_path`` indexed, as (layer id, path within the layer), or
        (None, path within the set) for the set's own structs. Plain data, so it is read after
        a memfile undo freed the structs (``build(undo=...)``); None without an index."""
        paths, owners = self._paths, getattr(self, '_path_owners', None)
        if paths is None or owners is None:
            return None
        layer_of = {pointer: identifier for identifier, pointers in owners.items() for pointer in pointers}
        return {pointer: (layer_of.get(pointer), entry[1]) for pointer, entry in paths.items()
                if entry[0] is None or pointer in layer_of}

    def _undo_structs(self, index):
        """``undo_index`` resolved in the restored set: old address -> the struct now at its path."""
        ts = self.ts
        own = {'': ts, '.bake': ts.bake, **{f'.baked_maps[{i}]': m for i, m in enumerate(ts.baked_maps)}}
        layers, found, moves = self._plan.layers, {}, {}
        for pointer, (identifier, suffix) in index.items():
            if identifier is None:
                struct = own.get(suffix)
            else:
                structs = found.get(identifier)
                if structs is None:
                    layer = layers.get(identifier)
                    structs = found[identifier] = dict(_layer_structs(layer)) if layer is not None else {}
                struct = structs.get(suffix)
            if struct is not None:
                moves[pointer] = struct
        return moves

    def _refresh_paths(self, plan, edited, moved=()):
        """Index again the structs of the ``edited`` layers only: a patch leaves every other
        layer's collections where they were (``_path``). A layer that only ``moved`` in the
        stack's array keeps its entries: its index cell and its own address follow it."""
        paths, owners = self._paths, getattr(self, '_path_owners', None)
        cells = getattr(self, '_path_cells', None)
        if paths is None or owners is None or cells is None or edited is None:
            self._paths = self._path_owners = self._path_cells = None
            return
        moved = set(moved) - set(edited)
        for identifier in [identifier for identifier in moved if identifier not in owners]:
            moved.discard(identifier)
            edited = {*edited, identifier}
        for identifier in edited:
            for pointer in owners.pop(identifier, ()):
                paths.pop(pointer, None)
            cells.pop(identifier, None)
        self._path_base = self.ts.path_from_id()
        positions = {identifier: index for index, identifier in enumerate(plan.ids)}
        for identifier in [identifier for identifier in cells if identifier not in positions]:
            for pointer in owners.pop(identifier, ()):
                paths.pop(pointer, None)
            del cells[identifier]
        for identifier, cell in cells.items():
            cell[0] = positions[identifier]
        # Every old address goes before any new one is written: a layer moved one slot up
        # takes the address the layer above it had.
        shifted = [identifier for identifier in moved if owners[identifier][0] != plan.pointers[identifier]]
        for identifier in shifted:
            paths.pop(owners[identifier][0], None)
        for identifier in shifted:
            now = plan.pointers[identifier]
            paths[now] = (cells[identifier], '')
            owners[identifier] = (now, *owners[identifier][1:])
        for identifier in edited:
            layer = plan.layers.get(identifier)
            if layer is None:
                continue
            cell = cells[identifier] = [positions[identifier]]
            found = _layer_entries(cell, layer)
            paths.update(found)
            owners[identifier] = tuple(found)

    def _owner_key(self, owner):
        """Stable identity of an RNA item: ids rather than collection indices."""
        if owner == self.ts:
            return ['ts']
        path = self._path(owner)
        tokens = self._PATH.findall(path)
        key, cursor = [], None
        for name, index in tokens:
            if name in {'open_painter', 'texture_sets'}:
                continue
            if name == 'layers':
                cursor = self.ts.layers[int(index)]
                key = ['layer', cursor.id]
            elif name == 'channels':
                cursor = cursor.channels[int(index)]
                key = ['channel', key[1], cursor.channel]
            elif name == 'effects':
                key = ['effect', *key[1:], int(index)]
            elif name == 'masks':
                cursor = cursor.masks[int(index)]
                key = ['mask', key[1], cursor.id]
            elif name == 'effect':
                key = ['mask_effect', *key[1:]]
            elif name == 'influence':
                key = ['influence', *key[1:]]
            elif name == 'mesh':
                key = ['mesh', *key[1:]]
            elif name == 'cache':
                cursor = cursor.cache[int(index)]
                key = ['cache', key[1], cursor.channel]
            elif name == 'baked_maps':
                key = ['baked', self.ts.baked_maps[int(index)].kind]
            elif name == 'bake':
                key = ['bake']
            else:
                raise ValueError('Unsupported shader owner path: ' + path)
        return key

    def _resolve(self, key):
        """RNA item for a descriptor key, or None when it no longer exists."""
        kind = key[0]
        if kind == 'ts':
            return self.ts
        if kind == 'bake':
            return self.ts.bake
        if kind == 'baked':
            return next((m for m in self.ts.baked_maps if m.kind == key[1] and m.image), None)
        layer = self._layer(key[1])
        if layer is None:
            return None
        if kind == 'layer':
            return layer
        if kind == 'cache':
            return self._cache_row(layer, key[2])
        if kind in {'channel', 'effect'}:
            prop = channel_for(layer, key[2])
            if prop is None:
                return None
            if kind == 'channel':
                return prop
            effects = list(prop.effects)
            return effects[key[3]] if key[3] < len(effects) else None
        mask = next((m for m in layer.masks if m.id == key[2]), None)
        if mask is None:
            return None
        return {'mask': mask, 'mask_effect': mask.effect, 'influence': mask.influence, 'mesh': mask.mesh}.get(kind)

    def param(self, owner, field, index=None):
        key = (self.unit.key if self.unit is not None else None, _owner_token(owner), field, index)
        if key not in self.bindings:
            identifier = 'Parameter:' + self._path(owner) + '.' + field
            if index is not None:
                identifier += f'[{index}]'
            with self.role(identifier):
                node = self.node('ShaderNodeValue')
            if self.DESCRIPTORS:
                node['op_binding'] = json.dumps([self._owner_key(owner), field, index])
            raw = getattr(owner, field)
            value = float(raw if index is None else raw[index])
            node.outputs[0].default_value = value
            binding = Binding(owner, field, index, node.outputs[0], value, identifier, unit=key[0])
            self.bindings[key] = binding
            self.owner_bindings[owner.as_pointer()].append(binding)
            if self.unit is not None:
                self.unit.bindings[key] = binding
        return self.bindings[key].socket

    def color_param(self, owner, field):
        return self.combine(*(self.param(owner, field, i) for i in range(3)))

    @contextmanager
    def native(self, mask):
        """Compile ``mask``'s Mask Graph: its node bindings also answer to the mask, whose
        callbacks are what a node edit notifies (``procedural_graph.edits``)."""
        previous, self._native_mask = getattr(self, '_native_mask', None), mask
        try:
            yield
        finally:
            self._native_mask = previous

    def _bind(self, key, binding):
        binding.unit = key[0]
        mask = getattr(self, '_native_mask', None)
        if mask is not None:
            binding.aliases = (mask.as_pointer(),)
            owner = binding.owner
            binding.native = (self.unit.key if self.unit is not None else None, mask.node_group.as_pointer(),
                              (owner.as_pointer(), owner.bl_rna.identifier))
        self.bindings[key] = binding
        for pointer in binding.pointers():
            self.owner_bindings[pointer].append(binding)
        if self.unit is not None:
            self.unit.bindings[key] = binding

    def native_param(self, owner, field, index=None, tree=''):
        """A uniform bound to a Mask Graph node, socket or ramp stop (no stack path)."""
        key = (self.unit.key if self.unit is not None else None, owner.as_pointer(), field, index)
        if key not in self.bindings:
            try:
                from ..core.layer_store import scene_path
                path = scene_path(owner)
            except ValueError:
                path = f'item@{owner.as_pointer():x}'   # a ramp stop: RNA builds no path inside a custom node
            identifier = f'Parameter:{tree}:{path}.{field}' + (f'[{index}]' if index is not None else '')
            with self.role(identifier):
                node = self.node('ShaderNodeValue')
            raw = getattr(owner, field)
            value = float(raw if index is None else raw[index])
            node.outputs[0].default_value = value
            self._bind(key, Binding(owner, field, index, node.outputs[0], value, identifier))
        return self.bindings[key].socket

    def scalar_mix(self, a, b, t):
        if not is_socket(a) and not is_socket(b) and a == b:
            # a + (a - a) * t is exactly a; keep the constant so an opaque
            # backdrop stays a compile-time constant after a passthrough folder.
            return a
        return self.math('ADD', a, self.math('MULTIPLY', self.math('SUBTRACT', b, a), t))

    # ---- unit interface -------------------------------------------------

    def _export(self, name, value, socket_type):
        unit = self.unit
        if is_socket(value):
            unit.tree.interface.new_socket(name=name, in_out='OUTPUT', socket_type=socket_type)
            unit.outputs[name] = socket_type
            self.link(value, unit.group_output.inputs[name])
            unit.exports[name] = ('socket', name)
        else:
            unit.exports[name] = ('const', value)

    def _import(self, producer, export_name, socket_type):
        kind, payload = producer.exports[export_name]
        if kind == 'const':
            # A baked literal is part of the adoption contract too: the unit must
            # be recompiled when the producer starts exporting a socket instead.
            if self.unit is not None:
                self.unit.const_imports.append((producer.key, export_name, payload))
            return payload
        unit = self.unit
        if unit is None:
            raise RuntimeError('Published shader stages can only be read inside a unit')
        existing = unit.imported(producer.key, export_name)
        if existing is not None:
            return existing
        input_name = f'Ref{len(unit.imports)}'
        unit.input(input_name, socket_type)
        unit.imports.append((producer.key, export_name, input_name))
        return unit.group_input.outputs[input_name]

    def _import_signal(self, producer):
        return Signal(self._import(producer, 'Color', 'NodeSocketColor'),
                      self._import(producer, 'Alpha', 'NodeSocketFloat'))

    def _import_composite(self, producer, export):
        """A CHANNEL unit's blended ``Out`` or child ``Group``; alpha is 1 in opaque contexts."""
        alpha = export + ':Alpha'
        return Signal(self._import(producer, export, 'NodeSocketColor'),
                      self._import(producer, alpha, 'NodeSocketFloat') if alpha in producer.exports else 1.0)

    def _import_mask(self, producer, public=True):
        value = self._import(producer, 'Value', 'NodeSocketFloat')
        if producer.flags.get('empty'):
            return 1.0 if public else None
        return value


    def smooth(self, low, high, value):
        x = self.clamp(self.math('DIVIDE', self.math('SUBTRACT', value, low),
                               self.math('MAXIMUM', self.math('SUBTRACT', high, low), 1e-6)))
        return self.math('MULTIPLY', self.math('MULTIPLY', x, x),
                         self.math('SUBTRACT', 3, self.math('MULTIPLY', 2, x)))

    def _coverage(self, coordinate):
        x, y, _ = self.separate(coordinate)
        value = 1.0
        for c in (x, y):
            value = self.math('MULTIPLY', value, self.math('SUBTRACT', 1, self.math('LESS_THAN', c, 0)))
            value = self.math('MULTIPLY', value, self.math('SUBTRACT', 1, self.math('GREATER_THAN', c, 1)))
        return value

    def coordinates(self, owner, pair=None):
        x, y = pair if pair is not None else self.separate(self.coord_uv())[:2]
        center = .5 if pair is None else 0.0
        x = self.math('MULTIPLY', self.math('SUBTRACT', x, center), self.param(owner, 'scale', 0))
        y = self.math('MULTIPLY', self.math('SUBTRACT', y, center), self.param(owner, 'scale', 1))
        angle = self.param(owner, 'rotation')
        c, s = self.math('COSINE', angle), self.math('SINE', angle)
        u = self.math('SUBTRACT', self.math('MULTIPLY', c, x), self.math('MULTIPLY', s, y))
        v = self.math('ADD', self.math('MULTIPLY', s, x), self.math('MULTIPLY', c, y))
        return self.combine(self.math('ADD', self.math('ADD', u, center), self.param(owner, 'offset', 0)),
                            self.math('ADD', self.math('ADD', v, center), self.param(owner, 'offset', 1)), 0)

    def image(self, image, coordinate=None, filtering='BILINEAR', tiling='REPEAT', getter=None, owner=None,
              attribute='image'):
        if image is None:
            raise ValueError('A shader source image is missing')
        node = self.node('ShaderNodeTexImage')
        node.image = image
        node.interpolation = 'Closest' if filtering == 'NEAREST' else 'Linear'
        node.extension = 'REPEAT' if tiling == 'REPEAT' else 'EXTEND'
        coord = coordinate if coordinate is not None else self.coord_uv()
        self.link(coord, node.inputs['Vector'])
        if getter:
            if self.DESCRIPTORS:
                node['op_image'] = json.dumps([self._owner_key(owner), attribute])
            entry = (owner.as_pointer(), self._role, getter, node, image_key(image))
            self.image_bindings.append(entry)
            if self.unit is not None:
                self.unit.image_bindings.append(entry)
        result = Signal(node.outputs['Color'], node.outputs['Alpha'])
        if tiling == 'NONE':
            coverage = self._coverage(coord)
            result = Signal(self.vec('SCALE', result.color, coverage),
                            self.math('MULTIPLY', result.alpha, coverage))
        return result

    def projected(self, owner, sampler):
        if owner.projection != 'TRIPLANAR':
            return sampler(self.coordinates(owner))
        position = self.separate(self.coord_position())
        normal = self.separate(self.coord_normal())
        weights = [self.math('ABSOLUTE', v) for v in normal]
        maximum = self.math('MAXIMUM', self.math('MAXIMUM', weights[0], weights[1]), weights[2])
        hardness = self.param(owner, 'triplanar_hardness')
        weights = [self.math('POWER', self.math('DIVIDE', w, self.math('MAXIMUM', maximum, 1e-8)), hardness)
                   for w in weights]
        total = self.math('MAXIMUM', self.math('ADD', self.math('ADD', weights[0], weights[1]), weights[2]), 1e-8)
        color, alpha = (0., 0., 0.), 0.0
        for axis, (a, b) in enumerate(((1, 2), (0, 2), (0, 1))):
            value = sampler(self.coordinates(owner, (position[a], position[b])))
            weight = self.math('DIVIDE', weights[axis], total)
            color = self.vec('ADD', color, self.vec('SCALE', value.color, weight))
            alpha = self.math('ADD', alpha, self.math('MULTIPLY', value.alpha, weight))
        return Signal(color, alpha)

    def effect_image(self, effect):
        """A Normal Combine detail map sampled as stored, like the CPU's raw read.

        An artist's image arrives sRGB-tagged (image.open's default for a PNG),
        and the Image Texture node decodes it. Retagging the datablock would
        also turn a Base Color fill of the same image raw, so the decode is
        undone here per sample instead; a Non-Color or float map is data already.
        """
        image = effect.image
        color = self.image(image, getter=lambda: effect.image, owner=effect).color
        return srgb_encode(self, color) if stored_encoding(image) == 'SRGB' else color

    def mesh_map(self, kind):
        prop = next((m for m in self.ts.baked_maps if m.kind == kind and m.image), None)
        if prop is None:
            raise ValueError(f'This mask requires a baked {kind.title()} map. Bake mesh maps first.')
        return self.image(prop.image, getter=lambda: prop.image, owner=prop)

    def boundary(self, identifier, owners, identity, factory, descriptor=None, placeholder=None, fallback=None,
                 interactive=False, stable=False):
        """Expose one unsupported source/prefix as a stable shader image input.

        ``owners`` lists the RNA items whose edits re-identify this input, or a
        callable returning them so an adopted unit can refresh their addresses.
        ``descriptor`` names the site and its owners so the binding can be
        rebuilt from the persisted node after undo. ``placeholder`` is the colour
        an unpublished leaf shows; ``fallback`` an existing image the node samples
        instead until the first leaf publishes (a paint canvas under its strokes).
        """
        signature = identity()
        layer_id = _descriptor_layer(descriptor)
        image = self._request_leaf(signature, factory, layer_id, placeholder, interactive)
        node = self.node('ShaderNodeTexImage')
        pending = None
        if image is None:
            # Only reachable if a build ran while new leaves are frozen (post-undo); the build
            # guard in shader_preview.ensure keeps that from happening. Bind the fallback if any
            # and leave the binding un-signed so it is requested once the freeze lifts.
            node.image, pending = fallback, None
            signature = None
        elif fallback is not None and not image.get('op_shader_resource_ready'):
            node.image, pending = fallback, image
        else:
            node.image = image
        node.interpolation, node.extension = 'Linear', 'EXTEND'
        self.link(self.coord_uv(), node.inputs['Vector'])
        if descriptor is not None:
            node['op_boundary'] = json.dumps(descriptor)
        owner_factory = owners if callable(owners) else None
        items = owners() if callable(owners) else owners
        binding = ResourceBinding(identifier, {o.as_pointer() for o in items}, identity, factory,
                                  node, signature, owner_factory=owner_factory, layer_id=layer_id,
                                  placeholder=placeholder, pending_image=pending, interactive=interactive, stable=stable)
        if stable and image is not None and image.get(RESOURCE):
            binding.stable_image = image
        self.resource_bindings.append(binding)
        if self.unit is not None:
            self.unit.resource_bindings.append(binding)
        return Signal(node.outputs['Color'], node.outputs['Alpha'])

    def _request_leaf(self, signature, factory, layer_id, placeholder, interactive=False, reuse=None, stable=None):
        if placeholder is None:
            return self.resources.request(signature, lambda: factory(signature), owner=layer_id, interactive=interactive,
                                          reuse=reuse, stable=stable)
        return self.resources.request(signature, lambda: factory(signature), owner=layer_id, placeholder=placeholder,
                                      interactive=interactive, reuse=reuse, stable=stable)

    def _rebind(self, node, owners, identity, factory, placeholder=None, interactive=False, stable=False):
        """Reattach a persisted boundary node to fresh closures (undo rehydration)."""
        signature = identity()
        owner_factory = owners if callable(owners) else None
        items = owners() if callable(owners) else owners
        layer_id = _descriptor_layer(json.loads(node['op_boundary']) if node.get('op_boundary') else None)
        binding = ResourceBinding(node.get('op_dependency'), {o.as_pointer() for o in items}, identity,
                                  factory, node, signature, owner_factory=owner_factory, layer_id=layer_id,
                                  placeholder=placeholder, interactive=interactive, stable=stable)
        image = node.image
        reuse = None
        if (image is not None and image.get(RESOURCE) and image.get('op_shader_resource_ready')
                and not image.has_data and image.packed_file is None):
            # A reloaded leaf reads its packed pixels on first use; one saved
            # without them comes back as its neutral fill: never adopt it again.
            image['op_shader_resource_ready'] = False
        if (image is not None and image.get(RESOURCE) and not image.get('op_shader_resource_ready')
                and image.packed_file is None):
            # The datablock the node binds lost its pixels (an undo, a reload): the fresh
            # source is produced into it rather than into a replacement, so the node keeps
            # its image and nothing is removed under a material that may still sample it.
            reuse = image
        # The persisted image is adopted through the cache-hit path so it lives in
        # this graph's entries: an image no table knows is never evicted once a
        # later re-identification replaces it.
        fresh = self._request_leaf(signature, factory, layer_id, placeholder, interactive, reuse=reuse)
        if fresh is None:
            # Frozen after undo and no datablock to reuse: keep whatever the node binds (its
            # retired leaf, or nothing) and leave the binding without a signature so the next
            # authored gesture — which releases the freeze — requests the source afresh. No new
            # datablock is created into the persisted tree in this between-undo-steps window.
            binding.pending_image, binding.signature = None, None
        elif fresh.get('op_shader_resource_ready'):
            node.image = fresh
        else:
            binding.pending_image = fresh
        if stable and fresh is not None and fresh.get(RESOURCE):
            binding.stable_image = fresh
        if fresh is not None and image is not None and image != fresh and image.get(RESOURCE):
            self.resources.adopt(image)   # superseded: the table, not bpy.data, keeps it
        self.resource_bindings.append(binding)
        if self.unit is not None:
            self.unit.resource_bindings.append(binding)
        return binding

    @staticmethod
    def _parts(data, rows=16):
        for low in range(0, data.shape[0], rows):
            yield low, data[low:low + rows]

    def _geometry_stamp(self, needed):
        if not needed:
            return None
        from .dependencies import geometry_key
        stamp = geometry_key(self.scene, self.ts)
        # Resolution, time and the assigned objects: what a raster of the mesh reads.
        # Scene-field influencers, mesh-mask selectors and decal frames have their
        # own identities and must not evict those leaves. ``time`` is None unless the
        # set is animated (dependencies.animated), so scrubbing a static prop keeps
        # every stamped identity, and the CPU boundaries behind them, current.
        return stamp[:3] if stamp is not None else None

    def _field(self, owner, kind=None, generator=False, rehydrate=None):
        """Preserve custom integer-hash noise as a separately cached scalar."""
        kind = kind or owner.procedural
        from . import material_space
        if generator:
            identity = lambda: ('legacy-generator-field', kind, owner.source == 'NOISE', owner.noise_scale, owner.seed)
        else:
            identity = lambda: ('legacy-channel-field', material_space.identity(owner), kind, owner.procedural_scale, owner.procedural_seed,
                owner.projection, tuple(owner.scale), owner.rotation, tuple(owner.offset), owner.triplanar_hardness,
                self._geometry_stamp(owner.projection == 'TRIPLANAR' or material_space.enabled(owner)))

        def factory(signature):
            if generator:
                _, name, old_noise, scale, seed = signature
                def parts():
                    from ..texture_engine.procedurals import evaluate
                    from ..generators.noise import noise
                    u = (np.arange(self.n, dtype=np.float32)[None, :] + .5) / self.n
                    for low in range(0, self.n, 16):
                        v = (np.arange(low, min(low + 16, self.n), dtype=np.float32)[:, None] + .5) / self.n
                        yield low, (noise(u, v, scale, seed) if old_noise else evaluate(name, u, v, scale=scale, seed=seed))
                return parts()
            from .snapshots import capture
            snapshot = capture(owner)
            def parts():
                from .channel_eval import _procedural_tile
                resources = {}
                for low in range(0, self.n, 16):
                    yield low, _procedural_tile(snapshot, self.n, low, min(low + 16, self.n), self.ts, resources)
            return parts()
        if rehydrate is not None:
            return self._rebind(rehydrate, [owner], identity, factory)
        with self.role('SourceField:' + self._path(owner) + ':' + kind):
            return self.separate(self.boundary(self._role, [owner], identity, factory,
                                               ['field', self._owner_key(owner), kind, bool(generator)]).color)[0]

    ANALYTIC = {'SOLID', 'GRADIENT', 'LINEAR_GRADIENT', 'RADIAL_GRADIENT',
                'CHECKER', 'STRIPES', 'DOTS', 'PATTERN', 'GRID', 'TILE_PATTERN', 'BRICK_PATTERN'}

    def analytic(self, kind, coordinate, scale):
        u, v, _ = self.separate(coordinate)
        x, y = self.math('MULTIPLY', u, scale), self.math('MULTIPLY', v, scale)
        fx, fy = self.math('FRACT', x), self.math('FRACT', y)
        if kind == 'SOLID':
            return 1.0
        if kind in {'GRADIENT', 'LINEAR_GRADIENT'}:
            return fx
        if kind == 'CHECKER':
            return self.math('PINGPONG', self.math('ADD', self.math('FLOOR', x), self.math('FLOOR', y)), 1)
        if kind == 'STRIPES':
            return self.math('LESS_THAN', fx, .5)
        if kind == 'RADIAL_GRADIENT':
            length = self.vec('DISTANCE', coordinate, (.5, .5, 0))
            return self.clamp(self.math('SUBTRACT', 1, self.math('MULTIPLY', length, scale)))
        if kind in {'DOTS', 'PATTERN'}:
            distance = self.vec('DISTANCE', self.combine(fx, fy, 0), (.5, .5, 0))
            return self.math('SUBTRACT', 1, self.smooth(.26, .3, distance))
        if kind == 'BRICK_PATTERN':
            fx = self.math('FRACT', self.math('ADD', x,
                self.math('MULTIPLY', self.math('PINGPONG', self.math('FLOOR', y), 1), .5)))
        bx = self.math('MINIMUM', fx, self.math('SUBTRACT', 1, fx))
        by = self.math('MINIMUM', fy, self.math('SUBTRACT', 1, fy))
        value = self.smooth(.02, .04, self.math('MINIMUM', bx, by))
        return self.math('SUBTRACT', 1, value) if kind == 'GRID' else value

    @staticmethod
    def _field_key(layer, prop, channel):
        """Unit key of a channel's triplanar image detail (``DETAIL_FIELDS``), or None where
        ``_channel_base`` compiles the detail itself: another projection (one sample, cheaper
        than a pass of its own), paint and folder layers, Normal (a source boundary), Material
        Mapping v1 (a CPU boundary) and procedurals."""
        if (not DETAIL_FIELDS or prop is None or layer.type != 'FILL' or not prop.detail_image
                or prop.projection != 'TRIPLANAR' or channel == 'NORMAL'
                or getattr(prop, 'material_mapping_version', 0) == 1):
            return None
        return ('FIELD', layer.id, channel)

    def _detail_value(self, layer, prop, channel):
        """The detail a field channel modulates: its FIELD unit's value, or compiled in place."""
        key = self._field_key(layer, prop, channel)
        if key is None or self.unit is None or self.unit.key == key:
            return self.detail(prop)
        return self._import(self.compile_unit(key), 'Value', 'NodeSocketFloat')

    def detail(self, prop):
        if prop.detail_image:
            result = self.projected(prop, lambda uv: self.image(prop.detail_image, uv,
                prop.filtering, prop.tiling, getter=lambda: prop.detail_image, owner=prop, attribute='detail_image'))
            return luminance(self, result.color)
        from ..texture_engine.procedurals import ALIASES
        kind = ALIASES.get(prop.procedural, prop.procedural)
        if kind in self.ANALYTIC and not getattr(prop, 'material_mapping_version', 0):
            scale = self.param(prop, 'procedural_scale')
            result = self.projected(prop, lambda uv: Signal(self.analytic(kind, uv, scale)))
            return self.separate(result.color)[0]
        return self._field(prop, kind)

    def _content_stamp(self, layer, prop, effects):
        """Inputs the CPU raster of a channel reads beyond the channel's own values:
        the layer's vector strokes, the decal Empty's frame, and the mesh (a
        projected source, or a Surface-space filter padding across its seams)."""
        from .operation_contracts import surface
        stamp = ()
        if prop.channel == 'HEIGHT' and getattr(prop, 'material_mapping_version', 0) == 1:
            stamp += (('metric-height-distance', self.ts.height_distance),)
        if layer.type == 'PAINT' and getattr(layer, 'stroke_key', ''):
            from ..painting.strokes import stroke_identity
            stamp += (('strokes', stroke_identity(self.scene.open_painter, layer.stroke_key, self.ts)),)
        if layer.type == 'FILL' and prop.projection == 'DECAL':
            from ..painting import decals
            stamp += (('decal', decals.signature(layer)),)
        geometry = (bool(stamp) or prop.projection == 'TRIPLANAR' or getattr(prop, 'material_mapping_version', 0) == 1
                    or any(surface(e) for e in effects if e.enabled and e.strength > 0))
        return stamp + (self._geometry_stamp(geometry),)

    def _channel_prefix(self, layer, prop, count, rehydrate=None):
        anchored = layer.type == 'FILL' and prop.anchor
        def owners():
            items = [prop, *list(prop.effects)[:count]]
            if getattr(layer, 'stroke_key', '') or prop.projection == 'DECAL':
                items.append(layer)   # stroke edits and decal drags notify the layer
            if anchored:
                items.extend(self._anchor_dependencies(prop.anchor, channel=prop.anchor_channel or prop.channel)[0])
            return items
        def identity():
            effects = list(prop.effects)[:count]
            return ('channel-prefix', layer.type, prop.channel,
                    values(prop, excluded={'effects', 'enabled', 'opacity', 'blend_mode'}),
                    tuple(values(e, excluded={'strength'} if i == count - 1 else ()) for i, e in enumerate(effects)),
                    self._content_stamp(layer, prop, effects),
                    ('anchor', self._anchor_dependencies(prop.anchor, channel=prop.anchor_channel or prop.channel)[1])
                    if anchored else None)
        def factory(signature):
            from .snapshots import capture
            effects = tuple(capture(e).replaced(strength=1.) if i == count - 1 else capture(e)
                            for i, e in enumerate(list(prop.effects)[:count]))
            snapshot = capture(prop).replaced(effects=effects)
            def parts():
                from .channel_eval import _raw_source, apply_texture_effects
                from .operation_contracts import stack_footprint
                from .tiles import expand
                halo = stack_footprint(snapshot.effects)
                cache, geometry = {}, {}
                if halo is not None:
                    rows = _band_rows(halo, self.n)
                    for low in range(0, self.n, rows):
                        high = min(low + rows, self.n)
                        start, stop = expand(low, high, halo, self.n)
                        data = np.empty((stop - start, self.n, 4), np.float32)
                        for row in range(start, stop, 16):
                            end = min(row + 16, stop)
                            data[row - start:end - start] = _raw_source(snapshot, layer.type, self.n,
                                                                        row, end, cache, self.ts, geometry)
                        yield low, apply_texture_effects(data, snapshot.effects)[low-start:high-start]
                    return
                data = np.empty((self.n, self.n, 4), np.float32)
                for low in range(0, self.n, 16):
                    high = min(low + 16, self.n)
                    data[low:high] = _raw_source(snapshot, layer.type, self.n, low, high, cache, self.ts, geometry)
                    # Source generation yields before the bounded unsupported filter.
                    yield low, data[low:high]
                from .channel_eval import seam_map_for
                filtered = apply_texture_effects(data, snapshot.effects, seam_map_for(self.ts, self.n, snapshot.effects))
                yield from self._parts(filtered)
            return parts()
        if rehydrate is not None:
            return self._rebind(rehydrate, owners, identity, factory)
        with self.role(f'RasterChannelPrefix:{layer.id}:{prop.channel}:{count}'):
            return self.boundary(self._role, owners, identity, factory,
                                 ['channel_prefix', layer.id, prop.channel, count])

    def _strokes_boundary(self, layer, prop, rehydrate=None):
        """The layer's vector strokes for one channel, composited over its paint image on the CPU.

        A re-identification (a stroke added, edited or undone) rebuilds only the rows
        the strokes that differ from the last published leaf touch, starting from
        that leaf's pixels; the stroke rasters themselves are memoised in the set's
        shared cache, so a gesture never re-rasterises the layer's history.
        """
        from ..painting.strokes import stroke_identity
        channel = prop.channel
        def owners():
            return [layer, prop]
        def identity():
            # The strokes this set may render (per-stroke scope, 0.13 hardening H2): a stroke
            # excluded here neither identifies nor rebuilds the leaf.
            return ('strokes', layer.stroke_key, channel, stroke_identity(self.scene.open_painter, layer.stroke_key, self.ts),
                    image_key(prop.image) if prop.image else None, self._geometry_stamp(True))
        def factory(signature):
            from .snapshots import capture
            snapshot = capture(prop)
            _, key, _, identity_now, base_key, stamp = signature
            previous = _previous_strokes_leaf(self.n, key, channel, base_key, stamp)
            # The base leaf may have lost its last user (a gesture preview stands in for it
            # on the node): pinned so the retention budget keeps it until this job is done.
            pinned = self.resources.pin(previous[0]) if previous is not None else None
            def parts():
                from ..painting.stroke_raster import channel_rows, rasters, raster_band
                from ..painting.strokes import stroke_channels, strokes_for
                from .channel_eval import _image_source
                from .raster_cache import for_texture_set
                try:
                    cache = {}
                    geometry = for_texture_set(self.scene, self.ts)
                    n = self.n
                    project = self.scene.open_painter
                    strokes = [s for s in strokes_for(project, key, self.ts) if channel in stroke_channels(s)]
                    bands = {stroke.id: raster_band(raster, n)
                             for stroke, raster in zip(strokes, rasters(self.ts, strokes, n, geometry))}
                    state = {'key': key, 'channel': channel, 'base': repr(base_key), 'geometry': repr(stamp),
                             'identity': [[s.id, s.revision] for s in strokes], 'rows': bands}
                    if previous is None:
                        ranges = [(0, n)]
                    else:
                        image, before = previous
                        yield 'base', image
                        ranges = _dirty_rows(before, state, n)
                    rows = 16 if previous is None else 64
                    for low_range, high_range in ranges:
                        for low in range(low_range, high_range, rows):
                            high = min(low + rows, high_range)
                            base = (_image_source(snapshot.image, snapshot, 'PAINT', n, low, high, cache, self.ts, geometry)
                                    if snapshot.image else None)
                            if base is not None:
                                base = np.broadcast_to(base, (high - low, n, 4))
                            yield low, channel_rows(self.ts, layer, channel, n, low, high, geometry, base=base, cache=cache)
                    yield 'state', state
                finally:
                    if pinned is not None:
                        self.resources.unpin(pinned)
            return parts()
        placeholder = (0.0, 0.0, 0.0, 0.0)   # nothing of the layer, never a grey slab, until the leaf publishes
        # A stroke boundary keeps one leaf datablock for its life and regenerates content into it
        # (``stable``): a new stroke never makes a new datablock, so a memfile undo never frees a
        # leaf the material still binds — the open 0.13 crash (docs/quality-stability-0.13.md, S2).
        if rehydrate is not None:
            return self._rebind(rehydrate, owners, identity, factory, placeholder=placeholder, interactive=True, stable=True)
        fallback = prop.image if (prop.image is not None and tuple(prop.image.size) == (self.n, self.n)) else None
        with self.role(f'Strokes:{layer.id}:{channel}'):
            return self.boundary(self._role, owners, identity, factory, ['strokes', layer.id, channel],
                                 placeholder=placeholder, fallback=fallback, interactive=True, stable=True)

    # ---- cached layers (0.9.0) ----------------------------------------------

    def _find_layer(self, identifier):
        """Resolve a layer from the live collection (closures must not keep RNA references)."""
        return next((item for item in self.ts.layers if item.id == identifier), None)

    def _subtree_identity(self, folder, channel):
        """Owners and identity of an isolated folder's child composite for one
        channel: every descendant's blend state, content chain and mask stack.
        The folder's own opacity, visibility, blend and mask stay live."""
        from ..core.layer_stack import descendants
        inside = descendants(self.ts, folder.id)
        owners, keys = [], []
        for layer in self.ts.layers:
            if layer.id not in inside:
                continue
            owners.append(layer)
            prop = channel_for(layer, channel)
            stamp = (layer.id, values(layer, excluded={'channels', 'masks'}))
            if prop is not None:
                stamp += ((prop.enabled, prop.opacity, prop.blend_mode),)
                if layer.type != 'FOLDER':
                    nested_owners, nested_key = self._channel_identity(layer, prop, len(prop.effects))
                    owners.extend(nested_owners)
                    stamp += (nested_key,)
            nested_owners, nested_keys = self._mask_dependencies(list(layer.masks))
            owners.extend(nested_owners)
            keys.append(stamp + (nested_keys,))
        return owners, ('subtree', folder.id, channel, tuple(keys))

    def _cache_identity(self, layer, channel):
        """Owners and identity of what a cache image of ``layer``/``channel`` holds.

        Built with stable (name-based) datablock keys: the identity is persisted
        as the row's provenance and compared again after the file is reopened.
        """
        from .resource_keys import stable_keys
        with stable_keys():
            if layer.type == 'FOLDER':
                owners, key = self._subtree_identity(layer, channel)
            else:
                prop = channel_for(layer, channel)
                owners, key = ([], None) if prop is None else self._channel_identity(layer, prop, len(prop.effects))
                mask_owners, mask_key = self._mask_dependencies(list(layer.masks))
                owners = [*owners, *mask_owners]
                key = (key, mask_key)
        return owners, ('layer-cache', self.n, layer.type, channel, key)

    def _layer_cache(self, layer, channel, rehydrate=None):
        """One managed image sampled in place of a cached layer's content (0.9.0).

        The sampler is an image binding on the layer's cache row (a publication
        swaps the image through ``update``) and a CacheBinding whose identity
        is compared with the row's provenance to detect a stale cache. Neither
        allocates: refreshes go through ``layer_cache.request``.
        """
        from . import layer_cache
        layer_id = layer.id
        row = self._cache_row(layer, channel)
        if row is None:
            raise ValueError(f'Layer "{layer.name}" has no cache row for {channel}')

        def owners():
            current = self._find_layer(layer_id)
            if current is None:
                return [self.ts]
            items = [current, *self._cache_identity(current, channel)[0]]
            current_row = self._cache_row(current, channel)
            if current_row is not None:
                items.append(current_row)
            return items

        def identity():
            current = self._find_layer(layer_id)
            if current is None:
                return ('layer-cache', 'missing', layer_id, channel)
            return self._cache_identity(current, channel)[1]

        def getter():
            current = self._find_layer(layer_id)
            current_row = self._cache_row(current, channel) if current is not None else None
            image = current_row.image if current_row is not None else None
            if image is None or tuple(image.size) != (self.n, self.n) or not image.has_data:
                return layer_cache.placeholder()
            return image

        image = getter()
        if rehydrate is not None:
            node = rehydrate
            node.image = image
        else:
            node = self.node('ShaderNodeTexImage')
            node.image = image
            node.interpolation, node.extension = 'Linear', 'EXTEND'
            self.link(self.coord_uv(), node.inputs['Vector'])
            node['op_cache'] = json.dumps([layer_id, channel])
        entry = (row.as_pointer(), self._role, getter, node, image_key(image))
        self.image_bindings.append(entry)
        binding = CacheBinding(layer_id, channel, self._role, {o.as_pointer() for o in owners()},
                               identity, node, identity(), owner_factory=owners)
        self.cache_bindings.append(binding)
        if self.unit is not None:
            self.unit.image_bindings.append(entry)
            self.unit.cache_bindings.append(binding)
        return Signal(node.outputs['Color'], node.outputs['Alpha'])

    def _cache_factory(self, layer_id, channel):
        """Strips of the layer's cache content, evaluated by the CPU reference path."""
        layer = self._find_layer(layer_id)
        if layer is None:
            raise ValueError('The cached layer no longer exists')
        n = self.n
        if layer.type == 'FOLDER':
            from .composite import Composer
            composer = Composer(self.ts, channel, n, {}, {})

            def parts():
                for low in range(0, n, 16):
                    high = min(low + 16, n)
                    yield low, composer.group(layer_id, low, high)
            return parts()
        from .snapshots import capture
        snapshot = capture(layer)
        prop = channel_for(snapshot, channel)
        kind = layer.type

        def parts():
            from .channel_eval import source
            from .mask_eval import evaluate_masks
            cache, geometry = {}, {}
            for low in range(0, n, 16):
                high = min(low + 16, n)
                if prop is None:
                    rows = np.zeros((high - low, n, 4), np.float32)
                else:
                    rows = np.array(np.broadcast_to(source(prop, kind, n, low, high, cache, self.ts, geometry),
                                                    (high - low, n, 4)), np.float32)
                if snapshot.masks:
                    rows[..., 3] *= evaluate_masks(self.ts, snapshot, n, low, high, cache, geometry)
                yield low, rows
        return parts()

    def _reconcile_cache(self, binding, force=False):
        """Compare a cache binding with its row; request a refresh when CACHED (or forced).

        Returns the number of requests made.
        """
        from . import layer_cache
        layer = self._find_layer(binding.layer_id)
        row = self._cache_row(layer, binding.channel) if layer is not None else None
        if layer is None or row is None:
            binding.stale = True
            return 0
        image = row.image
        missing = image is None or tuple(image.size) != (self.n, self.n) or not image.has_data
        binding.stale = missing or row.provenance != repr(binding.signature)
        if not binding.stale and not force:
            layer_cache.discard(self.scene, self.ts, layer.id, binding.channel)
            return 0
        if layer.cache_mode != 'CACHED' and not force:
            return 0
        layer_id, channel = binding.layer_id, binding.channel
        layer_cache.request(self.scene, self.ts, layer_id, channel, repr(binding.signature), self.n,
                            factory=lambda: self._cache_factory(layer_id, channel),
                            current=lambda: repr(binding.identity()), name=layer.name, immediate=force)
        return 1

    def request_caches(self, layer_id, force=False):
        """Request every cache image of a layer (explicit Refresh); returns how many."""
        count = 0
        for binding in self.cache_bindings:
            if binding.layer_id == layer_id:
                binding.signature = binding.identity()
                count += self._reconcile_cache(binding, force=force)
        return count

    def cache_states(self):
        """'CURRENT' | 'STALE' | 'MISSING' per cached layer channel, for diagnostics and the UI."""
        states = {}
        if not self.cache_bindings:
            return states
        # Each layer can publish several cached channels. Resolve the current
        # RNA collection once per report, not once per channel. Keep the first
        # match just as _find_layer does while a duplicate is being repaired.
        layers = {}
        for layer in self.ts.layers:
            layers.setdefault(layer.id, layer)
        for binding in self.cache_bindings:
            layer = layers.get(binding.layer_id)
            row = self._cache_row(layer, binding.channel) if layer is not None else None
            image = row.image if row is not None else None
            if image is None or tuple(image.size) != (self.n, self.n) or not image.has_data:
                state = 'MISSING'
            else:
                state = 'STALE' if binding.stale else 'CURRENT'
            states[f'{binding.layer_id}:{binding.channel}'] = state
        return states

    def _decal_signal(self, layer, prop, channel):
        """A decal channel (0.9.6): the image through the Empty's frame, natively.

        Texture Coordinate bound to the object gives decal-local positions, so
        moving, rotating or scaling the Empty updates the viewport without
        Python work; depth, facing, softness and the frame axes are uniforms.
        """
        from ..painting import decals
        obj = decals.resolve(layer)
        if getattr(layer, 'decal_inline', False):
            coordinates = self.node('ShaderNodeNewGeometry')
            origin = self.combine(*(self.param(layer, 'decal_origin', i) for i in range(3)))
            delta = self.vec('SUBTRACT', coordinates.outputs['Position'], origin)
            local = [self.vec('DOT_PRODUCT', delta, self.combine(*(self.param(layer, 'decal_axes', axis * 3 + i) for i in range(3)))) for axis in range(3)]
            width, height = (self.param(layer, 'decal_extent', i) for i in range(2))
            offset = (-.5, -.5)
        else:
            coordinates = self.node('ShaderNodeTexCoord')
            coordinates.object = obj
            coordinates['op_decal'] = layer.id
            width, height = decals.extent(obj)
            offset = tuple(getattr(obj, 'empty_image_offset', (-.5, -.5)))
            local = self.separate(coordinates.outputs['Object'])
        u = self.math('SUBTRACT', self.math('DIVIDE', local[0], width), offset[0])
        v = self.math('SUBTRACT', self.math('DIVIDE', local[1], height), offset[1])
        sample = self.image(prop.image, self.combine(u, v, 0.0), prop.filtering, 'NONE',
                            getter=lambda: prop.image, owner=prop)
        depth = self.math('MAXIMUM', self.param(layer, 'decal_depth'), 1e-6)
        distance = self.math('MULTIPLY', self.math('ABSOLUTE', local[2]), self.param(layer, 'decal_scale', 2))
        coverage = self.math('MULTIPLY', sample.alpha, self.math('LESS_THAN', distance, depth))
        axis_z = self.combine(*(self.param(layer, 'decal_axes', 6 + i) for i in range(3)))
        facing = self.math('GREATER_THAN', self.vec('DOT_PRODUCT', self.coord_normal(), axis_z), 0.0)
        facing = self.math('MAXIMUM', facing, self.math('SUBTRACT', 1.0, self.param(layer, 'decal_facing')))
        coverage = self.math('MULTIPLY', coverage, facing)
        border = self.math('MINIMUM', self.math('MINIMUM', u, self.math('SUBTRACT', 1.0, u)),
                           self.math('MINIMUM', v, self.math('SUBTRACT', 1.0, v)))
        soft = self.clamp(self.math('DIVIDE', border, self.math('MAXIMUM', self.param(layer, 'decal_falloff'), 1e-6)))
        coverage = self.math('MULTIPLY', coverage, soft)
        color = sample.color
        if channel == 'NORMAL':
            nx, ny, nz = self.separate(self.vec('SUBTRACT', self.vec('SCALE', color, 2.0), (1., 1., 1.)))
            axis_x = self.combine(*(self.param(layer, 'decal_axes', i) for i in range(3)))
            axis_y = self.combine(*(self.param(layer, 'decal_axes', 3 + i) for i in range(3)))
            world = self.vec('ADD', self.vec('ADD', self.vec('SCALE', axis_x, nx), self.vec('SCALE', axis_y, ny)),
                             self.vec('SCALE', axis_z, nz))
            # The set's tangent frame, signed like the Normal Map node that decodes
            # the result: a Normal Map node fed a constant tangent-space (1, 0, 0)
            # returns T and (0, 1, 0) returns sign · N × T, so a mirrored UV island
            # flips the bitangent here as it does in painting.projection.tangents.
            normal = self.coord_normal()
            t, b = (self._tangent_axis(axis) for axis in ((1., .5, .5), (.5, 1., .5)))
            components = self.combine(self.vec('DOT_PRODUCT', world, t), self.vec('DOT_PRODUCT', world, b),
                                      self.vec('DOT_PRODUCT', world, normal))
            color = self.vec('ADD', self.vec('SCALE', components, .5), (.5, .5, .5))
        return Signal(color, coverage)

    def _tangent_axis(self, color):
        frame = self.node('ShaderNodeNormalMap')
        frame.space = 'TANGENT'
        frame.inputs['Strength'].default_value = 1.0
        frame.inputs['Color'].default_value = (*color, 1.0)
        return frame.outputs['Normal']

    def _channel_base(self, layer, prop, channel):
        with self.role(f'LayerSource:{layer.id}:{channel}'):
            if (layer.type == 'FILL' and getattr(prop, 'material_mapping_version', 0) == 1
                    and (prop.image or prop.detail_image)):
                return self._channel_prefix(layer, prop, 0)
            if layer.type == 'PAINT' and getattr(layer, 'stroke_key', ''):
                value = self._strokes_boundary(layer, prop)
            elif layer.type == 'FILL' and prop.projection == 'DECAL' and not prop.anchor:
                from ..painting import decals
                if prop.detail_image:
                    return self._channel_prefix(layer, prop, 0)
                if prop.image is None or (decals.resolve(layer) is None and not getattr(layer, 'decal_inline', False)):
                    # Nothing to project (channel_eval._base_source): the flat
                    # colour must not cover the whole mesh.
                    value = Signal((0., 0., 0.), 0.0)
                else:
                    value = self._decal_signal(layer, prop, channel)
            elif layer.type == 'FILL' and prop.anchor:
                # Anchored content is already in texture space: no projection or tiling.
                value = self.anchor_signal(prop.anchor, prop.anchor_channel or channel)
            elif prop.image:
                value = (self.image(prop.image, getter=lambda: prop.image, owner=prop) if layer.type == 'PAINT'
                    else self.projected(prop, lambda uv: self.image(prop.image, uv, prop.filtering, prop.tiling,
                                                                  getter=lambda: prop.image, owner=prop)))
                if layer.type == 'PAINT' and prop.image.is_float:
                    # Native float paint canvases contain premultiplied pixels;
                    # the compositor's source-over inputs are straight color.
                    factor = self.math('DIVIDE', self.math('GREATER_THAN', value.alpha, 1e-8),
                                       self.math('MAXIMUM', value.alpha, 1e-8))
                    value = Signal(self.vec('SCALE', value.color, factor), value.alpha)
            elif layer.type == 'PAINT':
                value = Signal((0., 0., 0.), 0)
            elif channel in {'BASE_COLOR', 'NORMAL', 'EMISSION'}:
                value = Signal(self.color_param(prop, 'color'), self.param(prop, 'color', 3))
            else:
                value = Signal(self.param(prop, 'value'))
            field = layer.type == 'FILL' and bool(prop.detail_image or prop.procedural)
            if field and channel == 'NORMAL':
                # UV derivatives are a compatibility source boundary. Normal
                # strength/combine effects remain shader-native downstream.
                value = self._channel_prefix(layer, prop, 0)
            elif field:
                scalar = self.math('SUBTRACT', self._detail_value(layer, prop, channel), .5)
                if channel == 'HEIGHT':
                    if getattr(prop, 'material_mapping_version', 0) == 1:
                        amplitude = self.math('DIVIDE', self.param(prop, 'material_height_m'),
                            self.math('MAXIMUM', self.math('MULTIPLY', self.param(prop, 'material_meters_per_unit'),
                                self.param(self.ts, 'height_distance')), 1e-15))
                    else:
                        amplitude = self.param(prop, 'procedural_height')
                    color = self.math('ADD', .5, self.math('MULTIPLY', scalar, amplitude))
                elif channel in {'BASE_COLOR', 'EMISSION'}:
                    color = self.vec('SCALE', value.color, self.math('ADD', 1,
                        self.math('MULTIPLY', scalar, self.param(prop, 'procedural_detail'))))
                else:
                    variation = self.math('MULTIPLY', self.math('MULTIPLY', scalar, self.param(prop, 'procedural_detail')), .35)
                    color = self.vec('ADD', value.color, self.combine(variation, variation, variation))
                value = Signal(self.clamp(color), value.alpha)
                if prop.projection == 'UV' and prop.tiling == 'NONE':
                    value = Signal(value.color, self.math('MULTIPLY', value.alpha, self._coverage(self.coordinates(prop))))
        return value

    # ---- unit keys and planning ----------------------------------------

    def _layer(self, identifier):
        layers = self._plan.layers if self._plan is not None else {l.id: l for l in self.ts.layers}
        return layers.get(identifier)

    @staticmethod
    def _mode(layer, prop):
        return prop.blend_mode if prop and prop.blend_mode != 'INHERIT' else layer.blend_mode

    def _channel(self, layer, channel):
        """channel_for, memoized per plan (RNA references: dropped with the plan's layers)."""
        memo = getattr(self._plan, 'channels', None)
        if memo is None:
            return channel_for(layer, channel)
        key = (layer.id, channel)
        prop = memo.get(key, memo)
        if prop is memo:
            prop = memo[key] = channel_for(layer, channel)
        return prop

    def _touched(self, layer, channel):
        memo = getattr(self._plan, 'touched', None)
        if memo is not None:
            memo = _layer_memo(memo, layer.id)
            if channel in memo:
                return memo[channel]
        prop = self._channel(layer, channel)
        touched = not (prop and not prop.enabled) and self._mode(layer, prop) != 'DISABLE'
        if memo is not None:
            memo[channel] = touched
        return touched

    def _wired(self, layer, channel):
        """The layer's channel unit is in the chain: it blends, or it is a folder
        whose disabled channel still publishes a composite (or a descendant's
        output) that consumers read there; that unit passes its backdrop through."""
        if self._touched(layer, channel):
            return True
        if layer.type != 'FOLDER' or self._skipped(layer):
            return False
        return channel in self._subtree_consumed(layer)

    def _composite_consumed(self, layer):
        """Channels consumers read from the layer's OUTPUT/GROUP anchors (indexed once per plan)."""
        from .anchors import consumed_channels, output_id, group_id
        plan = self._plan
        table = getattr(plan, 'consumed', None) if plan is not None else None
        if table is None:
            table = {}
            readers = getattr(plan, 'composite_readers', None)
            items = (self.ts.layers if readers is None else
                     [plan.layers[identifier] for identifier in readers if identifier in plan.layers])
            for item in items:
                for source in (*item.channels, *item.masks):
                    for identifier, requested in consumed_channels(source):
                        anchor = self.anchors.get(identifier)
                        if anchor is not None and anchor.kind in COMPOSITE_KINDS:
                            table.setdefault(identifier, set()).add(channel_of(anchor, requested))
            if plan is not None:
                plan.consumed = table
        channels = set(table.get(output_id(layer.id), ()))
        if layer.type == 'FOLDER':
            channels |= table.get(group_id(layer.id), set())
        return channels

    def _subtree_consumed(self, folder):
        """Channels consumers read from the folder's or any descendant's composite anchors."""
        plan = self._plan
        memo = getattr(plan, 'subtree_consumed', None)
        if memo is not None and folder.id in memo:
            return memo[folder.id]
        channels = set(self._composite_consumed(folder))
        if getattr(plan, 'composite_readers', True):   # nothing reads a composite: no descendant adds any
            children = getattr(plan, 'children', None)
            if children is None:
                from ..core.layer_stack import descendants
                inside = descendants(self.ts, folder.id)
                for layer in self.ts.layers:
                    if layer.id in inside:
                        channels |= self._composite_consumed(layer)
            else:
                work, seen = [folder.id], {folder.id}
                while work:
                    for layer in children.get(work.pop(), ()):
                        if layer.id not in seen:
                            seen.add(layer.id)
                            channels |= self._composite_consumed(layer)
                            work.append(layer.id)
        if memo is not None:
            memo[folder.id] = channels
        return channels

    def _source_key(self, layer, channel, count=None):
        """Unit key of a channel prefix, or None when its content is a constant."""
        prop = channel_for(layer, channel)
        if prop is None or not prop.enabled or layer.type == 'FOLDER':
            return None
        effects = len(prop.effects)
        count = effects if count is None else min(int(count), effects)
        return ('SRC', layer.id, channel, count)

    @staticmethod
    def _mask_active(mask, pre_filter):
        return mask.enabled and mask.source != 'ANCHOR' and not (pre_filter and mask.source == 'FILTER')

    def _mask_key(self, layer, through='', pre_filter=False):
        """Unit key of a mask prefix, or the constant it evaluates to."""
        masks = list(layer.masks)
        stop = len(masks)
        if through:
            stop = next((i + 1 for i, m in enumerate(masks) if m.id == through), None)
            if stop is None:
                return 0.0  # A missing published stage reads as neutral.
        if not any(self._mask_active(m, pre_filter) for m in masks[:stop]):
            return 1.0
        return ('MASK', layer.id, '' if stop == len(masks) else masks[stop - 1].id, bool(pre_filter))

    def _anchor_key(self, identifier, channel=None):
        anchor = self.anchors.get(identifier)
        if anchor is None:
            return None
        if anchor.kind == 'MASK':
            return self._mask_key(anchor.layer, identifier)
        if anchor.kind in COMPOSITE_KINDS:
            source = self._composite_source(anchor, channel)
            return source[0] if isinstance(source, tuple) else None
        return self._source_key(anchor.layer, anchor.channel, anchor.position)

    # ---- composite anchors: what the top tree wires, resolved to unit exports ----

    def _composite_source(self, anchor, channel=None):
        """(unit key, export name) publishing a composite anchor, or a constant Signal."""
        channel = channel_of(anchor, channel)
        layer = anchor.layer
        if anchor.kind == 'OUTPUT':
            return self._composite_output(layer, channel)
        return self._composite_group(layer, channel)

    def _composite_output(self, layer, channel):
        """A layer blended over its backdrop: its CHANNEL unit, else its backdrop."""
        if self._touched(layer, channel):
            return (('CHANNEL', layer.id, channel), 'Out')
        return self._composite_backdrop(layer, channel)

    def _composite_backdrop(self, layer, channel):
        """What a layer's ``In`` sees, following ``_wire``: the previous touched
        sibling's output, a passthrough parent's backdrop, or a constant."""
        siblings = [item for item in self.ts.layers if item.parent_id == layer.parent_id]
        position = next((i for i, item in enumerate(siblings) if item.id == layer.id), 0)
        for previous in reversed(siblings[:position]):
            if self._wired(previous, channel):
                return (('CHANNEL', previous.id, channel), 'Out')
        parent = self._layer(layer.parent_id) if layer.parent_id else None
        if parent is None:
            return Signal(DEFAULTS[channel][:3], 1.0)
        if parent.group_mode == 'PASSTHROUGH':
            return self._composite_backdrop(parent, channel)
        return Signal((0., 0., 0.), 0.0)

    def _composite_group(self, folder, channel):
        """A folder's child composite before it blends; a folder whose disabled
        channel is read composes its children without blending them."""
        if folder.type == 'FOLDER' and self._wired(folder, channel):
            return (('CHANNEL', folder.id, channel), 'Group')
        if folder.type == 'FOLDER' and folder.group_mode == 'PASSTHROUGH':
            return self._composite_backdrop(folder, channel)
        return Signal((0., 0., 0.), 0.0)

    def _mask_reference_key(self, mask):
        if mask.reference_anchor:
            return self._anchor_key(mask.reference_anchor, mask.reference_channel)
        target = self._layer(mask.reference_layer)
        if target is None:
            return None
        if mask.reference_channel == 'MASK':
            return self._mask_key(target, mask.reference_mask, mask.reference_stage == 'PRE_FILTER')
        return self._source_key(target, mask.reference_channel,
                                0 if mask.reference_stage == 'PRE_FILTER' else None)

    def _reference_keys(self, key):
        """Unit keys a unit imports: its layer mask, its content and published stages
        (memoized per plan; an incremental plan carries those of layers no edit reached)."""
        memo = getattr(self._plan, 'refs', None)
        if memo is None:
            return tuple(self._reference_keys_uncached(key))
        memo = _layer_memo(memo, key[1])
        found = memo.get(key)
        if found is None:
            found = memo[key] = tuple(self._reference_keys_uncached(key))
        return found

    def _reference_keys_uncached(self, key):
        layer = self._layer(key[1])
        if layer is None:
            return []
        refs = []
        if key[0] == 'CHANNEL':
            if self._skipped(layer):
                return []   # a pass-through imports nothing: its content work stops
            cached = self._cache_effective(layer, key[2]) != 'LIVE'
            if (layer.type == 'FOLDER' or not cached) and self._touched(layer, key[2]):
                refs.append(self._mask_key(layer))
            if layer.type != 'FOLDER' and not cached:
                refs.append(self._source_key(layer, key[2]))
        elif key[0] == 'SRC':
            prop = self._channel(layer, key[2])
            if layer.type == 'FILL' and prop is not None and prop.anchor:
                refs.append(self._anchor_key(prop.anchor, prop.anchor_channel or key[2]))
            refs.append(self._field_key(layer, prop, key[2]))
        elif key[0] == 'MASK':
            masks = list(layer.masks)
            stop = len(masks) if not key[2] else next(i + 1 for i, m in enumerate(masks) if m.id == key[2])
            for mask in masks[:stop]:
                if self._mask_active(mask, key[3]) and mask.source == 'REFERENCE':
                    refs.append(self._mask_reference_key(mask))
                if self._mask_active(mask, key[3]):
                    refs.append(self._graph_key(layer, mask))
        return [ref for ref in refs if isinstance(ref, tuple)]

    @staticmethod
    def _graph_key(layer, mask):
        """The unit of a Custom Graph mask's Mask Graph (its own pass), or None."""
        from ..procedural_graph import is_native
        if mask.source == 'GRAPH' and is_native(mask.node_group):
            return ('GRAPH', layer.id, mask.id)
        return None

    def _continuation(self, key):
        """The longest shorter prefix unit this unit continues from, or None."""
        if key[0] == 'SRC':
            for count in range(key[3] - 1, -1, -1):
                candidate = ('SRC', key[1], key[2], count)
                if candidate in self._plan.keys:
                    return candidate
            return None
        if key[0] == 'MASK':
            layer = self._layer(key[1])
            masks = list(layer.masks)
            stop = len(masks) if not key[2] else next(i + 1 for i, m in enumerate(masks) if m.id == key[2])
            for i in range(stop - 1, 0, -1):
                if masks[i - 1].parent_id or masks[i - 1].source == 'GROUP':
                    continue   # a continuation never starts inside a group's member run
                candidate = self._mask_key(layer, masks[i - 1].id, key[3])
                if isinstance(candidate, tuple) and candidate != key and candidate in self._plan.keys:
                    return candidate
        return None

    @staticmethod
    def _included(layer):
        # Hidden layers compile behind a zero uniform: toggling visibility
        # writes one socket instead of rebuilding and recompiling the material.
        return True

    def _visible(self, layer):
        """Effective visibility: the layer and every ancestor are visible."""
        seen = set()
        while layer is not None and layer.id not in seen:
            if not layer.visible:
                return False
            seen.add(layer.id)
            layer = self._layer(layer.parent_id) if layer.parent_id else None
        return True

    # ---- layer performance (0.9.0): bypass and cached layers --------------

    def _skipped(self, layer):
        """The layer compiles as a pass-through: it or an ancestor is bypassed,
        or an ancestor is cached (its subtree is sampled from the cache image)."""
        memo = getattr(self._plan, 'skipped', None)
        if memo is not None:
            skipped = memo.get(layer.id)
            if skipped is None:
                skipped = memo[layer.id] = self._skipped_walk(layer)
            return skipped
        return self._skipped_walk(layer)

    def _skipped_walk(self, layer):
        seen, cursor, own, consumed = set(), layer, True, None
        while cursor is not None and cursor.id not in seen:
            if getattr(cursor, 'bypass', False):
                return True
            if not own and self._cache_mode(cursor) != 'LIVE':
                if consumed is None:
                    return True
                # The cache image ignores the anchor; its consumers read the layer
                # (or the published folder it sits in) live, composed over the
                # isolated folder's empty backdrop only.
                self._warn(f'Anchor consumers read "{consumed.name}" inside cached folder '
                           f'"{cursor.name}": compiled without its backdrop', layer.id)
                return False
            if consumed is None and self._composite_consumed(cursor):
                consumed = cursor
            seen.add(cursor.id)
            own = False
            cursor = self._layer(cursor.parent_id) if cursor.parent_id else None
        return False

    def _warn(self, message, layer_id):
        """A warning of the plan's walk about ``layer_id``, kept with the plan (an incremental
        plan carries it with the layer's other answers; see ``_build_warnings``)."""
        warned = getattr(self._plan, 'warned', None)
        found = self._warnings if warned is None else warned.setdefault(layer_id, [])
        if message not in found:
            found.append(message)

    def _build_warnings(self):
        """What the plan's walk and the compiled units reported, then anything else."""
        found = []
        for messages in getattr(self._plan, 'warned', {}).values():
            found.extend(message for message in messages if message not in found)
        for unit in self.units.values():
            if unit.warnings:   # most units have none
                found.extend(message for message in unit.warnings if message not in found)
        found.extend(message for message in self._warnings if message not in found)
        return found

    def _cache_mode(self, layer):
        """The layer's own cache mode, or LIVE when it cannot be cached."""
        mode = getattr(layer, 'cache_mode', 'LIVE')
        if mode == 'LIVE' or not len(layer.cache):
            return 'LIVE'
        if layer.type == 'FOLDER' and layer.group_mode == 'PASSTHROUGH':
            # A passthrough composite depends on its backdrop: no backdrop-
            # independent image exists for it.
            self._warn(f'Passthrough folder "{layer.name}" cannot be cached; set it to Isolated '
                       'or cache its children', layer.id)
            return 'LIVE'
        return mode

    def _cache_effective(self, layer, channel=None):
        """CACHED/FROZEN when this layer's channel units sample a cache image, else LIVE.

        A folder channel no descendant touches stays LIVE whatever the mode: it passes
        its backdrop through, and a cache image of that would cost a full-resolution
        float texture (64 MiB at 2048) for nothing.
        """
        if self._skipped(layer):
            return 'LIVE'
        mode = self._cache_mode(layer)
        if mode != 'LIVE' and channel is not None and layer.type == 'FOLDER' and not self._subtree_touches(layer, channel):
            return 'LIVE'
        return mode

    def _subtree_touches(self, folder, channel):
        """Whether any descendant contributes to ``channel`` (visibility aside: it is a uniform)."""
        stack, seen = [folder.id], set()
        children = getattr(self._plan, 'children', None)
        while stack:
            parent = stack.pop()
            for layer in (children.get(parent, ()) if children is not None
                          else [l for l in self.ts.layers if l.parent_id == parent]):
                if layer.id in seen:
                    continue
                seen.add(layer.id)
                if layer.type == 'FOLDER':
                    stack.append(layer.id)
                elif self._touched(layer, channel):
                    return True
        return False

    @staticmethod
    def _cache_row(layer, channel):
        return next((row for row in layer.cache if row.channel == channel), None)

    def plan(self, previous=None, dirty=None):
        """Resolve the unit set, wiring and opacity contexts from authoring data.

        ``previous`` is the plan the graph's structure was made from and ``dirty`` the layers
        structurally edited since (``touch_layers``). The answers of every layer no edit can
        have reached (``_reach``) are carried over from it instead of read again: a blend
        mode at 200 layers re-plans a handful of layers, not every one. None, or a stack
        whose layers were added, removed or reordered, plans every layer.
        """
        stack_layers = list(self.ts.layers)
        layers, children, ids, pointers = {}, {}, [], {}
        for layer in stack_layers:
            identifier = layer.id
            ids.append(identifier)
            layers[identifier] = layer
            pointers[identifier] = layer.as_pointer()   # where each layer's struct is now
            children.setdefault(layer.parent_id, []).append(layer)
        ids = tuple(ids)
        # A collection edited without a callback (the operators invalidate the set, a script
        # may not): its layer's collection sizes differ, and it is planned again.
        prints = {identifier: _collection_print(layer) for identifier, layer in layers.items()}
        affected = None
        # Removing an image, node group, object or text clears the fields pointing at it with no
        # callback: a removal since the previous plan plans every layer.
        present = _datablocks()
        reordered = (_reordered(previous.ids, ids) if previous is not None and dirty is not None
                     and getattr(previous, 'ids', None) is not None
                     and not (getattr(previous, 'datablocks', frozenset()) - present) else None)
        if reordered is not None:
            moved = {identifier for identifier, value in prints.items() if previous.prints.get(identifier) != value}
            affected = self._reach(previous, layers, children, set(dirty) | moved | reordered)
        while True:
            plan = self._plan_from(previous, affected, layers, children, ids, prints)
            plan.pointers = pointers
            plan.datablocks = present
            # Which plan it carried answers from, by token: holding the plan would chain every plan.
            plan.token = object()
            plan.carried = previous.token if affected is not None and previous is not None else None
            if affected is None:
                break
            # A re-planned layer that reads another now: that one is reached too.
            targets = {ref[1] for key in plan.keys if key[1] in affected for ref in plan.refs[key[1]][key]} - affected
            if not targets:
                break
            affected = self._reach(previous, layers, children, affected | targets)
        if VERIFY_STRUCTURE and affected is not None:
            self._verify_plan(plan)
        return plan

    def _plan_from(self, previous, affected, layers, children, ids, prints):
        # Per-plan memos: answers that only structural edits change, and a new plan
        # is made for every structural edit. channels holds RNA references and is
        # dropped whenever update() refreshes the layers (a collection may move).
        plan = SimpleNamespace(layers=layers, keys=set(), opaque={}, wiring=[], order=[], children=children,
                               skipped={}, touched={}, channels={}, signatures={}, base_owners=None,
                               ids=ids, prints=prints, wired={}, refs={}, warned={}, subtree_consumed={},
                               composite_readers=None, affected=affected, links=None, facts=None)
        if affected is None:
            plan.facts = {identifier: _layer_links(layer) for identifier, layer in layers.items()}
        else:
            plan.wired = {i: entry for i, entry in previous.wired.items() if i not in affected}
            plan.skipped = {i: value for i, value in previous.skipped.items() if i not in affected}
            # Per-layer memos of layers no edit reached are shared: their answers still hold.
            plan.touched = {i: memo for i, memo in previous.touched.items() if i not in affected}
            plan.refs = {i: memo for i, memo in previous.refs.items() if i not in affected}
            plan.signatures = {i: memo for i, memo in previous.signatures.items() if i not in affected}
            plan.warned = {i: list(found) for i, found in previous.warned.items() if i not in affected}
            plan.subtree_consumed = {i: value for i, value in previous.subtree_consumed.items()
                                     if i not in affected}
            plan.facts = {i: value for i, value in previous.facts.items() if i not in affected and i in layers}
            plan.facts.update((i, _layer_links(layers[i])) for i in affected if i in layers)
        plan.composite_readers = {identifier for identifier, (reads, _) in plan.facts.items()
                                  if any(_composite_handle(handle) for handle in reads)}
        self._plan = plan
        self._anchors = None

        def walk(parent, opaque, ancestors):
            for layer in children.get(parent, ()):
                if not self._included(layer):
                    continue
                identifier = layer.id
                if identifier in ancestors:
                    raise ValueError('Layer hierarchy contains a cycle')
                plan.opaque[identifier] = opaque
                entry = plan.wired.get(identifier)
                if entry is None:
                    touched = tuple(channel for channel in CHANNELS if self._wired(layer, channel))
                    entry = plan.wired[identifier] = (identifier, layer.type, parent, layer.group_mode, touched)
                plan.wiring.append(entry)
                for channel in entry[4]:
                    plan.order.append(('CHANNEL', identifier, channel))
                if entry[1] == 'FOLDER':
                    walk(identifier, opaque and entry[3] == 'PASSTHROUGH', ancestors | {identifier})

        walk('', True, frozenset())
        keys, refs, work = plan.keys, plan.refs, list(plan.order)
        while work:
            key = work.pop()
            if key in keys:
                continue
            keys.add(key)
            memo = refs.get(key[1])   # ``_reference_keys``'s memo, read in place
            found = memo.get(key) if memo is not None else None
            work.extend(found if found is not None else self._reference_keys(key))
        return plan

    @staticmethod
    def _links(plan):
        """(readers, targets, owners, named): which layers each layer reads, by what its units
        import and by what authoring names (``_layer_links``), the reverse, each anchor's layer,
        and the layers naming each handle (resolved or not)."""
        if plan.links is None:
            readers, targets, named = defaultdict(set), defaultdict(set), defaultdict(set)
            owners = {handle: identifier for identifier, (_, owns) in plan.facts.items() for handle in owns}
            for key, refs in ((key, refs) for memo in plan.refs.values() for key, refs in memo.items()):
                for ref in refs:
                    if ref[1] != key[1]:
                        readers[ref[1]].add(key[1])
                        targets[key[1]].add(ref[1])
            for identifier, (reads, _) in plan.facts.items():
                for handle in reads:
                    named[handle].add(identifier)
                    target = _link_target(handle, owners)
                    if target is not None and target != identifier:
                        readers[target].add(identifier)
                        targets[identifier].add(target)
            plan.links = (readers, targets, owners, named)
        return plan.links

    def _reach(self, previous, layers, children, seeds):
        """Layers whose answers an edit of ``seeds`` can change: each one and its descendants
        (skip, cache and backdrop contexts come from above), its ancestors alone (a folder's
        wiring and cache state see their subtree), the layers reading it or read by it, by what
        their units imported and by what authoring names, and every reader of a composite anchor
        (what it resolves to depends on the target's siblings), until nothing more is reached."""
        readers, targets, owners, named = self._links(previous)
        reached, below = set(), set()
        work = [(identifier, True) for identifier in seeds]
        work.extend((identifier, True) for identifier in previous.composite_readers)
        while work:
            identifier, down = work.pop()
            if identifier in reached and (not down or identifier in below):
                continue
            reached.add(identifier)
            layer = layers.get(identifier)
            for parent in (layer.parent_id if layer is not None else '',
                           previous.wired[identifier][2] if identifier in previous.wired else ''):
                if parent:
                    work.append((parent, False))
            work.extend((reader, True) for reader in readers.get(identifier, ()))
            if not down:
                continue
            below.add(identifier)
            work.extend((child.id, True) for child in children.get(identifier, ()))
            work.extend((target, True) for target in targets.get(identifier, ()))
            if layer is not None:
                # What it names now: a reference or anchor added since reaches its layer too.
                reads, owns = _layer_links(layer)
                for handle in reads:
                    target = _link_target(handle, owners)
                    if target is not None:
                        work.append((target, True))
                for handle in owns:
                    if handle in owners:
                        work.append((owners[handle], True))
                    work.extend((reader, True) for reader in named.get(handle, ()))   # named before it existed
        return reached

    def _verify_plan(self, plan):
        """OP_VERIFY_STRUCTURE: an incremental plan must equal a full plan of the same stack."""
        anchors = self._anchors
        full = self.plan()
        expected = {key: self._unit_signature(key, stub=self._stub(key)) for key in full.keys}
        self._plan, self._anchors = plan, anchors
        found = {key: self._unit_signature(key, stub=self._stub(key)) for key in plan.keys}
        problems = [name for name in ('wiring', 'order', 'keys', 'opaque')
                    if getattr(full, name) != getattr(plan, name)]
        differ = sorted(key for key in expected.keys() | found.keys() if expected.get(key) != found.get(key))
        if problems or differ:
            layers = sorted({key[1] for key in differ} | set(plan.affected))[:8]
            previous = self._structure_plan
            links = ({identifier: (sorted(self._links(previous)[0].get(identifier, ())),
                                   sorted(self._links(previous)[1].get(identifier, ()))) for identifier in layers}
                     if previous is not None else None)
            raise AssertionError(f'Incremental plan differs from a full plan: {problems} '
                                 f'{differ[:6]} (reached {sorted(plan.affected)[:12]}; readers and targets '
                                 f'in the previous plan: {links}; its references: '
                                 f'{ {i: previous.refs.get(i) for i in layers} if previous else None}; '
                                 f'now: { {i: plan.refs.get(i) for i in layers} })')

    def _unit_signature(self, key, stub=None):
        """The structure a unit compiles from; memoized per plan (each structural edit plans anew)."""
        memo = getattr(self._plan, 'signatures', None)
        if memo is None:
            return self._unit_signature_uncached(key, stub)
        memo = _layer_memo(memo, key[1])
        entry = (key, stub)
        found = memo.get(entry, memo)
        if found is memo:
            found = memo[entry] = self._unit_signature_uncached(key, stub)
        return found

    def _unit_signature_uncached(self, key, stub=None):
        layer = self._layer(key[1])
        if layer is None:
            return None
        refs = tuple(self._reference_keys(key))
        if key[0] == 'CHANNEL':
            prop = self._channel(layer, key[2])
            signature = (layer.type, self._mode(layer, prop), layer.group_mode, self._plan.opaque.get(layer.id), refs,
                         bool(getattr(layer, 'publish_group', False)) if layer.type == 'FOLDER' else None,
                         'skip' if self._skipped(layer) else self._cache_effective(layer, key[2]),
                         self._touched(layer, key[2]))
            if stub:
                # A stub stands in while the layer is hidden and its content unchanged.
                return ('stub', self._visible(layer), signature, self._source_signature(layer, key[2]))
            return signature
        if key[0] == 'SRC':
            prop = self._channel(layer, key[2])
            decal = None
            if prop is not None and prop.projection == 'DECAL':
                from ..painting import decals
                obj = decals.resolve(layer)
                decal = (obj.name, round(float(getattr(obj, 'empty_display_size', 1.0)), 6),
                         tuple(round(float(x), 6) for x in getattr(obj, 'empty_image_offset', (0, 0))),
                         tuple(round(x, 6) for x in decals.aspect(obj))) if obj else None
                if getattr(layer, 'decal_inline', False):
                    decal = ('inline-frame-v1',)
            return (layer.type, getattr(layer, 'stroke_key', ''), _prop_signature(prop, key[3]), refs,
                    self._continuation(key), decal)
        if key[0] == 'FIELD':
            # What ``detail`` compiles from: its projection and sampler state; the image and the
            # projection's numbers are bindings.
            prop = self._channel(layer, key[2])
            if prop is None:
                return None
            return ('field', layer.type, getattr(prop, 'material_mapping_version', 0), bool(prop.detail_image),
                    prop.projection, prop.filtering, prop.tiling, refs)
        if key[0] == 'GRAPH':
            from ..procedural_graph.program import unit_signature
            mask = next((m for m in layer.masks if m.id == key[2]), None)
            return ('mask-graph', unit_signature(mask.node_group) if mask is not None else None)
        masks = list(layer.masks)
        stop = len(masks) if not key[2] else next(i + 1 for i, m in enumerate(masks) if m.id == key[2])
        return (tuple(_mask_signature(m) for m in masks[:stop]), refs, self._continuation(key))

    def _source_signature(self, layer, channel):
        """Everything a stubbed channel would compile: its source and mask stacks."""
        prop = channel_for(layer, channel)
        return (_prop_signature(prop) if prop is not None else None,
                tuple(_mask_signature(m) for m in layer.masks))

    # ---- channel content ---------------------------------------------------

    def source(self, layer, channel, count=None):
        """Channel content after the first `count` effects; None means the whole stack.

        Every prefix is its own unit, so an anchor consumer and the owning layer
        share one node chain instead of compiling the same source twice.
        """
        key = self._source_key(layer, channel, count)
        if key is None:
            return Signal((0., 0., 0.), 0)
        if self.unit is None or self.unit.key != key:
            return self._import_signal(self.compile_unit(key))
        prop = channel_for(layer, channel)
        effects = list(prop.effects)
        count = key[3]
        if prop.image_name and not prop.image:
            raise ValueError('Texture image is missing: ' + prop.image_name + '. Reload or clear the channel texture.')
        if prop.detail_image_name and not prop.detail_image:
            raise ValueError('Surface detail image is missing: ' + prop.detail_image_name + '. Reload or clear the detail texture.')
        start, value = 0, None
        continued = self._continuation(key)
        if continued is not None:
            start, value = continued[3], self._import_signal(self.compile_unit(continued))
        if value is None:
            value = self._channel_base(layer, prop, channel)
        for index in range(start, count):
            effect = effects[index]
            # A disabled pointwise effect compiles behind its enabled uniform,
            # so toggling it never rebuilds; spatial filters stay structural.
            if effect.kind != 'ANCHOR' and (effect.enabled or is_pointwise(effect.kind)):
                self._compiled_effects[effect.as_pointer()] = effect
                self.unit.compiled_effects[effect.as_pointer()] = effect
                with self.role(f'ChannelEffect:{layer.id}:{channel}:{index}'):
                    try:
                        value = apply_effect(self, value, effect)
                    except UnsupportedEffect:
                        filtered = self._channel_prefix(layer, prop, index + 1)
                        value = Signal(self.mix(value.color, filtered.color, self.param(effect, 'strength')),
                                       value.alpha)
        return value

    def _anchor(self, identifier, channel=None):
        """(anchor, Signal) published at an anchor point; a missing anchor is transparent.

        ``channel`` selects the channel of a composite anchor (anchors.channel_of).
        """
        anchor = self.anchors.get(identifier)
        if anchor is None:
            return None, Signal((0., 0., 0.), 0)
        if anchor.kind == 'MASK':
            gray = self.mask(anchor.layer, through=identifier)
            return anchor, Signal(self.combine(gray, gray, gray), 1.0)
        if anchor.kind in COMPOSITE_KINDS:
            source = self._composite_source(anchor, channel)
            if not isinstance(source, tuple):
                return anchor, source
            key, export = source
            signal = self._import_composite(self.compile_unit(key), export)
            if export == 'Out':
                # A blended Out is the chain's premultiplied form in a non-opaque
                # context; composite.Composer.output publishes straight RGBA.
                signal = self._straight(signal)
            return anchor, signal
        return anchor, self.source(anchor.layer, anchor.channel, count=anchor.position)

    def _straight(self, signal):
        """Unpremultiply a Signal (a constant alpha of 1 is already straight)."""
        alpha = signal.alpha
        if not is_socket(alpha) and float(alpha) == 1.0:
            return signal
        factor = self.math('DIVIDE', self.math('GREATER_THAN', alpha, 1e-8), self.math('MAXIMUM', alpha, 1e-8))
        return Signal(self.vec('SCALE', signal.color, factor), alpha)

    def anchor_signal(self, identifier, channel=None):
        return self._anchor(identifier, channel)[1]

    def anchor_scalar(self, identifier, channel=None):
        """Mask coverage read from an anchor: red or luminance times alpha, like references.mask_value."""
        anchor, signal = self._anchor(identifier, channel)
        if anchor is None:
            return 0.0
        if anchor.kind == 'MASK':
            return self.mask(anchor.layer, through=identifier)
        gray = (luminance(self, signal.color) if channel_of(anchor, channel) in {'BASE_COLOR', 'EMISSION', 'NORMAL'}
                else self.separate(signal.color)[0])
        return self.math('MULTIPLY', gray, signal.alpha)

    def _anchor_dependencies(self, identifier, seen=frozenset(), channel=None):
        """Owners and repr-stable identity of everything an anchor publishes."""
        anchor = self.anchors.get(identifier)
        if anchor is None:
            return [], ('missing-anchor', identifier)
        if anchor.kind in COMPOSITE_KINDS:
            return self._composite_dependencies(anchor, channel_of(anchor, channel), seen)
        node = (anchor.layer_id, anchor.channel, anchor.position)
        if node in seen:
            raise ValueError('Anchor points contain a reference cycle')
        seen = seen | {node}
        if anchor.kind == 'MASK':
            owners, keys = self._mask_dependencies(list(anchor.layer.masks)[:anchor.position], seen)
            return owners, ('anchor-mask', keys)
        prop = channel_for(anchor.layer, anchor.channel)
        if prop is None:
            return [], ('missing-anchor', identifier)
        return self._channel_identity(anchor.layer, prop, anchor.position, seen)

    def _channel_identity(self, layer, prop, count, seen=frozenset()):
        """Exact inputs of a channel prefix, including anchored content it instantiates."""
        effects = list(prop.effects)[:count]
        owners = [prop, *effects]
        if getattr(layer, 'stroke_key', '') or prop.projection == 'DECAL':
            owners.append(layer)
        key = ('channel', layer.type, prop.channel,
               values(prop, excluded={'effects', 'enabled', 'opacity', 'blend_mode'}),
               tuple(values(e) for e in effects), self._content_stamp(layer, prop, effects))
        if layer.type == 'FILL' and prop.anchor:
            nested, nested_key = self._anchor_dependencies(prop.anchor, seen, prop.anchor_channel or prop.channel)
            owners.extend(nested)
            key += (('anchor', nested_key),)
        return owners, key

    def _composite_dependencies(self, anchor, channel, seen=frozenset()):
        """Owners and identity of a composite anchor: every contributing layer's
        blend state, content chain and mask stack for the selected channel."""
        from .anchors import contributors
        node = ('OUT' if anchor.kind == 'OUTPUT' else 'GROUP', anchor.layer_id, channel)
        if node in seen:
            raise ValueError('Published inputs contain a reference cycle')
        seen = seen | {node}
        owners, keys = [], []
        inside = contributors(self.ts, anchor)
        for layer in self.ts.layers:
            if layer.id not in inside:
                continue
            owners.append(layer)
            prop = channel_for(layer, channel)
            stamp = (layer.id, values(layer, excluded={'channels', 'masks'}))
            if prop is not None:
                stamp += ((prop.enabled, prop.opacity, prop.blend_mode),)
                if layer.type != 'FOLDER':
                    nested_owners, nested_key = self._channel_identity(layer, prop, len(prop.effects), seen)
                    owners.extend(nested_owners)
                    stamp += (nested_key,)
            nested_owners, nested_keys = self._mask_dependencies(list(layer.masks), seen)
            owners.extend(nested_owners)
            keys.append(stamp + (nested_keys,))
        return owners, ('anchor-composite', anchor.kind, anchor.layer_id, channel, tuple(keys))

    def _micro(self, mask, rehydrate=None):
        """(convex, cavity) from anchored micro details, or None without anchors.

        The strength-independent convexity raster is a CPU boundary: a flat
        node tree cannot sample a compiled anchor at neighbouring texels, and
        sharing generators/micro_details.encode keeps both evaluators identical.
        """
        from ..generators import micro_details as details
        refs = details.anchors(mask)
        if not refs:
            return None
        def owners():
            items = [mask]
            for slot, identifier in refs:
                items.extend(self._anchor_dependencies(identifier, channel=slot)[0])
            return items
        def identity():
            return ('micro-details', mask.detail_radius,
                    tuple((slot, identifier, self._anchor_dependencies(identifier, channel=slot)[1])
                          for slot, identifier in refs))
        def factory(signature):
            from .snapshots import capture
            snapshot = capture(mask)
            def parts():
                cache, resources = {}, {}
                height = slopes = None
                for low in range(0, self.n, 64):
                    high = min(low + 64, self.n)
                    rows_height, rows_slopes = details.fields(self.ts, snapshot, self.n, low, high, cache, resources)
                    if rows_height is not None:
                        if height is None:
                            height = np.full((self.n, self.n), .5, np.float32)
                        height[low:high] = rows_height
                    if rows_slopes is not None:
                        if slopes is None:
                            slopes = (np.zeros((self.n, self.n), np.float32), np.zeros((self.n, self.n), np.float32))
                        slopes[0][low:high], slopes[1][low:high] = rows_slopes
                    # Placeholder rows keep the strip budget honest while anchors evaluate.
                    yield low, np.full((high - low, self.n), .5, np.float32)
                if height is None and slopes is None:
                    encoded = np.full((self.n, self.n, 3), .5, np.float32)
                else:
                    encoded = details.encode(height, slopes, float(snapshot.detail_radius))
                yield from self._parts(encoded)
            return parts()
        if rehydrate is not None:
            return self._rebind(rehydrate, owners, identity, factory)
        with self.role('MicroDetails:' + self._path(mask)):
            red, green, _ = self.separate(self.boundary(self._role, owners, identity, factory,
                                                        ['micro', *self._owner_key(mask)[1:]]).color)
        signed = 0.0
        if any(slot == 'HEIGHT' for slot, _ in refs):
            signed = self.math('ADD', signed, self.math('MULTIPLY', self.math('SUBTRACT', red, .5),
                self.math('MULTIPLY', 2, self.param(mask, 'detail_height_strength'))))
        if any(slot == 'NORMAL' for slot, _ in refs):
            signed = self.math('ADD', signed, self.math('MULTIPLY', self.math('SUBTRACT', green, .5),
                self.math('MULTIPLY', 2, self.param(mask, 'detail_normal_strength'))))
        return self.clamp(signed), self.clamp(self.math('MULTIPLY', signed, -1))

    EDGE_MAP_KINDS = ('CURVATURE_THIN', 'CURVATURE', 'CURVATURE_WIDE', 'CURVATURE_XWIDE')

    def _edge_curvature(self, mask, red, layer=None):
        """Curvature at the mask's Edge Size, the CPU generator's blend across the edge-map set.

        With ``edge_source == 'LIVE'`` the edges are the live EDGE scene field of the
        mask's influence: a raster boundary the field engine republishes whenever the
        geometry changes, so Edge Size does not apply (reach = the field's distance).
        """
        if getattr(mask, 'edge_source', 'BAKED') == 'LIVE':
            return self._scene_boundary(layer, mask)
        if not all(any(m.kind == kind and m.image for m in self.ts.baked_maps) for kind in self.EDGE_MAP_KINDS):
            return red('CURVATURE')
        size = self.param(mask, 'edge_size')
        value = red(self.EDGE_MAP_KINDS[0])
        for index, kind in enumerate(self.EDGE_MAP_KINDS[1:]):
            t = self.clamp(self.math('SUBTRACT', size, float(index)))
            value = self.scalar_mix(value, red(kind), t)
        return value

    def generator(self, mask, layer=None):
        kind = 'NOISE' if mask.source == 'NOISE' else mask.generator
        if kind == 'RUNOFF' and layer is not None:
            return self._mask_source_boundary(layer, mask)
        if (kind in {'EDGE_WEAR', 'CHIPS'} and mask.edge_source == 'BAKED' and layer is not None
                and not mask.detail_height_anchor and not mask.detail_normal_anchor):
            # Four curvature reaches + optional AO + breakup/chip fields exceed
            # Metal's sampler allowance in otherwise modest materials. Resolve
            # only this editable generator through the existing versioned raster
            # boundary; levels, opacity and downstream composition remain live.
            return self._mask_source_boundary(layer, mask)
        amount, contrast = self.param(mask, 'amount'), self.param(mask, 'contrast')
        strength = self.param(mask, 'noise_strength')
        micro = self._micro(mask) if kind in {'EDGE_WEAR', 'CHIPS', 'DIRT', 'CAVITY_DIRT', 'RUST', 'DRIPS'} else None
        field = None
        def noise():
            nonlocal field
            if field is None:
                field = self._field(mask, 'FBM', generator=True)
            return field
        def threshold(value):
            return self.clamp(self.math('ADD', self.math('MULTIPLY',
                self.math('SUBTRACT', value, self.math('SUBTRACT', 1, amount)), contrast), .5))
        def breakup(value):
            return self.math('MULTIPLY', value, self.scalar_mix(1, noise(), strength))
        def red(name):
            return self.separate(self.mesh_map(name).color)[0]
        if kind == 'NOISE':
            return noise() if mask.source == 'NOISE' else threshold(noise())
        if kind in {'EDGE_WEAR', 'CHIPS'}:
            # Edge Size blends the baked edge-map set live; Edge Width then
            # narrows the chosen band: (curvature - (1 - w)) / w, both uniforms.
            width = self.param(mask, 'edge_width')
            band = self.clamp(self.math('DIVIDE', self.math('SUBTRACT', self.clamp(self._edge_curvature(mask, red, layer)),
                self.math('SUBTRACT', 1, width)), self.math('MAXIMUM', width, 1e-4)))
            if micro is not None:
                band = self.math('MAXIMUM', band, micro[0])
            value = self.clamp(self.math('MULTIPLY', self.math('MULTIPLY', band,
                self.math('MULTIPLY', 2, amount)), contrast))
            value = breakup(value)
            if any(m.kind == 'AO' and m.image for m in self.ts.baked_maps):
                value = self.math('MULTIPLY', value, self.clamp(red('AO')))
            if kind == 'CHIPS':
                chips = self._field(mask, 'VORONOI', generator=True)
                value = self.math('MULTIPLY', value, self.math('ADD', .15,
                    self.math('MULTIPLY', .85, threshold(chips))))
            return self.clamp(value)
        if kind == 'DIRT':
            ao, curvature = red('AO'), red('CURVATURE')
            if micro is not None:
                ao = self.math('MINIMUM', ao, self.math('SUBTRACT', 1, micro[1]))
                curvature = self.math('MAXIMUM', curvature, micro[0])
            cavity = self.math('SUBTRACT', 1, ao)
            value = self.math('MULTIPLY', cavity, self.math('ADD', .8,
                self.math('MULTIPLY', .2, self.math('SUBTRACT', 1, curvature))))
            value = self.math('ADD', value, self.math('SUBTRACT', amount, .5))
            value = self.math('ADD', value, self.math('MULTIPLY', self.math('SUBTRACT', noise(), .5), strength))
            return self.clamp(self.math('MULTIPLY', value, contrast))
        if kind in {'CAVITY_DIRT', 'RUST'}:
            cavity = self.clamp(self.math('SUBTRACT', 1, red('AO')))
            if micro is not None:
                cavity = self.math('MAXIMUM', cavity, micro[1])
            if kind == 'CAVITY_DIRT':
                return breakup(self.clamp(self.math('MULTIPLY', self.math('MULTIPLY', cavity,
                    self.math('ADD', 1, self.math('MULTIPLY', amount, 8))), contrast)))
            return breakup(threshold(self.math('ADD', noise(), self.math('MULTIPLY', cavity, .55))))
        if kind in {'DUST', 'WORLD_NORMAL', 'OBJECT_NORMAL'}:
            up = self.separate(self.mesh_map('OBJECT_NORMAL' if kind == 'OBJECT_NORMAL' else 'WORLD_NORMAL').color)[2]
            up = self.clamp(self.math('SUBTRACT', self.math('MULTIPLY', up, 2), 1))
            return breakup(self.clamp(self.math('MULTIPLY', self.math('MULTIPLY', up,
                self.math('MULTIPLY', amount, 2)), contrast)))
        if kind == 'SCRATCHES':
            return self.clamp(self.math('MULTIPLY', self.math('MULTIPLY',
                self._field(mask, 'SCRATCHES', generator=True), self.math('MULTIPLY', amount, 2)), contrast))
        if kind in {'MATERIAL_ID', 'OBJECT_ID', 'UV_ISLAND'}:
            distance = self.vec('DISTANCE', self.mesh_map(kind).color, self.color_param(mask, 'id_color'))
            tolerance = self.math('MAXIMUM', .001, self.math('MULTIPLY', amount, .1))
            return self.math('SUBTRACT', 1, self.math('GREATER_THAN', distance, tolerance))
        if kind == 'POSITION':
            return threshold(self.separate(self.mesh_map('POSITION').color)[2])
        if kind in {'THICKNESS', 'UV_BORDER', 'CURVATURE', 'AO'}:
            source = self.mesh_map(kind)
            value = self.separate(source.color)[0]
            if kind == 'AO':
                value = self.math('SUBTRACT', 1, value)
            return self.math('MULTIPLY', self.clamp(self.math('MULTIPLY', self.math('MULTIPLY', value,
                self.math('MULTIPLY', amount, 2)), contrast)), source.alpha)
        if kind == 'DRIPS':
            streaks = self._drips(mask)
            pos = self.separate(self.mesh_map('POSITION').color)[2]
            cavity = self.math('SUBTRACT', 1, red('AO'))
            if micro is not None:
                cavity = self.math('MAXIMUM', cavity, micro[1])
            return self.math('MULTIPLY', threshold(streaks), self.clamp(self.math('ADD',
                self.math('MULTIPLY', cavity, 3), self.math('MULTIPLY', amount, self.math('SUBTRACT', 1, pos)))))
        raise ValueError('Unknown generator: ' + kind)

    def _drips(self, mask, rehydrate=None):
        # Domain-warped legacy streak hash needs just one cached scalar field;
        # its threshold, amount and cavity mix remain ordinary uniforms.
        def identity():
            return ('drip-field', mask.noise_scale, mask.seed,
                tuple((m.kind, image_key(m.image)) for m in self.ts.baked_maps if m.kind == 'POSITION'))
        def factory(signature):
            _, scale, seed, _ = signature
            def parts():
                from ..texture_engine.procedurals import evaluate
                from .mask_eval import map_pixels
                from .blend_modes import sample_uv
                cache = {}
                u = (np.arange(self.n, dtype=np.float32)[None, :] + .5) / self.n
                for low in range(0, self.n, 16):
                    v = (np.arange(low, min(low + 16, self.n), dtype=np.float32)[:, None] + .5) / self.n
                    pos = sample_uv(map_pixels(self.ts, 'POSITION', cache), u, v)
                    yield low, evaluate('STREAKS', pos[..., 0] + pos[..., 1] * .731, pos[..., 2], scale=scale, seed=seed)
            return parts()
        if rehydrate is not None:
            return self._rebind(rehydrate, [mask], identity, factory)
        with self.role('SourceField:' + self._path(mask) + ':DRIPS'):
            return self.separate(self.boundary(self._role, [mask], identity, factory,
                                               ['drips', *self._owner_key(mask)[1:]]).color)[0]

    def _mask_source_boundary(self, layer, mask, rehydrate=None):
        def identity():
            if mask.source == 'SCENE':
                return ('scene-field-gutters-v1', self._scene_identity(mask.influence))
            if mask.source == 'STROKES':
                from ..painting.strokes import stroke_identity
                return ('strokes-mask', mask.stroke_key, stroke_identity(self.scene.open_painter, mask.stroke_key, self.ts),
                        self._geometry_stamp(True))
            if mask.source == 'MESH':
                from .mesh_masks import sampling_signature as mesh_signature
                return ('mesh-mask', mesh_signature(self.ts, mask), self._geometry_stamp(True))
            if mask.source == 'PROCEDURAL':
                from .material_space import identity as metric_identity, enabled
                return ('legacy-mask-field', mask.procedural, mask.noise_scale, mask.seed,
                        mask.projection, tuple(mask.scale), mask.rotation, tuple(mask.offset),
                        mask.tiling, mask.triplanar_hardness, metric_identity(mask),
                        self._geometry_stamp(mask.projection == 'TRIPLANAR' or enabled(mask)))
            return ('mask-source', values(mask, excluded={'black', 'white', 'invert', 'strength', 'operation', 'enabled'}),
                    self._geometry_stamp(mask.projection == 'TRIPLANAR' or mask.source == 'SCENE'),
                    tuple((m.kind, image_key(m.image)) for m in self.ts.baked_maps))
        def factory(signature):
            from .snapshots import capture
            snapshot = capture(mask).replaced(black=0., white=1., invert=False, strength=1., enabled=True)
            source_layer = capture(layer).replaced(masks=(snapshot,))
            def parts():
                from .mask_eval import _evaluate_masks
                cache, geometry = {}, {}
                for low in range(0, self.n, 16):
                    yield low, _evaluate_masks(self.ts, source_layer, self.n, low, min(low + 16, self.n), cache, geometry)
            return parts()
        def owners():
            return [mask, mask.influence, mask.mesh, *self._stroke_layers(mask)]
        if rehydrate is not None:
            return self._rebind(rehydrate, owners, identity, factory)
        return self.separate(self.boundary(self._role, owners, identity, factory,
                                           ['mask_source', layer.id, mask.id]).color)[0]

    def _stroke_layers(self, mask):
        """Stroke edits notify the paint layers rendering a key: a Vector Strokes
        mask reading that key is re-identified through them."""
        if mask.source != 'STROKES' or not mask.stroke_key:
            return []
        from ..painting.strokes import linked_layers
        return [layer for _, layer in linked_layers(self.scene.open_painter, mask.stroke_key)]

    def _scene_identity(self, field):
        falloff = getattr(field, 'falloff', 'LINEAR')
        if field.mode == 'MANUAL':
            from ..generators.scene_fields import signature
            # First analysis publishes this same provenance token. Its derived
            # image allocation must not invalidate a later Levels-only edit.
            return (field.mode, field.update_serial,
                    field.provenance or repr(signature(self.ts, field)), falloff)
        if field.mode == 'LIVE':
            from . import field_jobs
            if field_jobs.active():
                # The published raster is the identity: the field engine analyses
                # off the evaluation path and republishes (new image) when done.
                # Unpublished fields must not all share the same empty resource:
                # its first factory only queues analysis for one owning mask.
                inputs = values(field, excluded={'image', 'provenance', 'error'})
                from ..core.layer_store import scene_path
                return (field.mode, scene_path(field), inputs, field.provenance, image_key(field.image), falloff)
            from ..generators.scene_fields import signature
            return (field.mode, signature(self.ts, field), falloff)
        return (field.mode, image_key(field.image), field.update_serial, falloff)

    def _scene_boundary(self, layer, mask, rehydrate=None):
        """Raw scene-field raster of a mask's influence (live edges), as a boundary."""
        def identity():
            return ('scene-edge-gutters-v1', self._scene_identity(mask.influence))
        def factory(signature):
            from .snapshots import capture
            snapshot = capture(mask)
            def parts():
                from ..generators.scene_fields import evaluate
                cache = {}
                n = self.n
                for low in range(0, n, 16):
                    high = min(low + 16, n)
                    u, v = np.meshgrid((np.arange(n, dtype=np.float32) + .5) / n,
                                       (np.arange(low, high, dtype=np.float32) + .5) / n)
                    value = evaluate(self.ts, snapshot, u, v, cache)
                    rows = np.ones((high - low, n, 4), np.float32)
                    rows[..., :3] = np.asarray(value, np.float32)[..., None]
                    yield low, rows
            return parts()
        owners = [mask, mask.influence]
        if rehydrate is not None:
            return self._rebind(rehydrate, owners, identity, factory)
        with self.role(f'SceneEdge:{layer.id}:{mask.id}'):
            return self.separate(self.boundary(self._role, owners, identity, factory,
                                               ['scene_edge', layer.id, mask.id]).color)[0]

    def _mask_dependencies(self, masks, seen=frozenset()):
        """Exact published source stages used by an isolated mask prefix.

        Traverse authoring inputs rather than the texture-set serial: unrelated
        channels and masks beyond a published stage cannot invalidate this leaf.
        """
        from .dependencies import map_dependencies
        from .anchors import consumed_channels
        from .operation_contracts import surface
        owners, keys = [], []
        for mask in masks:
            if not mask.enabled or mask.source == 'ANCHOR':
                continue
            owners.extend((mask, mask.effect, mask.influence, mask.mesh))
            stamp = values(mask, excluded={'influence'})
            if mask.source == 'SCENE' or (mask.source == 'GENERATOR' and getattr(mask, 'edge_source', 'BAKED') == 'LIVE'):
                stamp += (('scene', self._scene_identity(mask.influence)),)
            if mask.source == 'MESH':
                from .mesh_masks import sampling_signature as mesh_signature
                stamp += (('mesh', mesh_signature(self.ts, mask)),)
            if mask.source == 'GRAPH':
                # A Mask Graph evaluates on the CPU from its values; a legacy shader group is baked
                # over the assigned meshes (custom_graph.evaluate).
                from .custom_graph import signature as graph_signature
                stamp += (('graph', graph_signature(mask.node_group), self._geometry_stamp(True)),)
            if mask.source == 'STROKES':
                from ..painting.strokes import stroke_identity
                owners.extend(self._stroke_layers(mask))
                stamp += (('strokes', stroke_identity(self.scene.open_painter, mask.stroke_key, self.ts), self._geometry_stamp(True)),)
            if mask.source == 'FILTER' and surface(mask.effect):
                # Surface-space filters pad each UV island from the set's seam map.
                stamp += (('geometry', self._geometry_stamp(True)),)
            required = map_dependencies(mask)
            stamp += (('maps', tuple((m.kind, image_key(m.image)) for m in self.ts.baked_maps
                                    if required is None or m.kind in required)),)
            if mask.projection == 'TRIPLANAR':
                stamp += (('geometry', self._geometry_stamp(True)),)
            if mask.source == 'REFERENCE' and not mask.reference_anchor:
                target = next((l for l in self.ts.layers if l.id == mask.reference_layer), None)
                if target is None:
                    stamp += (('missing', mask.reference_layer),)
                elif mask.reference_channel == 'MASK':
                    prefix, through = [], len(target.masks)
                    for index, item in enumerate(target.masks):
                        if mask.reference_stage != 'PRE_FILTER' or item.source != 'FILTER':
                            prefix.append(item)
                        if item.id == mask.reference_mask:
                            through = index + 1
                            break
                    node = (target.id, 'MASK', through)
                    if node in seen:
                        raise ValueError('Published inputs contain a reference cycle')
                    nested_owners, nested_keys = self._mask_dependencies(prefix, seen | {node})
                    owners.extend(nested_owners)
                    stamp += (('published-mask', nested_keys),)
                else:
                    prop = channel_for(target, mask.reference_channel)
                    if prop:
                        count = 0 if mask.reference_stage == 'PRE_FILTER' else len(prop.effects)
                        nested_owners, nested_key = self._channel_identity(target, prop, count, seen)
                        owners.extend(nested_owners)
                        stamp += (('published-channel', nested_key),)
            for identifier, requested in consumed_channels(mask):
                nested_owners, nested_key = self._anchor_dependencies(identifier, seen, requested)
                owners.extend(nested_owners)
                stamp += (('anchor', identifier, nested_key),)
            keys.append(stamp)
        return owners, tuple(keys)

    def _mask_prefix(self, layer, masks, pre_filter=False, rehydrate=None):
        # Freeze the selected prefix before the caller continues appending masks.
        masks = tuple(masks)
        def owners():
            items, _ = self._mask_dependencies(masks[:-1])
            items.extend((masks[-1], masks[-1].effect))
            return items
        def identity():
            from .operation_contracts import surface
            return ('mask-filter-prefix', self._mask_dependencies(masks[:-1])[1],
                    values(masks[-1].effect, excluded={'strength'}),
                    self._geometry_stamp(surface(masks[-1].effect)))
        def factory(signature):
            from .snapshots import capture
            last = capture(masks[-1]).replaced(black=0., white=1., invert=False, strength=1.,
                                              effect=capture(masks[-1].effect).replaced(strength=1.))
            snapshot = capture(layer).replaced(masks=tuple(capture(m) for m in masks[:-1]) + (last,))
            def parts():
                from .mask_eval import evaluate_masks
                from .operation_contracts import mask_footprint
                cache, geometry = {}, {}
                rows = _band_rows(mask_footprint(snapshot.masks), self.n)
                # Neighborhood operators consume exactly this prefix, never the
                # final material or unrelated layer/channel sources.
                for low in range(0, self.n, rows):
                    yield low, evaluate_masks(self.ts, snapshot, self.n, low,
                                              min(low + rows, self.n), cache, geometry)
                    # Each band is filtered once and not retained for the next.
                    for key in [k for k in geometry if isinstance(k, tuple) and k and k[0] == 'mask_stack']:
                        del geometry[key]
            return parts()
        if rehydrate is not None:
            return self._rebind(rehydrate, owners, identity, factory)
        with self.role(f'RasterMaskPrefix:{layer.id}:{masks[-1].id}'):
            return self.separate(self.boundary(self._role, owners, identity, factory,
                                               ['mask_prefix', layer.id, masks[-1].id, bool(pre_filter)]).color)[0]

    def _mask_walk(self, masks, start, stop, pre_filter, prefix):
        """The entries of [start, stop) a CPU-rastered filter reads, as ``_mask_chain``
        accumulates them: active entries, an active group with its member run,
        nothing of a disabled group's run."""
        from ..core.mask_stack import block
        index = start
        while index < stop:
            mask = masks[index]
            index += 1
            if not self._mask_active(mask, pre_filter):
                if mask.source == 'GROUP':
                    index = min(block(masks, index - 1)[1], stop)
                continue
            prefix.append(mask)
            if mask.source == 'GROUP':
                run_stop = min(block(masks, index - 1)[1], stop)
                self._mask_walk(masks, index, run_stop, pre_filter, prefix)
                index = run_stop
        return prefix

    def _run_prefix(self, masks, stop, pre_filter):
        """What a filter at ``stop - 1`` rasters: its innermost group's run up to it,
        or the whole outer stack for a top-level entry (never the outer entries of
        a member, which the CPU reference folds after the group)."""
        parent = getattr(masks[stop - 1], 'parent_id', '')
        start = next((i + 1 for i, m in enumerate(masks[:stop]) if parent and m.id == parent), 0)
        return self._mask_walk(masks, start, stop, pre_filter, [])

    def _mask_chain(self, layer, masks, start, stop, pre_filter, result, prefix):
        """Fold entries [start, stop) into ``result``; a GROUP folds its member run first (0.9.3)."""
        from ..core.mask_stack import block
        index = start
        while index < stop:
            mask = masks[index]
            if mask.source == 'GROUP':
                _, run_stop = block(masks, index)
                run_stop = min(run_stop, stop)
                if self._mask_active(mask, pre_filter):
                    prefix.append(mask)
                    # Members raster their own run only (mask_eval._stack evaluates the
                    # run from nothing and folds the group into the outer result after).
                    run = []
                    value = self._mask_chain(layer, masks, index + 1, run_stop, pre_filter, None, run)
                    prefix.extend(run)
                    if value is None:
                        value = 1.0
                    result = self._mask_fold(layer, mask, value, result)
                index = run_stop
                continue
            index += 1
            if not self._mask_active(mask, pre_filter):
                continue
            prefix.append(mask)
            with self.role(f'MaskSource:{layer.id}:{mask.id}'):
                value = self._mask_value(layer, mask, result, prefix, pre_filter)
            result = self._mask_fold(layer, mask, value, result)
        return result

    def _mask_fold(self, layer, mask, value, result):
        """Levels, invert and the entry's blend operation."""
        with self.role(f'MaskLevels:{layer.id}:{mask.id}'):
            black, white = self.param(mask, 'black'), self.param(mask, 'white')
            value = self.clamp(self.math('DIVIDE', self.math('SUBTRACT', value, black),
                self.math('MAXIMUM', self.math('SUBTRACT', white, black), 1e-6)))
            value = self.scalar_mix(value, self.math('SUBTRACT', 1, value), self.param(mask, 'invert'))
        with self.role(f'MaskMix:{layer.id}:{mask.id}'):
            strength = self.param(mask, 'strength')
            if result is None:
                return self.scalar_mix(1, value, strength)
            if mask.source == 'FILTER' or mask.operation == 'NORMAL':
                return self.scalar_mix(result, value, strength)
            if mask.operation == 'MULTIPLY':
                return self.math('MULTIPLY', result, self.scalar_mix(1, value, strength))
            if mask.operation in {'MAX', 'MIN'}:
                return self.scalar_mix(result, self.math('MAXIMUM' if mask.operation == 'MAX' else 'MINIMUM',
                                                         result, value), strength)
            return self.clamp(self.math('ADD', result, self.math('MULTIPLY', value, strength)))

    def _mask_value(self, layer, mask, result, prefix, pre_filter):
        """Raw coverage of one non-group entry (filters read the running result)."""
        if mask.source == 'FILTER':
            if result is None:
                result = 1.0
            if not mask.effect.enabled and not is_pointwise(mask.effect.kind):
                return result
            self._compiled_effects[mask.effect.as_pointer()] = mask.effect
            self.unit.compiled_effects[mask.effect.as_pointer()] = mask.effect
            try:
                filtered = apply_effect(self, Signal(result), mask.effect)
                return luminance(self, filtered.color)
            except UnsupportedEffect:
                filtered = self._mask_prefix(layer, prefix, pre_filter)
                return self.scalar_mix(result, filtered, self.param(mask.effect, 'strength'))
        if mask.source == 'REFERENCE':
            if mask.reference_anchor:
                return self.anchor_scalar(mask.reference_anchor, mask.reference_channel)
            target = self._layer(mask.reference_layer)
            if target is None:
                return 0.0
            if mask.reference_channel == 'MASK':
                return self.mask(target, mask.reference_mask, mask.reference_stage == 'PRE_FILTER')
            sample = self.source(target, mask.reference_channel, 0 if mask.reference_stage == 'PRE_FILTER' else None)
            gray = (luminance(self, sample.color) if mask.reference_channel in {'BASE_COLOR', 'EMISSION', 'NORMAL'}
                    else self.separate(sample.color)[0])
            return self.math('MULTIPLY', gray, sample.alpha)
        if mask.source == 'TEXT':
            from .text_source import ensure
            getter = lambda: ensure(mask.text, int(self.ts.resolution))
            return self.separate(self.image(getter(), getter=getter, owner=mask).color)[0]
        if mask.source in {'FILL', 'PAINT'}:
            sample = (self.projected(mask, lambda uv: self.image(mask.image, uv, mask.filtering, mask.tiling,
                                                getter=lambda mask=mask: mask.image, owner=mask))
                      if mask.source == 'FILL' else self.image(mask.image, getter=lambda mask=mask: mask.image, owner=mask))
            return self.separate(sample.color)[0]
        if mask.source == 'BAKED':
            return self.separate(self.mesh_map(mask.map_type).color)[0]
        if mask.source in {'GENERATOR', 'NOISE'}:
            return self.generator(mask, layer)
        if mask.source == 'GRAPH':
            key = self._graph_key(layer, mask)
            if key is not None:
                # A Mask Graph is its own unit: the mask samples its result.
                return self._import_mask(self.compile_unit(key))
            return self._custom_graph(mask)
        if mask.source == 'PROCEDURAL':
            from ..texture_engine.procedurals import ALIASES
            from .material_space import enabled
            kind = ALIASES.get(mask.procedural, mask.procedural)
            if kind in self.ANALYTIC and not enabled(mask):
                scale = self.param(mask, 'noise_scale')
                sample = self.projected(mask, lambda uv: Signal(self.analytic(kind, uv, scale)))
                value = self.separate(sample.color)[0]
                if mask.projection == 'UV' and mask.tiling == 'NONE':
                    value = self.math('MULTIPLY', value, self._coverage(self.coordinates(mask)))
                return value
            return self._mask_source_boundary(layer, mask)
        return self._mask_source_boundary(layer, mask)

    def _native_graph(self, layer, mask):
        """A Mask Graph's coverage; the material graph samples the CPU evaluation (the engine
        compiles the graph to GPU code instead, ``EngineGraph._native_graph``)."""
        return self._mask_source_boundary(layer, mask)

    def _custom_graph(self, mask):
        """Instance the artist's shader node group; UV/Position/Normal inputs wire by name (0.9.3)."""
        tree = mask.node_group
        if tree is None or tree.bl_idname != 'ShaderNodeTree':
            raise ValueError(f'Mask "{mask.name}" needs a shader node group')
        outputs = [item for item in tree.interface.items_tree if item.item_type == 'SOCKET' and item.in_out == 'OUTPUT']
        if not outputs:
            raise ValueError(f'Node group "{tree.name}" has no output socket')
        node = self.node('ShaderNodeGroup')
        node.node_tree = tree
        node['op_custom_graph'] = tree.name
        from .custom_graph import COORDINATE_INPUTS
        for socket in node.inputs:
            name = socket.name.lower()
            if name in COORDINATE_INPUTS:
                self.link(self.coord_uv(), socket)
            elif name == 'position':
                self.link(self.coord_position(), socket)
            elif name == 'normal':
                self.link(self.coord_normal(), socket)
        output = node.outputs[0]
        if output.type in {'RGBA', 'VECTOR'}:
            return self.separate(output)[0]
        return output

    def mask(self, layer, through='', pre_filter=False):
        """Mask coverage after the item `through` (inclusive) or the whole stack.

        Every published prefix is its own unit, so an anchor prefix and the
        owning layer share one chain; a first-item mix stays distinct from an
        all-ones backdrop until the public result is returned.
        """
        key = self._mask_key(layer, through, pre_filter)
        if not isinstance(key, tuple):
            return key
        if self.unit is None or self.unit.key != key:
            return self._import_mask(self.compile_unit(key))
        masks = list(layer.masks)
        stop = len(masks) if not key[2] else next(i + 1 for i, m in enumerate(masks) if m.id == key[2])
        result, start = None, 0
        continued = self._continuation(key)
        if continued is not None:
            producer = self.compile_unit(continued)
            result = self._import_mask(producer, public=False)
            start = next(i + 1 for i, m in enumerate(masks) if m.id == continued[2]) if continued[2] else len(masks)
        prefix = self._mask_walk(masks, 0, start, pre_filter, [])
        result = self._mask_chain(layer, masks, start, stop, pre_filter, result, prefix)
        self.unit.flags['empty'] = result is None
        return 1.0 if result is None else result

    # ---- units ---------------------------------------------------------------

    def _unit_name(self, key):
        layer = self._layer(key[1])
        name = layer.name[:24] if layer is not None else key[1][:8]
        return 'OP ' + ' · '.join(str(part) for part in (key[0], name, *key[2:]))

    def _base_owners(self):
        """The set and its baked maps are owners of every unit: addressed once per plan."""
        plan = self._plan
        base = getattr(plan, 'base_owners', None)
        if base is None:
            base = tuple(item.as_pointer() for item in (self.ts, *self.ts.baked_maps))
            if plan is not None:
                plan.base_owners = base
        return base

    def _unit_owners(self, key):
        """RNA items whose addresses this unit's bindings and closures capture."""
        base = self._base_owners()
        layer = self._layer(key[1])
        owners = [layer]
        if key[0] == 'CHANNEL':
            prop = self._channel(layer, key[2])
            if prop is not None:
                owners.append(prop)
            if self._cache_effective(layer, key[2]) != 'LIVE':
                row = self._cache_row(layer, key[2])
                if row is not None:
                    owners.append(row)
        elif key[0] == 'SRC':
            prop = self._channel(layer, key[2])
            owners.append(prop)
            owners.extend(list(prop.effects)[:key[3]])
        elif key[0] == 'FIELD':
            owners.append(self._channel(layer, key[2]))
        elif key[0] == 'GRAPH':
            from ..procedural_graph.program import GraphError, build
            mask = next(m for m in layer.masks if m.id == key[2])
            owners.append(mask)
            try:
                program = build(mask.node_group)
            except GraphError:
                program = None
            if program is not None:
                owners.extend((mask.node_group, program.output))
                if program.result[0] == 'socket':
                    owners.append(program.result[1])
                for step in program.steps:
                    owners.append(step.node)
                    owners.extend(step.node.stops if step.kind == 'ramp' else ())
                    owners.extend(ref[1] for ref in step.inputs.values() if ref[0] == 'socket')
        else:
            masks = list(layer.masks)
            stop = len(masks) if not key[2] else next(i + 1 for i, m in enumerate(masks) if m.id == key[2])
            for mask in masks[:stop]:
                owners.extend((mask, mask.effect, mask.influence, mask.mesh))
        return base + tuple(item.as_pointer() for item in owners)

    def _adopt(self, previous, key):
        """Move an unchanged unit out of the previous graph instead of recompiling."""
        unit = previous.units.get(key) if previous is not None else None
        if unit is None or unit.tree is None:
            return None
        if previous.n != self.n:
            # Boundary factories and identities are closures over the graph they were compiled
            # in, so an adopted unit would keep rastering at the previous resolution.
            return None
        try:
            unit.tree.users   # a tree removed as an orphan is recompiled, not adopted
            if unit.signature != self._unit_signature(key, stub=unit.flags.get('stub')):
                return None
            # Collection reallocation moves PropertyGroups; captured references
            # are only valid while every owner still lives at its old address.
            current = self._unit_owners(key)
            if self._undo_moves is not None:
                # After an undo every struct moved (an address may even repeat): re-point always.
                if not self._follow_undo(unit, key, current):
                    return None
            elif unit.owner_pointers != current and not self._follow_moved_layer(unit, key, current):
                return None
            if _removed_graph_units(unit.bindings.values()):
                return None   # a Mask Graph binding lost its node: compile afresh, never refresh it
        except (ValueError, StopIteration, ReferenceError):
            return None
        # Its published inputs must exist in this graph too (adopted or rebuilt),
        # with the export shapes this tree was linked against: a producer whose
        # export turned into a constant (or a baked literal into a socket) has
        # no socket to wire by name, so the consumer is recompiled instead.
        self._building_units.add(key)
        try:
            for producer_key, export_name, _ in unit.imports:
                if self.compile_unit(producer_key).exports.get(export_name) != ('socket', export_name):
                    return None
            for producer_key, export_name, payload in unit.const_imports:
                export = self.compile_unit(producer_key).exports.get(export_name)
                if export is None or export[0] != 'const' or not _same_literal(export[1], payload):
                    return None
        finally:
            self._building_units.discard(key)
        # Everything taken from the previous graph is handed back if this build fails.
        self._adopted.append((key, unit, unit.instance,
                              [(b, b.node.image, b.pending_image) for b in unit.resource_bindings]))
        del previous.units[key]
        unit.instance = None
        for binding in unit.resource_bindings:
            if binding.owner_factory is not None:
                binding.owners = {o.as_pointer() for o in binding.owner_factory()}
            held = binding.node.image
            # A finished image is adopted into this graph's entries (cache hit), so
            # a later re-identification leaves it to the eviction budget; the
            # previous graph closes its unfinished jobs, so those are queued again.
            # A raster that failed is inherited with its error, not run again
            # (0.9.8): it is retried only once an edit re-identifies the input
            # or Retry forgets the failure, not on every structural rebuild.
            self._request(binding, binding.signature, previous=previous)
            if held is not None and held.get(RESOURCE) and bound(held):
                # The leaf the node still shows (the previous value, or an
                # interrupted placeholder) follows the binding into this table,
                # so it is released once the binding moves on. Never removed
                # here: a rolled-back build hands it back to the node.
                self.resources.adopt(held)
        self.bindings.update(unit.bindings)
        for binding in unit.bindings.values():
            for pointer in binding.pointers():
                self.owner_bindings[pointer].append(binding)
        self.resource_bindings.extend(unit.resource_bindings)
        self.image_bindings.extend(unit.image_bindings)
        for binding in unit.cache_bindings:
            if binding.owner_factory is not None:
                binding.owners = {o.as_pointer() for o in binding.owner_factory()}
        self.cache_bindings.extend(unit.cache_bindings)
        self._compiled_effects.update(unit.compiled_effects)
        self._plan.keys.add(key)
        self.units[key] = unit
        self._reused.append(key)
        return unit

    def _follow_moved_layer(self, unit, key, current=None, tables=False):
        """An unchanged unit whose layer struct moved in the stack's array: a layer inserted,
        removed or moved below it, or the array grown. Only the layer group moves; its channels,
        masks and effects keep arrays of their own. Re-pointing what the unit captured of the old
        struct adopts it instead of recompiling it (an insert mid-stack moved every layer above).
        False, recompiling it, unless the layer is the only owner that moved and nothing still
        holds the old struct afterwards. ``tables``: the unit is bound in this graph, whose
        tables follow its re-pointed bindings too (image entries are left to the caller)."""
        current = self._unit_owners(key) if current is None else current
        before = unit.owner_pointers
        at = len(self._plan.base_owners)
        if (len(current) != len(before) or current[:at] != before[:at]
                or current[at + 1:] != before[at + 1:]):
            return False
        layer, old = self._layer(key[1]), before[at]
        if layer is None or layer.as_pointer() != current[at]:
            return False
        if not unit.holds_layer:
            unit.owner_pointers = current   # nothing it holds refers to the struct that moved
            return True
        new_pointer, seen = current[at], set()
        rebound, moved = {}, []
        for identifier, binding in unit.bindings.items():
            if binding.owner.as_pointer() == old:
                binding.owner = layer
                before_identifier = identifier
                if isinstance(identifier, tuple) and len(identifier) > 1 and identifier[1] == old:
                    identifier = (identifier[0], new_pointer, *identifier[2:])
                moved.append((before_identifier, identifier, binding))
            rebound[identifier] = binding
        unit.bindings = rebound
        if tables and moved:
            listed = self.owner_bindings.get(old)
            gone = {id(binding) for _, _, binding in moved}
            if listed:
                listed[:] = [binding for binding in listed if id(binding) not in gone]
                if not listed:
                    del self.owner_bindings[old]
            for before_identifier, identifier, binding in moved:
                if self.bindings.get(before_identifier) is binding:
                    del self.bindings[before_identifier]
                self.bindings[identifier] = binding
                self.owner_bindings[new_pointer].append(binding)
        for binding in (*unit.resource_bindings, *unit.cache_bindings):
            for name in ('identity', 'factory', 'owner_factory'):
                _repoint(getattr(binding, name, None), old, layer, seen)
            if old in binding.owners:
                binding.owners = (set(binding.owners) - {old}) | {new_pointer}
        entries = []
        for pointer, role, getter, node, image in unit.image_bindings:
            _repoint(getter, old, layer, seen)
            entries.append((new_pointer if pointer == old else pointer, role, getter, node, image))
        unit.image_bindings = entries
        checked = set()
        if (any(binding.owner.as_pointer() == old for binding in unit.bindings.values())
                or any(_captures(getattr(binding, name, None), old, checked)
                       for binding in (*unit.resource_bindings, *unit.cache_bindings)
                       for name in ('identity', 'factory', 'owner_factory'))
                or any(_captures(entry[2], old, checked) for entry in unit.image_bindings)):
            return False
        unit.owner_pointers = current
        return True

    def _follow_undo(self, unit, key, current):
        """``_follow_moved_layer`` after a memfile undo, which reallocated every struct of the set
        and invalidated every ID wrapper. Each captured owner is re-pointed to the struct now at
        its path (``_undo_moves``), image nodes to their datablocks by session id. False,
        recompiling the unit, when it holds anything else from before the undo: a resource or
        cache binding, a Mask Graph, a decal object or custom node group (IDs ``rna_nodes`` holds,
        which no path maps) or a GPU resource. A node's own RNA (a filter's effect) is re-pointed."""
        moves, moved, before = self._undo_moves, self._undo_moved, unit.owner_pointers
        if (len(before) != len(current) or unit.resource_bindings or unit.cache_bindings
                or any(binding.native for binding in unit.bindings.values())):
            return False
        try:
            if tuple(map(moved.__getitem__, before)) != current:
                return False
            images, attributes = [], []
            for node in unit.tree.nodes.image_nodes:
                held = node.__dict__
                if held.get('_gpu_resource') is not None:
                    return False
                if held.get('_image') is not None:
                    image = self._undo_images.get(held.get('_image_uid'))
                    if image is None:
                        return False
                    images.append((node, image))
            for node in unit.rna_nodes:
                for name, value in node.__dict__.items():
                    if name not in _NODE_REFERENCES and isinstance(value, bpy.types.bpy_struct):
                        attributes.append((node, name, moves[value.as_pointer()]))   # an ID: KeyError
            rebound = []
            for identifier, binding in unit.bindings.items():
                owner = moves[binding.owner.as_pointer()]
                aliases = tuple(map(moved.__getitem__, binding.aliases))
                if isinstance(identifier, tuple) and len(identifier) > 1 and isinstance(identifier[1], int):
                    identifier = (identifier[0], moved[identifier[1]], *identifier[2:])
                rebound.append((identifier, binding, owner, aliases))
            effects = {moved[pointer]: moves[pointer] for pointer in unit.compiled_effects}
            entries = [(moved[entry[0]], *entry[1:]) for entry in unit.image_bindings]
        except (KeyError, ReferenceError):
            return False
        seen, fresh = set(), self._undo_fresh
        for entry in unit.image_bindings:
            _repoint_many(entry[2], moves, seen)
        checked = set()
        if any(_holds_foreign(entry[2], fresh, checked) for entry in unit.image_bindings):
            return False   # the unit is recompiled; what was re-pointed goes with the retired graph
        unit.bindings = {}
        for identifier, binding, owner, aliases in rebound:
            binding.owner, binding.aliases = owner, aliases
            unit.bindings[identifier] = binding
        unit.compiled_effects = effects
        unit.image_bindings = entries
        for node, image in images:
            node.image = image
        for node, name, struct in attributes:
            setattr(node, name, struct)
        unit.owner_pointers = current
        return True

    def verify_undo(self):
        """``OP_VERIFY_UNDO``: every unit adopted after an undo equals a fresh compile of it."""
        from . import metrics
        fresh, active = type(self)(self.scene, self.ts), metrics._active
        metrics._active = None   # the check's own compile counts toward nothing a test asserts
        try:
            fresh.build()
        finally:
            metrics._active = active
        try:
            for key in self._reused:
                mine, theirs = _unit_shape(self.units[key]), _unit_shape(fresh.units[key])
                if mine != theirs:
                    field = next(name for name in mine if mine[name] != theirs[name])
                    a, b = mine[field], theirs[field]
                    if isinstance(a, list) and isinstance(b, list):
                        at = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
                        a, b = (f'[{at}] of {len(a)}: {a[at] if at < len(a) else None!r}',
                                f'[{at}] of {len(b)}: {b[at] if at < len(b) else None!r}')
                    raise AssertionError(f'Unit {key} adopted after an undo differs from its compile in {field}: '
                                         f'{a!s:.600} != {b!s:.600}')
        finally:
            fresh.close()

    def _rollback_adoption(self):
        """A failed build hands adopted units back to the graph they came from."""
        previous = self._previous
        for key, unit, instance, images in self._adopted:
            self.units.pop(key, None)
            if previous is not None:
                previous.units[key] = unit
            unit.instance = instance
            for binding, node_image, pending in images:
                binding.node.image, binding.pending_image = node_image, pending
                if previous is not None:
                    # An inherited failure returns to the table that still
                    # serves the binding, or closing this candidate's table
                    # would take its placeholder from under the previous graph.
                    self.resources.restore(binding.signature, previous.resources)
        self._adopted = []
        if previous is not None and self._head[0] is self:
            self._head[0] = previous

    def compile_unit(self, key):
        """Compile one unit (and, recursively, every unit it imports)."""
        unit = self.units.get(key)
        if unit is not None:
            return unit
        if key in self._building_units:
            raise ValueError('Published inputs contain a reference cycle')
        unit = self._adopt(self._previous, key)
        if unit is not None:
            return unit
        self._building_units.add(key)
        started = perf_counter()
        layer = self._layer(key[1])
        try:
            if key[0] == 'CHANNEL' and self._skipped(layer):
                # Bypassed (or inside a cached folder): the backdrop passes
                # through and invalid content never raises.
                unit = self._compile(key, layer, bypass=True)
            else:
                unit = self._compile(key, layer)
        except ValueError as error:
            if key[0] != 'CHANNEL' or self._visible(layer):
                self._building_units.discard(key)
                raise
            # A hidden layer's invalid content passes its backdrop through; the
            # stub is replaced, and the error surfaced, when the layer is shown.
            unit = self._compile(key, layer, stub=str(error))
        unit.owner_pointers = self._unit_owners(key)
        pointer = layer.as_pointer() if layer is not None else None
        unit.holds_layer = bool(unit.resource_bindings or unit.cache_bindings or unit.image_bindings) or any(
            binding.owner.as_pointer() == pointer for binding in unit.bindings.values())
        # An image getter closes over the channel, mask or effect it reads, which stay where they
        # are when the layer's struct moves; resource and cache closures are followed regardless.
        seen = set()
        unit.captures_layer = bool(unit.resource_bindings or unit.cache_bindings) or any(
            entry[0] == pointer or _captures(entry[2], pointer, seen, unfilled=True) for entry in unit.image_bindings)
        unit.signature = self._unit_signature(key, stub=unit.flags.get('stub'))
        if not isinstance(unit.tree, bpy.types.ID):
            unit.rna_nodes = tuple(node for node in unit.tree.nodes if any(
                isinstance(value, bpy.types.bpy_struct) for name, value in node.__dict__.items()
                if name not in _NODE_REFERENCES))
        unit.build_ms = (perf_counter() - started) * 1000
        if self.DESCRIPTORS:
            unit.tree['op_flags'] = json.dumps(unit.flags)
            unit.tree['op_exports'] = json.dumps(unit.exports)
            unit.tree['op_const_imports'] = json.dumps([[list(k), name, value] for k, name, value in unit.const_imports])
            unit.tree['op_effects'] = json.dumps([self._owner_key(e) for e in unit.compiled_effects.values()])
        self._building_units.discard(key)
        self._plan.keys.add(key)
        self.units[key] = unit
        return unit

    def _new_unit_tree(self, key):
        """The node tree a unit compiles into (the engine substitutes a headless one)."""
        tree = bpy.data.node_groups.new(self._unit_name(key), 'ShaderNodeTree')
        tree[OWNER] = True
        tree['op_shader_graph'] = self.ts.id
        tree['op_unit'] = repr(key)
        from ..utils.ids import new_id
        tree['op_tree_identity'] = new_id()
        return tree

    def _free_tree(self, tree):
        if tree is not None and not tree.users:
            bpy.data.node_groups.remove(tree)

    def _compile(self, key, layer, stub=None, bypass=False):
        unit = Unit(key)
        unit.tree = self._new_unit_tree(key)
        try:
            with self.enter(unit):
                unit.group_input = self.node('NodeGroupInput')
                unit.group_output = self.node('NodeGroupOutput')
                if stub is not None or bypass:
                    if bypass:
                        unit.flags['bypass'] = True
                    else:
                        unit.flags['stub'] = stub
                        unit.warnings.append(f'Hidden layer "{layer.name}" is not compiled: {stub}')
                    self._export('Out', unit.input('In', 'NodeSocketColor'), 'NodeSocketColor')
                    if not self._plan.opaque[layer.id]:
                        self._export('Out:Alpha', unit.input('In:Alpha', 'NodeSocketFloat'), 'NodeSocketFloat')
                    if layer.type == 'FOLDER' and getattr(layer, 'publish_group', False):
                        # GROUP anchors still import these sockets (consumers adopted from the
                        # previous build wire them by name): a skipped folder composes nothing.
                        self._export('Group', self.combine(0., 0., 0.), 'NodeSocketColor')
                        self._export('Group:Alpha', self.math('ADD', 0., 0.), 'NodeSocketFloat')
                elif key[0] == 'SRC':
                    signal = self.source(layer, key[2], key[3])
                    self._export('Color', signal.color, 'NodeSocketColor')
                    self._export('Alpha', signal.alpha, 'NodeSocketFloat')
                elif key[0] == 'MASK':
                    self._export('Value', self.mask(layer, key[2], key[3]), 'NodeSocketFloat')
                elif key[0] == 'FIELD':
                    with self.role(f'LayerSource:{layer.id}:{key[2]}'):
                        self._export('Value', self.detail(self._channel(layer, key[2])), 'NodeSocketFloat')
                elif key[0] == 'GRAPH':
                    mask = next(m for m in layer.masks if m.id == key[2])
                    with self.native(mask):
                        self._export('Value', self._native_graph(layer, mask), 'NodeSocketFloat')
                else:
                    self._compile_channel(unit, layer, key[2])
        except Exception:
            self._discard_partial(unit)
            raise
        return unit

    def _discard_partial(self, unit):
        """Drop the tree and bindings of a unit whose compilation failed."""
        for key in list(self.bindings):
            if key[0] == unit.key:
                binding = self.bindings.pop(key)
                for pointer in binding.pointers():
                    owners = self.owner_bindings.get(pointer, [])
                    if binding in owners:
                        owners.remove(binding)
        self.resource_bindings = [b for b in self.resource_bindings if b not in unit.resource_bindings]
        self.image_bindings = [b for b in self.image_bindings if b not in unit.image_bindings]
        self.cache_bindings = [b for b in self.cache_bindings if b not in unit.cache_bindings]
        for pointer in unit.compiled_effects:
            self._compiled_effects.pop(pointer, None)
        try:
            self._free_tree(unit.tree)
        except (ReferenceError, RuntimeError):
            pass
        unit.tree = None

    def _unbind_units(self, units):
        """Take compiled units' bindings out of the graph's tables; the units keep their own.
        A recompile of the same key must not find them (``param`` returns a bound socket)."""
        bindings, owned, nodes = {}, set(), set()
        for unit in units:
            for identifier, binding in unit.bindings.items():
                if self.bindings.get(identifier) is binding:
                    del self.bindings[identifier]
                bindings[id(binding)] = binding
            owned.update(id(b) for b in (*unit.resource_bindings, *unit.cache_bindings))
            # Image entries are tuples ``update`` replaces with new ones: matched by their node.
            nodes.update(id(entry[3]) for entry in unit.image_bindings)
        pointers = set()
        for binding in bindings.values():
            try:
                pointers.update(binding.pointers())
            except ReferenceError:   # an owner Blender removed: look for the binding everywhere
                pointers.update(self.owner_bindings)
                break
        for pointer in pointers:
            listed = self.owner_bindings.get(pointer)
            if listed:
                listed[:] = [binding for binding in listed if id(binding) not in bindings]
                if not listed:
                    del self.owner_bindings[pointer]
        if owned:
            self.resource_bindings = [b for b in self.resource_bindings if id(b) not in owned]
            self.cache_bindings = [b for b in self.cache_bindings if id(b) not in owned]
        if nodes:
            self.image_bindings = [entry for entry in self.image_bindings if id(entry[3]) not in nodes]

    def _rebind_units(self, units):
        """Undo ``_unbind_units``: a patch that failed puts its units back as they were."""
        for unit in units:
            self.bindings.update(unit.bindings)
            for binding in unit.bindings.values():
                try:
                    pointers = binding.pointers()
                except ReferenceError:
                    continue   # its owner is gone: no edit can reach it
                for pointer in pointers:
                    self.owner_bindings[pointer].append(binding)
            self.resource_bindings.extend(unit.resource_bindings)
            self.image_bindings.extend(unit.image_bindings)
            self.cache_bindings.extend(unit.cache_bindings)

    def blend(self, backdrop, source, coverage, mode, channel):
        return blend(self, backdrop, source, coverage, mode, channel)

    def _compile_channel(self, unit, layer, channel):
        """Blend one layer's channel over its backdrop inside the unit."""
        prop = channel_for(layer, channel)
        mode = self._mode(layer, prop)
        opaque = self._plan.opaque[layer.id]
        backdrop = Signal(unit.input('In', 'NodeSocketColor'),
                          1.0 if opaque else unit.input('In:Alpha', 'NodeSocketFloat'))
        cached = self._cache_effective(layer, channel) != 'LIVE'
        # A folder whose disabled channel is read by consumers composes its
        # children for them and passes its backdrop through (composite.Composer).
        blends = self._touched(layer, channel)
        with self.role(f'LayerMix:{layer.id}:{channel}'):
            amount = 0.0
            if blends:
                amount = self.math('MULTIPLY', self.param(layer, 'opacity'), self.param(layer, 'visible'))
                if prop:
                    amount = self.math('MULTIPLY', amount, self.param(prop, 'opacity'))
                if layer.type == 'FOLDER' or not cached:
                    # A cached paint/fill layer bakes its mask into the image alpha.
                    amount = self.math('MULTIPLY', amount, self.mask(layer))
            if cached:
                content = self._layer_cache(layer, channel)
                result = self.blend(backdrop, content, amount, mode, channel) if blends else backdrop
                published = content
            elif layer.type == 'FOLDER' and layer.group_mode == 'PASSTHROUGH':
                grouped = Signal(unit.input('Grouped', 'NodeSocketColor'),
                                 1.0 if opaque else unit.input('Grouped:Alpha', 'NodeSocketFloat'))
                result = (Signal(self.mix(backdrop.color, grouped.color, amount),
                                 self.scalar_mix(backdrop.alpha, grouped.alpha, amount)) if blends else backdrop)
                # The chain is premultiplied in a non-opaque context; anchors read straight RGBA.
                published = self._straight(grouped)
            elif layer.type == 'FOLDER':
                child = Signal(unit.input('Grouped', 'NodeSocketColor'), unit.input('Grouped:Alpha', 'NodeSocketFloat'))
                source = self._straight(child)
                result = self.blend(backdrop, source, amount, mode, channel) if blends else backdrop
                published = source
            else:
                source = self.source(layer, channel)
                result = self.blend(backdrop, source, amount, mode, channel)
        self._export('Out', result.color, 'NodeSocketColor')
        self._export('Out:Alpha', result.alpha, 'NodeSocketFloat')
        if layer.type == 'FOLDER' and getattr(layer, 'publish_group', False):
            # The child composite before this folder blends, for GROUP anchors.
            self._export('Group', published.color, 'NodeSocketColor')
            self._export('Group:Alpha', published.alpha, 'NodeSocketFloat')

    def _wire(self, parent, channel, backdrop, ancestors=frozenset()):
        """Chain layer channel instances in the top tree, following the CPU evaluator."""
        for layer in self.ts.layers:
            if layer.parent_id != parent or not self._included(layer):
                continue
            if layer.id in ancestors:
                raise ValueError('Layer hierarchy contains a cycle')
            if not self._wired(layer, channel):
                continue
            unit = self.units[('CHANNEL', layer.id, channel)]
            instance = unit.instance
            self.link(backdrop.color, instance.inputs['In'])
            if 'In:Alpha' in unit.inputs:
                self.link(backdrop.alpha, instance.inputs['In:Alpha'])
            if layer.type == 'FOLDER':
                if layer.group_mode == 'PASSTHROUGH':
                    grouped = self._wire(layer.id, channel, backdrop, ancestors | {layer.id})
                else:
                    grouped = self._wire(layer.id, channel, Signal((0., 0., 0.), 0.0), ancestors | {layer.id})
                if 'Grouped' in unit.inputs:
                    self.link(grouped.color, instance.inputs['Grouped'])
                if 'Grouped:Alpha' in unit.inputs:
                    self.link(grouped.alpha, instance.inputs['Grouped:Alpha'])
            backdrop = Signal(instance.outputs['Out'],
                              instance.outputs['Out:Alpha'] if 'Out:Alpha' in unit.outputs else 1.0)
        return backdrop

    def _instance(self, unit):
        with self.role('UnitInterface'):
            node = self.node('ShaderNodeGroup')
        node.node_tree = unit.tree
        node['op_unit'] = repr(unit.key)
        node['op_tree_identity'] = unit.tree['op_tree_identity']
        node.label = ' '.join(str(part) for part in unit.key)
        unit.instance = node
        return node

    def build(self, reuse=None, undo=None):
        """Compile the graph; units of ``reuse`` with unchanged structure are adopted. ``undo``:
        ``reuse`` is a graph a memfile undo retired, and this its ``undo_index``: none of its
        RNA is read, its units are re-pointed (``_follow_undo``)."""
        from ..core.validation import hierarchy_issues
        from ..core.udim import validate as validate_udim
        from .references import scope
        with scope(self.ts):   # the issues below and the build's warnings read one reference graph
            return self._checked_build(reuse, hierarchy_issues, validate_udim, undo)

    def _checked_build(self, reuse, hierarchy_issues, validate_udim, undo=None):
        validate_udim(self.ts)
        problems = hierarchy_issues(self.ts)
        if problems:
            raise ValueError(' | '.join(problems))
        started = perf_counter()
        self._paths = None   # collection positions may have moved since the last build
        self._reused, self._warnings, self._adopted = [], [], []
        if undo is not None:
            plan = self.plan()
            self._previous = reuse if (reuse is not None and reuse.units
                                       and getattr(reuse, '_global', None) == global_signature(self.ts)) else None
            if self._previous is not None:
                self._undo_moves = self._undo_structs(undo)
                self._undo_moved = _addresses(self._undo_moves)
                self._undo_fresh = {id(struct) for struct in self._undo_moves.values()}
                self._undo_images = {image.session_uid: image for image in bpy.data.images}
        else:
            plan = reuse.scope_plan(self.ts) if reuse is not None else None
            if plan is None:
                plan = self.plan()
            else:
                self._plan, self._anchors = plan, None
            self._previous = reuse if (reuse is not None and reuse.units
                                       and global_signature(reuse.ts) == global_signature(self.ts)) else None
        if self._previous is not None:
            # Adopted closures read authoring state through the session's live graph.
            self._head = self._previous._head
            self._head[0] = self
        try:
            self._build(plan, started)
        except Exception:
            self._rollback_adoption()
            raise
        finally:
            self._previous = self._undo_moves = self._undo_moved = self._undo_images = self._undo_fresh = None
        self._global = global_signature(self.ts)
        if self._paths is None:
            # Adopted units ask for no path: index the set now, for the next undo (``undo_index``).
            self._path(self.ts)
        if reuse is not None:
            # Uniform edits the previous graph wrote and nobody restamped yet: the adopted
            # units carry the written values, so this build's own pass finds nothing new.
            held = getattr(reuse, 'touched', set())
            self.touched = None if held is None or self.touched is None else self.touched | held
        return self

    def _build(self, plan, started):
        self.tree = bpy.data.node_groups.new('OP Procedural · ' + self.ts.name, 'ShaderNodeTree')
        self.tree[OWNER] = True
        self.tree['op_shader_graph'] = self.ts.id
        from ..utils.ids import new_id
        self.tree['op_tree_identity'] = new_id()
        self.geometry = self.node('ShaderNodeNewGeometry')
        self.uv = self.node('ShaderNodeTexCoord').outputs['UV']
        if self.ts.udim_tile:
            from ..geometry.udim import tile_origin
            source = self.node('ShaderNodeUVMap')
            source.uv_map = self.ts.udim_source_uv
            u, v = tile_origin(self.ts.udim_tile)
            self.uv = self.vec('SUBTRACT', source.outputs['UV'], (u, v, 0.0))
            self.uv.node['op_udim_uv'] = True
        for key in plan.order:
            self.compile_unit(key)
        # Boundaries a discarded unit requested (its compilation raised after the
        # request) are dropped by the resource stepper instead of being rastered.
        self.resources.used = self._used_resources()
        for unit in self.units.values():
            # Every tree is instanced, sockets or not: a user-less unit tree would be
            # removed as an orphan while its unit is still adoptable.
            self._instance(unit)
        for unit in self.units.values():
            for name, source in (('UV', self.uv), ('Position', self.geometry.outputs['Position']),
                                 ('Normal', self.geometry.outputs['Normal'])):
                if name in unit.inputs:
                    self.link(source, unit.instance.inputs[name])
            for producer_key, export_name, input_name in unit.imports:
                kind, payload = self.units[producer_key].exports[export_name]
                if kind == 'socket':
                    self.link(self.units[producer_key].instance.outputs[export_name], unit.instance.inputs[input_name])
                else:
                    self.link(payload, unit.instance.inputs[input_name])
        for channel in CHANNELS:
            self.tree.interface.new_socket(name=channel, in_out='OUTPUT', socket_type='NodeSocketColor')
        output = self.node('NodeGroupOutput')
        output['op_dependency'] = 'OutputInterface'
        for channel in CHANNELS:
            signal = self._wire('', channel, Signal(DEFAULTS[channel][:3]))
            with self.role('Final:' + channel):
                color = signal.color
                if channel == 'NORMAL':
                    vector = self.vec('SUBTRACT', self.vec('SCALE', color, 2), (1., 1., 1.))
                    color = self.vec('SCALE', self.vec('ADD', self.vec('NORMALIZE', vector), (1., 1., 1.)), .5)
                final = self.clamp(color)
                if not hasattr(final, 'node'):
                    final = self.rgb((final,) * 3)
                self.link(final, output.inputs[channel])
                self.channels['Final:' + channel] = channel
        self.instance = self.material.node_tree.nodes.new('ShaderNodeGroup')
        self.instance[OWNER] = True
        self.instance['role'] = 'Procedural Graph'
        self.instance.node_tree = self.tree
        self.instance['op_tree_identity'] = self.tree['op_tree_identity']
        self.instance.label = 'Open Painter · Editable Material'
        self.instance.location = (-400, 200)
        self.outputs = {channel: self.instance.outputs[channel] for channel in CHANNELS}
        self._reverse_map()
        self._structure = self.structural_signature()
        self._settled(self._plan)
        self.tree['op_signature'] = repr(self._structure)
        self._stubs = any(unit.flags.get('stub') for unit in self.units.values())
        self.validate_parameters()
        # A batch can alter topology and numeric/image inputs together. Adopted
        # units retain their nodes, but their uniforms and resource identities
        # must be reconciled before marking this replacement graph current.
        corrections = self.update(check_structure=False) if self._reused else {}
        for binding in self.cache_bindings:
            self._reconcile_cache(binding)
        from ..core.validation import warnings
        unit_nodes = {repr(key): unit.node_count for key, unit in self.units.items()}
        queued = sum(key in self.resources.used for key in self.resources.queue)
        self._last = {'parameter_changes': corrections.get('parameter_changes', []),
                      'invalidated_nodes': corrections.get('invalidated_nodes', []), 'affected_channels': list(CHANNELS),
                      'updated_bindings': corrections.get('updated_bindings', 0), 'raster_boundaries_queued': queued,
                      'texture_bake_triggered': False, 'submission_ms': (perf_counter() - started) * 1000,
                      'build_ms': (perf_counter() - started) * 1000,
                      'shader_nodes': self.node_count, 'units': len(self.units), 'unit_nodes': unit_nodes,
                      'largest_unit': max(unit_nodes.values(), default=0),
                      'reused_units': len(self._reused), 'rebuilt_units': len(self.units) - len(self._reused),
                      'unique_images': len(self.unique_images()), 'max_textures_frag': _sampler_limit(),
                      'pending_resources': self.pending_resources,
                      'unit_build_ms': {repr(key): unit.build_ms for key, unit in self.units.items()},
                      'cache_states': self.cache_states(),
                      'warnings': list(warnings(self.ts)) + self._build_warnings()}
        metrics.count('shader_graph_builds')
        from .dependencies import remember, geometry_key
        remember(self.scene, self.ts, geometry_key(self.scene, self.ts))
        self._adopted = []

    def rehydrate(self):
        """Adopt the material's persisted graph after undo/redo instead of rebuilding.

        Returns True when the persisted top tree matches the current structural
        signature and every descriptor resolves; bindings, image and boundary
        bindings and the dependency closure are rebuilt from the trees. Nothing
        is created or modified on failure, so the caller can compile normally.
        """
        self._paths = None   # collection positions may have moved since the last build
        from ..core.validation import hierarchy_issues
        if self.material is None or self.material.node_tree is None or hierarchy_issues(self.ts):
            return False
        instance = next((node for node in self.material.node_tree.nodes
                         if node.bl_idname == 'ShaderNodeGroup'
                         and node.get(OWNER) and node.get('role') == 'Procedural Graph'), None)
        if instance is None:
            return False
        # Resolved by owner tags, never through ``instance.node_tree``: that
        # getter refines a raw pointer and access-violates Blender when the
        # referenced tree is gone instead of raising (see ``owned_tops``).
        top = _adoptable_top(self.ts.id, instance)
        if top is None:
            return False
        started = perf_counter()
        plan = self.plan()
        units, by_instance = {}, {}
        for node in top.nodes:
            if node.bl_idname != 'ShaderNodeGroup' or not node.get('op_unit'):
                continue
            unit_tree = _adoptable_unit(self.ts.id, node['op_unit'], node)
            if unit_tree is None:
                return False
            unit = Unit(literal_eval(node['op_unit']))
            unit.tree, unit.instance = unit_tree, node
            unit.group_input = next((n for n in unit.tree.nodes if n.bl_idname == 'NodeGroupInput'), None)
            unit.group_output = next((n for n in unit.tree.nodes if n.bl_idname == 'NodeGroupOutput'), None)
            if unit.group_input is None or unit.group_output is None:
                return False
            for item in unit.tree.interface.items_tree:
                if item.item_type != 'SOCKET':
                    continue
                (unit.inputs if item.in_out == 'INPUT' else unit.outputs)[item.name] = item.socket_type
            unit.flags = json.loads(unit.tree.get('op_flags', '{}'))
            unit.exports = {name: tuple(value) for name, value in json.loads(unit.tree.get('op_exports', '{}')).items()}
            unit.const_imports = [(tuple(key), name, value)
                                  for key, name, value in json.loads(unit.tree.get('op_const_imports', '[]'))]
            units[unit.key] = unit
            by_instance[node.as_pointer()] = unit
        self.units = units
        complete = False
        try:
            if repr(self.structural_signature()) != top['op_signature']:
                return False
            for link in top.links:
                consumer = by_instance.get(link.to_node.as_pointer())
                producer = by_instance.get(link.from_node.as_pointer())
                if consumer is not None and producer is not None and link.to_socket.name.startswith('Ref'):
                    consumer.imports.append((producer.key, link.from_socket.name, link.to_socket.name))
            for unit in units.values():
                if not self._rehydrate_unit(unit):
                    return False
            complete = True
        finally:
            if not complete:
                # Leave the persisted trees untouched; forget partial bookkeeping.
                self.units, self.bindings, self.owner_bindings = {}, {}, defaultdict(list)
                self.resource_bindings, self.image_bindings, self._compiled_effects = [], [], {}
                self.cache_bindings = []
                self.resources.close()
        self.tree, self.instance = top, instance
        self.geometry = next(n for n in top.nodes if n.bl_idname == 'ShaderNodeNewGeometry')
        self.uv = next(n for n in top.nodes if n.bl_idname == 'ShaderNodeTexCoord').outputs['UV']
        if self.ts.udim_tile:
            self.uv = next(n for n in top.nodes if n.get('op_udim_uv')).outputs['Vector']
        self.outputs = {channel: instance.outputs[channel] for channel in CHANNELS}
        for channel in CHANNELS:
            self.channels['Final:' + channel] = channel
        self._reverse_map()
        self._structure = self.structural_signature()
        self._settled(self._plan)
        self._stubs = any(unit.flags.get('stub') for unit in self.units.values())
        self.resources.used = self._used_resources()
        for binding in self.cache_bindings:
            self._reconcile_cache(binding)
        from ..core.validation import warnings
        unit_nodes = {repr(key): unit.node_count for key, unit in self.units.items()}
        self._last = {'parameter_changes': [], 'invalidated_nodes': [], 'affected_channels': [],
                      'updated_bindings': 0, 'raster_boundaries_queued': len(self.resources.queue),
                      'texture_bake_triggered': False, 'submission_ms': (perf_counter() - started) * 1000,
                      'build_ms': 0.0, 'rehydrate_ms': (perf_counter() - started) * 1000, 'rehydrated': True,
                      'shader_nodes': self.node_count, 'units': len(self.units), 'unit_nodes': unit_nodes,
                      'largest_unit': max(unit_nodes.values(), default=0),
                      'reused_units': len(self.units), 'rebuilt_units': 0,
                      'unique_images': len(self.unique_images()), 'max_textures_frag': _sampler_limit(),
                      'pending_resources': self.pending_resources,
                      'unit_build_ms': {repr(key): unit.build_ms for key, unit in self.units.items()},
                      'cache_states': self.cache_states(),
                      'warnings': list(warnings(self.ts)) + self._build_warnings()}
        metrics.count('shader_graph_rehydrations')
        from .dependencies import remember, geometry_key
        remember(self.scene, self.ts, geometry_key(self.scene, self.ts))
        return True

    def _rehydrate_unit(self, unit):
        with self.enter(unit):
            for node in unit.tree.nodes:
                if node.get('op_binding'):
                    owner_key, field, index = json.loads(node['op_binding'])
                    owner = self._resolve(owner_key)
                    if owner is None:
                        return False
                    identifier = node.get('op_dependency')
                    binding = Binding(owner, field, index, node.outputs[0], float(node.outputs[0].default_value),
                                      identifier, unit=unit.key)
                    key = (unit.key, _owner_token(owner), field, index)
                    self.bindings[key] = binding
                    unit.bindings[key] = binding
                    self.owner_bindings[owner.as_pointer()].append(binding)
                elif node.get('op_image'):
                    owner_key, attribute = json.loads(node['op_image'])
                    owner = self._resolve(owner_key)
                    if owner is None:
                        return False
                    getter = (lambda o=owner, a=attribute: getattr(o, a))
                    entry = (owner.as_pointer(), node.get('op_dependency'), getter, node, image_key(node.image))
                    self.image_bindings.append(entry)
                    unit.image_bindings.append(entry)
                elif node.get('op_boundary'):
                    if not self._rehydrate_boundary(unit, node, json.loads(node['op_boundary'])):
                        return False
                elif node.get('op_cache'):
                    layer_id, channel = json.loads(node['op_cache'])
                    layer = self._layer(layer_id)
                    if layer is None or self._cache_row(layer, channel) is None:
                        return False
                    # The binding's identifier is the mix role the reverse map propagates.
                    with self.role(node.get('op_dependency') or self._role):
                        self._layer_cache(layer, channel, rehydrate=node)
            for owner_key in json.loads(unit.tree.get('op_effects', '[]')):
                effect = self._resolve(owner_key)
                if effect is None:
                    return False
                unit.compiled_effects[effect.as_pointer()] = effect
                self._compiled_effects[effect.as_pointer()] = effect
        unit.owner_pointers = self._unit_owners(unit.key)
        unit.signature = self._unit_signature(unit.key, stub=unit.flags.get('stub'))
        return True

    def _rehydrate_boundary(self, unit, node, descriptor):
        site, *args = descriptor
        if site == 'field':
            owner = self._resolve(args[0])
            return owner is not None and self._field(owner, args[1], args[2], rehydrate=node) is not None
        layer = self._layer(args[0])
        if layer is None:
            return False
        if site == 'channel_prefix':
            prop = channel_for(layer, args[1])
            return prop is not None and self._channel_prefix(layer, prop, args[2], rehydrate=node) is not None
        if site == 'strokes':
            prop = channel_for(layer, args[1])
            return prop is not None and self._strokes_boundary(layer, prop, rehydrate=node) is not None
        mask = next((m for m in layer.masks if m.id == args[1]), None)
        if mask is None:
            return False
        if site == 'micro':
            return self._micro(mask, rehydrate=node) is not None
        if site == 'drips':
            return self._drips(mask, rehydrate=node) is not None
        if site == 'mask_source':
            return self._mask_source_boundary(layer, mask, rehydrate=node) is not None
        if site == 'scene_edge':
            return self._scene_boundary(layer, mask, rehydrate=node) is not None
        if site == 'mask_prefix':
            masks = list(layer.masks)
            stop = next(i + 1 for i, m in enumerate(masks) if m.id == args[1])
            prefix = self._run_prefix(masks, stop, args[2])
            return self._mask_prefix(layer, prefix, args[2], rehydrate=node) is not None
        return False

    def _reverse_map(self):
        """Dependency closure across unit boundaries, keyed by op_dependency roles."""
        self.reverse = defaultdict(set)
        by_instance = {unit.instance.as_pointer(): unit for unit in self.units.values() if unit.instance}

        def role(node):
            return node.get('op_dependency')

        for unit in self.units.values():
            unit.input_consumers, unit.output_producer = {}, {}
            for link in unit.tree.links:
                source, target = link.from_node, link.to_node
                if source == unit.group_input:
                    if target == unit.group_output:
                        unit.output_producer[link.to_socket.name] = ('input', link.from_socket.name)
                    else:
                        unit.input_consumers.setdefault(link.from_socket.name, set()).add(role(target))
                elif target == unit.group_output:
                    unit.output_producer[link.to_socket.name] = role(source)
                else:
                    a, b = role(source), role(target)
                    if a and b and a != b:
                        self.reverse[a].add(b)
        feeding = defaultdict(list)
        for link in self.tree.links:
            feeding[(link.to_node.as_pointer(), link.to_socket.name)].append(link)
        memo = {}

        def producers(unit, name):
            token = (unit.key, name)
            if token in memo:
                return memo[token]
            memo[token] = set()
            producer = unit.output_producer.get(name)
            found = set()
            if isinstance(producer, tuple):
                for link in feeding.get((unit.instance.as_pointer(), producer[1]), ()):
                    upstream = by_instance.get(link.from_node.as_pointer())
                    if upstream is not None:
                        found |= producers(upstream, link.from_socket.name)
                    elif role(link.from_node):
                        found.add(role(link.from_node))
            elif producer:
                found.add(producer)
            memo[token] = found
            return found

        for link in self.tree.links:
            source, target = link.from_node, link.to_node
            from_unit = by_instance.get(source.as_pointer())
            to_unit = by_instance.get(target.as_pointer())
            if to_unit is not None:
                consumers = to_unit.input_consumers.get(link.to_socket.name, set())
                sources = producers(from_unit, link.from_socket.name) if from_unit is not None else {role(source)}
                for a in sources:
                    for b in consumers:
                        if a and b and a != b:
                            self.reverse[a].add(b)
            elif from_unit is not None:
                b = role(target)
                if b and b != 'OutputInterface':
                    for a in producers(from_unit, link.from_socket.name):
                        if a != b:
                            self.reverse[a].add(b)
            else:
                a, b = role(source), role(target)
                if a and b and a != b and b != 'OutputInterface':
                    self.reverse[a].add(b)

    # ---- inspection ------------------------------------------------------------

    def trees(self):
        if self.tree is not None:
            yield self.tree
        for unit in self.units.values():
            if unit.tree is not None:
                yield unit.tree

    def nodes(self):
        for tree in self.trees():
            yield from tree.nodes

    @property
    def node_count(self):
        return sum(len(tree.nodes) for tree in self.trees())

    def node_identities(self):
        return tuple(node.as_pointer() for node in self.nodes())

    def unique_images(self):
        return {pointer for pointer in (_image_pointer(node) for node in self.nodes()
                                        if node.bl_idname == 'ShaderNodeTexImage') if pointer is not None}

    def bound_sampler_count(self):
        # The same image with different sampling state is not one binding.
        # Read the settled nodes, not build-time placeholder diagnostics.
        return len({(node.image.as_pointer(), node.interpolation, node.extension)
                    for node in self.nodes() if node.bl_idname == 'ShaderNodeTexImage' and node.image})

    def structural_signature(self, plan=None):
        """``plan``: the plan this build was made from (nothing edited the stack since)."""
        plan = plan if plan is not None else self.plan()
        stubs = {key: unit.flags['stub'] for key, unit in self.units.items() if unit.flags.get('stub')}
        units = tuple(sorted((key, self._unit_signature(key, stub=stubs.get(key))) for key in plan.keys))
        return (global_signature(self.ts), tuple(plan.wiring), units)

    def _stub(self, key):
        unit = self.units.get(key)
        return unit.flags.get('stub') if unit is not None else None

    def scope_plan(self, ts, signature=False):
        """The plan ``rebuild_scope`` made, taken once, while nothing structural changed since:
        each structural edit is checked by its callback and then rebuilt on the next tick.
        ``signature``: (plan, the structural signature it checked), or None."""
        held = getattr(self, '_scope_plan', None)
        self._scope_plan = None
        if held is None or held[0] is None or held[0] != _plan_token(ts):
            return None
        return (held[1], held[2]) if signature else held[1]

    def matches_structure(self):
        try:
            return self._structure == self.structural_signature()
        except ValueError:
            # An invalid hierarchy is reported by the rebuild, with its message.
            return False

    def touch_layers(self, identifiers):
        """Layers an edit reached, for the next structure check (``plan``); None: unknown."""
        if identifiers is None:
            self._dirty_layers = None
        elif self._dirty_layers is not None:
            self._dirty_layers.update(identifiers)

    def _settled(self, plan):
        """``plan`` is what the graph's structure now matches."""
        self._structure_plan, self._dirty_layers = plan, set()

    def rebuild_scope(self):
        """'global', or the unit keys whose structure no longer matches; None when current."""
        try:
            plan = self.plan(self._structure_plan, self._dirty_layers)
            current = self.structural_signature(plan)
        except ValueError:
            return 'global'
        # The rebuild this answer triggers need not plan the stack again (``scope_plan``).
        self._scope_plan = (_plan_token(self.ts), self._plan, current)
        if current == self._structure:
            self._settled(plan)
            return None
        if current[0] != self._structure[0]:
            return 'global'
        before, after = dict(self._structure[2]), dict(current[2])
        changed = [key for key, signature in after.items() if before.get(key) != signature]
        changed.extend(key for key in before if key not in after)
        return changed or 'wiring'

    def update(self, owner=None, check_structure=True, resources=True, values=True, owners=None,
               value_owners=None):
        """Write changed bindings; ``check_structure`` is False for numeric-only edits.

        ``resources=False`` is the stroke-time path (freeze while painting): image
        bindings are refreshed so the canvas shows, but no structural walk,
        boundary re-identification or cache reconciliation happens until release.
        ``values=False`` reads no uniform and no image field: a patch whose kept units
        saw every edit reconciles only inputs a structural edit can re-identify, and with
        ``owners`` (addresses) only the inputs one of those owns. ``value_owners``
        (addresses): only these owners' uniforms and image fields are read.
        """
        started = perf_counter()
        if resources and (check_structure or self._stubs):
            # A stub stands in for hidden content, so visibility is structural for it.
            scope = self.rebuild_scope()
            if scope is not None:
                raise RebuildRequired('Shader graph topology changed', scope)
        elif self._plan is not None:
            # A collection add() may have moved the layer structs since the last
            # plan; the numeric path reads them without re-planning.
            self._plan.layers = {l.id: l for l in self.ts.layers}
            self._plan.channels = {}
        if resources:
            self.validate_parameters()
        pointer = owner.as_pointer() if owner is not None and owner != self.ts else None
        bindings = self.owner_bindings.get(pointer, ()) if pointer is not None else self.bindings.values()
        removed = _removed_graph_units(bindings)
        if removed:
            raise RebuildRequired('A Mask Graph node was removed', removed)
        if value_owners is not None and pointer is None:
            bindings = list({id(binding): binding for address in value_owners
                             for binding in self.owner_bindings.get(address, ())}.values())
        changed = []
        touched = self.touched
        for binding in bindings if values else ():
            value = binding.read()
            if value != binding.value:
                binding.socket.default_value = value
                binding.value = value
                if touched is not None:
                    unit = binding.unit
                    if unit is UNKNOWN_UNIT or unit is None:
                        self.touched = touched = None
                    else:
                        touched.add(unit)
                if binding.identifier not in changed:
                    changed.append(binding.identifier)
        invalidated = set(changed)
        queued = 0
        cache_requests = 0
        for binding in self.resource_bindings:
            if not resources:
                break
            # Purge Unused Data may have taken the in-flight placeholder: its source
            # is requested again whichever owner this update was narrowed to.
            removed = self._placeholder_removed(binding)
            if pointer is not None and pointer not in binding.owners and not removed:
                continue
            if owners is not None and owners.isdisjoint(binding.owners) and not removed:
                continue
            signature = binding.identity()
            if signature != binding.signature:
                queued += self._request(binding, signature)
                if binding.owner_factory is not None:
                    # New inputs (an added effect or mask) must reach the owner filter.
                    binding.owners = {o.as_pointer() for o in binding.owner_factory()}
                invalidated.add(binding.identifier)
        for index, (p, identifier, getter, node, previous) in enumerate(self.image_bindings if values else ()):
            if (pointer is not None and p != pointer) or (value_owners is not None and p not in value_owners):
                continue
            image = getter()
            identity = image_key(image)
            if identity != previous:
                node.image = image
                self.image_bindings[index] = (p, identifier, getter, node, identity)
                invalidated.add(identifier)
        for binding in self.cache_bindings:
            if not resources:
                break
            if pointer is not None and pointer not in binding.owners:
                continue
            if owners is not None and owners.isdisjoint(binding.owners) and not binding.stale:
                continue
            signature = binding.identity()
            if signature != binding.signature or binding.stale:
                # A publication does not change the identity; it clears staleness.
                if signature != binding.signature:
                    if binding.owner_factory is not None:
                        binding.owners = {o.as_pointer() for o in binding.owner_factory()}
                    # The export re-rasters this content whether or not a refresh is requested.
                    invalidated.add(binding.identifier)
                binding.signature = signature
                cache_requests += self._reconcile_cache(binding)
        if resources:
            self.resources.used = self._used_resources()
        pending = list(invalidated)
        while pending:
            for downstream in self.reverse.get(pending.pop(), ()):
                if downstream not in invalidated:
                    invalidated.add(downstream)
                    pending.append(downstream)
        channels = sorted({self.channels[node] for node in invalidated if node in self.channels})
        report = {'parameter_changes': changed, 'invalidated_nodes': sorted(invalidated),
                  'affected_channels': channels, 'updated_bindings': len(changed),
                  'raster_boundaries_queued': queued, 'texture_bake_triggered': False,
                  'cache_requests': cache_requests, 'deferred': not resources,
                  'submission_ms': (perf_counter() - started) * 1000,
                  'pending_resources': self.pending_resources,
                  'gpu_execution_scope': 'Blender evaluates the material shader; per-node execution is not exposed'}
        self._last = report
        metrics.count('shader_parameter_bindings_updated', len(changed))
        metrics.count('shader_parameter_updates', bool(changed))
        metrics.sample('shader_submission_ms', report['submission_ms'])
        return report

    @property
    def pending_resources(self):
        return self.resources.pending

    def take_touched(self):
        """Units whose uniforms changed since the last call; None means all of them."""
        touched, self.touched = self.touched, set()
        return touched

    def _request(self, binding, signature, previous=None):
        """Bind the cached image for ``signature``, or keep its placeholder pending.
        Returns how many rasters this queued. ``previous`` is the graph the binding
        is adopted from: a raster it saw fail is inherited instead of requested."""
        before = len(self.resources.queue)
        image = self.resources.inherit(signature, previous.resources) if previous is not None else None
        if image is None:
            stable = None
            if binding.stable and previous is None:
                # Regenerate into the leaf this binding owns even when a gesture preview or a
                # fallback canvas currently stands on the node: the datablock stays put, so no
                # per-stroke churn leaves an orphan for a later undo to free (0.13 S2).
                for candidate in (binding.stable_image, binding.pending_image, binding.node.image):
                    if candidate is not None and alive(candidate) and candidate.get(RESOURCE):
                        stable = candidate
                        break
            image = self._request_leaf(signature, binding.factory, binding.layer_id, binding.placeholder,
                                       binding.interactive, stable=stable)
        if image is not None and binding.stable and image.get(RESOURCE):
            binding.stable_image = image
        if image is None:
            # Frozen after undo, nothing to reuse: leave the binding un-signed so the next
            # authored gesture requests it once the freeze lifts (no new datablock right now).
            binding.pending_image, binding.signature = None, None
            return 0
        if image.get('op_shader_resource_ready'):
            binding.node.image, binding.pending_image = image, None
        else:
            binding.pending_image = image
        binding.signature = signature
        return len(self.resources.queue) - before

    @staticmethod
    def _placeholder_removed(binding):
        """A held placeholder Blender has since removed is forgotten, and the
        binding's signature reset so its source is requested again."""
        if binding.pending_image is None or alive(binding.pending_image):
            return False
        binding.pending_image, binding.signature = None, None
        return True

    def _used_resources(self):
        keys = set()
        for binding in self.resource_bindings:
            self._placeholder_removed(binding)
            image = binding.pending_image or binding.node.image
            if image:
                keys.add(image.get(RESOURCE))
        return keys

    def resource_error(self):
        """The failure of an isolated input this graph still shows a placeholder for.

        A raster that raised keeps its key bound (the identity did not change),
        so the error must outlive the edit that reported it: the session stays
        failed until Retry rebuilds or an edit re-identifies the input.
        """
        return next((entry.error for key, entry in self.resources.entries.items()
                     if entry.error and key in self.resources.used), '')

    def retry_failures(self):
        """Request every failed isolated input again (Retry, 0.9.8).

        A failure is otherwise carried from graph to graph by the bindings that
        show it, so only an edit that re-identifies the input retries its raster.
        Returns how many inputs were requested again.
        """
        failed = self.resources.forget_failures()
        if not failed:
            return 0
        requested = 0
        for binding in self.resource_bindings:
            # The failed placeholder nothing sampled went with its entry; one the
            # node still shows is replaced once the new raster publishes.
            dropped = self._placeholder_removed(binding)
            image = binding.pending_image or binding.node.image
            if dropped or (image is not None and image.get(RESOURCE) in failed):
                self._request(binding, binding.identity())
                requested += 1
        self.resources.used = self._used_resources()
        return requested

    def step_resources(self, budget=.006):
        for binding in self.resource_bindings:
            if self._placeholder_removed(binding):
                self._request(binding, binding.identity())
        result = self.resources.step(budget)
        for binding in self.resource_bindings:
            if binding.pending_image and binding.pending_image.get('op_shader_resource_ready'):
                stale = binding.node.image
                binding.node.image, binding.pending_image = binding.pending_image, None
                if binding.stable and binding.node.image.get(RESOURCE):
                    binding.stable_image = binding.node.image
                if (stale is not None and stale is not binding.node.image and not bound(stale)
                        and stale.get(RESOURCE) and not stale.get('op_shader_resource_ready')):
                    # An interrupted placeholder from a superseded graph: released, never
                    # removed while the material that sampled it may still be compiling.
                    from .shader_resources import release_image
                    release_image(stale)
        return result
    def close(self):
        try:
            if self.instance:
                self.material.node_tree.nodes.remove(self.instance)
            if self.tree and not self.tree.users:
                bpy.data.node_groups.remove(self.tree)
        except (ReferenceError, RuntimeError):
            pass
        for unit in self.units.values():
            try:
                if unit.tree and not unit.tree.users:
                    bpy.data.node_groups.remove(unit.tree)
            except (ReferenceError, RuntimeError):
                pass
        self.resources.close()

    def detach(self):
        """Freeze a usable material on disable/undo without destroying its graph."""
        for entry in self.resources.entries.values():
            if entry.job:
                entry.job.close()
                entry.job = None
        self.resources.queue.clear()

    def validate_parameters(self):
        # Validate before the first socket write, so an invalid slider range
        # cannot partly update a material while reporting a failed transaction.
        # A hidden layer may still publish a source to a visible mask. Validate
        # effects reached by compilation, rather than approximating dependency
        # scope using layer visibility. First-build checks run in apply_effect.
        for effect in self._live_effects():
            if (effect.enabled and effect.strength > 0
                    and effect.kind == 'NORMAL_COMBINE' and not effect.image):
                raise ValueError('Normal Combine needs a detail normal texture.')
            if (effect.enabled and effect.strength > 0
                    and effect.kind in {'LEVELS', 'ROUGHNESS_REMAP', 'METALLIC_REMAP', 'HEIGHT_REMAP'}
                    and effect.white <= effect.black):
                raise ValueError('White level must be greater than black level')

    def _live_effects(self):
        """Effects reachable from visible layers; hidden-only content is not validated."""
        if not self.units:
            return list(self._compiled_effects.values())
        # Liveness changes only with what reads what (the reach map) or layer visibility (a
        # uniform edit): validation on every slider step must not walk every unit again. A
        # patch that kept the reach only collects the replaced units' effects again.
        reach = getattr(self, '_reach_token', None) or getattr(self, '_structure', None)
        units = getattr(self, '_units_token', None)
        visibility = frozenset(layer.id for layer in self.ts.layers if not layer.visible)   # the hidden layers
        cached = getattr(self, '_live_cache', None)
        pending, self._live_seeds = getattr(self, '_live_seeds', None), None
        if cached is not None and cached[0] is reach and cached[2] == visibility:
            if cached[1] is units and cached[3] == len(self.units):
                return cached[5]
            if all(key in self.units for key in cached[4]):
                return self._collect_live(reach, units, visibility, cached[4])
        shown = {}   # layer id -> effective visibility, each layer read once

        def visible(identifier):
            if identifier not in shown:
                layer = self._layer(identifier)
                shown[identifier] = True   # a parent cycle ends the walk, as in ``_visible``
                shown[identifier] = layer is not None and layer.visible and (
                    not layer.parent_id or visible(layer.parent_id))
            return shown[identifier]
        live = None
        if (pending is not None and cached is not None and pending[0] is cached and cached[2] == visibility
                and getattr(self, 'consumers', None) is not None):
            live = self._live_after_patch(cached[4], pending[1], pending[2], visible)
        if live is None or VERIFY_STRUCTURE:
            found, work = set(), [key for key in self.units if key[0] == 'CHANNEL' and visible(key[1])]
            while work:
                key = work.pop()
                if key in found or key not in self.units:
                    continue
                found.add(key)
                work.extend(producer for producer, _, _ in self.units[key].imports)
            if live is not None and live != found:
                raise AssertionError(f'Patched live units differ: {sorted(map(str, live ^ found))[:6]}')
            live = found
        return self._collect_live(reach, units, visibility, live)

    def _live_after_patch(self, before, fresh, seeds, visible):
        """The live units after a patch, from those before it: only the units that came, went or
        were replaced, the producers they import or imported, and the producers of a unit whose
        liveness changed can change theirs. None when the walk does not settle (a cycle)."""
        units, consumers = self.units, self.consumers
        live = {key for key in before if key in units}
        work, expanded = list(fresh | seeds), set()
        budget = 4 * len(units) + len(work)
        while work:
            budget -= 1
            if budget < 0:
                return None
            key = work.pop()
            unit = units.get(key)
            if unit is None:
                continue
            now = (key[0] == 'CHANNEL' and visible(key[1])) or any(
                consumer in live for consumer in consumers.get(key, ()))
            if now != (key in live):
                if now:
                    live.add(key)
                else:
                    live.discard(key)
                work.extend(producer for producer, _, _ in unit.imports)
            elif key in fresh and key not in expanded:
                expanded.add(key)   # a replaced unit may import other producers now
                work.extend(producer for producer, _, _ in unit.imports)
        return live

    def _collect_live(self, reach, units, visibility, live):
        effects = {}
        for key in live:
            effects.update(self.units[key].compiled_effects)
        result = list(effects.values())
        self._live_cache = (reach, units, visibility, len(self.units), live, result)
        return result


def _sampler_limit():
    try:
        import gpu
        return int(gpu.capabilities.max_textures_frag_get())
    except Exception:
        return None
