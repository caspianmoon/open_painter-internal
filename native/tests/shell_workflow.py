"""Exercise the exact JSON dispatcher called by the standalone controls."""
import base64,json,time
import bpy,open_painter_native as painter

def call(action,**values):
    payload=base64.b64encode(json.dumps(dict(action=action,**values)).encode()).decode()
    return json.loads(painter.shell_dispatch(payload))

start=time.perf_counter()
call('new');call('layer',kind='GROUP');state=call('layer',kind='PAINT')
mat=painter.material(bpy.context);layer=mat.painter_layers[mat.painter_active]
assert layer.parent==mat.painter_layers[0].uid
assert layer.image.size[:]==(1024,1024)
assert mat.node_tree.nodes.get(layer.uid+'alpha')
assert mat.node_tree.nodes[layer.uid+'mix0'].inputs[0].is_linked
call('set',key='name',value="Test ' quoted layer")
call('set',key='color',value=[.8,.1,.2,1])
node=mat.node_tree.nodes['Painter Surface'].as_pointer()
for i in range(100):call('set',key='roughness',value=i/100)
assert mat.node_tree.nodes['Painter Surface'].as_pointer()==node
call('duplicate');assert len(mat.painter_layers)==3
call('set',key='mask',value='BLACK')
assert mat.node_tree.nodes[mat.painter_layers[-1].uid+'alpha'].inputs[0].default_value==0
call('delete');assert len(mat.painter_layers)==2
call('save',path='/Volumes/SSD/projects/open_painter/native/logs/shell-workflow.blend')
bpy.ops.wm.open_mainfile(filepath='/Volumes/SSD/projects/open_painter/native/logs/shell-workflow.blend')
mat=painter.material(bpy.context)
assert len(mat.painter_layers)==2 and mat.painter_layers[1].image
assert mat.painter_layers[1].image.packed_file
print('SHELL_WORKFLOW_PASS',round(time.perf_counter()-start,3),flush=True)
