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
from collections import Counter, OrderedDict
from contextlib import contextmanager
from time import perf_counter

import numpy as np

from ...constants import CHANNELS, DEFAULTS
from .. import metrics
from . import glsl, stages

RGBA = 'RGBA32F'
HALF = 'RGBA16F'   # display precision for every channel but Height (bump detail is sub-1/2048)
SCALAR = 'R32F'
RETAIN_BYTES = 512 * 1024 * 1024    # SRC/MASK results kept between evaluations, per texture set
POOL_SPARES = 3                      # free textures of one size and format kept for reuse
PRECISE = False   # full-float intermediates for every channel by default (parity tests)
# Publication through the images' pixel buffers: None follows the backend (Vulkan), True/False
# force it (tests run the Vulkan path on Metal). On Vulkan Blender's image textures are storage,
# not render targets, and every pixel write makes Blender rebuild the image's texture and mips.
PIXEL_PUBLISH = True if os.environ.get('OP_ENGINE_PIXEL_PUBLISH') == '1' else None   # the env: whole suites
PIXEL_BUDGET_MS = 20.0   # per evaluation through pixels: the channels waiting longest publish first

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
        self.shaders = {}
        self._interfaces = {}
        self.batch = None
        self.pool = {}
        self.compiles = 0
        self.compile_seconds = 0.0
        self.passes = 0
        self.wait_cost = 0.0 # ms of submitting a pass that does nothing and waiting for it (``calibrate``)
        self._readbacks = {}  # side -> float buffer a pixel publication reads into (no allocation each time)
        self._fixed = {}
        try:
            self.max_samplers = int(gpu.capabilities.max_textures_frag_get())
        except Exception:
            self.max_samplers = 16

    # ---- programs -----------------------------------------------------------------

    def shader(self, program):
        key = (program.source, tuple(s.name for s in program.samplers), program.parameter_slots,
               len(program.objects), len(program.targets), program.procedurals)
        shader = self.shaders.get(key)
        if shader is None:
            shader = self._create(program.library + program.source, [s.name for s in program.samplers],
                                  program.parameter_slots, len(program.objects), len(program.targets), preamble=True)
            self.shaders[key] = shader
        return shader

    def _create(self, source, samplers, parameter_slots=0, objects=0, targets=1, preamble=False, ints=()):
        import gpu
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
        self._interfaces[shader] = (uniforms, block)
        self.compiles += 1
        elapsed = perf_counter() - started
        self.compile_seconds += elapsed
        metrics.count('engine_program_compiles')
        metrics.sample('engine_program_compile_ms', elapsed * 1000)
        return shader

    def fixed(self, name):
        shader = self._fixed.get(name)
        if shader is None:
            source, samplers, ints, targets = {
                'publish': (PUBLISH, ['op_source', 'op_donor'], ('op_mode', 'op_pad'), 2),
                'publish_pixels': (PUBLISH_PIXELS, ['op_source', 'op_donor'], ('op_mode', 'op_pad'), 1),
                'mip': (MIP, ['op_source'], (), 2),
                'finish': (FINISH, ['op_source'], ('op_mode',), 1),
                'pad': (PAD, ['op_source', 'op_donor'], (), 1),
                'downsample': (DOWNSAMPLE, ['op_source'], (), 1),
                'copy': (COPY, ['op_source'], (), 1)}[name]
            shader = self._fixed[name] = self._create(source, samplers, targets=targets, ints=ints)
        return shader

    # ---- textures -----------------------------------------------------------------

    def texture(self, n, fmt=RGBA):
        import gpu
        free = self.pool.get((n, fmt))
        while free:
            texture = free.pop()
            if texture.width == n and texture.height == n:
                return texture   # (a texture released under another size is dropped, never reused)
        metrics.count('engine_texture_allocations')
        return gpu.types.GPUTexture((n, n), format=fmt)

    def release(self, texture, n, fmt=RGBA):
        if texture is not None:
            self.pool.setdefault((n, fmt), []).append(texture)

    def upload(self, array, fmt=RGBA):
        import gpu
        array = np.ascontiguousarray(array, np.float32)
        h, w = array.shape[:2]
        return gpu.types.GPUTexture((w, h), format=fmt, data=gpu.types.Buffer('FLOAT', array.size, array.ravel()))

    def trim(self, spares=0):
        """Free pooled textures beyond ``spares`` per size and format (0: all of them)."""
        for key, free in self.pool.items():
            del free[spares:]

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

    def draw(self, shader, targets, size, samplers=(), ubo=None, ints=None, mip=0):
        """One full-screen pass. A target is a texture or a {'texture': t, 'mip': k} slot."""
        import gpu
        slots = [({'texture': texture, 'mip': mip} if mip and not isinstance(texture, dict) else texture)
                 for texture in targets]
        framebuffer = gpu.types.GPUFrameBuffer(color_slots=slots)
        uniforms, block = self._interfaces[shader]
        with framebuffer.bind():
            gpu.state.viewport_set(0, 0, size, size)
            # Keep the caller's scissor-test enable state; cover the entire target
            # whether clipping is enabled or disabled. state() restores its rectangle.
            gpu.state.scissor_set(0, 0, size, size)
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
        """Results reach the images through their pixel buffers (Vulkan), not a framebuffer pass."""
        return PIXEL_PUBLISH if PIXEL_PUBLISH is not None else self.backend == 'VULKAN'

    def readback(self, texture, n):
        """The n × n ``texture`` as RGBA floats in a buffer reused between publications: a 2K
        channel is 64 MB, allocated once rather than on every stroke tick."""
        import gpu
        buffer = self._readbacks.get(n)
        if buffer is None:
            self._readbacks.clear()   # one resolution at a time
            buffer = self._readbacks[n] = gpu.types.Buffer('FLOAT', n * n * 4)
        framebuffer = gpu.types.GPUFrameBuffer(color_slots=(texture,))
        with self.state(), framebuffer.bind():
            framebuffer.read_color(0, 0, n, n, 4, 0, 'FLOAT', data=buffer)
        return buffer

    def finish(self):
        """Wait for every pass submitted so far (a profile then times each pass alone)."""
        import gpu
        texture = self.texture(1, SCALAR)
        framebuffer = gpu.types.GPUFrameBuffer(color_slots=(texture,))
        try:
            with self.state(), framebuffer.bind():
                _wait(framebuffer)
        finally:
            self.release(texture, 1, SCALAR)

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
    _device = None
    Engine._constants.clear()


class UnitState:
    __slots__ = ('unit', 'program', 'shader', 'signature', 'stamp', 'textures', 'formats', 'ubo', 'ubo_data')

    def __init__(self, unit):
        self.unit = unit
        self.program = self.shader = self.signature = self.stamp = None
        self.textures = None
        self.formats = ()
        self.ubo = self.ubo_data = None

    def uniforms(self):
        """The unit's uniform buffer holding its current parameter values (None without any)."""
        import gpu
        program = self.program
        if not program.params and not program.objects:
            return None
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


def _image_identity(image, epochs):
    if image is None:
        return None
    try:
        # A CPU boundary leaf is published in place (placeholder → pixels, or re-keyed and
        # regenerated): its readiness and publication serial are part of its identity.
        return (image.as_pointer(), image.name, tuple(image.size), image.get('open_painter_shader_resource'),
                bool(image.get('op_shader_resource_ready')), image.get('op_resource_serial', 0),
                image.get('open_painter_pixel_revision', 0), epochs.get(image.as_pointer(), 0),
                image.alpha_mode, image.colorspace_settings.name, image.is_float)
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
        self.set_id = ts.id
        self.graph = None
        self.states = {}
        self.positions = {}             # CHANNEL key -> (backdrop ref, grouped ref)
        self.finals = {}                # channel -> ref of the chain's result
        self.orders = {}                # channel -> CHANNEL keys in render order
        self.keep = set()               # CHANNEL results kept between evaluations (checkpoints)
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
        self.waiting = {}               # channel -> when its change was first held back (pixel budget)
        self.deferred = set()           # channels the last evaluation held back for the next

    # ---- graph -----------------------------------------------------------------------

    def adopt(self, graph):
        """Take a (re)built graph; states of units it adopted unchanged keep their programs and results."""
        n_before = self.graph.n if self.graph is not None else None
        self.graph = graph
        if n_before is not None and n_before != graph.n:
            self.close(n_before)   # the textures were rendered at the previous resolution
        live = {}
        for key, unit in graph.units.items():
            state = self.states.get(key)
            if state is None or state.unit is not unit:
                if state is not None:
                    self._free(key, state, n_before)
                state = UnitState(unit)
            live[key] = state
        for key, state in self.states.items():
            if key not in live:
                self._free(key, state, n_before)
        self.states = live
        self.positions, self.finals, self.orders = _positions(graph)
        # A content or mask unit read by one unit only is compiled into that unit: one pass
        # and one full-resolution texture less for every layer channel.
        self.inline = frozenset(key for key in graph.units if key[0] in {'SRC', 'MASK'}
                                and len(graph.consumers.get(key, ())) == 1)
        self.keep &= set(live)
        self.published.clear()   # a rebuilt graph re-derives every chain stamp

    def _free(self, key, state, n):
        if state.textures and n is not None:
            for texture, fmt in zip(state.textures, state.formats):
                device().release(texture, n, fmt)
        state.textures = None
        self.retained.pop(key, None)

    def close(self, n=None):
        """Release every unit's textures to the pool, under ``n`` (default: the graph's side): the
        resolution they were created at, or a later request would be handed a texture of another size."""
        n = n if n is not None else (self.graph.n if self.graph is not None else None)
        for key, state in list(self.states.items()):
            self._free(key, state, n)
        self.states = {}
        self.retained.clear()
        self.keep.clear()
        self.published = {}
        self.images = {}
        self.geometry = {}
        self.geometry_key = None
        self.padding = None

    # ---- programs ----------------------------------------------------------------------

    def _signature(self, key):
        # Code depends on the unit's node structure, the producers compiled into it and the
        # images it binds (alpha handling). An inlined producer counts by its compiled tree: a
        # mask or source rebuilt under an adopted consumer (a mask's kind changed) must reach
        # the consumer's program, whose code and uniform sockets came from the old tree. The
        # tree itself is held (compared by identity), so a freed tree's id is never matched.
        units = self.graph.units
        return (id(self.states[key].unit.tree), tuple((producer, units[producer].tree) for producer in self._inlined(key)),
                self.precise, tuple(_alpha_signature(image) for _, image in self._images(key)))

    def _state(self, key):
        state = self.states[key]
        unit = state.unit
        signature = self._signature(key)
        if state.signature != signature:
            started, compiled = perf_counter(), device().compile_seconds
            program = glsl.translate(self.graph, unit, self.inline)
            limit = device().max_samplers
            while len(program.samplers) > limit:
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
            if state.textures:
                n = self.graph.n
                for texture, fmt in zip(state.textures, state.formats):
                    device().release(texture, n, fmt)
                state.textures = None
            state.formats = tuple(self._format(key, color) for color, _ in state.program.targets)
            compiled = device().compile_seconds - compiled
            self.stages['compile_ms'] += compiled * 1000
            self.stages['translate_ms'] += (perf_counter() - started - compiled) * 1000
        return state

    def _inlined(self, key):
        """The producers compiled into ``key``'s program, nearest first."""
        found, work = [], [key]
        while work:
            current = work.pop(0)
            for producer, _, _ in self.graph.units[current].imports:
                if producer in self.inline and producer not in found and producer in self.graph.units:
                    found.append(producer)
                    work.append(producer)
        return found

    def _images(self, key):
        """Image nodes of a unit and of the producers compiled into it."""
        return [item for current in (key, *self._inlined(key)) for item in self.graph.unit_images(current)]

    def _format(self, key, color):
        if color == 'Value':
            return SCALAR
        channel = key[2] if key[0] in {'CHANNEL', 'SRC'} else None
        return RGBA if (self.precise or channel == 'HEIGHT') else HALF

    # ---- stamps --------------------------------------------------------------------------

    def _own_stamp(self, state, epochs):
        program = state.program
        # A program reading the surface (world position, normal, tangent frame) is stale when
        # the assigned geometry moves or deforms.
        surface = (self._surface() if any(sampler.kind in GEOMETRY for sampler in program.samplers) else None)
        return (id(state.shader), tuple(float(socket.default_value) for socket in program.params),
                tuple(_image_identity(sampler.node.image, epochs) for sampler in program.samplers
                      if sampler.kind == 'image'),
                tuple(_object_matrix(obj) for obj in program.objects), surface)

    def _surface(self):
        """The geometry key of this evaluation (computed once per evaluation)."""
        if self._surface_key is None:
            self._surface_key = (self._geometry_stamp(self.graph.n),)
        return self._surface_key[0]

    def _stamp(self, key, memo, epochs, stack=()):
        if key in memo:
            return memo[key]
        if key in stack:
            raise ValueError('Published inputs contain a reference cycle')
        state = self._state(key)
        stack = stack + (key,)
        imports = tuple(sorted({sampler.producer[0] for sampler in state.program.samplers if sampler.kind == 'import'}))
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

    def _texture_of(self, ref, n, memo, epochs):
        if ref is None:
            return None
        if ref[0] == 'const':
            return self._constant(n, ref[1])
        return self._render(ref[1], n, memo, epochs)[0]

    def _render(self, key, n, memo, epochs):
        """Render a unit and whatever it reads; returns its target textures."""
        state = self._state(key)
        stamp = self._stamp(key, memo, epochs)
        if state.textures is not None and state.stamp == stamp:
            if key in self.retained:
                self.retained.move_to_end(key)
            return state.textures
        program = state.program
        position = self.positions.get(key)
        # Inputs rendered for this draw stay resident until it has drawn: the retention
        # budget must not hand their textures back to the pool (as this unit's target).
        inputs = {sampler.producer[0] for sampler in program.samplers if sampler.kind == 'import'}
        inputs |= {ref[1] for ref in (position or ()) if ref is not None and ref[0] == 'unit'}
        self._in_use.update(inputs)
        try:
            backdrop = self._texture_of(position[0], n, memo, epochs) if position else None
            grouped = self._texture_of(position[1], n, memo, epochs) if position and position[1] else None
            samplers = [(sampler.name, self._bind(sampler, n, memo, epochs, backdrop, grouped))
                        for sampler in program.samplers]
            dev = device()
            if state.textures is None:
                state.textures = [dev.texture(n, fmt) for fmt in state.formats]
            drawn = perf_counter()
            uniforms = state.uniforms()
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
        self.rendered.append(key)
        # What a chain step composed over is not needed once this step has drawn.
        if position:
            for ref in position:
                if ref is not None and ref[0] == 'unit':
                    self._settle(ref[1], n)
        if key[0] != 'CHANNEL' or self.graph.consumers.get(key):
            self._retain(key, state, n)
        return state.textures

    def _gpu_time(self, state, n, samplers, uniforms, repeats=5):
        """A pass's GPU milliseconds: (``repeats`` draws and a wait) less (one draw and a wait),
        over ``repeats`` − 1, so the wait's own cost cancels. Each draw is a render pass of its
        own (a tiled GPU cannot merge them) and writes the same result again."""
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
        dev.passes = passes
        return max(0.0, (many - single) / (repeats - 1))

    def _settle(self, key, n):
        """Release a CHANNEL result its consumer has drawn with, unless it is kept."""
        if key in self.keep or self.graph.consumers.get(key) or self._in_use[key] > 0:
            return
        state = self.states.get(key)
        if state is not None and state.textures:
            for texture, fmt in zip(state.textures, state.formats):
                device().release(texture, n, fmt)
            state.textures = None

    def _bind(self, sampler, n, memo, epochs, backdrop, grouped):
        kind = sampler.kind
        if kind == 'image':
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

    def _retain(self, key, state, n):
        self.retained[key] = sum((4 if fmt == SCALAR else 16) * n * n for fmt in state.formats)
        self.retained.move_to_end(key)
        total = sum(self.retained.values())
        for victim in list(self.retained):
            if total <= RETAIN_BYTES:
                break
            if victim == key or self._in_use[victim] > 0:
                continue
            total -= self.retained.pop(victim)
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
        """Keep the result just below the lowest changed unit of the chain: the next edit
        of the same layer re-renders from there upward only."""
        order = self.orders.get(channel, ())
        lowest = next((key for key in order if self.states[key].stamp != self._stamp(key, memo, epochs)), None)
        keep = {key for key in self.keep if key[2] != channel}
        if lowest is not None:
            below = self.positions[lowest][0]
            if below is not None and below[0] == 'unit':
                keep.add(below[1])
        released = {key for key in self.keep if key[2] == channel} - keep
        self.keep = keep
        return released

    # ---- geometry --------------------------------------------------------------------------

    def _geometry_texture(self, kind, n):
        key = self._surface()
        if self.geometry_key != key:
            self.geometry = {}
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
        texture = None
        try:
            owned = padding.ownership(ts, n)
            donor, _ = padding.nearest_donors(owned.covered, owned.island, padding.automatic_padding(n))
            if (donor >= 0).any():
                xy = np.full((n, n, 2), -1.0, np.float32)
                valid = donor >= 0
                xy[..., 0] = np.where(valid, donor % n, -1)
                xy[..., 1] = np.where(valid, donor // n, -1)
                texture = device().upload(xy, 'RG32F')
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
        self._in_use = Counter()
        self._surface_key = None
        pixels = dev.pixel_publish
        if budget is None:
            budget = PIXEL_BUDGET_MS if pixels and images is None and not self.profiling else False
        now = perf_counter()
        order = list(channels or CHANNELS)
        if budget is not False:
            order.sort(key=lambda channel: (self.waiting.get(channel, now), CHANNELS.index(channel)))
        deferred, spent = set(), 0.0
        with dev.state():
            for channel in order:
                image = (images or {}).get(channel) or _output_image(ts, channel)
                if image is None:
                    continue
                stamp = self._ref_stamp(self.finals[channel], memo, epochs)
                pointer = image.as_pointer()
                if self.published.get(channel) == stamp and self.images.get(channel) == pointer:
                    self.waiting.pop(channel, None)
                    continue
                if budget is not False and written and spent >= budget:
                    deferred.add(channel)   # published by a later evaluation (cache.tick keeps coming)
                    self.waiting.setdefault(channel, now)
                    continue
                released = self._checkpoints(channel, memo, epochs)
                for key in released:
                    self._settle(key, n)
                final = self.finals[channel]
                result = self._texture_of(final, n, memo, epochs)
                if self.profiling:
                    dev.finish()   # what was queued before is not publishing's
                publishing, inputs = perf_counter(), self.stages['inputs_ms']
                self._publish(image, channel, result, n)
                # A later channel may fail. Already published GPU pixels must still
                # be synchronized on save/export, even when this evaluation aborts.
                if pixels:
                    self.dirty_cpu.discard(channel)
                else:
                    self.dirty_cpu.add(channel)
                if self.profiling:
                    dev.finish()
                # The padding table it may build first counts as a CPU input, not as publishing;
                # a profile's wait is not the GPU's time.
                elapsed = max(0.0, (perf_counter() - publishing) * 1000
                              - (self.stages['inputs_ms'] - inputs)
                              - (dev.wait_cost if self.profiling else 0.0))
                self.stages['publish_ms'] += elapsed
                spent += elapsed
                self.stages['published'].append(channel)
                self.waiting.pop(channel, None)
                if final[0] == 'unit':
                    self._settle(final[1], n)
                self.published[channel] = stamp
                self.images[channel] = pointer
                written.add(channel)
        self.deferred = deferred
        self.stages['deferred'] = sorted(deferred)
        dev.trim(POOL_SPARES)
        self.stages['compiles'] = dev.compiles - compiles
        self.stages['publish_path'] = ('pixels' if pixels else 'framebuffer') if written else ''
        self.report = {'evaluate_ms': (perf_counter() - started) * 1000, 'channels': sorted(written),
                       'passes': dev.passes - passes, 'units_rendered': len(self.rendered),
                       'programs': len(dev.shaders), 'compiles': dev.compiles,
                       'stages': self.stages}
        if written:
            metrics.count('engine_channel_publications', len(written))
            metrics.sample('engine_evaluate_ms', self.report['evaluate_ms'])
        return written

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
        with device().state():
            texture = self._texture_of(self.finals[channel], n, memo, epochs or {})
            values = np.array(texture.read(), np.float32).reshape(texture.height, texture.width, 4)
            return np.broadcast_to(values, (n, n, 4)).copy() if values.shape[0] == 1 else values

    def _publish(self, image, channel, result, n):
        import gpu
        dev = device()
        if tuple(image.size) != (n, n):
            raise ValueError(f'{image.name} is {tuple(image.size)}, not the set resolution {n}')
        donor = self._donor(n) if self.pad else None
        fmt = RGBA if (self.precise or channel == 'HEIGHT') else HALF
        chain = dev.texture(n, fmt)
        ints = {'op_mode': 1 if channel == 'NORMAL' else 0, 'op_pad': 1 if donor is not None else 0}
        samplers = [('op_source', result), ('op_donor', donor if donor is not None else result)]
        scratch = [(chain, n)]
        try:
            if dev.pixel_publish:
                # Vulkan: image textures cannot be render targets. Reuse a readback
                # buffer; Blender rebuilds the image texture and mips on its next draw.
                dev.draw(dev.fixed('publish_pixels'), [chain], n, samplers, ints=ints)
                image.pixels.foreach_set(dev.readback(chain, n))
                return
            target = gpu.texture.from_image(image)
            # Finish, pad and write level 0 in one pass; each mip level in one more.
            dev.draw(dev.fixed('publish'), [target, chain], n, samplers, ints=ints)
            source, size, level = chain, n, 0
            while size > 1:
                size = max(1, size // 2)
                level += 1
                smaller = dev.texture(size, fmt)
                scratch.append((smaller, size))
                dev.draw(dev.fixed('mip'), [{'texture': target, 'mip': level}, smaller], size, [('op_source', source)])
                source = smaller
        finally:
            for texture, size in scratch:
                dev.release(texture, size, fmt)

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
            self.dirty_cpu.discard(channel)


def _positions(graph):
    """What each CHANNEL unit composes over, each channel's result, and the render order."""
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
    image.pixels.foreach_set(np.ascontiguousarray(values, np.float32).ravel())
