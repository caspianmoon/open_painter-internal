"""Engine channel mappings, evaluated bundles and atlas source preservation."""
import json,sys
from pathlib import Path
from tempfile import TemporaryDirectory
import bpy
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
import open_painter
from open_painter.core import texture_set_manager as sets,layer_stack as stack,image_manager as images
from open_painter.export.exporter import export
from open_painter.export.mesh_bundle import export_bundle
from open_painter.export.atlas import build
from open_painter.geometry.texel_density import measure
open_painter.register()
scene=bpy.context.scene; cube=bpy.context.active_object
ts=sets.create(scene,'Export source',512); sets.assign(scene,ts,cube)
layer=stack.add(ts,'FILL'); stack.channel_for(layer,'ROUGHNESS').value=.25; stack.channel_for(layer,'METALLIC').value=.75
ao=stack.channel_for(layer,'AO'); ao.enabled=True; ao.value=.5
opacity=stack.channel_for(layer,'OPACITY'); opacity.enabled=True; opacity.value=0
original_uv=[tuple(item.uv) for item in cube.data.uv_layers.active.data]
original_vertices=[tuple(v.co) for v in cube.data.vertices]
assert measure(ts)['world_area']==24
# Measure works on the snapshot arrays alone: the per-triangle RNA list stalled the
# operator for tens of seconds on subdivided assets. It must still agree with a
# per-triangle reference and count a triangle straddling both UV bounds once.
from unittest.mock import patch as _patch
from open_painter.geometry import snapshots as _snapshots
from open_painter.geometry.uv_rasterizer import tiles as _tiles
bpy.ops.mesh.primitive_plane_add(); plane=bpy.context.active_object
for item in plane.data.uv_layers.active.data: item.uv=(item.uv[0]*2-.5,item.uv[1]*2-.5)
straddling=sets.create(scene,'Straddling UVs',512); sets.assign(scene,straddling,plane)
ts=next(item for item in scene.open_painter.texture_sets if item.id==ts.id)
def _no_rna(self,key): raise AssertionError(f'measure requested the RNA {key!r} list')
for candidate in (ts,straddling):
    with _snapshots.targets(candidate) as entries:
        reference=np.zeros((128,128),np.bool_); world=uv_sum=0.; crossing=0
        for entry in entries:
            for xyz,uv in zip(entry['positions'][entry['tri_vertices']],entry['uv'][entry['tri_loops']].astype(np.float64)):
                world+=np.linalg.norm(np.cross(xyz[1]-xyz[0],xyz[2]-xyz[0]))*.5
                a,b=uv[1]-uv[0],uv[2]-uv[0]; uv_sum+=abs(a[0]*b[1]-a[1]*b[0])*.5
                crossing+=bool(np.any(uv<0) or np.any(uv>1))
                for region,inside,*_ in _tiles(uv,128): reference[region]|=inside
    with _patch.object(_snapshots.Target,'__missing__',_no_rna):
        result=measure(candidate)
    assert abs(result['world_area']-world)<1e-6 and abs(result['uv_area_sum']-uv_sum)<1e-6,(candidate.name,result)
    assert result['outside_triangles']==crossing and result['sampled_occupancy']==reference.mean(),(candidate.name,result)
assert measure(straddling)['outside_triangles']==2
with TemporaryDirectory(prefix='op-production-') as directory:
    root=Path(directory)
    for profile,label,expected in [('GODOT','ORM',(.5,.25,.75,1)),('GLTF','OcclusionRoughnessMetallic',(.5,.25,.75,1)),
                                    ('UNITY_URP','MetallicGlossMap',(.75,0,0,.75)),('UNITY_HDRP','MaskMap',(.75,.5,1,.75))]:
        ts.export.preset=profile; ts.export.directory=str(root/profile)
        files=export(bpy.context,ts,ts.export)
        path=next(path for path in files if path.stem.endswith('_'+label))
        image=bpy.data.images.load(str(path)); image.colorspace_settings.name='Non-Color'
        np.testing.assert_allclose(images.pixels(image)[0,0],expected,atol=1/255+.00001)
        bpy.data.images.remove(image)
    # A Custom tuple of only Zero/One constants loads no channel: the file is still
    # written at the export resolution (it raised a bare StopIteration before 0.9.7).
    ts.export.preset='CUSTOM'; ts.export.directory=str(root/'constants'); ts.export.resolution='1024'
    ts.export.custom_r='ONE'; ts.export.custom_g='ZERO'; ts.export.custom_b='ONE'; ts.export.custom_a='ONE'
    files=export(bpy.context,ts,ts.export)
    image=bpy.data.images.load(str(files[0])); image.colorspace_settings.name='Non-Color'
    assert tuple(image.size)==(1024,1024),tuple(image.size)
    np.testing.assert_allclose(images.pixels(image)[0,0],(1,0,1,1),atol=1/255+.00001)
    bpy.data.images.remove(image); ts.export.resolution='SET'; ts.export.preset='UNITY_HDRP'
    manifest=export_bundle(bpy.context,[ts],root/'bundle',geometry='OMIT')
    record=json.loads(manifest.read_text())
    assert record['generated_geometry']=='OMIT' and record['triangles']==12
    assert (manifest.parent/'scene.blend').exists()
    assert [tuple(v.co) for v in cube.data.vertices]==original_vertices
    identifier=ts.id
    atlas,copies=build(bpy.context,[ts],512,8)
    source=next(item for item in scene.open_painter.texture_sets if item.id==identifier)
    assert [tuple(item.uv) for item in cube.data.uv_layers.active.data]==original_uv
    assert copies[0].data!=cube.data and copies[0].data.uv_layers.active.name=='Open Painter Atlas UV'
    actual=images.pixels(images.output(atlas,'ROUGHNESS').image)
    np.testing.assert_allclose(actual[10:-10,10:-10,0],.25,atol=1e-6)
    assert all(stack.channel_for(atlas.layers[0],key).image for key in ('BASE_COLOR','ROUGHNESS','NORMAL','HEIGHT','AO','OPACITY','METALLIC','EMISSION'))
    from unittest.mock import patch
    before=(len(bpy.data.images),len(bpy.data.materials),len(bpy.data.objects),len(scene.open_painter.texture_sets))
    with patch('open_painter.export.atlas.sample_uv',side_effect=RuntimeError('Injected atlas sampling failure')):
        try:
            build(bpy.context,[source],512,8)
            raise AssertionError('Failed atlas was accepted')
        except RuntimeError as exc: assert 'Injected' in str(exc)
    assert before==(len(bpy.data.images),len(bpy.data.materials),len(bpy.data.objects),len(scene.open_painter.texture_sets))
# A source larger than its cell is box-filtered, not point-sampled: one-texel stripes
# must average out instead of aliasing into moiré.
striped=sets.create(scene,'Striped source',1024); striped_id=striped.id
bpy.ops.mesh.primitive_cube_add(); striped_cube=bpy.context.active_object; sets.assign(scene,striped,striped_cube)
stripes=np.zeros((1024,1024,4),np.float32); stripes[...,3]=1; stripes[:,::2,:3]=1
stripe_image=images.create('Stripes',1024,color_data=False); images.write(stripe_image,stripes)
painted=stack.add(striped,'PAINT'); prop=stack.channel_for(painted,'ROUGHNESS'); prop.image=stripe_image; prop.enabled=True
stack.invalidate(striped)
atlas,copies=build(bpy.context,[striped],512,8)
assert copies[0].data.uv_layers.active.name=='Open Painter Atlas UV' and len(copies)==1
actual=images.pixels(images.output(atlas,'ROUGHNESS').image)[16:-16,16:-16,0]
assert abs(actual.mean()-.5)<.01 and actual.std()<.01,(actual.mean(),actual.std())
# Exporting above the authored resolution clamps at the canvas border: a repeating
# fetch wrapped the opposite edge into the outermost exported texels (0.25 at 2x).
striped=next(item for item in scene.open_painter.texture_sets if item.id==striped_id)
edge=np.zeros((1024,1024,4),np.float32); edge[...,3]=1; edge[0,:,:3]=1
images.write(stripe_image,edge); stack.invalidate(striped)
striped.export.preset='CUSTOM'; striped.export.resolution='2048'
striped.export.custom_r='ROUGHNESS'; striped.export.custom_g='ZERO'; striped.export.custom_b='ZERO'; striped.export.custom_a='ONE'
with TemporaryDirectory(prefix='op-upscale-') as directory:
    striped.export.directory=directory
    files=export(bpy.context,striped,striped.export)
    image=bpy.data.images.load(str(files[0])); image.colorspace_settings.name='Non-Color'
    assert tuple(image.size)==(2048,2048),tuple(image.size)
    exported=images.pixels(image)[...,0]; bpy.data.images.remove(image)
# Since 0.13 only texels the geometry covers carry data (the rest is gutter, filled from
# the nearest covered texel): the clamp is checked where the cube's islands reach the rows.
from open_painter.export import padding as export_padding
covered=export_padding.ownership(striped,2048).covered
assert covered[0].any() and covered[1].any()
np.testing.assert_allclose(exported[0][covered[0]],1,atol=1/255+.00001)
np.testing.assert_allclose(exported[1][covered[1]],.75,atol=1/255+.00001)
assert (exported[-2:][covered[-2:]]==0).all(),(exported[-1].max(),exported[-2].max())
striped.export.resolution='SET'
# A multi-material prop whose slots belong to two sets is staged as a single copy
# with every owned slot remapped into its own cell and assigned to the atlas.
bpy.ops.mesh.primitive_cube_add(); prop_object=bpy.context.active_object
for name in ('Body','Glass'): prop_object.data.materials.append(bpy.data.materials.new(name))
for polygon in prop_object.data.polygons[:3]: polygon.material_index=1
body=sets.create(scene,'Body',512); glass=sets.create(scene,'Glass',512)
body_id,glass_id=body.id,glass.id
sets.assign(scene,body,prop_object,0); glass=next(item for item in scene.open_painter.texture_sets if item.id==glass_id)
sets.assign(scene,glass,prop_object,1)
body=next(item for item in scene.open_painter.texture_sets if item.id==body_id)
staged_before=len(bpy.data.objects)
atlas,copies=build(bpy.context,[body,glass],512,8)
assert len(copies)==1 and len(bpy.data.objects)==staged_before+1,[obj.name for obj in copies]
staged=copies[0]; uv=np.empty((len(staged.data.loops),2),np.float32); staged.data.uv_layers.active.data.foreach_get('uv',uv.ravel())
for polygon in staged.data.polygons:
    corners=uv[list(polygon.loop_indices)]
    low=0 if polygon.material_index==0 else 256
    assert (corners[:,0]*512>=low+8-1e-3).all() and (corners[:,0]*512<=low+248+1e-3).all(),(polygon.material_index,corners)
    assert (corners[:,1]*512>=8-1e-3).all() and (corners[:,1]*512<=248+1e-3).all(),(polygon.material_index,corners)
assert {(ref.object,ref.slot_index) for ref in atlas.assignments}=={(staged,0),(staged,1)}
assert all(slot.material==atlas.material for slot in staged.material_slots)
open_painter.unregister()
print('PRODUCTION_EXPORT_PASS',flush=True)
