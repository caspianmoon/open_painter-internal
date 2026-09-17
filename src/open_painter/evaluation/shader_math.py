"""Persistent shader arithmetic matching the CPU compositor.

``Math`` accepts Blender sockets and plain scalar/RGB constants. Its node factory
is overridable so a compiler can attach dependency identifiers. This module never
reads or writes an Image and never evaluates pixels. ``Signal`` carries straight
source color or premultiplied accumulated color, as documented by ``blend``.
"""

from dataclasses import dataclass
from numbers import Real

from ..constants import OWNER


@dataclass(frozen=True)
class Signal:
    color: object
    alpha: object = 1.0


def is_socket(value):
    return hasattr(value, "is_output") and hasattr(value, "node")


def is_vector(value):
    return (getattr(value, "type", None) in {"RGBA", "VECTOR"} if is_socket(value)
            else not isinstance(value, Real))


class Math:
    """Small socket builder; subclasses supply ``param(owner, field, index)``.

    ``math`` returns a scalar. ``vec`` returns a vector except for DOT_PRODUCT,
    DISTANCE and LENGTH. SCALE accepts its scalar as the second argument without
    exposing Blender's nonsequential input index. ``mix(a,b,t)`` is unclamped
    linear RGB interpolation; ``clamp`` preserves scalar/vector dimensionality.
    """

    def __init__(self, tree):
        self.tree = tree

    def node(self, kind):
        node = self.tree.nodes.new(kind)
        # Free Blender's default type name immediately. Repeatedly allocating
        # thousands of "Math.001" siblings spends seconds searching suffixes.
        # Dependency labels/ownership remain independent of these unique names.
        sequence = getattr(self, "_node_sequence", len(self.tree.nodes)) + 1
        self._node_sequence = sequence
        node.name = f"OP_{sequence:06d}"
        node[OWNER] = True
        return node

    def link(self, value, target):
        if is_socket(value):
            self.tree.links.new(value, target)
        elif target.type in {"VECTOR", "RGBA"}:
            values = (float(value),) * 3 if isinstance(value, Real) else tuple(value)[:3]
            target.default_value = (*values, 1.0) if target.type == "RGBA" else values
        else:
            target.default_value = float(value)
        return target

    def rgb(self, value):
        node = self.node("ShaderNodeRGB")
        values = tuple(value)
        node.outputs[0].default_value = (*values[:3], values[3] if len(values) > 3 else 1.0)
        return node.outputs[0]

    def math(self, operation, *values):
        node = self.node("ShaderNodeMath")
        node.operation = operation
        for target, value in zip(node.inputs, values):
            self.link(value, target)
        return node.outputs[0]

    def vec(self, operation, *values):
        node = self.node("ShaderNodeVectorMath")
        node.operation = operation
        for index, value in enumerate(values):
            self.link(value, node.inputs[3 if operation == "SCALE" and index == 1 else index])
        return node.outputs["Value" if operation in {"DOT_PRODUCT", "DISTANCE", "LENGTH"} else "Vector"]

    def combine(self, x, y, z):
        node = self.node("ShaderNodeCombineXYZ")
        for target, value in zip(node.inputs, (x, y, z)):
            self.link(value, target)
        return node.outputs[0]

    def separate(self, value):
        if not is_socket(value):
            return (value,) * 3 if isinstance(value, Real) else tuple(value)[:3]
        node = self.node("ShaderNodeSeparateXYZ")
        self.link(value, node.inputs[0])
        return tuple(node.outputs)

    def mix(self, a, b, factor):
        return self.vec("ADD", a, self.vec("SCALE", self.vec("SUBTRACT", b, a), factor))

    def clamp(self, value, lo=0.0, hi=1.0):
        if is_vector(value):
            low = self.combine(lo, lo, lo) if is_socket(lo) and not is_vector(lo) else lo
            high = self.combine(hi, hi, hi) if is_socket(hi) and not is_vector(hi) else hi
            return self.vec("MINIMUM", self.vec("MAXIMUM", value, low), high)
        return self.math("MINIMUM", self.math("MAXIMUM", value, lo), hi)


def _per_component(b, operation, *colors):
    channels = tuple(b.separate(color) for color in colors)
    return b.combine(*(operation(*(color[index] for color in channels)) for index in range(3)))


def _divide(b, a, denominator, fallback=1.0, epsilon=1e-8):
    valid = b.math("GREATER_THAN", b.math("ABSOLUTE", denominator), epsilon)
    quotient = b.math("DIVIDE", a, denominator)
    return b.math("ADD", fallback, b.math("MULTIPLY", valid, b.math("SUBTRACT", quotient, fallback)))


def _choose(b, condition, yes, no):
    return b.math("ADD", no, b.math("MULTIPLY", condition, b.math("SUBTRACT", yes, no)))


def luminance(b, color, weights=(.2126, .7152, .0722)):
    return b.vec("DOT_PRODUCT", color, weights)


def srgb_encode(b, color):
    """Piecewise sRGB transfer per component: the stored bytes of a sample the
    Image Texture node linearised (image_snapshots.stored_encoding == 'SRGB')."""
    def encode(v):
        v = b.math("MAXIMUM", v, 0.0)
        low = b.math("MULTIPLY", v, 12.92)
        high = b.math("SUBTRACT", b.math("MULTIPLY", b.math("POWER", v, 1 / 2.4), 1.055), .055)
        return _choose(b, b.math("LESS_THAN", v, .0031308), low, high)
    return _per_component(b, encode, color)


def _extrema(b, color):
    r, g, blue = b.separate(color)
    return (b.math("MINIMUM", b.math("MINIMUM", r, g), blue),
            b.math("MAXIMUM", b.math("MAXIMUM", r, g), blue))


def _set_lum(b, color, luminosity):
    old = luminance(b, color, (.3, .59, .11))
    shift = b.math("SUBTRACT", luminosity, old)
    color = b.vec("ADD", color, b.combine(shift, shift, shift))
    low, high = _extrema(b, color)
    # The reference computes both extrema before either correction.
    gray = b.combine(luminosity, luminosity, luminosity)
    lower = b.vec("ADD", gray, b.vec("SCALE", b.vec("SUBTRACT", color, gray),
                 _divide(b, luminosity, b.math("SUBTRACT", luminosity, low))))
    color = b.mix(color, lower, b.math("LESS_THAN", low, 0.0))
    upper = b.vec("ADD", gray, b.vec("SCALE", b.vec("SUBTRACT", color, gray),
                 _divide(b, b.math("SUBTRACT", 1.0, luminosity), b.math("SUBTRACT", high, luminosity))))
    return b.mix(color, upper, b.math("GREATER_THAN", high, 1.0))


def _set_sat(b, color, saturation):
    low, high = _extrema(b, color)
    delta = b.math("SUBTRACT", high, low)
    factor = b.math("MULTIPLY", b.math("GREATER_THAN", delta, 1e-8), _divide(b, saturation, delta))
    return b.vec("SCALE", b.vec("SUBTRACT", color, b.combine(low, low, low)), factor)


def normal_unit(b, vector):
    """Normalize a tangent normal, with the CPU's neutral +Z fallback."""
    length = b.vec("LENGTH", vector)
    normalized = b.vec("SCALE", vector, b.math("DIVIDE", 1.0, b.math("MAXIMUM", length, 1e-8)))
    return b.mix((0.0, 0.0, 1.0), normalized, b.math("GREATER_THAN", length, 1e-8))


def normal_combine(b, base, detail, mode="RNM"):
    n1 = b.vec("SUBTRACT", b.vec("SCALE", base, 2.0), (1.0, 1.0, 1.0))
    n2 = b.vec("SUBTRACT", b.vec("SCALE", detail, 2.0), (1.0, 1.0, 1.0))
    if mode in {"NORMAL_REPLACE", "REPLACE"}:
        vector = n2
    elif mode in {"NORMAL_COMBINE", "WHITEOUT"}:
        x, y, z = b.separate(n1)
        u, v, w = b.separate(n2)
        vector = b.combine(b.math("ADD", x, u), b.math("ADD", y, v), b.math("MULTIPLY", z, w))
    elif mode in {"NORMAL_ADD", "ADD"}:
        vector = b.vec("SUBTRACT", b.vec("ADD", n1, n2), (0.0, 0.0, 1.0))
    elif mode == "RNM":
        t = b.vec("ADD", n1, (0.0, 0.0, 1.0))
        u = b.vec("MULTIPLY", n2, (-1.0, -1.0, 1.0))
        z = b.separate(t)[2]
        scale = b.math("DIVIDE", b.vec("DOT_PRODUCT", t, u), b.math("MAXIMUM", z, 1e-6))
        combined = b.vec("SUBTRACT", b.vec("SCALE", t, scale), u)
        vector = b.mix(n2, combined, b.math("GREATER_THAN", z, 1e-6))
    else:
        raise ValueError(f"Unknown normal blend: {mode}")
    return b.vec("SCALE", b.vec("ADD", normal_unit(b, vector), (1.0, 1.0, 1.0)), .5)


def blend_value(b, base, source, mode):
    """Exact pointwise blend_modes.blend(base, source, 1, mode) formula."""
    if mode == "NORMAL":
        return source
    if mode == "DISABLE":
        return base
    if mode in {"NORMAL_COMBINE", "RNM", "NORMAL_ADD", "NORMAL_REPLACE"}:
        return normal_combine(b, base, source, mode)
    if mode in {"HUE", "SATURATION", "COLOR", "LUMINOSITY"}:
        low, high = _extrema(b, base if mode == "HUE" else source)
        saturation = b.math("SUBTRACT", high, low)
        if mode == "HUE":
            return _set_lum(b, _set_sat(b, source, saturation), luminance(b, base, (.3, .59, .11)))
        if mode == "SATURATION":
            return _set_lum(b, _set_sat(b, base, saturation), luminance(b, base, (.3, .59, .11)))
        if mode == "COLOR":
            return _set_lum(b, source, luminance(b, base, (.3, .59, .11)))
        return _set_lum(b, base, luminance(b, source, (.3, .59, .11)))
    simple = {"MULTIPLY": "MULTIPLY", "ADD": "ADD", "SUBTRACT": "SUBTRACT",
              "DARKEN": "MINIMUM", "LIGHTEN": "MAXIMUM"}
    if mode in simple:
        return b.vec(simple[mode], base, source)

    m = b.math

    def burn(a, s):
        raw = m("SUBTRACT", 1.0, m("MINIMUM", 1.0, _divide(b, m("SUBTRACT", 1.0, a), s)))
        raw = _choose(b, m("GREATER_THAN", s, 0.0), raw, 0.0)
        return _choose(b, m("LESS_THAN", a, 1.0), raw, 1.0)

    def dodge(a, s):
        raw = m("MINIMUM", 1.0, _divide(b, a, m("SUBTRACT", 1.0, s)))
        raw = _choose(b, m("LESS_THAN", s, 1.0), raw, 1.0)
        return _choose(b, m("GREATER_THAN", a, 0.0), raw, 0.0)

    def component(a, s):
        if mode == "DIVIDE":
            return b.clamp(_divide(b, a, s))
        if mode == "DIFFERENCE":
            return m("ABSOLUTE", m("SUBTRACT", a, s))
        if mode == "EXCLUSION":
            return m("SUBTRACT", m("ADD", a, s), m("MULTIPLY", 2.0, m("MULTIPLY", a, s)))
        if mode == "SCREEN":
            return m("SUBTRACT", 1.0, m("MULTIPLY", m("SUBTRACT", 1.0, a), m("SUBTRACT", 1.0, s)))
        if mode == "LINEAR_BURN":
            return m("MAXIMUM", 0.0, m("SUBTRACT", m("ADD", a, s), 1.0))
        if mode == "COLOR_BURN":
            return burn(a, s)
        if mode == "COLOR_DODGE":
            return dodge(a, s)
        if mode in {"OVERLAY", "HARD_LIGHT"}:
            lower = m("MULTIPLY", 2.0, m("MULTIPLY", a, s))
            upper = m("SUBTRACT", 1.0, m("MULTIPLY", 2.0, m("MULTIPLY", m("SUBTRACT", 1.0, a), m("SUBTRACT", 1.0, s))))
            return _choose(b, m("GREATER_THAN", a if mode == "OVERLAY" else s, .5), upper, lower)
        if mode == "SOFT_LIGHT":
            cubic = m("MULTIPLY", m("ADD", m("MULTIPLY", m("SUBTRACT", m("MULTIPLY", 16.0, a), 12.0), a), 4.0), a)
            curve = _choose(b, m("GREATER_THAN", a, .25), m("SQRT", m("MAXIMUM", a, 0.0)), cubic)
            lower = m("SUBTRACT", a, m("MULTIPLY", m("SUBTRACT", 1.0, m("MULTIPLY", 2.0, s)), m("MULTIPLY", a, m("SUBTRACT", 1.0, a))))
            upper = m("ADD", a, m("MULTIPLY", m("SUBTRACT", m("MULTIPLY", 2.0, s), 1.0), m("SUBTRACT", curve, a)))
            return _choose(b, m("GREATER_THAN", s, .5), upper, lower)
        if mode == "VIVID_LIGHT":
            return _choose(b, m("GREATER_THAN", s, .5), dodge(a, m("SUBTRACT", m("MULTIPLY", 2.0, s), 1.0)), burn(a, m("MULTIPLY", 2.0, s)))
        if mode == "LINEAR_LIGHT":
            return b.clamp(m("SUBTRACT", m("ADD", a, m("MULTIPLY", 2.0, s)), 1.0))
        if mode == "PIN_LIGHT":
            lower = m("MINIMUM", a, m("MULTIPLY", 2.0, s))
            upper = m("MAXIMUM", a, m("SUBTRACT", m("MULTIPLY", 2.0, s), 1.0))
            return _choose(b, m("GREATER_THAN", s, .5), upper, lower)
        raise ValueError(f"Unsupported blend mode: {mode}")

    return _per_component(b, component, base, source)


def blend(b, backdrop, source, coverage, mode="NORMAL", channel=None):
    """Compose straight ``source`` over premultiplied ``backdrop``.

    ``coverage`` is layer opacity × channel opacity × mask. The returned Signal
    is premultiplied and can be the next backdrop. Top-level channels start with
    alpha=1; isolated folders start with alpha=0 and unpremultiply their result
    once before using it as a source. Passthrough folders interpolate their whole
    result separately, following evaluator.compose. ``channel`` is API context;
    final normal-output normalization belongs after the complete channel stack.
    """
    if mode == "DISABLE":
        return backdrop
    alpha = b.clamp(b.math("MULTIPLY", source.alpha, coverage))
    if isinstance(backdrop.alpha, Real) and backdrop.alpha == 1.0:
        return Signal(b.mix(backdrop.color, blend_value(b, backdrop.color, source.color, mode), alpha), 1.0)
    inverse = _divide(b, 1.0, backdrop.alpha, fallback=0.0)
    straight = b.vec("SCALE", backdrop.color, inverse)
    mixed = blend_value(b, straight, source.color, mode)
    contributing = b.mix(source.color, mixed, backdrop.alpha)
    color = b.vec("ADD", b.vec("SCALE", backdrop.color, b.math("SUBTRACT", 1.0, alpha)),
                  b.vec("SCALE", contributing, alpha))
    combined_alpha = b.math("ADD", alpha, b.math("MULTIPLY", backdrop.alpha, b.math("SUBTRACT", 1.0, alpha)))
    return Signal(color, combined_alpha)
