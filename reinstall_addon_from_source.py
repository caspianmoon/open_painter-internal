# Run this inside Blender's Text Editor to reinstall Open Painter from this source folder.
#
# The repository is a Blender extension: the manifest sits at the repository root
# and the package under src/open_painter. The zip is built from those two places
# and installed into the User Default extension repository, replacing the copy
# Blender currently has enabled.

import addon_utils
import bpy
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path


SOURCE_DIR = Path(r"C:\Users\USER\Desktop\OpenPainter")
PACKAGE_DIR = SOURCE_DIR / "src" / "open_painter"
MANIFEST_PATH = SOURCE_DIR / "blender_manifest.toml"
PKG_ID = "open_painter"
REPO_MODULE = "user_default"
MODULE_NAME = f"bl_ext.{REPO_MODULE}.{PKG_ID}"
ZIP_PATH = Path(tempfile.gettempdir()) / f"{PKG_ID}_dev_reinstall.zip"

EXCLUDE_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
}
EXCLUDE_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".zip",
}


def log(message):
    print(f"[Open Painter Reinstall] {message}")


def extension_repo_dirs():
    paths = []
    for repo in bpy.context.preferences.extensions.repos:
        if repo.module == REPO_MODULE and repo.directory:
            paths.append(Path(repo.directory))

    user_repo = bpy.utils.user_resource("EXTENSIONS", path=REPO_MODULE, create=True)
    if user_repo:
        paths.append(Path(user_repo))

    unique = []
    for path in paths:
        resolved = path.resolve()
        if resolved not in unique:
            unique.append(resolved)
    return unique


def clean_modules(module_name):
    for name in list(sys.modules):
        if name == module_name or name.startswith(module_name + "."):
            del sys.modules[name]


def disable_addon(module_name):
    try:
        addon_utils.disable(module_name, default_set=True, handle_error=None)
        log(f"Disabled addon module: {module_name}")
    except Exception as exc:
        log(f"Disable skipped/failed: {exc}")


def remove_installed_addon(pkg_id, source_dir):
    source_resolved = source_dir.resolve()

    for repo_dir in extension_repo_dirs():
        target = repo_dir / pkg_id
        if not target.exists():
            continue

        try:
            if target.resolve() == source_resolved:
                log(f"Skipping source folder removal: {target}")
                continue
        except Exception:
            pass

        shutil.rmtree(target)
        log(f"Deleted installed extension folder: {target}")


def should_zip(path):
    if path.name in EXCLUDE_DIRS:
        return False
    if path.suffix.lower() in EXCLUDE_SUFFIXES:
        return False
    return True


def build_zip(package_dir, manifest_path, zip_path):
    if zip_path.exists():
        zip_path.unlink()

    # Extension archives carry the manifest and the package's files at the
    # archive root; the installer maps them into a folder named after the
    # manifest ID. build_extension.py packages the release the same way.
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(manifest_path, manifest_path.name)
        for root, dirs, files in os.walk(package_dir):
            root_path = Path(root)
            dirs[:] = [dirname for dirname in dirs if should_zip(root_path / dirname)]

            for filename in files:
                file_path = root_path / filename
                if not should_zip(file_path):
                    continue

                archive.write(file_path, file_path.relative_to(package_dir).as_posix())

    log(f"Built zip: {zip_path}")
    return zip_path


def install_and_enable(zip_path, module_name):
    result = bpy.ops.extensions.package_install_files(
        filepath=str(zip_path),
        repo=REPO_MODULE,
        enable_on_install=False,
    )
    if result != {"FINISHED"}:
        raise RuntimeError(f"Extension install returned {result}")
    log("Installed zip through Blender preferences.")

    # The install replaces the folder, but any module imported this session is
    # still the old code in sys.modules; drop it before enabling the fresh copy.
    clean_modules(module_name)
    result = bpy.ops.preferences.addon_enable(module=module_name)
    if result != {"FINISHED"}:
        raise RuntimeError(f"Extension enable returned {result}")
    log(f"Enabled addon module: {module_name}")

    try:
        bpy.ops.wm.save_userpref()
        log("Saved Blender user preferences.")
    except Exception as exc:
        log(f"Could not save user preferences: {exc}")


def main():
    if not SOURCE_DIR.exists():
        raise FileNotFoundError(f"Source folder not found: {SOURCE_DIR}")
    if not (PACKAGE_DIR / "__init__.py").exists():
        raise FileNotFoundError(f"Not an extension folder, missing __init__.py: {PACKAGE_DIR}")
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(f"Extension manifest not found: {MANIFEST_PATH}")

    log(f"Source: {SOURCE_DIR}")
    log(f"Module: {MODULE_NAME}")

    disable_addon(MODULE_NAME)
    clean_modules(MODULE_NAME)
    remove_installed_addon(PKG_ID, PACKAGE_DIR)
    clean_modules(MODULE_NAME)

    zip_path = build_zip(PACKAGE_DIR, MANIFEST_PATH, ZIP_PATH)
    install_and_enable(zip_path, MODULE_NAME)

    log("Done. Fresh extension is installed and enabled.")


main()
