# Open Painter native application

This replaces the external Python/Qt rendering bridge architecture. The application is built from Blender source, with EEVEE, GPU/Metal, dependency graph, mesh evaluation, material nodes, Python API and `.blend` I/O in the same executable. No installed Blender application or socket/frame-streaming backend is part of this target.

## Current milestone

The macOS executable now opens an independent AppKit window and runs its own event loop. EEVEE renders into a persistent GPU offscreen texture, presented directly through a CAMetalLayer on the same Metal command queue. No Blender editor UI, external Blender process, socket, or CPU frame readback is used by this shell. `shell/painter_shell.mm` is the shell source; `build.sh` copies it into the upstream build.

Verified: native window launch and an actual EEVEE-rendered cube, captured in `logs/shell-window.png`. Drag-to-orbit and scroll-to-zoom handlers are implemented; interactive latency has not yet been measured. This is an architectural prototype, not the completed painting application. The earlier Blender editor layout is superseded and is not opened by this shell.

The retained in-process layer model passed fill/folder/mask/UV and material persistence tests. These controls are not yet exposed in the new shell. A material and its explicit layer description survived native → standard Blender → native save/reopen. This does not establish full Blender compatibility or Substance Painter capability parity.

Upstream: https://projects.blender.org/blender/blender.git
Branch: blender-v5.2-release
Pinned commit: b787619620d14538fc4b4aa282d074734a0f3029
Apple Silicon libraries: a76ef917b4849ba2b1b1deb1a643e131a884a63b

Changes are recorded in `patches/0001-native-application.patch`; downloaded source, dependencies and build products are ignored. Preserve upstream licenses and copyright notices. Source distribution must include the pinned source and our modifications, not just an executable.

## Build (macOS Apple Silicon)

Requirements: Xcode command-line developer tools, CMake, Git and Git LFS. This checkout uses a project-local Git LFS executable under `tools/git-lfs-3.8.0/`.

Run `./native/bootstrap.sh` to prepare the pinned checkout with local LFS configuration. For manual setup, clone the upstream source into `vendor/blender`, check out the pinned commit, and apply the patch using `git apply`. In that directory run `python3 build_files/utils/make_update.py --no-blender` with Git LFS on PATH. The patch keeps LFS configuration local to the checkout.

Run `./native/build.sh` from the project root. Output is intended at `native/build/bin/OpenPainter.app`; do not treat the output as ready until compilation, installation and smoke tests pass.

## Run and verification

Launch `native/build/bin/OpenPainter.app/Contents/MacOS/OpenPainter --factory-startup` for the independent window. The internal background initialization flag suppresses Blender window creation; the application then runs its own AppKit loop in the same process. Explicit `--background` remains available for automated scene tests.

Run `native/build/bin/OpenPainter.app/Contents/MacOS/OpenPainter --background --factory-startup --python native/tests/smoke.py` for scene persistence checks, and substitute `native/tests/painter_layers.py` for layer-model checks.

Next work: expose the layer model through custom controls matching the supplied reference, implement 2D display and painting, and measure input-to-display latency. The current shell is macOS/Metal only. Preserve upstream licenses and ship corresponding source with distributions.
