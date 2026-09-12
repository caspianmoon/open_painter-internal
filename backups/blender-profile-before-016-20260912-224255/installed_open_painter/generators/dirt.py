import numpy as np


def dirt(ao, curvature, noise, amount, contrast, noise_strength):
    value = (
        (1 - ao) * (0.8 + 0.2 * (1 - curvature)) + (amount - 0.5) + (noise - 0.5) * noise_strength
    )
    return np.clip(value * contrast, 0, 1)
