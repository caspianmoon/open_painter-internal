"""Translate one compiled unit (a headless node tree) into an Open Painter GPU program.

A unit renders in texture space: one fragment per texel, ``uv`` at the texel
centre, world position / normal / tangent frame read from the set's geometry
rasters. Node semantics are ported from Blender 5.2's GLSL (Math, Vector Math,
implicit socket conversions, Image Texture alpha handling) so the engine computes
what the node graph described. Image sampling follows the CPU reference
(``blend_modes.sample_uv``: bilinear at texel centres, no mipmaps), so the engine's
textures match the exported ones rather than a view-dependent material sample.

Everything that changes without a structural edit is data, never code: parameter
values live in a uniform buffer, images are sampler bindings. Two units with the same
structure therefore produce the same source text and share one compiled program.
"""
from dataclasses import dataclass, field

from .headless import RGBA, VALUE, VECTOR

GLSL_TYPES = {VALUE: 'float', VECTOR: 'vec3', RGBA: 'vec4'}
# Rec. 709 luminance: the coefficients Blender's default OCIO configuration gives the
# colour-to-float socket conversion (gpu_shader_codegen_lib float_from_float4).
LUMINANCE = (0.2126, 0.7152, 0.0722)

# Group input sockets a unit reads from the engine rather than from another node.
BUILTIN_INPUTS = {'UV', 'Position', 'Normal'}


class Unsupported(ValueError):
    """The unit uses something the engine cannot evaluate on the GPU."""


PREAMBLE = r'''
float op_safe_divide(float a, float b) { return (b != 0.0) ? (a / b) : 0.0; }
vec3 op_safe_divide(vec3 a, vec3 b) { return vec3(op_safe_divide(a.x, b.x), op_safe_divide(a.y, b.y), op_safe_divide(a.z, b.z)); }
float op_compatible_pow(float x, float y) {
  if (y == 0.0) { return 1.0; }
  if (x < 0.0) {
    if (mod(-y, 2.0) == 0.0) { return pow(-x, y); }
    return -pow(-x, y);
  }
  else if (x == 0.0) { return 0.0; }
  return pow(x, y);
}
vec3 op_compatible_pow(vec3 a, vec3 b) { return vec3(op_compatible_pow(a.x, b.x), op_compatible_pow(a.y, b.y), op_compatible_pow(a.z, b.z)); }
float op_compatible_mod(float a, float b) {
  if (b != 0.0) { int n = int(a / b); return a - float(n) * b; }
  return 0.0;
}
vec3 op_compatible_mod(vec3 a, vec3 b) { return vec3(op_compatible_mod(a.x, b.x), op_compatible_mod(a.y, b.y), op_compatible_mod(a.z, b.z)); }
float op_wrap(float a, float b, float c) {
  float range = b - c;
  float s = (a != b) ? floor((a - c) / range) : 1.0;
  return (range != 0.0) ? a - range * s : c;
}
vec3 op_wrap(vec3 a, vec3 b, vec3 c) { return vec3(op_wrap(a.x, b.x, c.x), op_wrap(a.y, b.y, c.y), op_wrap(a.z, b.z, c.z)); }
float op_power(float a, float b) {
  if (a >= 0.0) { return op_compatible_pow(a, b); }
  float fraction = mod(abs(b), 1.0);
  if (fraction > 0.999 || fraction < 0.001) { return op_compatible_pow(a, floor(b + 0.5)); }
  return 0.0;
}
float op_pingpong(float a, float b) { return (b != 0.0) ? abs(fract((a - b) / (b * 2.0)) * b * 2.0 - b) : 0.0; }
float op_smoothmin(float a, float b, float c) {
  if (c != 0.0) { float h = max(c - abs(a - b), 0.0) / c; return min(a, b) - h * h * h * c * (1.0 / 6.0); }
  return min(a, b);
}
vec3 op_normalize(vec3 a) { float l = dot(a, a); return (l > 0.0) ? a * inversesqrt(l) : a; }
vec3 op_project(vec3 a, vec3 b) { float l = dot(b, b); return (l != 0.0) ? (dot(a, b) / l) * b : vec3(0.0); }
vec3 op_snap(vec3 a, vec3 b) { return floor(op_safe_divide(a, b)) * b; }

/* blend_modes.sample_uv: bottom-left origin, texel centres at (i + 0.5) / size, bilinear or
 * nearest, repeat (u % 1) or clamped to the edge. Explicit weights keep the CPU's float math. */
vec4 op_fetch(sampler2D image, ivec2 p, ivec2 size, bool repeat) {
  /* p >= -1 here (a texel beside the first); GLSL leaves % undefined for negative operands. */
  if (repeat) { p = (p + size) % size; }
  else { p = clamp(p, ivec2(0), size - 1); }
  return texelFetch(image, p, 0);
}
vec4 op_sample(sampler2D image, vec2 uv, bool repeat, bool nearest) {
  ivec2 size = textureSize(image, 0);
  vec2 s = repeat ? (uv - floor(uv)) : clamp(uv, 0.0, 1.0);
  vec2 xy = s * vec2(size) - 0.5;
  if (nearest) { return op_fetch(image, ivec2(floor(xy + 0.5)), size, repeat); }
  ivec2 i = ivec2(floor(xy));
  vec2 f = xy - vec2(i);
  vec4 a = op_fetch(image, i, size, repeat) * (1.0 - f.x) + op_fetch(image, i + ivec2(1, 0), size, repeat) * f.x;
  vec4 b = op_fetch(image, i + ivec2(0, 1), size, repeat) * (1.0 - f.x) + op_fetch(image, i + ivec2(1, 1), size, repeat) * f.x;
  return a * (1.0 - f.y) + b * f.y;
}
vec4 op_unpremultiply(vec4 c) { return (c.a == 0.0 || c.a == 1.0) ? c : vec4(c.rgb / c.a, c.a); }
'''

# Blender's Math node (gpu_shader_common_math.glsl) as expressions of a, b, c.
MATH = {
    'ADD': '({a} + {b})', 'SUBTRACT': '({a} - {b})', 'MULTIPLY': '({a} * {b})', 'DIVIDE': 'op_safe_divide({a}, {b})',
    'POWER': 'op_power({a}, {b})', 'LOGARITHM': '(({a} > 0.0 && {b} > 0.0) ? log2({a}) / log2({b}) : 0.0)',
    'SQRT': '(({a} > 0.0) ? sqrt({a}) : 0.0)', 'INVERSE_SQRT': 'inversesqrt({a})', 'ABSOLUTE': 'abs({a})',
    'RADIANS': 'radians({a})', 'DEGREES': 'degrees({a})', 'MINIMUM': 'min({a}, {b})', 'MAXIMUM': 'max({a}, {b})',
    'LESS_THAN': '(({a} < {b}) ? 1.0 : 0.0)', 'GREATER_THAN': '(({a} > {b}) ? 1.0 : 0.0)',
    'ROUND': 'floor({a} + 0.5)', 'FLOOR': 'floor({a})', 'CEIL': 'ceil({a})', 'FRACT': '({a} - floor({a}))',
    'MODULO': 'op_compatible_mod({a}, {b})', 'FLOORED_MODULO': '(({b} != 0.0) ? {a} - floor({a} / {b}) * {b} : 0.0)',
    'TRUNC': 'trunc({a})', 'SNAP': '(floor(op_safe_divide({a}, {b})) * {b})', 'PINGPONG': 'op_pingpong({a}, {b})',
    'WRAP': 'op_wrap({a}, {b}, {c})', 'SINE': 'sin({a})', 'COSINE': 'cos({a})', 'TANGENT': 'tan({a})',
    'SINH': 'sinh({a})', 'COSH': 'cosh({a})', 'TANH': 'tanh({a})',
    'ARCSINE': '(({a} <= 1.0 && {a} >= -1.0) ? asin({a}) : 0.0)',
    'ARCCOSINE': '(({a} <= 1.0 && {a} >= -1.0) ? acos({a}) : 0.0)', 'ARCTANGENT': 'atan({a})',
    'ARCTAN2': '(({a} == 0.0 && {b} == 0.0) ? 0.0 : atan({a}, {b}))', 'SIGN': 'sign({a})', 'EXPONENT': 'exp({a})',
    'COMPARE': '((abs({a} - {b}) <= max({c}, 1e-5)) ? 1.0 : 0.0)', 'MULTIPLY_ADD': '({a} * {b} + {c})',
    'SMOOTH_MIN': 'op_smoothmin({a}, {b}, {c})', 'SMOOTH_MAX': '(-op_smoothmin(-({a}), -({b}), {c}))',
}
# Vector Math (gpu_shader_material_vector_math.glsl): (vector expression, value expression).
VECTOR_MATH = {
    'ADD': ('({a} + {b})', None), 'SUBTRACT': ('({a} - {b})', None), 'MULTIPLY': ('({a} * {b})', None),
    'DIVIDE': ('op_safe_divide({a}, {b})', None), 'CROSS_PRODUCT': ('cross({a}, {b})', None),
    'PROJECT': ('op_project({a}, {b})', None), 'REFLECT': ('reflect({a}, op_normalize({b}))', None),
    'DOT_PRODUCT': (None, 'dot({a}, {b})'), 'DISTANCE': (None, 'distance({a}, {b})'), 'LENGTH': (None, 'length({a})'),
    'SCALE': ('({a} * {s})', None), 'NORMALIZE': ('op_normalize({a})', None), 'SNAP': ('op_snap({a}, {b})', None),
    'FLOOR': ('floor({a})', None), 'CEIL': ('ceil({a})', None), 'MODULO': ('op_compatible_mod({a}, {b})', None),
    'WRAP': ('op_wrap({a}, {b}, {c})', None), 'FRACTION': ('fract({a})', None), 'ABSOLUTE': ('abs({a})', None),
    'POWER': ('op_compatible_pow({a}, {b})', None), 'SIGN': ('sign({a})', None),
    'ROUND': ('floor({a} + 0.5)', None), 'MINIMUM': ('min({a}, {b})', None), 'MAXIMUM': ('max({a}, {b})', None),
    'SINE': ('sin({a})', None), 'COSINE': ('cos({a})', None), 'TANGENT': ('tan({a})', None),
    'REFRACT': ('refract({a}, op_normalize({b}), {s})', None), 'FACEFORWARD': ('faceforward({a}, {b}, {c})', None),
    'MULTIPLY_ADD': ('({a} * {b} + {c})', None),
}


def literal(value):
    """A GLSL float literal that round-trips the Python float."""
    value = float(value)
    if value != value or value in (float('inf'), float('-inf')):
        raise Unsupported('Non-finite constant in a shader unit')
    text = repr(value)
    if 'e' in text or 'E' in text:
        mantissa, exponent = text.lower().split('e')
        if '.' not in mantissa:
            mantissa += '.0'
        return f'{mantissa}e{int(exponent)}'
    return text if '.' in text else text + '.0'


def constant(value, kind):
    if kind == VALUE:
        return literal(value)
    values = tuple(value)
    if kind == VECTOR:
        return 'vec3({}, {}, {})'.format(*(literal(v) for v in values[:3]))
    values = values + (1.0,) * (4 - len(values))
    return 'vec4({}, {}, {}, {})'.format(*(literal(v) for v in values[:4]))


def convert(expression, have, want):
    """Blender's implicit socket conversions (gpu_shader_codegen_lib.glsl)."""
    if have == want:
        return expression
    if want == VALUE:
        if have == VECTOR:
            return f'(({expression}).x + ({expression}).y + ({expression}).z) * (1.0 / 3.0)'
        return 'dot(({}).rgb, vec3({}, {}, {}))'.format(expression, *(literal(v) for v in LUMINANCE))
    if want == VECTOR:
        return f'({expression}).rgb' if have == RGBA else f'vec3({expression})'
    return f'vec4({expression}, 1.0)' if have == VECTOR else f'vec4(vec3({expression}), 1.0)'


@dataclass
class Sampler:
    """One sampler slot of a program and what the engine binds to it."""
    name: str
    kind: str            # 'image', 'import', 'backdrop', 'grouped', 'position', 'normal', 'tangent', 'bitangent'
    node: object = None  # the image node for 'image'
    producer: tuple = None   # (unit key, target index) for 'import'
    source: object = None    # canonical RGBA input program of a spatial pass


@dataclass
class Program:
    """GLSL of one unit plus the data the engine feeds it per render."""
    source: str
    samplers: list = field(default_factory=list)
    params: list = field(default_factory=list)       # headless Value sockets, in uniform order
    objects: list = field(default_factory=list)      # bpy Objects whose inverse world matrix is a uniform
    targets: list = field(default_factory=list)      # [(color export, alpha export or None)]
    imports: dict = field(default_factory=dict)      # input socket name -> (producer key, export name)
    procedurals: tuple = ()                          # catalog sources the program evaluates
    native: bool = False                            # complete source generated by the C IR compiler
    native_program: object = None                   # owned immutable IR and revisioned uniform bank
    # Component map of an output that is a per-texel affine function of the backdrop
    # (op_ir_backdrop_affine; 255: independent), or None. Native CHANNEL programs only.
    affine: tuple = None

    @property
    def library(self):
        from .procedural_glsl import library
        return library(self.procedurals) if self.procedurals else ''

    @property
    def parameter_slots(self):
        return (len(self.params) + 3) // 4


def export_targets(unit):
    """Render targets of a unit: each export pairs a colour with its alpha."""
    names = [name for name, (kind, _) in unit.exports.items() if kind == 'socket']
    targets, used = [], set()
    for color, alpha in (('Out', 'Out:Alpha'), ('Group', 'Group:Alpha'), ('Color', 'Alpha'), ('Value', None)):
        if color in names or (alpha and alpha in names):
            targets.append((color if color in names else None, alpha if alpha and alpha in names else None))
            used.update(filter(None, (color, alpha)))
    extra = [name for name in names if name not in used]
    if extra:
        raise Unsupported(f'Unit {unit.key} exports {extra}, which the engine does not lay out')
    return targets


def target_of(unit, export):
    """(target index, component) where a unit's export lands."""
    for index, (color, alpha) in enumerate(export_targets(unit)):
        if export == color:
            from .formats import scalar_unit
            return index, ('rrr' if scalar_unit(unit.key) else 'rgb') if color != 'Value' else 'r'
        if export == alpha:
            from .formats import scalar_unit
            return index, 'g' if scalar_unit(unit.key) else 'a'
    raise Unsupported(f'Unit {unit.key} has no export {export}')


def _tree_units(graph):
    """The unit of each tree (by id), kept on the graph while its units stand: every program
    translated used to index all ~1,750 units of a deep stack again."""
    key = (getattr(graph, '_units_token', None), len(graph.units))
    cached = getattr(graph, '_tree_units', None)
    if cached is None or cached[0] != key:
        cached = (key, {id(item.tree): item for item in graph.units.values() if item.tree is not None})
        graph._tree_units = cached
    return cached[1]


class Translator:
    """Walks a unit tree from its group output; emits one GLSL variable per node output."""

    def __init__(self, graph, unit, inline=frozenset()):
        self.graph, self.unit = graph, unit
        self.inline = inline          # producer units compiled into this program instead of sampled
        self.lines = []
        self.memo = {}           # output socket id -> (expression, type)
        self.samplers = []
        self.sampler_index = {}  # key -> sampler name
        self.params = []
        self.param_index = {}
        self.objects = []
        self.object_index = {}
        self.imports = {}
        self.procedurals = set()
        self.counter = 0

    # ---- resources ---------------------------------------------------------------

    def _sampler(self, key, kind, **fields):
        name = self.sampler_index.get(key)
        if name is None:
            name = f'op_s{len(self.samplers)}'
            self.sampler_index[key] = name
            self.samplers.append(Sampler(name, kind, **fields))
        return name

    def _param(self, socket):
        index = self.param_index.get(id(socket))
        if index is None:
            index = self.param_index[id(socket)] = len(self.params)
            self.params.append(socket)
        return f'op_params.p[{index >> 2}].{"xyzw"[index & 3]}'

    def _object(self, obj):
        """Uniform index of an object's inverse world matrix (Texture Coordinate > Object)."""
        index = self.object_index.get(id(obj))
        if index is None:
            index = self.object_index[id(obj)] = len(self.objects)
            self.objects.append(obj)
        return index

    def _variable(self, kind, expression):
        name = f'v{self.counter}'
        self.counter += 1
        self.lines.append(f'  {GLSL_TYPES[kind]} {name} = {expression};')
        return name

    # ---- values --------------------------------------------------------------------

    def input(self, socket, want=None):
        """Expression of an input socket converted to ``want`` (default: the socket's own type)."""
        want = want or socket.type
        if socket.links:
            source = socket.links[0].from_socket
            expression, have = self.output(source)
            return convert(expression, have, want)
        return convert(constant(socket.default_value, socket.type), socket.type, want)

    def output(self, socket):
        key = id(socket)
        if key not in self.memo:
            self._emit(socket.node)
            if key not in self.memo:
                raise Unsupported(f'{socket.node.bl_idname}.{socket.identifier} is not evaluated by the engine')
        return self.memo[key]

    def _set(self, socket, expression, kind=None):
        self.memo[id(socket)] = (expression, kind or socket.type)

    def _tree_unit(self, tree):
        """The unit compiling ``tree`` (an inlined producer's), else this program's own."""
        found = _tree_units(self.graph).get(id(tree))
        if found is not None and found.tree is not tree:
            self.graph._tree_units = None   # an id reused since the index was taken
            found = _tree_units(self.graph).get(id(tree))
        return found if found is not None and found.tree is tree else self.unit

    # ---- nodes -----------------------------------------------------------------------

    def _emit(self, node):
        method = getattr(self, '_node_' + node.bl_idname, None)
        if method is None:
            raise Unsupported(f'The engine does not evaluate {node.bl_idname} nodes')
        method(node)

    def _node_OPBlend(self, node):
        from ..shader_math import Math, Signal, blend, is_socket
        from .headless import Tree
        # Retain trees for the lifetime of the translator: memo keys use ids.
        trees = self.__dict__.setdefault('_blend_trees', [])
        tree = Tree('Legacy blend reference')
        trees.append(tree)
        values = [s.links[0].from_socket if s.links else s.default_value for s in node.inputs]
        base, alpha, source, source_alpha, coverage = values
        result = blend(Math(tree), Signal(base, 1.0 if node.opaque else alpha),
                       Signal(source, source_alpha), coverage, node.operation)
        for socket, value in zip(node.outputs, (result.color, result.alpha)):
            expression, kind = self.output(value) if is_socket(value) else (constant(value, socket.type), socket.type)
            self._set(socket, convert(expression, kind, socket.type))

    def _node_OPEffect(self, node):
        from types import SimpleNamespace
        from ..shader_math import Math, Signal
        from ..shader_effects import apply_effect
        from .headless import Tree
        tree = Tree('Legacy effect reference')
        self.__dict__.setdefault('_semantic_trees', []).append(tree)
        builder = Math(tree)
        values = {s.name: s.links[0].from_socket if s.links else s.default_value for s in node.inputs}
        def param(_effect, field, index=None):
            if field == 'matrix':
                return builder.separate(values['row' + str(index // 3)])[index % 3]
            # The reference eagerly builds unused histogram intermediates.
            # Only these dead inputs are absent from the semantic signature.
            if field not in values and ((node.operation == 'HISTOGRAM_RANGE' and field == 'amount')
                    or (node.operation == 'THRESHOLD' and field in {'softness', 'contrast'})):
                return 0.0
            value = values[field]
            return builder.separate(value)[index] if index is not None else value
        builder.param = param
        builder.effect_image = lambda _effect: values['detail']
        effect = SimpleNamespace(kind=node.operation, enabled=False, strength=0,
                                 image=node.operation == 'NORMAL_COMBINE')
        result = apply_effect(builder, Signal(values['source']), effect)
        expression, kind = self.output(result.color)
        self._set(node.outputs['Color'], convert(expression, kind, VECTOR), VECTOR)

    def _node_OPMaskFold(self, node):
        from contextlib import nullcontext
        from types import SimpleNamespace
        from ..shader_math import Math
        from ..shader_graph import Graph
        from .headless import Tree
        tree = Tree('Legacy mask reference')
        self.__dict__.setdefault('_semantic_trees', []).append(tree)
        builder = Math(tree)
        values = {s.name.lower(): s.links[0].from_socket if s.links else s.default_value for s in node.inputs}
        builder.param = lambda _owner, field: values[field]
        builder.role = lambda _role: nullcontext()
        builder.scalar_mix = lambda a, b, t: Graph.scalar_mix(builder, a, b, t)
        mask = SimpleNamespace(id='reference', source='FILL', operation=node.operation)
        result = Graph._mask_fold(builder, SimpleNamespace(id='reference'), mask,
                                  values['value'], None if node.first else values['previous'])
        self._set(node.outputs['Value'], self.output(result)[0], VALUE)

    def _node_ShaderNodeMath(self, node):
        template = MATH.get(node.operation)
        if template is None:
            raise Unsupported(f'Math operation {node.operation}')
        a, b, c = (self.input(socket, VALUE) for socket in node.inputs)
        expression = template.format(a=a, b=b, c=c)
        if getattr(node, 'use_clamp', False):
            expression = f'clamp({expression}, 0.0, 1.0)'
        self._set(node.outputs[0], self._variable(VALUE, expression))

    def _node_ShaderNodeVectorMath(self, node):
        entry = VECTOR_MATH.get(node.operation)
        if entry is None:
            raise Unsupported(f'Vector Math operation {node.operation}')
        vector_template, value_template = entry
        a, b, c = (self.input(node.inputs[i], VECTOR) for i in range(3))
        s = self.input(node.inputs[3], VALUE)
        vector, value = node.outputs['Vector'], node.outputs['Value']
        if vector_template is not None:
            self._set(vector, self._variable(VECTOR, vector_template.format(a=a, b=b, c=c, s=s)))
            self._set(value, '0.0', VALUE)
        else:
            self._set(value, self._variable(VALUE, value_template.format(a=a, b=b, c=c, s=s)))
            self._set(vector, 'vec3(0.0)', VECTOR)

    def _node_ShaderNodeCombineXYZ(self, node):
        x, y, z = (self.input(socket, VALUE) for socket in node.inputs)
        self._set(node.outputs[0], self._variable(VECTOR, f'vec3({x}, {y}, {z})'))

    def _node_ShaderNodeSeparateXYZ(self, node):
        vector = self.input(node.inputs[0], VECTOR)
        if not vector.isidentifier():
            vector = self._variable(VECTOR, vector)
        for socket, axis in zip(node.outputs, 'xyz'):
            self._set(socket, f'{vector}.{axis}', VALUE)

    def _node_ShaderNodeRGB(self, node):
        self._set(node.outputs[0], constant(node.outputs[0].default_value, RGBA), RGBA)

    def _node_ShaderNodeValue(self, node):
        self._set(node.outputs[0], self._param(node.outputs[0]), VALUE)

    def _node_ShaderNodeTexImage(self, node):
        from .resources import binding, decode
        coordinate = self.input(node.inputs['Vector'], VECTOR)
        image = live_image(node.image)
        resource = binding(node)
        if image is None and resource is None:
            raw = 'vec4(0.0)'
        else:
            name = self._sampler(('image', id(node)), 'image', node=node)
            repeat = 'true' if node.extension == 'REPEAT' else 'false'
            nearest = 'true' if node.interpolation == 'Closest' else 'false'
            raw = self._variable(RGBA, f'op_sample({name}, ({coordinate}).xy, {repeat}, {nearest})')
            if resource is not None:
                raw = self._variable(RGBA, decode(raw, resource.mapping))
        color, alpha = node.outputs['Color'], node.outputs['Alpha']
        self._set(alpha, f'{raw}.a', VALUE)
        self._set(color, self._variable(RGBA, image_color(image, raw, alpha.is_linked)))

    def _node_ShaderNodeNewGeometry(self, node):
        self._set(node.outputs['Position'], self._geometry('position'), VECTOR)
        self._set(node.outputs['Normal'], self._geometry('normal'), VECTOR)
        self._set(node.outputs['True Normal'], self._geometry('normal'), VECTOR)

    def _geometry(self, kind):
        name = self._sampler(('geometry', kind), kind)
        # Clamped: a set without assigned geometry binds a one-texel neutral surface.
        return f'texelFetch({name}, min(op_texel, textureSize({name}, 0) - 1), 0).xyz'

    def _node_ShaderNodeTexCoord(self, node):
        self._set(node.outputs['UV'], 'vec3(op_uv, 0.0)', VECTOR)
        self._set(node.outputs['Normal'], self._geometry('normal'), VECTOR)
        obj = node.object
        if obj is not None:
            index = self._object(obj)
            position = self._geometry('position')
            self._set(node.outputs['Object'],
                      self._variable(VECTOR, f'(op_params.objects[{index}] * vec4({position}, 1.0)).xyz'))

    def _node_ShaderNodeNormalMap(self, node):
        if node.space != 'TANGENT':
            raise Unsupported(f'Normal Map space {node.space}')
        color = self.input(node.inputs['Color'], VECTOR)
        strength = self.input(node.inputs['Strength'], VALUE)
        tangent, bitangent, normal = self._geometry('tangent'), self._geometry('bitangent'), self._geometry('normal')
        t = self._variable(VECTOR, f'{color} * 2.0 - 1.0')
        mapped = self._variable(VECTOR, f'op_normalize({t}.x * {tangent} + {t}.y * {bitangent} + {t}.z * {normal})')
        self._set(node.outputs['Normal'],
                  self._variable(VECTOR, f'op_normalize(mix({normal}, {mapped}, max({strength}, 0.0)))'))

    def _node_OPProcedural(self, node):
        from .procedural_glsl import canonical, function_name, KINDS
        kind = canonical(node.kind)
        if kind not in KINDS:
            raise Unsupported(f'Procedural source {node.kind}')
        self.procedurals.add(kind)
        coordinate = self.input(node.inputs['Vector'], VECTOR)
        scale = self.input(node.inputs['Scale'], VALUE)
        high = self.input(node.inputs['Seed High'], VALUE)
        low = self.input(node.inputs['Seed Low'], VALUE)
        seed = f'(uint(floor({high} + 0.5)) * 65536u + uint(floor({low} + 0.5)))'
        self._set(node.outputs[0], self._variable(
            VALUE, f'{function_name(kind)}(({coordinate}).xy, {scale}, {seed})'))

    def _node_OPSpatialFilter(self, node):
        child = Translator(self.graph, self.unit, self.inline)
        color = child.input(node.inputs['Color'], VECTOR)
        alpha = child.input(node.inputs['Alpha'], VALUE)
        source = ('void main() {\n  ivec2 op_texel = ivec2(gl_FragCoord.xy);\n'
                  '  vec2 op_uv = (vec2(op_texel) + 0.5) * op_inv_size;\n' + '\n'.join(child.lines)
                  + f'\n  op_out0 = vec4({color}, {alpha});\n}}\n')
        program = Program(source, child.samplers, child.params, child.objects, [('Color', 'Alpha')],
                          child.imports, tuple(sorted(child.procedurals)))
        name = self._sampler(('filter', id(node)), 'filter', node=node, source=program)
        self._set(node.outputs['Color'], self._variable(RGBA, f'texelFetch({name}, op_texel, 0)'))

    def _node_NodeGroupInput(self, node):
        for socket in node.outputs:
            name = socket.name
            if name == 'UV':
                self._set(socket, 'vec3(op_uv, 0.0)', VECTOR)
            elif name == 'Position':
                self._set(socket, self._geometry('position'), VECTOR)
            elif name == 'Normal':
                self._set(socket, self._geometry('normal'), VECTOR)
            elif name in {'In', 'In:Alpha', 'Grouped', 'Grouped:Alpha'}:
                kind = 'backdrop' if name.startswith('In') else 'grouped'
                # A constant backdrop is a 1x1 texture: the fetch clamps into it.
                sampler = self._sampler(kind, kind)
                fetch = f'texelFetch({sampler}, min(op_texel, textureSize({sampler}, 0) - 1), 0)'
                from .formats import scalar_unit
                if scalar_unit(self.unit.key):
                    fetch = f'vec4(({fetch}).rrr, ({fetch}).g)'
                if name.endswith(':Alpha'):
                    self._set(socket, f'{fetch}.a', VALUE)
                elif socket.type == RGBA:
                    self._set(socket, f'vec4({fetch}.rgb, 1.0)', RGBA)
                else:
                    self._set(socket, f'{fetch}.rgb', VECTOR)
            else:
                self._set_import(socket, self._tree_unit(node.id_data))

    def _set_import(self, socket, unit):
        entry = next(((key, export) for key, export, input_name in unit.imports if input_name == socket.name), None)
        if entry is None:
            raise Unsupported(f'Unit {unit.key} reads an unknown input {socket.name}')
        producer_key, export = entry
        producer = self.graph.units[producer_key]
        if producer_key in self.inline:
            # The producer's nodes evaluated here, at this texel: no pass, no texture.
            source = producer.group_output.inputs[export]
            if source.links:
                expression, have = self.output(source.links[0].from_socket)
            else:
                expression, have = constant(source.default_value, source.type), source.type
            if socket.type == RGBA:
                # As through a texture: an imported colour carries no alpha of its own.
                self._set(socket, f'vec4({convert(expression, have, VECTOR)}, 1.0)', RGBA)
            else:
                self._set(socket, convert(expression, have, socket.type), socket.type)
            return
        target, component = target_of(producer, export)
        sampler = self._sampler(('import', producer_key, target), 'import', producer=(producer_key, target))
        self.imports[socket.name] = (producer_key, export)
        fetch = f'texelFetch({sampler}, op_texel, 0)'
        if component in {'a', 'g'}:
            self._set(socket, f'{fetch}.{component}', VALUE)
        elif component == 'r':
            self._set(socket, f'{fetch}.r', VALUE)
        elif socket.type == RGBA:
            self._set(socket, f'vec4({fetch}.{component}, 1.0)', RGBA)
        else:
            self._set(socket, f'{fetch}.{component}', VECTOR)

    # ---- program -------------------------------------------------------------------------

    def translate(self):
        from .formats import scalar_unit
        unit = self.unit
        outputs = []
        for index, (color, alpha) in enumerate(export_targets(unit)):
            rgb = self._export(color, VECTOR) if color and color != 'Value' else None
            value = self._export(color, VALUE) if color == 'Value' else None
            a = self._export(alpha, VALUE) if alpha else '1.0'
            if value is not None:
                outputs.append(f'  op_out{index} = vec4({value}, {value}, {value}, 1.0);')
            elif scalar_unit(unit.key):
                outputs.append(f'  op_out{index} = vec4(({rgb or "vec3(0.0)"}).r, {a}, 0.0, 0.0);')
            else:
                outputs.append(f'  op_out{index} = vec4({rgb or "vec3(0.0)"}, {a});')
        body = '\n'.join(self.lines + outputs)
        source = ('void main() {\n  ivec2 op_texel = ivec2(gl_FragCoord.xy);\n'
                  '  vec2 op_uv = (vec2(op_texel) + 0.5) * op_inv_size;\n' + body + '\n}\n')
        return Program(source, self.samplers, self.params, self.objects, export_targets(unit), self.imports,
                       tuple(sorted(self.procedurals)))

    def _export(self, name, want):
        socket = self.unit.group_output.inputs[name]
        return self.input(socket, want)


def image_color(image, raw, alpha_linked):
    """The Image Texture node's Color output from the stored texel (node_shader_tex_image.cc)."""
    if image is None:
        return raw
    alpha_mode = image.alpha_mode
    is_data = bool(getattr(image.colorspace_settings, 'is_data', False))
    if alpha_mode in {'NONE', 'CHANNEL_PACKED'} or is_data:
        return f'vec4({raw}.rgb, 1.0)'
    if alpha_mode == 'PREMUL':
        return f'op_unpremultiply({raw})' if alpha_linked else f'vec4({raw}.rgb, 1.0)'
    return f'vec4({raw}.rgb, 1.0)' if alpha_linked else f'vec4({raw}.rgb * {raw}.a, {raw}.a)'


def live_image(image):
    """The image, or None once Blender removed it (a leaf an undo took)."""
    if image is None:
        return None
    try:
        image.name
    except ReferenceError:
        return None
    return image


def translate(graph, unit, inline=frozenset()):
    from ...native_bridge.lifecycle import active_library
    lib = active_library()
    if lib is not None and hasattr(lib, 'op_ir_compile'):
        from ...native_bridge.compiler import Snapshot
        return Snapshot(lib, graph, unit, inline).translate()
    return Translator(graph, unit, inline).translate()


def programs(program):
    """A program and the input passes behind its spatial-filter samplers."""
    yield program
    for sampler in program.samplers:
        if sampler.source is not None:
            yield from programs(sampler.source)
