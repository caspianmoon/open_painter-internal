"""Native registration/unregistration leaves Blender's layout and panels intact."""

import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import open_painter
from open_painter.evaluation import cache
from open_painter.ui import editor, native

initial = [(a.type, a.width, a.height) for a in bpy.context.screen.areas]
for _ in range(3):
    open_painter.register()
    assert bpy.app.handlers.load_post.count(editor.load) == 1
    assert bpy.app.handlers.load_post.count(cache.on_load) == 1
    assert initial == [(a.type, a.width, a.height) for a in bpy.context.screen.areas]
    assert "bl_rna" in native.VIEW3D_AST_open_painter.__dict__
    assert "bl_rna" in native.IMAGE_AST_open_painter.__dict__
    assert native.VIEW3D_AST_open_painter.bl_rna != native.IMAGE_AST_open_painter.bl_rna
    open_painter.unregister()
    assert not hasattr(bpy.types.Scene, "open_painter")
    assert editor.load not in bpy.app.handlers.load_post
    assert cache.on_load not in bpy.app.handlers.load_post
    assert not bpy.app.timers.is_registered(editor.restore_initial)
    assert "bl_rna" not in native.VIEW3D_AST_open_painter.__dict__
print("NATIVE_REGISTRATION_PASS")
