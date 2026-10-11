"""Open Painter's GPU evaluator.

A texture set's compiled units (``compiler.EngineGraph``) render in texture space,
one full-screen pass per unit, into textures the engine owns; each channel's chain
of CHANNEL units composes bottom to top exactly as the material graph wired them.
The finished channel is padded across UV seams with the exporter's donor map and
written into the set's channel image, mip chain included, so every material that
reads it through the Open Painter Texture Set node shows the new texels without a
material change. Nothing here builds shader nodes.

Change detection is by stamp: a unit's stamp is its program, parameter values,
image identities and object matrices plus the stamps of everything it reads. A
channel whose chain stamp is unchanged is not rendered; a changed one re-renders
its chain, reusing retained SRC/MASK results whose stamps still match.
"""
import os
import weakref
from collections import Counter, OrderedDict
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from time import perf_counter

import numpy as np

from ...constants import CHANNELS, DEFAULTS
from .. import metrics
from ..resource_keys import colorspace_name
from . import fusion, glsl, stages
from .formats import layout, scalar_unit, texture_bytes
from .resources import binding

RGBA = 'RGBA32F'
HALF = 'RGBA16F'   # display precision for every channel but Height (bump detail is sub-1/2048)
SCALAR = 'R32F'
MIB, GIB = 1024 ** 2, 1024 ** 3


def physical_memory():
    try:
        return os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')
    except (ValueError, OSError, AttributeError):
        return 16 * GIB


def memory_share(fraction, low, high):
    """A budget scaled to the machine: 8 GB machines keep room for Blender and the rest of the
    system; 16 GB and larger ones get the full budget."""
    return int(min(high, max(low, physical_memory() * fraction)))


RETAIN_BYTES = memory_share(1 / 32, 128 * MIB, 512 * MIB)   # SRC/MASK results kept between evaluations, per set
# Keep the unchanged results an edit's changed units read, for its next tick (_update_frontier).
KEEP_READS = os.environ.get('OP_KEEP_READS', '1') != '0'
DEVICE_BYTES = memory_share(1 / 4, 1536 * MIB, 4 * GIB)     # all managed textures across texture sets
INTERFACE_ENTRIES = 256              # metadata cannot retain evicted shaders forever
POOL_SPARES = 3                      # free textures of one size and format kept for reuse
MIP_SCRATCH_BYTES = memory_share(1 / 48, 64 * MIB, 384 * MIB)   # publication's kept mip scratch (2K fits)
# Global cap on free textures; on 16 GB+ it fits a 4K float publication scratch plus compact stages.
POOL_BYTES = memory_share(1 / 32, 128 * MIB, 512 * MIB)
PRECISE = False   # full-float intermediates for every channel by default (parity tests)
# Publication through the images' pixel buffers (read back, written to Image.pixels, re-uploaded
# by Blender) instead of rendering into the images' GPU textures. None: the backend's default,
# which is the framebuffer pass everywhere; Blender 5.2's Vulkan image textures are render
# targets, mip levels included, as on Metal and OpenGL. OP_ENGINE_PIXEL_PUBLISH=1/0 forces it
# for whole suites; tests set this to True/False.
PIXEL_PUBLISH = {'1': True, '0': False}.get(os.environ.get('OP_ENGINE_PIXEL_PUBLISH'))
# The Vulkan default before 0.14.2: pixel publication with its Vulkan shortcuts (uniform clears,
# one-texel constants, fused finals). Kept as a fallback; its suites turn it on.
VULKAN_PIXEL_ROUTE = False
# A background Vulkan session takes that route too. Nothing draws the images' GPU textures
# there: each set's would only hold device memory (charged to the process on Windows), every
# consumer (export, bake, save) reads the CPU pixels, and Blender's Vulkan backend frees released
# GPU resources at frame ends, which a background session never has.
BACKGROUND_PIXELS = True
PIXEL_BUDGET_MS = 20.0   # per evaluation through pixels: the channels waiting longest publish first
FUSE_PIXEL_PUBLISH = True   # test/diagnostic opt-out; the normal publication path remains available
FUSE_PIXEL_PUBLISH_MAX_SIZE = 1024  # larger Vulkan targets retain the measured faster compact path
COMPACT_PIXEL_PUBLISH = True   # scalar channels publish one component through pixels (_publication_format)
CONSTANT_OUTPUTS = True     # evaluate proven spatially constant fills at one texel on Vulkan
CACHE_EVALUATION_STATES = True  # recheck live bindings once per synchronous evaluation
NATIVE_HOST_GRAPH = True       # diagnostic paired comparison; library/Metal still required
NATIVE_REVISIONS = True        # all-or-nothing framebuffer commits with GPU rollback snapshots
# Native revisions keep each channel's accepted mip chain and its next one. Every backend
# publishes in place instead: as fast on warm edits, faster after an undo, and less memory.
# Vulkan, 40 layers at 1K on a GTX 1050 (2026-10-02): undo 162-197 -> 120 ms, 0.45 GiB less.
# Metal, the same stack on an M-series Mac (2026-10-11): undo 37-38 -> 32-33 ms, 85-105 MiB
# less; three 1K sets in a window 4.51 -> 4.23 GiB. OP_REVISION_BACKENDS=METAL brings them back
# for comparison.
REVISION_BACKENDS = frozenset(filter(None, os.environ.get('OP_REVISION_BACKENDS', '').split(',')))
BINDING_DRAWS = True           # passes through the binding's C draw wherever the library loads
MIP_LANES = 3                  # channels published in place whose mips share a draw (1 to 3)
# A FIELD (a channel's image detail, shader_graph.DETAIL_FIELDS) holding what another FIELD already
# holds (the same shader, parameter values and images: a layer's channels share one detail) is
# filled by copying that field's texels instead of sampling the detail again.
FIELD_COPIES = True
# A folder step whose children leave it nothing in a channel blends a transparent group over
# its backdrop: its result does not depend on its parameters (Engine._param_free).
PARAM_FREE = os.environ.get('OP_PARAM_FREE', '1') != '0'
EMPTY_GROUP = ('const', (0.0, 0.0, 0.0, 0.0))
MIP_SCRATCH_SIDE = 2048        # publication keeps its mip scratch up to this side; larger leases it each time
# A padding donor map up to this side holds its texel coordinates in half floats: every integer
# up to 2048 is exact in a half, so publication reads 4 bytes a texel instead of 8, the same donors.
DONOR_HALF_SIDE = 2048
VERIFY_ADOPTION = os.environ.get('OP_VERIFY_STRUCTURE') == '1'   # incremental adoption against a full one
CARRY_RESULTS = os.environ.get('OP_CARRY_RESULTS', '1') != '0'   # a rebuilt unit starts from its predecessor's result
VERIFY_REBIND = os.environ.get('OP_VERIFY_INCREMENTAL') == '1'   # every rebound program against its translation

VERTEX = 'void main() { gl_Position = vec4(op_pos, 0.0, 1.0); }'

# Fixed programs of the publication path.
PUBLISH = r'''
void main() {
  ivec2 p = ivec2(gl_FragCoord.xy);
  ivec2 q = p;
  if (op_pad == 1) {
    /* The exporter's seam padding: empty texels take their nearest island texel. */
    vec2 donor = texelFetch(op_donor, p, 0).xy;
    if (donor.x >= 0.0) { q = ivec2(donor); }
  }
  /* A channel no layer touches publishes its default: a one-texel constant. */
  vec3 rgb = texelFetch(op_source, min(q, textureSize(op_source, 0) - 1), 0).rgb;
  if (op_scalar == 1) { rgb = vec3(rgb.r); }
  if (op_mode == 1) {
    /* Final:NORMAL of the material graph: Blender's safe normalize of (2c - 1), re-encoded. */
    vec3 v = rgb * 2.0 - 1.0;
    float l = dot(v, v);
    v = (l > 0.0) ? v * inversesqrt(l) : v;
    rgb = (v + 1.0) * 0.5;
  }
  vec4 value = vec4(clamp(rgb, 0.0, 1.0), 1.0);
  op_out0 = value;   /* the image, level 0 */
  op_out1 = value;   /* the source of its mip chain */
}
'''
# Through pixels (Vulkan) only the image's content is read back: no second target for a mip chain
# Blender rebuilds itself.
PUBLISH_PIXELS = PUBLISH.replace('  op_out1 = value;   /* the source of its mip chain */\n', '')
assert PUBLISH_PIXELS != PUBLISH
MIP = r'''
void main() {
  ivec2 p = ivec2(gl_FragCoord.xy), size = textureSize(op_source, 0), q = p * 2;
  vec4 value = (texelFetch(op_source, min(q, size - 1), 0) + texelFetch(op_source, min(q + ivec2(1, 0), size - 1), 0) +
                texelFetch(op_source, min(q + ivec2(0, 1), size - 1), 0) + texelFetch(op_source, min(q + ivec2(1, 1), size - 1), 0)) * 0.25;
  op_out0 = value;   /* the image, this level */
  op_out1 = value;   /* the source of the next level */
}
'''
FINISH = r'''
void main() {
  ivec2 p = ivec2(gl_FragCoord.xy);
  vec3 rgb = texelFetch(op_source, p, 0).rgb;
  if (op_mode == 1) {
    /* Final:NORMAL of the material graph: Blender's safe normalize of (2c - 1), re-encoded. */
    vec3 v = rgb * 2.0 - 1.0;
    float l = dot(v, v);
    v = (l > 0.0) ? v * inversesqrt(l) : v;
    rgb = (v + 1.0) * 0.5;
  }
  op_out0 = vec4(clamp(rgb, 0.0, 1.0), 1.0);
}
'''
PAD = r'''
void main() {
  ivec2 p = ivec2(gl_FragCoord.xy);
  vec2 donor = texelFetch(op_donor, p, 0).xy;
  op_out0 = texelFetch(op_source, (donor.x >= 0.0) ? ivec2(donor) : p, 0);
}
'''
DOWNSAMPLE = r'''
void main() {
  ivec2 p = ivec2(gl_FragCoord.xy), size = textureSize(op_source, 0), q = p * 2;
  op_out0 = (texelFetch(op_source, min(q, size - 1), 0) + texelFetch(op_source, min(q + ivec2(1, 0), size - 1), 0) +
             texelFetch(op_source, min(q + ivec2(0, 1), size - 1), 0) + texelFetch(op_source, min(q + ivec2(1, 1), size - 1), 0)) * 0.25;
}
'''
COPY = r'''
void main() { op_out0 = texelFetch(op_source, ivec2(gl_FragCoord.xy), 0); }
'''
COPY_MIP = r'''
void main() {
  op_out0 = textureLod(op_source, gl_FragCoord.xy / vec2(textureSize(op_source, op_level)), float(op_level));
}
'''
SAMPLE_PIXELS = r'''
void main() { op_out0 = texelFetch(op_source, ivec2(gl_FragCoord.xy) * op_step, 0); }
'''
GEOMETRY = frozenset({'position', 'normal', 'tangent', 'bitangent'})
NEUTRAL_SURFACE = {'position': (0.0, 0.0, 0.0, 1.0), 'normal': (0.0, 0.0, 1.0, 1.0),
                   'tangent': (1.0, 0.0, 0.0, 1.0), 'bitangent': (0.0, 1.0, 0.0, 1.0)}


class EngineUnavailable(RuntimeError):
    """No GPU context the engine can use (background Blender before ``gpu.init``)."""


def ensure_gpu():
    """Make the gpu module usable here: a window's context, or ``gpu.init`` in background."""
    import bpy
    import gpu
    try:
        gpu.platform.backend_type_get()
        gpu.types.GPUTexture((1, 1), format='R32F')
        return
    except SystemError:
        if not bpy.app.background:
            raise EngineUnavailable('The GPU is not available in this Blender session')
    try:
        gpu.init()
    except SystemError as error:
        raise EngineUnavailable(f'The GPU could not be initialised: {error}') from error


class Device:
    """Compiled programs, a texture pool and the full-screen pass, shared by every set."""

    def __init__(self):
        import gpu
        ensure_gpu()
        self.backend = gpu.platform.backend_type_get()
        from .program_cache import ProgramCache
        self.shaders = ProgramCache()
        self._interfaces = OrderedDict()
        self.engines = weakref.WeakSet()
        self._reclaiming = False
        self.batch = None
        self.pool = OrderedDict()
        self._pooled_bytes = 0
        self._pooled_ids = set()
        self._leases = {}  # id -> base-level bytes; metadata only, never keeps a texture alive
        self._native_pool = None
        self.compiles = 0
        self.compile_seconds = 0.0
        self.passes = 0
        self.settled = 0     # ``passes`` when the GPU was last waited for (``settle``)
        self._mip_scratch = {}       # (lane, side, format) -> texture a publication reduces mips through
        self._mip_scratch_bytes = 0
        self._mip_scratch_owner = None   # the engine whose queued mips use it, until they are drawn
        self.wait_cost = 0.0 # ms of submitting a pass that does nothing and waiting for it (``calibrate``)
        self._readbacks = {}  # side -> float buffer a pixel publication reads into (no allocation each time)
        self._compact_readback = None  # (side, components, buffer), at most one compact transfer buffer
        self._fixed = {}
        try:
            self.max_samplers = int(gpu.capabilities.max_textures_frag_get())
        except Exception:
            self.max_samplers = 16
        from ...native_bridge.lifecycle import execution_library
        execution_library(self.backend)

    # ---- programs -----------------------------------------------------------------

    def shader(self, program):
        key = (program.source, tuple(s.name for s in program.samplers), program.parameter_slots,
               len(program.objects), len(program.targets), program.procedurals)
        shader = self.shaders.get(key)
        if shader is None:
            shader = self._create(program.library + program.source, [s.name for s in program.samplers],
                                  program.parameter_slots, len(program.objects), len(program.targets), preamble=not program.native)
            for retired in self.shaders.put(key, shader, len(program.library.encode()) + len(program.source.encode())):
                self._interfaces.pop(retired, None)
                metrics.count('engine_shader_evictions')
        return shader

    def _create(self, source, samplers, parameter_slots=0, objects=0, targets=1, preamble=False, ints=(),
                counted=True):
        """``counted``: a program of the units' or a fixed pass (``compiles``); fusion's derived
        shaders count their own (``engine_chain_compiles``)."""
        import gpu
        if len(samplers) > self.max_samplers:
            raise ValueError(f'GPU pass needs {len(samplers)} samplers; device limit is {self.max_samplers}')
        started = perf_counter()
        info = gpu.types.GPUShaderCreateInfo()
        info.vertex_in(0, 'VEC2', 'op_pos')
        info.push_constant('VEC2', 'op_inv_size')
        if parameter_slots or objects:
            # Parameters (and decal frames) in one uniform buffer: a unit can carry more
            # than the 128 bytes of push constants every backend guarantees.
            members = f'vec4 p[{max(1, parameter_slots)}];'
            if objects:
                members += f' mat4 objects[{objects}];'
            info.typedef_source(f'struct OpParams {{ {members} }};')
            info.uniform_buf(0, 'OpParams', 'op_params')
        for name in ints:
            info.push_constant('INT', name)
        for slot, name in enumerate(samplers):
            info.sampler(slot, 'FLOAT_2D', name)
        for index in range(targets):
            info.fragment_out(index, 'VEC4', f'op_out{index}')
        info.vertex_source(VERTEX)
        info.fragment_source((glsl.PREAMBLE if preamble else '') + source)
        try:
            shader = gpu.shader.create_from_info(info)
        except Exception as error:
            raise RuntimeError(f'Open Painter engine program failed to compile: {error}\n{source}') from error
        # OpenGL removes unused uniforms (even op_inv_size in a constant fill).
        # Reflect once at compilation, rather than failing or probing on every pass.
        uniforms = frozenset(name for name in ('op_inv_size', *ints)
                             if _has_uniform(shader, name))
        # Samplers have ordinary uniform locations only on OpenGL. Metal/Vulkan
        # use separate resource bindings, which uniform_from_name cannot query.
        uniforms |= frozenset(name for name in samplers
                              if self.backend != 'OPENGL' or _has_uniform(shader, name))
        block = bool(parameter_slots or objects) and _has_uniform(shader, 'op_params', block=True)
        self._remember_interface(shader, (uniforms, block))
        elapsed = perf_counter() - started
        self.compile_seconds += elapsed
        if counted:
            self.compiles += 1
            metrics.count('engine_program_compiles')
            metrics.sample('engine_program_compile_ms', elapsed * 1000)
        return shader

    def _remember_interface(self, shader, interface):
        self._interfaces[shader] = interface
        self._interfaces.move_to_end(shader)
        while len(self._interfaces) > INTERFACE_ENTRIES:
            self._interfaces.popitem(last=False)
        return interface

    def _interface(self, shader, samplers, ubo, ints):
        interface = self._interfaces.get(shader)
        if interface is not None:
            self._interfaces.move_to_end(shader)
            return interface
        # An active unit may outlive its shared cache entry. Reflect its public
        # interface again instead of keeping every historical GPUShader alive.
        names = ('op_inv_size', *(ints or {}))
        uniforms = frozenset(name for name in names if _has_uniform(shader, name))
        uniforms |= frozenset(name for name, _ in samplers
                              if self.backend != 'OPENGL' or _has_uniform(shader, name))
        return self._remember_interface(shader, (uniforms, ubo is not None and
                                                 _has_uniform(shader, 'op_params', block=True)))

    def fixed(self, name, counted=True):
        """A fixed pass's shader, compiled once. ``counted=False``: a first compile for a self-test
        (fusion's store check) is not one of the units' or passes' (``compiles``)."""
        shader = self._fixed.get(name)
        if shader is None:
            if name.startswith('suffix.'):
                # Native-only kernels (no Python reference): suffix collapse runs on the C path.
                from ...native_bridge.compiler import fixed_source
                samplers, targets = {'suffix.accumulate': (['op_f0', 'op_f1', 'op_a', 'op_b'], 2),
                                     'suffix.apply': (['op_source', 'op_a', 'op_b'], 1)}[name]
                shader = self._fixed[name] = self._create(fixed_source(name, None), samplers,
                                                          targets=targets, ints=('op_swizzle',))
                return shader
            if name.startswith('mip_'):
                from .publication_shaders import mip_batch_source
                from ...native_bridge.compiler import fixed_source
                count = int(name[4:])
                source = fixed_source('runtime.' + name, mip_batch_source(MIP, count))
                shader = self._fixed[name] = self._create(source,
                    [f'op_source_{i}' for i in range(count)], targets=2 * count)
                return shader
            source, samplers, ints, targets = {
                'publish': (PUBLISH, ['op_source', 'op_donor'], ('op_mode', 'op_pad', 'op_scalar'), 2),
                'publish_pixels': (PUBLISH_PIXELS, ['op_source', 'op_donor'], ('op_mode', 'op_pad', 'op_scalar'), 1),
                'mip': (MIP, ['op_source'], (), 2),
                'finish': (FINISH, ['op_source'], ('op_mode',), 1),
                'pad': (PAD, ['op_source', 'op_donor'], (), 1),
                'downsample': (DOWNSAMPLE, ['op_source'], (), 1),
                'sample_pixels': (SAMPLE_PIXELS, ['op_source'], ('op_step',), 1),
                'copy': (COPY, ['op_source'], (), 1),
                'copy_mip': (COPY_MIP, ['op_source'], ('op_level',), 1)}[name]
            from ...native_bridge.compiler import fixed_source
            source = fixed_source('runtime.' + name, source)
            shader = self._fixed[name] = self._create(source, samplers, targets=targets, ints=ints,
                                                      counted=counted)
        return shader

    # ---- textures -----------------------------------------------------------------

    def _resource_pool(self):
        pool = getattr(self, '_native_pool', None)
        if pool is None and not self._leases and not self.pool:
            from ...native_bridge.lifecycle import execution_library
            lib = execution_library(getattr(self, 'backend', None))
            if lib is not None and hasattr(lib, 'op_pool_create'):
                from ...native_bridge.resources import NativePool
                pool = self._native_pool = NativePool(lib, self)
        if pool is not None:
            pool.configure(POOL_BYTES, POOL_SPARES, DEVICE_BYTES)
        return pool

    def _admit(self, native, width, height, fmt):
        if native.admit(width, height, fmt):
            return
        # Recompute idle results if another set needs room. Active evaluations,
        # paint history and accepted display revisions are never discarded here.
        if self._mip_scratch and self.drop_mip_scratch() and native.admit(width, height, fmt):
            return   # the mip scratch publication kept is idle storage: it goes first
        if not self._reclaiming:
            self._reclaiming = True
            try:
                for engine in tuple(self.engines):
                    if getattr(engine, '_native_revisions', None) is not None:
                        engine._native_revisions.trim_spares()
                        if native.admit(width, height, fmt):
                            return
                    if getattr(engine, '_suffix', None) is not None and engine._suffix.entries:
                        engine._suffix.reclaim()
                        if native.admit(width, height, fmt):
                            return
                    if getattr(engine, '_evaluation_depth', 0):
                        continue
                    n = engine.graph.n if engine.graph is not None else None
                    for key, state in engine.states.items():
                        engine._free(key, state, n)
                    engine._clear_filters()
                    if native.admit(width, height, fmt):
                        return
            finally:
                self._reclaiming = False
        raise MemoryError('Open Painter managed texture budget exhausted; close an unused texture set or reduce its resolution.')

    def texture(self, n, fmt=RGBA):
        import gpu
        native = self._resource_pool()
        if native is not None:
            texture = native.acquire(n, fmt)
            if texture is not None:
                return texture
            self._admit(native, n, n, fmt)
            texture = gpu.types.GPUTexture((n, n), format=fmt)
            native.register(texture, n, n, fmt)
            metrics.count('engine_texture_allocations')
            metrics.count('engine_texture_allocated_bytes', texture_bytes(n, n, fmt))
            return texture
        key = (n, fmt)
        free = self.pool.get(key)
        while free:
            texture = self._take_pooled(key)
            if texture.width == n and texture.height == n:
                self._leases[id(texture)] = texture_bytes(n, n, fmt)
                return texture   # (a texture released under another size is dropped, never reused)
            metrics.count('engine_pool_evictions')
        metrics.count('engine_texture_allocations')
        texture = gpu.types.GPUTexture((n, n), format=fmt)
        self._leases[id(texture)] = texture_bytes(n, n, fmt)
        metrics.count('engine_texture_allocated_bytes', self._leases[id(texture)])
        return texture

    def release(self, texture, n, fmt=RGBA):
        native = self._resource_pool()
        if native is not None:
            native.release(texture, n, fmt)
            return
        if texture is None or id(texture) in self._pooled_ids:
            return
        size = texture_bytes(n, n, fmt)
        self._leases.pop(id(texture), None)
        # Island-filter scratch and undo patches are returned inside long loops,
        # and failed evaluations never reach the normal end-of-call trim. Bound
        # free storage immediately; active leases are never eviction candidates.
        if size > max(0, POOL_BYTES) or POOL_SPARES <= 0:
            metrics.count('engine_pool_evictions')
            return
        key = (n, fmt)
        free = self.pool.setdefault(key, [])
        free.append(texture)
        self._pooled_ids.add(id(texture))
        self._pooled_bytes += size
        self.pool.move_to_end(key)
        while len(free) > POOL_SPARES:
            self._take_pooled(key, 0)
            metrics.count('engine_pool_evictions')
        self._trim_pool_bytes()

    def _take_pooled(self, key, index=-1):
        """Remove one free texture and its accounting, pruning empty buckets."""
        free = self.pool[key]
        texture = free.pop(index)
        n, fmt = key
        self._pooled_bytes -= texture_bytes(n, n, fmt)
        self._pooled_ids.remove(id(texture))
        if not free:
            del self.pool[key]
        return texture

    def _trim_pool_bytes(self):
        while self._pooled_bytes > max(0, POOL_BYTES):
            key = next(iter(self.pool))
            self._take_pooled(key, 0)
            metrics.count('engine_pool_evictions')

    def discard(self, texture):
        """Forget an uploaded texture whose owner is dropping its last reference."""
        native = getattr(self, '_native_pool', None)
        if native is not None:
            native.discard(texture)
            return
        self._leases.pop(id(texture), None)

    def memory(self):
        native = getattr(self, '_native_pool', None)
        if native is not None:
            return native.memory()
        return {'leased_bytes': sum(self._leases.values()),
                'pooled_bytes': self._pooled_bytes}

    def upload(self, array, fmt=RGBA):
        import gpu
        array = np.ascontiguousarray(array, np.float32)
        h, w = array.shape[:2]
        native = self._resource_pool()
        if native is not None:
            self._admit(native, w, h, fmt)
        texture = gpu.types.GPUTexture((w, h), format=fmt, data=gpu.types.Buffer('FLOAT', array.size, array.ravel()))
        if native is not None:
            native.register(texture, w, h, fmt)
        else:
            self._leases[id(texture)] = texture_bytes(w, h, fmt)
        metrics.count('engine_texture_allocations')
        metrics.count('engine_texture_uploads')
        metrics.count('engine_texture_uploaded_bytes', array.nbytes)
        metrics.count('engine_texture_allocated_bytes', self._leases[id(texture)])
        return texture

    def trim(self, spares=0):
        """Free pooled textures beyond ``spares`` per size and format (0: all of them, and the
        mip scratch publication keeps)."""
        if spares <= 0:
            self.drop_mip_scratch()
        native = self._resource_pool()
        if native is not None:
            native.trim(spares)
            return
        spares = max(0, spares)
        for key, free in list(self.pool.items()):
            while len(free) > spares:
                self._take_pooled(key)
                metrics.count('engine_pool_evictions')
        self._trim_pool_bytes()

    # ---- passes -------------------------------------------------------------------

    @contextmanager
    def state(self):
        import gpu
        viewport = gpu.state.viewport_get()
        scissor = gpu.state.scissor_get()
        blend = gpu.state.blend_get()
        depth = gpu.state.depth_test_get()
        depth_mask = gpu.state.depth_mask_get()
        try:
            gpu.state.blend_set('NONE')
            gpu.state.depth_test_set('NONE')
            gpu.state.depth_mask_set(False)
            yield
        finally:
            gpu.state.viewport_set(*viewport)
            gpu.state.scissor_set(*scissor)
            gpu.state.blend_set(blend)
            gpu.state.depth_test_set(depth)
            gpu.state.depth_mask_set(depth_mask)

    def _batch(self, shader):
        if self.batch is None:
            from gpu_extras.batch import batch_for_shader
            # One oversized triangle covers the target without a diagonal seam or
            # triangle-fan emulation on backends that do not support fans natively.
            self.batch = batch_for_shader(shader, 'TRIS', {'op_pos': ((-1, -1), (3, -1), (-1, 3))})
        return self.batch

    def draw(self, shader, targets, size, samplers=(), ubo=None, ints=None, mip=0, region=None):
        """One full-screen pass. A target is a texture or a {'texture': t, 'mip': k} slot."""
        import gpu
        slots = [({'texture': texture, 'mip': mip} if mip and not isinstance(texture, dict) else texture)
                 for texture in targets]
        # The binding's draw makes the calls below, in their order, from C: wherever the library
        # loads, whichever scheduler runs (OpenGL's default one included).
        from ...native_bridge.lifecycle import active_library, cpu_library
        lib = cpu_library() if BINDING_DRAWS else active_library()
        native = getattr(lib, '_op_binding', None) if lib is not None else None
        if native is not None:
            uniforms, block = self._interface(shader, samplers, ubo, ints)
            native.gpu_draw(shader, slots, size, samplers, ubo, ints, uniforms, block, self._batch(shader), region)
            self.passes += 1
            return
        attachments = [slot['texture'] if isinstance(slot, dict) else slot for slot in slots]
        if any(source is target for _, source in samplers for target in attachments):
            raise ValueError('A GPU pass cannot sample its render target')
        framebuffer = gpu.types.GPUFrameBuffer(color_slots=slots)
        uniforms, block = self._interface(shader, samplers, ubo, ints)
        with framebuffer.bind():
            viewport = region if region is not None else (0, 0, size, size)
            gpu.state.viewport_set(*viewport)
            # Keep the caller's scissor-test enable state; cover the entire target
            # whether clipping is enabled or disabled. state() restores its rectangle.
            gpu.state.scissor_set(*viewport)
            shader.bind()
            if 'op_inv_size' in uniforms:
                shader.uniform_float('op_inv_size', (1.0 / size, 1.0 / size))
            if ubo is not None and block:
                shader.uniform_block('op_params', ubo)
            for name, value in (ints or {}).items():
                if name in uniforms:
                    shader.uniform_int(name, value)
            for name, texture in samplers:
                if name in uniforms:
                    shader.uniform_sampler(name, texture)
            self._batch(shader).draw(shader)
        self.passes += 1

    @property
    def pixel_publish(self):
        """Results reach the images through their pixel buffers, not a framebuffer pass: when
        forced (PIXEL_PUBLISH), on the Vulkan fallback route (VULKAN_PIXEL_ROUTE), and in a
        background Vulkan session (BACKGROUND_PIXELS). Every backend renders into the images'
        GPU textures in a window."""
        if PIXEL_PUBLISH is not None:
            return PIXEL_PUBLISH
        return self.backend == 'VULKAN' and (VULKAN_PIXEL_ROUTE or (BACKGROUND_PIXELS and _background()))

    def readback(self, texture, n, gray=False):
        """The n × n ``texture`` as RGBA floats in a buffer reused between publications: a 2K
        channel is 64 MB, allocated once rather than on every stroke tick.

        Read compact textures with their actual component count: Vulkan does not expand
        R/RG framebuffer reads to RGBA. Missing components use (0, 0, 0, 1), as OpenGL
        does; ``gray`` reads an R texture as (r, r, r, 1), a scalar publication's layout.
        The returned buffer is borrowed and is overwritten by the next readback.
        """
        import gpu
        buffer = self._readback_buffer(n)
        components = {'R16F': 1, 'R32F': 1, 'RG16F': 2, 'RG32F': 2}.get(getattr(texture, 'format', ''), 4)
        transfer = buffer
        if components != 4:
            if self._compact_readback is None or self._compact_readback[:2] != (n, components):
                self._compact_readback = (n, components, gpu.types.Buffer('FLOAT', n * n * components))
            transfer = self._compact_readback[2]
        metrics.count('engine_texture_readbacks')
        metrics.count('engine_texture_readback_bytes', n * n * components * 4)
        framebuffer = gpu.types.GPUFrameBuffer(color_slots=(texture,))
        with self.state(), framebuffer.bind():
            framebuffer.read_color(0, 0, n, n, components, 0, 'FLOAT', data=transfer)
        self.settled = self.passes   # a read waits for every pass before it
        if components != 4:
            from .compact_readback import expand_compact
            expand_compact(transfer, buffer, components, gray and components == 1)
        return buffer

    def _readback_buffer(self, n):
        """Borrow one resolution's RGBA transfer buffer, also used for uniform CPU fills."""
        import gpu
        buffer = self._readbacks.get(n)
        if buffer is None:
            self._readbacks.clear()   # one resolution at a time
            self._compact_readback = None
            buffer = self._readbacks[n] = gpu.types.Buffer('FLOAT', n * n * 4)
        return buffer

    def readback_sampled(self, texture, n):
        """Read the exact strided texels needed by a bounded brush preview.

        Preview reduction is point sampling, not mip filtering. Copy those
        texels in the source format before transfer, preserving both precision
        and R/RG default-component behavior without expanding the full image.
        As with readback(), the returned buffer is borrowed.
        """
        size = texture.width
        if n <= 0 or texture.height != size or size < n or size % n:
            raise ValueError('Preview readback needs an integer reduction of a square texture')
        if size == n:
            return self.readback(texture, n)
        fmt = texture.format
        if fmt not in {'R16F', 'R32F', 'RG16F', 'RG32F', 'RGBA16F', 'RGBA32F'}:
            raise ValueError(f'Unsupported preview source format: {fmt}')
        target = self.texture(n, fmt)
        try:
            with self.state():
                self.draw(self.fixed('sample_pixels'), [target], n,
                          [('op_source', texture)], ints={'op_step': size // n})
                return self.readback(target, n)
        finally:
            self.release(target, n, fmt)

    def finish(self):
        """Wait for every pass submitted so far (a profile then times each pass alone)."""
        import gpu
        texture = self.texture(1, SCALAR)
        framebuffer = gpu.types.GPUFrameBuffer(color_slots=(texture,))
        try:
            with self.state(), framebuffer.bind():
                _wait(framebuffer)
            self.settled = self.passes
        finally:
            self.release(texture, 1, SCALAR)

    def mip_scratch(self, owner, lane, n, fmt):
        """A texture publication reduces a mip level through, kept between publications: one per
        lane (the channels whose mips share a draw), level and format. Leasing one from the pool
        and returning it costs two calls into the native registry and their callbacks: about
        1 ms per published channel at 1K. These stay leased, so the device budget counts them.

        ``owner`` (an engine) holds them from its first queued channel until its mips are drawn
        (``end_mip_scratch``); None while another engine's mips are queued, and the caller leases
        its own. Nothing is dropped while they are held: ``end_mip_scratch`` drops them once
        they outgrew their budget, ``trim(0)`` and device pressure when idle."""
        if self._mip_scratch_owner is not None and self._mip_scratch_owner is not owner:
            return None
        self._mip_scratch_owner = owner
        key = (lane, n, fmt)
        texture = self._mip_scratch.get(key)
        if texture is None:
            texture = self._mip_scratch[key] = self.texture(n, fmt)
            self._mip_scratch_bytes += texture_bytes(n, n, fmt)
        return texture

    def end_mip_scratch(self, owner):
        """``owner``'s queued mips are drawn: the kept scratch is free for the next publication."""
        if self._mip_scratch_owner is owner:
            self._mip_scratch_owner = None
            if self._mip_scratch_bytes > MIP_SCRATCH_BYTES:
                self.drop_mip_scratch()   # (two sides of every lane, at most, stay)

    def drop_mip_scratch(self):
        """Release the kept scratch unless queued mips use it; True when it was released."""
        if self._mip_scratch_owner is not None:
            return False
        for (_, n, fmt), texture in self._mip_scratch.items():
            self.release(texture, n, fmt)
        self._mip_scratch.clear()
        self._mip_scratch_bytes = 0
        return True

    def settle(self):
        """Wait for the passes submitted since the last wait. Blender's Vulkan backend destroys
        the GPU resources released meanwhile (textures a resize or a pool trim let go, upload
        staging) only once the work that used them is known to be complete: at a window's frame
        end, or at a wait. A background session draws no frames, so without a wait every
        resolution switch kept its old textures (1K switches grew by 0.5 GB each)."""
        if self.passes != self.settled:
            self.finish()

    def calibrate(self, samples=7):
        """The cost of submitting a pass that does nothing and waiting for it (a 1×1 draw:
        about half a millisecond on Metal), subtracted from a profiled publication."""
        target, source = self.texture(1, SCALAR), self.texture(1, SCALAR)
        passes, costs = self.passes, []
        try:
            with self.state():
                self.finish()
                for index in range(samples + 1):
                    started = perf_counter()
                    self.draw(self.fixed('copy'), [target], 1, [('op_source', source)])
                    self.finish()
                    if index:   # the first draw warms the pipeline
                        costs.append((perf_counter() - started) * 1000)
        finally:
            self.passes = passes
            self.release(target, 1, SCALAR)
            self.release(source, 1, SCALAR)
        self.wait_cost = sorted(costs)[len(costs) // 2]
        return self.wait_cost

    def fill(self, texture, size, color):
        import gpu
        framebuffer = gpu.types.GPUFrameBuffer(color_slots=(texture,))
        with self.state(), framebuffer.bind():
            gpu.state.viewport_set(0, 0, size, size)
            gpu.state.scissor_set(0, 0, size, size)
            framebuffer.clear(color=tuple(color))


def _has_uniform(shader, name, *, block=False):
    """Only declared resources are queried; a driver may optimize them away."""
    try:
        location = (shader.uniform_block_from_name(name) if block else shader.uniform_from_name(name))
        return location >= 0
    except ValueError:
        return False


def _background():
    """No window shows the images: a background Blender, or none at all (the bpy-free unit tests)."""
    try:
        import bpy
    except ImportError:
        return True
    return bpy.app.background


def _wait(framebuffer):
    """Read one texel back: the GPU finishes the work queued before it (Blender exposes no fence)."""
    try:
        framebuffer.read_color(0, 0, 1, 1, 1, 0, 'FLOAT')
    except Exception:
        pass


_device = None


def device():
    global _device
    if _device is None:
        _device = Device()
    return _device


def forget_device():
    global _device
    from ...painting.gpu_sources import clear
    clear()
    if _device is not None:
        _device.shaders.close()
    if _device is not None and getattr(_device, '_native_pool', None) is not None:
        _device._native_pool.close()
    _device = None
    Engine._constants.clear()


class UnitState:
    __slots__ = ('unit', 'program', 'shader', 'signature', '_stamp', '_textures', 'formats', 'ubo', 'ubo_data',
                 'slot', 'carried')

    def __init__(self, unit):
        self.unit = unit
        self.program = self.shader = self.signature = self._stamp = None
        self._textures = None
        self.formats = ()
        self.ubo = self.ubo_data = None
        self.slot = None   # the native graph's mirror of stamp and residency (NativeGraph)
        # (predecessor state, [(producer key, producer unit)] compiled into its program) of a
        # carried result (``Engine.adopt``): its program is rebound to this unit's tree when they
        # are the same program, else the result is only a render target (``_state``).
        self.carried = None

    @property
    def stamp(self):
        return self._stamp

    @stamp.setter
    def stamp(self, value):
        self._stamp = value
        if self.slot is not None:
            self.slot.sync(self)

    @property
    def textures(self):
        return self._textures

    @textures.setter
    def textures(self, value):
        self._textures = value
        if self.slot is not None:
            self.slot.sync(self)

    def uniforms(self):
        """The unit's uniform buffer holding its current parameter values (None without any)."""
        import gpu
        program = self.program
        if not program.params and not program.objects:
            return None
        native = getattr(program, 'native_program', None)
        if native is not None:
            native.update(program)
            data = native.data()
            values = np.frombuffer(data, np.float32)
            if self.ubo is None or self.ubo_data is None or len(self.ubo_data) != len(data):
                self.ubo = gpu.types.GPUUniformBuf(gpu.types.Buffer('FLOAT', len(values), values))
            elif self.ubo_data != data:
                self.ubo.update(gpu.types.Buffer('FLOAT', len(values), values))
            self.ubo_data = data
            return self.ubo
        data = [float(socket.default_value) for socket in program.params]
        data += [0.0] * (max(1, program.parameter_slots) * 4 - len(data))
        for obj in program.objects:
            data.extend(_object_matrix(obj))
        data = tuple(data)
        if self.ubo is None or self.ubo_data is None or len(self.ubo_data) != len(data):
            self.ubo = gpu.types.GPUUniformBuf(gpu.types.Buffer('FLOAT', len(data), data))
        elif self.ubo_data != data:
            self.ubo.update(gpu.types.Buffer('FLOAT', len(data), data))
        self.ubo_data = data
        return self.ubo


def revisions_fit(n):
    """Whether native revisions are worth their memory at n x n (on REVISION_BACKENDS). A revision
    commit keeps every channel's previous mip chain and the next one, and pins each final source
    until the batch commits; that is what the retained-result budget is sized to hold. Where it
    is not (2K on a machine under 16 GB, 4K anywhere), each channel publishes straight into its
    image, as on Vulkan and OpenGL: the same pixels, without the all-or-nothing rollback."""
    return n * n * 16 * 8 <= RETAIN_BYTES


class InPlacePresentation:
    """Eviction recovery for channels published straight into their images (no native revision).

    Blender recreates an image's GPU texture from its CPU pixels after evicting it (Texture Time
    Out, ``gl_free``), and those lag behind a framebuffer publication. The texture a channel was
    published into stays alive while its public wrapper is held, so before the next viewport draw
    (or on the session tick) each of its mips is copied into the recreated one. Holding the
    wrapper costs nothing until an eviction, and then only the old texture until the copy."""
    in_place = True         # restored whether or not the native scheduler runs (lifecycle)
    survives_undo = True    # an undo keeps the images; a load closes it

    def __init__(self, dev, scene_uid):
        self.dev, self.scene_uid = dev, scene_uid
        self.targets = {}   # channel -> (image, its pointer, the wrapper it was published into)

    def published(self, channel, image, target):
        self.targets[channel] = image, image.as_pointer(), target

    def forget(self, channel):
        self.targets.pop(channel, None)

    def close(self):
        self.targets.clear()

    def restore_evicted(self):
        if not self.targets:
            return
        import gpu
        replacements = []
        for channel, (image, pointer, previous) in tuple(self.targets.items()):
            try:
                if image.as_pointer() != pointer:
                    raise ReferenceError
                target = gpu.texture.from_image(image)
            except ReferenceError:
                self.targets.pop(channel)
                continue
            if target is previous:
                continue
            if (target.width, target.height, target.format) != (previous.width, previous.height, previous.format):
                self.targets.pop(channel)   # resized: the resolution rebuild publishes it again
                continue
            replacements.append((channel, image, pointer, previous, target))
        if not replacements:
            return
        shader = self.dev.fixed('copy_mip')
        with self.dev.state():
            for channel, image, pointer, previous, target in replacements:
                size, level = previous.width, 0
                while True:
                    self.dev.draw(shader, [{'texture': target, 'mip': level}], size,
                                  [('op_source', previous)], ints={'op_level': level})
                    if size == 1:
                        break
                    size, level = max(1, size // 2), level + 1
                self.targets[channel] = image, pointer, target
                metrics.count('engine_image_restorations')


def _publication_format(channel, precise, gray=False):
    """The texture a channel is published from. Through pixels (Vulkan) a gray publication (a
    scalar channel: (v, v, v, 1) by construction) renders only its one component, which cuts the
    read-back bytes and Blender's half-to-float conversion to a quarter; the readback expands it
    to RGBA on the CPU (op_expand_compact). Every component of the RGBA layout held that same
    half or float, so the published pixels are the same."""
    high = precise or channel == 'HEIGHT'
    if gray and COMPACT_PIXEL_PUBLISH:
        return 'R32F' if high else 'R16F'
    return RGBA if high else HALF


def _publication_program(program, scalar=False, donor=False):
    """Fuse a non-normal final CHANNEL with its clamp/padding publication pass.

    The last intermediate and publication target have the same precision. For
    finite RGB, clamping to [0, 1] commutes with that last half/full store, so no
    intermediate quantization is lost. NORMAL cannot use this shortcut: its
    normalization must read the already-quantized intermediate.

    Only the translator's known coordinate prologue is rewritten. A future
    program using fragment coordinates elsewhere falls back rather than sampling
    a donor's inputs with the destination pixel's coordinates. The native
    compiler's programs open with helper functions, so the prologue is found
    where main begins rather than at the start of the source.
    """
    prefix = 'void main() {\n  ivec2 op_texel = ivec2(gl_FragCoord.xy);\n'
    if (len(program.targets) != 1 or program.source.count(prefix) != 1
            or program.source.count('gl_FragCoord') != 1):
        return None
    coordinates = 'void op_channel() {\n  ivec2 op_texel = ivec2(gl_FragCoord.xy);\n'
    samplers = list(program.samplers)
    if donor:
        coordinates += ('  vec2 donor = texelFetch(op_final_donor, op_texel, 0).xy;\n'
                        '  if (donor.x >= 0.0) { op_texel = ivec2(donor); }\n')
        samplers.append(glsl.Sampler('op_final_donor', 'publication_donor'))
    source = program.source.replace(prefix, coordinates, 1).replace('op_out0', 'op_channel_value')
    rgb = 'vec3(op_channel_value.r)' if scalar else 'op_channel_value.rgb'
    source = ('vec4 op_channel_value;\n' + source + '\nvoid main() {\n  op_channel();\n'
              f'  op_out0 = vec4(clamp({rgb}, 0.0, 1.0), 1.0);\n}}\n')
    return replace(program, source=source, samplers=samplers)


def _image_identity(image, epochs):
    if image is None:
        return None
    try:
        # A CPU boundary leaf is published in place (placeholder → pixels, or re-keyed and
        # regenerated): its readiness and publication serial are part of its identity.
        return (image.as_pointer(), image.name, tuple(image.size), image.get('open_painter_shader_resource'),
                bool(image.get('op_shader_resource_ready')), image.get('op_resource_serial', 0),
                image.get('open_painter_pixel_revision', 0), epochs.get(image.as_pointer(), 0),
                image.alpha_mode, colorspace_name(image), image.is_float)
    except ReferenceError:
        return None


def _object_matrix(obj):
    try:
        matrix = obj.matrix_world.inverted_safe()
    except (ReferenceError, AttributeError):
        return (1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0)
    # Column-major, as GLSL reads a mat4 uniform.
    return tuple(float(matrix[row][column]) for column in range(4) for row in range(4))


class Engine:
    """The evaluator of one texture set: unit programs, retained results, publication."""

    def __init__(self, scene, ts):
        self.scene = scene
        self.scene_uid = getattr(scene, 'session_uid', None)
        self.set_id = ts.id
        self.graph = None
        self.states = {}
        self.filter_states = OrderedDict()
        self.positions = {}             # CHANNEL key -> (backdrop ref, grouped ref)
        self.finals = {}                # channel -> ref of the chain's result
        self.orders = {}                # channel -> CHANNEL keys in render order
        self.keep = set()               # CHANNEL results kept between evaluations (checkpoints)
        # Unchanged CHANNEL results that changed units read in the last evaluation that changed
        # anything: the next tick of the same drag reads them again (_update_frontier).
        self._frontier = set()
        self._reads = self._changed = None   # being recorded during an evaluation (_note_reads)
        self._reads_prearmed = None          # (layer, root stamps) whose reads idle arming kept
        self.retained = OrderedDict()   # SRC/MASK (and imported CHANNEL) results kept, LRU order
        self.published = {}             # channel -> stamp of the published result
        self.images = {}                # channel -> image pointer it was published into
        self.geometry = {}              # kind -> texture
        self.geometry_key = None
        self.padding = None             # (key, donor texture)
        self.pad = True
        self.precise = PRECISE          # full-float intermediates for every channel (parity tests)
        self.inline = frozenset()       # SRC/MASK units compiled into their single consumer
        self.report = {}
        self.stages = stages.evaluation()   # the running evaluation's time by stage
        self.profiling = False          # time every pass on the GPU (``profile``)
        self.rendered = []
        self._in_use = Counter()
        self._surface_key = None        # (geometry key,) of the running evaluation
        self.dirty_cpu = set()          # channels whose image pixels lag behind the GPU result
        self._uniform_cpu = {}          # channel -> known CPU constant (image pointer, side, RGBA, revision)
        self._constant_renderer = None
        self._publication_programs = {} # unit + padding -> (original program, fused program)
        self.waiting = {}               # channel -> when its change was first held back (pixel budget)
        self.deferred = set()           # channels the last evaluation held back for the next
        self._state_cache = None        # only live inside one synchronous evaluation
        self._native_graph = None
        self._native_stamps = None
        self._native_revisions = None
        self._native_channels = None
        self._native_cache = None
        self._presentation = None       # eviction recovery of channels published in place
        self._pending_mips = []         # (image texture, level-0 source, n, format, kept) awaiting their mips
        self._kept_finals = False       # finals were kept this evaluation: trim the cache once it ends
        self._fused_finals = {}         # final published fused -> (its collapsed run or None, its reads)
        self._fused_reads = Counter()   # what those read: held until an idle tick keeps the finals
        self._evaluation_depth = 0
        self.replaced = None            # units a patch replaced, until the native graph takes them
        self._chains = None             # the graph chains ``positions`` were derived from
        from .suffix import SuffixCache
        self._suffix = SuffixCache()    # armed affine runs above an edited layer
        self._field_twins = {}          # FIELD content -> the FIELD whose texture holds it (_copy_field)
        self._field_contents = {}       # FIELD key -> the content its texture holds
        self._field_copies = set()      # FIELDs filled by a copy: released once their consumers drew
        device().engines.add(self)

    # ---- graph -----------------------------------------------------------------------

    def adopt(self, graph):
        """Take a (re)built graph; states of units it adopted unchanged keep their programs and results.

        The graph this engine already holds, patched in place (``EngineGraph.patch``): only the
        replaced units lose their states, filter results and publication variants, and the
        native graph learns which (``replaced``) instead of describing every unit again."""
        if self._native_channels is not None and self._native_channels.dispatching:
            raise RuntimeError("Cannot adopt a graph during native channel submission")
        if self._evaluation_depth:
            raise RuntimeError("Cannot adopt a graph during an evaluation")
        patched = graph is self.graph
        n_before = self.graph.n if self.graph is not None else None
        previous, previous_inline = self.graph, self.inline
        self._clear_constant_renderer()
        # Suffix transforms survive: run keys identify members by content, not by
        # position in the graph (a resolution change closes them below).
        self.graph = graph
        self._native_stamps = None
        if not patched:
            self._publication_programs.clear()
            self._clear_filters()
        if n_before is not None and n_before != graph.n:
            self.close(n_before)   # the textures were rendered at the previous resolution
        take = getattr(graph, 'take_unit_changes', None)
        changes = take() if take is not None else None
        replaced = set()
        if not patched or changes is None or n_before != graph.n:
            live = {}
            carry = CARRY_RESULTS and not patched and n_before == graph.n
            for key, unit in graph.units.items():
                state = self.states.get(key)
                if state is None or state.unit is not unit:
                    fresh = UnitState(unit)
                    if state is not None and carry:
                        # A rebuilt graph (an undo, a declined patch): the unit starts from its
                        # predecessor's program, rebound to its tree when it is the same one
                        # (``_rebound``), and from its result and stamp, which the native graph
                        # renders again only where its content stamp differs.
                        fresh.carried = (state, [(producer, previous.units[producer]) for producer in
                                                 _inlined_in(previous, previous_inline, key)])
                        if state.textures:
                            fresh._textures, fresh.formats, fresh._stamp = state.textures, state.formats, state.stamp
                            state._textures, state.slot = None, None
                            metrics.count('engine_carried_results')
                        else:
                            self._retire_state(key, state, n_before, patched)
                    elif state is not None:
                        self._retire_state(key, state, n_before, patched)
                    state = fresh
                    replaced.add(key)
                live[key] = state
            for key, state in self.states.items():
                if key not in live:
                    self._retire_state(key, state, n_before, patched)
                    replaced.add(key)
            self.states = live
            # A content or mask unit read by one unit only is compiled into that unit: one pass
            # and one full-resolution texture less for every layer channel.
            inline = frozenset(key for key in graph.units if key[0] in {'SRC', 'MASK'}
                               and len(graph.consumers.get(key, ())) == 1)
        else:
            # Patches since the last adoption changed only these units: the others keep their
            # states, and only the producers the changed units import (now or before) can have
            # gained or lost a consumer.
            imported = set()
            for key in changes:
                unit, state = graph.units.get(key), self.states.get(key)
                if state is not None and state.unit is unit:
                    continue
                if state is not None:
                    imported.update(producer for producer, _, _ in state.unit.imports)
                    self._retire_state(key, state, n_before, patched)
                    del self.states[key]
                if unit is not None:
                    imported.update(producer for producer, _, _ in unit.imports)
                    self.states[key] = UnitState(unit)
                replaced.add(key)
            inline = set(self.inline)
            for key in imported | replaced:
                if key in graph.units and key[0] in {'SRC', 'MASK'} and len(graph.consumers.get(key, ())) == 1:
                    inline.add(key)
                else:
                    inline.discard(key)
            if VERIFY_ADOPTION:
                self._verify_adoption(graph, inline)
        if not patched or graph.chains is not self._chains:
            # Positions follow the chains alone: a patch that kept them keeps these.
            self.positions, self.finals, self.orders = _positions(graph)
            self._chains = graph.chains
        # An unchanged inline set keeps its identity, which the native graph's description is
        # checked against.
        if inline != self.inline:
            self.inline = frozenset(inline)
        self.keep.intersection_update(self.states)
        self._frontier.intersection_update(self.states)
        if patched:
            self.replaced = replaced if self.replaced is None else self.replaced | replaced
        else:
            self.replaced = None
            self.published.clear()   # a rebuilt graph re-derives every chain stamp

    def _verify_adoption(self, graph, inline):
        """OP_VERIFY_STRUCTURE: an adoption of the patched units only must leave what a full one does."""
        stale = sorted(str(key) for key in graph.units.keys() ^ self.states.keys())
        stale += sorted(str(key) for key, unit in graph.units.items()
                        if key in self.states and self.states[key].unit is not unit)
        full = {key for key in graph.units if key[0] in {'SRC', 'MASK'} and len(graph.consumers.get(key, ())) == 1}
        if stale or set(inline) != full:
            raise AssertionError(f'Incremental adoption differs: states {stale[:6]}, '
                                 f'inline {sorted(map(str, set(inline) ^ full))[:6]}')

    def take_replaced(self):
        """Units a patch replaced since the native graph last looked; None after a rebuild."""
        replaced, self.replaced = self.replaced, None
        return replaced

    def _retire_state(self, key, state, n, patched):
        if patched:
            # Its filter results and publication variants go with it; every other unit keeps its own.
            for program in glsl.programs(state.program) if state.program is not None else ():
                for sampler in program.samplers:
                    if sampler.kind == 'filter':
                        held = self.filter_states.pop(sampler.node, None)
                        if held is not None:
                            device().release(held[1], held[2], RGBA)
            for program_key in [k for k in self._publication_programs if k[0] == key]:
                del self._publication_programs[program_key]
        self._free(key, state, n)
        state.slot = None
        if key[0] == 'FIELD':
            self._forget_field(key)
            self._field_copies.discard(key)

    def _free(self, key, state, n):
        if state.textures and n is not None:
            for texture, fmt in zip(state.textures, state.formats):
                device().release(texture, n, fmt)
        state.textures = None
        self.retained.pop(key, None)
        if self._native_cache is not None:
            self._native_cache.remove(key)

    def close(self, n=None):
        """Release every unit's textures to the pool, under ``n`` (default: the graph's side): the
        resolution they were created at, or a later request would be handed a texture of another size."""
        if self._evaluation_depth:
            # A callback inside a draw or a publication (any backend) must not free the
            # states the evaluation is using.
            raise RuntimeError("Cannot close an engine during an evaluation")
        if self._native_channels is not None:
            self._native_channels.close()
            self._native_channels = None
        n = n if n is not None else (self.graph.n if self.graph is not None else None)
        for key, state in list(self.states.items()):
            self._free(key, state, n)
        self._clear_filters()
        self.states = {}
        if self._native_graph is not None:
            self._native_graph.close()
            self._native_graph = None
        self._native_stamps = None
        if self._native_revisions is not None:
            self._native_revisions.close()
            self._native_revisions = None
        if self._presentation is not None:
            self._presentation.close()
        if self._native_cache is not None:
            self._native_cache.close()
            self._native_cache = None
        self._suffix.clear()
        self._field_twins.clear()
        self._field_contents.clear()
        self._field_copies.clear()
        self.retained.clear()
        self.keep.clear()
        self._frontier.clear()
        self.replaced = None
        self.published = {}
        self.images = {}
        self._uniform_cpu.clear()
        self._clear_constant_renderer()
        self._publication_programs.clear()
        self._clear_geometry()
        if self.padding is not None and self.padding[1] is not None:
            device().discard(self.padding[1])
        self.geometry = {}
        self.geometry_key = None
        self.padding = None

    def _clear_constant_renderer(self):
        if self._constant_renderer is not None:
            self._constant_renderer.close()
            self._constant_renderer = None

    def _clear_filters(self):
        for _, texture, n in self.filter_states.values():
            device().release(texture, n, RGBA)
        self.filter_states.clear()

    # ---- programs ----------------------------------------------------------------------

    def _signature(self, key):
        # Code depends on the unit's node structure, the producers compiled into it and the
        # images it binds (alpha handling). An inlined producer counts by its compiled tree: a
        # mask or source rebuilt under an adopted consumer (a mask's kind changed) must reach
        # the consumer's program, whose code and uniform sockets came from the old tree. The
        # tree itself is held (compared by identity), so a freed tree's id is never matched.
        from ...native_bridge.lifecycle import active_library
        units = self.graph.units
        return (id(self.states[key].unit.tree), tuple((producer, units[producer].tree) for producer in self._inlined(key)),
                self.precise, bool(active_library()), tuple((_alpha_signature(image), binding(node).mapping if binding(node) else None)
                                    for node, image in self._images(key)))

    def _state(self, key):
        cache = self._state_cache
        cached = cache.get(key) if cache is not None else None
        if cached is not None and cached[0] is self.inline and cached[1] == self.precise:
            metrics.count('engine_state_cache_hits')
            return cached[2]
        metrics.count('engine_state_signature_checks')
        state = self.states[key]
        unit = state.unit
        signature = self._signature(key)
        if state.signature != signature:
            started, compiled = perf_counter(), device().compile_seconds
            carried, state.carried = state.carried, None
            rebound = self._rebound(key, state, signature, *carried) if carried is not None else None
            if rebound is not None:
                state.program, state.shader, state.signature = rebound, carried[0].shader, signature
                if state.textures is None:
                    state.formats = carried[0].formats
                metrics.count('engine_rebound_programs')
                if cache is not None:
                    cache[key] = (self.inline, self.precise, state)
                return state
            program = glsl.translate(self.graph, unit, self.inline)
            limit = device().max_samplers
            while max(len(p.samplers) for p in glsl.programs(program)) > limit:
                # Over the GPU's textures per pass: the inlined producer carrying the most
                # textures becomes a pass of its own, until the program fits.
                direct = [producer for producer, _, _ in unit.imports if producer in self.inline]
                if not direct:
                    raise ValueError(f'"{unit.tree.name}" samples {len(program.samplers)} textures; '
                                     f'this GPU allows {limit} in one pass')
                self.inline = self.inline - {max(direct, key=lambda producer: len(self._images(producer)))}
                program = glsl.translate(self.graph, unit, self.inline)
                signature = self._signature(key)
            state.program = program
            state.shader = device().shader(state.program)
            state.signature = signature
            formats = tuple(self._format(key, color) for color, _ in state.program.targets)
            if state.textures and formats != state.formats:
                # Targets of another layout: the next render allocates them.
                n = self.graph.n
                for texture, fmt in zip(state.textures, state.formats):
                    device().release(texture, n, fmt)
                state.textures = None
            elif state.textures:
                # Same layout: the targets stay for the next render, but their content stands only
                # with the program it was rendered by (a new program restarts its uniform revision).
                state.stamp = None
            state.formats = formats
            compiled = device().compile_seconds - compiled
            self.stages['compile_ms'] += compiled * 1000
            self.stages['translate_ms'] += (perf_counter() - started - compiled) * 1000
        if cache is not None:
            cache[key] = (self.inline, self.precise, state)
        return state

    @contextmanager
    def _evaluation_states(self):
        """Share prepared states across stamping and drawing this synchronous call.

        Bindings can change between calls without rebuilding the graph. Nothing
        survives the scope, including after failures. Sampler-limit decisions
        change ``inline``; cached entries from before that decision are rechecked.
        """
        previous = self._state_cache
        self._evaluation_depth = getattr(self, '_evaluation_depth', 0) + 1
        self._state_cache = {} if CACHE_EVALUATION_STATES else None
        try:
            yield
        finally:
            self._state_cache = previous
            self._evaluation_depth -= 1

    def _inlined(self, key):
        """The producers compiled into ``key``'s program, nearest first."""
        return _inlined_in(self.graph, self.inline, key)

    def _rebound(self, key, state, signature, prior, inlined):
        """The predecessor's program on this unit's tree, when it is the same program: the unit
        and every producer compiled into it sign the same and their trees have the same shape,
        and its images sign the same. None: translate (a carried result is then only a target)."""
        program = prior.program
        if program is None or prior.shader is None or prior.signature is None:
            return None
        if any(part.objects for part in glsl.programs(program)):
            return None   # object uniforms name RNA objects, which an undo replaced
        if prior.unit.signature is None or prior.unit.signature != state.unit.signature:
            return None
        if [producer for producer, _ in inlined] != self._inlined(key) or prior.signature[2:] != signature[2:]:
            return None
        units = self.graph.units
        pairs = [(prior.unit, state.unit), *((unit, units[producer]) for producer, unit in inlined)]
        if any(old.signature is None or old.signature != new.signature for old, new in pairs[1:]):
            return None
        nodes, sockets = {}, {}
        if not all(_same_shape(old.tree, new.tree, nodes, sockets) for old, new in pairs):
            return None
        rebound = _rebind(program, nodes, sockets)
        if rebound is not None and VERIFY_REBIND:
            fresh = glsl.translate(self.graph, state.unit, self.inline)
            mine = [(p.source, [id(s) for s in p.params], [(s.kind, id(s.node), s.producer) for s in p.samplers],
                     p.targets, p.imports) for p in glsl.programs(rebound)]
            theirs = [(p.source, [id(s) for s in p.params], [(s.kind, id(s.node), s.producer) for s in p.samplers],
                       p.targets, p.imports) for p in glsl.programs(fresh)]
            if mine != theirs:
                raise AssertionError(f'A rebound program of {key} differs from its translation')
        return rebound

    def _images(self, key):
        """Image nodes of a unit and of the producers compiled into it."""
        return [item for current in (key, *self._inlined(key)) for item in self.graph.unit_images(current)]

    def _format(self, key, color):
        return layout(key, color, self.precise)

    # ---- stamps --------------------------------------------------------------------------

    def _own_stamp(self, state, epochs):
        return id(state.shader), self._program_stamp(state.program, epochs, self._param_free(state))

    def _param_free(self, state):
        """Whether a unit's result cannot depend on its parameters: a folder step whose children
        leave it nothing in this channel (its group input is the constant transparent composite)
        blends with zero alpha over its backdrop, whatever its opacity and masks. Each such
        program is checked once on the GPU (``_check_param_free``) the first time a stamp asks,
        so every stamp of it agrees, an incremental restamp and a full one alike. Its content
        stamp then leaves its parameters out, so dragging them renders and publishes nothing in
        that channel."""
        if not PARAM_FREE or state.unit is None or state.program is None:
            return False
        key = state.unit.key
        if key[0] != 'CHANNEL':
            return False
        position = self.positions.get(key)
        if not position or position[1] != EMPTY_GROUP:
            return False
        checked = device().__dict__.setdefault('param_free', {})
        source = state.program.source
        found = checked.get(source)
        if found is None:
            found = checked[source] = self._check_param_free(state)
        return found

    def _check_param_free(self, state):
        """Render a corner of the unit with its parameters, with all zeros, all ones and random
        values in [0, 1); True when all four agree bit for bit. The inputs are synthetic: noise
        for every texture it reads, the transparent composite for its group (independence from
        the parameters holds for any of them)."""
        import gpu
        program = state.program
        native = getattr(program, 'native_program', None)
        if (native is None or len(state.formats) != 1 or program.objects or self.graph is None
                or any(s.source is not None or s.kind == 'filter' for s in program.samplers)):
            return False
        native.update(program)
        values = np.frombuffer(bytes(native.data()), np.float32)
        if not len(values):
            return False
        random = np.random.default_rng(1).random(len(values), dtype=np.float32)
        dev, fmt, n = device(), state.formats[0], self.graph.n
        size = min(n, 64)
        noise = dev.__dict__.get('_param_free_noise')
        if noise is None or noise.width != size:
            data = np.random.default_rng(2).random(size * size * 4, dtype=np.float32)
            noise = dev._param_free_noise = gpu.types.GPUTexture(
                (size, size), format=RGBA, data=gpu.types.Buffer('FLOAT', len(data), data))
        transparent = self._constant(n, (0.0, 0.0, 0.0, 0.0))
        samplers = [(s.name, transparent if s.kind == 'grouped' else noise) for s in program.samplers]
        components = 2 if fmt in ('RG16F', 'RG32F') else 4
        results = []
        for variant in (values, np.zeros_like(values), np.ones_like(values), random):
            ubo = gpu.types.GPUUniformBuf(gpu.types.Buffer('FLOAT', len(variant), variant))
            target = dev.texture(size, fmt)
            try:
                dev.draw(state.shader, [target], n, samplers, ubo, region=(0, 0, size, size))
                framebuffer = gpu.types.GPUFrameBuffer(color_slots=(target,))
                with framebuffer.bind():
                    buffer = framebuffer.read_color(0, 0, size, size, components, 0, 'FLOAT')
                buffer.dimensions = size * size * components
                results.append(np.array(buffer, np.float32).view(np.uint32))
            finally:
                dev.release(target, size, fmt)
        dev.settled = dev.passes
        metrics.count('engine_param_free_checks')
        return all(np.array_equal(results[0], other) for other in results[1:])

    def _program_stamp(self, program, epochs, param_free=False):
        from .filters import signature
        programs = tuple(glsl.programs(program))
        samplers = [s for p in programs for s in p.samplers]
        surface = (self._surface() if any(s.kind in GEOMETRY or
                   (s.kind == 'filter' and getattr(s.node.effect, 'space', 'UV') == 'SURFACE') for s in samplers) else None)
        numeric = () if param_free else tuple(
            (p.native_program.update(p) if getattr(p, 'native_program', None) is not None
             else tuple(float(socket.default_value) for socket in p.params)) for p in programs)
        return (numeric,
                tuple((binding(s.node).stamp if binding(s.node) is not None else _image_identity(s.node.image, epochs))
                      for s in samplers if s.kind == 'image'),
                tuple(_object_matrix(obj) for p in programs if getattr(p, 'native_program', None) is None for obj in p.objects), surface,
                tuple(signature(s.node.effect) for s in samplers if s.kind == 'filter'))

    def _surface(self):
        """The geometry key of this evaluation (computed once per evaluation)."""
        if self._surface_key is None:
            self._surface_key = (self._geometry_stamp(self.graph.n),)
        return self._surface_key[0]

    def _stamp(self, key, memo, epochs, stack=()):
        if key in memo:
            return memo[key]
        if self._native_stamps is not None and key in self._native_stamps:
            memo[key] = self._native_stamps[key]
            return memo[key]
        if key in stack:
            raise ValueError('Published inputs contain a reference cycle')
        state = self._state(key)
        stack = stack + (key,)
        imports = tuple(sorted({sampler.producer[0] for p in glsl.programs(state.program)
                                for sampler in p.samplers if sampler.kind == 'import'}))
        position = self.positions.get(key)
        where = None
        if position is not None:
            where = (self._ref_stamp(position[0], memo, epochs, stack), self._ref_stamp(position[1], memo, epochs, stack))
        stamp = hash((self._own_stamp(state, epochs),
                      tuple(self._stamp(producer, memo, epochs, stack) for producer in imports), where))
        memo[key] = stamp
        return stamp

    def _ref_stamp(self, ref, memo, epochs, stack=()):
        if ref is None:
            return None
        if ref[0] == 'const':
            return hash(ref)
        return self._stamp(ref[1], memo, epochs, stack)

    # ---- rendering -----------------------------------------------------------------------

    def _texture_of(self, ref, n, memo, epochs, packed=False):
        if ref is None:
            return None
        if ref[0] == 'const':
            color = ref[1]
            return self._constant(n, (color[0], color[3], 0.0, 0.0) if packed else color)
        if (self._native_stamps is not None and self._native_graph is not None
                and not self._native_graph.dispatching and n == self.graph.n
                and ref[1] in self._native_stamps):
            return self._native_graph.render(self, ref[1], n, memo, epochs)[0]
        return self._render(ref[1], n, memo, epochs)[0]

    def _render(self, key, n, memo, epochs):
        """Render a unit and whatever it reads; returns its target textures."""
        state = self._state(key)
        stamp = self._stamp(key, memo, epochs)
        previous = state.stamp
        if state.textures is not None and previous == stamp:
            if key in self.retained:
                self.retained.move_to_end(key)
                if self._native_cache is not None:
                    self._native_cache.touch(key)
            return state.textures
        if (self._native_graph is not None and self._native_graph.dispatching
                and self._native_graph.current_key != key):
            raise RuntimeError('A native batch dependency lost residency before its consumer')
        if key[0] == 'FIELD' and FIELD_COPIES:
            content = self._field_content(state, n, epochs)
            if self._copy_field(key, state, n, content):
                state.stamp = stamp
                state.carried = None
                self.rendered.append(key)
                return state.textures   # not retained: _drop_copy releases it once its consumers drew
        program = state.program
        position = self.positions.get(key)
        # Inputs rendered for this draw stay resident until it has drawn: the retention
        # budget must not hand their textures back to the pool (as this unit's target).
        inputs = {sampler.producer[0] for p in glsl.programs(program) for sampler in p.samplers if sampler.kind == 'import'}
        inputs |= {ref[1] for ref in (position or ()) if ref is not None and ref[0] == 'unit'}
        self._in_use.update(inputs)
        try:
            backdrop = self._texture_of(position[0], n, memo, epochs, scalar_unit(key)) if position else None
            grouped = self._texture_of(position[1], n, memo, epochs, scalar_unit(key)) if position and position[1] else None
            samplers = [(sampler.name, self._bind(sampler, n, memo, epochs, backdrop, grouped, key))
                        for sampler in program.samplers]
            dev = device()
            if state.textures is None:
                allocated = []
                try:
                    for fmt in state.formats:
                        allocated.append(dev.texture(n, fmt))
                except Exception:
                    for texture, fmt in zip(allocated, state.formats):
                        dev.release(texture, n, fmt)
                    raise
                state.textures = allocated
            drawn = perf_counter()
            uniforms = state.uniforms()
            # A failed submission may already have overwritten this allocation.
            # Its old revision must not become a cache hit after undo/retry.
            state.stamp = None
            dev.draw(state.shader, state.textures, n, samplers, uniforms)
            elapsed = (perf_counter() - drawn) * 1000
            if self.profiling:
                elapsed = self._gpu_time(state, n, samplers, uniforms)
                self.stages['units'][key] = self.stages['units'].get(key, 0.0) + elapsed
            self.stages['render_ms'][key[0]] += elapsed
            self.stages['passes'][key[0]] += 1
        finally:
            self._in_use.subtract(inputs)
        state.stamp = stamp
        state.carried = None
        self.rendered.append(key)
        self._note_reads(key, previous != stamp, inputs)
        if key[0] == 'FIELD' and FIELD_COPIES:
            self._field_copies.discard(key)
            self._field_holds(key, content)
        for producer in inputs:
            if producer[0] == 'FIELD':
                self._drop_copy(producer, n)
        # What a chain step composed over is not needed once this step has drawn.
        if position:
            for ref in position:
                if ref is not None and ref[0] == 'unit':
                    self._settle(ref[1], n)
        if key[0] != 'CHANNEL' or self.graph.consumers.get(key):
            self._retain(key, state, n)
        return state.textures

    def _field_content(self, state, n, epochs):
        """What a FIELD renders: its shader, its parameters' float32 bits and the identities of
        the images and surface it samples. A native program's own stamp counts its uniform
        revisions, which differ between units holding equal values; these do not."""
        programs = tuple(glsl.programs(state.program))
        samplers = [s for p in programs for s in p.samplers]
        values = np.asarray([float(socket.default_value) for p in programs for socket in p.params], np.float32)
        images = tuple((binding(s.node).stamp if binding(s.node) is not None else _image_identity(s.node.image, epochs))
                       for s in samplers if s.kind == 'image')
        surface = self._surface() if any(s.kind in GEOMETRY for s in samplers) else None
        return n, state.shader, values.tobytes(), images, surface

    def _field_holds(self, key, content):
        """``key``'s texture now holds ``content``: a FIELD of the same content copies it."""
        self._forget_field(key)
        self._field_contents[key] = content
        self._field_twins[content] = key

    def _forget_field(self, key):
        """``key`` no longer holds what it rendered: it was filled by a copy or retired."""
        previous = self._field_contents.pop(key, None)
        if previous is not None and self._field_twins.get(previous) == key:
            del self._field_twins[previous]

    def _copy_field(self, key, state, n, content):
        """Fill a FIELD from another FIELD holding the same content: the same shader, parameter
        values and images (a layer's channels share one detail). True if it was copied; the
        texels are the other field's, bit for bit (``texelFetch``)."""
        twin = self._field_twins.get(content)
        if twin is None or twin == key or self._field_contents.get(twin) != content:
            return False
        other = self.states.get(twin)
        if other is None or other.textures is None or other.stamp is None:
            return False
        dev = device()
        if state.textures is None:
            state.textures = [dev.texture(n, fmt) for fmt in state.formats]
        state.stamp = None
        self._in_use[twin] += 1
        try:
            dev.draw(dev.fixed('copy'), state.textures[:1], n, [('op_source', other.textures[0])])
        finally:
            self._in_use[twin] -= 1
        self.stages['passes']['FIELD copy'] += 1
        metrics.count('engine_field_copies')
        # The twin stays the holder: a copy is released as soon as its consumers drew.
        self._forget_field(key)
        self._field_copies.add(key)
        return True

    def _drop_copy(self, key, n):
        """Release a copied FIELD whose consumers drew: another FIELD keeps its texels."""
        if key not in self._field_copies or self._in_use[key] > 0:
            return
        state = self.states.get(key)
        if state is not None and state.textures:
            for texture, fmt in zip(state.textures, state.formats):
                device().release(texture, n, fmt)
            state.textures = None
        self._field_copies.discard(key)

    def _fuses_final(self, channel, n):
        """Whether ``channel``'s final unit may render in its pixel publication's layout
        (``_fused_final``): Vulkan through pixels, a non-normal CHANNEL final nothing else reads
        or keeps."""
        dev = device()
        final = self.finals[channel]
        if (not FUSE_PIXEL_PUBLISH or n > FUSE_PIXEL_PUBLISH_MAX_SIZE
                or dev.backend != 'VULKAN' or not dev.pixel_publish
                or self.profiling or channel == 'NORMAL' or final[0] != 'unit'):
            return False
        key = final[1]
        return key[0] == 'CHANNEL' and key not in self.keep and not self.graph.consumers.get(key)

    def _fused_final(self, channel, n, memo, epochs):
        """Render a disposable final result directly in its publication layout.

        Imported/retained CHANNEL outputs keep the ordinary representation, as
        do normals, other backends and profiles of individual unit passes. The
        caller owns and releases the returned publication scratch: RGBA, or R
        for a scalar channel (``_publication_format``).
        """
        dev = device()
        if not self._fuses_final(channel, n):
            return None
        key = self.finals[channel][1]
        state = self._state(key)
        stamp = self._stamp(key, memo, epochs)
        if state.textures is not None and state.stamp == stamp:
            return None   # a retained result needs only the existing copy pass
        if len(state.program.targets) != 1:
            return None
        donor = self._donor(n) if self.pad else None
        if len(state.program.samplers) + int(donor is not None) > dev.max_samplers:
            return None
        program_key = (key, donor is not None)
        cached_program = self._publication_programs.get(program_key)
        if cached_program is None or cached_program[0] is not state.program:
            translated = perf_counter()
            self._publication_programs[program_key] = (state.program, _publication_program(
                state.program, scalar_unit(key), donor is not None))
            self.stages['translate_ms'] += (perf_counter() - translated) * 1000
        # Replace each unit's old variant when image/layout identity changes;
        # a long editing session must not retain every obsolete rewrite.
        program = self._publication_programs[program_key][1]
        if program is None:
            return None
        compiled = dev.compile_seconds
        shader = dev.shader(program)
        compiled = dev.compile_seconds - compiled
        self.stages['compile_ms'] += compiled * 1000
        position = self.positions.get(key)
        inputs = {s.producer[0] for p in glsl.programs(state.program)
                  for s in p.samplers if s.kind == 'import'}
        inputs |= {ref[1] for ref in (position or ()) if ref is not None and ref[0] == 'unit'}
        self._in_use.update(inputs)
        previous = state.stamp
        result = None
        fmt = _publication_format(channel, self.precise, scalar_unit(key))
        try:
            backdrop = self._texture_of(position[0], n, memo, epochs, scalar_unit(key)) if position else None
            grouped = self._texture_of(position[1], n, memo, epochs, scalar_unit(key)) if position and position[1] else None
            samplers = [(s.name, self._bind(s, n, memo, epochs, backdrop, grouped, key))
                        for s in state.program.samplers]
            if donor is not None:
                samplers.append(('op_final_donor', donor))
            result = dev.texture(n, fmt)
            drawn = perf_counter()
            dev.draw(shader, [result], n, samplers, state.uniforms())
            self.stages['render_ms']['CHANNEL'] += (perf_counter() - drawn) * 1000
            self.stages['passes']['CHANNEL'] += 1
        except Exception:
            if result is not None:
                dev.release(result, n, fmt)
            raise
        finally:
            self._in_use.subtract(inputs)
        # No CHANNEL texture is retained: its raw alpha/layout is different from
        # this finished image. A later dependency can still render it normally.
        self._free(key, state, n)
        state.stamp = stamp
        state.carried = None
        self.rendered.append(key)
        self._note_reads(key, previous != stamp, inputs)
        for ref in position or ():
            if ref is not None and ref[0] == 'unit':
                self._settle(ref[1], n)
        metrics.count('engine_fused_publications')
        return result

    def _gpu_time(self, state, n, samplers, uniforms, repeats=5, floor_ms=0.05):
        """A pass's GPU milliseconds: (``repeats`` draws and a wait) less (one draw and a wait),
        over ``repeats`` − 1, so the wait's own cost cancels. Each draw is a render pass of its
        own (a tiled GPU cannot merge them) and writes the same result again.

        The clock is the CPU's, so a stall in the single draw can outweigh several draws of a
        small pass, which then measured 0. When the draws stand less than ``floor_ms`` above
        the single draw, the single draw keeps its fastest run and the draws double (up to 64)."""
        dev = device()
        passes = dev.passes

        def run(count):
            started = perf_counter()
            for _ in range(count):
                dev.draw(state.shader, state.textures, n, samplers, uniforms)
            dev.finish()
            return (perf_counter() - started) * 1000

        run(1)   # the pipeline and the inputs' first use are not the pass's cost
        single = run(1)
        many = run(repeats)
        while many - single <= floor_ms and repeats < 64:
            single = min(single, run(1))
            repeats *= 2
            many = run(repeats)
        dev.passes = passes
        return max(0.0, (many - single) / (repeats - 1))

    def _settle(self, key, n):
        """Release a CHANNEL result its consumer has drawn with, unless it is kept."""
        if (key in self.keep or key in self.retained or self.graph.consumers.get(key) or self._in_use[key] > 0
                or key in self._fused_reads):
            return
        if key in self._frontier or (self._reads is not None and key in self._reads and key not in self._changed):
            return
        state = self.states.get(key)
        if state is not None and state.textures:
            for texture, fmt in zip(state.textures, state.formats):
                device().release(texture, n, fmt)
            state.textures = None

    def _note_reads(self, key, changed, inputs):
        """Record that ``key`` was rendered with new content from ``inputs`` (unit keys)."""
        if self._reads is None or not changed or not KEEP_READS:
            return
        self._changed.add(key)
        self._reads.update((item, None) for item in inputs if item[0] == 'CHANNEL')

    def _update_frontier(self, n):
        """Keep the unchanged CHANNEL results that changed units read in this evaluation.

        During a drag the same units change on every tick and read the same unchanged
        results: the inside of a smart material above the edited layer, which its folder
        step composes over again, or the layers below. Released after their use, these were
        rendered again on every tick, with everything they read. They are kept until an
        evaluation changes something without reading them, in what the retention budget
        has left, earliest read first. An evaluation that changed nothing keeps them as
        they are."""
        reads, changed = self._reads, self._changed
        self._reads = self._changed = None
        if not changed:
            return
        budget, frontier = RETAIN_BYTES - sum(self.retained.values()), set()
        for key in reads:
            if key in changed:
                continue
            state = self.states.get(key)
            if state is None or not state.textures or key in self.keep:
                continue
            size = sum(texture_bytes(n, n, fmt) for fmt in state.formats)
            if size <= budget:
                budget -= size
                frontier.add(key)
        stale = (self._frontier | (reads.keys() - changed)) - frontier
        self._frontier = frontier
        for key in stale:
            self._settle(key, n)

    def _bind(self, sampler, n, memo, epochs, backdrop, grouped, owner=None):
        kind = sampler.kind
        if kind == 'filter':
            return self._filter(sampler, n, memo, epochs, backdrop, grouped, owner)
        if kind == 'image':
            resource = binding(sampler.node)
            if resource is not None:
                return resource.texture
            image = glsl.live_image(sampler.node.image)
            if image is None:
                return self._constant(n, (0.0, 0.0, 0.0, 0.0))
            import gpu
            return gpu.texture.from_image(image)
        if kind == 'import':
            producer, target = sampler.producer
            return self._render(producer, n, memo, epochs)[target]
        if kind == 'backdrop':
            return backdrop
        if kind == 'grouped':
            return grouped
        return self._geometry_texture(kind, n)

    def _filter(self, sampler, n, memo, epochs, backdrop, grouped, owner):
        from . import filters
        source = sampler.source
        imports = tuple(sorted({s.producer[0] for p in glsl.programs(source) for s in p.samplers if s.kind == 'import'}))
        stamp = (self._program_stamp(source, epochs), filters.signature(sampler.node.effect),
                 tuple(self._stamp(key, memo, epochs) for key in imports),
                 tuple(self._ref_stamp(ref, memo, epochs) for ref in self.positions.get(owner, ())), n)
        cached = self.filter_states.get(sampler.node)
        if cached is not None and cached[0] == stamp:
            self.filter_states.move_to_end(sampler.node)
            return cached[1]
        dev = device()
        inputs = [(s.name, self._bind(s, n, memo, epochs, backdrop, grouped, owner)) for s in source.samplers]
        input_texture = dev.texture(n, RGBA)
        state = UnitState(None)
        state.program = source
        try:
            dev.draw(dev.shader(source), [input_texture], n, inputs, state.uniforms())
            texture = filters.apply(input_texture, sampler.node.effect, n, self.ts())
        finally:
            dev.release(input_texture, n, RGBA)
        if cached is not None:
            dev.release(cached[1], cached[2], RGBA)
        self.filter_states[sampler.node] = stamp, texture, n
        metrics.count('engine_spatial_filter_passes')
        return texture

    def _trim_filters(self, budget):
        from ...native_bridge.cache import select_victims
        victims = select_victims(self.filter_states, budget, lambda value: texture_bytes(value[2], value[2], RGBA))
        for key in victims:
            _, texture, n = self.filter_states.pop(key)
            device().release(texture, n, RGBA)
            metrics.count('engine_filter_evictions')

    def _keep_final(self, key, n):
        """A channel's final result published in place stays in the bounded result cache, as a
        revision's sources do when it commits: the next edit composes over it (a layer added on
        top, an undo) instead of rendering the stack below again. As there, the cache's budget
        is enforced once the evaluation has published (``evaluate``)."""
        state = self.states.get(key)
        if state is not None and state.textures:
            self._retain(key, state, n, trim=False)
            self._kept_finals = True

    def _retain(self, key, state, n, trim=True):
        self.retained[key] = sum(texture_bytes(n, n, fmt) for fmt in state.formats)
        self.retained.move_to_end(key)
        if self._native_cache is not None and self._native_cache.handle:
            self._native_cache.put(key, self.retained[key])
        if trim:
            self._trim_retained(n, (key,))

    def _trim_retained(self, n, protected=()):
        total = sum(self.retained.values())
        from ...painting.gpu_sources import trim
        trim(self.set_id, max(0, RETAIN_BYTES - total))
        from ...native_bridge.lifecycle import active_library
        lib = active_library()
        if lib is not None:
            if self._native_cache is None or not self._native_cache.handle:
                from ...native_bridge.cache import Cache
                self._native_cache = Cache(lib)
                for name, size in self.retained.items():
                    self._native_cache.put(name, size)
            victims = self._native_cache.trim(RETAIN_BYTES, [*protected, *(k for k, uses in self._in_use.items() if uses > 0)])
        else:
            # A diagnostic legacy session can change residency independently.
            if self._native_cache is not None:
                self._native_cache.close()
                self._native_cache = None
            victims = []
            for candidate in self.retained:
                if total <= RETAIN_BYTES:
                    break
                if candidate not in protected and self._in_use[candidate] <= 0:
                    victims.append(candidate)
                    total -= self.retained[candidate]
        for victim in victims:
            self.retained.pop(victim, None)
            victim_state = self.states.get(victim)
            if victim_state is not None and victim_state.textures:
                for texture, fmt in zip(victim_state.textures, victim_state.formats):
                    device().release(texture, n, fmt)
                victim_state.textures = None
                metrics.count('engine_evictions')

    _constants = {}

    def _constant(self, n, color):
        """A constant input: one texel (programs clamp their backdrop fetches into it)."""
        key = tuple(round(float(c), 9) for c in color)
        texture = self._constants.get(key)
        if texture is None:
            import gpu
            texture = gpu.types.GPUTexture((1, 1), format=RGBA, data=gpu.types.Buffer('FLOAT', 4, list(key)))
            self._constants[key] = texture
        return texture

    def _checkpoints(self, channel, memo, epochs):
        """Keep the results the lowest changed unit of the chain composes over: the one
        just below it and, natively, the one below each folder enclosing it. The next
        edit of the same layer re-renders nothing beneath them."""
        keep = {key for key in self.keep if key[2] != channel}
        if self._native_stamps is not None and self._native_graph is not None:
            keep.update(self._native_graph.checkpoints(self, channel))
        else:
            order = self.orders.get(channel, ())
            lowest = next((key for key in order if self.states[key].stamp != self._stamp(key, memo, epochs)), None)
            if lowest is not None:
                below = self.positions[lowest][0]
                if below is not None and below[0] == 'unit':
                    keep.add(below[1])
        released = {key for key in self.keep if key[2] == channel} - keep
        self.keep = keep
        return released

    # ---- geometry --------------------------------------------------------------------------

    def _clear_geometry(self):
        for texture in self.geometry.values():
            # Neutral constants belong to the device, not this geometry cache.
            if not any(texture is value for value in self._constants.values()):
                device().discard(texture)
        self.geometry.clear()

    def _geometry_texture(self, kind, n):
        key = self._surface()
        if self.geometry_key != key:
            self._clear_geometry()
            self.geometry_key = key
        texture = self.geometry.get(kind)
        if texture is None:
            with self._inputs():
                texture = self._build_geometry(kind, n)
        return texture

    @contextmanager
    def _inputs(self):
        started = perf_counter()
        try:
            yield
        finally:
            self.stages['inputs_ms'] += (perf_counter() - started) * 1000

    def _build_geometry(self, kind, n):
        from ...painting.projection import geometry, tangents
        ts = self.ts()
        try:
            if kind in {'position', 'normal'}:
                positions, normals = geometry(ts, n)
                pairs = (('position', positions), ('normal', normals))
            else:
                pairs = tuple(zip(('tangent', 'bitangent'), tangents(ts, n)))
        except ValueError:
            # No valid assigned mesh (a set between assignments): a neutral surface keeps
            # every other layer showing, as the material did; export reports the mesh.
            self.geometry[kind] = self._constant(n, NEUTRAL_SURFACE[kind])
            return self.geometry[kind]
        for name, values in pairs:
            rgba = np.ones((n, n, 4), np.float32)
            rgba[..., :3] = values
            self.geometry[name] = device().upload(rgba)
        return self.geometry[kind]

    def _geometry_stamp(self, n):
        from ..dependencies import geometry_key
        ts = self.ts()
        try:
            return (n, geometry_key(self.scene, ts))
        except Exception:
            return (n, None)

    def ts(self):
        return next(item for item in self.scene.open_painter.texture_sets if item.id == self.set_id)

    # ---- padding ---------------------------------------------------------------------------

    def _donor(self, n):
        ts = self.ts()
        key = (n, tuple(ref.object.name if ref.object else None for ref in ts.assignments), self._surface())
        if self.padding is not None and self.padding[0] == key:
            return self.padding[1]
        with self._inputs():
            return self._build_donor(ts, n, key)

    def _build_donor(self, ts, n, key):
        from ...export import padding
        if self.padding is not None and self.padding[1] is not None:
            device().discard(self.padding[1])
        texture = None
        try:
            owned = padding.ownership(ts, n)
            donor, _ = padding.nearest_donors(owned.covered, owned.island, padding.automatic_padding(n))
            if (donor >= 0).any():
                xy = np.full((n, n, 2), -1.0, np.float32)
                valid = donor >= 0
                xy[..., 0] = np.where(valid, donor % n, -1)
                xy[..., 1] = np.where(valid, donor // n, -1)
                texture = device().upload(xy, 'RG16F' if n <= DONOR_HALF_SIDE else 'RG32F')
        except Exception:
            texture = None   # no assigned geometry: nothing to pad
        self.padding = (key, texture)
        return texture

    # ---- evaluation --------------------------------------------------------------------------

    def evaluate(self, channels=None, epochs=None, images=None, budget=None):
        """Render changed channels and publish them into the set's images; returns the channels written.

        ``images`` maps channel -> Image to publish into (default: the set's outputs). Through pixels
        (Vulkan) each publication costs a read-back, a pixel write and Blender's rebuild of the
        image's texture, so an evaluation spends at most ``budget`` ms on them (default
        ``PIXEL_BUDGET_MS``; False: no limit), always at least one channel, the channels waiting
        longest first; the others stay in ``deferred`` for the next evaluation.
        """
        if self._native_channels is not None and self._native_channels.dispatching:
            raise RuntimeError("Cannot reenter native channel evaluation")
        if self._evaluation_depth:
            # Pixel publication has no native channel submission to refuse it: guard here.
            raise RuntimeError("Cannot reenter an evaluation")
        graph = self.graph
        if graph is None:
            return set()
        started = perf_counter()
        ts = self.ts()
        n = graph.n
        epochs = epochs or {}
        memo = {}
        written = set()
        dev = device()
        passes, compiles = dev.passes, dev.compiles
        self.stages = stages.evaluation()
        self.stages['profiled'] = self.profiling
        self.rendered = []
        self._reads, self._changed = {}, set()
        self._in_use = Counter()
        self._surface_key = None
        pixels = dev.pixel_publish
        if budget is None:
            budget = PIXEL_BUDGET_MS if pixels and images is None and not self.profiling else False
        now = perf_counter()
        order = list(channels or CHANNELS)
        if budget is not False:
            order.sort(key=lambda channel: (self.waiting.get(channel, now), CHANNELS.index(channel)))
        deferred, spent, uniform_writes = set(), 0.0, 0
        self._suffix.begin()
        with self._evaluation_states(), dev.state():
            self._prepare_native(epochs)
            native_channels = self._native_stamps is not None and not pixels
            if not native_channels and self._native_channels is not None:
                self._native_channels.close()
                self._native_channels = None
            with self._revision_transaction(dev, external_commit=native_channels), self._mip_batches():
                if native_channels:
                    written = self._evaluate_native_channels(order, ts, images, n, memo, epochs)
                else:
                    for channel in order:
                        image = (images or {}).get(channel) or _output_image(ts, channel)
                        if image is None:
                            continue
                        uniform = self._uniform_output(ts, channel, image) if images is None else None
                        stamp = (('uniform', uniform, n, self.precise, image.get('open_painter_pixel_revision', 0))
                                 if uniform is not None else
                                 self._ref_stamp(self.finals[channel], memo, epochs))
                        pointer = image.as_pointer()
                        if self.published.get(channel) == stamp and self.images.get(channel) == pointer:
                            self.waiting.pop(channel, None)
                            continue
                        if budget is not False and written and spent >= budget:
                            deferred.add(channel)   # published by a later evaluation (cache.tick keeps coming)
                            self.waiting.setdefault(channel, now)
                            continue
                        final = self.finals[channel]
                        fused = False
                        constant_publish_ms = 0.0
                        if uniform is None and images is None and self._constant_output(ts, channel, image):
                            from .constant_outputs import ConstantRenderer
                            if self._constant_renderer is None:
                                self._constant_renderer = ConstantRenderer()
                            uniform = self._constant_renderer.evaluate(self, channel, epochs)
                            self.rendered.extend(self._constant_renderer.rendered)
                            for stage in ('render_ms', 'passes'):
                                self.stages[stage].update(self._constant_renderer.stages[stage])
                            for stage in ('inputs_ms', 'translate_ms', 'compile_ms'):
                                self.stages[stage] += self._constant_renderer.stages[stage]
                            constant_publish_ms = self._constant_renderer.stages['publish_ms']
                        if uniform is None:
                            released = self._checkpoints(channel, memo, epochs)
                            for key in released:
                                self._settle(key, n)
                            if final[0] == 'unit' and self._native_stamps is not None:
                                # Fused chains, then the runs under the final unit, one pass each
                                # (fusion.py, suffix.py), as on the native channel path. A collapsed
                                # final is then current, so it is published as it is rather than fused.
                                if self._fuses_final(channel, n):
                                    # The final renders with its publication (_fused_final): a chain
                                    # ending at it fuses the units below it.
                                    ending = fusion.materialize(self, final[1], n, memo, epochs, defer=True)
                                    if ending is not None:
                                        fusion.render_deferred(self, (ending[0][:-1], ending[1]), n, memo, epochs)
                                else:
                                    fusion.materialize(self, final[1], n, memo, epochs)
                                self._suffix.collapse(self, final[1], n, memo, epochs)
                            result = self._fused_final(channel, n, memo, epochs)
                            fused = result is not None
                            if not fused:
                                result = self._texture_of(final, n, memo, epochs)
                        if self.profiling:
                            dev.finish()   # what was queued before is not publishing's
                        publishing, inputs = perf_counter(), self.stages['inputs_ms']
                        if uniform is not None:
                            self._publish_uniform(image, channel, uniform, n)
                            uniform_writes += 1
                        else:
                            if fused:
                                try:
                                    self._uniform_cpu.pop(channel, None)
                                    if tuple(image.size) != (n, n):
                                        raise ValueError(f'{image.name} is {tuple(image.size)}, not the set resolution {n}')
                                    # A scalar channel's fused final is R: read as (v, v, v, 1).
                                    image.pixels.foreach_set(dev.readback(result, n, gray=True))
                                    metrics.count('engine_image_pixel_writes')
                                    metrics.count('engine_image_pixel_write_bytes', n * n * 16)
                                finally:
                                    dev.release(result, n, result.format)
                            else:
                                self._publish(image, channel, result, n)
                        # A later channel may fail. Already published GPU pixels must still
                        # be synchronized on save/export, even when this evaluation aborts.
                        if pixels or uniform is not None:
                            self.dirty_cpu.discard(channel)
                        else:
                            self.dirty_cpu.add(channel)
                        if self.profiling:
                            dev.finish()
                        # The padding table it may build first counts as a CPU input, not as publishing;
                        # a profile's wait is not the GPU's time.
                        elapsed = constant_publish_ms + max(0.0, (perf_counter() - publishing) * 1000
                                      - (self.stages['inputs_ms'] - inputs)
                                      - (dev.wait_cost if self.profiling else 0.0))
                        self.stages['publish_ms'] += elapsed
                        spent += elapsed
                        self.stages['published'].append(channel)
                        self.waiting.pop(channel, None)
                        if uniform is None and final[0] == 'unit':
                            if not pixels:
                                self._keep_final(final[1], n)
                            self._settle(final[1], n)
                        self.published[channel] = stamp
                        self.images[channel] = pointer
                        written.add(channel)
        self.deferred = deferred
        self._update_frontier(n)
        self._prune_fused_finals(n)
        if self._kept_finals:
            self._kept_finals = False
            self._trim_retained(n)
        self._trim_filters(RETAIN_BYTES - sum(self.retained.values()))
        from ...painting.gpu_sources import trim as trim_strokes
        trim_strokes(self.set_id, max(0, RETAIN_BYTES - sum(self.retained.values()) -
                     sum(texture_bytes(size, size, RGBA) for _, _, size in self.filter_states.values())))
        self.stages['deferred'] = sorted(deferred)
        dev.trim(POOL_SPARES)
        self.stages['compiles'] = dev.compiles - compiles
        path = 'pixels' if pixels else 'framebuffer'
        if uniform_writes:
            path = 'uniform publication' if uniform_writes == len(written) else path + ' + uniform publication'
        self.stages['publish_path'] = path if written else ''
        self.report = {'evaluate_ms': (perf_counter() - started) * 1000, 'channels': sorted(written),
                       'passes': dev.passes - passes, 'units_rendered': len(self.rendered),
                       'programs': len(dev.shaders), 'compiles': dev.compiles,
                       'stages': self.stages,
                       'execution_backend': ('c17-host-' + str(dev.backend).lower() if self._native_stamps is not None
                                             else 'legacy'),
                       'native_topology_rebuilds': self._native_graph.rebuilds if self._native_graph else 0,
                       'native_channel_selection': native_channels,
                       'material_revision': self._native_revisions.revision if self._native_revisions else None}
        if written:
            metrics.count('engine_channel_publications', len(written))
            metrics.sample('engine_evaluate_ms', self.report['evaluate_ms'])
        return written

    def deferred_channels(self):
        """Channels an evaluation held back for a later one (pixel publication's budget)."""
        return set(self.waiting)

    def prearm(self, layer_id, epochs=None):
        """While idle: arm one suffix transform an edit of ``layer_id`` would use.

        Returns True when it did work (call again on a later idle tick). Nothing is
        armed while an edit is still unevaluated: prediction assumes the current
        content is the one on screen. Under pixel publication (Vulkan) a channel
        still waiting for its publication budget keeps the session dirty, so this
        runs only once every channel is on screen, as with framebuffer publication.
        """
        from .suffix import prearm_enabled
        if (self.graph is None or not prearm_enabled() or self.deferred_channels()
                or (self._native_channels is not None and self._native_channels.dispatching)):
            return False
        n = self.graph.n
        self.stages = stages.evaluation()
        self._in_use = Counter()
        self._surface_key = None
        self.rendered = []
        with self._evaluation_states(), device().state():
            self._prepare_native(epochs or {})
            graph = self._native_graph
            if self._native_stamps is None or graph is None or graph.last_patch_count:
                return False
            memo = {}
            if self._keep_fused_finals(n, memo, epochs or {}):
                return True
            # An edit of the layer will compose over its spine: make it resident and
            # keep it, as the edit's own checkpoints will, so its first tick renders
            # nothing below the layer.
            for channel in CHANNELS:
                node = ('CHANNEL', layer_id, channel)
                spine = [key for key in graph.spine(node) if key not in self.keep]
                for key in spine:
                    self.keep.add(key)
                    self._texture_of(('unit', key), n, memo, epochs or {})
            if self._suffix.prearm(self, layer_id, n, memo, epochs or {}):
                return True
            if fusion.prearm(self, layer_id, n, memo, epochs or {}):
                return True
            self._prearm_reads(layer_id, n, memo, epochs or {})
            return False

    def _prearm_reads(self, layer_id, n, memo, epochs):
        """While idle, once the spine and the transforms are ready: make resident and keep the
        unchanged results an edit of ``layer_id`` would read (``_predicted_reads``), so its
        first tick renders only what changes, as the next ones do. Results kept for an
        edit of another layer are released. Once per state of the stack."""
        if not KEEP_READS:
            return
        stamps = self._native_graph.stamps
        state = (layer_id, tuple(stamps.get(final[1]) for final in self.finals.values()
                                 if final and final[0] == 'unit'))
        if self._reads_prearmed == state:
            return
        self._reads_prearmed = state
        budget, frontier = RETAIN_BYTES - sum(self.retained.values()), set()
        for key in self._predicted_reads(layer_id):
            unit = self.states.get(key)
            if unit is None or key in self.keep:
                continue
            size = sum(texture_bytes(n, n, fmt) for fmt in unit.formats)
            if size > budget:
                continue
            self._texture_of(('unit', key), n, memo, epochs)
            frontier.add(key)
            self._frontier.add(key)   # kept while the next one renders
            budget -= size
        stale, self._frontier = self._frontier - frontier, frontier
        for key in stale:
            self._settle(key, n)
        metrics.count('engine_prearmed_reads', len(frontier))

    def _predicted_reads(self, layer_id):
        """The unchanged CHANNEL results an edit of ``layer_id`` would read, in render order:
        the inputs of every unit it changes (its own units and all that read them), except
        the members of the affine runs suffix collapse renders from their input."""
        readers = self._composition_readers()
        importers = self.graph.consumers
        start = [key for key in self.states if len(key) > 1 and key[1] == layer_id]
        changed, stack = set(start), list(start)
        while stack:
            key = stack.pop()
            for reader in (*readers.get(key, ()), *importers.get(key, ())):
                if reader not in changed:
                    changed.add(reader)
                    stack.append(reader)
        from . import suffix
        collapsed, inputs = set(), []
        if suffix.ENABLED:
            for channel, final in self.finals.items():
                node = ('CHANNEL', layer_id, channel)
                if not final or final[0] != 'unit' or node not in self.states:
                    continue
                for _, source, _, members in self._native_graph.edit_runs(final[1], node):
                    if len(members) >= suffix.MIN_LENGTH:
                        collapsed.update(members)
                        if source is not None:
                            inputs.append(source)
        for key in self._render_order():
            if key not in changed or key in collapsed:
                continue
            state = self.states.get(key)
            if state is None or state.program is None:
                continue
            inputs.extend(ref[1] for ref in self.positions.get(key) or () if ref is not None and ref[0] == 'unit')
            inputs.extend(s.producer[0] for p in glsl.programs(state.program) for s in p.samplers if s.kind == 'import')
        return [key for key in dict.fromkeys(inputs) if key[0] == 'CHANNEL' and key not in changed]

    def _render_order(self):
        """Every unit, CHANNEL units in each channel's render order first."""
        seen = dict.fromkeys(key for order in self.orders.values() for key in order)
        seen.update(dict.fromkeys(self.states))
        return seen

    def _composition_readers(self):
        """Each unit's composition readers: the units whose backdrop or grouped input it is."""
        cached = self.__dict__.get('_readers')
        if cached is None or cached[0] is not self.positions:
            readers = {}
            for key, position in self.positions.items():
                for ref in position or ():
                    if ref is not None and ref[0] == 'unit':
                        readers.setdefault(ref[1], []).append(key)
            cached = self._readers = (self.positions, readers)
        return cached[1]

    def _evaluate_native_channels(self, order, ts, images, n, memo, epochs):
        from ...native_bridge import lifecycle
        from ...native_bridge.channels import NativeChannels, Request
        dev = device()
        if self._native_channels is None or not self._native_channels.handle:
            self._native_channels = NativeChannels(self._native_graph.lib)
            lifecycle.track(self._native_channels)
        owner = self._native_channels
        requests, targets = [], {}
        for channel in dict.fromkeys(order):
            image = (images or {}).get(channel) or _output_image(ts, channel)
            if image is None:
                continue
            index = CHANNELS.index(channel)
            final = self.finals[channel]
            stamp = self._ref_stamp(final, memo, epochs)
            requests.append(owner.request(index, image, final, stamp, n, self.precise,
                                          self.pad, channel not in self.published))
            targets[index] = channel, image, final, stamp
            self.waiting.pop(channel, None)

        written = set()
        def submit(action, index):
            if action == 1:
                started = perf_counter()
                if self._native_revisions is not None and self._native_revisions.submitting:
                    self._native_revisions.commit()
                else:
                    self._flush_mips()   # each channel was published in place: C accepts complete images
                self.stages['publish_ms'] += (perf_counter() - started) * 1000
                return
            channel, image, final, stamp = targets[index]
            for key in self._checkpoints(channel, memo, epochs):
                self._settle(key, n)
            deferred = run = handed = None
            if final[0] == 'unit':
                # A fused chain ending at the final, or the collapsed run it tops (and the chain
                # that is that run's input), renders in the publication pass (_publish_fused).
                run_inputs = {}
                deferred = fusion.materialize(self, final[1], n, memo, epochs, defer=True, inputs=run_inputs)
                run = self._suffix.collapse(self, final[1], n, memo, epochs, defer=True)
                handed = run_inputs.get(final[1])
            started, inputs = perf_counter(), self.stages['inputs_ms']
            if not self._publish_fused(image, channel, deferred, run, n, memo, epochs, handed):
                result = self._texture_of(final, n, memo, epochs)
                if self.profiling:
                    dev.finish()
                started, inputs = perf_counter(), self.stages['inputs_ms']
                self._publish(image, channel, result, n)
            if self.profiling:
                dev.finish()
            self.stages['publish_ms'] += max(0.0, (perf_counter() - started) * 1000
                - (self.stages['inputs_ms'] - inputs) - (dev.wait_cost if self.profiling else 0.0))
            if final[0] == 'unit':
                if not (self._native_revisions is not None and self._native_revisions.submitting):
                    self._keep_final(final[1], n)   # a revision keeps its sources when it commits
                self._settle(final[1], n)
            # Allocate/update host mirrors while publication is still staged.
            # A Python allocation failure must occur before C accepts the batch.
            self.published[channel] = stamp
            self.images[channel] = image.as_pointer()
            self.dirty_cpu.add(channel)
            self.stages['published'].append(channel)
            written.add(channel)

        owner.execute((Request * len(requests))(*requests), submit)
        return written

    def _revision_transaction(self, dev, *, external_commit=False):
        from ...native_bridge import lifecycle
        lib = lifecycle.execution_library(dev.backend)
        if (lib is None or dev.pixel_publish or not NATIVE_REVISIONS or dev.backend not in REVISION_BACKENDS
                or not revisions_fit(self.graph.n)):
            if self._native_revisions is not None and not self._native_revisions.submitting:
                # Its immutable chains (the previous revision of every channel) are only worth
                # their memory while commits go through it.
                self._native_revisions.close()
                self._native_revisions = None
            return nullcontext()
        if self._native_revisions is None or not self._native_revisions.handle:
            from ...native_bridge.revisions import Revisions
            self._native_revisions = Revisions(lib, dev)
            lifecycle.track(self._native_revisions)
        return self._native_revisions.transaction(self, external_commit=external_commit)

    def _prepare_native(self, epochs):
        from ...native_bridge import lifecycle
        self._native_stamps = None
        if not NATIVE_HOST_GRAPH:
            return
        lib = lifecycle.execution_library(device().backend)
        if lib is None:
            self._native_stamps = None
            return
        if self._native_graph is None or not self._native_graph.handle:
            from ...native_bridge.graph import NativeGraph
            self._native_graph = NativeGraph(lib)
            lifecycle.track(self._native_graph)
        self._native_stamps = self._native_graph.prepare(self, epochs)
        metrics.count('native_graph_evaluations')
        metrics.count('native_planned_units', len(self._native_graph.plan))

    def _constant_output(self, ts, channel, image):
        """Use original shaders at one texel only when the entire channel is spatially constant."""
        dev = device()
        if (not CONSTANT_OUTPUTS or self.profiling or dev.backend != 'VULKAN'
                or PIXEL_PUBLISH is not None or channel == 'BASE_COLOR'
                or not getattr(getattr(image, 'colorspace_settings', None), 'is_data', False)):
            return False
        from .constant_channels import spatially_constant_channel
        return spatially_constant_channel(ts, channel)

    def _uniform_output(self, ts, channel, image):
        """Prove a default output before dropping any dependencies from its stamp.

        Vulkan transfer-clears can update all image mips without CPU publication.
        Only exact neutral defaults are admitted: no half-float rounding changes
        or color-space conversion are hidden by the shortcut. Explicit pixel
        publication remains available for tests and compatibility fallback.
        """
        dev = device()
        if (dev.backend != 'VULKAN' or PIXEL_PUBLISH is not None or channel == 'BASE_COLOR' or
                not getattr(getattr(image, 'colorspace_settings', None), 'is_data', False)):
            return None
        color = (*DEFAULTS[channel][:3], 1.0)
        ref = self.finals[channel]
        if ref[0] == 'const' and tuple(ref[1]) == color:
            return color
        if ref[0] == 'unit':
            from .uniforms import uniform_channel
            return uniform_channel(ts, channel, precise=self.precise)
        return None

    def _publish_uniform(self, image, channel, color, n):
        """Update canonical CPU constants, or refresh unchanged GPU mips without a readback.

        Keeping the CPU constant current also makes Blender's texture eviction,
        explicit gl_free(), saving and Cycles safe without a later GPU read.
        """
        import gpu
        if self._presentation is not None:
            self._presentation.forget(channel)
        if tuple(image.size) != (n, n):
            raise ValueError(f'{image.name} is {tuple(image.size)}, not the set resolution {n}')
        stamp = (image.as_pointer(), n, color, image.get('open_painter_pixel_revision', 0))
        if self._uniform_cpu.get(channel) != stamp:
            buffer = device()._readback_buffer(n)
            values = np.frombuffer(buffer, np.float32)
            # Duplicate a completed RGBA prefix with contiguous copies. Strided
            # component writes revisit every cache line up to four times at 2K+.
            values[:4] = color
            filled = 4
            while filled < values.size:
                count = min(filled, values.size - filled)
                np.copyto(values[filled:filled + count], values[:count])
                filled += count
            image.pixels.foreach_set(buffer)
            self._uniform_cpu[channel] = stamp
            metrics.count('engine_uniform_cpu_initializations')
            metrics.count('engine_uniform_cpu_bytes', n * n * 16)
            metrics.count('engine_image_pixel_writes')
            metrics.count('engine_image_pixel_write_bytes', n * n * 16)
            metrics.count('engine_uniform_publications')
            # foreach_set already invalidates the image texture and mip chain.
            # Let Blender upload them when sampled, like ordinary publication:
            # an eager from_image + clear only repeats the same update and stalls
            # the callback before the other channels can submit their work.
            return
        # Vulkan's texture transfer-clear covers its full mip range. Unlike a
        # level-zero compute write, distant/minified material samples stay current.
        texture = gpu.texture.from_image(image)
        # Vulkan's float clear can truncate a half-float target, while Blender's
        # CPU image upload rounds to nearest. Quantize only the clear value to
        # match that upload; precise canonical CPU pixels keep their full value.
        gpu_color = tuple(np.asarray(color, np.float16).astype(np.float32)) if texture.format == HALF else color
        texture.clear(format='FLOAT', value=gpu_color)
        metrics.count('engine_uniform_texture_clears')
        metrics.count('engine_uniform_publications')

    def memory(self):
        """Engine-owned base textures, excluding Blender images and driver overhead."""
        n = self.graph.n if self.graph is not None else 0
        retained = sum(self.retained.values())
        active = sum(sum(texture_bytes(n, n, fmt) for fmt in state.formats)
                     for key, state in self.states.items() if state.textures and key not in self.retained)
        from ...painting.gpu_sources import memory, retained_bytes
        from ...painting.gpu_strokes import active_bytes
        active += active_bytes(self.set_id)
        retained += retained_bytes(self.set_id) + sum(texture_bytes(size, size, RGBA) for _, _, size in self.filter_states.values())
        return dict(device().memory(), retained_bytes=retained, active_bytes=active, undo_bytes=memory(self.set_id),
                    suffix_bytes=self._suffix.memory())

    def profile(self, epochs=None):
        """Render and publish every channel again with each pass timed on the GPU
        (``_gpu_time``): ``stages['units']`` then holds each unit's own GPU milliseconds,
        each Mask Graph a pass of its own. The images end up as they were."""
        dev = device()
        dev.calibrate()
        self.published.clear()
        for state in self.states.values():
            state.stamp = None   # retained results render again too
        self.profiling = True
        try:
            dev.finish()
            written = self.evaluate(epochs=epochs)
        finally:
            self.profiling = False
        self.stages['wait_ms'] = dev.wait_cost
        return written

    def render_channel(self, channel, epochs=None):
        """The unpadded, unfinished chain result of one channel as an (n, n, 4) array (tests, export)."""
        graph = self.graph
        n = graph.n
        memo = {}
        self.rendered = []
        self.stages = stages.evaluation()
        self._in_use = Counter()
        self._surface_key = None
        with self._evaluation_states(), device().state():
            self._prepare_native(epochs or {})
            texture = self._texture_of(self.finals[channel], n, memo, epochs or {})
            values = np.array(device().readback(texture, texture.width), np.float32).reshape(texture.height, texture.width, 4)
            if getattr(texture, 'format', '') in {'RG16F', 'RG32F'}:
                values = values[..., [0, 0, 0, 1]]
            return np.broadcast_to(values, (n, n, 4)).copy() if values.shape[0] == 1 else values

    def restore_evicted(self):
        """Show the published channels again in image textures Blender recreated (eviction)."""
        if self._native_revisions is not None:
            self._native_revisions.restore_evicted()
        if self._presentation is not None:
            self._presentation.restore_evicted()

    def _publish(self, image, channel, result, n):
        if self._presentation is not None:
            self._presentation.forget(channel)   # a revision or the CPU pixels restore it now
        if self._native_revisions is not None and self._native_revisions.submitting:
            return self._native_revisions.stage(self, image, channel, result, n)
        dev = device()
        self._begin_publication(image, channel, n)
        donor = self._donor(n) if self.pad else None
        scalar = getattr(result, 'format', '') in {'RG16F', 'RG32F'}
        fmt = _publication_format(channel, self.precise, scalar and dev.pixel_publish)
        ints = {'op_mode': 1 if channel == 'NORMAL' else 0, 'op_pad': 1 if donor is not None else 0,
                'op_scalar': int(scalar)}
        samplers = [('op_source', result), ('op_donor', donor if donor is not None else result)]
        if dev.pixel_publish:
            # Through pixels (a background Vulkan session, or forced): reuse a readback
            # buffer; Blender rebuilds the image texture and mips on its next draw.
            chain = dev.texture(n, fmt)
            try:
                dev.draw(dev.fixed('publish_pixels'), [chain], n, samplers, ints=ints)
                image.pixels.foreach_set(dev.readback(chain, n, gray=True))
            finally:
                dev.release(chain, n, fmt)
            metrics.count('engine_image_pixel_writes')
            metrics.count('engine_image_pixel_write_bytes', n * n * 16)
            return
        # Finish, pad and write level 0 in one pass, and its copy the mips reduce.
        self._publish_in_place(image, channel, n, fmt,
                               lambda slots: dev.draw(dev.fixed('publish'), slots, n, samplers, ints=ints))

    def _publish_fused(self, image, channel, deferred, run, n, memo, epochs, handed=None):
        """Publish ``channel`` with its final rendered in the publication pass: the collapsed run
        it tops (``fusion.publish_suffix``) or a fused chain ending at it (``fusion.publish``).
        False, with what was left for that pass rendered now, when it cannot.

        Such a final keeps no texture, so only a drag's later ticks publish it so (the same edit
        on consecutive evaluations, ``fusion.repeated``). A one-off edit, an undo or a drag's
        first tick keeps its finals in the result cache (``_keep_final``) for the edits that
        compose over them and the undos back to them; a drag's last finals are kept by the next
        idle tick (``_keep_fused_finals``). ``handed``: the fused chain that is the run's input,
        (chain, repeated), evaluated in the same pass when it can be, rendered first otherwise."""
        if run is not None:
            again = fusion.repeated(self, ('publish', run[0]))
            if handed is not None:
                if again and fusion.publish_suffix(self, run, image, channel, n, memo, epochs, chain=handed[0]):
                    return True
                fusion.render_deferred(self, handed, n, memo, epochs)
            if again and fusion.publish_suffix(self, run, image, channel, n, memo, epochs):
                return True
            self._suffix.apply_deferred(self, run, n, memo, epochs)
            return False
        if handed is not None:
            fusion.render_deferred(self, handed, n, memo, epochs)
        if deferred is not None:
            if deferred[1] and fusion.publish(self, deferred[0], image, channel, n, memo, epochs):
                return True
            fusion.render_deferred(self, deferred, n, memo, epochs)   # one fused pass at least
        return False

    def _note_fused_final(self, key, run, reads, n):
        """``key`` was published fused (``fusion.publish``, ``publish_suffix``) from ``reads``
        (CHANNEL results), which stay resident until an idle tick keeps it from them."""
        previous = self._fused_finals.pop(key, None)
        self._fused_finals[key] = (run, tuple(reads))
        self._fused_reads.update(reads)
        if previous is not None:
            self._release_fused_reads(previous[1], n)

    def _release_fused_reads(self, reads, n):
        self._fused_reads.subtract(reads)
        for item in reads:
            if self._fused_reads[item] <= 0:
                del self._fused_reads[item]
                self._settle(item, n)

    def _fused_final_current(self, key, finals):
        """Whether ``key`` still holds what it published fused, in no texture."""
        graph, state = self._native_graph, self.states.get(key)
        return (key in finals and state is not None and state.textures is None and graph is not None
                and graph.stamps is not None and state.stamp == graph.stamps.get(key))

    def _prune_fused_finals(self, n):
        """After an evaluation: forget the finals published fused that were rendered since or no
        longer hold that content, and release what they read."""
        if not self._fused_finals:
            return
        finals = {final[1] for final in self.finals.values() if final[0] == 'unit'}
        for key in [key for key in self._fused_finals if not self._fused_final_current(key, finals)]:
            self._release_fused_reads(self._fused_finals.pop(key)[1], n)

    def _keep_fused_finals(self, n, memo, epochs):
        """While idle: render one final a drag's last tick published fused (it holds no texture)
        from what that tick read, held since, and keep it as an unfused publication would have
        (``_keep_final``): the edits composing over it and the undos back to it find it. A
        collapsed run's top is applied again with the same transform (collapse rounds otherwise
        than the run rendered layer by layer); a fused chain equals its members rendered one by
        one. True when it kept one."""
        finals = {final[1] for final in self.finals.values() if final[0] == 'unit'}
        while self._fused_finals:
            key, (run, reads) = self._fused_finals.popitem()
            try:
                if not self._fused_final_current(key, finals):
                    continue
                if run is None:
                    self._texture_of(('unit', key), n, memo, epochs)
                elif run[3].n == n and any(entry is run[3] for entry in self._suffix.entries.values()):
                    self._suffix.apply_deferred(self, run, n, memo, epochs)
                else:
                    continue   # its transform was reclaimed: rendered when an edit needs it
                self._keep_final(key, n)
                self._kept_finals = False
                self._trim_retained(n)
                metrics.count('engine_fused_finals_kept')
                return True
            finally:
                self._release_fused_reads(reads, n)
        return False

    def _begin_publication(self, image, channel, n):
        self._uniform_cpu.pop(channel, None)
        if tuple(image.size) != (n, n):
            raise ValueError(f'{image.name} is {tuple(image.size)}, not the set resolution {n}')

    def _publish_in_place(self, image, channel, n, fmt, draw):
        """``draw([image, copy])`` writes level 0 of ``image`` and the copy its mips reduce; the mips
        follow three channels to a draw (``_flush_mips``). The publication pass draws them, or a
        fused chain that publishes its result (``fusion.publish``)."""
        import gpu
        dev = device()
        target = gpu.texture.from_image(image)
        if self._pending_mips and self._pending_mips[0][2] != n:
            self._flush_mips()
        chain = dev.mip_scratch(self, len(self._pending_mips), n, fmt) if n <= MIP_SCRATCH_SIDE else None
        kept = chain is not None
        if not kept:
            chain = dev.texture(n, fmt)
        try:
            draw([target, chain])
        except BaseException:
            if not kept:
                dev.release(chain, n, fmt)
            elif not self._pending_mips:
                dev.end_mip_scratch(self)   # nothing of this engine's is queued on it
            raise
        if self._presentation is None:
            from ...native_bridge import lifecycle
            self._presentation = InPlacePresentation(dev, self.scene_uid)
            lifecycle.track(self._presentation)
        self._presentation.published(channel, image, target)
        self._pending_mips.append((target, chain, n, fmt, kept))   # the queue releases a leased chain
        if len(self._pending_mips) >= MIP_LANES or self.profiling:   # a profile times each channel's own
            self._flush_mips()

    @contextmanager
    def _mip_batches(self):
        """An evaluation's channels published in place get their mips before it returns, or fails."""
        try:
            yield
        finally:
            if self._pending_mips:
                started = perf_counter()
                self._flush_mips()
                self.stages['publish_ms'] += (perf_counter() - started) * 1000
            device().end_mip_scratch(self)

    def _flush_mips(self):
        """The mip chains of the channels ``_publish`` queued, three channels to a draw as a revision
        commit renders them: each level of each lane is the same exact box reduction of the level
        above it, written to the image's level and to the texture the next level reads."""
        pending, self._pending_mips = self._pending_mips, []
        if not pending:
            return
        dev = device()
        for width in range(1, MIP_LANES + 1):   # every batch width with the first: no compile in a later edit
            dev.fixed('mip' if width == 1 else f'mip_{width}')
        scratch = []
        try:
            for start in range(0, len(pending), MIP_LANES):
                batch = pending[start:start + MIP_LANES]
                count = len(batch)
                shader = dev.fixed('mip' if count == 1 else f'mip_{count}')
                sources, size, level = [entry[1] for entry in batch], batch[0][2], 0
                while size > 1:
                    size, level = max(1, size // 2), level + 1
                    slots, samplers, smaller = [], [], []
                    for lane, (target, _, _, fmt, kept) in enumerate(batch):
                        texture = dev.mip_scratch(self, lane, size, fmt) if kept else None
                        if texture is None:
                            texture = dev.texture(size, fmt)
                            scratch.append((texture, size, fmt))
                        slots.extend(({'texture': target, 'mip': level}, texture))
                        samplers.append(('op_source' if count == 1 else f'op_source_{lane}', sources[lane]))
                        smaller.append(texture)
                    dev.draw(shader, slots, size, samplers)
                    sources = smaller
        finally:
            for _, chain, n, fmt, kept in pending:
                if not kept:
                    dev.release(chain, n, fmt)
            for texture, size, fmt in scratch:
                dev.release(texture, size, fmt)
            dev.end_mip_scratch(self)

    # ---- CPU pixels ----------------------------------------------------------------------------

    def sync_pixels(self, channels=None):
        """Copy published results into the images' pixel buffers (save, render, export, idle)."""
        import gpu
        ts = self.ts()
        pending = set(channels or self.dirty_cpu) & self.dirty_cpu
        for channel in sorted(pending):
            image = _output_image(ts, channel)
            if image is None:
                continue
            n = image.size[0]
            texture = gpu.texture.from_image(image)
            # Reuse the same buffer as Vulkan publication: avoid another full-size
            # GPU read buffer and NumPy copy for every channel on save/export.
            image.pixels.foreach_set(device().readback(texture, n))
            metrics.count('engine_image_pixel_writes')
            metrics.count('engine_image_pixel_write_bytes', n * n * 16)
            self.dirty_cpu.discard(channel)


def _inlined_in(graph, inline, key):
    found, work = [], [key]
    while work:
        current = work.pop(0)
        for producer, _, _ in graph.units[current].imports:
            if producer in inline and producer not in found and producer in graph.units:
                found.append(producer)
                work.append(producer)
    return found


def _same_shape(old, new, nodes, sockets):
    """Whether two unit trees have the same nodes (kind and sockets, in order) and links; fills
    ``nodes`` and ``sockets`` (old id -> new) for ``_rebind``."""
    if old is None or new is None:
        return False
    a, b = list(old.nodes), list(new.nodes)
    if len(a) != len(b) or len(old.links) != len(new.links):
        return False
    for x, y in zip(a, b):
        if x.bl_idname != y.bl_idname or len(x.inputs) != len(y.inputs) or len(x.outputs) != len(y.outputs):
            return False
        nodes[id(x)] = y
        for p, q in zip(x.inputs, y.inputs):
            sockets[id(p)] = q
        for p, q in zip(x.outputs, y.outputs):
            sockets[id(p)] = q
    linked = {(id(link.from_socket), id(link.to_socket)) for link in new.links}
    return all((id(sockets.get(id(link.from_socket))), id(sockets.get(id(link.to_socket)))) in linked
               for link in old.links)


def _rebind(program, nodes, sockets):
    """``program`` reading the matching nodes and sockets of another tree of the same shape (its
    source, native uniform bank and revision stay); None when something is not mapped."""
    samplers = []
    for sampler in program.samplers:
        node = sampler.node
        if node is not None and id(node) not in nodes:
            return None
        source = _rebind(sampler.source, nodes, sockets) if sampler.source is not None else None
        if sampler.source is not None and source is None:
            return None
        samplers.append(replace(sampler, node=nodes[id(node)] if node is not None else None, source=source))
    if any(id(socket) not in sockets for socket in program.params):
        return None
    return replace(program, samplers=samplers, params=[sockets[id(socket)] for socket in program.params])


def _positions(graph):
    """What each CHANNEL unit composes over, each channel's result, and the render order."""
    from ...native_bridge.lifecycle import active_library
    if (lib := active_library()) is not None:
        from ...native_bridge.stack import positions
        return positions(lib, graph)
    positions, finals, orders = {}, {}, {}

    def walk(steps, backdrop, order):
        for step in steps:
            grouped = None
            if 'children' in step:
                start = backdrop if step['passthrough'] else ('const', (0.0, 0.0, 0.0, 0.0))
                grouped = walk(step['children'], start, order)
            positions[step['key']] = (backdrop, grouped)
            order.append(step['key'])
            backdrop = ('unit', step['key'])
        return backdrop

    for channel, steps in graph.chains.items():
        order = []
        finals[channel] = walk(steps, ('const', (*DEFAULTS[channel][:3], 1.0)), order)
        orders[channel] = order
    return positions, finals, orders


def _alpha_signature(image):
    if image is None:
        return None
    try:
        return (image.alpha_mode, bool(getattr(image.colorspace_settings, 'is_data', False)))
    except ReferenceError:
        return None


def _output_image(ts, channel):
    return next((output.image for output in ts.outputs if output.channel == channel and output.image), None)


def write_pixels(image, values):
    pixels = np.ascontiguousarray(values, np.float32).ravel()
    image.pixels.foreach_set(pixels)
    metrics.count('engine_image_pixel_writes')
    metrics.count('engine_image_pixel_write_bytes', pixels.nbytes)
