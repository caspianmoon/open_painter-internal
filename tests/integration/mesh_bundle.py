"""Evaluated bundle staging: helpers on an extracted curve reach an ACTUAL bundle, and
scene.blend carries the live material's unsaved paint (in the set's packed channel images,
which the material's texture set node samples)."""
import json,sys,time
from pathlib import Path
from tempfile import TemporaryDirectory
import bpy
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
import open_painter
from open_painter.core import texture_set_manager as sets,layer_stack as stack,image_manager as images
from open_painter.evaluation import cache,shader_preview
from open_painter.export.mesh_bundle import export_bundle
from open_painter.geometry_effects import curves,manager,stitch,weld
from open_painter.painting import channel_paint
open_painter.register()
if bpy.app.timers.is_registered(cache.tick): bpy.app.timers.unregister(cache.tick)
scene=bpy.context.scene; cube=bpy.context.active_object
ts=sets.create(scene,'Bundle source',512); sets.assign(scene,ts,cube)
assert ts.preview_backend=='LIVE'
base=stack.add(ts,'FILL','Base'); stack.channel_for(base,'ROUGHNESS').value=.25
marks=stack.add(ts,'PAINT','Marks')
canvas=channel_paint.target(ts,marks,'BASE_COLOR')
red=np.zeros((512,512,4),np.float32); red[...,0]=1; red[...,3]=1
images.write(canvas,red,pack=False); channel_paint.mark_changed(ts,marks,'BASE_COLOR')   # a stroke: packed by Ctrl+S only
deadline=time.monotonic()+30
while True:
    shader_preview.ensure(scene,ts); cache.tick()
    diagnostic=shader_preview.diagnostics(scene,ts)
    assert not diagnostic.get('error'),diagnostic
    if shader_preview.current(scene,ts) and not diagnostic.get('pending_resources'): break
    assert time.monotonic()<deadline,diagnostic
    time.sleep(.01)
assert not canvas.packed_file
# Stitches and the seam bead hang off the curve, the bolts off the cube itself.
polylines=[(np.array([[1,-.5,1],[1,0,1],[1,.5,1]],np.float32),np.array([[1,0,0]]*3,np.float32))]
curve=curves.create_curve(scene,cube,polylines,'OP Stitch Path',manager.TAG)
stitches=stitch.create(scene,curve,'STITCH',spacing=.1,size=.02,seed=1,source=cube)
bead=weld.create(scene,curve,radius=.01)
assert stitches.parent==curve and bead.parent==curve and stitches['instance_count']>=3
bpy.context.view_layer.update()
with TemporaryDirectory(prefix='op-bundle-test-') as directory:
    root=Path(directory)
    manifest=export_bundle(bpy.context,[ts],root/'omit',geometry='OMIT')
    record=json.loads(manifest.read_text())
    assert record['generated_geometry']=='OMIT' and record['triangles']==12,record
    manifest=export_bundle(bpy.context,[ts],root/'actual',geometry='ACTUAL')
    record=json.loads(manifest.read_text())
    assert record['generated_geometry']=='ACTUAL' and record['triangles']>12,record
    base_color=next(o.image for o in ts.outputs if o.channel=='BASE_COLOR')
    with bpy.data.libraries.load(str(manifest.parent/'scene.blend'),link=True) as (data_from,data_to):
        names=list(data_from.objects); data_to.images=[base_color.name]
    assert sum(name.startswith('OP Effect Instance') for name in names)>=stitches['instance_count'],names
    assert all(any(name.startswith(helper.name) for name in names) for helper in (stitches,bead)),names   # staged copies carry .001
    bundled=data_to.images[0]
    assert bundled.library and bundled.packed_file,bundled
    np.testing.assert_allclose(images.pixels(bundled)[256,256][:3],(1,0,0),atol=1e-3)
    assert [obj.name for obj in scene.objects if obj.name.startswith('OP Effect Instance')]==[]   # staging left nothing behind
manager.remove(bead); manager.remove(stitches)
open_painter.unregister()
print('MESH_BUNDLE_PASS',flush=True)
