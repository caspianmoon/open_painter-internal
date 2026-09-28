"""The engine's evaluation of a Mask Graph: its steps emitted as compiler nodes.

``emit`` runs inside the graph's own unit (``shader_graph`` key ``('GRAPH', layer,
mask)``), so the graph renders as one GPU pass whose result the mask samples: an
edit elsewhere in the stack never renders it again, and its cost is reported on
its own. Every number is a uniform (``Graph.native_param``), so dragging a slider
in the graph compiles nothing. Formulas mirror ``cpu`` operation by operation.
"""
from .program import build


def emit(graph, mask):
    """The coverage of ``mask``'s graph in the unit being compiled (``graph``: an EngineGraph)."""
    from ..evaluation.engine.procedural_glsl import canonical
    program = build(mask.node_group)
    tree = mask.node_group.name
    outputs = {}
    math = graph.math

    def number(owner, field, index=None):
        return graph.native_param(owner, field, index, tree)

    def read(ref):
        if ref[0] == 'link':
            return outputs[(ref[1], ref[2])]
        if ref[0] == 'uv':
            return graph.coord_uv()
        return number(ref[1], 'default_value')

    def clamp(value):
        return math('MINIMUM', math('MAXIMUM', value, 0.0), 1.0)

    def toward(a, x, f):
        return math('ADD', a, math('MULTIPLY', math('SUBTRACT', x, a), f))

    for position, step in enumerate(program.steps):
        node, kind, inputs = step.node, step.kind, step.inputs
        with graph.role(f'MaskGraph:{tree}:{node.name}'):
            if kind == 'coordinates':
                outputs[(position, 'UV')] = graph.coord_uv()
                outputs[(position, 'Position')] = graph.coord_position()
                outputs[(position, 'Normal')] = graph.coord_normal()
                continue
            if kind == 'value':
                value = number(node, 'value')
            elif kind == 'procedural':
                value = graph._procedural(canonical(node.kind), read(inputs['Vector']), number(node, 'scale'),
                                          graph.native_seed(node, 'seed', tree))
            elif kind == 'text':
                from ..evaluation.text_source import ensure
                getter = lambda node=node: ensure(node.text, int(graph.ts.resolution))
                value = graph.separate(graph.image(getter(), read(inputs['Vector']), getter=getter).color)[0]
            elif kind == 'mesh_map':
                value = graph.separate(graph.mesh_map(node.kind).color)[0]
            elif kind == 'math':
                a = read(inputs['A'])
                b = read(inputs['B']) if 'B' in inputs else 0.0
                operation = node.operation
                value = math('SUBTRACT', 1.0, a) if operation == 'ONE_MINUS' else math(operation, a, b)
                if node.use_clamp:
                    value = clamp(value)
            elif kind == 'mix':
                value = mix(math, node.blend, read(inputs['Factor']), read(inputs['A']), read(inputs['B']), toward)
                if node.use_clamp:
                    value = clamp(value)
            elif kind == 'ramp':
                stops = [(number(node.stops[i], 'position'), number(node.stops[i], 'value')) for i in step.order]
                value = ramp(math, clamp, node.interpolation, read(inputs['Fac']), stops)
            elif kind == 'levels':
                low, high = number(node, 'black'), number(node, 'white')
                normalized = clamp(math('DIVIDE', math('SUBTRACT', read(inputs['Value']), low),
                                        math('SUBTRACT', high, low)))
                exponent = math('DIVIDE', 1.0, math('MAXIMUM', number(node, 'gamma'), 1e-5))
                width = math('SUBTRACT', number(node, 'output_white'), number(node, 'output_black'))
                value = math('ADD', number(node, 'output_black'),
                             math('MULTIPLY', math('POWER', normalized, exponent), width))
            elif kind == 'mapping':
                x, y, z = graph.separate(read(inputs['Vector']))
                x = math('MULTIPLY', math('SUBTRACT', x, 0.5), number(node, 'scale', 0))
                y = math('MULTIPLY', math('SUBTRACT', y, 0.5), number(node, 'scale', 1))
                angle = number(node, 'rotation')
                c, s = math('COSINE', angle), math('SINE', angle)
                u = math('SUBTRACT', math('MULTIPLY', c, x), math('MULTIPLY', s, y))
                v = math('ADD', math('MULTIPLY', s, x), math('MULTIPLY', c, y))
                outputs[(position, 'Vector')] = graph.combine(
                    math('ADD', math('ADD', u, 0.5), number(node, 'offset', 0)),
                    math('ADD', math('ADD', v, 0.5), number(node, 'offset', 1)), z)
                continue
            else:
                raise ValueError(f'Unknown Mask Graph step {kind}')
        outputs[(position, 'Value')] = value
    return read(program.result)


def mix(math, blend, f, a, b, toward):
    if blend == 'MIX':
        return toward(a, b, f)
    if blend == 'MULTIPLY':
        return toward(a, math('MULTIPLY', a, b), f)
    if blend == 'SCREEN':
        return toward(a, math('SUBTRACT', 1.0, math('MULTIPLY', math('SUBTRACT', 1.0, a), math('SUBTRACT', 1.0, b))), f)
    if blend == 'OVERLAY':
        low = math('LESS_THAN', a, 0.5)
        dark = math('MULTIPLY', math('MULTIPLY', 2.0, a), b)
        light = math('SUBTRACT', 1.0, math('MULTIPLY', math('MULTIPLY', 2.0, math('SUBTRACT', 1.0, a)),
                                           math('SUBTRACT', 1.0, b)))
        return toward(a, math('ADD', math('MULTIPLY', low, dark), math('MULTIPLY', math('SUBTRACT', 1.0, low), light)), f)
    if blend == 'ADD':
        return math('ADD', a, math('MULTIPLY', b, f))
    if blend == 'SUBTRACT':
        return math('SUBTRACT', a, math('MULTIPLY', b, f))
    if blend == 'DIFFERENCE':
        return toward(a, math('ABSOLUTE', math('SUBTRACT', a, b)), f)
    if blend == 'LIGHTEN':
        return toward(a, math('MAXIMUM', a, b), f)
    if blend == 'DARKEN':
        return toward(a, math('MINIMUM', a, b), f)
    raise ValueError(f'Unknown Mask Graph blend {blend}')


def ramp(math, clamp, mode, x, stops):
    """v₀ + Σ (vᵢ − vᵢ₋₁)·wᵢ over the stops in position order, wᵢ the segment weight:
    clamp((x − pᵢ₋₁) / max(pᵢ − pᵢ₋₁, 10⁻⁶)), smoothstepped for Ease, a step at pᵢ for Constant."""
    result = stops[0][1]
    for (p0, v0), (p1, v1) in zip(stops, stops[1:]):
        step = math('SUBTRACT', v1, v0)
        if mode == 'CONSTANT':
            weight = math('SUBTRACT', 1.0, math('LESS_THAN', x, p1))
        else:
            weight = clamp(math('DIVIDE', math('SUBTRACT', x, p0), math('MAXIMUM', math('SUBTRACT', p1, p0), 1e-6)))
            if mode == 'EASE':
                weight = math('MULTIPLY', math('MULTIPLY', weight, weight),
                              math('SUBTRACT', 3.0, math('MULTIPLY', 2.0, weight)))
        result = math('ADD', result, math('MULTIPLY', step, weight))
    return result
