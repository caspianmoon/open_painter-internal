"""Texture layouts shared by allocation, bindings and memory accounting."""

FORMAT_BYTES = {'R16F': 2, 'R32F': 4, 'RG16F': 4, 'RG32F': 8,
                'RGBA8': 4, 'RGBA16F': 8, 'RGBA32F': 16, 'R32UI': 4}
SCALAR_CHANNELS = frozenset({'ROUGHNESS', 'METALLIC', 'HEIGHT', 'AO', 'OPACITY'})


def texture_bytes(width, height, fmt):
    """Base-level storage; unknown formats must not silently undercount a budget."""
    return int(width) * int(height) * FORMAT_BYTES[fmt]


def scalar_unit(key):
    return len(key) > 2 and key[0] in {'CHANNEL', 'SRC'} and key[2] in SCALAR_CHANNELS


def layout(key, export, precise=False):
    if export == 'Value':
        return 'R32F'
    high = precise or (len(key) > 2 and key[2] == 'HEIGHT')
    if scalar_unit(key):
        return 'RG32F' if high else 'RG16F'
    return 'RGBA32F' if high else 'RGBA16F'
