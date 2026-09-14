"""Open the current add-on interface in stock Blender's current 3D area.

Run: /Applications/Blender.app/Contents/MacOS/Blender --python scripts/launch_blender.py
Does not save a project, startup file, or preferences.
"""
import importlib
import addon_utils
import bpy


def open_painter():
    module='bl_ext.user_default.open_painter'
    if not addon_utils.check(module)[1]:
        addon_utils.enable(module,default_set=False)
    version=importlib.import_module(module+'.constants').VERSION
    assert version=='0.2.9',version
    window=bpy.context.window or next(iter(bpy.context.window_manager.windows),None)
    if window is None:return .25
    area=max((a for a in window.screen.areas if a.type=='VIEW_3D'),key=lambda a:a.width*a.height,default=None)
    if area is None:raise RuntimeError('No 3D Viewport is available to open Open Painter.')
    region=next(r for r in area.regions if r.type=='WINDOW')
    with bpy.context.temp_override(window=window,area=area,region=region):
        result=bpy.ops.open_painter.open_editor()
        assert result=={'FINISHED'},result
    assert importlib.import_module(module+'.ui.editor').is_area(area)
    print('OPEN_PAINTER_0_2_0_VISIBLE',flush=True)
    return None

bpy.app.timers.register(open_painter,first_interval=1)
