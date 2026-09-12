"""Real external Qt process ↔ Blender main-thread API ↔ EEVEE offscreen render."""
import sys,subprocess,json,time,traceback
from pathlib import Path
import bpy
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
import open_painter_bridge as bridge
result=ROOT/'bridge_test.json'
result.write_text(json.dumps({'status':'RUNNING'}))
start=None
snapshot=None

def launch():
    global start,snapshot
    try:
        area=next(a for a in bpy.context.screen.areas if a.type=='VIEW_3D')
        snapshot={'areas':[(a.type,a.width,a.height) for a in bpy.context.screen.areas], 'engine':bpy.context.scene.render.engine,'shading':area.spaces.active.shading.type}
        with bpy.context.temp_override(area=area):
            bridge._session=bridge.Session(bpy.context)
        session=bridge._session
        try:
            session.command({'op':'state','token':'wrong'})
            raise AssertionError('Invalid token was accepted')
        except ValueError: pass
        assert not session.authenticated
        session.process=subprocess.Popen([str(ROOT/'.venv/bin/python'),str(ROOT/'open_painter_bridge/client.py'),str(session.config),str(ROOT/'client_test.json')])
        bpy.app.timers.register(bridge.tick,first_interval=.01)
        start=time.monotonic()
        bpy.app.timers.register(check,first_interval=1)
    except Exception:
        result.write_text(json.dumps({'status':'FAIL','error':traceback.format_exc()}))
        traceback.print_exc()
        bpy.ops.wm.quit_blender()


def check():
    try:
        if bridge._session:
            if time.monotonic()-start>55: raise TimeoutError('External render test timed out')
            return 1
        data=json.loads((ROOT/'client_test.json').read_text())
        assert len(data['frames'])>=6
        assert abs(data['roughness']-.23)<.001
        assert abs(data['metallic']-.64)<.001
        area=next(a for a in bpy.context.screen.areas if a.type=='VIEW_3D')
        assert snapshot['areas']==[(a.type,a.width,a.height) for a in bpy.context.screen.areas]
        assert snapshot['engine']==bpy.context.scene.render.engine
        assert snapshot['shading']==area.spaces.active.shading.type
        node=bpy.context.object.active_material.node_tree.nodes.get('Principled BSDF')
        assert abs(node.inputs['Roughness'].default_value-.23)<.001
        result.write_text(json.dumps({'status':'PASS','checks':['separate Qt process','EEVEE offscreen frames','material edits round trip','main window layout and renderer restored','disconnect cleanup','invalid token rejected'], 'frames':data['frames']},indent=2))
        print(result.read_text())
    except Exception:
        result.write_text(json.dumps({'status':'FAIL','error':traceback.format_exc()},indent=2))
        traceback.print_exc()
    if bridge._session: bridge._session.close();bridge._session=None
    bpy.ops.wm.quit_blender()

bpy.context.preferences.view.show_splash=False
bpy.app.timers.register(launch,first_interval=2)
