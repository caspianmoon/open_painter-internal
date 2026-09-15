"""Deterministic prototype meshes for instanced geometry effects.

Every kind yields several variants so instances do not repeat one silhouette.
Coordinates are in a unit frame: +Z is away from the surface, the base sits on
z = 0 (or slightly below so instances embed rather than float) and sizes are
scaled by the effect's Size. Rust flakes, snow, splinters and peel fragments
are procedural; moss clumps come from the CC0 Poly Haven ``moss_01`` scan
(``assets/geometry/moss_01/provenance.json``).
"""
import json
import math
from pathlib import Path

import numpy as np

ASSETS = Path(__file__).resolve().parents[1] / "assets" / "geometry"


def _rng(seed, salt):
    return np.random.default_rng((int(seed) * 1000003 + salt * 7919) & 0x7fffffff)


def _outline(rng, count, jag):
    """Jagged closed outline radii for evenly spaced angles."""
    angles = np.linspace(0, math.tau, count, endpoint=False)
    radius = np.ones(count)
    for harmonic in (2, 3, 5, 7):
        radius += jag * rng.uniform(.15, .4) / (harmonic ** .5) * np.sin(harmonic * angles + rng.uniform(0, math.tau))
    radius += jag * .25 * rng.uniform(-1, 1, count)
    radius = np.clip(radius, .45, 1.6)
    return np.stack((np.cos(angles) * radius, np.sin(angles) * radius), 1)


def rust_flake(seed, variant):
    """Papery, crumbly-edged oxide plate, curling up and lifting on one side.

    Returns a ``rim`` float attribute (0 centre, 1 edge) so the material can
    lighten and dust the broken edges the way real scale does.
    """
    rng = _rng(seed, 11 + variant)
    count = int(rng.integers(18, 28))
    ring = _outline(rng, count, .5) * (1.0, rng.uniform(.6, 1.0))
    # Bites out of the outline: a few concave notches where scale crumbled.
    for _ in range(int(rng.integers(1, 4))):
        centre = rng.integers(count)
        width = int(rng.integers(1, 3))
        for offset in range(-width, width + 1):
            ring[(centre + offset) % count] *= 1 - rng.uniform(.15, .4) * (1 - abs(offset) / (width + 1))
    thickness = rng.uniform(.015, .045)
    curl = rng.uniform(.06, .3)
    tilt = rng.uniform(0, math.tau)
    lift = rng.uniform(.05, .3)
    x, y = ring[:, 0], ring[:, 1]
    edge_z = curl * (x ** 2 + y ** 2) + lift * np.clip(x * math.cos(tilt) + y * math.sin(tilt), 0, None) ** 1.5
    inner = ring * rng.uniform(.45, .6)
    inner_z = curl * (inner[:, 0] ** 2 + inner[:, 1] ** 2) * .5 + rng.uniform(-.01, .01, count)
    n = count
    top = np.column_stack((x, y, edge_z + thickness))
    bottom = np.column_stack((x, y, edge_z))
    mid_top = np.column_stack((inner[:, 0], inner[:, 1], inner_z + thickness * rng.uniform(1.0, 1.8)))
    mid_bottom = np.column_stack((inner[:, 0], inner[:, 1], inner_z))
    centre_top = np.array([0, 0, thickness * 1.3])
    centre_bottom = np.array([0, 0, 0.0])
    vertices = np.vstack((top, bottom, mid_top, mid_bottom, centre_top[None], centre_bottom[None]))
    rim = np.concatenate((np.ones(2 * n), np.full(2 * n, .35), [0, 0]))
    faces = []
    for i in range(n):
        j = (i + 1) % n
        faces.append((i, j, 2 * n + j, 2 * n + i))                          # top outer ring
        faces.append((2 * n + i, 2 * n + j, 4 * n))                          # top inner fan
        faces.append((n + j, n + i, 3 * n + i, 3 * n + j))                   # bottom outer ring
        faces.append((3 * n + j, 3 * n + i, 4 * n + 1))                      # bottom inner fan
        faces.append((i, n + i, n + j, j))                                   # side
    return vertices * .5, faces, False, rim


def snow_clump(seed, variant):
    """Soft rounded mound: displaced icosphere, squashed, sunk into the surface."""
    rng = _rng(seed, 101 + variant)
    t = (1 + 5 ** .5) / 2
    verts = np.array([(-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0), (0, -1, t), (0, 1, t), (0, -1, -t), (0, 1, -t),
                      (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1)], float)
    faces = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8),
             (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9), (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1)]
    verts /= np.linalg.norm(verts, axis=1, keepdims=True)
    for _ in range(2):  # 20 -> 80 -> 320 faces
        cache, new_faces, verts = {}, [], list(map(tuple, verts))
        def midpoint(a, b):
            key = (min(a, b), max(a, b))
            if key not in cache:
                m = (np.array(verts[a]) + np.array(verts[b])) / 2
                verts.append(tuple(m / np.linalg.norm(m)))
                cache[key] = len(verts) - 1
            return cache[key]
        for a, b, c in faces:
            ab, bc, ca = midpoint(a, b), midpoint(b, c), midpoint(c, a)
            new_faces += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
        faces, verts = new_faces, np.array(verts)
    phases = rng.uniform(0, math.tau, 5)
    bumps = np.zeros(len(verts))
    for k, (frequency, amplitude) in enumerate(((1.4, .22), (2.6, .12), (4.5, .06), (9.0, .03), (17.0, .015))):
        bumps += amplitude * np.sin(verts @ (rng.normal(size=3) * frequency) + phases[k])
    verts = verts * (1 + bumps)[:, None] * (rng.uniform(.75, 1.3), rng.uniform(.75, 1.3), rng.uniform(.24, .36))
    verts[:, 2] = np.maximum(verts[:, 2], -.05)
    return verts * .5, faces, True, None


def splinter(seed, variant):
    """Long wood shard: straight fibrous sides, one splintered break, slight lift."""
    rng = _rng(seed, 211 + variant)
    segments = 11
    width, thick = rng.uniform(.09, .2), rng.uniform(.045, .09)
    bend = rng.uniform(-.05, .05)
    break_end = rng.choice((-1, 1))
    profile = np.array([(1, 0), (.7, .8), (-.7, .8), (-1, 0), (-.7, -.6), (.7, -.6)], float) * (width / 2, thick / 2)
    ys = np.linspace(-.5, .5, segments)
    verts = []
    for y in ys:
        along = y * break_end                                # +0.5 at the broken end
        if along > .15:
            taper = max(.05, 1 - ((along - .15) / .35) ** rng.uniform(1.2, 2.5))
        else:
            taper = 1 - .35 * max(0.0, -along - .3) / .2     # gentle point at the fibre end
        jag = 1 + .18 * rng.uniform(-1, 1) if along > .2 else 1
        ring = profile * (taper * jag, taper)
        verts += [(px + bend * (y ** 2 - .25) * 4, y, pz + thick / 2 * taper + .001) for px, pz in ring]
    verts = np.array(verts)
    faces = []
    m = len(profile)
    for r in range(segments - 1):
        for i in range(m):
            j = (i + 1) % m
            faces.append((r * m + i, r * m + j, (r + 1) * m + j, (r + 1) * m + i))
    faces.append(tuple(range(m - 1, -1, -1)))
    faces.append(tuple((segments - 1) * m + i for i in range(m)))
    return verts, faces, False, None


def peel_fragment(seed, variant):
    """Cracked paint chip attached along its base edge, rolling up like dry paint."""
    rng = _rng(seed, 307 + variant)
    across, along = 9, 13
    xs, ys = np.linspace(-.5, .5, across), np.linspace(0, 1, along)
    roll = rng.uniform(1.6, 3.6)          # total curl angle in radians
    radius = rng.uniform(.12, .26)        # curl radius relative to the chip length
    twist = rng.uniform(-.35, .35)
    outline = _outline(rng, 24, .45)
    verts, faces = [], []
    for j, y in enumerate(ys):
        theta = roll * y
        lift = radius * (1 - math.cos(theta))
        forward = radius * math.sin(theta)
        for i, x in enumerate(xs):
            angle = math.atan2(y - .35, x) % math.tau
            border = np.interp(angle, np.linspace(0, math.tau, 24, endpoint=False), np.linalg.norm(outline, axis=1), period=math.tau)
            edge = max(abs(x) * 2, y) ** 3
            shrink = 1 - .35 * (1.6 - border) * edge
            crack = 1 - .12 * rng.random() * edge
            verts.append((x * shrink * crack * (1 - .2 * y), forward * shrink, lift + twist * x * y))
            if i and j:
                a = j * across + i
                faces.append((a - across - 1, a - across, a, a - 1))
    return np.array(verts), faces, True, None


def moss_variants(seed=1):
    """Clusters of Poly Haven moss_01 clump cards (CC0): vertices, faces, UVs.

    Each variant unites four to six cards with jittered placement so instances
    read as a cushion rather than a single tuft; the unit frame is about 10 cm.
    """
    data = json.loads((ASSETS / "moss_01" / "clumps.json").read_text())
    cards = []
    for entry in data["variants"]:
        verts = np.asarray(entry["vertices"], float)
        verts[:, 2] -= entry["origin_offset"]
        cards.append((verts / .1, entry["faces"], entry["uvs"]))   # 10 cm cluster frame
    rng = _rng(seed, 401)
    result = []
    for variant in range(8):
        picks = rng.choice(len(cards), int(rng.integers(10, 15)), replace=True)
        vertices, faces, uvs = [], [], []
        for k, pick in enumerate(picks):
            verts, card_faces, card_uvs = cards[pick]
            angle = rng.uniform(0, math.tau)
            c, s_ = math.cos(angle), math.sin(angle)
            offset = np.array((rng.uniform(-.42, .42), rng.uniform(-.42, .42), 0)) if k else np.zeros(3)
            scale = rng.uniform(.85, 1.3)
            placed = verts * scale
            placed = np.column_stack((placed[:, 0] * c - placed[:, 1] * s_, placed[:, 0] * s_ + placed[:, 1] * c, placed[:, 2])) + offset
            base = len(vertices)
            vertices.extend(placed.tolist())
            faces.extend([[base + v for v in face] for face in card_faces])
            uvs.extend(card_uvs)
        result.append((np.asarray(vertices), faces, uvs, f"moss cluster {variant + 1}"))
    return result


from . import prototypes_detail as detail

BUILDERS = {"RUST": rust_flake, "SNOW": snow_clump, "SPLINTERS": splinter, "PEEL": peel_fragment, **detail.BUILDERS}
VARIANTS = {"RUST": 6, "SNOW": 4, "SPLINTERS": 5, "PEEL": 4, **detail.VARIANTS}


def variants(kind, seed):
    """Return [(vertices, faces, uvs or None, smooth, name, rim or None)] for a kind."""
    if kind == "MOSS":
        return [(v, f, uv, False, name, None) for v, f, uv, name in moss_variants(seed)]
    build = BUILDERS[kind]
    result = []
    for index in range(VARIANTS[kind]):
        verts, faces, smooth, rim = build(seed, index)
        result.append((np.asarray(verts, float), faces, None, smooth, f"{kind.title()} {index + 1}", rim))
    return result
