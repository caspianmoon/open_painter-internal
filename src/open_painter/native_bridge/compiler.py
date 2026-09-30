"""Snapshot host node bindings into portable typed IR; C generates the shader.

This module resolves host socket links, image metadata and uniform owners. It
contains no shader expressions, math implementation or procedural shader code.
The existing compiler remains the independent legacy/export reference.
"""
import ctypes as C
import struct

from .core import NativeError
from ..evaluation.engine import glsl
from ..evaluation.engine.headless import VALUE, VECTOR, RGBA

TYPES = {VALUE: 1, VECTOR: 2, RGBA: 3}
CONSTANT, PARAMETER, CONVERT, MATH, VECTOR_MATH, COMBINE, COMPONENT, IMAGE, IMAGE_COLOR, GEOMETRY, OBJECT, NORMAL_MAP, PROCEDURAL, FETCH, UV, BLEND, EFFECT, MASK_FOLD = range(1, 19)


class Node(C.Structure):
    _fields_ = [('opcode', C.c_uint32), ('type', C.c_uint32), ('inputs', C.c_uint32 * 8),
                ('aux', C.c_uint32 * 4), ('value', C.c_double * 4), ('operation', C.c_char * 64)]


class Target(C.Structure):
    _fields_ = [('color', C.c_uint32), ('alpha', C.c_uint32), ('layout', C.c_uint32)]


# ``Node`` as packed bytes: one pack per IR node instead of four ctypes objects (a smart material
# builds some 2,000). Indices wrap to 32 bits as the ctypes fields did.
_NODE = struct.Struct('<II8I4I4d64s')
assert _NODE.size == C.sizeof(Node)


class Snapshot(glsl.Translator):
    def __init__(self, lib, graph, unit, inline=frozenset()):
        super().__init__(graph, unit, inline)
        self.lib = lib
        self._ir, self.count = bytearray(), 0   # packed ``Node`` records
        self.visiting = set()

    @property
    def ir(self):
        """The IR so far, as ``Node`` records."""
        return (Node * self.count).from_buffer_copy(self._ir)

    def add(self, opcode, kind, inputs=(), aux=(), values=(), operation=''):
        name = operation.encode()
        if len(name) >= 64:
            raise glsl.Unsupported('Native IR operation name exceeds 63 bytes')
        if len(inputs) > 8 or len(aux) > 4 or len(values) > 4:
            raise ValueError('Native IR node has too many inputs')
        result = self.count
        self._ir += _NODE.pack(opcode, TYPES[kind], *[index & 0xffffffff for index in inputs], *(0,) * (8 - len(inputs)),
                              *[item & 0xffffffff for item in aux], *(0,) * (4 - len(aux)),
                              *[float(value) for value in values], *(0.0,) * (4 - len(values)), name)
        self.count += 1
        return result

    def constant(self, value, kind):
        values = (float(value),) if kind == VALUE else tuple(float(v) for v in value)
        return self.add(CONSTANT, kind, values=values)

    def convert(self, index, have, want):
        return index if have == want else self.add(CONVERT, want, (index,))

    def input(self, socket, want=None):
        want = want or socket.type
        if socket.links:
            index, have = self.output(socket.links[0].from_socket)
        else:
            index, have = self.constant(socket.default_value, socket.type), socket.type
        return self.convert(index, have, want)

    def output(self, socket):
        key = id(socket)
        if key not in self.memo:
            if key in self.visiting:
                raise glsl.Unsupported('Cycle in native shader IR')
            self.visiting.add(key)
            try:
                self._emit(socket.node)
            finally:
                self.visiting.remove(key)
        if key not in self.memo:
            raise glsl.Unsupported(f'No native binding for {socket.node.bl_idname}.{socket.identifier}')
        return self.memo[key]

    def sampler(self, key, kind, **fields):
        return int(self._sampler(key, kind, **fields)[4:])

    def _geometry(self, kind):
        return self.add(GEOMETRY, VECTOR, aux=(self.sampler(('geometry', kind), kind),))

    def _node_OPBlend(self, node):
        inputs = tuple(self.input(s) for s in node.inputs)
        result = self.add(BLEND, RGBA, inputs, (bool(node.opaque),), operation=node.operation)
        self._set(node.outputs['Color'], self.convert(result, RGBA, VECTOR), VECTOR)
        self._set(node.outputs['Alpha'], self.add(COMPONENT, VALUE, (result,), (3,)), VALUE)

    def _node_OPEffect(self, node):
        inputs = tuple(self.input(s) for s in node.inputs)
        self._set(node.outputs['Color'], self.add(EFFECT, VECTOR, inputs, operation=node.operation), VECTOR)

    def _node_OPMaskFold(self, node):
        inputs = tuple(self.input(s, VALUE) for s in node.inputs)
        self._set(node.outputs['Value'], self.add(MASK_FOLD, VALUE, inputs, (bool(node.first),), operation=node.operation), VALUE)

    def _node_ShaderNodeMath(self, node):
        inputs = tuple(self.input(s, VALUE) for s in node.inputs)
        self._set(node.outputs[0], self.add(MATH, VALUE, inputs, (bool(getattr(node, 'use_clamp', False)),), operation=node.operation))

    def _node_ShaderNodeVectorMath(self, node):
        inputs = tuple(self.input(node.inputs[i], VECTOR) for i in range(3)) + (self.input(node.inputs[3], VALUE),)
        for kind, name in ((VECTOR, 'Vector'), (VALUE, 'Value')):
            self._set(node.outputs[name], self.add(VECTOR_MATH, kind, inputs, operation=node.operation), kind)

    def _node_ShaderNodeCombineXYZ(self, node):
        self._set(node.outputs[0], self.add(COMBINE, VECTOR, tuple(self.input(s, VALUE) for s in node.inputs)))

    def _node_ShaderNodeSeparateXYZ(self, node):
        vector = self.input(node.inputs[0], VECTOR)
        for axis, socket in enumerate(node.outputs):
            self._set(socket, self.add(COMPONENT, VALUE, (vector,), (axis,)), VALUE)

    def _node_ShaderNodeRGB(self, node):
        self._set(node.outputs[0], self.constant(node.outputs[0].default_value, RGBA), RGBA)

    def _node_ShaderNodeValue(self, node):
        socket = node.outputs[0]
        index = self.param_index.get(id(socket))
        if index is None:
            index = self.param_index[id(socket)] = len(self.params)
            self.params.append(socket)
        self._set(socket, self.add(PARAMETER, VALUE, aux=(index,)), VALUE)

    def _node_ShaderNodeTexImage(self, node):
        from ..evaluation.engine.resources import binding
        coordinate = self.input(node.inputs['Vector'], VECTOR)
        image, resource = glsl.live_image(node.image), binding(node)
        if image is None and resource is None:
            raw = self.constant((0, 0, 0, 0), RGBA)
        else:
            sampler = self.sampler(('image', id(node)), 'image', node=node)
            mapping = {'rgba': 0, 'rg': 1, 'r': 2}.get(resource.mapping, 0) if resource is not None else 0
            raw = self.add(IMAGE, RGBA, (coordinate,), (sampler, node.extension == 'REPEAT', node.interpolation == 'Closest', mapping))
        color, alpha = node.outputs['Color'], node.outputs['Alpha']
        mode = 0 if image is None else {'NONE': 1, 'CHANNEL_PACKED': 1, 'PREMUL': 2}.get(image.alpha_mode, 3)
        data = bool(image is not None and getattr(image.colorspace_settings, 'is_data', False))
        self._set(alpha, self.add(COMPONENT, VALUE, (raw,), (3,)), VALUE)
        self._set(color, self.add(IMAGE_COLOR, RGBA, (raw,), (mode, data, alpha.is_linked)), RGBA)

    def _node_ShaderNodeNewGeometry(self, node):
        self._set(node.outputs['Position'], self._geometry('position'), VECTOR)
        self._set(node.outputs['Normal'], self._geometry('normal'), VECTOR)
        self._set(node.outputs['True Normal'], self._geometry('normal'), VECTOR)

    def _node_ShaderNodeTexCoord(self, node):
        self._set(node.outputs['UV'], self.add(UV, VECTOR), VECTOR)
        self._set(node.outputs['Normal'], self._geometry('normal'), VECTOR)
        if node.object is not None:
            index = self._object(node.object)
            self._set(node.outputs['Object'], self.add(OBJECT, VECTOR, (self._geometry('position'),), (index,)), VECTOR)

    def _node_ShaderNodeNormalMap(self, node):
        if node.space != 'TANGENT':
            raise glsl.Unsupported(f'Normal map space {node.space}')
        inputs = (self.input(node.inputs['Color'], VECTOR), self.input(node.inputs['Strength'], VALUE))
        inputs += tuple(self._geometry(k) for k in ('tangent', 'bitangent', 'normal'))
        self._set(node.outputs['Normal'], self.add(NORMAL_MAP, VECTOR, inputs), VECTOR)

    def _node_OPProcedural(self, node):
        from ..evaluation.engine.procedural_glsl import canonical
        # Canonicalization is a project-format alias mapping, not shader codegen.
        kind = canonical(node.kind)
        inputs = (self.input(node.inputs['Vector'], VECTOR),) + tuple(self.input(node.inputs[k], VALUE) for k in ('Scale', 'Seed High', 'Seed Low'))
        self._set(node.outputs[0], self.add(PROCEDURAL, VALUE, inputs, operation=kind), VALUE)

    def _node_OPSpatialFilter(self, node):
        child = Snapshot(self.lib, self.graph, self.unit, self.inline)
        color, alpha = child.input(node.inputs['Color'], VECTOR), child.input(node.inputs['Alpha'], VALUE)
        program = child.compile([(color, alpha, 0)], [('Color', 'Alpha')])
        sampler = self.sampler(('filter', id(node)), 'filter', node=node, source=program)
        self._set(node.outputs['Color'], self.add(FETCH, RGBA, aux=(sampler,)), RGBA)

    def _node_NodeGroupInput(self, node):
        from ..evaluation.engine.formats import scalar_unit
        for socket in node.outputs:
            name = socket.name
            if name == 'UV':
                self._set(socket, self.add(UV, VECTOR), VECTOR)
            elif name in {'Position', 'Normal'}:
                self._set(socket, self._geometry(name.lower()), VECTOR)
            elif name in {'In', 'In:Alpha', 'Grouped', 'Grouped:Alpha'}:
                kind = 'backdrop' if name.startswith('In') else 'grouped'
                fetch = self.add(FETCH, RGBA, aux=(self.sampler(kind, kind), 1))
                if name.endswith(':Alpha'):
                    value = self.add(COMPONENT, VALUE, (fetch,), (1 if scalar_unit(self.unit.key) else 3,))
                    self._set(socket, value, VALUE)
                else:
                    if scalar_unit(self.unit.key):
                        fetch = self.add(COMPONENT, VALUE, (fetch,), (0,))
                        rgb = self.convert(fetch, VALUE, VECTOR)
                    else:
                        rgb = self.convert(fetch, RGBA, VECTOR)
                    self._set(socket, self.convert(rgb, VECTOR, socket.type), socket.type)
            else:
                self._set_import(socket, self._tree_unit(node.id_data))

    def _set_import(self, socket, unit):
        from ..evaluation.engine.formats import scalar_unit
        entry = next(((key, export) for key, export, name in unit.imports if name == socket.name), None)
        if entry is None:
            raise glsl.Unsupported(f'Unknown import {socket.name}')
        key, export = entry
        producer = self.graph.units[key]
        if key in self.inline:
            source = producer.group_output.inputs[export]
            if socket.type == RGBA:
                rgb = self.input(source, VECTOR)
                self._set(socket, self.convert(rgb, VECTOR, RGBA), RGBA)
            else:
                self._set(socket, self.input(source, socket.type), socket.type)
            return
        target, component = glsl.target_of(producer, export)
        sampler = self.sampler(('import', key, target), 'import', producer=(key, target))
        self.imports[socket.name] = (key, export)
        fetch = self.add(FETCH, RGBA, aux=(sampler,))
        if component in {'a', 'g', 'r'}:
            self._set(socket, self.add(COMPONENT, VALUE, (fetch,), ({'r': 0, 'g': 1, 'a': 3}[component],)), VALUE)
        else:
            if scalar_unit(producer.key):
                fetch = self.add(COMPONENT, VALUE, (fetch,), (0,))
                rgb = self.convert(fetch, VALUE, VECTOR)
            else:
                rgb = self.convert(fetch, RGBA, VECTOR)
            self._set(socket, self.convert(rgb, VECTOR, socket.type), socket.type)

    def compile(self, targets, names, classify=False):
        lib = self.lib
        lib.op_ir_compile.argtypes = [C.POINTER(Node), C.c_uint32, C.POINTER(Target), C.c_uint32, C.POINTER(C.c_void_p)]
        lib.op_ir_compile.restype = C.c_int
        lib.op_ir_free.argtypes, lib.op_ir_free.restype = [C.c_void_p], None
        data = self.ir
        outputs = (Target * len(targets))(*(Target(*t) for t in targets))
        binding = getattr(lib, '_op_binding', None)
        native_program = affine = None
        if binding is not None:
            source, handle = binding.program_create(bytes(data), bytes(outputs), len(self.params), len(self.objects))
            native_program = ProgramInputs(binding, handle)
            if classify:
                affine = self._classify(binding, data, outputs)
        else:
            pointer = C.c_void_p()
            status = lib.op_ir_compile(data, len(data), outputs, len(outputs), C.byref(pointer))
            if status:
                raise NativeError(status, f'Native shader compiler rejected unit {self.unit.key}')
            try:
                source = C.string_at(pointer).decode()
            finally:
                lib.op_ir_free(pointer)
        return glsl.Program(source, self.samplers, self.params, self.objects, names, self.imports, (), True,
                            native_program, affine)

    def _classify(self, binding, data, outputs):
        """The component map of an output affine in the unit's backdrop, or None.

        A spatial filter's input program is a sampler of its own: a filter that
        reads the backdrop hides that dependency from the IR, so it disqualifies.
        """
        backdrop = next((int(s.name[4:]) for s in self.samplers if s.kind == 'backdrop'), None)
        if backdrop is None or len(outputs) != 1:
            return None
        for sampler in self.samplers:
            if sampler.source is not None and any(inner.kind in {'backdrop', 'grouped'}
                                                  for program in glsl.programs(sampler.source)
                                                  for inner in program.samplers):
                return None
        return binding.ir_backdrop_affine(bytes(data), bytes(outputs), backdrop)

    def translate(self):
        from ..evaluation.engine.formats import scalar_unit
        names, targets = glsl.export_targets(self.unit), []
        for color, alpha in names:
            scalar = color == 'Value'
            rgb = self._export(color, VALUE if scalar else VECTOR) if color else self.constant((0, 0, 0), VECTOR)
            a = self._export(alpha, VALUE) if alpha else self.constant(1, VALUE)
            targets.append((rgb, a, 2 if scalar else int(scalar_unit(self.unit.key))))
        return self.compile(targets, names, classify=self.unit.key[0] == 'CHANNEL')


def fixed_source(name, fallback):
    from .lifecycle import active_library
    lib = active_library()
    if lib is None:
        return fallback
    binding = getattr(lib, '_op_binding', None)
    if binding is not None:
        return binding.shader_source(name)
    lib.op_shader_source.argtypes = [C.c_char_p]
    lib.op_shader_source.restype = C.c_char_p
    value = lib.op_shader_source(name.encode('ascii'))
    if value is None:
        raise ValueError('Native shader asset is missing: ' + name)
    return value.decode('utf-8')


def field_source(kind):
    from .lifecycle import active_library
    lib = active_library()
    if lib is None:
        return None
    binding = getattr(lib, '_op_binding', None)
    if binding is not None:
        return binding.field_source(kind)
    lib.op_ir_field.argtypes = [C.c_char_p, C.POINTER(C.c_void_p)]
    lib.op_ir_field.restype = C.c_int
    lib.op_ir_free.argtypes, lib.op_ir_free.restype = [C.c_void_p], None
    pointer = C.c_void_p()
    status = lib.op_ir_field(kind.encode('ascii'), C.byref(pointer))
    if status:
        raise NativeError(status, 'Native procedural field compilation failed')
    try:
        return C.string_at(pointer).decode('utf-8')
    finally:
        lib.op_ir_free(pointer)


class ProgramInputs:
    """UI values become a validated native parameter transaction before scheduling."""
    def __init__(self, binding, handle):
        self.binding, self.handle, self.revision = binding, handle, 0

    def update(self, program):
        import numpy as np
        from ..evaluation.engine.runtime import _object_matrix
        values = np.asarray([float(socket.default_value) for socket in program.params], np.float32)
        matrices = np.asarray([_object_matrix(obj) for obj in program.objects], np.float32)
        self.revision = self.binding.program_update(self.handle, values, matrices)
        return self.revision

    def data(self):
        return self.binding.program_uniforms(self.handle)
