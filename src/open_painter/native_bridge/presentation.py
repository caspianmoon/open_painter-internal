"""Gate 1 Route C experiment: C plan/kernel, public Blender GPU execution.

Only explicit diagnostics use this module. It never selects a production
backend. The shader dab is not an implementation of persistent brushwork.
All GPU calls must run on Blender's context-owning thread.
"""
import ctypes
from time import perf_counter_ns


class ProbePlan(ctypes.Structure):
    _fields_ = [("base", (ctypes.c_float * 4) * 8),
                ("coat", (ctypes.c_float * 4) * 8), ("opacity", ctypes.c_float)]


def build_plan(core, doc):
    lib = core._lib
    lib.op_probe_build.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                  ctypes.POINTER(ProbePlan)]
    lib.op_probe_build.restype = ctypes.c_int
    plan = ProbePlan()
    core._check(lib.op_probe_build(core._ctx, doc, ctypes.byref(plan)))
    return plan


class HostGPUProbe:
    """Borrow host image storage; never inspect Blender memory or native handles.

    Full mip publication reuses the existing public GPU adapter. Validation
    reads are explicit and separately counted by the harness. No thread,
    context, external device, window, or renderer is created here.
    """

    def __init__(self, core, size):
        import gpu
        from gpu_extras.batch import batch_for_shader
        from ..evaluation.engine import runtime
        runtime.ensure_gpu()
        self.backend = gpu.platform.backend_type_get()
        if size not in (512, 1024, 2048):
            raise ValueError("Probe resolution must be 512, 1024 or 2048")
        self.size = size
        self.core = core
        self.device = runtime.device()
        lib = core._lib
        lib.op_probe_fragment_source.argtypes = []
        lib.op_probe_fragment_source.restype = ctypes.c_char_p
        source = lib.op_probe_fragment_source().decode("utf-8")
        info = gpu.types.GPUShaderCreateInfo()
        info.vertex_in(0, 'VEC2', 'op_pos')
        info.vertex_source('void main() { gl_Position = vec4(op_pos, 0.0, 1.0); }')
        for name in ('op_base', 'op_coat', 'op_brush'):
            info.push_constant('VEC4', name)
        for name in ('op_opacity', 'op_size'):
            info.push_constant('FLOAT', name)
        info.fragment_out(0, 'VEC4', 'op_out0')
        info.fragment_source(source)
        self.shader = gpu.shader.create_from_info(info)
        self.batch = batch_for_shader(self.shader, 'TRIS',
                                     {'op_pos': ((-1, -1), (3, -1), (-1, 3))})
        self.trace = []
        info = gpu.types.GPUShaderCreateInfo()
        info.vertex_in(0, 'VEC2', 'op_pos')
        info.vertex_source('void main() { gl_Position = vec4(op_pos, 0.0, 1.0); }')
        info.sampler(0, 'FLOAT_2D', 'op_texture')
        info.push_constant('INT', 'op_level')
        info.fragment_out(0, 'VEC4', 'op_out0')
        info.fragment_source('void main() { op_out0 = textureLod(op_texture, '
                             'gl_FragCoord.xy / vec2(textureSize(op_texture, op_level)), '
                             'float(op_level)); }')
        self.sample_shader = gpu.shader.create_from_info(info)
        self.sample_batch = batch_for_shader(self.sample_shader, 'TRIS',
                                            {'op_pos': ((-1, -1), (3, -1), (-1, 3))})

    def read_mip(self, texture, level, size):
        """Intrusive diagnostic only: sample an explicit LOD then read scratch."""
        import gpu
        import numpy as np
        target = gpu.types.GPUTexture((size, size), format='RGBA32F')
        fb = gpu.types.GPUFrameBuffer(color_slots=(target,))
        with self.device.state(), fb.bind():
            gpu.state.viewport_set(0, 0, size, size)
            gpu.state.scissor_set(0, 0, size, size)
            self.sample_shader.bind()
            self.sample_shader.uniform_sampler('op_texture', texture)
            self.sample_shader.uniform_int('op_level', level)
            self.sample_batch.draw(self.sample_shader)
            return np.array(fb.read_color(0, 0, size, size, 4, 0, 'FLOAT')).reshape(size, size, 4)

    def publish(self, doc, images, brush=(0., 0., 0., 0.)):
        """Submit all channels/mips before returning to a host draw.

        This ordering is an experiment, not a verified retirement/eviction
        contract. Failure may leave mixed host storage; production stays off.
        """
        import gpu
        if self.backend == "VULKAN":
            # Reads (read_mip) only sample image storage; this renders into it.
            raise RuntimeError("Vulkan Blender image storage has no qualified render-target "
                               "usage or public external texture import; legacy backend retained")
        if len(images) != 8 or any(tuple(im.size) != (self.size, self.size) for im in images):
            raise ValueError("Eight full-resolution images are required")
        start = perf_counter_ns()
        plan = build_plan(self.core, doc)
        self.core.evaluate(doc)
        lease = self.core.acquire(doc)
        try:
            with self.device.state():
                # Stage every channel before touching Blender image storage.
                staged = []
                for channel in range(8):
                    texture = gpu.types.GPUTexture((self.size, self.size), format='RGBA32F')
                    staged.append(texture)
                    fb = gpu.types.GPUFrameBuffer(color_slots=(texture,))
                    with fb.bind():
                        gpu.state.viewport_set(0, 0, self.size, self.size)
                        gpu.state.scissor_set(0, 0, self.size, self.size)
                        self.shader.bind()
                        self.shader.uniform_float('op_base', tuple(plan.base[channel]))
                        self.shader.uniform_float('op_coat', tuple(plan.coat[channel]))
                        self.shader.uniform_float('op_opacity', plan.opacity)
                        self.shader.uniform_float('op_size', float(self.size))
                        self.shader.uniform_float('op_brush', brush)
                        self.batch.draw(self.shader)
                # Host public GPU API manages ownership; no foreign handle conversion.
                for im, source in zip(images, staged):
                    target = gpu.texture.from_image(im)
                    self.device.draw(self.device.fixed('copy'), [target], self.size,
                                     [('op_source', source)])
                    size, level = self.size, 0
                    while size > 1:
                        size //= 2
                        level += 1
                        smaller = gpu.types.GPUTexture((size, size), format='RGBA32F')
                        self.device.draw(self.device.fixed('mip'),
                                         [{'texture': target, 'mip': level}, smaller], size,
                                         [('op_source', source)])
                        source = smaller
            event = {'event_id': len(self.trace) + 1, 'generation': lease.generation,
                     'revision': lease.revision, 'channels': list(range(8)),
                     'submit_start_ns': start, 'submit_end_ns': perf_counter_ns(),
                     'publication': 'host_gpu_submitted', 'gpu_ready_ns': None,
                     'draw_ns': None, 'display_ns': None,
                     'interactive_readback_bytes': 0, 'image_pixel_write_bytes': 0}
            self.trace.append(event)
            return event
        finally:
            # This is a CPU document lease only. Host GPU owns submitted resources.
            self.core.release(doc, lease)
