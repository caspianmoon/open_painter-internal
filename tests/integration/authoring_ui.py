"""Inspect editable text and Brush Graph authoring in a real Blender window."""
import json
import sys
import time
import traceback
from pathlib import Path

import bpy
import gpu

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import layer_stack as stack, texture_set_manager as sets
from open_painter.evaluation import shader_preview

OUT = ROOT / '.blender-test/authoring-upgrade'
OUT.mkdir(exist_ok=True, parents=True)


def steps():
    open_painter.register()
    bpy.context.preferences.view.show_splash = False
    scene, window = bpy.context.scene, bpy.context.window
    window.event_simulate(type='ESC', value='PRESS')
    yield .5
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_plane_add(size=2)
    obj = bpy.context.object
    ts = sets.create(scene, 'Authoring Preview', 512)
    sets.assign(scene, ts, obj)
    layer = stack.add(ts, 'FILL', 'Blue enamel')
    stack.channel_for(layer, 'BASE_COLOR').color = (.015, .12, .3, 1)
    area = max((a for a in window.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    with bpy.context.temp_override(area=area, region=next(r for r in area.regions if r.type == 'WINDOW')):
        bpy.ops.open_painter.open_editor()
        bpy.ops.open_painter.layer(action='TEXT')
        bpy.ops.view3d.view_axis(type='TOP')
        bpy.ops.view3d.view_all(center=True)
    text_layer = stack.active(ts)
    text_layer.masks[0].text.content = 'GPU\nPAINT'
    text_layer.masks[0].text.size = .18
    stack.channel_for(text_layer, 'BASE_COLOR').color = (.9, .95, 1, 1)
    area.spaces.active.shading.type = 'MATERIAL'
    settings = scene.open_painter.vector_brush
    settings.symmetry = (True, False, False)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        shader_preview.tick(scene, ts)
        if shader_preview.current(scene, ts):
            break
        yield .1
    assert shader_preview.current(scene, ts), shader_preview.diagnostics(scene, ts)
    yield 2.
    bpy.ops.screen.screenshot(filepath=str(OUT / 'text-authoring.png'))
    before = {a.as_pointer() for a in window.screen.areas}
    with bpy.context.temp_override(area=area):
        bpy.ops.screen.area_split(direction='VERTICAL', factor=.55)
    yield .3
    editor = next(a for a in window.screen.areas if a.as_pointer() not in before)
    editor.type = 'NODE_EDITOR'
    with bpy.context.temp_override(area=editor):
        bpy.ops.open_painter.brush_graph()
    editor.spaces.active.show_region_ui = False
    area.spaces.active.show_region_ui = False
    area.spaces.active.overlay.show_overlays = False
    with bpy.context.temp_override(area=area, region=next(r for r in area.regions if r.type == 'WINDOW')):
        bpy.ops.view3d.view_all(center=True)
    tree = settings.node_group
    random = tree.nodes.new('OPBrushRandom')
    random.location = (-220, -250)
    output = next(n for n in tree.nodes if n.bl_idname == 'OPBrushOutput')
    tree.links.new(random.outputs['Value'], output.inputs['Roughness'])
    yield .3
    with bpy.context.temp_override(area=editor, region=next(r for r in editor.regions if r.type == 'WINDOW')):
        bpy.ops.node.view_all()
    yield 2.
    bpy.ops.screen.screenshot(filepath=str(OUT / 'brush-graph-authoring.png'))
    (OUT / 'authoring-ui.json').write_text(json.dumps({'status': 'PASS', 'backend': gpu.platform.backend_type_get(),
        'checks': ['Add Text action', 'Multiline saved mask', 'Material display', 'Brush Graph editor', 'Graph sockets'],
        'screenshots': ['text-authoring.png', 'brush-graph-authoring.png']}))
    print('AUTHORING_UI_PASS', flush=True)
    bpy.ops.wm.quit_blender()


iterator = steps()
def tick():
    try:
        return next(iterator)
    except StopIteration:
        return None
    except Exception:
        traceback.print_exc()
        (OUT / 'authoring-ui.json').write_text(json.dumps({'status': 'FAIL', 'error': traceback.format_exc()}))
        bpy.ops.wm.quit_blender()
        return None
bpy.app.timers.register(tick, first_interval=1.)
