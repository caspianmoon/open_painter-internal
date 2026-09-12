"""Explicit channel packing; scalar channels use the red component."""

import numpy as np


def pack(channels, assignments):
    reference = next(iter(channels.values()))
    result = np.ones(reference.shape, dtype=np.float32)
    for index, key in enumerate(assignments):
        invert = key.startswith("ONE_MINUS_")
        key = key.removeprefix("ONE_MINUS_")
        if key in {"ZERO", "ONE"}:
            value = 0 if key == "ZERO" else 1
        elif key in channels:
            value = channels[key][..., 0]
        else:
            raise ValueError(f"Missing channel for packing: {key}")
        result[..., index] = 1 - value if invert else value
    return result
