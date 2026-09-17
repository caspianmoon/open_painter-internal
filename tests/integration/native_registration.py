"""Native registration/unregistration leaves Blender's layout and panels intact."""

import logging
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import open_painter
from open_painter.evaluation import cache, dependencies
from open_painter.ui import editor, native
from open_painter.utils.logging import logger

# A saved Developer Mode is restored from userpref.blend without firing its
# update callback: registration itself must hand the level to the logger.
addon = bpy.context.preferences.addons.new()
addon.module = open_painter.__name__
initial = [(a.type, a.width, a.height) for a in bpy.context.screen.areas]
for cycle in range(3):
    logger.setLevel(logging.INFO)   # as a fresh import of utils.logging would
    open_painter.register()
    assert logger.level == (logging.DEBUG if cycle else logging.INFO), (cycle, logger.level)
    if not cycle:
        addon.preferences.developer_mode = True
    assert bpy.app.handlers.load_post.count(editor.load) == 1
    assert bpy.app.handlers.load_post.count(cache.on_load) == 1
    assert bpy.app.handlers.depsgraph_update_post.count(dependencies.on_depsgraph) == 1
    assert bpy.app.handlers.frame_change_post.count(dependencies.on_frame) == 1
    # Shelf handles are unmarked before a write and re-marked after it, whether
    # the write succeeded or not (a failed save fires save_post_fail only).
    assert bpy.app.handlers.save_pre.count(native.unmark_handles) == 1
    assert bpy.app.handlers.save_post.count(native.remark_handles) == 1
    assert bpy.app.handlers.save_post_fail.count(native.remark_handles) == 1
    assert initial == [(a.type, a.width, a.height) for a in bpy.context.screen.areas]
    assert "bl_rna" in native.VIEW3D_AST_open_painter.__dict__
    assert "bl_rna" in native.IMAGE_AST_open_painter.__dict__
    assert native.VIEW3D_AST_open_painter.bl_rna != native.IMAGE_AST_open_painter.bl_rna
    open_painter.unregister()
    assert not hasattr(bpy.types.Scene, "open_painter")
    assert editor.load not in bpy.app.handlers.load_post
    assert cache.on_load not in bpy.app.handlers.load_post
    assert dependencies.on_depsgraph not in bpy.app.handlers.depsgraph_update_post
    assert dependencies.on_frame not in bpy.app.handlers.frame_change_post
    assert native.unmark_handles not in bpy.app.handlers.save_pre
    assert native.remark_handles not in bpy.app.handlers.save_post
    assert native.remark_handles not in bpy.app.handlers.save_post_fail
    assert not dependencies._known and not cache._states
    assert not bpy.app.timers.is_registered(editor.restore_initial)
    assert "bl_rna" not in native.VIEW3D_AST_open_painter.__dict__
print("NATIVE_REGISTRATION_PASS")
