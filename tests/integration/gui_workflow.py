"""Actual viewport stroke and workspace validation in a separate Blender window."""

import sys, traceback
from pathlib import Path
import bpy
import numpy as np

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "src"))
import open_painter
from open_painter.core import (
    texture_set_manager as sets,
    layer_stack as stack,
    image_manager as images,
)
from open_painter.painting import paint_session
from open_painter.evaluation.evaluator import LayerEvaluator

open_painter.register()


def run():
    try:
        ts = sets.create(bpy.context.scene, "Viewport Test")
        ts.resolution = "512"
        sets.assign(bpy.context.scene, ts, bpy.context.object)
        stack.add(ts, "PAINT")
        bpy.ops.open_painter.open_editor(view_mode="SINGLE")
        area = next(a for a in bpy.context.screen.areas if a.type == "VIEW_3D")
        region = next(r for r in area.regions if r.type == "WINDOW")
        with bpy.context.temp_override(area=area, region=region):
            bpy.ops.view3d.view_axis(type="FRONT")
            bpy.ops.view3d.view_selected(use_all_regions=False)
            image = paint_session.begin(bpy.context, ts, "ROUGHNESS")
        bpy.app.timers.register(stroke, first_interval=3)
    except Exception:
        fail()
    return None


def stroke():
    try:
        area = next(a for a in bpy.context.screen.areas if a.type == "VIEW_3D")
        region = next(r for r in area.regions if r.type == "WINDOW")
        with bpy.context.temp_override(area=area, region=region):
            paint = bpy.context.scene.tool_settings.image_paint
            brush = paint.brush
            unified = paint.unified_paint_settings
            for owner in (brush, unified):
                owner.color = (0.85, 0.85, 0.85)
                owner.strength = 1
                owner.size = 50
            points = []
            for index in range(5):
                points.append(
                    dict(
                        name="stroke",
                        mouse=(region.width * 0.5 + index * 10, region.height * 0.5),
                        mouse_event=(region.width * 0.5 + index * 10, region.height * 0.5),
                        is_start=index == 0,
                        location=(0, 0, 0),
                        pressure=1,
                        size=50,
                        time=index * 0.1,
                        x_tilt=0,
                        y_tilt=0,
                    )
                )
            print("STROKE_RESULT", bpy.ops.paint.image_paint(stroke=points))
            values = images.pixels(paint.canvas)
            print("STROKE_RANGE", values.min(), values.max(), np.count_nonzero(values[..., 3]))
            assert np.count_nonzero(values[..., 3]) > 0, (
                "Viewport stroke did not reach the layer image"
            )
            paint_session.detect(bpy.context.scene, force=True)
            ts = bpy.context.scene.open_painter.texture_sets[0]
            LayerEvaluator(bpy.context.scene).evaluate_texture_set(ts.id)
            out = next(o.image for o in ts.outputs if o.channel == "ROUGHNESS")
            assert images.pixels(out)[..., :3].max() > 0.6
        assert bpy.context.workspace.name == "Layout", bpy.context.workspace.name
        bpy.ops.wm.save_as_mainfile(filepath=str(root / ".blender-test" / "viewport_painted.blend"))
        (root / ".blender-test" / "gui_result.txt").write_text(
            "PASS: actual native viewport stroke, roughness composition, workspace"
        )
        bpy.app.timers.register(finish, first_interval=2)
    except Exception:
        fail()
    return None


def finish():
    try:
        a = next(a for a in bpy.context.screen.areas if a.type == "VIEW_3D")
        assert not a.spaces.active.show_region_ui
        assert not a.spaces.active.show_region_toolbar
        assert a.spaces.active.show_region_header
        bpy.ops.screen.screenshot(filepath=str(root / ".blender-test/workspace.png"))
        (root / ".blender-test/gui_result.txt").write_text(
            "PASS: actual native viewport stroke, roughness composition, same-area editor, native panels hidden"
        )
    except Exception:
        fail()
    bpy.ops.wm.quit_blender()
    return None


def fail():
    traceback.print_exc()
    (root / ".blender-test" / "gui_result.txt").write_text(traceback.format_exc())
    bpy.ops.wm.quit_blender()


bpy.app.timers.register(run, first_interval=2)
