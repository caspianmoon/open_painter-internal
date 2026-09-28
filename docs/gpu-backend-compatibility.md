# GPU backend compatibility

The evaluator uses Blender's `GPUShaderCreateInfo`, textures, uniform buffers and
framebuffers. It does not issue raw OpenGL or Vulkan commands.

This hardening pass fixes and optimizes the shared paths:

- Shader interfaces are inspected once after compilation. Uniforms and blocks
  removed by the OpenGL compiler are skipped during drawing. Samplers use ordinary
  uniform locations only on OpenGL; Metal and Vulkan retain descriptor bindings.
- Offscreen drawing covers the complete target and restores the caller's viewport,
  scissor rectangle, blending and depth state, including after an exception.
  Readback, clears and profiling calibration preserve state too.
- Fullscreen evaluator passes use one oversized triangle, eliminating the quad's
  diagonal and avoiding triangle-fan conversion.
- Vulkan publication continues through owned render textures and a reusable CPU
  buffer, without attaching Blender Image textures as render targets. Existing
  publication budgeting, mip generation and export synchronization are retained.
- OpenGL/Metal save synchronization now reuses the readback buffer instead of
  allocating another full-channel NumPy copy. A 2048² RGBA float buffer is 64 MiB;
  the device retains one readback resolution at a time.
- A successful channel remains marked for synchronization if a later channel
  fails. Failed publication returns temporary textures to the pool.
- The vector brush guide uses Blender's polyline shader for its width, and brush
  and graph overlays restore the drawing state they change.

The API distinctions follow Blender's [GPU interface implementation](https://github.com/blender/blender/blob/main/source/blender/python/gpu/gpu_py_shader.cc)
and [framebuffer state documentation](https://docs.blender.org/api/5.2/gpu.state.html).

## Repeatable validation

Run the compatibility matrix with an isolated Blender profile:

```sh
python3 scripts/run_gpu_compatibility.py --blender /path/to/blender
```

The matrix includes simulated API contracts, actual shader/pixel/state tests,
procedural reference parity, complete layer-stack parity, the same stack through
forced pixel publication, publication budgeting, engine stages and material-node
lifecycle. It caps the engine at 16 fragment samplers by default, even on a device
that supports more (`--sampler-limit` changes this cap).

`--backend opengl`, `--backend vulkan` or `--backend metal` requires that actual
backend. A driver fallback fails the run. Each fresh GPU process records the real
backend, renderer, Blender version and sampler cap. Reports and logs are under
`.blender-test/backend-validation/` (or `--output`).

Without Vulkan hardware, the strict fake GPU rejects image render attachments
and models descriptor samplers without ordinary uniform locations. The strict
OpenGL model rejects optimized-out uniform writes. Real GPU tests then exercise
the pixel-publication path on the available device. These checks test add-on
contracts and output pixels; they do not simulate a Vulkan driver, SPIR-V compiler,
device synchronization or vendor-specific behavior.

The contract unit tests are included in `scripts/run_pure_tests.py`, so they also
run in the existing CPU CI job. The real-window regressions remain available with
`python3 scripts/run_tests.py --gpu-only`.

## Local evidence, 2026-09-28

Blender 5.2.1 LTS, macOS Apple Silicon, actual backend **METAL**:

- Compatibility matrix: eight suites passed, including eight simulated API tests
  and layer-stack parity through both native and forced pixel publication.
- Pure suite: 293 tests run, 291 passed and two skipped.
- Real-window GPU suites: all six passed, including 183 compositor checks,
  102 checkpoint checks, 38 procedural configurations, 11 mask cases and five
  surface-relief checks, plus painting/save/export preview lifecycle.

There is no local Vulkan or OpenGL device. Those drivers remain unverified.
These results establish the tested cases, not universal correctness or a measured
performance gain on either target backend.
