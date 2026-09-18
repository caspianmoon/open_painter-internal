"""The Material Aging panel draws in a real window, ages the set and follows an edit while the shader session runs."""

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
from open_painter.evaluation import shader_preview
from open_painter.ui import aging_panel

REPORT = ROOT / ".blender-test/material-aging-ui.json"
REPORT.write_text(json.dumps(dict(result="RUNNING")))
bpy.context.preferences.view.show_splash = False
area = None
checks = []
drawn = []
original_draw = aging_panel.OP_PT_native_aging.draw


def observe_draw(self, context):
    original_draw(self, context)
    drawn.append(str(self.layout.introspect()))


def finish(error=None):
    REPORT.write_text(json.dumps(dict(result="FAIL" if error else "PASS", error=error, checks=checks), indent=2))
    print("MATERIAL_AGING_UI_" + ("FAIL" if error else "PASS"), error or "", flush=True)
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
        pass   # not laid out yet: the editor opens on Painter anyway
    sidebar.tag_redraw()


def setup():
    global area
    open_painter.register()
    # The probe wants the body drawn; the panel ships collapsed.
    panel = aging_panel.OP_PT_native_aging
    bpy.utils.unregister_class(panel)
    panel.bl_options = set()
    panel.draw = observe_draw
    bpy.utils.register_class(panel)
    area = next(a for a in bpy.context.screen.areas if a.type == "VIEW_3D")
    with bpy.context.temp_override(area=area):
        bpy.ops.open_painter.open_editor()
        ts = sets.create(bpy.context.scene, "Aged cube", 512)
        sets.assign(bpy.context.scene, ts, bpy.context.scene.objects["Cube"])
        area.spaces.active.shading.type = "MATERIAL"
    later(redraw, delay=1.0)
    later(before_maps, delay=2.0)


def before_maps():
    assert drawn, "the panel did not draw"
    assert "Bake Mesh Maps first" in drawn[-1] and "open_painter.aging" in drawn[-1], drawn[-1][:400]
    checks.append("panel-asks-for-mesh-maps-before-aging")
    ts = require_set(bpy.context)
    u, v = np.meshgrid((np.arange(32) + .5) / 32, (np.arange(32) + .5) / 32)
    for kind in ("CURVATURE", "AO", "POSITION", "WORLD_NORMAL"):
        entry = ts.baked_maps.add()
        entry.kind = kind
        entry.image = images.create(f"UI {kind}", 32)
        value = np.ones((32, 32, 4), np.float32)
        if kind == "CURVATURE":
            value[..., :3] = np.clip((.16 - np.abs(u - .5)) * 8, 0, 1)[..., None]
        elif kind == "AO":
            value[..., :3] = (.25 + .75 * v)[..., None]
        elif kind == "POSITION":
            value[..., 0], value[..., 1], value[..., 2] = u, v, v
        else:
            z = 2 * v - 1
            value[..., 0] = .5 + .5 * np.sqrt(np.maximum(0, 1 - z * z))
            value[..., 1], value[..., 2] = .5, v
        images.write(entry.image, value)
    drawn.clear()
    redraw()
    later(age)


def age():
    assert drawn and "Bake Mesh Maps first" not in drawn[-1] and "Age Material" in drawn[-1], drawn[-1][:400]
    assert bpy.ops.open_painter.aging(action="APPLY") == {"FINISHED"}
    ts = require_set(bpy.context)
    assert engine.folder_of(ts) is not None
    checks.append("age-material-builds-the-folder-from-the-panel")
    drawn.clear()
    redraw()
    later(edit, delay=2.0)


def edit():
    ts = require_set(bpy.context)
    # Art Direction is a closed sub-panel by default (after the simulation); its header is drawn,
    # its Reset Art Direction button only once the artist opens it.
    assert drawn and "Re-age" in drawn[-1] and "Art Direction" in drawn[-1] and "Remove Aging" in drawn[-1], drawn[-1][-400:]
    checks.append("panel-shows-the-driven-folder-controls")
    before = {layer.aging_role: round(layer.opacity, 4) for layer in engine.members(ts, engine.folder_of(ts))}
    ts.aging.age_years = 40
    ts.aging.environment = "COASTAL"
    bpy.ops.open_painter.aging(action="ADD_EVENT")   # the events list draws rows: "just now" / "n y ago" / after the age
    ts.aging.events[0].year = 15
    bpy.ops.open_painter.aging(action="ADD_EVENT")
    ts.aging.events[1].year = 60
    after = {layer.aging_role: round(layer.opacity, 4) for layer in engine.members(ts, engine.folder_of(ts))}
    assert after["corrosion"] > before["corrosion"] and after["salt"] > before["salt"], (before, after)
    checks.append("age-and-environment-edits-re-drive-the-layers")
    later(session, delay=3.0)


def session():
    ts = require_set(bpy.context)
    for _ in range(40):
        shader_preview.tick(bpy.context.scene, ts)
    info = shader_preview.diagnostics(bpy.context.scene, ts)
    assert info["state"] in {"SHADER", "BOUNDARY", "CPU"} and not info["error"], info
    checks.append("shader-session-follows-the-aged-folder")
    bpy.ops.screen.screenshot(filepath=str(ROOT / ".blender-test/material-aging-ui.png"))
    later(finish)


later(setup)
