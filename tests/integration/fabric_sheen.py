"""Fabric sheen persists and reaches CPU/live surfaces without painted highlights."""
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid5
from zipfile import ZipFile

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'src'))
import open_painter
from open_painter.authoring import document
from open_painter.constants import OWNER
from open_painter.core import material_manager
from open_painter.core import texture_set_manager as sets
from open_painter.smart_materials import controls, loader, serializer

open_painter.register()
scene=bpy.context.scene
scene.open_painter.auto_evaluate=False
uid=UUID('567c9b1a-1ff5-46a6-b882-49113556cdb1')
identity=lambda label:uuid5(uid,label).hex
surface=dict(ior=1.5,transmission_weight=0,subsurface_weight=.04,subsurface_scale=.0005,sheen_weight=.82,sheen_roughness=.36)
data=dict(format_version=4,name='PBR optical test',surface=surface,
 module=dict(id=str(uid),version=1,root=identity('root'),name='PBR optical test'),layers=[
 dict(id=identity('root'),type='FOLDER',name='PBR optical test',group_mode='ISOLATED',channels=[],masks=[],
 master_controls=json.dumps(dict(version=1,required_maps=[],controls=[dict(label='Sheen',scope='set',field='sheen_weight')]))),
 dict(id=identity('body'),type='FILL',name='Polymer body',parent_id=identity('root'),masks=[],channels=[
 dict(channel='BASE_COLOR',enabled=True,color=[.95,.98,1,1]),dict(channel='ROUGHNESS',enabled=True,value=.1),
 dict(channel='METALLIC',enabled=True,value=0),dict(channel='OPACITY',enabled=True,value=1)])])

def verify(ts, expected):
 material_manager.repair(ts)
 bsdf=next(n for n in ts.material.node_tree.nodes if n.get(OWNER) and n.get('role')=='Surface')
 for field,socket in [('ior','IOR'),('transmission_weight','Transmission Weight'),('subsurface_weight','Subsurface Weight'),('subsurface_scale','Subsurface Scale'),('sheen_weight','Sheen Weight'),('sheen_roughness','Sheen Roughness')]:
  assert abs(getattr(ts,field)-expected[field])<1e-6,(field,getattr(ts,field),expected[field])
  assert abs(bsdf.inputs[socket].default_value-expected[field])<1e-6
 assert bsdf.inputs['Alpha'].is_linked

with TemporaryDirectory() as folder:
 folder=Path(folder)
 source=folder/'polymer.openpaintmaterial'
 source.write_text(json.dumps(data))
 ts=sets.create(scene,'PBR polymer',512)
 sets.assign(scene,ts,bpy.context.object)
 imported=loader.apply(ts,source)
 verify(ts,surface)
 binding=controls.bindings(ts,imported[0])[0]
 assert binding.ok and binding.owner==ts
 ts.sheen_weight=.61
 expected=dict(surface,sheen_weight=.61)
 verify(ts,expected)
 before=document.snapshot(ts)
 assert all(k in before['surface'] for k in surface)
 ts.ior=1.2
 document.apply(ts,before)
 verify(ts,expected)
 # Save/import the actual packed material and duplicate the texture set.
 saved=serializer.save(ts,folder/'saved.openpaintmaterial')
 with ZipFile(saved) as z:
  recorded=json.loads(z.read('material.json'))
  assert all(abs(recorded['surface'][k]-v)<1e-6 for k,v in expected.items())
 target=sets.create(scene,'Imported PBR',512)
 loader.apply(target,saved)
 verify(target,expected)
 duplicate=sets.duplicate(scene,1)
 verify(duplicate,expected)
 duplicate.preview_backend='LIVE'
 verify(duplicate,expected)
 # Older materials have no optical fields: restore their opaque interpretation.
 legacy=dict(data,surface={'coat_weight':.2})
 legacy_path=folder/'legacy.openpaintmaterial';legacy_path.write_text(json.dumps(legacy))
 loader.apply(target,legacy_path)
 verify(target,dict(ior=1.5,transmission_weight=0,subsurface_weight=0,subsurface_scale=.001,sheen_weight=0,sheen_roughness=.5))
 # Invalid values must reject before modifying layers or a preexisting surface.
 count=len(target.layers)
 for field,value in [('sheen_weight',1.1),('sheen_roughness',-.1),('sheen_weight',True),('sheen_roughness',float('nan')),('ior',.9),('ior',4.1),('transmission_weight',1.1),('subsurface_weight',-.1),('subsurface_scale',11),('ior',True),('ior',float('nan'))]:
  invalid=dict(data,surface={field:value})
  path=folder/'invalid.openpaintmaterial';path.write_text(json.dumps(invalid))
  try:loader.apply(target,path)
  except ValueError:pass
  else:raise AssertionError(('invalid surface accepted',field,value))
  assert len(target.layers)==count
  verify(target,dict(ior=1.5,transmission_weight=0,subsurface_weight=0,subsurface_scale=.001,sheen_weight=0,sheen_roughness=.5))
 blend=folder/'polymer.blend'
 bpy.ops.wm.save_as_mainfile(filepath=str(blend))
 bpy.ops.wm.open_mainfile(filepath=str(blend))
 restored=next(t for t in bpy.context.scene.open_painter.texture_sets if t.name=='PBR polymer')
 verify(restored,expected)
print('FABRIC_SHEEN PASS: shader, controls, save/import, clone, authoring, reopen, legacy reset, atomic validation',flush=True)
