"""Full-width strip support. Halos clamp at real image borders only."""


def expand(low, high, halo, height):
    return max(0, low - halo), min(height, high + halo)


def slices(height, rows, halo=0):
    if rows < 1:
        raise ValueError('Strip height must be positive')
    for low in range(0, height, rows):
        high = min(height, low + rows)
        start, stop = expand(low, high, halo, height)
        yield slice(low, high), slice(start, stop), slice(low - start, high - start)
