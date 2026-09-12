"""Capture the actual docked workspace, preserving the demo's native paint view."""

import sys
from pathlib import Path
import bpy

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
import open_painter

open_painter.register()
bpy.ops.wm.open_mainfile(filepath=str(root / "examples/open_painter_demo.blend"))


def setup():
    bpy.ops.open_painter.workspace()
    bpy.app.timers.register(capture, first_interval=5)
    return None


def capture():
    bpy.ops.screen.screenshot(filepath=str(root / "docs/workspace.png"))
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(setup, first_interval=2)
