"""Shared scalar surface contract for authoring, persistence and portable materials."""
import math

DEFAULTS = {"anisotropy": 0, "coat_weight": 0, "coat_roughness": .03, "height_distance": .05,
            "ior": 1.5, "transmission_weight": 0, "subsurface_weight": 0, "subsurface_scale": .001, "sheen_weight": 0, "sheen_roughness": .5}
FIELDS = tuple(DEFAULTS)
BOUNDS = {key: (0, 1) for key in FIELDS}
BOUNDS.update(ior=(1, 4), height_distance=(0, math.inf), subsurface_scale=(0, 10))


def valid(key, value):
    if key not in BOUNDS or isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    low, high = BOUNDS[key]
    return math.isfinite(value) and low <= value <= high


def inputs(ts):
    return (("Emission Strength", 1), ("Anisotropic", ts.anisotropy),
            ("Coat Weight", ts.coat_weight), ("Coat Roughness", ts.coat_roughness),
            ("IOR", ts.ior), ("Transmission Weight", ts.transmission_weight),
            ("Subsurface Weight", ts.subsurface_weight), ("Subsurface Scale", ts.subsurface_scale),
            ("Subsurface Radius", (1., 1., 1.)),
            ("Sheen Weight", ts.sheen_weight), ("Sheen Roughness", ts.sheen_roughness))
