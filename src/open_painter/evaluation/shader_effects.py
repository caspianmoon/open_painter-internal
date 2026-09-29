"""Shader-native pointwise effects, matching texture_engine.filters.

The builder supplies ``param(effect, field, index=None)`` to bind RNA values to
stable scalar sockets. An optional ``effect_image(effect)`` returns the sampled
RGB detail normal for NORMAL_COMBINE. Spatial filters explicitly raise
UnsupportedEffect so the compiler can put a raster boundary around only that
operation and its inputs. Changing an effect's numeric controls never adds nodes.
"""

from .shader_math import Signal, _choose, _extrema, _per_component, luminance, normal_combine, normal_unit


class UnsupportedEffect(ValueError):
    """This operation needs a spatial resource, not pointwise shader arithmetic."""


POINTWISE = frozenset({
    "LEVELS", "ROUGHNESS_REMAP", "METALLIC_REMAP", "HEIGHT_REMAP", "GAMMA",
    "INVERT", "CLAMP", "BRIGHTNESS_CONTRAST", "ROUGHNESS_CONTRAST", "THRESHOLD",
    "HISTOGRAM_SCAN", "HISTOGRAM_RANGE", "POSTERIZE", "QUANTIZE", "AO_STRENGTH",
    "NORMAL_STRENGTH", "NORMAL_INVERT_Y", "NORMAL_COMBINE", "HSL", "HSV",
    "HUE_SHIFT", "SATURATION", "COLOR_BALANCE", "GRADIENT_MAP", "COLORIZE",
    "TINT", "TEMPERATURE", "DESATURATE", "REPLACE_COLOR", "CHANNEL_MIXER",
})


# Portable semantic input order. Vector entries only pack RNA scalars; formulas
# and effect dispatch belong to the native compiler.
EFFECT_FIELDS = {
    **{k: ('black', 'white', 'gamma', 'output_black', 'output_white')
       for k in ('LEVELS', 'ROUGHNESS_REMAP', 'METALLIC_REMAP', 'HEIGHT_REMAP')},
    'GAMMA': ('gamma',), 'INVERT': (), 'CLAMP': ('black', 'white'),
    'BRIGHTNESS_CONTRAST': ('amount', 'contrast'), 'ROUGHNESS_CONTRAST': ('contrast',),
    'THRESHOLD': ('amount',), 'HISTOGRAM_SCAN': ('amount', 'softness', 'contrast'),
    'HISTOGRAM_RANGE': ('black', 'white', 'softness', 'contrast'),
    'POSTERIZE': ('steps',), 'QUANTIZE': ('steps',), 'AO_STRENGTH': ('amount',),
    'NORMAL_STRENGTH': ('amount',), 'NORMAL_INVERT_Y': (),
    'NORMAL_COMBINE': ('detail', 'amount'),
    **{k: ('hue', 'saturation', 'lightness') for k in ('HSL', 'HSV', 'HUE_SHIFT', 'SATURATION')},
    'COLOR_BALANCE': ('color',), 'GRADIENT_MAP': ('color', 'color2'),
    'COLORIZE': ('color', 'amount'), 'TINT': ('color', 'amount'),
    'TEMPERATURE': ('amount',), 'DESATURATE': (),
    'REPLACE_COLOR': ('color', 'color2', 'softness'),
    'CHANNEL_MIXER': ('row0', 'row1', 'row2', 'bias'),
}
EFFECT_VECTORS = frozenset({'detail', 'color', 'color2', 'row0', 'row1', 'row2', 'bias'})


def effect_key(kind):
    key = str(kind).strip().upper().replace("-", "_").replace(" / ", "_").replace(" ", "_")
    return {"GRAYSCALE": "DESATURATE", "HSL_ADJUSTMENT": "HSL"}.get(key, key)


def is_pointwise(kind):
    """True when the effect compiles to shader arithmetic; its enabled flag is then a uniform."""
    return effect_key(kind) in POINTWISE


def smooth(b, low, high, value):
    """Reference smoothstep with the same protected interval and saturation."""
    t = b.clamp(b.math("DIVIDE", b.math("SUBTRACT", value, low),
                       b.math("MAXIMUM", b.math("SUBTRACT", high, low), 1e-6)))
    return b.math("MULTIPLY", b.math("MULTIPLY", t, t),
                  b.math("SUBTRACT", 3.0, b.math("MULTIPLY", 2.0, t)))


def _mod(b, value, divisor):
    # Explicit floor modulo also handles negative hue shifts identically to NumPy.
    return b.math("SUBTRACT", value, b.math("MULTIPLY", divisor,
                  b.math("FLOOR", b.math("DIVIDE", value, divisor))))


def _round_even(b, value):
    """np.round tie-to-even; Blender's Math ROUND uses a different tie rule."""
    low = b.math("FLOOR", value)
    fraction = b.math("SUBTRACT", value, low)
    tie = b.math("COMPARE", fraction, .5, 0.0)
    up = b.math("ADD", b.math("GREATER_THAN", fraction, .5),
                b.math("MULTIPLY", tie, _mod(b, low, 2.0)))
    return b.math("ADD", low, up)


def _rgb_to_hsv(b, rgb):
    m = b.math
    red, green, blue = b.separate(rgb)
    low, high = _extrema(b, rgb)
    delta = m("SUBTRACT", high, low)
    safe = m("MAXIMUM", delta, 1e-8)
    r_hue = m("DIVIDE", m("SUBTRACT", green, blue), safe)
    g_hue = m("ADD", m("DIVIDE", m("SUBTRACT", blue, red), safe), 2.0)
    b_hue = m("ADD", m("DIVIDE", m("SUBTRACT", red, green), safe), 4.0)
    hue = _choose(b, m("COMPARE", high, green, 0.0), g_hue, b_hue)
    hue = _choose(b, m("COMPARE", high, red, 0.0), r_hue, hue)
    hue = m("MULTIPLY", m("GREATER_THAN", delta, 1e-8), _mod(b, m("DIVIDE", hue, 6.0), 1.0))
    return hue, m("DIVIDE", delta, m("MAXIMUM", high, 1e-8)), high, low


def _hsv_to_rgb(b, hue, saturation, value):
    m = b.math
    outputs = []
    for offset in (5.0, 3.0, 1.0):
        k = _mod(b, m("ADD", m("MULTIPLY", hue, 6.0), offset), 6.0)
        shape = b.clamp(m("MINIMUM", k, m("SUBTRACT", 4.0, k)))
        outputs.append(m("MULTIPLY", value, m("SUBTRACT", 1.0, m("MULTIPLY", saturation, shape))))
    return b.combine(*outputs)


def _normal_strength(b, rgb, strength):
    vector = b.vec("SUBTRACT", b.vec("SCALE", rgb, 2.0), (1.0, 1.0, 1.0))
    x, y, z = b.separate(vector)
    vector = b.combine(b.math("MULTIPLY", x, strength), b.math("MULTIPLY", y, strength), z)
    return b.vec("SCALE", b.vec("ADD", normal_unit(b, vector), (1.0, 1.0, 1.0)), .5)


def apply_effect(b, signal, effect):
    """Compile one effect over straight RGB, preserving the input alpha.

    Strength/enabled are shader bindings, including for disabled effects. Filter
    output is clamped before effect opacity, exactly like apply_effect's CPU
    path. This function makes no persistent property or source image mutations.
    """
    key = effect_key(effect.kind)
    if key not in POINTWISE:
        if hasattr(b, 'spatial_effect'):
            return b.spatial_effect(signal, effect)
        raise UnsupportedEffect(f"{effect.kind} requires a spatial raster boundary")
    if hasattr(b, 'pointwise_effect'):
        if (key in {'LEVELS', 'ROUGHNESS_REMAP', 'METALLIC_REMAP', 'HEIGHT_REMAP'}
                and effect.enabled and effect.strength > 0 and effect.white <= effect.black):
            raise ValueError("White level must be greater than black level")
        if key == 'NORMAL_COMBINE' and not effect.image and effect.enabled and effect.strength > 0:
            raise ValueError("Normal Combine needs a detail normal texture.")
        return b.pointwise_effect(signal, effect)
    m = b.math
    values = {}

    def p(field, index=None):
        identity = field, index
        if identity not in values:
            values[identity] = b.param(effect, field, index=index)
        return values[identity]

    def color(field):
        return b.combine(*(p(field, index) for index in range(3)))

    source = signal.color
    if key in {"LEVELS", "ROUGHNESS_REMAP", "METALLIC_REMAP", "HEIGHT_REMAP"}:
        # Invalid intervals retain the bake evaluator's validation error. The
        # compiler/runtime also validates after edits before publishing a graph.
        if effect.enabled and effect.strength > 0 and effect.white <= effect.black:
            raise ValueError("White level must be greater than black level")
        low, high = p("black"), p("white")
        exponent = m("DIVIDE", 1.0, m("MAXIMUM", p("gamma"), 1e-5))
        width = m("SUBTRACT", high, low)
        output_width = m("SUBTRACT", p("output_white"), p("output_black"))

        def levels(value):
            normalized = b.clamp(m("DIVIDE", m("SUBTRACT", value, low), width))
            return m("ADD", p("output_black"), m("MULTIPLY", m("POWER", normalized, exponent), output_width))

        filtered = _per_component(b, levels, source)
    elif key == "GAMMA":
        exponent = m("DIVIDE", 1.0, m("MAXIMUM", p("gamma"), 1e-5))
        filtered = _per_component(b, lambda v: m("POWER", m("MAXIMUM", v, 0.0), exponent), source)
    elif key == "INVERT":
        filtered = b.vec("SUBTRACT", (1.0, 1.0, 1.0), source)
    elif key == "CLAMP":
        filtered = b.clamp(source, p("black"), p("white"))
    elif key in {"BRIGHTNESS_CONTRAST", "ROUGHNESS_CONTRAST"}:
        brightness = p("amount") if key == "BRIGHTNESS_CONTRAST" else 0.0
        offset = m("ADD", .5, brightness)
        filtered = b.vec("ADD", b.vec("SCALE", b.vec("SUBTRACT", source, (.5, .5, .5)), p("contrast")),
                         b.combine(offset, offset, offset))
    elif key in {"THRESHOLD", "HISTOGRAM_SCAN", "HISTOGRAM_RANGE"}:
        position = p("amount")
        softness = m("MAXIMUM", m("DIVIDE", p("softness"), m("MAXIMUM", p("contrast"), .001)), 1e-6)

        def histogram(value):
            if key == "THRESHOLD":
                return m("SUBTRACT", 1.0, m("LESS_THAN", value, position))
            if key == "HISTOGRAM_SCAN":
                return smooth(b, m("SUBTRACT", position, softness), m("ADD", position, softness), value)
            left = smooth(b, m("SUBTRACT", p("black"), softness), p("black"), value)
            right = smooth(b, p("white"), m("ADD", p("white"), softness), value)
            return m("MULTIPLY", left, m("SUBTRACT", 1.0, right))

        filtered = _per_component(b, histogram, source)
    elif key in {"POSTERIZE", "QUANTIZE"}:
        steps = m("SUBTRACT", m("MAXIMUM", p("steps"), 2.0), 1.0)
        filtered = _per_component(b, lambda v: m("DIVIDE", _round_even(b, m("MULTIPLY", v, steps)), steps), source)
    elif key == "AO_STRENGTH":
        filtered = b.vec("SUBTRACT", (1.0, 1.0, 1.0), b.vec("SCALE", b.vec("SUBTRACT", (1.0, 1.0, 1.0), source), p("amount")))
    elif key == "NORMAL_INVERT_Y":
        x, y, z = b.separate(source)
        filtered = b.combine(x, m("SUBTRACT", 1.0, y), z)
    elif key == "NORMAL_STRENGTH":
        filtered = _normal_strength(b, source, p("amount"))
    elif key == "NORMAL_COMBINE":
        image = getattr(effect, "image", None)
        if not image and effect.enabled and effect.strength > 0:
            raise ValueError("Normal Combine needs a detail normal texture.")
        if image and not hasattr(b, "effect_image"):
            raise UnsupportedEffect("Normal Combine requires a sampled detail normal")
        detail = b.clamp(_normal_strength(b, b.effect_image(effect) if image else (.5, .5, 1.), p("amount")))
        filtered = normal_combine(b, source, detail, "RNM")
    elif key in {"HSL", "HSV", "HUE_SHIFT", "SATURATION"}:
        h, s, v, low = _rgb_to_hsv(b, source)
        h = _mod(b, m("ADD", h, p("hue")), 1.0)
        if key == "HSL":
            light = m("MULTIPLY", m("ADD", v, low), .5)
            chroma = m("SUBTRACT", v, low)
            denominator = m("MAXIMUM", m("SUBTRACT", 1.0, m("ABSOLUTE", m("SUBTRACT", m("MULTIPLY", 2.0, light), 1.0))), 1e-8)
            saturation = b.clamp(m("MULTIPLY", m("DIVIDE", chroma, denominator), p("saturation")))
            light = b.clamp(m("ADD", light, p("lightness")))
            v = m("ADD", light, m("MULTIPLY", saturation, m("MINIMUM", light, m("SUBTRACT", 1.0, light))))
            s = m("MULTIPLY", 2.0, m("SUBTRACT", 1.0, m("DIVIDE", light, m("MAXIMUM", v, 1e-8))))
        else:
            s = b.clamp(m("MULTIPLY", s, p("saturation")))
            v = b.clamp(m("ADD", v, p("lightness")))
        filtered = _hsv_to_rgb(b, h, s, v)
    elif key == "DESATURATE":
        gray = luminance(b, source)
        filtered = b.combine(gray, gray, gray)
    elif key == "COLOR_BALANCE":
        filtered = b.vec("ADD", source, color("color"))
    elif key == "TEMPERATURE":
        red = m("ADD", 1.0, m("MULTIPLY", .2, p("amount")))
        blue = m("SUBTRACT", 1.0, m("MULTIPLY", .2, p("amount")))
        filtered = b.vec("MULTIPLY", source, b.combine(red, 1.0, blue))
    elif key in {"COLORIZE", "TINT"}:
        target = color("color")
        if key == "COLORIZE":
            target = b.vec("SCALE", target, luminance(b, source))
        filtered = b.mix(source, target, b.clamp(p("amount")))
    elif key == "REPLACE_COLOR":
        distance = b.vec("DISTANCE", source, color("color"))
        threshold = p("softness")
        feather = smooth(b, threshold, m("ADD", m("MULTIPLY", threshold, 2.0), 1e-6), distance)
        filtered = b.mix(color("color2"), source, feather)
    elif key == "CHANNEL_MIXER":
        outputs = []
        for row in range(3):
            matrix_row = b.combine(*(p("matrix", row * 3 + column) for column in range(3)))
            outputs.append(m("ADD", b.vec("DOT_PRODUCT", source, matrix_row), p("bias", row)))
        filtered = b.combine(*outputs)
    elif key == "GRADIENT_MAP":
        filtered = b.mix(color("color"), color("color2"), b.clamp(luminance(b, source)))
    else:
        raise UnsupportedEffect(f"No shader implementation for {effect.kind}")
    filtered = b.clamp(filtered)
    amount = m("MULTIPLY", p("strength"), p("enabled"))
    return Signal(b.mix(source, filtered, amount), signal.alpha)
