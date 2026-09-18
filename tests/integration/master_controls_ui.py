"""Master controls in a real window: drawn as real properties, undone and redone as one step each.

Cast Iron carries all three binding kinds — direct (Casting Roughness), inverted factor
(Oxide Coverage, a component of the folder's ``master_factor`` vector) and multiplier
(Surface Relief, ``master_scale``). The inspector draws them as those properties; an edit of
each, pushed like a UI edit, undoes and redoes with its members; a duplicate's controls stay
apart from the original's; deleting the bound layer makes the control unavailable and undo
brings it back.

Launch: blender --factory-startup --enable-event-simulate --python tests/integration/master_controls_ui.py
OP_MASTER_CONTROLS_UI_REPORT selects the JSON report.
"""
import json
import os
import sys
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests/integration"))
import open_painter
import ui_fixture
from open_painter.core import layer_stack as stack, texture_set_manager as sets
from open_painter.smart_materials import composer, controls
from open_painter.ui import native

REPORT = Path(os.environ.get("OP_MASTER_CONTROLS_UI_REPORT", str(ROOT / ".blender-test/master-controls-ui.json")))
REPORT.write_text(json.dumps(dict(result="RUNNING")))
bpy.context.preferences.view.show_splash = False
state = dict(area=None, ts_id=None, folder_id=None, checks=[], drawn=[])


def observe(cls):
    original = cls.draw

    def draw(self, context):
        original(self, context)
        try:
            state["drawn"].append(str(self.layout.introspect()))
        except SyntaxError:
            state["drawn"].append("DRAWN (introspection failed)")
    cls.draw = draw


def finish(error=None):
    REPORT.write_text(json.dumps(dict(result="FAIL" if error else "PASS", error=error, checks=state["checks"]), indent=2))
    print("MASTER_CONTROLS_UI_" + ("FAIL" if error else "PASS"), error or "", flush=True)
    bpy.ops.wm.quit_blender()


def later(function, delay=.6):
    def run():
        try:
            area = state["area"]
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
    sidebar = next(r for r in state["area"].regions if r.type == "UI" and r.width > 10)
    try:
        sidebar.active_panel_category = "Painter"
    except AttributeError:
        pass
    sidebar.tag_redraw()


def ts():
    return next(t for t in bpy.context.scene.open_painter.texture_sets if t.id == state["ts_id"])


def folder():
    return next(l for l in ts().layers if l.id == state["folder_id"])


def by_label():
    return {b.control.label: b for b in controls.bindings(ts(), folder())}


def get(binding):
    if binding.control.computed:
        return list(getattr(binding.owner, binding.field))[binding.index]
    return getattr(binding.owner, binding.field)


def put(binding, value):
    if binding.control.computed:
        vector = list(getattr(binding.owner, binding.field))
        vector[binding.index] = value
        setattr(binding.owner, binding.field, vector)
    else:
        setattr(binding.owner, binding.field, value)


def setup():
    open_painter.register()
    observe(native.OP_PT_native_properties)
    area = max((a for a in bpy.context.screen.areas if a.type == "VIEW_3D"), key=lambda a: a.width * a.height)
    state["area"] = area
    with bpy.context.temp_override(area=area, region=next(r for r in area.regions if r.type == "WINDOW")):
        bpy.ops.open_painter.open_editor()
        scene = bpy.context.scene
        set_ = sets.create(scene, "Iron", 512)
        sets.assign(scene, set_, scene.objects["Cube"])
        bpy.context.view_layer.update()
        ui_fixture.bake_synthetic(set_)
        state["ts_id"] = set_.id
        state["folder_id"] = composer.apply(set_, "Cast Iron").id
        set_.active_layer = stack.index_of(set_, state["folder_id"])
    bpy.ops.ed.undo_push(message="Cast Iron applied")
    later(redraw, 1.0)
    later(drawn, 2.0)


def drawn():
    text = state["drawn"][-1] if state["drawn"] else ""
    for label in ("Casting Roughness", "Casting Grain", "Surface Relief", "Oxide Coverage", "Rust Speckling", "Cavity Deposits"):
        assert label in text, (label, text[:400])
    # Computed controls are drawn as components of the folder's vectors, direct ones as their property.
    assert "master_factor" in text and "master_scale" in text, text[:600]
    assert "OP_Channel.value" in text, "a direct control draws the channel property"
    state["checks"].append("inspector-draws-direct-and-computed-controls")
    bindings = by_label()
    state["before"] = dict(roughness=get(bindings["Casting Roughness"]), coverage=get(bindings["Oxide Coverage"]),
                           relief=get(bindings["Surface Relief"]))
    # Three edits, pushed like three UI edits.
    put(bindings["Casting Roughness"], .2)
    bpy.ops.ed.undo_push(message="Casting Roughness")
    put(bindings["Oxide Coverage"], .3)
    bpy.ops.ed.undo_push(message="Oxide Coverage")
    put(bindings["Surface Relief"], 2.5)
    bpy.ops.ed.undo_push(message="Surface Relief")
    later(undo_steps, .5)


def undo_steps():
    bindings = by_label()
    skin = bindings["Oxide Coverage"].members[0][0]
    assert abs(skin.amount - .7) < 1e-6, skin.amount
    assert abs(get(bindings["Surface Relief"]) - 2.5) < 1e-6
    bpy.ops.ed.undo()
    bindings = by_label()
    assert abs(get(bindings["Surface Relief"]) - state["before"]["relief"]) < 1e-6, get(bindings["Surface Relief"])
    assert abs(get(bindings["Oxide Coverage"]) - .3) < 1e-6, "the earlier edit stays"
    bpy.ops.ed.undo()
    bindings = by_label()
    assert abs(get(bindings["Oxide Coverage"]) - state["before"]["coverage"]) < 1e-6
    assert abs(bindings["Oxide Coverage"].members[0][0].amount - .1) < 1e-6, "undo restored the member"
    assert abs(get(bindings["Casting Roughness"]) - .2) < 1e-6
    bpy.ops.ed.undo()
    bindings = by_label()
    assert abs(get(bindings["Casting Roughness"]) - state["before"]["roughness"]) < 1e-6
    state["checks"].append("undo-restores-direct-inverted-and-multiplier-edits-one-step-each")
    bpy.ops.ed.redo()
    bpy.ops.ed.redo()
    bpy.ops.ed.redo()
    bindings = by_label()
    assert abs(get(bindings["Casting Roughness"]) - .2) < 1e-6
    assert abs(get(bindings["Oxide Coverage"]) - .3) < 1e-6
    assert abs(get(bindings["Surface Relief"]) - 2.5) < 1e-6
    assert all(b.ok for b in bindings.values())
    state["checks"].append("redo-reapplies-the-edits-with-bindings-intact")
    later(duplicate, .5)


def duplicate():
    set_ = ts()
    set_.active_layer = stack.index_of(set_, state["folder_id"])
    assert bpy.ops.open_painter.layer(action="DUPLICATE") == {"FINISHED"}
    bpy.ops.ed.undo_push(message="Duplicate")
    set_ = ts()
    original = folder()
    copy = next(l for l in set_.layers if l.type == "FOLDER" and l.id != original.id and l.module_source == original.module_source)
    copied = {b.control.label: b for b in controls.bindings(set_, copy)}
    assert all(b.ok for b in copied.values())
    assert abs(get(copied["Oxide Coverage"]) - .3) < 1e-6
    put(copied["Oxide Coverage"], .9)
    assert abs(get(by_label()["Oxide Coverage"]) - .3) < 1e-6, "the original keeps its value"
    state["checks"].append("duplicate-controls-its-own-layers")
    state["copy_id"] = copy.id
    bpy.ops.ed.undo_push(message="Copy edit")
    later(delete_target, .5)


def delete_target():
    set_ = ts()
    bindings = by_label()
    set_.active_layer = stack.index_of(set_, bindings["Rust Speckling"].layer.id)
    assert bpy.ops.open_painter.layer(action="DELETE") == {"FINISHED"}
    bpy.ops.ed.undo_push(message="Delete")
    bpy.ops.open_painter.native_select(layer_id=state["folder_id"])   # the material again, not its neighbour
    redraw()
    later(deleted_drawn, 1.0)


def deleted_drawn():
    bindings = by_label()
    assert not bindings["Rust Speckling"].ok and "deleted" in bindings["Rust Speckling"].reason
    text = state["drawn"][-1]
    assert "Rust Speckling: layer 'Pinpoint oxidation' was deleted" in text, text[:600]
    assert bindings["Oxide Coverage"].ok
    state["checks"].append("deleted-target-shows-as-unavailable")
    bpy.ops.ed.undo()
    bindings = by_label()
    assert bindings["Rust Speckling"].ok and bindings["Rust Speckling"].layer.name == "Pinpoint oxidation"
    state["checks"].append("undo-brings-the-binding-back")
    bpy.ops.screen.screenshot(filepath=str(ROOT / ".blender-test/master-controls-ui.png"))
    later(finish, .5)


bpy.app.timers.register(setup, first_interval=.2)
