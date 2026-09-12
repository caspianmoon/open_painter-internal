"""Real strokes prove slot isolation, layer switching and imported alpha masking."""

import sys
import traceback
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
from open_painter.painting import paint_session, alphas
from open_painter.export.image_writer import write
from open_painter.ui.dock import interaction, state

bpy.context.preferences.view.show_splash = False
open_painter.register()
result = root / ".blender-test/paint_workflow_result.txt"
result.write_text("RUNNING")
area = None
image_a = None
image_b = None


def step(fn):
    def wrapped():
        try:
            if area:
                region = next(r for r in area.regions if r.type == "WINDOW")
                with bpy.context.temp_override(area=area, region=region):
                    fn()
            else:
                fn()
        except Exception:
            result.write_text(traceback.format_exc())
            traceback.print_exc()
            bpy.ops.wm.quit_blender()
        return None

    return wrapped


def stroke():
    region = bpy.context.region
    brush = bpy.context.scene.tool_settings.image_paint.brush
    unified = bpy.context.scene.tool_settings.image_paint.unified_paint_settings
    for owner in (brush, unified):
        owner.color = (0.8, 0.8, 0.8)
        owner.strength = 1
        owner.size = 45
    point = dict(
        name="stroke",
        mouse=(region.width * 0.5, region.height * 0.5),
        mouse_event=(region.width * 0.5, region.height * 0.5),
        is_start=True,
        location=(0, 0, 0),
        pressure=1,
        size=45,
        time=0,
        x_tilt=0,
        y_tilt=0,
    )
    assert bpy.ops.paint.image_paint(stroke=[point]) == {"FINISHED"}


@step
def setup():
    global area, image_a
    scene = bpy.context.scene
    obj = bpy.context.object
    obj.data.materials.append(bpy.data.materials.new("Main"))
    obj.data.materials.append(bpy.data.materials.new("Front"))
    for p in obj.data.polygons:
        p.material_index = 1 if p.normal.y < -0.5 else 0
    ts = sets.create(scene, "Other Faces")
    ts.resolution = "512"
    sets.assign(scene, ts, obj, 0)
    stack.add(ts, "PAINT")
    bpy.ops.open_painter.open_editor(view_mode="SINGLE")
    area = next(a for a in bpy.context.screen.areas if a.type == "VIEW_3D")
    region = next(r for r in area.regions if r.type == "WINDOW")
    with bpy.context.temp_override(area=area, region=region):
        bpy.ops.view3d.view_axis(type="FRONT")
        bpy.ops.view3d.view_selected(use_all_regions=False)
        image_a = paint_session.begin(bpy.context, ts, "ROUGHNESS")
    bpy.app.timers.register(blocked_face, first_interval=3)


@step
def blocked_face():
    global image_b
    stroke()
    assert np.count_nonzero(images.pixels(image_a)[..., 3]) == 0, (
        "Unassigned front face received paint"
    )
    paint_session.finish(bpy.context)
    ts = sets.create(bpy.context.scene, "Front Face")
    ts.resolution = "512"
    sets.assign(bpy.context.scene, ts, bpy.context.object, 1)
    stack.add(ts, "PAINT", "First")
    image_b = paint_session.begin(bpy.context, ts, "ROUGHNESS")
    bpy.app.timers.register(assigned_face, first_interval=1)


@step
def assigned_face():
    stroke()
    assert np.count_nonzero(images.pixels(image_b)[..., 3]) > 0, (
        "Assigned front face did not receive paint"
    )
    ts = bpy.context.scene.open_painter.texture_sets[1]
    stack.add(ts, "PAINT", "Second")
    interaction.route(bpy.context, state.get(area), "SELECT_LAYER", 1)
    assert bpy.context.scene.open_painter.paint_image != image_b
    # A completely black alpha must block the next stroke.
    alpha = np.zeros((32, 32, 4), dtype=np.float32)
    alpha[..., 3] = 1
    path = root / ".blender-test/black_alpha.png"
    write(path, alpha, "PNG", 8)
    alphas.import_alpha(bpy.context, str(path))
    bpy.app.timers.register(blocked_alpha, first_interval=1)


@step
def blocked_alpha():
    stroke()
    canvas = bpy.context.scene.open_painter.paint_image
    assert np.count_nonzero(images.pixels(canvas)[..., 3]) == 0, (
        "Black alpha failed to block brush stroke"
    )
    bpy.ops.open_painter.brush_action(action="CLEAR_ALPHA")
    bpy.app.timers.register(clear_alpha, first_interval=1)


@step
def clear_alpha():
    before = images.pixels(image_b).copy()
    stroke()
    canvas = bpy.context.scene.open_painter.paint_image
    assert np.count_nonzero(images.pixels(canvas)[..., 3]) > 0
    assert np.array_equal(images.pixels(image_b), before), "Second layer stroke changed first layer"
    # The full brush controls are accessible by scrolling, including the new import action.
    ui = state.get(area)
    ui.properties_scroll = ui.properties_limit
    ui.tab = "ALPHAS"
    bpy.app.timers.register(capture, first_interval=1)


@step
def capture():
    ui = state.get(area)
    assert any(h[4] == "IMPORT_ALPHA" for h in ui.hits), "Alpha control not reachable"
    bpy.ops.screen.screenshot(filepath=str(root / "docs/painting_controls.png"))
    interaction.route(bpy.context, ui, "VIEW", "IMAGE_EDITOR")
    paint_session.finish(bpy.context)
    assert area.spaces.active.ui_mode == "VIEW", "Finished session left output writable in 2D"
    result.write_text(
        "PASS: actual strokes respect material slots, layer selection and imported alpha; complete brush controls reachable; 2D finish prevents output painting"
    )
    print(result.read_text())
    bpy.ops.wm.quit_blender()


def dismiss_splash():
    for value in ("PRESS", "RELEASE"):
        bpy.context.window.event_simulate(type="ESC", value=value, x=100, y=100)
    return None


bpy.app.timers.register(dismiss_splash, first_interval=0.5)
bpy.app.timers.register(setup, first_interval=2)
