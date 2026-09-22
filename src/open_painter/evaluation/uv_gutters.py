"""Transient sampling gutters for geometry-derived UV fields.

A field is only defined on mesh-owned texels. Filtering its zero-filled atlas
background before mask inversion creates false coverage along every chart edge.
Use the exporter's geometry ownership and deterministic nearest donors without
changing a single owned texel or any persistent image pixel.
"""
from weakref import WeakKeyDictionary

VERSION = 1
_donors = WeakKeyDictionary()


def padded(ts, values):
    import numpy as np
    from ..export import padding
    values = np.asarray(values, np.float32)
    if values.ndim not in {2, 3} or values.shape[0] != values.shape[1]:
        raise ValueError('Geometry field sampling requires a square raster')
    ownership = padding.ownership(ts, values.shape[0])
    donor = _donors.get(ownership)
    if donor is None:
        donor, _ = padding.nearest_donors(ownership.covered, ownership.island,
                                         padding.automatic_padding(ownership.n))
        _donors[ownership] = donor
    scalar = values.ndim == 2
    result = padding.extend(values[..., None] if scalar else values, donor)
    return result[..., 0] if scalar else result
