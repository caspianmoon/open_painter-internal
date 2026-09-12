"""Pointwise adjustments; row tiles produce the same result as whole images.

Effects operate on straight linear values and never modify source buffers or alpha.
Levels without clamping extends gamma symmetrically below zero; final output/channel
storage still has its own range constraints (signed HDR channels are not implemented).
"""

import numpy as np


def levels(value, black=0, white=1, gamma=1, output_black=0, output_white=1, clamp=True):
    if white <= black:
        raise ValueError("Levels Input White must be greater than Input Black.")
    if gamma <= 0:
        raise ValueError("Levels Gamma must be positive.")
    value = (value - black) / (white - black)
    if clamp:
        value = np.clip(value, 0, 1)
    value = np.sign(value) * np.power(np.abs(value), 1 / gamma)
    result = output_black + value * (output_white - output_black)
    return np.clip(result, 0, 1) if clamp else result


def apply_effects(effects, value, channel, mask=False):
    result = value
    for effect in effects:
        if not effect.enabled or effect.opacity == 0 or (not mask and effect.channel != channel):
            continue
        if effect.type == "LEVELS":
            adjusted = levels(
                result,
                effect.input_black,
                effect.input_white,
                effect.gamma,
                effect.output_black,
                effect.output_white,
                effect.clamp,
            )
        elif effect.filter == "INVERT":
            adjusted = 1 - result
        elif effect.filter == "GRAYSCALE":
            adjusted = (
                result
                if mask or result.shape[-1] == 1
                else np.sum(result * (0.2126, 0.7152, 0.0722), axis=-1, keepdims=True)
            )
            adjusted = np.broadcast_to(adjusted, np.shape(result))
        elif effect.filter == "BRIGHTNESS_CONTRAST":
            adjusted = (result - 0.5) * effect.contrast + 0.5 + effect.brightness
        else:
            raise ValueError(f"Unsupported filter: {effect.filter}")
        if effect.clamp:
            adjusted = np.clip(adjusted, 0, 1)
        if not mask and effect.component != "RGB" and channel in {"BASE_COLOR", "NORMAL"}:
            selected = np.arange(3) == {"R": 0, "G": 1, "B": 2}[effect.component]
            adjusted = np.where(selected, adjusted, result)
        result = result + effect.opacity * (adjusted - result)
    return result


def apply_group_effects(effects, premultiplied, coverage, channel):
    if not effects:
        return premultiplied
    straight = np.divide(
        premultiplied, coverage, out=np.zeros_like(premultiplied), where=coverage > 1e-8
    )
    return apply_effects(effects, straight, channel) * coverage
