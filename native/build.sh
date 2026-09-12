#!/bin/sh
set -eu
NATIVE_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export PATH="$NATIVE_ROOT/tools/git-lfs-3.8.0:$PATH"
cp "$NATIVE_ROOT/branding/"*.icns "$NATIVE_ROOT/vendor/blender/release/darwin/Blender.app/Contents/Resources/"
cp "$NATIVE_ROOT/shell/painter_shell.mm" "$NATIVE_ROOT/vendor/blender/source/creator/painter_shell.mm"
cp "$NATIVE_ROOT/runtime/open_painter_native.py" "$NATIVE_ROOT/vendor/blender/scripts/startup/open_painter_native.py"
mkdir -p "$NATIVE_ROOT/vendor/blender/scripts/startup/open_painter_previews"
cp "$NATIVE_ROOT/runtime/previews/"*.png "$NATIVE_ROOT/vendor/blender/scripts/startup/open_painter_previews/"
cmake -S "$NATIVE_ROOT/vendor/blender" -B "$NATIVE_ROOT/build" -G 'Unix Makefiles' -DCMAKE_INSTALL_PREFIX="$NATIVE_ROOT/build/bin" -DCMAKE_BUILD_TYPE=Release -DWITH_COMPILER_CCACHE=OFF
cmake --build "$NATIVE_ROOT/build" --parallel 8
cmake --install "$NATIVE_ROOT/build"

# Remove superseded upstream icon assets from incremental build bundles.
python3 "$NATIVE_ROOT/branding/verify_bundle.py" "$NATIVE_ROOT/build/bin/OpenPainter.app" --clean-legacy
