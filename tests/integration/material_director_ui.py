"""Visible native panel, full fixture turn, one-step Blender undo/redo, viewport PNG."""

import base64
import json
from pathlib import Path
import sys
import traceback
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
import open_painter
from open_painter.ai import session, viewport
from open_painter.authoring import document, transactions
from open_painter.core import texture_set_manager as sets
from open_painter.core.project_manager import require_set
from open_painter.evaluation import shader_preview
from open_painter.ui import material_director

REPORT = ROOT / ".blender-test/material-director-ui.json"
REPORT.write_text(json.dumps(dict(result="RUNNING")))
bpy.context.preferences.view.show_splash = False
area = None
checks = []
patcher = None
drawn = []
original_draw = material_director.OP_PT_material_director.draw


def observe_draw(self, context):
    original_draw(self, context)
    drawn.append(str(self.layout.introspect()))


material_director.OP_PT_material_director.draw = observe_draw


class FixtureConnection:
    def __init__(self):
        self.sent = []
        self.events = []
        self.closed = False

    def send(self, message):
        self.sent.append(message)

    def poll(self):
        events, self.events = self.events, []
        return events

    def close(self):
        self.closed = True


def finish(error=None):
    global patcher
    session.disconnect()
    if patcher: patcher.stop(); patcher = None
    REPORT.write_text(json.dumps(dict(result="FAIL" if error else "PASS", error=error,
                                      checks=checks, live_account_used=False), indent=2))
    print("MATERIAL_DIRECTOR_UI_" + ("FAIL" if error else "PASS"), error or "", flush=True)
    bpy.ops.wm.quit_blender()


def later(function, delay=.4):
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


def setup():
    global area, patcher
    open_painter.register()
    area = next(a for a in bpy.context.screen.areas if a.type == "VIEW_3D")
    with bpy.context.temp_override(area=area):
        bpy.ops.open_painter.open_editor()
        ts = sets.create(bpy.context.scene, "Material Director", 512)
        sets.assign(bpy.context.scene, ts, bpy.context.scene.objects["Cube"])
        area.spaces.active.shading.type = "MATERIAL"
        settings = bpy.context.window_manager.open_painter_director
        settings.mode = "CREATE"
        settings.prompt = "Make blue painted steel"
        session.connection = FixtureConnection()
        session.state.update(connected=True, signed_in=True, status="Ready (test fixture)")
        patcher = patch.object(session, "ensure_online")
        patcher.start()
        bpy.ops.ed.undo_push(message="Before Material Director")
    later(start_instruction)


def start_instruction():
    sidebar = next(r for r in area.regions if r.type == "UI")
    with bpy.context.temp_override(area=area, region=sidebar):
        sidebar.active_panel_category = "Director"
    sidebar.tag_redraw()
    assert bpy.ops.open_painter.director_run() == {"FINISHED"}
    sent = session.connection.sent[-1]
    assert sent["action"] == "run" and "current_context" in sent["input"]
    assert "viewport" in {item["id"] for item in sent["images"]}
    assert {t["name"] for t in sent["tools"]} == {"get_context", "search_library", "describe_asset", "list_generated_images", "propose_changes"}
    proposal = dict(version=1, summary="Blue painted steel", commands=[
        dict(op="add_layer", id="new:paint", name="Blue Paint", kind="FILL"),
        dict(op="set_channel", layer_id="new:paint", channel="BASE_COLOR", values=dict(color=[.035, .15, .35, 1])),
        dict(op="set_channel", layer_id="new:paint", channel="ROUGHNESS", values=dict(value=.42))])
    session.connection.events = [dict(event="tool", call_id="fixture", request_id=sent["request_id"],
                                      tool="propose_changes", arguments=dict(proposal_json=json.dumps(proposal))),
                                 dict(event="completed", request_id=sent["request_id"], status="completed")]
    session.tick()
    ts = require_set(bpy.context)
    assert len(ts.layers) == 1, session.state
    assert session.connection.sent[-1]["success"], session.connection.sent[-1]
    assert len(transactions.history(ts)) == 1
    shader_preview.tick(bpy.context.scene, ts)
    checks.append("native-run-provider-fixture-validated-automatic-commit")
    later(undo)


def undo():
    assert bpy.ops.ed.undo() == {"FINISHED"}
    assert not require_set(bpy.context).layers
    assert not transactions.history(require_set(bpy.context))
    checks.append("one-blender-undo-reverses-whole-ai-batch")
    later(redo)


def redo():
    assert bpy.ops.ed.redo() == {"FINISHED"}
    ts = require_set(bpy.context)
    assert len(ts.layers) == 1 and len(transactions.history(ts)) == 1
    shader_preview.tick(bpy.context.scene, ts)
    checks.append("one-blender-redo-restores-batch-and-receipt")
    later(capture_view, delay=1.5)


def capture_view():
    ts = require_set(bpy.context)
    before = document.canonical(document.snapshot(ts))
    resources = transactions.resources()
    image = viewport.capture(bpy.context, ts)
    data = base64.b64decode(image.split(",", 1)[1])
    assert data.startswith(b"\x89PNG") and len(data) > 2000, len(data)
    (ROOT / ".blender-test/material-director-view.png").write_bytes(data)
    assert before == document.canonical(document.snapshot(ts))
    assert resources == transactions.resources()
    checks.append("viewport-png-without-image-datablocks-or-material-mutations")
    assert drawn and any("open_painter.director_run" in layout for layout in drawn)
    checks.append("native-director-panel-drawn-with-real-controls")
    bpy.ops.screen.screenshot(filepath=str(ROOT / ".blender-test/material-director-ui.png"))
    later(finish)


later(setup)
