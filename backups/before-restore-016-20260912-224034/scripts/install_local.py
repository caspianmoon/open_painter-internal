"""Install the validated ZIP while preserving the user's existing Blender preferences.

Run with Blender --background --python scripts/install_local.py, without factory startup.
"""

import sys
import importlib
from pathlib import Path
import tomllib
import bpy
import addon_utils

if "--factory-startup" in sys.argv:
    raise RuntimeError("Refusing to save factory preferences over the existing user preferences.")
root = Path(__file__).resolve().parents[1]
version = tomllib.loads((root / "blender_manifest.toml").read_text())["version"]
repo = next(
    (r for r in bpy.context.preferences.extensions.repos if r.module == "user_default"), None
)
if repo is None:
    repo = bpy.context.preferences.extensions.repos.new(name="User Default", module="user_default")
result = bpy.ops.extensions.package_install_files(
    filepath=str(root / "dist" / f"open_painter-{version}.zip"),
    repo=repo.module,
    enable_on_install=True,
)
if result != {"FINISHED"}:
    raise RuntimeError(f"Extension installation failed: {result}")
module = f"bl_ext.{repo.module}.open_painter"
if not addon_utils.check(module)[1]:
    raise RuntimeError("Open Painter was not enabled after installation.")
installed_version = importlib.import_module(module + ".constants").VERSION
if installed_version != version:
    raise RuntimeError(f"Loaded version {installed_version}, expected {version}. Restart Blender to finish updating.")
# Window launcher replaces the retired editor subtype.
assert not hasattr(bpy.types, "OP_EditorType")
bpy.ops.wm.save_userpref()
print("OPEN PAINTER INSTALLED AND ENABLED:", module, installed_version)
print(
    "Use Window > Open Painter Window or F3 > Open Painter Window. Existing .blend files were not saved or modified."
)
