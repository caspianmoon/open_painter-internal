"""Install the built ZIP using Blender's real extension installer in a temporary profile."""

from pathlib import Path
import tomllib
import os
import bpy, addon_utils, importlib

root = Path(__file__).resolve().parents[2]
version = tomllib.loads((root / "blender_manifest.toml").read_text())["version"]
repo = bpy.context.preferences.extensions.repos.new(
    name="Open Painter Test",
    module="open_painter_test",
    custom_directory=str(root / ".blender-test/extension-repository"),
)
print("INSTALL_REPO", repo.module, repo.directory)
result = bpy.ops.extensions.package_install_files(
    filepath=os.environ.get("OPEN_PAINTER_TEST_ZIP", str(root / "dist" / f"open_painter-{version}.zip")),
    repo=repo.module,
    enable_on_install=True,
)
assert result == {"FINISHED"}, result
module = "bl_ext.open_painter_test.open_painter"
assert addon_utils.check(module)[1]
assert importlib.import_module(module + ".constants").VERSION == version
for _ in range(3):
    addon_utils.disable(module, default_set=True)
    assert not hasattr(bpy.types.Scene, "open_painter")
    addon_utils.enable(module, default_set=True)
    assert hasattr(bpy.types.Scene, "open_painter")
assert bpy.ops.open_painter.texture_set(action="ADD") == {"FINISHED"}
assert bpy.ops.open_painter.texture_set(action="ASSIGN") == {"FINISHED"}
assert bpy.ops.open_painter.layer(action="FILL") == {"FINISHED"}
ts = bpy.context.scene.open_painter.texture_sets[0]
ts.resolution = "512"
assert bpy.ops.open_painter.texture_set(action="REFRESH") == {"FINISHED"}
addon_utils.disable(module, default_set=True)
print(
    "INSTALL PASS: real ZIP installation, extension namespace, enable/disable, material evaluation"
)
