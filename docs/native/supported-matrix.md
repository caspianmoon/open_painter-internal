# Native integration matrix

| Configuration | Executed evidence | Selection |
| --- | --- | --- |
| macOS arm64, Apple M3 Pro, stock Blender 5.2.1 LTS / `9e2066aef7ef`, Metal | C/UBSan, all-channel and procedural parity, restore/cache/brush regressions, real viewport events | `c17-host-metal` when the bundled C library loads |
| Windows / Vulkan | Deferred to the other machine as requested | Existing executor |
| Linux / OpenGL | C CI configuration only; unrun locally | Existing executor |
| Other macOS hardware/builds | Not qualified by these runs | Compatible libraries can load; broader validation remains open |

The candidate macOS arm64 extension bundles its ABI 8 library, CPython limited-ABI module and SHA-256 manifest.
It needs no compiler or environment variable on the artist's machine. A missing
or incompatible library preserves the existing path; an explicit developer
library path never searches elsewhere for a replacement. `OP_NATIVE_ENGINE=0`
selects the legacy executor for diagnosis.

This matrix qualifies the tested integration, not a complete independent C GPU
backend or a performance guarantee. The C library does not own the Metal device.
C owns shader compilation, brush/geometry kernels, dependency and resource policies.
The remaining host orchestration and qualification are listed in
[migration status](migration-status.md).
No package was installed into the artist's ordinary Blender profile or published.
