"""Verify actual native regions without overlay handlers or custom input interception."""

import json
import sys
import traceback
from pathlib import Path

import bpy

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "src"))
import open_painter  # noqa: E402
from open_painter.ui import native  # noqa: E402
from open_painter.ui.dock import lifecycle  # noqa: E402

drawn = set()
asset_headers = set()
original_asset_buttons = native.asset_type_buttons


def asset_buttons(self, context):
    if native.editor.is_area(context.area):
        asset_headers.add(context.area.type)
    original_asset_buttons(self, context)


native.asset_type_buttons = asset_buttons


def traced(original, name):
    def draw(self, context):
        drawn.add(name)
        original(self, context)

    return draw


for cls in [native.OP_PT_native_sets, native.OP_PT_native_layers, native.OP_PT_native_properties]:
    cls.draw = traced(cls.draw, cls.__name__)
open_painter.register()
bpy.context.preferences.view.show_splash = False
result = root / ".blender-test/native-ui.json"
result.unlink(missing_ok=True)
area = None


def setup():
    global area
    try:
        area = max(
            (a for a in bpy.context.screen.areas if a.type == "VIEW_3D"),
            key=lambda a: a.width * a.height,
        )
        region = next(r for r in area.regions if r.type == "WINDOW")
        with bpy.context.temp_override(area=area, region=region):
            bpy.ops.open_painter.open_editor()
            bpy.ops.open_painter.texture_set(action="ADD")
            ts = bpy.context.scene.open_painter.texture_sets[0]
            ts.resolution = "512"
            from open_painter.core.surface_presets import apply

            group = apply(ts, "Painted Steel")
            coat = next(layer for layer in ts.layers if layer.parent_id == group.id and layer.masks)
            bpy.ops.open_painter.native_select(layer_id=coat.id)
            from open_painter.core.layer_stack import channel_for
            channel = channel_for(coat, 'BASE_COLOR')
            channel.opacity_percent = 50
            assert abs(channel.opacity - .5) < 1e-6 and ts.dirty
            channel.opacity_percent = 100
            from open_painter.core import image_manager

            for kind in ("AO", "CURVATURE"):
                m = ts.baked_maps.add()
                m.kind = kind
                m.image = image_manager.create("Test " + kind, 32, (0.5, 0.5, 0.5, 1))
            bpy.context.scene.open_painter.asset_category = "MASKS"
        bpy.app.timers.register(check, first_interval=4)
    except Exception:
        result.write_text(traceback.format_exc())
        traceback.print_exc()
        bpy.ops.wm.quit_blender()


def check():
    try:
        assert "OP_PT_native_layers" in drawn
        assert "VIEW_3D" in asset_headers
        assert not lifecycle._handlers
        assert not any(
            o.bl_idname == "OPEN_PAINTER_OT_dock_events" for o in bpy.context.window.modal_operators
        )
        assert any(r.type == "UI" and r.width > 10 for r in area.regions)
        assert any(r.type == "ASSET_SHELF" and r.height > 10 for r in area.regions)
        result.write_text(json.dumps({"native_regions": "PASS", "custom_handlers": 0}))
        shelf = next(r for r in area.regions if r.type == "ASSET_SHELF")
        bpy.context.window.cursor_warp(shelf.x + 60, shelf.y + 60)
        bpy.context.window.event_simulate(
            type="MOUSEMOVE", value="NOTHING", x=shelf.x + 60, y=shelf.y + 60
        )
        bpy.app.timers.register(click_asset, first_interval=1)
    except Exception:
        result.write_text(traceback.format_exc())
        traceback.print_exc()
        bpy.ops.wm.quit_blender()


def click_asset():
    shelf = next(r for r in area.regions if r.type == "ASSET_SHELF")
    bpy.context.window.event_simulate(
        type="LEFTMOUSE", value="PRESS", x=shelf.x + 60, y=shelf.y + 60
    )
    bpy.app.timers.register(release_asset, first_interval=0.2)


def release_asset():
    shelf = next(r for r in area.regions if r.type == "ASSET_SHELF")
    bpy.context.window.event_simulate(
        type="LEFTMOUSE", value="RELEASE", x=shelf.x + 60, y=shelf.y + 60
    )
    bpy.app.timers.register(applied, first_interval=1)


def applied():
    try:
        from open_painter.core.layer_stack import active

        ts = bpy.context.scene.open_painter.texture_sets[0]
        layer = active(ts)
        assert layer.name == "Paint coat"
        assert len(layer.masks) == 2 and layer.masks[-1].source == "GENERATOR", [
            (m.name, m.source) for m in layer.masks
        ]
        assert layer.masks[-1].operation == "NORMAL"
        data = json.loads(result.read_text())
        data["native_asset_click"] = "PASS"
        result.write_text(json.dumps(data))
        region = next(r for r in area.regions if r.type == "WINDOW")
        for key in ["T", "N"]:
            for value in ["PRESS", "RELEASE"]:
                bpy.context.window.event_simulate(
                    type=key,
                    value=value,
                    x=region.x + region.width // 2,
                    y=region.y + region.height // 2,
                )
        bpy.app.timers.register(hidden, first_interval=1)
    except Exception:
        result.write_text(traceback.format_exc())
        traceback.print_exc()
        bpy.ops.wm.quit_blender()


def hidden():
    try:
        assert not area.spaces.active.show_region_ui
        assert area.spaces.active.show_region_toolbar
        data = json.loads(result.read_text())
        data["native_T_N"] = "PASS"
        result.write_text(json.dumps(data))
        region = next(r for r in area.regions if r.type == "WINDOW")
        with bpy.context.temp_override(area=area, region=region):
            assert bpy.ops.open_painter.native_view(view="IMAGE_EDITOR") == {"FINISHED"}
        bpy.app.timers.register(check_2d, first_interval=1)
    except Exception:
        result.write_text(traceback.format_exc())
        traceback.print_exc()
        bpy.ops.wm.quit_blender()


def check_2d():
    try:
        assert area.type == "IMAGE_EDITOR"
        assert "IMAGE_EDITOR" in asset_headers
        assert any(r.type == "ASSET_SHELF" and r.height > 10 for r in area.regions)
        assert next(r for r in area.regions if r.type == "UI").active_panel_category == "Painter"
        region = next(r for r in area.regions if r.type == "WINDOW")
        with bpy.context.temp_override(area=area, region=region):
            assert bpy.ops.open_painter.native_view(view="VIEW_3D") == {"FINISHED"}
        bpy.app.timers.register(check_3d, first_interval=1)
    except Exception:
        result.write_text(traceback.format_exc())
        traceback.print_exc()
        bpy.ops.wm.quit_blender()


def check_3d():
    try:
        assert area.type == "VIEW_3D"
        assert next(r for r in area.regions if r.type == "UI").active_panel_category == "Painter"
        ts = bpy.context.scene.open_painter.texture_sets[0]
        assert len(ts.layers[ts.active_layer].masks) == 2
        data = json.loads(result.read_text())
        data["native_2d_3d"] = "PASS"
        result.write_text(json.dumps(data))
        area.tag_redraw()
        bpy.app.timers.register(capture, first_interval=2)
    except Exception:
        result.write_text(traceback.format_exc())
        traceback.print_exc()
        bpy.ops.wm.quit_blender()


def capture():
    ts = bpy.context.scene.open_painter.texture_sets[0]
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(area=area, region=region):
        bpy.ops.open_painter.native_select(layer_id=ts.layers[ts.active_layer].id, mask=False)
        from open_painter.ui.dock import state
        assert not state.get(area).mask_selected
    bpy.app.timers.register(redraw_capture, first_interval=1)

def redraw_capture():
    bpy.ops.wm.redraw_timer(type="DRAW_WIN", iterations=2)
    bpy.app.timers.register(screenshot, first_interval=1)


def screenshot():
    bpy.ops.screen.screenshot(filepath=str(root / "docs/native-painter.png"))
    bpy.app.timers.register(quit_test, first_interval=1)


def quit_test():
    bpy.ops.wm.quit_blender()


bpy.app.timers.register(setup, first_interval=1)
