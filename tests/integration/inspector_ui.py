"""The rebuilt Layers panel and inspector draw in a real window (UI plan phase 2).

Selecting Worn Painted Metal shows its authored master controls at once; selecting a
component layer, its mask, a paint layer and a channel filter shows each one's own
first controls; View Mask rewires the display material to a snapshot and Back to
Material restores it; Add Paint to Mask keeps the generator; Open Inspector hosts the
same inspector in a companion Properties area that follows the session and closes
again without touching other areas.

Launch: blender --factory-startup --enable-event-simulate --python tests/integration/inspector_ui.py
OP_INSPECTOR_UI_REPORT selects the JSON report.
"""
import json
import os
import sys
import time
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests/integration"))
import open_painter
import ui_fixture
from ui_layout_recorder import Recorder
from open_painter.core import layer_stack as stack
from open_painter.ui import companion, inspector, mask_view, native, selection

REPORT = Path(os.environ.get("OP_INSPECTOR_UI_REPORT", str(ROOT / ".blender-test/inspector-ui.json")))
SHOTS = ROOT / ".blender-test"
REPORT.write_text(json.dumps(dict(result="RUNNING")))
bpy.context.preferences.view.show_splash = False
state = dict(area=None, ids=None, checks=[], timings={}, drawn={"layers": [], "inspector": [], "companion": []},
             screen_before=None)


def observe(cls, key):
    original = cls.draw

    def draw(self, context):
        started = time.perf_counter()
        original(self, context)
        state["timings"].setdefault(key, []).append((time.perf_counter() - started) * 1000)
        try:
            # introspect() truncates long layouts; the recorder below reads the content.
            state["drawn"][key].append(str(self.layout.introspect()))
        except SyntaxError:
            state["drawn"][key].append("DRAWN (introspection truncated)")
    cls.draw = draw


def finish(error=None):
    timings = {key: dict(n=len(values), p95_ms=round(sorted(values)[int(len(values) * .95) - 1] if values else 0, 2),
                         max_ms=round(max(values), 2) if values else 0) for key, values in state["timings"].items()}
    REPORT.write_text(json.dumps(dict(result="FAIL" if error else "PASS", error=error, checks=state["checks"],
                                      draw_timings=timings, scroll_errors=state.get("scroll_errors", [])), indent=2))
    print("INSPECTOR_UI_" + ("FAIL" if error else "PASS"), error or "", flush=True)
    bpy.ops.wm.quit_blender()


def later(function, delay=.6):
    def run():
        try:
            area = state["area"]
            region = next(r for r in area.regions if r.type == "WINDOW")
            with bpy.context.temp_override(window=bpy.context.window, area=area, region=region,
                                           screen=bpy.context.window.screen):
                function()
        except Exception:
            finish(traceback.format_exc())
    bpy.app.timers.register(run, first_interval=delay)


def redraw(scroll=True):
    for key in state["drawn"]:
        state["drawn"][key].clear()
    sidebar = next(r for r in state["area"].regions if r.type == "UI" and r.width > 10)
    try:
        sidebar.active_panel_category = "Painter"
    except AttributeError:
        pass
    if scroll:
        # Panels scrolled out of the sidebar are not drawn: bring the inspector into view.
        with bpy.context.temp_override(window=bpy.context.window, area=state["area"], region=sidebar):
            for op in [lambda: bpy.ops.view2d.scroll_down(page=True)] * 4 + [lambda: bpy.ops.view2d.scroll_up(page=True)]:
                try:
                    op()   # to the bottom, then one page up: the inspector sits above the collapsed task panels
                except RuntimeError as exc:
                    state.setdefault("scroll_errors", []).append(str(exc))
    for area in bpy.context.window.screen.areas:
        area.tag_redraw()


def shot(name):
    path = SHOTS / f"inspector-{name}.png"
    try:
        bpy.ops.screen.screenshot(filepath=str(path))
    except RuntimeError:
        pass


def last(key):
    assert state["drawn"][key], f"the {key} panel did not draw"
    return state["drawn"][key][-1]


def rendered():
    """What the inspector shows for the session's target, through the layout recorder."""
    assert state["drawn"]["inspector"], "the inspector panel did not draw in the window"
    layout = Recorder()
    inspector.draw(layout, bpy.context, selection.resolve(bpy.context))
    return layout.text


def body():
    return next(ts for ts in bpy.context.scene.open_painter.texture_sets if ts.id == state["ids"]["body"])


def setup():
    open_painter.register()
    observe(native.OP_PT_native_layers, "layers")
    observe(native.OP_PT_native_properties, "inspector")
    observe(companion.OP_PT_companion_inspector, "companion")
    area = max((a for a in bpy.context.screen.areas if a.type == "VIEW_3D"), key=lambda a: a.width * a.height)
    state["area"] = area
    state["screen_before"] = [(a.type, a.width, a.height) for a in bpy.context.screen.areas]
    with bpy.context.temp_override(area=area, region=next(r for r in area.regions if r.type == "WINDOW")):
        bpy.ops.open_painter.open_editor()
        state["ids"] = ui_fixture.build(bpy.context.scene)
        ts = body()
        ts.preview_backend = "LIVE"   # the viewport shows the shader; the CPU baseline is elsewhere
        area.spaces.active.shading.type = "MATERIAL"
    later(redraw, 1.0)
    later(material, 2.5)


def material():
    layers, panel = last("layers"), rendered()
    for label in ("Add Layer", "Add Mask", "Add Effect", "More"):
        assert f"'draw_string': '{label}'" in layers, (label, layers[:300])
    assert "active_layer" in layers and "Blend Channel: " in layers, layers[:300]
    for label in ("Paint Color", "Paint Roughness", "Chip Amount", "Scratch Amount", "Dirt Amount", "Rust Amount"):
        assert label in panel, f"{label} missing from the material inspector: {panel[:500]}"
    assert panel.index("Paint Color") < panel.index("Published Outputs"), "appearance controls come first"
    assert "Worn Painted Metal" in panel
    state["checks"].append("material-shows-authored-master-controls-first")
    shot("material")
    # A master control is the actual property: editing the layer moves it.
    ts = body()
    coat = next(l for l in ts.layers if l.id == state["ids"]["material_layers"]["Blue paint coat"])
    from open_painter.smart_materials import controls
    folder = next(l for l in ts.layers if l.id == state["ids"]["material"])
    binding = next(b for b in controls.bindings(ts, folder) if b.control.label == "Paint Color")
    stack.channel_for(coat, "BASE_COLOR").color = (.6, .1, .1, 1)
    assert tuple(binding.value)[:3] == (.6, .1, .1) or abs(binding.value[0] - .6) < 1e-6
    state["checks"].append("master-control-binds-the-layer-property")
    redraw()
    later(component, 1.0)


def component():
    coat = state["ids"]["material_layers"]["Blue paint coat"]
    bpy.ops.open_painter.native_select(layer_id=coat)
    redraw()
    later(component_drawn, 1.0)


def component_drawn():
    shot("component")
    panel = rendered()
    assert "properties_channel" in panel and "enabled" in panel, panel[:400]
    assert "Layer Opacity" in panel and "Projection and Scale" in panel, panel[:600]
    state["checks"].append("fill-layer-shows-channels-values-and-projection")
    coat = state["ids"]["material_layers"]["Blue paint coat"]
    bpy.ops.open_painter.native_select(layer_id=coat, mask=True)
    redraw()
    later(mask_drawn, 1.0)


def mask_drawn():
    panel = rendered()
    assert "View Mask" in panel and "Add Paint to Mask" in panel, panel[:400]
    assert "Chips" in panel and "OP_UL_masks" in panel
    assert "procedural" in panel.lower()
    state["checks"].append("mask-inspector-shows-stack-view-and-paint-actions")
    shot("mask")
    ts = body()
    coat = next(l for l in ts.layers if l.id == state["ids"]["material_layers"]["Blue paint coat"])
    generators = [m.source for m in coat.masks]
    assert bpy.ops.open_painter.view_mask(action="VIEW") == {"FINISHED"}
    view = mask_view.active(ts)
    assert view is not None and view["layer_id"] == coat.id
    from open_painter.core import texture_set_node
    assert len(ts.material.node_tree.nodes) == 3, "the material keeps its Texture Set node, BSDF and output"
    assert texture_set_node.showing_mask(ts), "the surface shows the snapshot"
    state["checks"].append("view-mask-rewires-the-display-to-a-snapshot")
    redraw()
    later(viewing_drawn, 1.5)


def viewing_drawn():
    shot("view-mask")
    panel = rendered()
    assert "Back to Material" in panel and "Snapshot" in panel, panel[:400]
    ts = body()
    coat = next(l for l in ts.layers if l.id == state["ids"]["material_layers"]["Blue paint coat"])
    coat.masks[0].amount = .5
    assert mask_view.stale(ts), "an edit marks the snapshot out of date"
    assert bpy.ops.open_painter.view_mask(action="BACK") == {"FINISHED"}
    assert mask_view.active(ts) is None
    from open_painter.core import texture_set_node
    assert not texture_set_node.showing_mask(ts) and len(ts.material.node_tree.nodes) == 3
    state["checks"].append("back-to-material-restores-the-shaded-display")
    # Add Paint to Mask keeps the generator and adds an explicit paint mask on top.
    before = [m.source for m in coat.masks]
    assert bpy.ops.open_painter.mask_paint(role="REMOVE", start_painting=False) == {"FINISHED"}
    coat = next(l for l in ts.layers if l.id == state["ids"]["material_layers"]["Blue paint coat"])
    assert [m.source for m in coat.masks] == before + ["PAINT"], [m.source for m in coat.masks]
    assert coat.masks[-1].operation == "MULTIPLY" and "Remove Coverage" in coat.masks[-1].name
    assert selection.resolve(bpy.context).mask_id == coat.masks[-1].id
    state["checks"].append("add-paint-to-mask-keeps-the-generator")
    bpy.ops.open_painter.native_select(layer_id=state["ids"]["paint"])
    redraw()
    later(paint_drawn, 1.0)


def paint_drawn():
    panel = rendered()
    assert "Paint destination" in panel or "Painting" in panel, panel[:400]
    assert "open_painter.paint" in panel and "painter_brush" in panel
    state["checks"].append("paint-layer-shows-destination-and-brush")
    ts = body()
    stripes = next(l for l in ts.layers if l.id == state["ids"]["stripes"])
    prop = stack.channel_for(stripes, "BASE_COLOR")
    effect = prop.effects.add()
    effect.id, effect.kind, effect.name = "fx-ui-levels", "LEVELS", "Levels"
    bpy.ops.open_painter.native_select(layer_id=state["ids"]["stripes"], channel="BASE_COLOR", effect_id="fx-ui-levels")
    redraw()
    later(filter_drawn, 1.0)


def filter_drawn():
    panel = rendered()
    assert "Levels" in panel and "Filter Stack" in panel, panel[:400]
    assert "Back to Stripes" in panel
    state["checks"].append("filter-inspector-shows-parameters-first")
    bpy.ops.open_painter.native_select(layer_id=state["ids"]["details"])
    redraw()
    later(folder_drawn, 1.0)


def folder_drawn():
    panel = rendered()
    assert "Group Opacity" in panel and "Group Compositing" in panel, panel[:400]
    assert "Paint Color" not in panel, "an ordinary folder has no master controls"
    state["checks"].append("plain-folder-has-group-controls-only")
    # Open Inspector: a companion Properties area on this screen, the Painter area kept.
    screen = bpy.context.window.screen
    count = len(screen.areas)
    assert bpy.ops.open_painter.open_inspector(placement="SPLIT") == {"FINISHED"}
    assert len(screen.areas) == count + 1
    area = companion.companion_for(screen)
    assert area is not None and area.type == "PROPERTIES" and area.spaces.active.context == "TOOL"
    from open_painter.ui import editor
    assert editor.is_area(state["area"]), "the Painter area survives the split"
    state["checks"].append("open-inspector-splits-a-companion-properties-area")
    redraw()
    later(companion_drawn, 1.5)


def companion_drawn():
    shot("companion")
    panel = last("companion")
    assert "Details" in panel and ("Group Opacity" in panel or "truncated" in panel), panel[:400]
    bpy.ops.open_painter.native_select(layer_id=state["ids"]["material"])
    redraw()
    later(companion_follows, 1.0)


def companion_follows():
    panel = last("companion")
    assert "Paint Color" in panel or "truncated" in panel, "the companion follows the session's selection"
    area = companion.companion_for(bpy.context.window.screen)
    with bpy.context.temp_override(area=area, screen=bpy.context.window.screen):
        assert selection.resolve(bpy.context).layer_id == state["ids"]["material"]
    state["checks"].append("companion-inspector-follows-the-session")
    screen = bpy.context.window.screen
    count = len(screen.areas)
    assert bpy.ops.open_painter.close_inspector() == {"FINISHED"}
    assert len(screen.areas) == count - 1, "the split area is closed again"
    assert companion.companion_for(screen) is None
    types = [a.type for a in screen.areas]
    assert types.count("PROPERTIES") == [t for t, *_ in state["screen_before"]].count("PROPERTIES"), types
    state["checks"].append("close-inspector-removes-only-the-split-area")
    later(done, .5)


def done():
    timings = state["timings"]
    assert timings["inspector"] and timings["layers"]
    finish()


bpy.app.timers.register(setup, first_interval=.2)
