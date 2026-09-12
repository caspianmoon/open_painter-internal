"""Mesh-aware generator interface and built-in dispatch."""

from dataclasses import dataclass

from ..evaluation.blend_modes import sample_uv
from ..evaluation.mask_eval import map_pixels
from .dirt import dirt
from .dust import dust
from .edge_wear import edge_wear
from .noise import noise


@dataclass(frozen=True)
class Generator:
    """Evaluate a tile with explicit texture-set and parameter inputs."""

    id: str
    name: str

    def evaluate(self, texture_set, parameters, u, v, cache):
        n = noise(u, v, parameters.noise_scale, parameters.seed)
        if self.id == "NOISE":
            return n

        def mesh_map(kind):
            return sample_uv(map_pixels(texture_set, kind, cache), u, v)

        args = (n, parameters.amount, parameters.contrast, parameters.noise_strength)
        if self.id == "EDGE_WEAR":
            ao = (
                mesh_map("AO")[..., 0]
                if any(m.kind == "AO" and m.image for m in texture_set.baked_maps)
                else None
            )
            return edge_wear(mesh_map("CURVATURE")[..., 0], ao, *args)
        if self.id == "DIRT":
            return dirt(mesh_map("AO")[..., 0], mesh_map("CURVATURE")[..., 0], *args)
        if self.id == "DUST":
            return dust(mesh_map("NORMAL"), *args)
        raise ValueError(f"Unknown generator: {self.id}")


GENERATORS = {
    key: Generator(key, key.replace("_", " ").title())
    for key in ("NOISE", "EDGE_WEAR", "DIRT", "DUST")
}


def evaluate(ts, mask, u, v, cache):
    return GENERATORS["NOISE" if mask.source == "NOISE" else mask.generator].evaluate(
        ts, mask, u, v, cache
    )
