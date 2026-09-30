"""The asset shelf's built-in entries live in their own asset library, not in the artist's file.

Blender's asset shelf only lists asset datablocks, and every local image asset also
turns up in the Image Editor's browse menu, the outliner and the artist's saved
files. The catalog's file-independent entries — materials, smart materials and
masks, filters, procedurals, brushes, No Alpha — are therefore written once per
version to ``painter_assets.blend`` in a per-user directory that is registered as
the asset library "Open Painter"; the shelf reads them from there, the artist's
.blend never carries them. Entries that depend on the open file (imported textures
and alphas) keep their local handles, which ``native.unmark_handles`` strips
before every save.

The library is a small file: each entry is a 32×32 generated swatch carrying its
catalog key and category as asset tags, plus the rendered preview the shelf
draws. It is built by a background Blender process at registration (never inside
the artist's session, which would mark their file dirty) or in-process when
Blender itself runs in the background.
"""
import json
import subprocess
import sys
from pathlib import Path

import bpy

from ..constants import VERSION

LIBRARY_NAME = "Open Painter"
LIBRARY_FILE = "painter_assets.blend"
MANIFEST = "painter_assets.json"
CATALOGS_FILE = "blender_assets.cats.txt"
CATALOGS_FORMAT = 1          # bump when the catalog layout changes: the library is rebuilt
KEY_TAG = "op-key:"
CATEGORY_TAG = "op-category:"
BUILTIN_KINDS = {"FILL", "SMART", "SMART_RECIPE", "SMART_MASK", "FILTER", "BRUSH", "PROCEDURAL", "GEOMETRY"}
_process = None
_manifest = None


def package_name():
    return __package__.rsplit(".", 1)[0]


def directory():
    package = package_name()
    if package.startswith("bl_ext."):
        return Path(bpy.utils.extension_path_user(package, path="shelf", create=True))
    return Path(bpy.utils.user_resource("DATAFILES", path="open_painter/shelf", create=True))


def library_path():
    return directory() / LIBRARY_FILE


def is_builtin(entry):
    """Entries the library carries: everything that does not point at data of the open file."""
    return entry[1] in BUILTIN_KINDS or (entry[1] == "ALPHA" and not entry[2])


def builtin_entries():
    from .native import catalog
    return [entry for entry in catalog(local=False) if is_builtin(entry)]   # no bpy.data: registration may run restricted


def manifest():
    """{'version': ..., 'names': {image name: catalog key}} of the library on disk, or None."""
    global _manifest
    path = directory() / MANIFEST
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        _manifest = None
        return None
    if _manifest is None or _manifest[0] != stamp:
        try:
            _manifest = (stamp, json.loads(path.read_text()))
        except (OSError, ValueError):
            _manifest = None
            return None
    return _manifest[1]


def current():
    """Whether the library on disk carries this version's catalog."""
    data = manifest()
    from .library_categories import key
    return bool(data) and data.get("version") == VERSION and data.get("catalogs") == CATALOGS_FORMAT \
        and library_path().is_file() and (directory() / CATALOGS_FILE).is_file() \
        and set(data.get("names", {}).values()) == {key(entry) for entry in builtin_entries()}


def key_of(asset):
    """The catalog key of a library asset (from its tags), or None."""
    metadata = getattr(asset, "metadata", None)
    for tag in getattr(metadata, "tags", ()):
        if tag.name.startswith(KEY_TAG):
            return tag.name[len(KEY_TAG):]
    return None


def category_of(asset):
    metadata = getattr(asset, "metadata", None)
    for tag in getattr(metadata, "tags", ()):
        if tag.name.startswith(CATEGORY_TAG):
            return tag.name[len(CATEGORY_TAG):]
    return None


def is_library_asset(asset):
    if getattr(asset, "local_id", None) is not None:
        return False
    path = getattr(asset, "full_library_path", "") or ""
    return Path(path).name == LIBRARY_FILE


_facts = {}   # (asset pointer, name) -> (in the shelf library, its category)


def library_facts(asset):
    """(``is_library_asset``, ``category_of``) of an asset, remembered until the shelf or the
    library refreshes. The Painter shelf polls every asset on each layout, and each edit of
    a drag lays it out again: building hundreds of library paths and reading their tags
    per frame was a quarter of a frame."""
    pointer = getattr(asset, "as_pointer", None)
    key = (pointer(), asset.name) if pointer is not None else None
    facts = _facts.get(key) if key is not None else None
    if facts is None:
        if len(_facts) > 20000:
            _facts.clear()   # asset lists rebuilt many times over: start again
        library = is_library_asset(asset)
        facts = (library, category_of(asset) if library else None)
        if key is not None:
            _facts[key] = facts
    return facts


def forget_facts():
    _facts.clear()


def key_for_name(name):
    """Catalog key of a library image name (for activations that carry only the identifier)."""
    data = manifest() or {}
    return data.get("names", {}).get(name)


def build(target=None):
    """Write the library for this version; returns the path.

    Runs in whichever Blender calls it: the temporary images exist only until
    ``libraries.write`` has stored them and are removed afterwards.
    """
    from .library_categories import (ASSET_CATEGORY_KEY, CATALOG_IDS, KIND_CATEGORY, catalog_definitions, key,
                                     make_preview)
    from .native import ASSET_KEY, description_of
    from ..smart_materials.catalog import CATALOG
    target = Path(target) if target else directory()
    target.mkdir(parents=True, exist_ok=True)
    previews = Path(__file__).resolve().parents[1] / "assets/previews"
    created, names = [], {}
    try:
        for entry in builtin_entries():
            entry_key = key(entry)
            category = KIND_CATEGORY[entry[1]]
            image = bpy.data.images.new(entry[0], width=32, height=32)
            image.generated_color = (*entry[3], 1)
            image[ASSET_KEY] = entry_key
            image[ASSET_CATEGORY_KEY] = category
            image.asset_mark()
            image.asset_data.description = description_of(entry, CATALOG)
            image.asset_data.tags.new(KEY_TAG + entry_key, skip_if_exists=True)
            image.asset_data.tags.new(CATEGORY_TAG + category, skip_if_exists=True)
            image.asset_data.catalog_id = CATALOG_IDS[category]   # the shelf's category tab
            rendered = previews / (entry[0].replace(" ", "_") + ".png")
            if rendered.exists() and category in {"MATERIALS", "SMART_MATERIALS", "MASKS"}:
                with bpy.context.temp_override(id=image):
                    bpy.ops.ed.lib_id_load_custom_preview(filepath=str(rendered))
            else:
                rgba = make_preview(entry)
                preview = image.preview_ensure()
                preview.image_size = (rgba.shape[1], rgba.shape[0])
                preview.image_pixels_float = rgba.ravel()
            created.append(image)
            names[image.name] = entry_key
        path = target / LIBRARY_FILE
        bpy.data.libraries.write(str(path), set(created), fake_user=True, compress=True)
        (target / CATALOGS_FILE).write_text(catalog_definitions())
        (target / MANIFEST).write_text(json.dumps({"version": VERSION, "catalogs": CATALOGS_FORMAT, "names": names},
                                                  indent=1))
    finally:
        for image in created:
            try:
                bpy.data.images.remove(image)
            except (ReferenceError, RuntimeError):
                pass
    global _manifest
    _manifest = None
    return path


def register_library():
    """Make the directory an asset library of the user's preferences (idempotent)."""
    libraries = bpy.context.preferences.filepaths.asset_libraries
    target = str(directory())
    entry = next((item for item in libraries if item.name == LIBRARY_NAME), None)
    if entry is None:
        entry = libraries.new(name=LIBRARY_NAME, directory=target)
    elif entry.path != target:
        entry.path = target
    return entry


def ensure(background=None):
    """Register the library and (re)build it when this version's catalog is not on disk.

    A foreground Blender builds in a separate background process so the artist's
    session stays clean and undisturbed; a background Blender (tests, batch
    jobs) builds in place. Returns True when the library is ready now.
    """
    global _process
    forget_facts()
    if not isinstance(bpy.data, bpy.types.BlendData):
        # Registration at startup keeps bpy.data restricted (a headless render, an extension
        # enabled from the command line): build once the session is up, when timers run.
        bpy.app.timers.register(_deferred_ensure, first_interval=0.5)
        return False
    try:
        register_library()
    except Exception as exc:   # preferences can be read-only (factory startup); the shelf still works from LOCAL
        from ..utils.logging import logger
        logger.debug("Shelf library registration skipped: %s", exc)
    if current():
        return True
    if background is None:
        background = bpy.app.background
    if background:
        build()
        return True
    if _process is not None and _process.poll() is None:
        return False   # still building
    package_dir = Path(__file__).resolve().parents[1]
    script = (
        "import sys; sys.path.insert(0, %r); import importlib; "
        "module = importlib.import_module(%r + '.ui.shelf_library'); module.build(%r)"
        % (str(package_dir.parent), package_dir.name, str(directory()))
    )
    _process = subprocess.Popen([bpy.app.binary_path, "--background", "--factory-startup", "--python-expr", script],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    bpy.app.timers.register(_poll_build, first_interval=1.0)
    return False


def _deferred_ensure():
    try:
        ensure()
    except Exception:
        from ..utils.logging import logger
        logger.exception("Shelf library unavailable; the shelf shows local handles only")
    return None


def _poll_build():
    """Once the background build has landed, ask the open shelves to re-read the library."""
    if _process is not None and _process.poll() is None:
        return 1.0
    refresh_shelves()
    return None


def refresh_shelves():
    forget_facts()
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != "VIEW_3D":
                continue
            region = next((r for r in area.regions if r.type == "ASSET_SHELF"), None)
            if region is not None:
                try:
                    with bpy.context.temp_override(window=window, area=area, region=region):
                        bpy.ops.asset.library_refresh()
                except Exception:
                    pass
            area.tag_redraw()
