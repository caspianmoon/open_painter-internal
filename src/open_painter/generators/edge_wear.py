"""Texture-space wear: grunge breaks up curvature coverage, never creates it."""

import numpy as np


def edge_wear(curvature, ao, noise, amount, contrast, noise_strength, width=1.0, micro=None):
    # Curvature maps produced by our baker use black for flat surfaces.
    # Additive noise/amount used to generate wear on those flat pixels.
    edges = np.clip(curvature, 0, 1)
    if width < 1:
        # The V2 map falls off linearly across the baked radius, so a narrower
        # band is the part of it closer to the edge than (1 - width).
        edges = np.clip((edges - (1 - width)) / width, 0, 1)
    if micro is not None:
        # Anchored micro convexity has its own radius; Edge Width narrows only the baked band.
        edges = np.maximum(edges, np.clip(micro, 0, 1))
    coverage = np.clip(edges * (2 * amount) * contrast, 0, 1)
    breakup = (1 - noise_strength) + np.clip(noise, 0, 1) * noise_strength
    coverage *= breakup
    if ao is not None:
        coverage *= np.clip(ao, 0, 1)
    return np.clip(coverage, 0, 1)
