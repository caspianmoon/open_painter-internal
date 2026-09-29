# libopenpainter — embedded C17 runtime

The embedded library lives here because `native/` contains the separate
standalone application. No standalone application files are changed.

ABI 8 implements owned typed shader IR and parameter banks; the 71-family
procedural compiler; all 28 blend modes; all 30 pointwise effects and mask folding; layer/folder composition; incremental dependency
plans and native batch execution with dependency lifetime holds; accepted channel
state, output change selection and atomic channel dispatch;
GPU texture pooling and retained-cache policy; immutable material revisions;
brush kernels; geometry bounds/UV rasterization/surface walks; owned surface/anchor snapshots;
UV topology, stable island IDs, borders, seam pairing/centroids, closest-seam
selection, island bounds and correspondence/Jacobians; spatial-filter plans; and failure-safe multichannel
publication orchestration. The optional compiled CPython module uses Blender's public GPU API on its
Metal context thread. It does not create another device or read private Blender
memory.

The older constant-fill document/snapshot API remains a CPU reference fixture.
The normal add-on uses the program/graph/resource/revision APIs for GPU work.
See [ownership and remaining work](../docs/native/migration-status.md) and
[executed evidence](../docs/native/release-evidence.md). Full migration is not
claimed while the documented host orchestration remains in Python.

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
  --package dist/open_painter-macos-arm64-native.zip
```

The package contains the dylib, compiled binding and SHA-256/ABI manifest.
Installation needs no compiler or environment variable. Windows/Vulkan backend
work is deferred. Non-Metal hosts retain the existing executor.

```sh
cmake -S libopenpainter -B /tmp/openpainter-native-ubsan \
  -DOP_SANITIZERS=ON -DOP_SANITIZER_SET=undefined
cmake --build /tmp/openpainter-native-ubsan
ctest --test-dir /tmp/openpainter-native-ubsan --output-on-failure
```

Local Apple clang AddressSanitizer startup previously timed out. That is not a
passing safety result. The CI sanitizer matrix remains separate evidence until
executed. No native library is manually unloaded during a Blender session.
