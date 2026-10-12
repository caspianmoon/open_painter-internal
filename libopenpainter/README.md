# libopenpainter — Open Painter's portable C17 runtime

ABI 14 implements owned typed shader IR and parameter banks; the 71-family
procedural compiler; all 28 blend modes; all 30 pointwise effects and mask folding; nested mask hierarchy/prefix planning; layer/folder composition; incremental dependency
plans and native batch execution with dependency lifetime holds; accepted channel
state, output change selection and atomic channel dispatch;
GPU texture pooling and retained-cache policy; immutable material revisions;
brush kernels; geometry bounds/UV rasterization/surface walks; owned surface/anchor snapshots;
UV topology, stable island IDs, borders, seam pairing/centroids, closest-seam
selection, island bounds and correspondence/Jacobians; spatial-filter plans; and failure-safe multichannel
publication orchestration; suffix collapse of affine runs, chain fusion and idle arming;
and the CPU kernels for export pixels, PNG filtering, SHA-256, UV rasterization, padding and
brush coverage, with exact NEON and SSE4.2 paths. The optional compiled CPython module drives
Blender's public GPU API on the thread that owns its context, on Metal, Vulkan and OpenGL. It
does not create another device or read private Blender memory.

The older constant-fill document/snapshot API remains a CPU reference fixture.
The normal add-on uses the program/graph/resource/revision APIs for GPU work.
See [ownership and remaining work](../docs/native/migration-status.md) and the
[platform notes](../docs/native/platforms.md). Full migration is not claimed while the
documented host orchestration remains in Python.

```sh
cmake -S libopenpainter -B /tmp/openpainter-native-build
cmake --build /tmp/openpainter-native-build
ctest --test-dir /tmp/openpainter-native-build --output-on-failure
```

The pure C build has no Python dependency. For the production binding, add
`-DOP_PYTHON_INCLUDE=/path/to/cpython/include/python3.13` (CPython 3.11+ headers).
The module targets the CPython 3.11 limited ABI; it is tested in Blender's bundled
3.13 interpreter. Submission buffers are copied before native operations release
the GIL. GPU operations stay on the context-owning thread with the GIL held.

```sh
python3 scripts/build_native.py --python-include /path/to/python/headers \
  --package dist/open_painter-0.15.0-macos-arm64.zip
```

The package contains the dylib, compiled binding and SHA-256/ABI manifest.
Installation needs no compiler or environment variable. The package names the platform it
was built on; build it on each platform you release for. The native scheduler runs on Metal,
Vulkan and, on Windows, OpenGL; elsewhere the CPU kernels still run.

```sh
cmake -S libopenpainter -B /tmp/openpainter-native-ubsan \
  -DOP_SANITIZERS=ON -DOP_SANITIZER_SET=undefined
cmake --build /tmp/openpainter-native-ubsan
ctest --test-dir /tmp/openpainter-native-ubsan --output-on-failure
```

Local Apple clang AddressSanitizer startup previously timed out. That is not a
passing safety result. The CI sanitizer matrix remains separate evidence until
executed. No native library is manually unloaded during a Blender session.
