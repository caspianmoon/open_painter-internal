"""Deterministic, vectorized grayscale sources in texture coordinates.

All sources share ``evaluate(kind, u, v, seed=1, scale=8, **params)``. Coordinates
are broadcastable arrays, not pixel indices. ``angle`` is in degrees; ``width``
and ``density`` are dimensionless. No image-dependent normalization is used.
The Perlin implementation uses quintic interpolation of lattice gradients:
https://mrl.cs.nyu.edu/~perlin/noise/ . The integer hash and 2D implementations
here are original and do not use the reference permutation table.
"""

from ..evaluation.metrics import measured

import numpy as np


NOISES = {
    "PERLIN": "Quintic-interpolated two-dimensional gradient noise",
    "SIMPLEX": "Two-dimensional simplex lattice gradient noise",
    "FBM": "Normalized octave sum of gradient noise",
    "VALUE": "Quintic-interpolated random lattice values",
    "VORONOI": "Stable random grayscale per nearest feature cell",
    "CELLULAR": "Rounded cell interiors from feature distances",
    "WORLEY": "Distance to the nearest jittered feature point",
    "CLOUD": "Soft low-frequency octave noise",
    "FRACTAL": "Fine six-octave gradient fractal",
    "MULTI_FRACTAL": "Multiplicative octave modulation",
    "RIDGED": "Sharp ridges from folded gradient noise",
    "TURBULENCE": "Absolute-value gradient octave sum",
    "DIRECTIONAL": "Stretched gradient noise along one axis",
    "ANISOTROPIC": "Fine, strongly stretched octave noise",
    "WARPED": "Gradient noise with continuous domain distortion",
    "GRUNGE_FINE": "Small-scale uneven surface variation",
    "GRUNGE_MEDIUM": "Multiscale stained surface variation",
    "GRUNGE_HEAVY": "High-contrast clustered surface variation",
    "GRUNGE_SPOTS": "Irregular spots with soft breakup",
    "GRUNGE_SCRATCHES": "Randomly oriented finite scratch segments",
    "GRUNGE_STREAKS": "Broken directional streaks",
    "GRUNGE_DIRT": "Clouded grime with fine variation",
    "GRUNGE_DUST": "Sparse fine particulate coverage",
    "GRUNGE_LEAKS": "Uneven elongated drip coverage",
    "GRUNGE_CRACKS": "Warped cellular boundaries",
    "METAL_GRAIN": "Fine metallic microstructure",
    "BRUSHED_METAL_LINES": "Directional fine brushing and scratches",
    "MACHINING_LINES": "Concentric machining grooves with variation",
    "CASTING_NOISE": "Porous casting pits and coarse texture",
    "ORANGE_PEEL": "Rounded micro-dimples for coated surfaces",
    "PLASTIC_GRAIN": "Fine rounded molded surface grain",
    "RUBBER_GRAIN": "Matte granular irregularity",
    "LEATHER_GRAIN": "Warped cell creases with fine grain",
    "FABRIC_WEAVE": "Alternating raised warp and weft threads",
    "WOOD_GRAIN": "Warped growth rings along the grain",
    "WOOD_PORES": "Fine elongated timber pores",
    "CONCRETE_PORES": "Sparse rounded concrete pits",
    "CONCRETE_AGGREGATE": "Multiscale mineral aggregate cells",
    "STONE_GRAIN": "Contrasting mineral flecks at multiple scales",
    "RUST_FLAKES": "Layered irregular corrosion flakes",
    "RUST_PITTING": "Clustered surface corrosion pits",
    "OXIDATION_SPOTS": "Soft clustered oxidation patches",
    "HAMMERED_METAL": "Rounded overlapping hammer impressions",
    "SAND_GRAIN": "Fine uneven mineral grains",
    "DUST_SPECKLES": "Sparse separated dust particles",
}

FILLS = {
    "SOLID": "Constant grayscale value",
    "GRADIENT": "Repeating linear gradient",
    "LINEAR_GRADIENT": "Repeating linear gradient along the rotated axis",
    "RADIAL_GRADIENT": "Radial falloff around a configurable center",
    "PATTERN": "Rounded tiled geometric pattern",
    "TILE_PATTERN": "Square tiles with configurable grout",
    "BRICK_PATTERN": "Staggered bricks with configurable grout",
    "HEX_PATTERN": "Hexagonal cells with configurable borders",
    "GRID": "Orthogonal grid lines",
    "CHECKER": "Alternating checker squares",
    "STRIPES": "Parallel bands with configurable width",
    "DOTS": "Regular round dots",
    "SCRATCHES": "Random finite surface scratch segments",
    "DIRECTIONAL_SCRATCHES": "Aligned finite surface scratch segments",
    "CRACKS": "Cell-boundary fracture lines",
    "FIBERS": "Uneven elongated directional fibers",
    "FINGERPRINTS": "Warped nested fingerprint ridges in sparse elliptical patches",
    "SWIRLS": "Broken circular microscratches around seeded polishing centers",
    "SMUDGES": "Soft uneven stretched handling marks",
    "VEINS": "Warped branching mineral veins at several scales",
}

CATALOG = {**NOISES, **FILLS}
ALIASES = {
    "PERLIN_NOISE": "PERLIN", "SIMPLEX_NOISE": "SIMPLEX", "FBM_NOISE": "FBM",
    "VALUE_NOISE": "VALUE", "CELLULAR_NOISE": "CELLULAR", "WORLEY_NOISE": "WORLEY",
    "CLOUD_NOISE": "CLOUD", "FRACTAL_NOISE": "FRACTAL", "MULTI_FRACTAL_NOISE": "MULTI_FRACTAL",
    "RIDGED_NOISE": "RIDGED", "DIRECTIONAL_NOISE": "DIRECTIONAL",
    "ANISOTROPIC_NOISE": "ANISOTROPIC", "WARPED_NOISE": "WARPED",
    "GRUNGE": "GRUNGE_MEDIUM", "NOISE": "FBM", "PROCEDURAL_NOISE": "FBM",
    "SOLID_COLOR": "SOLID", "WEAVE": "FABRIC_WEAVE", "PITS": "CONCRETE_PORES",
    "SPOTS": "GRUNGE_SPOTS", "STREAKS": "GRUNGE_STREAKS", "CRACKING": "CRACKS",
    "RANDOM_SCRATCHES": "SCRATCHES", "BRUSHED": "BRUSHED_METAL_LINES",
    "MACHINING": "MACHINING_LINES",
}


def _hash(x, y, seed):
    # Unsigned 32-bit arithmetic implements the intended modulo-2**32 hash
    # directly; uint64 intermediates and masks doubled all lattice temporaries.
    with np.errstate(over="ignore", invalid="ignore"):
        a = np.asarray(x, dtype=np.int64).astype(np.uint32)
        b = np.asarray(y, dtype=np.int64).astype(np.uint32)
        salt = np.uint32(((int(seed) & 0xFFFFFFFF) * 2246822519) & 0xFFFFFFFF)
        h = a * np.uint32(374761393) + b * np.uint32(668265263) + salt
        h = (h ^ (h >> np.uint32(13))) * np.uint32(1274126177)
        h ^= h >> np.uint32(16)
    return h.astype(np.float32) * np.float32(1.0 / 4294967296.0)


def _fade(t):
    return t * t * t * (t * (t * 6 - 15) + 10)


def _mix(a, b, t):
    return a + (b - a) * t


def _smooth(a, b, x):
    t = np.clip((x - a) / max(float(b - a), 1e-6), 0, 1)
    return t * t * (3 - 2 * t)


def _value(x, y, seed):
    ix, iy = np.floor(x), np.floor(y)
    tx, ty = _fade(x - ix), _fade(y - iy)
    return _mix(_mix(_hash(ix, iy, seed), _hash(ix + 1, iy, seed), tx),
                _mix(_hash(ix, iy + 1, seed), _hash(ix + 1, iy + 1, seed), tx), ty)


def _gradient(ix, iy, dx, dy, seed):
    angle = _hash(ix, iy, seed) * np.float32(2 * np.pi)
    return np.cos(angle) * dx + np.sin(angle) * dy


def _perlin(x, y, seed):
    ix, iy = np.floor(x), np.floor(y)
    dx, dy = x - ix, y - iy
    return _mix(_mix(_gradient(ix, iy, dx, dy, seed),
                     _gradient(ix + 1, iy, dx - 1, dy, seed), _fade(dx)),
                _mix(_gradient(ix, iy + 1, dx, dy - 1, seed),
                     _gradient(ix + 1, iy + 1, dx - 1, dy - 1, seed), _fade(dx)), _fade(dy))


def _simplex(x, y, seed):
    skew, unskew = np.float32(0.3660254037844386), np.float32(0.21132486540518713)
    s = (x + y) * skew
    ix, iy = np.floor(x + s), np.floor(y + s)
    t = (ix + iy) * unskew
    dx, dy = x - ix + t, y - iy + t
    ox = (dx > dy).astype(np.float32)
    oy = 1 - ox
    result = np.zeros_like(x)
    for cx, cy, px, py in ((ix, iy, dx, dy),
                           (ix + ox, iy + oy, dx - ox + unskew, dy - oy + unskew),
                           (ix + 1, iy + 1, dx - 1 + 2 * unskew, dy - 1 + 2 * unskew)):
        attenuation = np.maximum(0.5 - px * px - py * py, 0)
        result += attenuation ** 4 * _gradient(cx, cy, px, py, seed)
    return result * 70


def _fbm(x, y, seed, octaves=4, persistence=0.5, mode="FBM"):
    result, total, amplitude = np.zeros_like(x), 0.0, 1.0
    for i in range(min(max(int(octaves), 1), 8)):
        n = _perlin(x, y, seed + i * 53)
        if mode == "RIDGED":
            n = (1 - np.abs(n) * 1.8) ** 2
        elif mode == "TURBULENCE":
            n = np.abs(n) * 1.8
        else:
            n = 0.5 + n * 0.8
        result += amplitude * n
        total += amplitude
        amplitude *= np.clip(persistence, 0.05, 0.95)
        x, y = x * 2.03 + 13.1, y * 2.03 - 7.7
    return result / total


def _cells(x, y, seed):
    """Nearest/second distances and cell value; 5x5 avoids F2 corner omissions."""
    ix, iy = np.floor(x), np.floor(y)
    first = np.full_like(x, np.inf)
    second = first.copy()
    identity = np.zeros_like(x)
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            cx, cy = ix + dx, iy + dy
            px = cx + _hash(cx, cy, seed)
            py = cy + _hash(cx, cy, seed + 79)
            dist = (x - px) ** 2 + (y - py) ** 2
            closer = dist < first
            second = np.where(closer, first, np.minimum(second, dist))
            identity = np.where(closer, _hash(cx, cy, seed + 173), identity)
            first = np.minimum(first, dist)
    return np.sqrt(first), np.sqrt(second), identity


def _scratch(x, y, seed, width=0.025, density=0.55, directional=False):
    # One finite segment per jittered cell, evaluated with neighbors. This avoids
    # full-image line generation and is independent of texture tile boundaries.
    ix, iy = np.floor(x), np.floor(y)
    result = np.zeros_like(x)
    for dy in range(-1, 2):
        for dx in range(-1, 2):
            cx, cy = ix + dx, iy + dy
            px = x - cx - _hash(cx, cy, seed)
            py = y - cy - _hash(cx, cy, seed + 21)
            angle = (_hash(cx, cy, seed + 51) - 0.5) * (0.16 if directional else np.pi)
            along = px * np.sin(angle) + py * np.cos(angle)
            across = px * np.cos(angle) - py * np.sin(angle)
            half = 0.12 + _hash(cx, cy, seed + 62) * 0.55
            distance = np.sqrt(across ** 2 + np.maximum(np.abs(along) - half, 0) ** 2)
            line = 1 - _smooth(width * 0.25, width, distance)
            result = np.maximum(result, line * (_hash(cx, cy, seed + 9) < density))
    return result


def _pores(x, y, seed, radius=0.2, density=0.55):
    f1, _, identity = _cells(x, y, seed)
    return (1 - _smooth(radius * 0.3, radius, f1)) * (identity < density)


@measured('source_generation')
def evaluate(kind, u, v, seed=1, scale=8, **params):
    """Evaluate one catalog source as finite float32 grayscale in [0, 1].

    Standard parameters: angle (degrees), width, density, octaves, persistence,
    anisotropy, warp, value. Pattern scale counts repetitions per coordinate unit.
    Unknown sources fail explicitly instead of silently substituting a noise.
    """
    key = str(kind).strip().upper().replace("-", "_").replace(" ", "_")
    key = ALIASES.get(key, key)
    if key not in CATALOG:
        raise ValueError(f"Unknown procedural source: {kind}")
    u, v = np.broadcast_arrays(np.asarray(u, np.float32), np.asarray(v, np.float32))
    if not np.all(np.isfinite(u)) or not np.all(np.isfinite(v)):
        raise ValueError("Procedural coordinates must be finite")
    scale = float(scale)
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError("Procedural scale must be finite and positive")
    angle = np.deg2rad(float(params.get("angle", 0)))
    x = (u * np.cos(angle) - v * np.sin(angle)).astype(np.float32) * scale
    y = (u * np.sin(angle) + v * np.cos(angle)).astype(np.float32) * scale
    seed = int(seed)
    width = max(float(params.get("width", 0.04)), 1e-5)
    density = np.clip(float(params.get("density", 0.55)), 0, 1)
    octaves = params.get("octaves", 4)
    persistence = params.get("persistence", 0.5)
    if key == "SOLID":
        out = np.full_like(x, float(params.get("value", 1)))
    elif key in {"GRADIENT", "LINEAR_GRADIENT"}:
        out = x % 1
    elif key == "RADIAL_GRADIENT":
        out = 1 - np.sqrt((u - params.get("center_x", 0.5)) ** 2
                          + (v - params.get("center_y", 0.5)) ** 2) * scale
    elif key == "PERLIN":
        out = 0.5 + _perlin(x, y, seed) * 0.8
    elif key == "SIMPLEX":
        out = 0.5 + _simplex(x, y, seed) * 0.5
    elif key == "VALUE":
        out = _value(x, y, seed)
    elif key in {"FBM", "FRACTAL", "CLOUD", "RIDGED", "TURBULENCE"}:
        factor = 0.5 if key == "CLOUD" else 1
        out = _fbm(x * factor, y * factor, seed, 6 if key == "FRACTAL" else octaves,
                   persistence, key)
    elif key == "MULTI_FRACTAL":
        out = np.ones_like(x)
        for i in range(min(max(int(octaves), 1), 8)):
            out *= 0.7 + 0.6 * _fbm(x * 2 ** i, y * 2 ** i, seed + i, 1)
        out = out * 0.5
    elif key in {"VORONOI", "CELLULAR", "WORLEY", "HAMMERED_METAL", "ORANGE_PEEL"}:
        a, _, identity = _cells(x, y, seed)
        out = identity if key == "VORONOI" else a
        if key == "CELLULAR":
            out = 1 - _smooth(0, 0.85, a)
        elif key == "HAMMERED_METAL":
            out = 1 - np.sqrt(np.maximum(1 - np.clip(a, 0, 1) ** 2, 0))
        elif key == "ORANGE_PEEL":
            out = _smooth(0, 0.9, a) * 0.65 + _fbm(x * 2, y * 2, seed + 9, 2) * 0.35
    elif key in {"DIRECTIONAL", "ANISOTROPIC", "FIBERS", "BRUSHED_METAL_LINES", "WOOD_PORES"}:
        stretch = float(params.get("anisotropy", 16 if key == "DIRECTIONAL" else 48))
        out = _fbm(x * 4, y / max(stretch, 1), seed, 3)
        if key == "BRUSHED_METAL_LINES":
            out = 0.7 * out + 0.3 * _scratch(x * 2, y * 0.15, seed + 4, width, density, True)
        elif key == "WOOD_PORES":
            out = _smooth(0.53, 0.68, out)
        elif key == "FIBERS":
            out = _smooth(0.3, 0.7, out)
    elif key == "WARPED":
        strength = float(params.get("warp", 2))
        out = _fbm(x + strength * _perlin(x * 0.6, y * 0.6, seed + 21),
                   y + strength * _perlin(x * 0.6, y * 0.6, seed + 73), seed, octaves)
    elif key in {"SCRATCHES", "DIRECTIONAL_SCRATCHES", "GRUNGE_SCRATCHES"}:
        out = _scratch(x, y, seed, width, density, key == "DIRECTIONAL_SCRATCHES")
    elif key in {"CRACKS", "GRUNGE_CRACKS", "LEATHER_GRAIN", "RUST_FLAKES"}:
        warp = float(params.get("warp", 0.4))
        a, b, identity = _cells(x + warp * _perlin(x * 1.7, y * 1.7, seed + 9),
                                y + warp * _perlin(x * 1.7, y * 1.7, seed + 13), seed)
        cracks = 1 - _smooth(width * 0.2, width, b - a)
        if key == "LEATHER_GRAIN":
            out = (1 - cracks) * 0.75 + _fbm(x * 9, y * 9, seed + 31, 2) * 0.25
        elif key == "RUST_FLAKES":
            out = (1 - cracks) * (0.3 + identity * 0.7)
        else:
            out = cracks
    elif key == "WOOD_GRAIN":
        bend = _fbm(x * 0.6, y * 0.08, seed, 3)
        ring = 0.5 + 0.5 * np.sin((x + bend * 4) * np.float32(2 * np.pi))
        out = 0.65 * ring ** 0.6 + 0.35 * _fbm(x * 6, y * 0.16, seed + 31, 3)
    elif key == "FABRIC_WEAVE":
        fx, fy = x % 1, y % 1
        alternate = (np.floor(x) + np.floor(y)) % 2
        warp = np.sin(fx * np.pi) ** 0.45
        weft = np.sin(fy * np.pi) ** 0.45
        out = np.where(alternate == 0, warp * (0.8 + 0.2 * weft), weft * (0.8 + 0.2 * warp))
        out *= 0.85 + _value(x * 24, y * 24, seed) * 0.15
    elif key == "MACHINING_LINES":
        radius = np.sqrt((u - 0.5) ** 2 + (v - 0.5) ** 2) * scale
        out = (0.5 + 0.5 * np.sin(radius * 2 * np.pi)) * 0.8 + _fbm(x * 4, y, seed, 2) * 0.2
    elif key in {"CONCRETE_PORES", "RUST_PITTING", "CASTING_NOISE", "DUST_SPECKLES", "GRUNGE_DUST"}:
        radius = float(params.get("radius", 0.12 if "DUST" in key else 0.3))
        out = _pores(x, y, seed, radius, density)
        if key == "CASTING_NOISE":
            out = out * 0.65 + _fbm(x * 2, y * 2, seed + 71, 3) * 0.35
        elif key == "RUST_PITTING":
            out *= _smooth(0.25, 0.7, _fbm(x * 0.4, y * 0.4, seed + 71, 3))
    elif key in {"CONCRETE_AGGREGATE", "STONE_GRAIN", "SAND_GRAIN"}:
        _, _, coarse = _cells(x, y, seed)
        fine = _value(x * 4, y * 4, seed + 17)
        out = coarse * 0.7 + fine * 0.3
        if key == "STONE_GRAIN":
            out = np.where(coarse > 0.6, out, out * 0.25)
        elif key == "SAND_GRAIN":
            out = np.sqrt(out)
    elif key in {"METAL_GRAIN", "PLASTIC_GRAIN", "RUBBER_GRAIN"}:
        out = _fbm(x * 3, y * 3, seed, 3)
        if key == "METAL_GRAIN":
            out = 0.65 * out + 0.35 * _value(x * 12, y * 12, seed + 8)
        elif key == "RUBBER_GRAIN":
            out = _smooth(0.25, 0.75, out)
    elif key in {"GRUNGE_FINE", "GRUNGE_MEDIUM", "GRUNGE_HEAVY", "GRUNGE_DIRT", "GRUNGE_SPOTS", "OXIDATION_SPOTS"}:
        factor = 3 if key == "GRUNGE_FINE" else 0.4 if key == "GRUNGE_HEAVY" else 1
        out = _fbm(x * factor, y * factor, seed, 5)
        if key in {"GRUNGE_SPOTS", "OXIDATION_SPOTS"}:
            out = _smooth(0.48, 0.62, out)
        elif key == "GRUNGE_DIRT":
            out = _smooth(0.3, 0.7, out) * _fbm(x * 4, y * 4, seed + 15, 2)
        elif key == "GRUNGE_HEAVY":
            out = _smooth(0.35, 0.65, out)
    elif key in {"GRUNGE_STREAKS", "GRUNGE_LEAKS"}:
        out = _fbm(x * 2, y * 0.08, seed, 3)
        out = _smooth(0.4, 0.65, out)
        out *= _smooth(0.22, 0.65, _fbm(x * 0.5, y * 0.35, seed + 11, 3))
        if key == "GRUNGE_LEAKS":
            out *= (1 - (y * 0.15 + _value(x, y * 0, seed + 8) * 0.8) % 1) ** 0.6
    elif key == "FINGERPRINTS":
        cx, cy = np.floor(x), np.floor(y)
        dx = (x % 1 - 0.5) * 1.4
        dy = (y % 1 - 0.5) * 0.8
        radius = np.sqrt((dx + 0.12 * np.sin(dy * 9)) ** 2 + dy ** 2)
        ridges = _smooth(0.0, 0.55, np.sin(radius * 170 + _perlin(x, y, seed) * 3))
        out = ridges * (1 - _smooth(0.27, 0.43, radius)) * (_hash(cx, cy, seed) < density)
    elif key == "SWIRLS":
        ix, iy = np.floor(x), np.floor(y)
        out = np.zeros_like(x)
        for dx in range(-1, 2):
            for dy in range(-1, 2):
                cx, cy = ix + dx, iy + dy
                px = x - cx - _hash(cx, cy, seed)
                py = y - cy - _hash(cx, cy, seed + 7)
                radius = np.sqrt(px * px + py * py)
                bands = (radius * 18 + _hash(cx, cy, seed + 11)) % 1
                ring = 1 - _smooth(0.1, 0.22, np.minimum(bands, 1 - bands))
                breakup = _smooth(0.45, 0.7, _value(px * 3, py * 3, seed + 19))
                ring *= (1 - _smooth(0.5, 0.85, radius)) * breakup
                out = np.maximum(out, ring)
    elif key == "SMUDGES":
        out = _smooth(0.4, 0.68, _fbm(x * 0.6, y * 1.8, seed, 4))
        out *= 0.75 + 0.25 * _fbm(x * 0.2, y * 5, seed + 18, 3)
    elif key == "VEINS":
        warped = x + 3.5 * _fbm(x * 0.5, y * 0.5, seed, 5)
        veins = np.abs(np.sin((warped + y * 0.3) * np.pi))
        out = (1 - _smooth(width, width * 4, veins))
        out = np.maximum(out, (1 - _smooth(0.0, width * 0.8,
                             np.abs(_perlin(x * 2 + warped, y * 2, seed + 72)))) * 0.3)
    else:
        fx, fy = x % 1, y % 1
        bx, by = np.minimum(fx, 1 - fx), np.minimum(fy, 1 - fy)
        if key == "CHECKER":
            out = (np.floor(x) + np.floor(y)) % 2
        elif key == "STRIPES":
            out = (fx < np.clip(params.get("duty", 0.5), 0, 1)).astype(np.float32)
        elif key in {"DOTS", "PATTERN"}:
            out = 1 - _smooth(0.3 - width, 0.3, np.sqrt((fx - 0.5) ** 2 + (fy - 0.5) ** 2))
        elif key == "GRID":
            out = 1 - _smooth(width * 0.5, width, np.minimum(bx, by))
        elif key in {"TILE_PATTERN", "BRICK_PATTERN"}:
            if key == "BRICK_PATTERN":
                fx = (x + (np.floor(y) % 2) * 0.5) % 1
                bx = np.minimum(fx, 1 - fx)
            out = _smooth(width * 0.5, width, np.minimum(bx, by))
        elif key == "HEX_PATTERN":
            # Nearest center on two staggered triangular sublattices.
            xx, yy = x, y / np.sqrt(3)
            ax, ay = (xx + 0.5) % 1 - 0.5, ((yy + 0.5) % 1 - 0.5) * np.sqrt(3)
            bx, by = xx % 1 - 0.5, (yy % 1 - 0.5) * np.sqrt(3)
            choose_a = ax * ax + ay * ay < bx * bx + by * by
            dx, dy = np.where(choose_a, ax, bx), np.where(choose_a, ay, by)
            edge = 0.5 - np.maximum(np.abs(dx), np.abs(dx) * 0.5 + np.abs(dy) * np.sqrt(3) * 0.5)
            out = _smooth(width * 0.5, width, edge)
        else:
            raise AssertionError(f"Unimplemented source: {key}")
    return np.clip(np.nan_to_num(out, nan=0, posinf=1, neginf=0), 0, 1).astype(np.float32)
