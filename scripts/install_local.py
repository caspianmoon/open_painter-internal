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
version = tomllib.loads((root / "blender_manifest.toml").read_text(encoding="utf-8"))["version"]
repo = next(
    (r for r in bpy.context.preferences.extensions.repos if r.module == "user_default"), None
)
if repo is None:
    repo = bpy.context.preferences.extensions.repos.new(name="User Default", module="user_default")
# Preserve the installed extension and preferences before the requested update.
import shutil
from datetime import datetime
backup = root / "backups" / ("blender-profile-before-update-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
backup.mkdir(parents=True, exist_ok=True)
existing = Path(repo.directory) / "open_painter"
if existing.exists():
    shutil.copytree(existing, backup / "installed_open_painter")
preferences = Path(bpy.utils.user_resource("CONFIG")) / "userpref.blend"
if preferences.exists():
    shutil.copy2(preferences, backup / "userpref.blend")
print("RESTORE BACKUP:", backup)
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
# Verify the Blender editor adapter is registered.
editor = importlib.import_module(module + ".ui.editor")
assert "bl_rna" in editor.OP_EditorType.__dict__
bpy.ops.wm.save_userpref()
print("OPEN PAINTER INSTALLED AND ENABLED:", module, installed_version)
print(
    "Use the Editor Type selector > Open Painter, or F3 > Open Painter. Existing .blend files were not saved or modified."
)
