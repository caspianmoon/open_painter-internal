"""The procedural catalog (``texture_engine.procedurals``) as GLSL for the engine.

Every source is a port of its NumPy definition: the same 32-bit integer lattice
hash (reproduced bit for bit with unsigned arithmetic), the same float32 operation
order, the same loops and constants. Only transcendental functions (sin, cos,
pow, sqrt) may differ from NumPy in their last bits, so results match the CPU
reference to float precision rather than exactly. ``library(kinds)`` returns the
functions a program needs, dependencies first; ``call(kind)`` names the entry.

Parameters are the catalog defaults (``evaluate`` without keyword parameters):
that is how layers, masks and generator fields call it.
"""

# name -> (dependencies, source)
SNIPPETS = {}


def _snippet(name, depends, source):
    SNIPPETS[name] = (tuple(depends), source)


_snippet('hash', (), r'''
float op_hash(float x, float y, uint seed) {
  uint a = uint(int(x));
  uint b = uint(int(y));
  uint h = a * 374761393u + b * 668265263u + seed * 2246822519u;
  h = (h ^ (h >> 13u)) * 1274126177u;
  h ^= h >> 16u;
  return float(h) * (1.0 / 4294967296.0);
}
''')
_snippet('fade', (), r'''
float op_fade(float t) {
  float inner = (t * 6.0 - 15.0) * t + 10.0;
  float cubic = t * t * t;
  return cubic * inner;
}
float op_lerp(float a, float b, float t) { return (b - a) * t + a; }
float op_smooth(float a, float b, float x) {
  float t = clamp((x - a) / max(b - a, 1e-6), 0.0, 1.0);
  return t * t * (3.0 - 2.0 * t);
}
float op_frac1(float x) { return x - floor(x); }
''')
_snippet('value', ('hash', 'fade'), r'''
float op_value(float x, float y, uint seed) {
  float ix = floor(x), iy = floor(y);
  float tx = op_fade(x - ix), ty = op_fade(y - iy);
  return op_lerp(op_lerp(op_hash(ix, iy, seed), op_hash(ix + 1.0, iy, seed), tx),
                 op_lerp(op_hash(ix, iy + 1.0, seed), op_hash(ix + 1.0, iy + 1.0, seed), tx), ty);
}
''')
_snippet('perlin', ('hash', 'fade'), r'''
float op_gradient(float ix, float iy, float dx, float dy, uint seed) {
  float angle = op_hash(ix, iy, seed) * 6.2831854820251465;
  return cos(angle) * dx + sin(angle) * dy;
}
float op_perlin(float x, float y, uint seed) {
  float ix = floor(x), iy = floor(y);
  float dx = x - ix, dy = y - iy;
  float ix1 = ix + 1.0, iy1 = iy + 1.0;
  float fx = op_fade(dx), fy = op_fade(dy);
  return op_lerp(op_lerp(op_gradient(ix, iy, dx, dy, seed), op_gradient(ix1, iy, dx - 1.0, dy, seed), fx),
                 op_lerp(op_gradient(ix, iy1, dx, dy - 1.0, seed), op_gradient(ix1, iy1, dx - 1.0, dy - 1.0, seed), fx),
                 fy);
}
''')
_snippet('simplex', ('perlin',), r'''
float op_simplex(float x, float y, uint seed) {
  const float skew = 0.3660254037844386, unskew = 0.21132486540518713;
  float s = (x + y) * skew;
  float ix = floor(x + s), iy = floor(y + s);
  float t = (ix + iy) * unskew;
  float dx = x - ix + t, dy = y - iy + t;
  float ox = (dx > dy) ? 1.0 : 0.0;
  float oy = 1.0 - ox;
  float result = 0.0;
  float cx[3] = float[3](ix, ix + ox, ix + 1.0);
  float cy[3] = float[3](iy, iy + oy, iy + 1.0);
  float px[3] = float[3](dx, dx - ox + unskew, dx - 1.0 + 2.0 * unskew);
  float py[3] = float[3](dy, dy - oy + unskew, dy - 1.0 + 2.0 * unskew);
  for (int i = 0; i < 3; i++) {
    float attenuation = max(0.5 - px[i] * px[i] - py[i] * py[i], 0.0);
    float a2 = attenuation * attenuation;
    result += a2 * a2 * op_gradient(cx[i], cy[i], px[i], py[i], seed);
  }
  return result * 70.0;
}
''')
_snippet('fbm', ('perlin',), r'''
/* mode 0 FBM, 1 RIDGED, 2 TURBULENCE; persistence 0.5 (the catalog default). */
float op_fbm_mode(float x, float y, uint seed, int octaves, int mode) {
  float result = 0.0, total = 0.0, amplitude = 1.0;
  int count = min(max(octaves, 1), 8);
  for (int i = 0; i < count; i++) {
    float n = op_perlin(x, y, seed + uint(i) * 53u);
    if (mode == 1) { float r = 1.0 - abs(n) * 1.8; n = r * r; }
    else if (mode == 2) { n = abs(n) * 1.8; }
    else { n = 0.5 + n * 0.8; }
    result += amplitude * n;
    total += amplitude;
    amplitude *= 0.5;
    x = x * 2.03 + 13.1;
    y = y * 2.03 - 7.7;
  }
  return result / total;
}
float op_fbm(float x, float y, uint seed, int octaves) { return op_fbm_mode(x, y, seed, octaves, 0); }
''')
_snippet('cells', ('hash',), r'''
/* Nearest and second distances and the winner's cell value (5x5 search, strict ordering). */
vec3 op_cells(float x, float y, uint seed) {
  float ix = floor(x), iy = floor(y);
  float first = 1.0 / 0.0, second = 1.0 / 0.0;
  float wx = 0.0, wy = 0.0;
  for (int dy = -2; dy <= 2; dy++) {
    float cy = iy + float(dy);
    for (int dx = -2; dx <= 2; dx++) {
      float cx = ix + float(dx);
      float px = x - (op_hash(cx, cy, seed) + cx);
      px = px * px;
      float py = y - (op_hash(cx, cy, seed + 79u) + cy);
      py = py * py;
      float dist = px + py;
      bool closer = dist < first;
      second = closer ? first : min(second, dist);
      if (closer) { wx = cx; wy = cy; }
      first = min(first, dist);
    }
  }
  return vec3(sqrt(first), sqrt(second), op_hash(wx, wy, seed + 173u));
}
''')
_snippet('scratch', ('hash', 'fade'), r'''
float op_scratch(float x, float y, uint seed, float width, float density, bool directional) {
  float ix = floor(x), iy = floor(y);
  float result = 0.0;
  for (int dy = -1; dy <= 1; dy++) {
    float cy = iy + float(dy);
    for (int dx = -1; dx <= 1; dx++) {
      float cx = ix + float(dx);
      float px = x - cx - op_hash(cx, cy, seed);
      float py = y - cy - op_hash(cx, cy, seed + 21u);
      float angle = (op_hash(cx, cy, seed + 51u) - 0.5) * (directional ? 0.16 : 3.141592653589793);
      float s = sin(angle), c = cos(angle);
      float along = px * s + py * c;
      float across = px * c - py * s;
      float half_length = 0.12 + op_hash(cx, cy, seed + 62u) * 0.55;
      float over = max(abs(along) - half_length, 0.0);
      float dist = sqrt(across * across + over * over);
      float line = 1.0 - op_smooth(width * 0.25, width, dist);
      result = max(result, line * ((op_hash(cx, cy, seed + 9u) < density) ? 1.0 : 0.0));
    }
  }
  return result;
}
''')
_snippet('pores', ('cells', 'fade'), r'''
float op_pores(float x, float y, uint seed, float radius, float density) {
  vec3 c = op_cells(x, y, seed);
  return (1.0 - op_smooth(radius * 0.3, radius, c.x)) * ((c.z < density) ? 1.0 : 0.0);
}
''')
_snippet('trace', ('fade',), r'''
float op_tapered_trace(float along, float across, float half_length, float width, float bend) {
  float t = along / max(half_length, 1e-5);
  float pressure = max(1.0 - t * t, 0.0);
  float center = bend * (t * t - 0.35) * half_length;
  float relative = abs(across - center) / max(width * pressure, 1e-6);
  float line = 1.0 - op_smooth(0.15, 1.0, relative);
  return line * sqrt(pressure);
}
''')
_snippet('tapered_scratches', ('hash', 'trace'), r'''
float op_tapered_scratches(float x, float y, uint seed, float width, float density) {
  float ix = floor(x), iy = floor(y);
  float result = 0.0;
  for (int dy = -1; dy <= 1; dy++) {
    for (int dx = -1; dx <= 1; dx++) {
      float cx = ix + float(dx), cy = iy + float(dy);
      float px = x - cx - op_hash(cx, cy, seed);
      float py = y - cy - op_hash(cx, cy, seed + 21u);
      float angle = (op_hash(cx, cy, seed + 51u) - 0.5) * 3.1415927410125732;
      float along = px * sin(angle) + py * cos(angle);
      float across = px * cos(angle) - py * sin(angle);
      float h62 = op_hash(cx, cy, seed + 62u);
      float half_length = 0.10 + h62 * h62 * 0.55;
      float local_width = width * (0.25 + op_hash(cx, cy, seed + 73u) * 0.75);
      float bend = (op_hash(cx, cy, seed + 87u) - 0.5) * 0.17;
      float trace = op_tapered_trace(along, across, half_length, local_width, bend);
      trace *= 0.35 + op_hash(cx, cy, seed + 99u) * 0.65;
      result = max(result, trace * ((op_hash(cx, cy, seed + 9u) < density) ? 1.0 : 0.0));
    }
  }
  return result;
}
''')
_snippet('fine_fissures', ('hash', 'trace'), r'''
float op_fine_fissures(float x, float y, uint seed, float width, float density) {
  float ix = floor(x), iy = floor(y);
  float result = 0.0;
  for (int dy = -1; dy <= 1; dy++) {
    for (int dx = -1; dx <= 1; dx++) {
      float cx = ix + float(dx), cy = iy + float(dy);
      float px = x - cx - op_hash(cx, cy, seed);
      float py = y - cy - op_hash(cx, cy, seed + 21u);
      float angle = op_hash(cx, cy, seed + 51u) * 3.1415927410125732;
      float along = px * sin(angle) + py * cos(angle);
      float across = px * cos(angle) - py * sin(angle);
      float half_length = 0.20 + op_hash(cx, cy, seed + 62u) * 0.36;
      float local_width = width * (0.45 + op_hash(cx, cy, seed + 73u) * 0.55);
      float bend = (op_hash(cx, cy, seed + 87u) - 0.5) * 0.50;
      float fissure = op_tapered_trace(along, across, half_length, local_width, bend);
      for (int k = 0; k < 2; k++) {
        float side = (k == 0) ? -1.0 : 1.0;
        uint salt = (k == 0) ? 131u : 179u;
        float root = (op_hash(cx, cy, seed + salt) - 0.5) * half_length;
        float ratio = root / half_length;
        float root_across = bend * (ratio * ratio - 0.35) * half_length;
        float branch_angle = side * (0.38 + op_hash(cx, cy, seed + salt + 1u) * 0.63);
        float trail_length = half_length * (0.27 + op_hash(cx, cy, seed + salt + 2u) * 0.46);
        float qa = along - root, qb = across - root_across;
        float branch_along = qa * cos(branch_angle) + qb * sin(branch_angle);
        float branch_across = qb * cos(branch_angle) - qa * sin(branch_angle);
        float branch = op_tapered_trace(branch_along - trail_length * 0.40, branch_across, trail_length * 0.65,
                                        local_width * 0.65, bend * 0.5);
        fissure = max(fissure, branch * 0.75);
      }
      result = max(result, fissure * ((op_hash(cx, cy, seed + 9u) < density) ? 1.0 : 0.0));
    }
  }
  return result;
}
''')
_snippet('fractured_paint', ('hash', 'fbm', 'value'), r'''
float op_fractured_paint(float x, float y, uint seed, float width, float density) {
  float ix = floor(x), iy = floor(y);
  float result = 0.0;
  float breakup = (op_fbm(x * 3.3, y * 3.3, seed + 211u, 3) - 0.5) * 0.22;
  breakup += (op_value(x * 23.0, y * 23.0, seed + 257u) - 0.5) * 0.055;
  for (int dy = -1; dy <= 1; dy++) {
    for (int dx = -1; dx <= 1; dx++) {
      float cx = ix + float(dx), cy = iy + float(dy);
      float px = x - cx - op_hash(cx, cy, seed);
      float py = y - cy - op_hash(cx, cy, seed + 21u);
      float angle = op_hash(cx, cy, seed + 51u) * 3.1415927410125732;
      float qx = px * cos(angle) - py * sin(angle);
      float qy = px * sin(angle) + py * cos(angle);
      float radius = 0.08 + pow(op_hash(cx, cy, seed + 62u), 0.7) * 0.40;
      float stretch = 0.55 + op_hash(cx, cy, seed + 73u) * 0.90;
      qx = qx / stretch;
      qy = qy * stretch;
      float boundary = max(qx * (1.0 + op_hash(cx, cy, seed + 87u) * 0.65),
                           -qx * (0.7 + op_hash(cx, cy, seed + 99u) * 0.65));
      boundary = max(boundary, qy * (0.85 + op_hash(cx, cy, seed + 111u) * 0.65));
      boundary = max(boundary, -qy * (0.75 + op_hash(cx, cy, seed + 127u) * 0.55));
      boundary = max(boundary, (qx * 0.6 - qy * 0.8) * 1.15);
      boundary = max(boundary, -qx * 0.72 + qy * 0.55);
      float dist = boundary - radius + breakup;
      float chip = 1.0 - op_smooth(-width, width, dist);
      result = max(result, chip * ((op_hash(cx, cy, seed + 9u) < density) ? 1.0 : 0.0));
    }
  }
  return result;
}
''')
_snippet('weathering', ('fbm', 'pores', 'perlin', 'hash'), r'''
float op_weathering_front(float x, float y, uint seed) {
  float wx = x + 0.85 * op_perlin(x * 0.63, y * 0.63, seed + 19u);
  float wy = y + 0.85 * op_perlin(x * 0.63, y * 0.63, seed + 71u);
  float large = op_fbm(wx * 0.47, wy * 0.47, seed, 3);
  float broken = op_fbm(wx * 2.7, wy * 2.7, seed + 137u, 3);
  float fine = op_fbm(wx * 13.0, wy * 13.0, seed + 269u, 2);
  return clamp((0.72 * large + 0.22 * broken + 0.06 * fine - 0.28) / 0.44, 0.0, 1.0);
}
float op_corrosion_grain(float x, float y, uint seed) {
  float granular = op_fbm(x * 3.7, y * 3.7, seed, 3);
  float pits = op_pores(x * 1.9, y * 1.9, seed + 31u, 0.23, 0.68);
  return clamp((granular - 0.28) / 0.44 - 0.45 * pits, 0.0, 1.0);
}
float op_runoff_trails(float x, float y, uint seed) {
  float ix = floor(x), iy = floor(y);
  float result = 0.0;
  for (int dy = 0; dy < 3; dy++) {
    for (int dx = -1; dx <= 1; dx++) {
      float cx = ix + float(dx), cy = iy + float(dy);
      float origin_x = cx + op_hash(cx, cy, seed + 11u);
      float origin_y = cy + op_hash(cx, cy, seed + 29u);
      float h47 = op_hash(cx, cy, seed + 47u);
      float trail_length = 0.25 + 1.45 * h47 * h47;
      float t = (origin_y - y) / trail_length;
      float h59 = op_hash(cx, cy, seed + 59u);
      float width = 0.012 + 0.055 * h59 * h59;
      float bend = 0.065 * op_perlin(origin_x * 0.8, y * 0.7, seed + 83u);
      float dist = abs(x - origin_x - bend);
      float taper = pow(max(1.0 - clamp(t, 0.0, 1.0), 0.0), 0.65);
      float line = 1.0 - op_smooth(0.15, 1.0, dist / max(width * taper, 0.0001));
      float ends = op_smooth(0.0, 0.05, t) * (1.0 - op_smooth(0.70, 1.0, t));
      float enabled_trail = (op_hash(cx, cy, seed + 101u) < 0.46) ? 1.0 : 0.0;
      result = max(result, line * ends * enabled_trail);
    }
  }
  return result;
}
''')

# Catalog source -> (dependencies, GLSL body computing ``res`` from x, y, u, v, scale, seed).
# Defaults of ``procedurals.evaluate``: width 0.04, density 0.55, octaves 4.
KINDS = {
    'SOLID': ((), 'res = 1.0;'),
    'GRADIENT': (('fade',), 'res = op_frac1(x);'),
    'LINEAR_GRADIENT': (('fade',), 'res = op_frac1(x);'),
    'RADIAL_GRADIENT': ((), 'res = 1.0 - sqrt((u - 0.5) * (u - 0.5) + (v - 0.5) * (v - 0.5)) * scale;'),
    'PERLIN': (('perlin',), 'res = 0.5 + op_perlin(x, y, seed) * 0.8;'),
    'SIMPLEX': (('simplex',), 'res = 0.5 + op_simplex(x, y, seed) * 0.5;'),
    'VALUE': (('value',), 'res = op_value(x, y, seed);'),
    'FBM': (('fbm',), 'res = op_fbm_mode(x, y, seed, 4, 0);'),
    'FRACTAL': (('fbm',), 'res = op_fbm_mode(x, y, seed, 6, 0);'),
    'CLOUD': (('fbm',), 'res = op_fbm_mode(x * 0.5, y * 0.5, seed, 4, 0);'),
    'RIDGED': (('fbm',), 'res = op_fbm_mode(x, y, seed, 4, 1);'),
    'TURBULENCE': (('fbm',), 'res = op_fbm_mode(x, y, seed, 4, 2);'),
    'MULTI_FRACTAL': (('fbm',), '''res = 1.0;
  float p = 1.0;
  for (int i = 0; i < 4; i++) { res *= 0.7 + 0.6 * op_fbm(x * p, y * p, seed + uint(i), 1); p *= 2.0; }
  res = res * 0.5;'''),
    'VORONOI': (('cells',), 'res = op_cells(x, y, seed).z;'),
    'CELLULAR': (('cells', 'fade'), 'res = 1.0 - op_smooth(0.0, 0.85, op_cells(x, y, seed).x);'),
    'WORLEY': (('cells',), 'res = op_cells(x, y, seed).x;'),
    'HAMMERED_METAL': (('cells',), '''float a = clamp(op_cells(x, y, seed).x, 0.0, 1.0);
  res = 1.0 - sqrt(max(1.0 - a * a, 0.0));'''),
    'ORANGE_PEEL': (('cells', 'fbm', 'fade'), '''float a = op_cells(x, y, seed).x;
  res = op_smooth(0.0, 0.9, a) * 0.65 + op_fbm(x * 2.0, y * 2.0, seed + 9u, 2) * 0.35;'''),
    'DIRECTIONAL': (('fbm',), 'res = op_fbm(x * 4.0, y / 16.0, seed, 3);'),
    'ANISOTROPIC': (('fbm',), 'res = op_fbm(x * 4.0, y / 48.0, seed, 3);'),
    'FIBERS': (('fbm', 'fade'), 'res = op_smooth(0.3, 0.7, op_fbm(x * 4.0, y / 48.0, seed, 3));'),
    'BRUSHED_METAL_LINES': (('fbm', 'scratch'), '''res = op_fbm(x * 4.0, y / 48.0, seed, 3);
  res = 0.7 * res + 0.3 * op_scratch(x * 2.0, y * 0.15, seed + 4u, 0.04, 0.55, true);'''),
    'WOOD_PORES': (('fbm', 'fade'), 'res = op_smooth(0.53, 0.68, op_fbm(x * 4.0, y / 48.0, seed, 3));'),
    'WARPED': (('fbm',), '''res = op_fbm(x + 2.0 * op_perlin(x * 0.6, y * 0.6, seed + 21u),
               y + 2.0 * op_perlin(x * 0.6, y * 0.6, seed + 73u), seed, 4);'''),
    'SCRATCHES': (('scratch',), 'res = op_scratch(x, y, seed, 0.04, 0.55, false);'),
    'GRUNGE_SCRATCHES': (('scratch',), 'res = op_scratch(x, y, seed, 0.04, 0.55, false);'),
    'DIRECTIONAL_SCRATCHES': (('scratch',), 'res = op_scratch(x, y, seed, 0.04, 0.55, true);'),
    'WEATHERING_FRONT': (('weathering',), 'res = op_weathering_front(x, y, seed);'),
    'CORROSION_GRAIN': (('weathering',), 'res = op_corrosion_grain(x, y, seed);'),
    'RUNOFF_TRAILS': (('weathering',), 'res = op_runoff_trails(x, y, seed);'),
    'TAPERED_SCRATCHES': (('tapered_scratches',), 'res = op_tapered_scratches(x, y, seed, 0.024, 0.34);'),
    'FINE_FISSURES': (('fine_fissures',), 'res = op_fine_fissures(x, y, seed, 0.022, 0.62);'),
    'FRACTURED_PAINT': (('fractured_paint',), 'res = op_fractured_paint(x, y, seed, 0.012, 0.82);'),
    'CRACKS': (('cells', 'perlin', 'fade'), '''vec3 c = op_cells(x + 0.4 * op_perlin(x * 1.7, y * 1.7, seed + 9u),
                     y + 0.4 * op_perlin(x * 1.7, y * 1.7, seed + 13u), seed);
  res = 1.0 - op_smooth(0.04 * 0.2, 0.04, c.y - c.x);'''),
    'GRUNGE_CRACKS': (('cells', 'perlin', 'fade'), '''vec3 c = op_cells(x + 0.4 * op_perlin(x * 1.7, y * 1.7, seed + 9u),
                     y + 0.4 * op_perlin(x * 1.7, y * 1.7, seed + 13u), seed);
  res = 1.0 - op_smooth(0.04 * 0.2, 0.04, c.y - c.x);'''),
    'LEATHER_GRAIN': (('cells', 'perlin', 'fbm', 'fade'), '''vec3 c = op_cells(x + 0.4 * op_perlin(x * 1.7, y * 1.7, seed + 9u),
                     y + 0.4 * op_perlin(x * 1.7, y * 1.7, seed + 13u), seed);
  float cracks = 1.0 - op_smooth(0.04 * 0.2, 0.04, c.y - c.x);
  res = (1.0 - cracks) * 0.75 + op_fbm(x * 9.0, y * 9.0, seed + 31u, 2) * 0.25;'''),
    'RUST_FLAKES': (('cells', 'perlin', 'fade'), '''vec3 c = op_cells(x + 0.4 * op_perlin(x * 1.7, y * 1.7, seed + 9u),
                     y + 0.4 * op_perlin(x * 1.7, y * 1.7, seed + 13u), seed);
  float cracks = 1.0 - op_smooth(0.04 * 0.2, 0.04, c.y - c.x);
  res = (1.0 - cracks) * (0.3 + c.z * 0.7);'''),
    'WOOD_GRAIN': (('fbm',), '''float bend = op_fbm(x * 0.6, y * 0.08, seed, 3);
  float ring = 0.5 + 0.5 * sin((x + bend * 4.0) * 6.2831854820251465);
  res = 0.65 * pow(ring, 0.6) + 0.35 * op_fbm(x * 6.0, y * 0.16, seed + 31u, 3);'''),
    'FABRIC_WEAVE': (('value', 'fade'), '''float fx = op_frac1(x), fy = op_frac1(y);
  float alternate = op_frac1((floor(x) + floor(y)) * 0.5) * 2.0;
  float warp = pow(sin(fx * 3.141592653589793), 0.45);
  float weft = pow(sin(fy * 3.141592653589793), 0.45);
  res = (alternate == 0.0) ? warp * (0.8 + 0.2 * weft) : weft * (0.8 + 0.2 * warp);
  res *= 0.85 + op_value(x * 24.0, y * 24.0, seed) * 0.15;'''),
    'MACHINING_LINES': (('fbm',), '''float radius = sqrt((u - 0.5) * (u - 0.5) + (v - 0.5) * (v - 0.5)) * scale;
  res = (0.5 + 0.5 * sin(radius * 2.0 * 3.141592653589793)) * 0.8 + op_fbm(x * 4.0, y, seed, 2) * 0.2;'''),
    'CONCRETE_PORES': (('pores',), 'res = op_pores(x, y, seed, 0.3, 0.55);'),
    'RUST_PITTING': (('pores', 'fbm'), '''res = op_pores(x, y, seed, 0.3, 0.55);
  res *= op_smooth(0.25, 0.7, op_fbm(x * 0.4, y * 0.4, seed + 71u, 3));'''),
    'CASTING_NOISE': (('pores', 'fbm'), '''res = op_pores(x, y, seed, 0.3, 0.55);
  res = res * 0.65 + op_fbm(x * 2.0, y * 2.0, seed + 71u, 3) * 0.35;'''),
    'DUST_SPECKLES': (('pores',), 'res = op_pores(x, y, seed, 0.12, 0.55);'),
    'GRUNGE_DUST': (('pores',), 'res = op_pores(x, y, seed, 0.12, 0.55);'),
    'CONCRETE_AGGREGATE': (('cells', 'value'), '''float coarse = op_cells(x, y, seed).z;
  res = coarse * 0.7 + op_value(x * 4.0, y * 4.0, seed + 17u) * 0.3;'''),
    'STONE_GRAIN': (('cells', 'value'), '''float coarse = op_cells(x, y, seed).z;
  res = coarse * 0.7 + op_value(x * 4.0, y * 4.0, seed + 17u) * 0.3;
  res = (coarse > 0.6) ? res : res * 0.25;'''),
    'SAND_GRAIN': (('cells', 'value'), '''float coarse = op_cells(x, y, seed).z;
  res = sqrt(coarse * 0.7 + op_value(x * 4.0, y * 4.0, seed + 17u) * 0.3);'''),
    'METAL_GRAIN': (('fbm', 'value'), 'res = 0.65 * op_fbm(x * 3.0, y * 3.0, seed, 3) + 0.35 * op_value(x * 12.0, y * 12.0, seed + 8u);'),
    'PLASTIC_GRAIN': (('fbm',), 'res = op_fbm(x * 3.0, y * 3.0, seed, 3);'),
    'RUBBER_GRAIN': (('fbm', 'fade'), 'res = op_smooth(0.25, 0.75, op_fbm(x * 3.0, y * 3.0, seed, 3));'),
    'GRUNGE_FINE': (('fbm',), 'res = op_fbm(x * 3.0, y * 3.0, seed, 5);'),
    'GRUNGE_MEDIUM': (('fbm',), 'res = op_fbm(x, y, seed, 5);'),
    'GRUNGE_HEAVY': (('fbm', 'fade'), 'res = op_smooth(0.35, 0.65, op_fbm(x * 0.4, y * 0.4, seed, 5));'),
    'GRUNGE_DIRT': (('fbm', 'fade'), 'res = op_smooth(0.3, 0.7, op_fbm(x, y, seed, 5)) * op_fbm(x * 4.0, y * 4.0, seed + 15u, 2);'),
    'GRUNGE_SPOTS': (('fbm', 'fade'), 'res = op_smooth(0.48, 0.62, op_fbm(x, y, seed, 5));'),
    'OXIDATION_SPOTS': (('fbm', 'fade'), 'res = op_smooth(0.48, 0.62, op_fbm(x, y, seed, 5));'),
    'GRUNGE_STREAKS': (('fbm', 'fade'), '''res = op_smooth(0.4, 0.65, op_fbm(x * 2.0, y * 0.08, seed, 3));
  res *= op_smooth(0.22, 0.65, op_fbm(x * 0.5, y * 0.35, seed + 11u, 3));'''),
    'GRUNGE_LEAKS': (('fbm', 'value', 'fade'), '''res = op_smooth(0.4, 0.65, op_fbm(x * 2.0, y * 0.08, seed, 3));
  res *= op_smooth(0.22, 0.65, op_fbm(x * 0.5, y * 0.35, seed + 11u, 3));
  res *= pow(1.0 - op_frac1(y * 0.15 + op_value(x, y * 0.0, seed + 8u) * 0.8), 0.6);'''),
    'FINGERPRINTS': (('perlin', 'hash', 'fade'), '''float dx = (op_frac1(x) - 0.5) * 1.4;
  float dy = (op_frac1(y) - 0.5) * 0.8;
  float bx = dx + 0.12 * sin(dy * 9.0);
  float radius = sqrt(bx * bx + dy * dy);
  float ridges = op_smooth(0.0, 0.55, sin(radius * 170.0 + op_perlin(x, y, seed) * 3.0));
  res = ridges * (1.0 - op_smooth(0.27, 0.43, radius)) * ((op_hash(floor(x), floor(y), seed) < 0.55) ? 1.0 : 0.0);'''),
    'SWIRLS': (('hash', 'value', 'fade'), '''float ix = floor(x), iy = floor(y);
  res = 0.0;
  for (int dx = -1; dx <= 1; dx++) {
    for (int dy = -1; dy <= 1; dy++) {
      float cx = ix + float(dx), cy = iy + float(dy);
      float px = x - cx - op_hash(cx, cy, seed);
      float py = y - cy - op_hash(cx, cy, seed + 7u);
      float radius = sqrt(px * px + py * py);
      float bands = op_frac1(radius * 18.0 + op_hash(cx, cy, seed + 11u));
      float ring = 1.0 - op_smooth(0.1, 0.22, min(bands, 1.0 - bands));
      float breakup = op_smooth(0.45, 0.7, op_value(px * 3.0, py * 3.0, seed + 19u));
      ring *= (1.0 - op_smooth(0.5, 0.85, radius)) * breakup;
      res = max(res, ring);
    }
  }'''),
    'SMUDGES': (('fbm', 'fade'), '''res = op_smooth(0.4, 0.68, op_fbm(x * 0.6, y * 1.8, seed, 4));
  res *= 0.75 + 0.25 * op_fbm(x * 0.2, y * 5.0, seed + 18u, 3);'''),
    'VEINS': (('fbm', 'perlin', 'fade'), '''float warped = x + 3.5 * op_fbm(x * 0.5, y * 0.5, seed, 5);
  float veins = abs(sin((warped + y * 0.3) * 3.141592653589793));
  res = 1.0 - op_smooth(0.04, 0.04 * 4.0, veins);
  res = max(res, (1.0 - op_smooth(0.0, 0.04 * 0.8, abs(op_perlin(x * 2.0 + warped, y * 2.0, seed + 72u)))) * 0.3);'''),
    'CHECKER': (('fade',), 'res = op_frac1((floor(x) + floor(y)) * 0.5) * 2.0;'),
    'STRIPES': (('fade',), 'res = (op_frac1(x) < 0.5) ? 1.0 : 0.0;'),
    'DOTS': (('fade',), '''float fx = op_frac1(x), fy = op_frac1(y);
  res = 1.0 - op_smooth(0.3 - 0.04, 0.3, sqrt((fx - 0.5) * (fx - 0.5) + (fy - 0.5) * (fy - 0.5)));'''),
    'PATTERN': (('fade',), '''float fx = op_frac1(x), fy = op_frac1(y);
  res = 1.0 - op_smooth(0.3 - 0.04, 0.3, sqrt((fx - 0.5) * (fx - 0.5) + (fy - 0.5) * (fy - 0.5)));'''),
    'GRID': (('fade',), '''float fx = op_frac1(x), fy = op_frac1(y);
  float bx = min(fx, 1.0 - fx), by = min(fy, 1.0 - fy);
  res = 1.0 - op_smooth(0.04 * 0.5, 0.04, min(bx, by));'''),
    'TILE_PATTERN': (('fade',), '''float fx = op_frac1(x), fy = op_frac1(y);
  float bx = min(fx, 1.0 - fx), by = min(fy, 1.0 - fy);
  res = op_smooth(0.04 * 0.5, 0.04, min(bx, by));'''),
    'BRICK_PATTERN': (('fade',), '''float fy = op_frac1(y);
  float fx = op_frac1(x + op_frac1(floor(y) * 0.5) * 2.0 * 0.5);
  float bx = min(fx, 1.0 - fx), by = min(fy, 1.0 - fy);
  res = op_smooth(0.04 * 0.5, 0.04, min(bx, by));'''),
    'HEX_PATTERN': (('fade',), '''float xx = x, yy = y / 1.7320508075688772;
  float ax = op_frac1(xx + 0.5) - 0.5, ay = (op_frac1(yy + 0.5) - 0.5) * 1.7320508075688772;
  float hx = op_frac1(xx) - 0.5, hy = (op_frac1(yy) - 0.5) * 1.7320508075688772;
  bool choose_a = ax * ax + ay * ay < hx * hx + hy * hy;
  float dx = choose_a ? ax : hx, dy = choose_a ? ay : hy;
  float edge = 0.5 - max(abs(dx), abs(dx) * 0.5 + abs(dy) * 1.7320508075688772 * 0.5);
  res = op_smooth(0.04 * 0.5, 0.04, edge);'''),
}


def supported(kind):
    from ...texture_engine.procedurals import ALIASES
    key = str(kind).strip().upper().replace('-', '_').replace(' ', '_')
    return ALIASES.get(key, key) in KINDS


def canonical(kind):
    from ...texture_engine.procedurals import ALIASES
    key = str(kind).strip().upper().replace('-', '_').replace(' ', '_')
    return ALIASES.get(key, key)


def function_name(kind):
    return 'op_procedural_' + canonical(kind).lower()


def library(kinds):
    """GLSL of the catalog sources ``kinds``, their primitives first, each once."""
    ordered, seen = [], set()

    def need(name):
        if name in seen:
            return
        seen.add(name)
        depends, source = SNIPPETS[name]
        for dependency in depends:
            need(dependency)
        ordered.append(source)

    functions = []
    for kind in sorted(set(canonical(k) for k in kinds)):
        depends, body = KINDS[kind]
        for name in depends:
            need(name)
        functions.append(
            f'float {function_name(kind)}(vec2 uv, float scale, uint seed) {{\n'
            f'  float u = uv.x, v = uv.y;\n'
            f'  float x = u * scale, y = v * scale;\n'
            f'  float res = 0.0;\n'
            f'  {body}\n'
            f'  if (isnan(res)) {{ res = 0.0; }}\n'
            f'  if (isinf(res)) {{ res = (res > 0.0) ? 1.0 : 0.0; }}\n'
            f'  return clamp(res, 0.0, 1.0);\n'
            f'}}\n')
    return ''.join(ordered) + ''.join(functions)
