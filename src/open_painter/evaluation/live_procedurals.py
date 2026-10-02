"""Restricted full-precision GPU scalar source evaluator.

This module is deliberately independent of the live input planner. Unsupported
sources/parameters raise UnsupportedSource so a caller can retain the exact CPU
path. Coordinates are small CPU float32 lookup tables, preserving NumPy octave
rounding without generating or uploading a full-resolution scalar raster.
"""

from contextlib import contextmanager
import re

import numpy as np

from . import metrics
from ..texture_engine.procedurals import ALIASES


KINDS = {name: i for i, name in enumerate((
    'PERLIN', 'FBM', 'GRUNGE_MEDIUM', 'BRUSHED_METAL_LINES',
    'CONCRETE_PORES', 'ORANGE_PEEL', 'SCRATCHES', 'VORONOI'))}


# Backends whose scalar and mask results the GPU parity suites (tests/integration/live_procedurals.py,
# live_masks.py) have checked against the CPU reference; anything else keeps the CPU path.
QUALIFIED_BACKENDS = frozenset({'METAL', 'VULKAN', 'OPENGL'})


class UnsupportedSource(ValueError):
    """Use the CPU reference for this source or coordinate domain."""


FRAGMENT = r'''
float hash_cell(ivec2 cell, uint seed) {
  uint h=uint(cell.x)*374761393u+uint(cell.y)*668265263u+seed*2246822519u;
  h=(h^(h>>13u))*1274126177u;
  h=h^(h>>16u);
  return float(h)*0.00000000023283064365386962890625;
}
float fade_curve(float t) { return t*t*t*(t*(t*6.0-15.0)+10.0); }
float interpolate(float a,float b,float t) { return a+(b-a)*t; }
float smooth_curve(float a,float b,float value) {
  float t=clamp((value-a)/max(b-a,0.000001),0.0,1.0);
  return t*t*(3.0-2.0*t);
}
vec2 coordinate(int row) {
  ivec2 p=ivec2(gl_FragCoord.xy);
  return vec2(texelFetch(coordinates,ivec2(p.x,row),0).r,
              texelFetch(coordinates,ivec2(p.y,row),0).g);
}
float gradient(ivec2 cell,vec2 d,uint seed) {
  float angle=hash_cell(cell,seed)*6.283185482025146484375;
  return cos(angle)*d.x+sin(angle)*d.y;
}
float perlin(vec2 p,uint seed) {
  ivec2 cell=ivec2(floor(p)); vec2 d=p-vec2(cell);
  float x=fade_curve(d.x),y=fade_curve(d.y);
  return interpolate(interpolate(gradient(cell,d,seed),gradient(cell+ivec2(1,0),d-vec2(1,0),seed),x),
                     interpolate(gradient(cell+ivec2(0,1),d-vec2(0,1),seed),gradient(cell+ivec2(1,1),d-vec2(1,1),seed),x),y);
}
float fbm(uint seed,int count) {
  float sum=0.0,total=0.0,amplitude=1.0;
  for(int i=0;i<8;i++) {
    if(i>=count)break;
    float value=0.5+perlin(coordinate(i+1),seed+uint(i)*53u)*0.8;
    sum+=amplitude*value; total+=amplitude; amplitude*=0.5;
  }
  return sum/total;
}
vec2 cells(vec2 p,uint seed) {
  ivec2 cell=ivec2(floor(p)); float first=1e30; ivec2 winner=cell;
  for(int y=-2;y<=2;y++)for(int x=-2;x<=2;x++) {
    ivec2 c=cell+ivec2(x,y);
    vec2 feature=vec2(c)+vec2(hash_cell(c,seed),hash_cell(c,seed+79u));
    vec2 delta=p-feature;
    float distance=delta.x*delta.x+delta.y*delta.y;
    if(distance<first) { first=distance;winner=c; }
  }
  return vec2(sqrt(first),hash_cell(winner,seed+173u));
}
float scratches(vec2 p,uint seed,bool directional) {
  ivec2 cell=ivec2(floor(p)); float result=0.0;
  for(int y=-1;y<=1;y++)for(int x=-1;x<=1;x++) {
    ivec2 c=cell+ivec2(x,y);
    vec2 delta=(p-vec2(c))-vec2(hash_cell(c,seed),hash_cell(c,seed+21u));
    float angle=(hash_cell(c,seed+51u)-0.5)*(directional?0.16:3.1415927410125732421875);
    float along=delta.x*sin(angle)+delta.y*cos(angle);
    float across=delta.x*cos(angle)-delta.y*sin(angle);
    float half_length=0.12+hash_cell(c,seed+62u)*0.55;
    float end=max(abs(along)-half_length,0.0);
    float distance=sqrt(across*across+end*end);
    float line=1.0-smooth_curve(parameters.x*0.25,parameters.x,distance);
    result=max(result,hash_cell(c,seed+9u)<parameters.y?line:0.0);
  }
  return result;
}
void main() {
  int kind=control.x; uint seed=uint(control.y); vec2 p=coordinate(0);
  float result=0.0;
  if(kind==0)result=0.5+perlin(p,seed)*0.8;
  else if(kind==1||kind==2)result=fbm(seed,control.z);
  else if(kind==3)result=0.7*fbm(seed,3)+0.3*scratches(coordinate(9),seed+4u,true);
  else if(kind==4) {
    vec2 cell=cells(p,seed);
    result=(1.0-smooth_curve(parameters.z*0.3,parameters.z,cell.x))*(cell.y<parameters.y?1.0:0.0);
  }
  else if(kind==5)result=smooth_curve(0.0,0.9,cells(p,seed).x)*0.65+fbm(seed+9u,2)*0.35;
  else if(kind==6)result=scratches(p,seed,false);
  else if(kind==7)result=cells(p,seed).y;
  fragColor=clamp(result,0.0,1.0);
}
'''
# Blender wraps translated fragment functions in a Metal struct. Clang floating
# point controls must be inside each function body, never at that struct scope.
FRAGMENT = re.sub(r'(^\w+ \w+\([^\n]*\) \{)',
    r'\1\n#ifdef GPU_METAL\n#pragma clang fp contract(off)\n#pragma clang fp reassociate(off)\n#endif\n',
    FRAGMENT, flags=re.MULTILINE)


def _configuration(kind, n, scale, seed, params):
    key = str(kind).strip().upper().replace('-', '_').replace(' ', '_')
    key = ALIASES.get(key, key)
    allowed = {'angle', 'width', 'density', 'radius', 'octaves', 'persistence', 'anisotropy'}
    if key not in KINDS or set(params) - allowed:
        raise UnsupportedSource('GPU source or parameter is unsupported')
    if not isinstance(n, int) or not 1 <= n <= 8192:
        raise UnsupportedSource('GPU source requires a square grid of 1–8192 pixels')
    if not np.isfinite(scale) or not 0 < scale <= 256:
        raise UnsupportedSource('GPU scalar scale must be within (0, 256]')
    if not isinstance(seed, (int, np.integer)) or not 0 <= seed <= 0x7fffffff - 512:
        raise UnsupportedSource('GPU source seed is outside the validated uint32 domain')
    if params.get('angle', 0) != 0 or params.get('persistence', .5) != .5:
        raise UnsupportedSource('Rotated source coordinates and persistence require CPU evaluation')
    if params.get('anisotropy', 48) != 48:
        raise UnsupportedSource('Non-default anisotropy requires CPU evaluation')
    width, density, radius = (float(params.get(k, v)) for k, v in (
        ('width', .04), ('density', .55), ('radius', .3)))
    if not all(np.isfinite(value) for value in (width, density, radius)) or not .02 <= width <= 1 or not .01 <= radius <= 1:
        raise UnsupportedSource('GPU scalar parameters are outside their supported domain')
    octaves = 5 if key == 'GRUNGE_MEDIUM' else 3 if key == 'BRUSHED_METAL_LINES' else 2 if key == 'ORANGE_PEEL' else int(params.get('octaves', 4))
    if not 1 <= octaves <= 8:
        raise UnsupportedSource('GPU octave count must be within [1, 8]')
    return key, octaves, (width, float(np.clip(density, 0, 1)), radius, 0.)


def supports(kind, n, *, scale=8., seed=1, **params):
    """Pure eligibility query; no shader compilation, allocation or image reads.

    The caller additionally checks UV identity transforms. Backend availability
    is checked when constructing ScalarDevice; only verified Metal is enabled.
    """
    try:
        _configuration(kind, n, scale, seed, params)
    except (UnsupportedSource, TypeError, ValueError, OverflowError):
        return False
    return True


def coordinate_table(kind, n, scale, octaves):
    """Ten O(n) rows, evaluated with the same NumPy float32 ordering as CPU."""
    axis = (np.arange(n, dtype=np.float32) + .5) / n
    x, y = axis * float(scale), axis * float(scale)
    table = np.zeros((10, n, 4), dtype=np.float32)
    table[0, :, 0], table[0, :, 1] = x, y
    if kind == 'BRUSHED_METAL_LINES':
        table[9, :, 0], table[9, :, 1] = x * 2, y * .15
        x, y = x * 4, y / 48
    elif kind == 'ORANGE_PEEL':
        x, y = x * 2, y * 2
    for i in range(octaves):
        table[i+1, :, 0], table[i+1, :, 1] = x, y
        x, y = x * 2.03 + 13.1, y * 2.03 - 7.7
    return table


class ScalarDevice:
    """One reusable shader, caller-owned R32F results, no image readback/writes."""

    def __init__(self):
        import bpy
        import gpu
        from gpu_extras.batch import batch_for_shader
        if bpy.app.background or not bpy.context.window:
            raise RuntimeError('Live procedural evaluation needs a Blender GPU window')
        if gpu.platform.backend_type_get() not in QUALIFIED_BACKENDS:
            raise UnsupportedSource('GPU scalar parity is verified on Metal, Vulkan and OpenGL only')
        self.window = bpy.context.window.as_pointer()
        info = gpu.types.GPUShaderCreateInfo()
        info.vertex_in(0, 'VEC2', 'position')
        info.fragment_out(0, 'FLOAT', 'fragColor')
        info.sampler(0, 'FLOAT_2D', 'coordinates')
        info.push_constant('IVEC4', 'control')
        info.push_constant('VEC4', 'parameters')
        info.vertex_source('void main(){gl_Position=vec4(position,0.0,1.0);}')
        info.fragment_source(FRAGMENT)
        self.shader = gpu.shader.create_from_info(info)
        metrics.count('live_gpu_source_shader_compilations')
        self.batch = batch_for_shader(self.shader, 'TRIS', {
            'position': ((-1,-1), (3,-1), (-1,3))})

    def _check(self):
        import bpy
        if self.shader is None or not bpy.context.window or bpy.context.window.as_pointer() != self.window:
            raise RuntimeError('Live procedural GPU window was closed or changed')

    @contextmanager
    def _state(self):
        import gpu
        self._check()
        viewport, scissor = gpu.state.viewport_get(), gpu.state.scissor_get()
        blend, depth, depth_mask = gpu.state.blend_get(), gpu.state.depth_test_get(), gpu.state.depth_mask_get()
        try:
            gpu.state.blend_set('NONE')
            gpu.state.depth_test_set('NONE')
            gpu.state.depth_mask_set(False)
            yield
        finally:
            gpu.shader.unbind()
            gpu.state.viewport_set(*viewport)
            gpu.state.scissor_set(*scissor)
            gpu.state.blend_set(blend)
            gpu.state.depth_test_set(depth)
            gpu.state.depth_mask_set(depth_mask)

    @metrics.measured('live_gpu_source_generation')
    def generate(self, kind, n, *, scale=8., seed=1, **params):
        import gpu
        key, octaves, parameters = _configuration(kind, n, scale, seed, params)
        table = coordinate_table(key, n, scale, octaves)
        with self._state():
            coordinates = gpu.types.GPUTexture((n, len(table)), format='RGBA32F',
                data=gpu.types.Buffer('FLOAT', table.size, table.ravel()))
            target = gpu.types.GPUTexture((n,n), format='R32F')
            metrics.count('live_gpu_uploads')
            metrics.count('live_gpu_upload_bytes', table.nbytes)
            metrics.count('live_gpu_allocations', 2)
            metrics.count('live_gpu_allocated_bytes', table.nbytes+n*n*4)
            framebuffer = gpu.types.GPUFrameBuffer(color_slots=target)
            with framebuffer.bind():
                gpu.state.viewport_set(0,0,n,n)
                gpu.state.scissor_set(0,0,n,n)
                self.shader.bind()
                self.shader.uniform_sampler('coordinates', coordinates)
                self.shader.uniform_int('control', (KINDS[key], int(seed), octaves, 0))
                self.shader.uniform_float('parameters', parameters)
                self.batch.draw(self.shader)
            metrics.count('live_gpu_passes')
            metrics.count('live_gpu_scalar_sources')
            return target

    def close(self):
        import gpu
        if self.shader is not None:
            gpu.shader.unbind()
        self.shader = self.batch = None
