"""Texture-space wear: grunge breaks up curvature coverage, never creates it."""

import numpy as np


def edge_wear(curvature, ao, noise, amount, contrast, noise_strength):
    # Curvature maps produced by our baker use black for flat surfaces.
    # Additive noise/amount used to generate wear on those flat pixels.
    edges = np.clip(curvature, 0, 1)
    coverage = np.clip(edges * (2 * amount) * contrast, 0, 1)
    breakup = (1 - noise_strength) + np.clip(noise, 0, 1) * noise_strength
    coverage *= breakup
    if ao is not None:
        coverage *= np.clip(ao, 0, 1)
    return np.clip(coverage, 0, 1)
