"""Brush Graph authoring and deterministic per-dab evaluation.

Compilation snapshots RNA before the gesture starts. Resolved outputs, rather
than references to mutable nodes, are saved with the resulting stroke.
"""
import hashlib

import bpy
import numpy as np

MARKER = 'op_brush_graph'
FLOAT, VECTOR = 'NodeSocketFloat', 'NodeSocketVector'
INPUTS = {'Pressure': 1., 'Tilt': (0., 0., 0.), 'Position': (0., 0., 0.), 'Normal': (0., 0., 1.),
          'Distance': 0., 'Time': 0., 'Sample Index': 0.}
OUTPUTS = {'Radius': 1., 'Flow': 1., 'Opacity': 1., 'Angle': 0., 'Offset': (0., 0., 0.),
           'Alpha': 1., 'Base Color': (.5, .5, .5), 'Roughness': .5, 'Metallic': 0., 'Height': .5,
           'AO': 1., 'Emission': (0., 0., 0.), 'Opacity Value': 1., 'Normal': (.5, .5, 1.)}
FIELDS = {'Radius': 'radius', 'Flow': 'flow', 'Opacity': 'opacity', 'Angle': 'alpha_angle',
          'Base Color': 'base_color', 'Roughness': 'roughness', 'Metallic': 'metallic', 'Height': 'height',
          'AO': 'ao', 'Emission': 'emission', 'Opacity Value': 'opacity_value', 'Normal': 'normal'}


def is_brush(tree):
    return tree is not None and tree.bl_idname == 'OpenPainterGraph' and bool(tree.get(MARKER))


def socket(collection, name, value):
    result = collection.new(VECTOR if isinstance(value, tuple) else FLOAT, name)
    result.default_value = value
    return result


class _Node:
    @classmethod
    def poll(cls, tree):
        return is_brush(tree)


class OP_BrushInputs(_Node, bpy.types.Node):
    bl_idname, bl_label = 'OPBrushInputs', 'Brush Inputs'
    def init(self, context):
        for name, value in INPUTS.items():
            socket(self.outputs, name, value)


class OP_BrushValue(_Node, bpy.types.Node):
    bl_idname, bl_label = 'OPBrushValue', 'Value'
    value: bpy.props.FloatProperty(name='Value', default=1.)
    def init(self, context):
        socket(self.outputs, 'Value', 1.)
    def draw_buttons(self, context, layout):
        layout.prop(self, 'value')


class OP_BrushVector(_Node, bpy.types.Node):
    bl_idname, bl_label = 'OPBrushVector', 'Vector'
    value: bpy.props.FloatVectorProperty(name='Vector', size=3)
    def init(self, context):
        socket(self.outputs, 'Vector', (0., 0., 0.))
    def draw_buttons(self, context, layout):
        layout.prop(self, 'value')


class OP_BrushMath(_Node, bpy.types.Node):
    bl_idname, bl_label = 'OPBrushMath', 'Math'
    operation: bpy.props.EnumProperty(items=[(k, k.title(), '') for k in
        ('ADD', 'SUBTRACT', 'MULTIPLY', 'DIVIDE', 'MINIMUM', 'MAXIMUM', 'POWER', 'SINE', 'COSINE', 'ABSOLUTE')], default='MULTIPLY')
    def init(self, context):
        socket(self.inputs, 'A', 1.)
        socket(self.inputs, 'B', 1.)
        socket(self.outputs, 'Value', 1.)
    def draw_buttons(self, context, layout):
        layout.prop(self, 'operation', text='')


class OP_BrushVectorMath(_Node, bpy.types.Node):
    bl_idname, bl_label = 'OPBrushVectorMath', 'Vector Math'
    operation: bpy.props.EnumProperty(items=[(k, k.title(), '') for k in
        ('ADD', 'SUBTRACT', 'MULTIPLY', 'SCALE', 'DOT', 'CROSS', 'NORMALIZE', 'LENGTH')], default='ADD')
    def init(self, context):
        socket(self.inputs, 'A', (0., 0., 0.))
        socket(self.inputs, 'B', (0., 0., 0.))
        socket(self.inputs, 'Scale', 1.)
        socket(self.outputs, 'Vector', (0., 0., 0.))
        socket(self.outputs, 'Value', 0.)
    def draw_buttons(self, context, layout):
        layout.prop(self, 'operation', text='')


class OP_BrushRandom(_Node, bpy.types.Node):
    bl_idname, bl_label = 'OPBrushRandom', 'Seeded Random'
    seed: bpy.props.IntProperty(name='Seed', default=1, min=0)
    def init(self, context):
        socket(self.inputs, 'Minimum', 0.)
        socket(self.inputs, 'Maximum', 1.)
        socket(self.outputs, 'Value', 0.)
    def draw_buttons(self, context, layout):
        layout.prop(self, 'seed')


class OP_BrushOutput(_Node, bpy.types.Node):
    bl_idname, bl_label = 'OPBrushOutput', 'Brush Output'
    def init(self, context):
        for name, value in OUTPUTS.items():
            socket(self.inputs, name, value)
    def draw_buttons(self, context, layout):
        layout.label(text='Linked inputs override brush settings')
        layout.label(text='Radius, Flow, Opacity and Alpha multiply')


NODE_CLASSES = (OP_BrushInputs, OP_BrushValue, OP_BrushVector, OP_BrushMath, OP_BrushVectorMath, OP_BrushRandom, OP_BrushOutput)


def compile(tree):
    """A JSON-compatible frozen program; reject invalid graphs before painting."""
    if not is_brush(tree):
        raise ValueError('Choose a Brush Graph')
    from ..utils.ids import new_id
    if not tree.get('op_brush_id'):
        tree['op_brush_id'] = new_id()
    outputs = [node for node in tree.nodes if node.bl_idname == 'OPBrushOutput']
    if len(outputs) != 1:
        raise ValueError('A Brush Graph needs exactly one Brush Output')
    if len(tree.nodes) > 256:
        raise ValueError('A Brush Graph supports at most 256 nodes')
    incoming = {link.to_socket.as_pointer(): link for link in tree.links if not link.is_muted}
    steps, visited, visiting = [], {}, set()

    def read(input):
        link = incoming.get(input.as_pointer())
        if link is None:
            value = input.default_value
            return ['value', list(value) if input.bl_idname == VECTOR else float(value)]
        if link.from_socket.bl_idname != input.bl_idname:
            raise ValueError('Brush Graph socket types must match')
        return ['link', visit(link.from_node), link.from_socket.name]

    def visit(node):
        key = node.as_pointer()
        if key in visiting:
            raise ValueError('The Brush Graph contains a cycle')
        if key in visited:
            return visited[key]
        if node.bl_idname not in {cls.bl_idname for cls in NODE_CLASSES[:-1]}:
            raise ValueError('Unsupported Brush Graph node: ' + node.name)
        if node.mute:
            raise ValueError('Reconnect muted Brush Graph nodes before painting')
        visiting.add(key)
        if not node.get('op_brush_id'):
            node['op_brush_id'] = new_id()
        item = {'id': node['op_brush_id'], 'kind': node.bl_idname, 'inputs': {s.name: read(s) for s in node.inputs}}
        for name in ('value', 'operation', 'seed'):
            if hasattr(node, name):
                value = getattr(node, name)
                item[name] = list(value) if node.bl_idname == 'OPBrushVector' else value
        steps.append(item)
        visited[key] = len(steps) - 1
        visiting.remove(key)
        return visited[key]
    result = {s.name: read(s) for s in outputs[0].inputs if s.as_pointer() in incoming}
    program = {'version': 1, 'id': tree['op_brush_id'], 'steps': steps, 'outputs': result}
    evaluate(program, INPUTS, 0)  # reject undefined/nonfinite constants before a gesture
    return program


def evaluate(program, context, seed):
    from ..procedural_graph.cpu import math as scalar_math
    values = {}
    def read(ref):
        return np.asarray(ref[1], dtype=np.float64) if ref[0] == 'value' else values[(ref[1], ref[2])]
    for index, node in enumerate(program['steps']):
        inputs = {key: read(ref) for key, ref in node['inputs'].items()}
        kind = node['kind']
        if kind == 'OPBrushInputs':
            values.update({(index, key): np.asarray(context[key], dtype=np.float64) for key in INPUTS})
            continue
        if kind in {'OPBrushValue', 'OPBrushVector'}:
            values[(index, 'Vector' if kind == 'OPBrushVector' else 'Value')] = np.asarray(node['value'])
        elif kind == 'OPBrushMath':
            values[(index, 'Value')] = scalar_math(node['operation'], inputs['A'], inputs['B'])
        elif kind == 'OPBrushRandom':
            token = f"{seed}:{node['seed']}:{index}:{int(context['Sample Index'])}".encode()
            unit = int.from_bytes(hashlib.blake2b(token, digest_size=8).digest(), 'little') / float(2 ** 64)
            values[(index, 'Value')] = inputs['Minimum'] + unit * (inputs['Maximum'] - inputs['Minimum'])
        elif kind == 'OPBrushVectorMath':
            a, b, op = inputs['A'], inputs['B'], node['operation']
            vector, scalar = np.zeros(3), 0.
            if op == 'ADD': vector = a + b
            elif op == 'SUBTRACT': vector = a - b
            elif op == 'MULTIPLY': vector = a * b
            elif op == 'SCALE': vector = a * inputs['Scale']
            elif op == 'CROSS': vector = np.cross(a, b)
            elif op == 'DOT': scalar = np.dot(a, b)
            elif op == 'LENGTH': scalar = np.linalg.norm(a)
            elif op == 'NORMALIZE': vector = a / max(np.linalg.norm(a), 1e-20)
            values[(index, 'Vector')], values[(index, 'Value')] = vector, scalar
    result = {key: read(ref) for key, ref in program['outputs'].items()}
    if any(not np.isfinite(value).all() for value in result.values()):
        raise ValueError('The Brush Graph produced a nonfinite value')
    return result


class OP_OT_brush_graph(bpy.types.Operator):
    bl_idname, bl_label = 'open_painter.brush_graph', 'Edit Brush Graph'
    bl_options = {'REGISTER', 'UNDO'}
    def execute(self, context):
        settings = context.scene.open_painter.vector_brush
        tree = settings.node_group
        if tree is None:
            tree = bpy.data.node_groups.new('Brush Graph', 'OpenPainterGraph')
            tree[MARKER] = True
            source, output = tree.nodes.new('OPBrushInputs'), tree.nodes.new('OPBrushOutput')
            source.location, output.location = (-220, 0), (200, 0)
            tree.links.new(source.outputs['Pressure'], output.inputs['Flow'])
            settings.node_group = tree
        area = next((a for a in context.screen.areas if a.type == 'NODE_EDITOR'), context.area)
        if area is not None:
            area.type = 'NODE_EDITOR'
            area.spaces.active.tree_type = 'OpenPainterGraph'
            area.spaces.active.node_tree = tree
            area.spaces.active.pin = True
        return {'FINISHED'}


def add_menu(self, context):
    if not is_brush(getattr(context.space_data, 'edit_tree', None)):
        return
    for cls in NODE_CLASSES:
        op = self.layout.operator('node.add_node', text=cls.bl_label)
        op.type, op.use_transform = cls.bl_idname, True


def register():
    bpy.types.NODE_MT_add.append(add_menu)


def unregister():
    bpy.types.NODE_MT_add.remove(add_menu)


CLASSES = (*NODE_CLASSES, OP_OT_brush_graph)
