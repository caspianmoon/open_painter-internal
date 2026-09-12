"""Normal-image curvature approximation, isolated for future replacement."""

import numpy as np

from ...core import image_manager as images


def derive(normal: np.ndarray) -> np.ndarray:
    vectors = normal[..., :3] * 2 - 1
    padded = np.pad(vectors, ((1, 1), (1, 1), (0, 0)), mode="edge")
    dx = (padded[1:-1, 2:, 0] - padded[1:-1, :-2, 0]) * 0.5
    dy = (padded[2:, 1:-1, 1] - padded[:-2, 1:-1, 1]) * 0.5
    # Unsigned curvature highlights surface changes; neutral flat areas stay 0.
    curvature = np.clip(np.sqrt(dx * dx + dy * dy) * 8, 0, 1)
    result = np.ones(normal.shape, dtype=np.float32)
    result[..., :3] = curvature[..., None]
    return result


def bake_curvature(ts):
    source = next((m.image for m in ts.baked_maps if m.kind == "NORMAL" and m.image), None)
    if source is None:
        raise ValueError("Curvature requires a baked Normal map. Enable Normal baking first.")
    image = images.create(f"{ts.name}_BakedCurvature", int(ts.resolution))
    images.write(image, derive(images.pixels(source)))
    return image
