#!/bin/sh
set -eu
NATIVE_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export PATH="$NATIVE_ROOT/tools/git-lfs-3.8.0:$PATH"
SOURCE_ROOT="$NATIVE_ROOT/vendor/blender"
if [ ! -d "$SOURCE_ROOT/.git" ]; then
  git clone --depth 1 --branch blender-v5.2-release https://projects.blender.org/blender/blender.git "$SOURCE_ROOT"
fi
cd "$SOURCE_ROOT"
EXPECTED=b787619620d14538fc4b4aa282d074734a0f3029
if [ "$(git rev-parse HEAD)" != "$EXPECTED" ]; then
  echo "Expected upstream commit $EXPECTED; refusing to change an existing checkout." >&2
  exit 1
fi
if git apply --check "$NATIVE_ROOT/patches/0001-native-application.patch" 2>/dev/null; then
  git apply "$NATIVE_ROOT/patches/0001-native-application.patch"
else
  git apply --reverse --check "$NATIVE_ROOT/patches/0001-native-application.patch"
fi
git lfs install --local --skip-repo
git lfs pull
python3 build_files/utils/make_update.py --no-blender
# The upstream updater configures LFS in the parent only.
git -C lib/macos_arm64 lfs install --local --skip-repo
git -C lib/macos_arm64 lfs checkout
