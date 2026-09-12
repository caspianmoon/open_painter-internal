"""Built-in and user-local library discovery."""

from pathlib import Path

import bpy


def directory(context):
    addon = context.preferences.addons.get(__package__.rsplit(".", 1)[0])
    configured = addon.preferences.library_directory if addon else ""
    if configured:
        return Path(bpy.path.abspath(configured))
    return Path(bpy.utils.user_resource("DATAFILES", path="open_painter/materials", create=True))


def entries(context):
    builtin = Path(__file__).resolve().parents[1] / "assets" / "smart_materials"
    return sorted(builtin.glob("*.openpaintmaterial")) + sorted(
        directory(context).glob("*.openpaintmaterial")
    )
