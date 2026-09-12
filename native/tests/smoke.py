"""Run with the compiled app --background --factory-startup --python this_file."""
import json
from pathlib import Path
import tempfile
import bpy

root=Path(__file__).resolve().parents[1]
output=root/'logs/smoke.json'
obj=bpy.context.active_object
assert obj and obj.type=='MESH'
assert obj.data.uv_layers
mat=bpy.data.materials.new('OpenPainter Compatibility')
mat.use_nodes=True
node=mat.node_tree.nodes.get('Principled BSDF')
node.inputs['Roughness'].default_value=.37
obj.data.materials.clear()
obj.data.materials.append(mat)
mod=obj.modifiers.new('Compatibility Subdivision','SUBSURF')
mod.levels=1
bpy.context.scene.render.engine='BLENDER_EEVEE'
with tempfile.TemporaryDirectory(prefix='openpainter-native-') as directory:
    path=Path(directory)/'roundtrip.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    bpy.ops.wm.open_mainfile(filepath=str(path))
    obj=bpy.context.active_object
    assert len(obj.data.vertices)==8
    assert len(obj.data.uv_layers)==1
    assert obj.modifiers['Compatibility Subdivision'].levels==1
    assert abs(obj.active_material.node_tree.nodes['Principled BSDF'].inputs['Roughness'].default_value-.37)<1e-5
    assert bpy.context.scene.render.engine=='BLENDER_EEVEE'
output.write_text(json.dumps({'status':'PASS','binary':bpy.app.binary_path,'version':bpy.app.version_string,'checks':['mesh','UVs','material nodes','modifier','blend save/reopen','EEVEE engine selection']},indent=2))
print(output.read_text())
