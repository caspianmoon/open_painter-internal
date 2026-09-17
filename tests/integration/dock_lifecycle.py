"""The Painter timer survives a broken paint canvas and follows the painting window."""

import sys
from pathlib import Path
from types import SimpleNamespace

import bpy

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "src"))
import open_painter
from open_painter.core import layer_stack as stack, texture_set_manager as sets
from open_painter.painting import paint_session
from open_painter.ui.dock import lifecycle

open_painter.register()


def fake_window(scene, active):
    return SimpleNamespace(scene=scene, view_layer=SimpleNamespace(objects=SimpleNamespace(active=active)))


def run():
    scene = bpy.context.scene
    project = scene.open_painter
    ts = sets.create(scene, "Dock lifecycle", 512)
    sets.assign(scene, ts, bpy.context.object)
    stack.add(ts, "PAINT", "Canvas")
    project.paint_channel = "BASE_COLOR"
    canvas = paint_session.begin(bpy.context, ts, "BASE_COLOR")
    cube = project.paint_object
    assert canvas and cube and cube.mode == "TEXTURE_PAINT"
    assert lifecycle.tick() == 0.15
    assert project.paint_image == canvas, "A live session must survive the tick"

    # Object mode is per object: a window on another view layer of the same
    # scene sees no paint object and must not be the one to judge the session.
    windows = list(bpy.context.window_manager.windows)
    assert lifecycle._paint_window(windows, scene) == windows[0]
    other = fake_window(scene, None)
    painting = fake_window(scene, cube)
    assert lifecycle._paint_window([other, painting], scene) is painting
    assert lifecycle._paint_window([painting, other], scene) is painting
    # Without any window holding the paint object, selection is still followed.
    assert lifecycle._paint_window([other], scene) is other
    elsewhere = fake_window(bpy.data.scenes.new("Elsewhere"), cube)
    assert lifecycle._paint_window([elsewhere], scene) is None
    assert lifecycle._paint_window([elsewhere, other], scene) is other

    # A persisted session whose canvas lost its pixel data (an unpacked file
    # that went missing) fails inside synchronize and again inside the finish
    # that follows; the timer must tear the session down instead of dying.
    missing = bpy.data.images.new("Missing canvas", 8, 8)
    missing.filepath_raw = str(root / ".blender-test/nowhere.png")
    missing.source = "FILE"
    assert not missing.has_data
    project.paint_image = missing
    bpy.ops.object.mode_set(mode="OBJECT")
    assert lifecycle.tick() == 0.15
    assert project.paint_image is None, project.paint_image
    assert project.paint_object is None and not project.paint_set_id
    assert "pixel data" in project.status, project.status
    assert lifecycle.tick() == 0.15
    # A fresh session starts cleanly after the teardown.
    project.status = ""
    canvas = paint_session.begin(bpy.context, ts, "BASE_COLOR")
    assert project.paint_image == canvas and cube.mode == "TEXTURE_PAINT"
    assert lifecycle.tick() == 0.15 and project.paint_image == canvas
    paint_session.finish(bpy.context)
    assert project.paint_image is None
    open_painter.unregister()
    print("DOCK_LIFECYCLE_PASS: tick survives a pixel-less canvas and tears the session down; the painting window judges the session", flush=True)


area = next(area for area in bpy.context.screen.areas if area.type == "VIEW_3D")
region = next(region for region in area.regions if region.type == "WINDOW")
with bpy.context.temp_override(area=area, region=region):
    run()
