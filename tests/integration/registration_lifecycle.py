"""Transactional extension registration without replacing the painter layout."""

import sys
from pathlib import Path
import bpy

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "src"))
import open_painter
from open_painter import registration
from open_painter.ui import editor
from open_painter.evaluation import cache

initial = [(a.type, a.width, a.height) for a in bpy.context.screen.areas]
for _ in range(3):
    initial = [(a.type, a.width, a.height) for a in bpy.context.screen.areas]
    open_painter.register()
    open_painter.register()
    assert registration._registered
    assert bpy.app.handlers.load_post.count(editor.load) == 1
    assert bpy.app.handlers.load_post.count(cache.on_load) == 1
    assert initial == [(a.type, a.width, a.height) for a in bpy.context.screen.areas]
    assert not hasattr(bpy.types, "OP_EditorType")
    open_painter.unregister()
    open_painter.unregister()
    assert not hasattr(bpy.types.Scene, "open_painter")
    assert editor.load not in bpy.app.handlers.load_post
    assert cache.on_load not in bpy.app.handlers.load_post
    assert not bpy.app.timers.is_registered(editor.restore_initial)

original = editor.register


def fail():
    original()
    raise RuntimeError("simulated registration failure")


editor.register = fail
try:
    open_painter.register()
except RuntimeError as exc:
    assert "simulated" in str(exc)
else:
    raise AssertionError("Expected registration failure")
finally:
    editor.register = original
assert not registration._registered and not registration._registered_classes
assert not hasattr(bpy.types.Scene, "open_painter")
assert editor.load not in bpy.app.handlers.load_post
assert cache.on_load not in bpy.app.handlers.load_post
open_painter.register()
assert registration._registered
open_painter.unregister()
print(
    "PASS: repeated registration, rollback after partial failure, recovery, no Editor Type registration, no layout changes during registration"
)
