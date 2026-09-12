import numpy as np


def edge_wear(curvature, ao, noise, amount, contrast, noise_strength):
    value = curvature + (amount - 0.5) * 2 + (noise - 0.5) * noise_strength
    if ao is not None:
        value *= 0.5 + 0.5 * ao
    return np.clip(value * contrast, 0, 1)
