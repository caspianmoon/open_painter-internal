"""Custom Qt application. No Blender editors, bpy imports, or second renderer."""
import json
from pathlib import Path
import queue
import socket
import struct
import sys
import time

from PySide6 import QtCore, QtGui, QtWidgets


class Bridge(QtCore.QThread):
    response=QtCore.Signal(str,dict,bytes,float)
    failed=QtCore.Signal(str)
    def __init__(self,config):
        super().__init__()
        self.config=config
        self.commands=queue.Queue(maxsize=8)
        self.busy=False
        self.running=True
        self.socket=None
    def submit(self,op,**args):
        try: self.commands.put_nowait({'op':op,**args})
        except queue.Full: pass
    def read(self,sock,count):
        result=bytearray()
        while len(result)<count:
            data=sock.recv(count-len(result))
            if not data: raise ConnectionError('Blender disconnected')
            result.extend(data)
        return bytes(result)
    def run(self):
        try:
            with socket.create_connection(('127.0.0.1',self.config['port']),timeout=30) as sock:
                self.socket=sock
                sock.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
                while self.running:
                    try: command=self.commands.get(timeout=.1)
                    except queue.Empty: continue
                    self.busy=True
                    command['token']=self.config['token']
                    start=time.perf_counter()
                    sock.sendall(json.dumps(command).encode()+b'\n')
                    size=struct.unpack('!I',self.read(sock,4))[0]
                    if size>65536: raise ValueError('Invalid response')
                    header=json.loads(self.read(sock,size))
                    count=header.get('bytes',0)
                    if not 0<=count<=1024*1024*4: raise ValueError('Invalid frame size')
                    raw=self.read(sock,count)
                    self.response.emit(command['op'],header,raw,(time.perf_counter()-start)*1000)
                    self.busy=False
        except Exception as exc:
            if self.running: self.failed.emit(str(exc))
        finally: self.socket=None
    def stop(self):
        self.running=False
        if self.socket:
            try: self.socket.shutdown(socket.SHUT_RDWR)
            except OSError: pass


class Canvas(QtWidgets.QLabel):
    orbit=QtCore.Signal(float,float,float)
    def __init__(self):
        super().__init__('Connecting to Blender EEVEE…')
        self.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(360,360)
        self.setStyleSheet('background:#242424;')
        self.setMouseTracking(True)
        self.last=None
        self.frame=None
    def mousePressEvent(self,event):
        if event.button() in (QtCore.Qt.MouseButton.MiddleButton,QtCore.Qt.MouseButton.RightButton): self.last=event.position()
    def mouseReleaseEvent(self,event): self.last=None
    def mouseMoveEvent(self,event):
        if self.last is not None:
            delta=event.position()-self.last
            self.last=event.position()
            self.orbit.emit(-delta.x()*.007,delta.y()*.007,0)
    def wheelEvent(self,event): self.orbit.emit(0,0,-event.angleDelta().y()*.001)
    def show_frame(self,raw,w,h):
        self.frame=QtGui.QImage(raw,w,h,w*4,QtGui.QImage.Format.Format_RGBA8888).copy()
        self.setPixmap(QtGui.QPixmap.fromImage(self.frame).scaled(self.size(),QtCore.Qt.AspectRatioMode.KeepAspectRatio,QtCore.Qt.TransformationMode.SmoothTransformation))


class Painter(QtWidgets.QMainWindow):
    def __init__(self,config):
        super().__init__()
        self.setWindowTitle('Open Painter — Adaptive Preview')
        self.resize(1400,850)
        self.material_color=[.5,.5,.5,1]
        self.received_frames=[]
        self.pending_orbit=[0.0,0.0,0.0]
        self.last_motion=0.0
        self.last_frame_request=0.0
        self.setStyleSheet('QMainWindow,QWidget{background:#303030;color:#dddddd;font:12px sans-serif;} QPushButton,QComboBox,QDoubleSpinBox{background:#454545;border:1px solid #222;border-radius:3px;padding:5px;} QDockWidget::title{background:#252525;padding:8px;} QTreeWidget,QListWidget{background:#303030;border:0;} QToolBar{spacing:6px;border:0;}')
        filemenu=self.menuBar().addMenu('File')
        filemenu.addAction('Close Window',self.close)
        viewmenu=self.menuBar().addMenu('View')
        self.canvas=Canvas()
        self.setCentralWidget(self.canvas)
        self.assets=QtWidgets.QListWidget()
        self.assets.setViewMode(QtWidgets.QListView.ViewMode.IconMode)
        self.assets.setIconSize(QtCore.QSize(66,66))
        self.assets.setGridSize(QtCore.QSize(76,92))
        self.assets.setResizeMode(QtWidgets.QListView.ResizeMode.Adjust)
        self.assets.setMovement(QtWidgets.QListView.Movement.Static)
        presets=[('Painted Steel',[.08,.35,.6,1],.3,.8),('Dirty Plastic',[.12,.1,.08,1],.7,0),('Rough Wood',[.3,.12,.04,1],.8,0),('Brushed Steel',[.45,.48,.5,1],.28,1),('Copper',[.65,.25,.08,1],.3,1),('Matte Rubber',[.02,.02,.025,1],.9,0),('Porcelain',[.8,.8,.7,1],.2,0),('Red Paint',[.5,.02,.02,1],.35,0)]
        for name,color,rough,metal in presets:
            item=QtWidgets.QListWidgetItem(name)
            path=Path(__file__).with_name('previews')/(name.replace(' ','_')+'.png')
            item.setIcon(QtGui.QIcon(str(path)))
            item.setData(QtCore.Qt.ItemDataRole.UserRole,{'color':color,'roughness':rough,'metallic':metal})
            item.setToolTip(name+' — constant PBR preset in this prototype')
            self.assets.addItem(item)
        leftbody=QtWidgets.QWidget()
        l=QtWidgets.QVBoxLayout(leftbody)
        search=QtWidgets.QLineEdit()
        search.setPlaceholderText('Search assets')
        l.addWidget(search)
        l.addWidget(self.assets)
        search.textChanged.connect(lambda text:[self.assets.item(i).setHidden(text.lower() not in self.assets.item(i).text().lower()) for i in range(self.assets.count())])
        self.left=self.dock('Assets',leftbody,QtCore.Qt.DockWidgetArea.LeftDockWidgetArea)
        self.left.setMinimumWidth(340)
        rightbody=QtWidgets.QWidget()
        r=QtWidgets.QVBoxLayout(rightbody)
        r.addWidget(QtWidgets.QLabel('TEXTURE SET LIST'))
        self.sets=QtWidgets.QTreeWidget()
        self.sets.setHeaderLabels(['Material','Renderer'])
        self.sets.setMaximumHeight(150)
        r.addWidget(self.sets)
        tabs=QtWidgets.QTabWidget()
        self.layers=QtWidgets.QTreeWidget()
        self.layers.setHeaderLabels(['Layers','Type'])
        self.layers.setToolTip('Layer editing is not implemented in this rendering bridge prototype.')
        tabs.addTab(self.layers,'Layers')
        info=QtWidgets.QLabel('Uses the selected Blender mesh and its material.\nChanges are applied directly in Blender.')
        info.setWordWrap(True)
        tabs.addTab(info,'Texture Set Settings')
        r.addWidget(tabs,1)
        r.addWidget(QtWidgets.QLabel('PROPERTIES · MATERIAL'))
        form=QtWidgets.QFormLayout()
        self.color=QtWidgets.QPushButton('Base Color')
        form.addRow('Base Color',self.color)
        self.rough=QtWidgets.QDoubleSpinBox()
        self.metal=QtWidgets.QDoubleSpinBox()
        for widget in (self.rough,self.metal):
            widget.setRange(0,1)
            widget.setSingleStep(.05)
            widget.setDecimals(3)
        form.addRow('Roughness',self.rough)
        form.addRow('Metallic',self.metal)
        r.addLayout(form)
        r.addStretch()
        self.right=self.dock('Texture Sets / Layers / Properties',rightbody,QtCore.Qt.DockWidgetArea.RightDockWidgetArea)
        self.right.setMinimumWidth(320)
        for dock,key in ((self.left,'T'),(self.right,'N')):
            action=dock.toggleViewAction()
            action.setShortcut(QtGui.QKeySequence(key))
            viewmenu.addAction(action)
        toolbar=self.addToolBar('View')
        toolbar.setMovable(False)
        toolbar.addWidget(QtWidgets.QLabel('  EEVEE  ·  3D View  '))
        toolbar.addAction('Refresh',lambda:self.bridge.submit('state'))
        toolbar.addWidget(QtWidgets.QLabel('  Right/middle drag: orbit  ·  Wheel: zoom'))
        self.statusBar().showMessage('Renderer bridge prototype · painting tools not migrated')
        self.bridge=Bridge(config)
        self.bridge.response.connect(self.response)
        self.bridge.failed.connect(lambda error:self.statusBar().showMessage(error))
        self.bridge.start()
        self.bridge.submit('state')
        self.canvas.orbit.connect(self.accumulate_orbit)
        self.assets.itemClicked.connect(lambda item:self.bridge.submit('material',**item.data(QtCore.Qt.ItemDataRole.UserRole)))
        self.rough.editingFinished.connect(lambda:self.bridge.submit('material',roughness=self.rough.value()))
        self.metal.editingFinished.connect(lambda:self.bridge.submit('material',metallic=self.metal.value()))
        self.color.clicked.connect(self.choose_color)
        self.timer=QtCore.QTimer(self)
        self.timer.timeout.connect(self.request_frame)
        self.timer.start(16)
    def dock(self,name,widget,side):
        dock=QtWidgets.QDockWidget(name,self)
        dock.setWidget(widget)
        dock.setObjectName(name)
        self.addDockWidget(side,dock)
        return dock
    def accumulate_orbit(self,dx,dy,zoom):
        self.last_motion=time.monotonic()
        for i,value in enumerate((dx,dy,zoom)): self.pending_orbit[i]+=value
    def request_frame(self):
        if self.isMinimized() or self.bridge.busy or not self.bridge.commands.empty(): return
        now=time.monotonic()
        moving=now-self.last_motion<.2
        if not moving and now-self.last_frame_request<.25: return
        self.last_frame_request=now
        dx,dy,zoom=self.pending_orbit
        self.pending_orbit=[0.0,0.0,0.0]
        limit=480 if moving else 800
        scale=min(1.0,limit/max(self.canvas.width(),self.canvas.height()))
        self.bridge.submit('frame',width=int(self.canvas.width()*scale),height=int(self.canvas.height()*scale),dx=dx,dy=dy,zoom=zoom)
    def choose_color(self):
        color=QtWidgets.QColorDialog.getColor(QtGui.QColor.fromRgbF(*self.material_color),self,'Base Color')
        if color.isValid(): self.bridge.submit('material',color=list(color.getRgbF()))
    def response(self,op,header,raw,elapsed):
        if 'error' in header:
            print('BRIDGE_ERROR',header['error'],flush=True)
            self.statusBar().showMessage(header['error'])
            return
        if op=='frame':
            self.canvas.show_frame(raw,header['width'],header['height'])
            self.received_frames.append({**{k:header[k] for k in ('render_ms','draw_ms','read_ms','copy_ms')},'round_trip_ms':elapsed})
            self.statusBar().showMessage(f'Blender EEVEE · {header["width"]} × {header["height"]} · render {header["render_ms"]:.0f} ms · transfer round trip {elapsed:.0f} ms')
        elif op in ('state','material'):
            self.material_color=header['color']
            self.rough.setValue(header['roughness'])
            self.metal.setValue(header['metallic'])
            self.sets.clear()
            self.sets.addTopLevelItem(QtWidgets.QTreeWidgetItem([header['material'] or header['object'],'EEVEE']))
            self.color.setStyleSheet('background: rgb('+','.join(str(round(c*255)) for c in self.material_color[:3])+');')
            theme=header['theme']
            self.setStyleSheet(f'QMainWindow,QWidget{{background:{theme["background"]};color:{theme["text"]};font:12px sans-serif;}} QPushButton,QComboBox,QDoubleSpinBox{{background:{theme["button"]};border:1px solid #222;border-radius:3px;padding:5px;}} QDockWidget::title{{background:#252525;padding:8px;}} QTreeWidget,QListWidget{{border:0;}} QTreeWidget::item:selected,QListWidget::item:selected{{background:{theme["accent"]};}}')
    def closeEvent(self,event):
        self.timer.stop()
        self.bridge.stop()
        self.bridge.wait(35000)
        event.accept()


if __name__=='__main__':
    config=json.loads(Path(sys.argv[1]).read_text())
    app=QtWidgets.QApplication(sys.argv)
    window=Painter(config)
    window.show()
    if len(sys.argv)>2:
        output=Path(sys.argv[2])
        def test():
            if len(window.received_frames)<6: return
            if len(window.received_frames)<12:
                window.accumulate_orbit(.08,0,0)
                return
            window.bridge.submit('material',roughness=.23,metallic=.64,color=[.1,.4,.7,1])
            def finish():
                window.grab().save(str(output.with_suffix('.png')))
                output.write_text(json.dumps({'frames':window.received_frames,'roughness':window.rough.value(),'metallic':window.metal.value()},indent=2))
                window.close()
            timer.stop()
            QtCore.QTimer.singleShot(1500,finish)
        timer=QtCore.QTimer()
        timer.timeout.connect(test)
        timer.start(300)
        QtCore.QTimer.singleShot(45000,window.close)
    sys.exit(app.exec())
