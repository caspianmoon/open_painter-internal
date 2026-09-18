"""The Geometry Effects panel and the effect dialog draw in a real window with the material-driven controls."""

import json
import sys
import traceback
from pathlib import Path

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
import open_painter
from open_painter.aging import engine
from open_painter.core import image_manager as images, texture_set_manager as sets
from open_painter.core.project_manager import require_set
from open_painter.geometry_effects import manager
from open_painter.ui import geometry_effects as ui

REPORT = ROOT / ".blender-test/geometry-effects-ui.json"
REPORT.write_text(json.dumps(dict(result="RUNNING")))
bpy.context.preferences.view.show_splash = False
area = None
checks = []
panel_drawn = []
dialog_drawn = []
menu_drawn = []
original_panel_draw = ui.OP_PT_geometry_effects.draw
original_dialog_draw = ui.OP_OT_geometry_effect.draw
original_menu_draw = ui.OP_MT_geometry_effect_add.draw


def observe_panel(self, context):
    original_panel_draw(self, context)
    panel_drawn.append(str(self.layout.introspect()))


def observe_dialog(self, context):
    original_dialog_draw(self, context)
    dialog_drawn.append(str(self.layout.introspect()))


def observe_menu(self, context):
    original_menu_draw(self, context)
    menu_drawn.append(str(self.layout.introspect()))


def finish(error=None):
    REPORT.write_text(json.dumps(dict(result="FAIL" if error else "PASS", error=error, checks=checks), indent=2))
    print("GEOMETRY_EFFECTS_UI_" + ("FAIL" if error else "PASS"), error or "", flush=True)
    bpy.ops.wm.quit_blender()


def later(function, delay=.5):
    def run():
        try:
            if area:
                region = next(r for r in area.regions if r.type == "WINDOW")
                with bpy.context.temp_override(window=bpy.context.window, area=area, region=region):
                    function()
            else:
                function()
        except Exception:
            finish(traceback.format_exc())
    bpy.app.timers.register(run, first_interval=delay)


def redraw():
    sidebar = next(r for r in area.regions if r.type == "UI" and r.width > 10)
    try:
        sidebar.active_panel_category = "Painter"
    except AttributeError:
        pass
    sidebar.tag_redraw()


def setup():
    global area
    open_painter.register()
    panel = ui.OP_PT_geometry_effects
    bpy.utils.unregister_class(panel)
    panel.bl_options = set()
    panel.draw = observe_panel
    bpy.utils.register_class(panel)
    ui.OP_OT_geometry_effect.draw = observe_dialog
    ui.OP_MT_geometry_effect_add.draw = observe_menu
    area = next(a for a in bpy.context.screen.areas if a.type == "VIEW_3D")
    with bpy.context.temp_override(area=area):
        bpy.ops.open_painter.open_editor()
        ts = sets.create(bpy.context.scene, "Aged cube", 512)
        sets.assign(bpy.context.scene, ts, bpy.context.scene.objects["Cube"])
        u, v = np.meshgrid((np.arange(32) + .5) / 32, (np.arange(32) + .5) / 32)
        for kind in ("CURVATURE", "AO"):
            entry = ts.baked_maps.add()
            entry.kind = kind
            entry.image = images.create(f"UI {kind}", 32)
            value = np.ones((32, 32, 4), np.float32)
            value[..., :3] = (np.clip((.16 - np.abs(u - .5)) * 8, 0, 1) if kind == "CURVATURE" else .25 + .75 * v)[..., None]
            images.write(entry.image, value)
        ts.aging.environment, ts.aging.age_years, ts.aging.usage = "COASTAL", 20, "HEAVY"
        engine.apply(ts)
    later(redraw, delay=1.0)
    later(before_effect, delay=2.0)


def before_effect():
    assert panel_drawn, "the panel did not draw"
    text = panel_drawn[-1]
    # The panel is the applied list (empty so far) with the Add Geometry Effect menu and
    # Regenerate All above it and Bake Back below; the effect kinds live in the menu.
    assert "Pieces go where the material shows them" in text and "Add Geometry Effect" in text, text[:300]
    assert "No geometry effect applied" in text and "REGENERATE_ALL" in text and "Bake Back" in text, text[:400]
    checks.append("panel-draws-material-driven-sections")
    bpy.ops.wm.call_menu(name="OP_MT_geometry_effect_add")
    later(menu_open, delay=1.0)


def menu_open():
    assert menu_drawn, "the Add Geometry Effect menu did not draw"
    text = menu_drawn[-1]
    assert "Rust Flakes" in text and "Bolts" in text and "Displace from Height" in text, text[:300]
    checks.append("add-menu-offers-the-effect-kinds")
    window = bpy.context.window
    window.event_simulate(type="ESC", value="PRESS")
    window.event_simulate(type="ESC", value="RELEASE")
    later(open_dialog, delay=1.0)


def open_dialog():
    bpy.context.view_layer.objects.active = bpy.context.scene.objects["Cube"]
    result = bpy.ops.open_painter.geometry_effect("INVOKE_DEFAULT", action="RUST")
    assert result == {"RUNNING_MODAL"} or result == {"FINISHED"}, result
    later(dialog_open, delay=1.0)


def dialog_open():
    assert dialog_drawn, "the effect dialog did not draw"
    text = dialog_drawn[-1]
    assert "'source'" in text or "source" in text, text[:300]
    assert "Where the aging shows corrosion and rust" in text, text[:400]
    assert "prototypes" in text and "match" in text and "follow" in text
    checks.append("dialog-offers-where-own-pieces-match-follow")
    window = bpy.context.window
    window.event_simulate(type="ESC", value="PRESS")
    window.event_simulate(type="ESC", value="RELEASE")
    later(place, delay=1.0)


def place():
    ts = require_set(bpy.context)
    bpy.context.view_layer.objects.active = bpy.context.scene.objects["Cube"]
    assert bpy.ops.open_painter.geometry_effect(action="RUST", source="MATERIAL", density=60, size=.03) == {"FINISHED"}
    helper = bpy.context.view_layer.objects.active
    assert helper.get(manager.TAG) == "INSTANCE_HELPER" and helper["source_label"] == "corrosion and surface rust"
    assert "pieces on corrosion and surface rust" in bpy.context.scene.open_painter.status
    checks.append("rust-flakes-placed-from-the-material")
    panel_drawn.clear()
    redraw()
    later(helper_section, delay=1.5)


def helper_section():
    text = panel_drawn[-1] if panel_drawn else ""
    # The applied list names the helper by its distribution; the inspector below shows the
    # selected helper's source, result and controls.
    assert "Rust · corrosion and surface rust" in text and "Distribution: corrosion and surface rust" in text, text[:400]
    assert "instanced pieces" in text and "Follow the Material" in text and "Match Surface" in text, text[:400]
    checks.append("helper-section-shows-source-follow-match")
    ts = require_set(bpy.context)
    count = bpy.context.view_layer.objects.active["instance_count"]
    ts.aging.age_years = 40
    assert (bpy.context.scene.name, ts.id) in manager._pending
    later(followed, delay=2.0)


def followed():
    ts = require_set(bpy.context)
    helpers = manager.helpers_of(ts, following=True)
    assert len(helpers) == 1 and (bpy.context.scene.name, ts.id) not in manager._pending, "the timer regenerated the helper"
    checks.append("helper-followed-the-age-change-on-its-own")
    bpy.ops.screen.screenshot(filepath=str(ROOT / ".blender-test/geometry-effects-ui.png"))
    later(finish)


later(setup)
