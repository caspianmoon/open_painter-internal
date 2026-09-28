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
import runpy
import sys
import tempfile
import zipfile
from pathlib import Path


# An override also supports running a copied text block inside Blender.
# NOTE: when run as a Blender Text block, __file__ is virtual (e.g. "\Text"),
# so Path(__file__).parent resolves to a drive root like C:\. Detect that case
# and fall back to the linked text filepath or OPEN_PAINTER_SOURCE.
def _resolve_source_dir():
    env = os.environ.get('OPEN_PAINTER_SOURCE')
    if env:
        return Path(env)
    try:
        candidate_file = Path(__file__).resolve()
        candidate_dir = candidate_file.parent
        # Only trust __file__ if it looks like a real checkout.
        if (candidate_dir / "blender_manifest.toml").exists():
            return candidate_dir
    except Exception:
        pass
    try:
        for text_block in getattr(bpy.data, "texts", []):
            filepath = getattr(text_block, "filepath", "") or ""
            if Path(filepath).name == "reinstall_addon_from_source.py":
                resolved = Path(filepath).resolve().parent
                if (resolved / "blender_manifest.toml").exists():
                    return resolved
                return resolved
    except Exception:
        pass
    try:
        space_text = getattr(getattr(bpy.context, "space_data", None), "text", None)
        filepath = getattr(space_text, "filepath", "") or ""
        if filepath:
            return Path(filepath).resolve().parent
    except Exception:
        pass
    # Last-resort guesses: this checkout and the process cwd. This unblocks
    # running as an unsaved/copied Text block (filepath == "").
    try:
        for guess in (Path(r"C:\Users\USER\Desktop\OpenPainter"), Path.cwd()):
            if (guess / "blender_manifest.toml").exists() and (guess / "src" / "open_painter" / "__init__.py").exists():
                return guess
    except Exception:
        pass
    # Last resort: historical behaviour (fails loudly in main() if wrong).
    return Path(__file__).resolve().parent


SOURCE_DIR = _resolve_source_dir()
PACKAGE_DIR = SOURCE_DIR / "src" / "open_painter"
MANIFEST_PATH = SOURCE_DIR / "blender_manifest.toml"
PKG_ID = "open_painter"
REPO_MODULE = "user_default"
MODULE_NAME = f"bl_ext.{REPO_MODULE}.{PKG_ID}"
ZIP_PATH = Path(tempfile.mkdtemp(prefix="open_painter_reinstall_")) / f"{PKG_ID}.zip"


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
    def failed():
        raise RuntimeError('Could not disable the existing extension') from sys.exc_info()[1]
    addon_utils.disable(module_name, default_set=False, handle_error=failed)
    if addon_utils.check(module_name)[1]:
        raise RuntimeError('The existing extension is still enabled; stop before replacing it')


def backup_installed_addon():
    """Keep the installed files before the installer replaces them."""
    for index, repo_dir in enumerate(extension_repo_dirs()):
        target = repo_dir / PKG_ID
        if target.exists():
            if target.resolve() == PACKAGE_DIR.resolve():
                raise RuntimeError('The extension repository points at the source checkout')
            backup = ZIP_PATH.parent / f'previous_install_{index}'
            shutil.copytree(target, backup)
            log(f'Previous installation backup: {backup}')


def build_zip():
    """Use the release packager, including its license and documentation files."""
    arguments = sys.argv
    try:
        sys.argv = [str(SOURCE_DIR / 'scripts/build_extension.py'), '--output', str(ZIP_PATH)]
        runpy.run_path(sys.argv[0], run_name='__main__')
    finally:
        sys.argv = arguments
    with zipfile.ZipFile(ZIP_PATH) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('The candidate archive is corrupt')
        for name in ('blender_manifest.toml', '__init__.py', 'LICENSE'):
            archive.getinfo(name)
    return ZIP_PATH


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
    if "--factory-startup" in sys.argv:
        raise RuntimeError("Refusing to save factory preferences over the existing user preferences.")
    if not SOURCE_DIR.exists():
        raise FileNotFoundError(f"Source folder not found: {SOURCE_DIR}")
    if not (PACKAGE_DIR / "__init__.py").exists():
        raise FileNotFoundError(
            f"Not an extension folder, missing __init__.py: {PACKAGE_DIR} "
            f"(resolved SOURCE_DIR={SOURCE_DIR} from __file__={__file__!r}; "
            f"open the file from disk in Blender's Text Editor or set "
            f"OPEN_PAINTER_SOURCE to your checkout, e.g. "
            f"C:\\Users\\USER\\Desktop\\OpenPainter)"
        )
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(f"Extension manifest not found: {MANIFEST_PATH}")

    log(f"Source: {SOURCE_DIR}")
    log(f"Module: {MODULE_NAME}")

    # Build and validate before touching the installed extension. The installer
    # replaces the package; never pre-delete a working installation.
    zip_path = build_zip()
    backup_installed_addon()
    disable_addon(MODULE_NAME)
    try:
        install_and_enable(zip_path, MODULE_NAME)
    except Exception:
        log(f'Reinstall failed; the previous files are backed up in {ZIP_PATH.parent}')
        raise

    log("Done. Fresh extension is installed and enabled.")


if __name__ == '__main__':
    main()
