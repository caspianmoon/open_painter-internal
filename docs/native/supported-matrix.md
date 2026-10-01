# Native integration matrix

| Configuration | Executed evidence | Selection |
| --- | --- | --- |
| macOS arm64, Apple M3 Pro, stock Blender 5.2.1 LTS / `9e2066aef7ef`, Metal | C/ASan/UBSan, all-channel and procedural parity, restore/cache/brush regressions, real viewport events | `c17-host-metal` when the bundled C library loads |
| Windows 11 x64, GTX 1050, stock Blender 5.2.2 LTS / `d13f752e3b9c`, OpenGL | MSVC C tests (vector and scalar), unit tests with the binding, every plan suite with and without the library, package install and clean install; with `OP_NATIVE_HOST_BACKENDS=OPENGL` every engine suite except the 4K one | Existing executor; CPU kernels when the library loads; `c17-host-opengl` opt-in |
| Windows 11 x64, same machine, Vulkan | As above; the native scheduler with suffix collapse, idle arming and fused finals through pixel publication: every engine and workflow suite except the 4K one, the GPU matrix at 16 and 8 samplers, and the deep-stack benchmark (faster than the legacy scheduler in every case) | `c17-host-vulkan` when the library loads (pixel publication, no revision transactions) |
| Linux / OpenGL | C CI configuration only; unrun locally | Existing executor |
| Other macOS hardware/builds | Not qualified by these runs | Compatible libraries can load; broader validation remains open |

The 0.14.1 macOS arm64 extension bundles its ABI 11 library, CPython limited-ABI module and SHA-256 manifest.
It needs no compiler or environment variable on the artist's machine. A missing
or incompatible library preserves the existing path; an explicit developer
library path never searches elsewhere for a replacement. `OP_NATIVE_ENGINE=0`
selects the legacy executor for diagnosis.

This matrix qualifies the tested integration, not a complete independent C GPU
backend or a performance guarantee. The C library does not own the Metal device.
C owns shader compilation, brush/geometry kernels, dependency and resource policies.
The retained host ownership and qualification boundaries are listed in
[migration status](migration-status.md).
No package was installed into the artist's ordinary Blender profile or published.
