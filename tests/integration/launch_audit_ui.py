"""Launch audit (0.12.2): the five workflows of the remedy plan, in a real window, with screenshots.

A. Smart material: apply, recolour, Dirt Amount to 0, more rust, into a component mask, back.
B. Mask correction: View Mask, Add Paint to Mask, paint a removal, back to the material.
C. Baking: open the Bake tab on a target-only bake, recommended maps, bake, change the mesh,
   see the map go Out of date.
D. Export: the Export tab on the active set with the Unreal preset; the preview lists exactly
   the files written.
E. Assets: browse smart materials, select (never applies), see the target, apply; then a
   smart mask on a chosen layer.

Every stop asserts the words an artist would rely on (what is selected, what changes) and
saves a screenshot to .blender-test/audit/ for the visual pass. Launch:

    blender --factory-startup --enable-event-simulate --python tests/integration/launch_audit_ui.py
OP_LAUNCH_AUDIT_REPORT selects the JSON report.
"""
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests/integration"))
import open_painter
import ui_fixture
from open_painter.smart_materials import controls
from open_painter.ui import bake_view, export_view, native, selection

REPORT = Path(os.environ.get("OP_LAUNCH_AUDIT_REPORT", str(ROOT / ".blender-test/launch-audit.json")))
SHOTS = ROOT / ".blender-test/audit"
SHOTS.mkdir(parents=True, exist_ok=True)
REPORT.write_text(json.dumps(dict(result="RUNNING")))
bpy.context.preferences.view.show_splash = False
state = dict(area=None, ids=None, checks=[], drawn={"inspector": [], "layers": [], "bake": [], "export": [], "assets": []},
             tmp=tempfile.mkdtemp(prefix="op-audit-"))


if os.name == "nt":
    # UILayout.introspect() evaluates the drawn text unescaped, so a Windows path such as
    # C:\Users\... ("\U") raises SyntaxError and the panel cannot be read. The test shows the
    # export folder with forward slashes (Windows accepts them); the plan itself is unchanged.
    class _ShownPosix(type(Path())):
        def __str__(self):
            return super().__str__().replace(chr(92), "/")

    _current_plan = export_view.current_plan

    def _plan_shown_posix(context):
        plan = _current_plan(context)
        if plan is not None and plan.directory is not None:
            plan.directory = _ShownPosix(plan.directory)
        return plan

    export_view.current_plan = _plan_shown_posix


def observe(cls, key):
    original = cls.draw

    def draw(self, context):
        original(self, context)
        try:
            state["drawn"][key].append(str(self.layout.introspect()))
        except SyntaxError:
            state["drawn"][key].append("DRAWN")
    cls.draw = draw


def finish(error=None):
    REPORT.write_text(json.dumps(dict(result="FAIL" if error else "PASS", error=error, checks=state["checks"],
                                      screenshots=sorted(p.name for p in SHOTS.glob("*.png"))), indent=2))
    print("LAUNCH_AUDIT_" + ("FAIL" if error else "PASS"), error or "", flush=True)
    bpy.ops.wm.quit_blender()


def later(function, delay=.7):
    def run():
        try:
            area = state["area"]
            region = next(r for r in area.regions if r.type == "WINDOW")
            with bpy.context.temp_override(window=bpy.context.window, area=area, region=region):
                function()
        except Exception:
            finish(traceback.format_exc())
    bpy.app.timers.register(run, first_interval=delay)


def redraw(category="Painter", scroll=0):
    """Redraw the sidebar on ``category``; ``scroll`` pages down first (what an artist does to
    reach the inspector under a long layer list on a small window)."""
    sidebar = next(r for r in state["area"].regions if r.type == "UI" and r.width > 10)
    try:
        sidebar.active_panel_category = category
    except (AttributeError, TypeError):
        pass
    if scroll:
        with bpy.context.temp_override(window=bpy.context.window, area=state["area"], region=sidebar):
            for _ in range(6):
                bpy.ops.view2d.scroll_up(page=True)
            for _ in range(scroll):
                bpy.ops.view2d.scroll_down(page=True)
    for region in state["area"].regions:
        region.tag_redraw()


def shot(name):
    bpy.ops.screen.screenshot(filepath=str(SHOTS / f"{name}.png"))


def last(key):
    return state["drawn"][key][-1] if state["drawn"][key] else ""


def body():
    return next(ts for ts in bpy.context.scene.open_painter.texture_sets if ts.id == state["ids"]["body"])


def bindings():
    ts = body()
    folder = next(l for l in ts.layers if l.id == state["ids"]["material"])
    return ts, folder, {b.control.label: b for b in controls.bindings(ts, folder)}


def put(binding, value):
    if binding.control.computed:
        vector = list(getattr(binding.owner, binding.field))
        vector[binding.index] = value
        setattr(binding.owner, binding.field, vector)
    else:
        setattr(binding.owner, binding.field, value)


def setup():
    open_painter.register()
    observe(native.OP_PT_native_properties, "inspector")
    observe(native.OP_PT_native_layers, "layers")
    observe(bake_view.OP_PT_bake_maps, "bake")
    observe(export_view.OP_PT_export_view, "export")
    observe(native.OP_PT_native_library, "assets")
    area = max((a for a in bpy.context.screen.areas if a.type == "VIEW_3D"), key=lambda a: a.width * a.height)
    state["area"] = area
    with bpy.context.temp_override(area=area, region=next(r for r in area.regions if r.type == "WINDOW")):
        # The audit looks at the Painter as an artist would: the area maximised (Ctrl+Space).
        bpy.ops.screen.screen_full_area()
    area = max((a for a in bpy.context.screen.areas if a.type == "VIEW_3D"), key=lambda a: a.width * a.height)
    state["area"] = area
    with bpy.context.temp_override(area=area, region=next(r for r in area.regions if r.type == "WINDOW")):
        bpy.ops.open_painter.open_editor()
        state["ids"] = ui_fixture.build(bpy.context.scene)
        ts = body()
        ts.preview_backend = "LIVE"
        area.spaces.active.shading.type = "MATERIAL"
        # Forward slashes keep the drawn folder field introspectable on Windows (see _ShownPosix).
        bpy.context.scene.open_painter.export_directory = (Path(state["tmp"]) / "export").as_posix()
    later(workflow_a, 2.0)


# ---- A. smart material -------------------------------------------------------------------------

def workflow_a():
    ts, folder, by_label = bindings()
    bpy.ops.open_painter.native_select(layer_id=folder.id)
    redraw(scroll=1)
    later(a_material, 1.0)


def a_material():
    text = last("inspector")
    assert "Worn Painted Metal" in text and "Paint Color" in text and "Dirt Amount" in text, text[:400]
    assert "Body" in text and "›" in text, "the breadcrumb names the set and the material"
    shot("A1-material-selected")
    ts, folder, by_label = bindings()
    put(by_label["Paint Color"], (.55, .12, .08, 1))
    put(by_label["Dirt Amount"], 0.0)
    put(by_label["Rust Amount"], 0.6)
    dirt = by_label["Dirt Amount"]
    assert dirt.members[0][0].opacity == 0, "Dirt Amount 0 hides the dirt layer"
    state["checks"].append("A: material controls edited from the inspector (dirt to none)")
    redraw(scroll=1)
    later(a_recoloured, 1.0)


def a_recoloured():
    shot("A2-recoloured-no-dirt-more-rust")
    dirt_layer = next(l for l in body().layers if l.id == state["ids"]["material_layers"]["Recess dirt"])
    bpy.ops.open_painter.native_select(layer_id=dirt_layer.id, mask=True)
    redraw(scroll=1)
    later(a_mask, 1.0)


def a_mask():
    text = last("inspector")
    assert "Recess dirt" in text and ("Cavity Dirt" in text or "cavity" in text.lower()), text[:500]
    assert "View Mask" in text and "Add Paint to Mask" in text
    target = selection.resolve(bpy.context)
    assert target.kind == "MASK" and target.layer.name == "Recess dirt"
    shot("A3-component-mask-selected")
    state["checks"].append("A: the component mask is the selection, named in the breadcrumb")
    _, folder, _ = bindings()
    bpy.ops.open_painter.native_select(layer_id=folder.id)
    redraw(scroll=1)
    later(a_back, 1.0)


def a_back():
    text = last("inspector")
    assert "Paint Color" in text and "Dirt Amount" in text
    shot("A4-back-to-material")
    state["checks"].append("A: back to the material with its controls")
    later(workflow_b, .5)


# ---- B. mask correction ------------------------------------------------------------------------

def workflow_b():
    dirt_layer = next(l for l in body().layers if l.id == state["ids"]["material_layers"]["Recess dirt"])
    bpy.ops.open_painter.native_select(layer_id=dirt_layer.id, mask=True)
    assert bpy.ops.open_painter.view_mask(action="VIEW") == {"FINISHED"}
    redraw(scroll=1)
    later(b_viewing, 1.2)


def b_viewing():
    text = last("inspector")
    assert "Back to Material" in text and "Snapshot" in text, text[:400]
    shot("B1-view-mask")
    state["checks"].append("B: View Mask shows the snapshot state and the way back")
    assert bpy.ops.open_painter.mask_paint(role="REMOVE", start_painting=True) == {"FINISHED"}
    redraw(scroll=1)
    later(b_painting, 1.2)


def b_painting():
    text = last("inspector")
    assert "Remove Coverage" in text, text[:600]
    assert "Paint black to remove coverage" in text, "the brush direction is stated"
    assert "Painting" in text
    target = selection.resolve(bpy.context)
    assert target.kind == "MASK" and target.mask.source == "PAINT" and target.mask.operation == "MULTIPLY"
    generators = [m for m in target.layer.masks if m.source == "GENERATOR"]
    assert generators, "the procedural mask is kept, never rasterised"
    shot("B2-painting-removal")
    state["checks"].append("B: paint correction added above the generator; direction stated")
    bpy.ops.open_painter.paint(stop=True)
    assert bpy.ops.open_painter.view_mask(action="BACK") == {"FINISHED"}
    _, folder, _ = bindings()
    bpy.ops.open_painter.native_select(layer_id=folder.id)
    redraw(scroll=1)
    later(b_back, 1.0)


def b_back():
    assert "Back to Material" not in last("inspector")
    shot("B3-back-to-material")
    state["checks"].append("B: back to the material")
    later(workflow_c, .5)


# ---- C. baking ---------------------------------------------------------------------------------

def workflow_c():
    from open_painter.baking.bake_manager import bake_selected
    ts = body()
    ts.bake.selected_to_active = False        # a target-only bake of the cube itself
    assert bpy.ops.open_painter.show_task(task="BAKE") == {"FINISHED"}
    assert bpy.ops.open_painter.bake(action="SELECT_RECOMMENDED") == {"FINISHED"}
    assert ts.bake.normal or ts.bake.ao or ts.bake.curvature, "the material's maps were selected"
    bake_selected(bpy.context, ts)            # synchronous here; the queue operator does the same in steps
    redraw("Bake")
    later(c_bake_tab, 1.5)


def c_bake_tab():
    text = last("bake")
    states = {kind: bake_view.map_state(body(), kind)[0] for kind in ("NORMAL", "AO", "CURVATURE")}
    assert all(s == "Up to date" for s in states.values()), states
    assert "Up to date" in text, text[:400]
    state["bake_before"] = states
    shot("C1-bake-tab-up-to-date")
    state["checks"].append(f"C: baked the recommended maps, states {states}")
    # A mesh edit: the maps read the mesh, so they go out of date.
    cube = bpy.context.scene.objects["Cube"]
    cube.data.vertices[0].co.x += .3
    cube.data.update()
    bpy.context.view_layer.update()
    redraw("Bake")
    later(c_stale, 1.2)


def c_stale():
    states = {kind: bake_view.map_state(body(), kind)[0] for kind in ("NORMAL", "AO", "CURVATURE")}
    text = last("bake")
    assert all(s == "Out of date" for s in states.values()), states
    assert "Out of date" in text and "Up to date" not in text, text[:400]
    shot("C2-bake-out-of-date")
    state["checks"].append(f"C: after a mesh edit the maps read {states}")
    later(workflow_d, .5)


# ---- D. export ---------------------------------------------------------------------------------

def workflow_d():
    project = bpy.context.scene.open_painter
    project.target = "UNREAL"
    assert bpy.ops.open_painter.show_task(task="EXPORT") == {"FINISHED"}
    view = export_view.view_settings(bpy.context)
    view.contents, view.scope, view.folder_source = "TARGET", "ACTIVE", "PROJECT"
    redraw("Export")
    later(d_preview, 1.2)


def d_preview():
    text = last("export")
    plan = export_view.current_plan(bpy.context)
    names = [output.path.name for set_plan in plan.sets for output in set_plan.outputs]
    assert names and all(name.startswith("T_") for name in names), names
    assert "Output Preview" in text, text[:300]
    assert all(name in text for name in names[:3]), (names[:3], text[:800])
    assert len(plan.sets) == 1, "Active Texture Set never widens to other sets"
    state["export_names"] = names
    shot("D1-export-preview")
    assert bpy.ops.open_painter.export_plan() == {"FINISHED"}, bpy.context.scene.open_painter.status
    written = sorted(p.name for p in Path(plan.directory).iterdir() if p.suffix.lower() in {".png", ".tif", ".tiff", ".exr"})
    listed = sorted(names)
    assert written == listed, (written, listed)
    state["checks"].append(f"D: the preview listed exactly the {len(listed)} textures written")
    redraw("Export")
    later(d_done, 1.0)


def d_done():
    shot("D2-exported")
    later(workflow_e, .5)


# ---- E. assets ---------------------------------------------------------------------------------

def workflow_e():
    from open_painter.ui import library_categories as categories
    assert bpy.ops.open_painter.show_task(task="PAINTER") == {"FINISHED"}
    project = bpy.context.scene.open_painter
    project.asset_category = "SMART_MATERIALS"
    entry = next((e for e in native.catalog() if e[1] == "SMART_RECIPE" and e[0] == "Old Brass"), None)
    assert entry is not None
    state["entry"] = entry
    layers_before = len(body().layers)
    applied = native.select_click(bpy.context, entry, categories.key(entry))
    assert not applied and len(body().layers) == layers_before, "a first click selects, never applies"
    redraw()
    later(e_selected, 1.0)


def e_selected():
    text = last("assets")
    assert "Old Brass" in text and "Apply" in text, text[:500]
    target, possible = native.application_target(bpy.context, state["entry"])
    assert possible and target, target
    assert target.split(":")[0].strip() in text or target[:20] in text, (target, text[:600])
    shot("E1-asset-selected-target-shown")
    state["checks"].append(f"E: selecting says what Apply does: {target}")
    ui = selection.ui_state(bpy.context)
    assert bpy.ops.open_painter.native_asset(entry_key=ui.pending_asset, apply_now=True) == {"FINISHED"}
    assert any(l.module_name == "Old Brass" for l in body().layers), "applied as an editable material"
    state["checks"].append("E: Apply added the smart material")
    from open_painter.ui import library_categories as categories
    project = bpy.context.scene.open_painter
    project.asset_category = "MASKS"
    coat = next(l for l in body().layers if l.id == state["ids"]["material_layers"]["Blue paint coat"])
    bpy.ops.open_painter.native_select(layer_id=coat.id)
    mask_entry = next(e for e in native.catalog() if e[1] == "SMART_MASK")
    state["mask_entry"] = mask_entry
    native.select_click(bpy.context, mask_entry, categories.key(mask_entry))
    redraw()
    later(e_mask_selected, 1.0)


def e_mask_selected():
    text = last("assets")
    target, possible = native.application_target(bpy.context, state["mask_entry"])
    assert "Blue paint coat" in target, target
    assert "Blue paint coat" in text, text[:600]
    shot("E2-smart-mask-target")
    coat = next(l for l in body().layers if l.id == state["ids"]["material_layers"]["Blue paint coat"])
    before = len(coat.masks)
    ui = selection.ui_state(bpy.context)
    assert bpy.ops.open_painter.native_asset(entry_key=ui.pending_asset, apply_now=True) == {"FINISHED"}
    coat = next(l for l in body().layers if l.id == state["ids"]["material_layers"]["Blue paint coat"])
    assert len(coat.masks) == before + 1, "the smart mask was added to the chosen layer, nothing replaced"
    state["checks"].append(f"E: smart mask applied to the stated target ({target})")
    redraw()
    later(e_done, 1.0)


def e_done():
    shot("E3-smart-mask-applied")
    later(finish, .5)


bpy.app.timers.register(setup, first_interval=.2)
