import numpy as np


def dust(normal, noise, amount, contrast, noise_strength):
    """Tangent-normal approximation; world-space direction is a later milestone."""
    return np.clip(
        (normal[..., 2] - (1 - amount) + (noise - 0.5) * noise_strength) * contrast, 0, 1
    )
