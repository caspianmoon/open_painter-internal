"""Build the isolated native reference workspace and test a real native stroke."""
import sys,time,traceback,json
from pathlib import Path
import bpy
from bpy_extras.view3d_utils import location_3d_to_region_2d

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import open_painter_prototype as addon
addon.register()
bpy.context.preferences.view.show_splash = False
bpy.context.workspace.name = addon.WORKSPACE
report = ROOT/'test_result.json'
report.write_text(json.dumps({'status':'RUNNING'}))
roles = {}
metrics = {}
last_tick = None
intervals = []

def heartbeat():
    global last_tick
    now = time.perf_counter()
    if last_tick: intervals.append((now-last_tick)*1000)
    last_tick = now
    return .016


def later(fn,delay=.4):
    def run():
        try: fn()
        except Exception:
            report.write_text(json.dumps({'status':'FAIL','error':traceback.format_exc()},indent=2))
            traceback.print_exc()
            bpy.ops.wm.quit_blender()
    bpy.app.timers.register(run,first_interval=delay)


def override(area):
    return bpy.context.temp_override(area=area,region=next(r for r in area.regions if r.type=='WINDOW'))


def join(source,target):
    with override(source):
        bpy.ops.screen.area_join(source_xy=(source.x+source.width//2,source.y+source.height//2),target_xy=(target.x+target.width//2,target.y+target.height//2))


def split(area,direction,factor):
    with override(area): bpy.ops.screen.area_split(direction=direction,factor=factor)


def begin():
    areas = bpy.context.screen.areas
    join(next(a for a in areas if a.type=='VIEW_3D'),next(a for a in areas if a.type=='DOPESHEET_EDITOR'))
    later(join_right)


def join_right():
    areas = bpy.context.screen.areas
    join(next(a for a in areas if a.type=='PROPERTIES'),next(a for a in areas if a.type=='OUTLINER'))
    later(join_all)


def join_all():
    areas = bpy.context.screen.areas
    join(next(a for a in areas if a.type=='VIEW_3D'),next(a for a in areas if a.type=='PROPERTIES'))
    later(asset_split)


def asset_split():
    assert len(bpy.context.screen.areas)==1
    split(bpy.context.screen.areas[0],'VERTICAL',.23)
    later(right_split)


def right_split():
    left,remaining = sorted(bpy.context.screen.areas,key=lambda a:a.x)
    roles['assets'] = left
    left.type='FILE_BROWSER'
    left.ui_type='ASSETS'
    split(remaining,'VERTICAL',.72)
    later(view_split)


def view_split():
    _,center,right = sorted(bpy.context.screen.areas,key=lambda a:a.x)
    roles['right']=right
    right.type='PROPERTIES'
    split(center,'VERTICAL',.5)
    later(set_split)


def set_split():
    areas=sorted(bpy.context.screen.areas,key=lambda a:a.x)
    roles['3d'],roles['2d'] = areas[1:3]
    roles['2d'].type='IMAGE_EDITOR'
    split(roles['right'],'HORIZONTAL',.74)
    later(property_split)


def property_split():
    props=sorted((a for a in bpy.context.screen.areas if a.type=='PROPERTIES'),key=lambda a:a.y)
    split(props[0],'HORIZONTAL',.52)
    later(configure)


def configure():
    from mathutils import Quaternion
    areas=bpy.context.screen.areas
    assert len(areas)==6
    for area in areas:
        space=area.spaces.active
        if area.type=='PROPERTIES':
            space.context='MATERIAL'
        if hasattr(space,'show_region_ui'): space.show_region_ui=False
        if hasattr(space,'show_region_toolbar'): space.show_region_toolbar=False
    view=roles['3d'].spaces.active
    view.show_region_toolbar=True
    view.show_region_tool_header=True
    view.shading.type='MATERIAL'
    view.overlay.show_overlays=False
    view.region_3d.view_distance=7
    view.region_3d.view_rotation=Quaternion((.880,.280,.116,.365)).normalized()
    roles['2d'].spaces.active.ui_mode='PAINT'
    browser=roles['assets'].spaces.active
    browser.params.asset_library_reference='LOCAL'
    browser.params.display_type='THUMBNAIL'
    prop=browser.params.bl_rna.properties.get('display_size')
    if prop and prop.type == 'ENUM': browser.params.display_size='SMALL'
    elif prop: browser.params.display_size=64
    # Rendered custom previews are data, not a custom UI draw handler.
    colors=[('Painted Steel',(.08,.35,.6,1),.3,.8),('Dirty Plastic',(.12,.1,.08,1),.7,0),('Rough Wood',(.3,.12,.04,1),.8,0),('Brushed Steel',(.45,.48,.5,1),.28,1),('Copper',(.65,.25,.08,1),.3,1),('Matte Rubber',(.02,.02,.025,1),.9,0),('Porcelain',(.8,.8,.7,1),.2,0),('Red Paint',(.5,.02,.02,1),.35,0)]
    for name,color,rough,metal in colors:
        mat=bpy.data.materials.new('OPP Asset · '+name)
        mat.use_nodes=True
        node=mat.node_tree.nodes.get('Principled BSDF')
        node.inputs['Base Color'].default_value=color
        node.inputs['Roughness'].default_value=rough
        node.inputs['Metallic'].default_value=metal
        mat.asset_mark()
        path=ROOT.parents[1]/'src/open_painter/assets/previews'/f'{name.replace(" ","_")}.png'
        # These are visual stand-ins; the prototype presets are simple constant PBR values.
        with bpy.context.temp_override(id=mat):
            bpy.ops.ed.lib_id_load_custom_preview(filepath=str(path))
    # Save an empty workspace template before creating the test document.
    bpy.ops.wm.save_as_mainfile(filepath=str(ROOT/'open_painter_prototype/workspace.blend'))
    with override(roles['3d']):
        data=addon.project(bpy.context)
        data.resolution='2048'
        bpy.ops.opp.layer(action='FILL')
        data.layers[0].color=(.04,.12,.2,1)
        start=time.perf_counter()
        bpy.ops.opp.layer(action='PAINT')
        metrics['add_paint_layer_ms']=(time.perf_counter()-start)*1000
        bpy.ops.opp.paint()
    later(start_stroke,3)


def start_stroke():
    global point,node_signature,paint_pixels_before
    for target in (roles['3d'],roles['2d']):
        with override(target):
            try: target.spaces.active.show_region_asset_shelf=False
            except AttributeError: pass
    area=roles['3d']
    with override(area):
        data=addon.project(bpy.context)
        node_signature=[(n.as_pointer(),n.bl_idname) for n in data.material.node_tree.nodes]
        paint=bpy.context.scene.tool_settings.image_paint
        owner=paint.unified_paint_settings
        owner.use_unified_color=True
        owner.color=(.8,.03,.02)
        owner.use_unified_size=True
        owner.size=45
        p=location_3d_to_region_2d(bpy.context.region,area.spaces.active.region_3d,bpy.context.object.location)
        point=(int(p.x+area.x),int(p.y+bpy.context.region.y))
    # Fit the active 2D image after the region exists.
    with override(roles['2d']): bpy.ops.image.view_all(fit_view=True)
    bpy.app.timers.register(heartbeat,first_interval=.016)
    event('MOUSEMOVE','NOTHING')
    event('LEFTMOUSE','PRESS')
    later(move,.2)


def event(kind,value):
    bpy.context.window.event_simulate(type=kind,value=value,x=point[0],y=point[1])


def move():
    global point
    point=(point[0]+20,point[1]+5)
    event('MOUSEMOVE','NOTHING')
    later(held,.5)


def held():
    assert any(op.bl_idname=='PAINT_OT_image_paint' for op in bpy.context.window.modal_operators)
    data=addon.project(bpy.context)
    assert node_signature==[(n.as_pointer(),n.bl_idname) for n in data.material.node_tree.nodes]
    import numpy as np
    values=np.empty(2048*2048*4,dtype=np.float32)
    data.layers[-1].image.pixels.foreach_get(values)
    assert np.count_nonzero(values[3::4])>0,'Native brush did not paint'
    metrics['painted_pixels_while_held']=int(np.count_nonzero(values[3::4]))
    metrics['timer_gap_max_ms']=max(intervals)
    metrics['timer_gap_median_ms']=float(np.median(intervals))
    bpy.app.timers.unregister(heartbeat)
    event('LEFTMOUSE','RELEASE')
    later(capture,1)


def capture():
    bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP',iterations=2)
    later(save,1)


def save():
    bpy.ops.screen.screenshot(filepath=str(ROOT/'reference_prototype.png'))
    for layer in addon.project(bpy.context).layers:
        if layer.image: layer.image.pack()
    bpy.ops.wm.save_as_mainfile(filepath=str(ROOT/'painted_cube.blend'))
    metrics['status']='PASS'
    metrics['scope']='Native 3D shader preview and active-image 2D view; two Base Color layers; no Python compositing or draw handler; heartbeat gaps are not display latency.'
    report.write_text(json.dumps(metrics,indent=2))
    print(report.read_text())
    bpy.ops.wm.quit_blender()

later(begin,2)
