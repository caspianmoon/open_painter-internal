"""Actual Blender draw/hit regions, theme, and T/N routing for the bottom shelf."""
import sys,traceback,json
from pathlib import Path
import bpy
root=Path(__file__).resolve().parents[2];sys.path.insert(0,str(root/'src'))
import open_painter
from open_painter.ui.dock import state,drawing
open_painter.register();bpy.context.preferences.view.show_splash=False
result=root/'.blender-test/shelf-result.json';result.parent.mkdir(exist_ok=True)
area=None

def setup():
    global area
    area=max((a for a in bpy.context.screen.areas if a.type=='VIEW_3D'),key=lambda a:a.width*a.height)
    region=next(r for r in area.regions if r.type=='WINDOW')
    with bpy.context.temp_override(area=area,region=region):
        bpy.ops.open_painter.open_editor()
        bpy.ops.open_painter.texture_set(action='ADD')
        bpy.context.scene.open_painter.texture_sets[0].resolution='512'
        bpy.ops.open_painter.layer(action='PAINT')
        bpy.ops.open_painter.paint()
        state.get(area).tab = "MASKS"
    bpy.app.timers.register(check,first_interval=3)

def check():
    try:
        ui=state.get(area)
        assets=[h for h in ui.hits if h[4]=='ASSET']
        assert assets and all(h[1]+h[3]<=ui.bottom for h in assets)
        assert all(h[1] >= 50 * ui.ui_scale for h in assets), 'Shelf captions overlap footer'
        assert len(assets) == 6 and all(h[5][1] == "SMART_MASK" for h in assets)
        assert any(h[4] == "ROW_BLEND" for h in ui.hits)
        assert any(h[4] == "SLIDER" and h[5][0][0] == "ROW_CHANNEL" for h in ui.hits)
        assert not any(h[4] == "POPUP" and h[5] == "CHANNEL_BLEND" for h in ui.hits)
        assert ui.left<=40*ui.ui_scale
        assert any(h[4]=='SCROLL_ASSETS' for h in ui.hits)
        assert any(h[4]=='POPUP' and h[5]=='EXPORT' for h in ui.hits)
        assert any(h[4]=='SLIDER' and h[5][0]==('BRUSH','size') and h[1]>ui.height-43*ui.ui_scale for h in ui.hits)
        assert drawing.BG==drawing.rgba(bpy.context.preferences.themes[0].properties.space.back)
        bpy.ops.screen.screenshot(filepath=str(root/'docs/bottom-shelf.png'))
        result.write_text(json.dumps({'status':'PASS','assets_visible':len(assets),'bottom':ui.bottom,'rectangle_cache':str(drawing.rectangle_batch.cache_info())},indent=2))
    except Exception:
        result.write_text(traceback.format_exc());traceback.print_exc()
    if result.read_text().startswith("{"):
        region=next(r for r in area.regions if r.type=='WINDOW')
        for key in ('T','N'):
            for value in ('PRESS','RELEASE'):
                bpy.context.window.event_simulate(type=key,value=value,x=region.x+int(area.width*.5),y=region.y+int(area.height*.55))
        bpy.app.timers.register(check_hidden,first_interval=1)
    else:bpy.ops.wm.quit_blender()

def check_hidden():
    try:
        ui=state.get(area)
        assert not ui.show_assets and not ui.show_properties
        assert ui.bottom==22*ui.ui_scale and ui.right==0
        assert not any(h[4]=='ASSET' for h in ui.hits)
        data=json.loads(result.read_text());data['T_N']='PASS';result.write_text(json.dumps(data,indent=2))
    except Exception:result.write_text(traceback.format_exc());traceback.print_exc()
    bpy.ops.wm.quit_blender()
bpy.app.timers.register(setup,first_interval=1)
