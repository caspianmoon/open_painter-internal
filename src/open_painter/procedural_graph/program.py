"""A Mask Graph read into the ordered steps both evaluators run.

``build`` walks back from the Mask Output node, following links (and reroutes),
checks socket types, and orders the reachable nodes so every step follows the
steps it reads. An input is a reference: another step's output, an unlinked
value socket (a uniform), or texture coordinates for an unlinked vector.

``Program.structure`` is what compiles into code (node types, operations,
kinds, links, ramp stop order): edits that leave it unchanged are uniforms.
``Program.values`` adds every number, for caches of evaluated results.
``unit_signature`` adds which nodes and ramp stops carry the structure: the
engine's uniforms are bound to them, so a node replaced by an identical one
(or stops reallocated) rebuilds the graph's pass rather than reading a removed
node.
"""
from dataclasses import dataclass, field

import bpy

from .tree import TEXTURE_NODES, UNARY, VECTOR_SOCKET

KINDS = {'OPMGText': 'text', 'OPMGCoordinates': 'coordinates', 'OPMGValue': 'value', 'OPMGMeshMap': 'mesh_map', 'OPMGMath': 'math',
         'OPMGMix': 'mix', 'OPMGRamp': 'ramp', 'OPMGLevels': 'levels', 'OPMGMapping': 'mapping',
         **{name: 'procedural' for name in TEXTURE_NODES}}
NUMBERS = {'value': ('value',), 'procedural': ('scale', 'seed'), 'levels': ('black', 'white', 'gamma', 'output_black',
           'output_white'), 'mapping': ('offset', 'rotation', 'scale')}


class GraphError(ValueError):
    """The graph cannot be evaluated (no output, a loop, a link between mismatched sockets)."""


@dataclass
class Step:
    node: object
    kind: str
    inputs: dict = field(default_factory=dict)   # input name -> reference
    order: tuple = ()                            # ramp: stop indices by position

    def settings(self):
        """The node's compiled settings (not its uniforms)."""
        node = self.node
        if self.kind == 'text':
            from ..evaluation.text_source import signature
            return signature(node.text)
        if self.kind in {'procedural', 'mesh_map'}:
            return (node.kind,)
        if self.kind == 'math':
            return (node.operation, node.use_clamp)
        if self.kind == 'mix':
            return (node.blend, node.use_clamp)
        if self.kind == 'ramp':
            return (node.interpolation, self.order)
        return ()


@dataclass
class Program:
    tree: object
    steps: list
    result: tuple
    placement: bool = False   # reads the world position or normal
    maps: tuple = ()          # mesh maps read
    output: object = None     # the Mask Output node (its Coverage is a uniform while unlinked)

    @property
    def structure(self):
        def shape(ref):
            return ref if ref[0] == 'link' else (ref[0],)
        return (tuple((step.node.bl_idname, step.settings(), tuple(sorted((name, shape(ref))
                       for name, ref in step.inputs.items()))) for step in self.steps), shape(self.result))

    @property
    def identity(self):
        """The nodes and ramp stops the program reads, by address (not saved: a session's rebuild key)."""
        return (tuple(step.node.as_pointer() for step in self.steps),
                tuple(tuple(stop.as_pointer() for stop in step.node.stops) for step in self.steps
                      if step.kind == 'ramp'),
                self.output.as_pointer() if self.output is not None else 0)

    @property
    def values(self):
        numbers = []
        for step in self.steps:
            node = step.node
            entry = [_number(getattr(node, name)) for name in NUMBERS.get(step.kind, ())]
            if step.kind == 'ramp':
                entry.append(tuple((round(float(s.position), 7), round(float(s.value), 7)) for s in node.stops))
            entry.extend(round(float(ref[1].default_value), 7) for ref in step.inputs.values() if ref[0] == 'socket')
            numbers.append(tuple(entry))
        if self.result[0] == 'socket':
            numbers.append(round(float(self.result[1].default_value), 7))
        return (self.structure, tuple(numbers))


def _number(value):
    try:
        return tuple(round(float(v), 7) for v in value)
    except TypeError:
        return round(float(value), 7)


def build(tree):
    """The program of a Mask Graph; raises ``GraphError`` when it cannot be evaluated."""
    if tree is None:
        raise GraphError('The Custom Graph mask has no Mask Graph')
    outputs = [node for node in tree.nodes if node.bl_idname == 'OPMGOutput']
    if not outputs:
        raise GraphError(f'Mask Graph "{tree.name}" has no Mask Output node')
    if len(outputs) > 1:
        raise GraphError(f'Mask Graph "{tree.name}" has {len(outputs)} Mask Output nodes; keep one')
    steps, index, used = [], {}, set()
    # The link into each input. ``is_valid`` is not consulted: inside ``NodeTree.update``
    # a link just added is not validated yet. ``resolve`` and ``visit`` check what
    # validation would (socket types, loops); an unavailable output never connects.
    incoming = {}
    for link in tree.links:
        if not link.is_muted and link.from_socket.enabled:
            incoming.setdefault(link.to_socket.as_pointer(), link)

    def resolve(socket, visiting):
        default = ('uv',) if socket.bl_idname == VECTOR_SOCKET else ('socket', socket)
        link = incoming.get(socket.as_pointer())
        if link is None:
            return default
        source, node = link.from_socket, link.from_node
        while node.bl_idname == 'NodeReroute' or node.mute:
            # A reroute passes its input through; a muted node its first input of the same kind.
            inner = (node.inputs[0] if node.bl_idname == 'NodeReroute' else
                     next((s for s in node.inputs if s.enabled and s.bl_idname == socket.bl_idname), None))
            link = incoming.get(inner.as_pointer()) if inner is not None else None
            if link is None:
                return default
            source, node = link.from_socket, link.from_node
        if node.bl_idname not in KINDS:
            raise GraphError(f'"{node.name}" is not a Mask Graph node')
        if source.bl_idname != socket.bl_idname:
            wanted = 'coordinates' if socket.bl_idname == VECTOR_SOCKET else 'a value'
            raise GraphError(f'"{socket.node.name}" {socket.name} takes {wanted}, not "{node.name}" {source.name}')
        position = visit(node, visiting)
        used.add((position, source.name))
        return ('link', position, source.name)

    def visit(node, visiting):
        pointer = node.as_pointer()
        if pointer in index:
            return index[pointer]
        if pointer in visiting:
            raise GraphError(f'Mask Graph "{tree.name}" contains a loop through "{node.name}"')
        step = Step(node, KINDS[node.bl_idname])
        for socket in node.inputs:
            if socket.enabled and not (step.kind == 'math' and socket.name == 'B' and node.operation in UNARY):
                step.inputs[socket.name] = resolve(socket, visiting | {pointer})
        if step.kind == 'ramp':
            if len(node.stops) < 2:
                raise GraphError(f'Ramp "{node.name}" needs at least two stops')
            step.order = tuple(sorted(range(len(node.stops)), key=lambda i: (node.stops[i].position, i)))
        steps.append(step)
        index[pointer] = len(steps) - 1
        return index[pointer]

    result = resolve(outputs[0].inputs['Coverage'], frozenset())
    placement = any(steps[i].kind == 'coordinates' and name in {'Position', 'Normal'} for i, name in used)
    maps = tuple(sorted({step.node.kind for step in steps if step.kind == 'mesh_map'}))
    return Program(tree, steps, result, placement, maps, outputs[0])


def structure(tree):
    """The compiled shape of a graph, or the reason it cannot compile (a signature never raises)."""
    try:
        return build(tree).structure
    except GraphError as error:
        return ('invalid', str(error))


def unit_signature(tree):
    """The structure and the nodes carrying it: what decides whether the engine rebuilds the graph's pass."""
    try:
        program = build(tree)
    except GraphError as error:
        return ('invalid', str(error))
    return (program.structure, program.identity)


def values(tree):
    try:
        return build(tree).values
    except GraphError as error:
        return ('invalid', str(error))


def live_items(pointer):
    """(address, RNA type) of the nodes, input sockets and ramp stops of the Mask Graph at
    ``pointer``, empty when that datablock is gone. Only live wrappers are dereferenced: a
    binding whose owner is not among them was removed (or its memory now holds something
    else) and must not be read."""
    tree = next((t for t in bpy.data.node_groups if t.as_pointer() == pointer), None)
    if tree is None:
        return frozenset()
    live = set()
    for node in tree.nodes:
        live.add((node.as_pointer(), node.bl_rna.identifier))
        live.update((socket.as_pointer(), socket.bl_rna.identifier) for socket in node.inputs)
        if node.bl_idname == 'OPMGRamp':
            live.update((stop.as_pointer(), stop.bl_rna.identifier) for stop in node.stops)
    return frozenset(live)


def reads_placement(tree):
    try:
        return build(tree).placement
    except GraphError:
        return False
