"""External UI bridge. Blender API and EEVEE calls remain on Blender's main thread."""
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import socket
import struct
import subprocess
import tempfile
import time

import bpy
from bpy.app.handlers import persistent
import gpu
import numpy as np
from mathutils import Matrix, Vector

_session = None


def rgba_hex(value):
    return '#' + ''.join(f'{round(max(0,min(1,float(c)))*255):02x}' for c in value[:3])


class Session:
    def __init__(self, context):
        self.window = context.window
        self.scene = context.scene
        self.view_layer = context.view_layer
        self.object = context.object
        self.area = next((a for a in context.screen.areas if a.type == 'VIEW_3D'),None)
        if not self.area:
            raise ValueError('Keep one 3D Viewport available for the EEVEE rendering context.')
        self.server = socket.socket()
        self.server.bind(('127.0.0.1',0))
        self.server.listen(1)
        self.server.setblocking(False)
        self.token = secrets.token_hex(32)
        self.directory = Path(tempfile.mkdtemp(prefix='open-painter-'))
        self.config = self.directory/'session.json'
        self.config.write_text(json.dumps({'port':self.server.getsockname()[1],'token':self.token}))
        os.chmod(self.config,0o600)
        self.client = None
        self.authenticated = False
        self.received = bytearray()
        self.pending = bytearray()
        self.offscreen = None
        self.size = None
        self.yaw = .65
        self.pitch = .35
        self.distance = 6.0
        self.process = None
        self.started = time.monotonic()
        self.metrics = []

    def state(self):
        theme=bpy.context.preferences.themes[0]
        ui=theme.user_interface
        obj=self.object
        mat=obj.active_material if obj and obj.type=='MESH' else None
        surface=next((n for n in mat.node_tree.nodes if n.type=='BSDF_PRINCIPLED'),None) if mat and mat.use_nodes else None
        return {'object':obj.name if obj else 'No selected mesh','material':mat.name if mat else None,
                'color':list(surface.inputs['Base Color'].default_value) if surface else [.5,.5,.5,1],
                'roughness':surface.inputs['Roughness'].default_value if surface else .5,
                'metallic':surface.inputs['Metallic'].default_value if surface else 0,
                'theme':{'background':rgba_hex(theme.properties.space.back),
                         'button':rgba_hex(ui.wcol_regular.inner),
                         'text':rgba_hex(ui.wcol_regular.text),
                         'accent':rgba_hex(ui.wcol_regular.inner_sel)},
                'engine':'BLENDER_EEVEE', 'protocol':1}

    def material(self):
        obj=self.object
        if not obj or obj.type!='MESH': raise ValueError('Select a mesh in Blender and reopen Open Painter.')
        mat=obj.active_material
        if mat is None:
            mat=bpy.data.materials.new('Open Painter Material')
            mat.use_nodes=True
            obj.data.materials.append(mat)
        mat.use_nodes=True
        node=next((n for n in mat.node_tree.nodes if n.type=='BSDF_PRINCIPLED'),None)
        if node is None: raise ValueError('Prototype editing requires a Principled BSDF material.')
        return mat,node

    def render(self,width,height):
        width=max(64,min(1024,int(width)))
        height=max(64,min(1024,int(height)))
        if (width,height)!=self.size:
            if self.offscreen: self.offscreen.free()
            self.offscreen=gpu.types.GPUOffScreen(width,height,format='RGBA8')
            self.size=(width,height)
        target=self.object.matrix_world.translation if self.object else Vector((0,0,0))
        eye=target+Vector((math.sin(self.yaw)*math.cos(self.pitch),-math.cos(self.yaw)*math.cos(self.pitch),math.sin(self.pitch)))*self.distance
        rotation=(target-eye).to_track_quat('-Z','Y')
        view=(Matrix.Translation(eye)@rotation.to_matrix().to_4x4()).inverted()
        f=1/math.tan(math.radians(50)/2)
        near,far=.05,1000
        projection=Matrix(((f*height/width,0,0,0),(0,f,0,0),(0,0,(far+near)/(near-far),2*far*near/(near-far)),(0,0,-1,0)))
        region=next(r for r in self.area.regions if r.type=='WINDOW')
        space=self.area.spaces.active
        if self.area.type!='VIEW_3D': raise ValueError('Restore the source 3D Viewport to continue rendering.')
        shading,engine,overlay=space.shading.type,self.scene.render.engine,space.overlay.show_overlays
        start=time.perf_counter()
        try:
            self.scene.render.engine='BLENDER_EEVEE'
            space.shading.type='RENDERED'
            space.overlay.show_overlays=False
            with bpy.context.temp_override(window=self.window,area=self.area,region=region):
                self.offscreen.draw_view3d(self.scene,self.view_layer,space,region,view,projection,do_color_management=True,draw_background=True)
                drawn=time.perf_counter()
                with self.offscreen.bind():
                    buffer=gpu.state.active_framebuffer_get().read_color(0,0,width,height,4,0,'UBYTE')
                    read=time.perf_counter()
                    pixels=np.frombuffer(buffer,dtype=np.uint8).reshape(height,width,4)
                    raw=np.ascontiguousarray(pixels[::-1]).tobytes()
        finally:
            space.shading.type=shading
            space.overlay.show_overlays=overlay
            self.scene.render.engine=engine
        elapsed=(time.perf_counter()-start)*1000
        self.metrics.append(elapsed)
        del self.metrics[:-120]
        return {'width':width,'height':height,'render_ms':elapsed,'draw_ms':(drawn-start)*1000,'read_ms':(read-drawn)*1000,'copy_ms':(time.perf_counter()-read)*1000,'engine':'BLENDER_EEVEE'},raw

    def command(self,message):
        if not self.authenticated:
            if not secrets.compare_digest(str(message.get('token','')),self.token):
                raise ValueError('Invalid session token')
            self.authenticated=True
        op=message.get('op')
        if op=='state': return self.state(),b''
        if op in ('frame','orbit'):
            self.yaw+=max(-1,min(1,float(message.get('dx',0))))
            self.pitch=max(-1.4,min(1.4,self.pitch+max(-1,min(1,float(message.get('dy',0))))))
            self.distance=max(.2,min(1000,self.distance*math.exp(max(-1,min(1,float(message.get('zoom',0)))))))
            if op=='frame': return self.render(message.get('width',640),message.get('height',640))
            return {'ok':True},b''
        if op=='material':
            mat,node=self.material()
            for key, socket_name in [('roughness','Roughness'),('metallic','Metallic'),('color','Base Color')]:
                if key in message and node.inputs[socket_name].is_linked:
                    raise ValueError(f'{socket_name} is driven by nodes; constant editing is unavailable.')
            for key,socket_name in [('roughness','Roughness'),('metallic','Metallic')]:
                if key in message: node.inputs[socket_name].default_value=max(0,min(1,float(message[key])))
            if 'color' in message:
                color=message['color']
                if len(color)!=4: raise ValueError('Color must have four components')
                node.inputs['Base Color'].default_value=[max(0,min(1,float(c))) for c in color]
            mat.node_tree.update_tag()
            return self.state(),b''
        raise ValueError('Unsupported bridge command')

    def tick(self):
        if self.process and self.process.poll() is not None: return False
        if not self.client:
            try:
                self.client,_=self.server.accept()
                self.client.setblocking(False)
                self.client.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
            except BlockingIOError: return time.monotonic()-self.started<120
        try:
            if self.pending:
                try:
                    count=self.client.send(self.pending)
                    del self.pending[:count]
                except BlockingIOError: pass
                return True
            try:
                chunk=self.client.recv(65536)
                if not chunk: return False
                self.received.extend(chunk)
            except BlockingIOError: pass
            if len(self.received)>65536: return False
            if b'\n' not in self.received: return True
            line,_,rest=self.received.partition(b'\n')
            self.received=bytearray(rest)
            try:
                header,raw=self.command(json.loads(line))
            except Exception as exc:
                header,raw={'error':str(exc)},b''
            header['bytes']=len(raw)
            packed=json.dumps(header).encode()
            self.pending.extend(struct.pack('!I',len(packed))+packed+raw)
            try:
                count=self.client.send(self.pending)
                del self.pending[:count]
            except BlockingIOError: pass
            return True
        except (OSError,ReferenceError): return False

    def close(self):
        if self.client: self.client.close()
        self.server.close()
        if self.offscreen: self.offscreen.free()
        if self.process and self.process.poll() is None: self.process.terminate()
        shutil.rmtree(self.directory,ignore_errors=True)


def tick():
    global _session
    if _session and _session.tick(): return .01
    if _session: _session.close()
    _session=None
    return None


class OPB_Preferences(bpy.types.AddonPreferences):
    bl_idname=__package__
    python_executable: bpy.props.StringProperty(name='UI Python executable',subtype='FILE_PATH',description='Python environment with PySide6 installed')
    def draw(self,context):
        self.layout.prop(self,'python_executable')


class OPB_OT_open(bpy.types.Operator):
    bl_idname='open_painter_bridge.open'
    bl_label='Open Painter — External Window'
    def execute(self,context):
        global _session
        if _session:
            self.report({'INFO'},'The Open Painter window is already running.')
            return {'FINISHED'}
        prefs=context.preferences.addons[__package__].preferences
        executable=bpy.path.abspath(prefs.python_executable)
        if not executable or not Path(executable).is_file():
            self.report({'ERROR'},'Set the UI Python executable in this extension’s preferences.')
            return {'CANCELLED'}
        try:
            _session=Session(context)
            _session.process=subprocess.Popen([executable,str(Path(__file__).with_name('client.py')),str(_session.config)])
            bpy.app.timers.register(tick,first_interval=.01)
        except Exception as exc:
            if _session: _session.close()
            _session=None
            self.report({'ERROR'},str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


@persistent
def stop_session(*args):
    global _session
    if bpy.app.timers.is_registered(tick): bpy.app.timers.unregister(tick)
    if _session: _session.close()
    _session=None


def menu(self,context): self.layout.operator('open_painter_bridge.open',icon='WINDOW')

def register():
    bpy.utils.register_class(OPB_Preferences)
    bpy.utils.register_class(OPB_OT_open)
    bpy.types.TOPBAR_MT_window.append(menu)
    bpy.app.handlers.load_pre.append(stop_session)


def unregister():
    global _session
    if bpy.app.timers.is_registered(tick): bpy.app.timers.unregister(tick)
    if _session: _session.close()
    _session=None
    if stop_session in bpy.app.handlers.load_pre: bpy.app.handlers.load_pre.remove(stop_session)
    bpy.types.TOPBAR_MT_window.remove(menu)
    bpy.utils.unregister_class(OPB_OT_open)
    bpy.utils.unregister_class(OPB_Preferences)
