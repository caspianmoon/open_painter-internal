"""Prototype builders for Material → Geometry: bolts, stitches, scales, rocks, bark.

Same contract as ``prototypes``: ``build(seed, variant) -> (vertices, faces,
smooth, rim)`` in a unit frame (about 1 unit across), bounded triangle counts.
"""
import math

import numpy as np


def _rng(seed, salt):
    return np.random.default_rng((int(seed) * 1000003 + salt * 7919) & 0x7fffffff)


def _lathe(profile, segments, close_top=True, close_bottom=True):
    """Revolve an (r, z) profile around Z into vertices/faces."""
    profile = np.asarray(profile, float)
    angles = np.linspace(0, math.tau, segments, endpoint=False)
    vertices, faces = [], []
    rings = len(profile)
    for r, z in profile:
        for a in angles:
            vertices.append((r * math.cos(a), r * math.sin(a), z))
    for ring in range(rings - 1):
        for i in range(segments):
            j = (i + 1) % segments
            a, b = ring * segments + i, ring * segments + j
            c, d = (ring + 1) * segments + j, (ring + 1) * segments + i
            faces.append((a, b, c, d))
    if close_bottom:
        centre = len(vertices); vertices.append((0, 0, profile[0][1]))
        for i in range(segments):
            faces.append((centre, (i + 1) % segments, i))
    if close_top:
        centre = len(vertices); vertices.append((0, 0, profile[-1][1]))
        base = (rings - 1) * segments
        for i in range(segments):
            faces.append((centre, base + i, base + (i + 1) % segments))
    return np.asarray(vertices), faces


def bolt(seed, variant):
    """Hex or round head on a thin washer, slightly chamfered; ~60 triangles."""
    rng = _rng(seed, 501 + variant)
    hexagonal = variant % 3 != 2
    segments = 6 if hexagonal else 16
    head_r = rng.uniform(.32, .4)
    head_h = rng.uniform(.18, .3)
    washer_r = rng.uniform(.45, .5)
    chamfer = head_r * rng.uniform(.08, .16)
    profile = [(washer_r, 0.0), (washer_r, .05), (head_r, .05), (head_r, head_h - chamfer), (head_r - chamfer, head_h)]
    vertices, faces = _lathe(profile, segments)
    if hexagonal:
        # A hexagonal head keeps flat sides; rotate variants so instances differ.
        angle = rng.uniform(0, math.tau / 6)
        c, s = math.cos(angle), math.sin(angle)
        vertices = np.column_stack((vertices[:, 0] * c - vertices[:, 1] * s, vertices[:, 0] * s + vertices[:, 1] * c, vertices[:, 2]))
    return vertices, faces, not hexagonal, None


def stitch(seed, variant):
    """A thread arc: a 6-sided tube bent over the surface, ends sunk in; ~70 triangles."""
    rng = _rng(seed, 601 + variant)
    length = rng.uniform(.8, 1.0)
    rise = rng.uniform(.14, .22)
    radius = rng.uniform(.06, .09)
    steps, sides = 7, 6
    t = np.linspace(0, 1, steps)
    centre = np.column_stack((t * length - length / 2, np.zeros(steps), np.sin(t * math.pi) * rise - .04))
    tangent = np.gradient(centre, axis=0)
    tangent /= np.linalg.norm(tangent, axis=1, keepdims=True)
    vertices, faces = [], []
    for k in range(steps):
        n = np.cross(tangent[k], (0, 1, 0)); n /= max(np.linalg.norm(n), 1e-8)
        b = np.cross(tangent[k], n)
        twist = rng.uniform(0, math.tau / sides)
        for s in range(sides):
            a = twist + s * math.tau / sides
            vertices.append(centre[k] + radius * (math.cos(a) * n + math.sin(a) * b))
    for k in range(steps - 1):
        for s in range(sides):
            i, j = k * sides + s, k * sides + (s + 1) % sides
            faces.append((i, j, j + sides, i + sides))
    return np.asarray(vertices), faces, True, None


def scale(seed, variant):
    """A curved rounded plate overlapping downward (align follows local -Z); ~48 triangles."""
    rng = _rng(seed, 701 + variant)
    across, down = 9, 5
    width, height = rng.uniform(.9, 1.1), rng.uniform(.9, 1.2)
    dome = rng.uniform(.08, .16)
    vertices = []
    for j in range(down):
        v = j / (down - 1)
        for i in range(across):
            u = i / (across - 1) * 2 - 1
            taper = 1 - .45 * v * v
            x = u * width / 2 * taper
            y = (v - .3) * height
            z = dome * (1 - u * u) * (1 - v) + .02
            vertices.append((x, y, z))
    faces = []
    for j in range(down - 1):
        for i in range(across - 1):
            a = j * across + i
            faces.append((a, a + 1, a + across + 1, a + across))
    # Underside skin so the plate has thickness.
    base = len(vertices)
    vertices += [(x, y, 0.0) for x, y, _ in vertices]
    for j in range(down - 1):
        for i in range(across - 1):
            a = base + j * across + i
            faces.append((a, a + across, a + across + 1, a + 1))
    return np.asarray(vertices), faces, True, None


def rock(seed, variant):
    """Angular displaced icosphere with a flat base; ~80 triangles."""
    rng = _rng(seed, 801 + variant)
    t = (1 + 5 ** .5) / 2
    base = np.array([(-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0), (0, -1, t), (0, 1, t), (0, -1, -t), (0, 1, -t),
                     (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1)], float)
    base /= np.linalg.norm(base, axis=1, keepdims=True)
    faces = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6),
             (7, 1, 8), (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9), (4, 9, 5), (2, 4, 11), (6, 2, 10),
             (8, 6, 7), (9, 8, 1)]
    # One subdivision for silhouette variety.
    vertices = [tuple(v) for v in base]
    cache = {}

    def midpoint(a, b):
        key = (min(a, b), max(a, b))
        if key not in cache:
            m = (np.asarray(vertices[a]) + np.asarray(vertices[b])) / 2
            m /= np.linalg.norm(m)
            cache[key] = len(vertices); vertices.append(tuple(m))
        return cache[key]
    refined = []
    for a, b, c in faces:
        ab, bc, ca = midpoint(a, b), midpoint(b, c), midpoint(c, a)
        refined += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
    points = np.asarray(vertices)
    bumps = 1 + rng.uniform(-.28, .28, len(points))
    stretch = np.array((rng.uniform(.8, 1.3), rng.uniform(.7, 1.1), rng.uniform(.5, .8)))
    points = points * bumps[:, None] * stretch
    points[:, 2] = np.maximum(points[:, 2], -.05) * .5 + .02   # flat-ish base sitting on the surface
    return points * .5, refined, False, None


def bark(seed, variant):
    """Wide ridged plate for bark (align along local Z with jitter); ~64 triangles."""
    rng = _rng(seed, 901 + variant)
    across, down = 5, 9
    ridges = rng.uniform(.06, .12)
    vertices = []
    for j in range(down):
        v = j / (down - 1)
        for i in range(across):
            u = i / (across - 1)
            x = (u - .5) * rng.uniform(.5, .7)
            y = (v - .5) * 1.0
            z = ridges * (1 + math.sin(u * math.pi * 2 + rng.uniform(0, 1)) * .5) * (1 - abs(v - .5) * 1.4) + .02
            vertices.append((x, y, max(z, .01)))
    faces = []
    for j in range(down - 1):
        for i in range(across - 1):
            a = j * across + i
            faces.append((a, a + 1, a + across + 1, a + across))
    return np.asarray(vertices), faces, False, None


BUILDERS = {'BOLT': bolt, 'STITCH': stitch, 'SCALE': scale, 'ROCK': rock, 'BARK': bark}
VARIANTS = {'BOLT': 3, 'STITCH': 3, 'SCALE': 4, 'ROCK': 5, 'BARK': 4}
