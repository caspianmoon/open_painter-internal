"""Image-space effects on NumPy textures, with clamped (never cyclic) borders.

Radii, offsets and displacement strengths are in pixels. For tiled processing,
the caller must supply enough halo; every result has the input spatial dimensions.
Color-only operations preserve alpha. Gradient map and height-to-normal expand
grayscale into RGB. Other operations work on grayscale or multi-channel arrays.
"""

from ..evaluation.metrics import measured

import numpy as np


CATALOG = {
    "LEVELS": "Input/output range and gamma remapping",
    "HISTOGRAM_SCAN": "Smooth threshold around a position",
    "HISTOGRAM_RANGE": "Smooth selection of an intensity range",
    "BRIGHTNESS_CONTRAST": "Midpoint contrast and brightness offset",
    "GAMMA": "Power-law intensity correction",
    "INVERT": "Invert texture values",
    "THRESHOLD": "Binary intensity threshold",
    "CLAMP": "Clamp values to an interval",
    "POSTERIZE": "Discrete intensity bands",
    "QUANTIZE": "Round to fixed intensity steps",
    "BLUR": "Separable box blur",
    "GAUSSIAN_BLUR": "Separable normalized Gaussian blur",
    "DIRECTIONAL_BLUR": "Bilinear blur along an angle",
    "RADIAL_BLUR": "Angular blur around image center",
    "SLOPE_BLUR": "Iterated blur along a driving height field",
    "HIGH_PASS": "Remove low-frequency intensity variation",
    "SHARPEN": "Unsharp-mask detail enhancement",
    "DILATE": "Expand bright features by a square pixel footprint",
    "ERODE": "Contract bright features by a square pixel footprint",
    "MORPHOLOGICAL_EXPAND": "Expand bright features by a square pixel footprint",
    "MORPHOLOGICAL_CONTRACT": "Contract bright features by a square pixel footprint",
    "EDGE_DETECT": "Sobel gradient magnitude",
    "SMOOTH_MASK": "Gaussian smoothing with soft threshold",
    "WARP": "Height-gradient displacement with bilinear sampling",
    "DIRECTIONAL_WARP": "Scalar-field displacement along an angle",
    "VECTOR_WARP": "Two-channel field displacement",
    "TWIRL": "Radially tapered angular distortion",
    "RIPPLE": "Radial sinusoidal displacement",
    "WAVE": "Directional sinusoidal displacement",
    "NON_UNIFORM_TRANSFORM": "Independent x/y scaling, rotation and translation",
    "UV_DISTORTION": "Two-channel field displacement",
    "HSL": "Hue, saturation and lightness adjustment",
    "HSV": "Hue, saturation and value adjustment",
    "HUE_SHIFT": "Cyclic hue rotation",
    "SATURATION": "Saturation adjustment",
    "COLOR_BALANCE": "Per-channel additive color adjustment",
    "GRADIENT_MAP": "Map luminance through ordered color stops",
    "COLORIZE": "Grayscale tint preserving luminosity",
    "TINT": "Mix a constant RGB tint",
    "TEMPERATURE": "Warm or cool RGB balance",
    "DESATURATE": "Linear-light luminance conversion",
    "REPLACE_COLOR": "Feathered color-distance replacement",
    "ROUGHNESS_REMAP": "Levels applied to roughness values",
    "METALLIC_REMAP": "Levels applied to metallic values",
    "HEIGHT_REMAP": "Levels applied to height values",
    "NORMAL_STRENGTH": "Scale tangent-normal slope and renormalize",
    "NORMAL_COMBINE": "Reoriented normal composition",
    "NORMAL_INVERT_Y": "Convert tangent-space normal Y convention",
    "HEIGHT_TO_NORMAL": "Height gradient to normalized tangent normal",
    "AO_STRENGTH": "Scale occlusion against unoccluded white",
    "ROUGHNESS_CONTRAST": "Roughness contrast around 0.5",
    "CHANNEL_MIXER": "RGB matrix with optional offset",
}


def _smooth(a, b, data):
    t = np.clip((data - a) / max(float(b - a), 1e-6), 0, 1)
    return t * t * (3 - 2 * t)


def _luma(data):
    if data.ndim == 2:
        return data
    if data.shape[-1] < 3:
        return data[..., 0]
    return np.sum(data[..., :3] * np.array([0.2126, 0.7152, 0.0722], np.float32), axis=-1)


# Direct convolution costs one full pass over the image per tap; the FFT
# path costs a fixed handful, so kernels longer than this (sigma above 6)
# are cheaper by FFT, and a 4k blur at the widest radius no longer takes
# tens of seconds. Both compute the same clamped kernel; only rounding differs.
FFT_TAPS = 40

# Rows per FFT band: the float64 transform of a band stays a few megabytes.
_BAND_ROWS = 64


def _fft_length(length):
    """Smallest transform length >= ``length`` with only 2, 3 and 5 as factors,
    the radices pocketfft handles fastest."""
    best = None
    two = 1
    while two < 2 * length:
        three = two
        while three < 2 * length:
            five = three
            while five < 2 * length:
                if five >= length and (best is None or five < best):
                    best = five
                five *= 5
            three *= 3
        two *= 2
    return best


def _convolve_axis_fft(data, kernel, axis):
    """The same clamped convolution as the direct taps, via float64 FFTs on
    bands of the other axis. Edge padding of ``half`` plus zeros up to the
    transform length keeps the circular wrap from reaching any output texel."""
    half = len(kernel) // 2
    length = data.shape[axis]
    padded_length = length + 2 * half
    size = _fft_length(padded_length)
    spectrum = np.fft.rfft(np.roll(np.pad(kernel.astype(np.float64), (0, size - len(kernel))), -half))
    shape = [1] * data.ndim
    shape[axis] = -1
    spectrum = spectrum.reshape(shape)
    pad = [(0, 0)] * data.ndim
    pad[axis] = (half, half + size - padded_length)
    take = [slice(None)] * data.ndim
    take[axis] = slice(half, half + length)
    other = 1 - axis
    out = np.empty(data.shape, np.float32)
    for low in range(0, data.shape[other], _BAND_ROWS):
        band = [slice(None)] * data.ndim
        band[other] = slice(low, low + _BAND_ROWS)
        transform = np.fft.rfft(np.pad(data[tuple(band)].astype(np.float64), pad, mode="edge"), axis=axis)
        transform *= spectrum
        out[tuple(band)] = np.fft.irfft(transform, n=size, axis=axis)[tuple(take)]
    return out


def _convolve_axis(data, kernel, axis):
    if len(kernel) > FFT_TAPS:
        return _convolve_axis_fft(data, kernel, axis)
    half = len(kernel) // 2
    pad = [(0, 0)] * data.ndim
    pad[axis] = (half, half)
    padded = np.pad(data, pad, mode="edge")
    result = np.zeros_like(data)
    scratch = np.empty_like(data)
    for offset, weight in enumerate(kernel):
        region = [slice(None)] * data.ndim
        region[axis] = slice(offset, offset + data.shape[axis])
        # result += slice * weight, without a fresh temporary per tap
        np.multiply(padded[tuple(region)], weight, out=scratch)
        result += scratch
    return result


def _box_axis(data, half, axis):
    """Mean over the clamped (2*half+1) window along ``axis``, exact for any
    radius: running sums in float64 make the cost independent of the width."""
    width = 2 * half + 1
    length = data.shape[axis]
    pad = [(0, 0)] * data.ndim
    pad[axis] = (half, half)
    padded = np.pad(data, pad, mode="edge")
    out = np.empty(data.shape, np.float32)
    if axis == 0:
        # Rows are contiguous, so sliding the window is one vector add and
        # subtract per row.
        window = padded[:width].sum(axis=0, dtype=np.float64)
        out[0] = window / width
        for row in range(1, length):
            window += padded[row + width - 1]
            window -= padded[row - 1]
            out[row] = window / width
        return out
    # Along the strided axis, cumulative sums per band of rows; the window
    # sum is the difference of the sums either side of it.
    for low in range(0, data.shape[0], _BAND_ROWS):
        sums = np.cumsum(padded[low:low + _BAND_ROWS], axis=1, dtype=np.float64)
        head = sums[:, width - 1:width - 1 + length]
        out[low:low + _BAND_ROWS, 0] = head[:, 0] / width
        out[low:low + _BAND_ROWS, 1:] = (head[:, 1:] - sums[:, :length - 1]) / width
    return out


def shift(data, dy, dx, fill=0.0):
    """``data`` moved by (dy, dx) texels without wrapping; vacated texels take ``fill``."""
    result = np.full_like(data, fill)
    h, w = data.shape[:2]
    ys, yd = (slice(0, h - dy), slice(dy, h)) if dy >= 0 else (slice(-dy, h), slice(0, h + dy))
    xs, xd = (slice(0, w - dx), slice(dx, w)) if dx >= 0 else (slice(-dx, w), slice(0, w + dx))
    result[yd, xd] = data[ys, xs]
    return result


def gaussian_blur(data, radius=2):
    """Radius is the Gaussian sigma in pixels; support is ceil(3*sigma)."""
    radius = max(float(radius), 0)
    if radius < 1e-5:
        return np.asarray(data, np.float32).copy()
    half = min(int(np.ceil(radius * 3)), 192)
    x = np.arange(-half, half + 1, dtype=np.float32)
    kernel = np.exp(-(x * x) / (2 * radius * radius))
    kernel /= kernel.sum()
    return _convolve_axis(_convolve_axis(np.asarray(data, np.float32), kernel, 1), kernel, 0)


def _morphology(data, radius, dilate):
    """Separable max/min over a (2r+1) window; exact for any radius.

    Running windows are built by doubling (a window of width w combined with
    itself shifted by w has width 2w) and the odd remainder is covered by two
    overlapping power-of-two windows, which is exact because max/min are
    idempotent. The pass count is logarithmic in the radius; the result is the
    same as the sequential sweep.
    """
    radius = min(max(int(np.ceil(radius)), 0), 192)
    out = data.copy()
    reduce = np.maximum if dilate else np.minimum
    width = 2 * radius + 1
    power = 1 << (width.bit_length() - 1)
    for axis in (0, 1):
        pad = [(0, 0)] * data.ndim
        pad[axis] = (radius, radius)
        padded = np.pad(out, pad, mode="edge")
        length = data.shape[axis]
        running, covered = padded, 1
        while covered < power:
            span = running.shape[axis] - covered
            head, tail = [slice(None)] * data.ndim, [slice(None)] * data.ndim
            head[axis], tail[axis] = slice(0, span), slice(covered, covered + span)
            running = reduce(running[tuple(head)], running[tuple(tail)])
            covered *= 2
        head, tail = [slice(None)] * data.ndim, [slice(None)] * data.ndim
        head[axis], tail[axis] = slice(0, length), slice(width - power, width - power + length)
        out = reduce(running[tuple(head)], running[tuple(tail)])
    return out


def sample(data, x, y):
    """Bilinear pixel-coordinate sampling, clamped at image boundaries."""
    x = np.clip(x, 0, data.shape[1] - 1)
    y = np.clip(y, 0, data.shape[0] - 1)
    x0, y0 = np.floor(x).astype(np.intp), np.floor(y).astype(np.intp)
    x1, y1 = np.minimum(x0 + 1, data.shape[1] - 1), np.minimum(y0 + 1, data.shape[0] - 1)
    fx, fy = x - x0, y - y0
    if data.ndim == 3:
        fx, fy = fx[..., None], fy[..., None]
    a = data[y0, x0] * (1 - fx) + data[y0, x1] * fx
    b = data[y1, x0] * (1 - fx) + data[y1, x1] * fx
    return (a * (1 - fy) + b * fy).astype(np.float32)


def _gradient(data):
    # np.gradient rejects one-pixel-wide images. Clamped centered differences
    # also give predictable boundary behavior for full images and supplied halos.
    padded = np.pad(data, ((1, 1), (1, 1)), mode="edge")
    return (padded[1:-1, 2:] - padded[1:-1, :-2]) * 0.5, (padded[2:, 1:-1] - padded[:-2, 1:-1]) * 0.5


def _normalize(vector):
    length = np.linalg.norm(vector, axis=-1, keepdims=True)
    fallback = np.zeros_like(vector)
    fallback[..., 2] = 1
    return np.where(length > 1e-8, vector / np.maximum(length, 1e-8), fallback)


def height_to_normal(height, strength=1):
    """Tangent normal with +Y following increasing row/texture V coordinates."""
    dx, dy = _gradient(_luma(np.asarray(height, np.float32)))
    return (_normalize(np.stack((-dx * strength, -dy * strength, np.ones_like(dx)), axis=-1)) + 1) * 0.5


def normal_combine(base, detail, mode="RNM"):
    """Combine encoded RGB normal maps; alpha, when supplied, follows base.

    RNM formula: Barré-Brisebois and Hill, 'Blending in Detail', 2012:
    https://blog.selfshadow.com/publications/blending-in-detail/ .
    """
    base, detail = np.asarray(base, np.float32), np.asarray(detail, np.float32)
    if base.shape[-1] < 3 or detail.shape[-1] < 3:
        raise ValueError("Normal composition needs RGB normal maps")
    n1, n2 = base[..., :3] * 2 - 1, detail[..., :3] * 2 - 1
    if mode.upper() in {"REPLACE", "REPLACE_NORMAL"}:
        normal = n2
    elif mode.upper() in {"WHITEOUT", "WHITEOUT_NORMAL"}:
        normal = np.stack((n1[..., 0] + n2[..., 0], n1[..., 1] + n2[..., 1],
                           n1[..., 2] * n2[..., 2]), axis=-1)
    elif mode.upper() in {"ADD", "ADDITIVE", "ADDITIVE_NORMAL"}:
        normal = n1 + n2 - np.array([0, 0, 1], np.float32)
    elif mode.upper() in {"RNM", "NORMAL_COMBINE", "REORIENTED_NORMAL_MAPPING"}:
        t = n1 + np.array([0, 0, 1], np.float32)
        u = n2 * np.array([-1, -1, 1], np.float32)
        # Stable division for near-horizontal base normals; tangent normals with
        # z=-1 are singular, so use a valid normalized additive fallback there.
        tz = t[..., 2:3]
        normal = t * np.sum(t * u, axis=-1, keepdims=True) / np.maximum(tz, 1e-6) - u
        normal = np.where(tz > 1e-6, normal, n2)
    else:
        raise ValueError(f"Unknown normal blend: {mode}")
    result = (_normalize(normal) + 1) * 0.5
    if base.shape[-1] > 3:
        result = np.concatenate((result, base[..., 3:]), axis=-1)
    return result.astype(np.float32)


def _rgb_to_hsv(rgb):
    maximum, minimum = rgb.max(axis=-1), rgb.min(axis=-1)
    delta = maximum - minimum
    safe = np.maximum(delta, 1e-8)
    hue = np.where(maximum == rgb[..., 0], (rgb[..., 1] - rgb[..., 2]) / safe,
                   np.where(maximum == rgb[..., 1], (rgb[..., 2] - rgb[..., 0]) / safe + 2,
                            (rgb[..., 0] - rgb[..., 1]) / safe + 4)) / 6
    hue = np.where(delta > 1e-8, hue % 1, 0)
    sat = delta / np.maximum(maximum, 1e-8)
    return hue, sat, maximum


def _hsv_to_rgb(h, s, v):
    # Explicit RGB phase offsets avoid a lookup table and preserve broadcasting.
    k = (h[..., None] * 6 + np.array([5, 3, 1], np.float32)) % 6
    return v[..., None] * (1 - s[..., None] * np.clip(np.minimum(k, 4 - k), 0, 1))


def _color_apply(data, key, params):
    gray_input = data.ndim == 2 or data.shape[-1] < 3
    rgb = np.repeat(_luma(data)[..., None], 3, axis=-1) if gray_input else data[..., :3]
    amount = float(params.get("amount", 1))
    if key in {"HSL", "HSV", "HUE_SHIFT", "SATURATION"}:
        h, s, v = _rgb_to_hsv(rgb)
        h = (h + float(params.get("hue", params.get("angle", 0) / 360))) % 1
        s = np.clip(s * float(params.get("saturation", params.get("strength", 1))), 0, 1)
        if key == "HSL":
            minimum = rgb.min(axis=-1)
            light = (v + minimum) * 0.5
            chroma = v - minimum
            saturation = chroma / np.maximum(1 - np.abs(2 * light - 1), 1e-8)
            saturation = np.clip(saturation * float(params.get("saturation", params.get("strength", 1))), 0, 1)
            light = np.clip(light + float(params.get("lightness", params.get("offset", (0, 0))[0])), 0, 1)
            value = light + saturation * np.minimum(light, 1 - light)
            out = _hsv_to_rgb(h, 2 * (1 - light / np.maximum(value, 1e-8)), value)
        else:
            v = np.clip(v + float(params.get("value", 0)), 0, 1)
            out = _hsv_to_rgb(h, s, v)
    elif key == "DESATURATE":
        out = np.repeat(_luma(rgb)[..., None], 3, axis=-1)
    elif key == "COLOR_BALANCE":
        out = rgb + np.asarray(params.get("balance", params.get("color", (0, 0, 0))), np.float32)[:3]
    elif key == "TEMPERATURE":
        out = rgb * (1 + np.asarray((0.2, 0, -0.2), np.float32) * amount)
    elif key in {"COLORIZE", "TINT"}:
        color = np.asarray(params.get("color", (0.5, 0.5, 0.5)), np.float32)[:3]
        target = _luma(rgb)[..., None] * color if key == "COLORIZE" else color
        out = rgb * (1 - np.clip(amount, 0, 1)) + target * np.clip(amount, 0, 1)
    elif key == "REPLACE_COLOR":
        color = np.asarray(params.get("color", (1, 0, 0)), np.float32)[:3]
        replacement = np.asarray(params.get("color2", (0, 0, 1)), np.float32)[:3]
        distance = np.linalg.norm(rgb - color, axis=-1)
        threshold = float(params.get("tolerance", 0.15))
        coverage = (1 - _smooth(threshold, threshold * 2 + 1e-6, distance))[..., None]
        out = rgb * (1 - coverage) + replacement * coverage
    elif key == "CHANNEL_MIXER":
        matrix = np.asarray(params.get("matrix", np.eye(3)), np.float32).reshape(3, 3)
        out = np.einsum("...j,ij->...i", rgb, matrix)
        out += np.asarray(params.get("bias", (0, 0, 0)), np.float32)
    elif key == "GRADIENT_MAP":
        stops = params.get("stops", [(0, params.get("color", (0, 0, 0))),
                                      (1, params.get("color2", (1, 1, 1)))])
        stops = sorted(stops, key=lambda s: s[0])
        if len(stops) < 2 or any(stops[i][0] >= stops[i + 1][0] for i in range(len(stops) - 1)):
            raise ValueError("Gradient stops need at least two distinct ordered positions")
        positions = np.array([stop[0] for stop in stops])
        colors = np.array([stop[1][:3] for stop in stops])
        gray = _luma(rgb)
        out = np.stack([np.interp(gray, positions, colors[:, c]) for c in range(3)], axis=-1)
    else:
        raise ValueError(f"Unknown color filter: {key}")
    if not gray_input and data.shape[-1] > 3:
        out = np.concatenate((out, data[..., 3:]), axis=-1)
    elif gray_input and key not in {"GRADIENT_MAP", "COLORIZE", "TINT", "REPLACE_COLOR", "COLOR_BALANCE", "TEMPERATURE"}:
        out = _luma(out)
        if data.ndim == 3:
            out = out[..., None]
    return out


@measured('filters')
def apply(data, kind, **params):
    """Apply a named filter without mutating input; extra parameters are ignored.

    Levels: black/white/gamma/output_black/output_white. Blur/morphology: radius.
    Warp: field (optional HxW or HxWx2), strength (pixels), angle (degrees).
    Slope blur: field, strength, samples, mode ('MIN', 'MAX' or 'BLUR').
    HSL: hue (turns), saturation (factor), lightness (offset). Gradient map:
    color/color2 or stops=[(position, RGB), ...]. Sharpen: radius, amount.
    """
    key = str(kind).strip().upper().replace("-", "_").replace(" / ", "_").replace(" ", "_")
    key = {"GAUSSIAN": "GAUSSIAN_BLUR", "GRAYSCALE": "DESATURATE", "HSL_ADJUSTMENT": "HSL"}.get(key, key)
    if key not in CATALOG:
        raise ValueError(f"Unknown texture filter: {kind}")
    source = np.asarray(data, np.float32)
    if source.ndim not in (2, 3) or 0 in source.shape:
        raise ValueError("Texture filters need a nonempty HxW or HxWxC image")
    if not np.all(np.isfinite(source)):
        raise ValueError("Texture filter input must be finite")
    radius = max(float(params.get("radius", 2)), 0)
    amount = float(params.get("amount", 1))
    strength = float(params.get("strength", 1))
    black, white = float(params.get("black", 0)), float(params.get("white", 1))
    gamma = max(float(params.get("gamma", 1)), 1e-5)
    if key in {"LEVELS", "ROUGHNESS_REMAP", "METALLIC_REMAP", "HEIGHT_REMAP"}:
        if white <= black:
            raise ValueError("White level must be greater than black level")
        out = np.clip((source - black) / (white - black), 0, 1) ** (1 / gamma)
        out = float(params.get("output_black", 0)) + out * (float(params.get("output_white", 1)) - float(params.get("output_black", 0)))
    elif key == "GAMMA":
        out = np.maximum(source, 0) ** (1 / gamma)
    elif key == "INVERT":
        out = 1 - source
    elif key == "CLAMP":
        out = np.clip(source, black, white)
    elif key in {"BRIGHTNESS_CONTRAST", "ROUGHNESS_CONTRAST"}:
        brightness = float(params.get("brightness", params.get("amount", 0))) if key == "BRIGHTNESS_CONTRAST" else 0
        out = (source - 0.5) * float(params.get("contrast", 1)) + 0.5 + brightness
    elif key in {"THRESHOLD", "HISTOGRAM_SCAN", "HISTOGRAM_RANGE"}:
        position = float(params.get("position", params.get("amount", 0.5)))
        softness = max(float(params.get("softness", 0.05)) / max(float(params.get("contrast", 1)), 0.001), 1e-6)
        if key == "THRESHOLD":
            out = (source >= position).astype(np.float32)
        elif key == "HISTOGRAM_SCAN":
            out = _smooth(position - softness, position + softness, source)
        else:
            out = _smooth(black - softness, black, source) * (1 - _smooth(white, white + softness, source))
    elif key in {"POSTERIZE", "QUANTIZE"}:
        bands = max(int(params.get("steps", 4)), 2)
        out = np.round(source * (bands - 1)) / (bands - 1)
    elif key in {"GAUSSIAN_BLUR", "HIGH_PASS", "SHARPEN", "SMOOTH_MASK"}:
        blurred = gaussian_blur(source, radius)
        if key == "HIGH_PASS":
            out = source - blurred + 0.5
        elif key == "SHARPEN":
            out = source + (source - blurred) * amount
        elif key == "SMOOTH_MASK":
            out = _smooth(0.25, 0.75, blurred)
        else:
            out = blurred
    elif key == "BLUR":
        half = min(int(np.ceil(radius)), 192)
        out = _box_axis(_box_axis(source, half, 0), half, 1)
    elif key in {"DILATE", "ERODE", "MORPHOLOGICAL_EXPAND", "MORPHOLOGICAL_CONTRACT"}:
        out = _morphology(source, radius, key in {"DILATE", "MORPHOLOGICAL_EXPAND"})
    elif key == "EDGE_DETECT":
        gray = _luma(source)
        gx = _convolve_axis(_convolve_axis(gray, np.array([-1, 0, 1], np.float32), 1), np.array([1, 2, 1], np.float32) / 4, 0)
        gy = _convolve_axis(_convolve_axis(gray, np.array([-1, 0, 1], np.float32), 0), np.array([1, 2, 1], np.float32) / 4, 1)
        out = np.sqrt(gx * gx + gy * gy) * strength
        if source.ndim == 3:
            out = np.repeat(out[..., None], source.shape[-1], axis=-1)
    elif key == "HEIGHT_TO_NORMAL":
        out = height_to_normal(source, strength)
    elif key in {"NORMAL_STRENGTH", "NORMAL_INVERT_Y", "NORMAL_COMBINE"}:
        if source.ndim != 3 or source.shape[-1] < 3:
            raise ValueError("Normal effects need RGB normal maps")
        out = source.copy()
        if key == "NORMAL_INVERT_Y":
            out[..., 1] = 1 - out[..., 1]
        elif key == "NORMAL_COMBINE":
            out = normal_combine(source, params.get("detail", np.full_like(source, (0.5, 0.5, 1) if source.shape[-1] == 3 else (0.5, 0.5, 1, 1))))
        else:
            normal = source[..., :3] * 2 - 1
            normal[..., :2] *= strength
            out[..., :3] = (_normalize(normal) + 1) * 0.5
    elif key == "AO_STRENGTH":
        out = 1 - (1 - source) * strength
    elif key in {"DIRECTIONAL_BLUR", "RADIAL_BLUR", "SLOPE_BLUR", "WARP", "DIRECTIONAL_WARP", "VECTOR_WARP", "UV_DISTORTION", "TWIRL", "RIPPLE", "WAVE", "NON_UNIFORM_TRANSFORM"}:
        y, x = np.indices(source.shape[:2], dtype=np.float32)
        angle = np.deg2rad(float(params.get("angle", 0)))
        # A per-texel direction (surface space) replaces the single angle.
        direction = params.get("direction_field")
        cos_a, sin_a = (np.cos(angle), np.sin(angle)) if direction is None else (direction[..., 0], direction[..., 1])
        field = np.asarray(params.get("field", _luma(source)), np.float32)
        if field.shape[:2] != source.shape[:2]:
            raise ValueError("Displacement field must match texture dimensions")
        if key in {"DIRECTIONAL_BLUR", "RADIAL_BLUR"}:
            count = min(max(int(params.get("samples", 9)), 2), 64)
            out = np.zeros_like(source)
            cx, cy = (source.shape[1] - 1) / 2, (source.shape[0] - 1) / 2
            for step in np.linspace(-1, 1, count):
                if key == "DIRECTIONAL_BLUR":
                    sx, sy = x + cos_a * radius * step, y + sin_a * radius * step
                else:
                    theta = np.deg2rad(radius * step)
                    sx = cx + (x - cx) * np.cos(theta) - (y - cy) * np.sin(theta)
                    sy = cy + (x - cx) * np.sin(theta) + (y - cy) * np.cos(theta)
                out += sample(source, sx, sy) / count
        elif key == "SLOPE_BLUR":
            count = min(max(int(params.get("samples", 4)), 1), 32)
            dx, dy = _gradient(_luma(field))
            out = source.copy()
            mode = str(params.get("mode", "BLUR")).upper()
            for _ in range(count):
                shifted = sample(out, x + dx * strength / count, y + dy * strength / count)
                out = np.minimum(out, shifted) if mode == "MIN" else np.maximum(out, shifted) if mode == "MAX" else (out + shifted) * 0.5
        elif key in {"WARP", "DIRECTIONAL_WARP", "VECTOR_WARP", "UV_DISTORTION"}:
            if key in {"VECTOR_WARP", "UV_DISTORTION"}:
                if field.ndim != 3 or field.shape[-1] < 2:
                    raise ValueError("Vector warp requires a two-channel displacement field")
                dx, dy = (field[..., 0] * 2 - 1) * strength, (field[..., 1] * 2 - 1) * strength
            elif key == "DIRECTIONAL_WARP":
                distance = (_luma(field) * 2 - 1) * strength
                dx, dy = cos_a * distance, sin_a * distance
            else:
                dx, dy = _gradient(_luma(field))
                dx, dy = dx * strength, dy * strength
            out = sample(source, x + dx, y + dy)
        else:
            cx, cy = (source.shape[1] - 1) / 2, (source.shape[0] - 1) / 2
            px, py = x - cx, y - cy
            distance = np.sqrt(px * px + py * py)
            if key == "TWIRL":
                theta = (1 - np.clip(distance / max(radius, 1e-6), 0, 1)) ** 2 * angle
                sx, sy = px * np.cos(theta) - py * np.sin(theta), px * np.sin(theta) + py * np.cos(theta)
            elif key == "RIPPLE":
                shift = np.sin(distance / max(radius, 1e-6) * 2 * np.pi) * strength
                sx, sy = px + px / np.maximum(distance, 1e-6) * shift, py + py / np.maximum(distance, 1e-6) * shift
            elif key == "WAVE":
                shift = np.sin((px * np.cos(angle) + py * np.sin(angle)) / max(radius, 1e-6) * 2 * np.pi) * strength
                sx, sy = px - np.sin(angle) * shift, py + np.cos(angle) * shift
            else:
                scale = np.broadcast_to(np.asarray(params.get("scale", (1, 1)), np.float32), (2,))
                offset = np.asarray(params.get("offset", (0, 0)), np.float32)
                sx = (px * np.cos(angle) + py * np.sin(angle)) / max(abs(scale[0]), 1e-6) - offset[0]
                sy = (-px * np.sin(angle) + py * np.cos(angle)) / max(abs(scale[1]), 1e-6) - offset[1]
            out = sample(source, sx + cx, sy + cy)
    else:
        out = _color_apply(source, key, params)
    # One sanitised copy, clipped in place; `out` may alias the caller's data.
    result = np.nan_to_num(out, nan=0, posinf=1, neginf=0)
    np.clip(result, 0, 1, out=result)
    return result.astype(np.float32, copy=False)
