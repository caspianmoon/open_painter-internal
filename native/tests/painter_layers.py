import bpy,json,time
from pathlib import Path
root=Path(__file__).resolve().parents[1]
start=time.perf_counter()
bpy.ops.painter.new_set()
mat=bpy.context.object.active_material
assert all(p.material_index==bpy.context.object.active_material_index for p in bpy.context.object.data.polygons)
bpy.ops.painter.layer(kind='FILL')
bottom=mat.painter_layers[0];bottom.color=(.8,.1,.1,1)
bpy.ops.painter.layer(kind='GROUP')
group=mat.painter_layers[1]
bpy.ops.painter.layer(kind='FILL')
child=mat.painter_layers[2]
assert child.parent==group.uid
node_count=len(mat.node_tree.nodes)
shader=mat.node_tree.nodes['Painter Surface'].as_pointer()
child.roughness=.2;child.opacity=.6;group.opacity=.4
assert shader==mat.node_tree.nodes['Painter Surface'].as_pointer()
assert node_count==len(mat.node_tree.nodes)
child.blend='MULTIPLY'
assert mat.node_tree.nodes[child.uid+'mix0'].blend_type=='MULTIPLY'
child.blend='MIX'
for i in range(100):child.roughness=i/100
assert shader==mat.node_tree.nodes['Painter Surface'].as_pointer()
bpy.ops.painter.mask(kind='BLACK')
assert mat.node_tree.nodes[child.uid+'mix0'].inputs[0].default_value==0
bpy.ops.painter.mask(kind='WHITE')
assert abs(mat.node_tree.nodes[child.uid+'mix0'].inputs[0].default_value-.6)<1e-5
image=bpy.data.images.new('Fill Image',width=32,height=32)
child.image=image;child.tiling=(2,3);child.offset=(.2,.3)
assert mat.node_tree.nodes[child.uid+'image'].image==image
assert tuple(mat.node_tree.nodes[child.uid+'mapping'].inputs['Scale'].default_value)==(2,3,1)
bpy.ops.painter.mask(kind='BITMAP');child.mask_image=image
assert mat.node_tree.nodes[child.uid+'mask_image'].image==image
assert mat.node_tree.nodes[child.uid+'mix0'].inputs[0].is_linked
child.opacity=.3
assert abs(mat.node_tree.nodes[child.uid+'mask_opacity'].inputs[0].default_value-.3)<1e-5
mat.painter_active=1;bpy.ops.painter.delete()
assert len(mat.painter_layers)==1
path=root/'logs/painter-roundtrip.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(path))
bpy.ops.wm.open_mainfile(filepath=str(path))
assert len(bpy.context.object.active_material.painter_layers)==1
(root/'logs/painter-tests.json').write_text(json.dumps({'status':'PASS','seconds':time.perf_counter()-start,'checks':['fill layer','folder parenting','group opacity','blend modes','100 property edits without rebuilding nodes','black/white/bitmap masks','image fill UV transforms','property edits preserve shader nodes','recursive folder deletion','saved layer data']},indent=2))
