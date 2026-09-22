"""Spatial regions on a single connected bent member, using production APIs.

Run in background for geometry, authoring, stale-state and persistence checks.
Run visibly for a decoded Eevee viewport image of the exact queried member.
"""
import base64
import json
import math
from pathlib import Path
import sys
import tempfile
import traceback

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.ai import viewport
from open_painter.authoring import document, regions, transactions
from open_painter.authoring.schema import AuthoringError
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import cache, mesh_masks


def fixture():
    scene = bpy.data.scenes.new('Connected geometric region fixture')
    bpy.context.window.scene = scene
    scene.render.engine = 'BLENDER_EEVEE'
    # A voxel-union L shell: shared vertices and edges throughout, no component
    # boundary between its vertical and horizontal members. No semantic tags.
    cells = {(0, 0), (0, 1), (0, 2), (1, 2), (2, 2)}
    vertices, faces, lookup, foreign = [], [], {}, None
    def face(coords):
        result = []
        for point in coords:
            if point not in lookup:
                lookup[point] = len(vertices)
                vertices.append(point)
            result.append(lookup[point])
        faces.append(result)
    for x, z in sorted(cells):
        face(((x,-.2,z),(x+1,-.2,z),(x+1,-.2,z+1),(x,-.2,z+1)))
        if (x,z) == (1,2): foreign = len(faces)-1
        face(((x,.2,z+1),(x+1,.2,z+1),(x+1,.2,z),(x,.2,z)))
        if (x-1,z) not in cells:
            face(((x,-.2,z+1),(x,.2,z+1),(x,.2,z),(x,-.2,z)))
        if (x+1,z) not in cells:
            face(((x+1,-.2,z),(x+1,.2,z),(x+1,.2,z+1),(x+1,-.2,z+1)))
        if (x,z-1) not in cells:
            face(((x,-.2,z),(x,.2,z),(x+1,.2,z),(x+1,-.2,z)))
        if (x,z+1) not in cells:
            face(((x,-.2,z+1),(x+1,-.2,z+1),(x+1,.2,z+1),(x,.2,z+1)))
    mesh = bpy.data.meshes.new('One connected bent member')
    mesh.from_pydata(vertices, [], faces)
    uv = mesh.uv_layers.new(name='RenderUV')
    for polygon in mesh.polygons:
        u, v = (polygon.index % 6) / 6, (polygon.index // 6) / 6
        for loop, xy in zip(polygon.loop_indices, ((u+.01,v+.01),(u+.15,v+.01),(u+.15,v+.15),(u+.01,v+.15))):
            uv.data[loop].uv = xy
        polygon.material_index = int(polygon.index == foreign)
    obj = bpy.data.objects.new('Bent surface', mesh)
    scene.collection.objects.link(obj)
    for name in ('Assigned', 'Foreign slot'):
        mesh.materials.append(bpy.data.materials.new(name))
    ts = sets.create(scene, 'Assigned finish', 512)
    sets.assign(scene, ts, obj, 0)
    bpy.context.view_layer.update()
    registry = {}
    return scene, obj, ts, regions.bindings({'object:0':obj}, registry), registry, foreign


def snapshot(obj, ts):
    return (document.canonical(document.snapshot(ts)), obj.data.as_pointer(),
            tuple(tuple(vertex.co) for vertex in obj.data.vertices),
            tuple((tuple(face.vertices),face.material_index,face.select) for face in obj.data.polygons),
            tuple(tuple(item.uv) for item in obj.data.uv_layers.active.data),
            tuple(slot.material.as_pointer() for slot in obj.material_slots),
            obj.select_get(), obj.hide_get(), tuple(tuple(row) for row in obj.matrix_world),
            tuple((kind,tuple(item.as_pointer() for item in getattr(bpy.data,kind)))
                  for kind in ('images','materials','objects','meshes','collections')))


def spec(**extra):
    return dict(dict(object_id='object:0',slot_index=0,space='WORLD',bounds_min=[0,-.3,0],
                     bounds_max=[1,.3,1.99],selection='CENTROID'), **extra)


def reject(fn, text):
    try: fn()
    except AuthoringError as exc: assert text in str(exc), str(exc)
    else: raise AssertionError('Expected rejection: '+text)


def checks():
    scene,obj,ts,objects,registry,foreign = fixture()
    initial = snapshot(obj,ts)
    catalogue = regions.capture(ts,objects)
    assert not any(item['kind']=='CONNECTED_COMPONENT' for item in catalogue), 'fixture is disconnected'
    record = regions.query(ts,objects,spec(),registry)
    assert record['kind']=='SPATIAL_QUERY' and record['face_count'] > 1
    assert record['bounds_world']['maximum']==[1.0, .20000000298023224, 2.0]
    assert record['id']==regions.query(ts,objects,spec(),registry)['id']
    assert record in regions.capture(ts,objects)
    selected=regions.selector(ts,record['id'],objects)
    faces=json.loads(selected['region_faces'])
    expected=[p.index for p in obj.data.polygons if p.center.z < 1.99 and p.material_index==0]
    assert faces==expected and foreign not in faces
    assert snapshot(obj,ts)==initial, 'read-only query changed artist data'
    reject(lambda: regions.selector(ts,record['id'],dict(objects)), 'Unknown or stale')
    reject(lambda: regions.query(ts,objects,spec(object_id='object:foreign'),registry), 'outside')
    reject(lambda: regions.query(ts,objects,spec(slot_index=1),registry), 'outside')
    reject(lambda: regions.query(ts,objects,spec(bounds_max=[-1,1,1]),registry), 'minimum bounds')
    reject(lambda: regions.query(ts,objects,spec(bounds_min=[math.nan,0,0]),registry), 'finite')
    reject(lambda: regions.query(ts,objects,spec(min_dot=.5),registry), 'require a normal direction')
    reject(lambda: regions.query(ts,objects,spec(normal_direction=[0,0,0]),registry), 'nonzero')
    reject(lambda: regions.query(ts,objects,spec(bounds_min=[20,20,20],bounds_max=[21,21,21]),registry), 'no assigned faces')
    # SAT must reject a triangle bbox corner with no actual triangle inside.
    triangle=np.array([[[0.,0.,0.],[1.,0.,0.],[0.,1.,0.]]])
    assert not regions._triangle_box(triangle,np.array([.8,.8,-.1]),np.array([.9,.9,.1]))[0]
    assert regions._triangle_box(triangle,np.array([.45,.45,0.]),np.array([.55,.55,0.]))[0]
    assert regions._triangle_box(triangle,np.array([1.,0.,0.]),np.array([1.,0.,0.]))[0], 'closed boundary excluded'
    front = regions.query(ts,objects,spec(normal_direction=[0,-1,0],min_dot=.99),registry)
    assert front['face_count']==2
    front_faces=json.loads(regions.selector(ts,front['id'],objects)['region_faces'])
    assert all(obj.data.polygons[i].normal.y < -.99 for i in front_faces)
    intersect=regions.query(ts,objects,spec(bounds_min=[.1,-.2,.1],bounds_max=[.2,-.2,.2],selection='INTERSECTION'),registry)
    assert intersect['face_count']==1 and 'whole polygon' in intersect['uncertainty']
    reject(lambda: regions.query(ts,objects,spec(bounds_min=[.1,-.2,.1],bounds_max=[.2,-.2,.2]),registry), 'no assigned faces')
    all_assigned=regions.query(ts,objects,spec(bounds_max=[3,.3,3]),registry)
    assert all_assigned['face_count']==len(obj.data.polygons)-1
    assert foreign not in json.loads(regions.selector(ts,all_assigned['id'],objects)['region_faces'])
    normalized=regions.query(ts,objects,spec(space='ASSIGNMENT_NORMALIZED',bounds_min=[0,0,0],bounds_max=[1/3,1,.663]),registry)
    assert json.loads(regions.selector(ts,normalized['id'],objects)['region_faces'])==faces
    # Exact isolated preview scope contains those polygons, not its connected
    # parent member, and preserves all mesh/assignment/resource data.
    with viewport.isolated(bpy.context,{obj},ts.assignments,region_faces={obj:set(faces)},
                           region_topologies={obj:selected['region_topology']}) as isolated:
        assert len(isolated)==1 and len(isolated[0].data.polygons)==len(faces)
        assert max(v.co.z for v in isolated[0].data.vertices)==2
        assert max(v.co.x for v in isolated[0].data.vertices)==1
    assert snapshot(obj,ts)==initial
    # Queries are rejected when geometry changes without changing topology.
    vertex=obj.data.vertices[0]; vertex.co.z += .1; bpy.context.view_layer.update()
    reject(lambda: regions.selector(ts,record['id'],objects), 'geometry, transform or UVs changed')
    vertex.co.z -= .1; bpy.context.view_layer.update()
    record=regions.query(ts,objects,spec(),registry)
    uv=obj.data.uv_layers.active.data[0]; original=uv.uv.copy(); uv.uv.x += .01; bpy.context.view_layer.update()
    reject(lambda: regions.selector(ts,record['id'],objects), 'geometry, transform or UVs changed')
    uv.uv=original; bpy.context.view_layer.update()
    record=regions.query(ts,objects,spec(),registry)
    obj.location=(4,2,1); obj.rotation_euler.z=math.pi/2; obj.scale=(2,1,.5); bpy.context.view_layer.update()
    reject(lambda: regions.selector(ts,record['id'],objects), 'geometry, transform or UVs changed')
    local=regions.query(ts,objects,spec(space='OBJECT',normal_direction=[0,-1,0],min_dot=.99),registry)
    assert json.loads(regions.selector(ts,local['id'],objects)['region_faces'])==front_faces
    # Object -Y becomes world +X after this rotation and nonuniform scale.
    world=regions.query(ts,objects,spec(bounds_min=[0,0,0],bounds_max=[10,10,10],normal_direction=[1,0,0],min_dot=.99),registry)
    assert world['face_count']==4  # five front polygons minus foreign material.
    obj.location=(0,0,0);obj.rotation_euler=(0,0,0);obj.scale=(1,1,1);bpy.context.view_layer.update()
    record=regions.query(ts,objects,spec(),registry)
    before=document.canonical(document.snapshot(ts))
    proposal=dict(version=1,summary='Editable geometric member region',commands=[
        dict(op='add_layer',id='new:member',name='Member finish',kind='FILL'),
        dict(op='add_region_mask',id='new:region',layer_id='new:member',name='Frozen geometric member',region_id=record['id'])])
    receipt=transactions.apply(ts,proposal,operation_id='spatial-region',expected_digest=document.digest(ts),objects=objects)
    layer=next(layer for layer in ts.layers if layer.id==receipt['aliases']['new:member'])
    mask=layer.masks[0]
    assert mask.source=='MESH' and mask.mesh.kind=='REGION'
    assert json.loads(mask.mesh.region_faces)==faces and mask.mesh.region_provenance.startswith('Frozen spatial query: ')
    raster=mesh_masks.raster(ts,mask,512).copy()
    assert raster.max()==1 and raster.min()==0
    assert any(item['id']==record['id'] and item['kind']=='SAVED_REGION' for item in regions.capture(ts,dict(objects)))
    transactions.undo(ts)
    assert document.canonical(document.snapshot(ts))==before
    transactions.apply(ts,proposal,operation_id='spatial-region-save',expected_digest=document.digest(ts),objects=objects)
    path=Path(tempfile.gettempdir())/'open-painter-spatial-region-persistence.blend'
    identifier=ts.id
    bpy.ops.wm.save_as_mainfile(filepath=str(path)); bpy.ops.wm.open_mainfile(filepath=str(path))
    ts=next(ts for ts in bpy.context.scene.open_painter.texture_sets if ts.id==identifier)
    mask=ts.layers[-1].masks[0]; obj=mask.mesh.object
    saved=regions.capture(ts,{'object:new':obj})
    assert any(item['id']==record['id'] and item['kind']=='SAVED_REGION' for item in saved)
    assert regions.selector(ts,record['id'],{'object:new':obj})['region_faces']==mask.mesh.region_faces
    np.testing.assert_array_equal(mesh_masks.raster(ts,mask,512),raster)
    print('DIRECTOR_SPATIAL_QUERY_CONNECTED_GEOMETRY_PERSISTENCE_OK',flush=True)


def pixels():
    scene,obj,ts,objects,registry,foreign=fixture()
    ts.preview_backend='CPU'
    material=ts.material; material.use_nodes=True; material.node_tree.nodes.clear()
    out=material.node_tree.nodes.new('ShaderNodeOutputMaterial')
    emission=material.node_tree.nodes.new('ShaderNodeEmission'); emission.inputs['Color'].default_value=(0,1,0,1)
    material.node_tree.links.new(emission.outputs[0],out.inputs['Surface'])
    camera=bpy.data.objects.new('Read-only region camera',bpy.data.cameras.new('Region camera'))
    scene.collection.objects.link(camera);scene.camera=camera
    camera.location=(1.5,-8,1.5);camera.rotation_euler=(math.pi/2,0,0)
    area=next(area for area in bpy.context.screen.areas if area.type=='VIEW_3D')
    space=area.spaces.active;space.shading.type='MATERIAL';space.region_3d.view_perspective='CAMERA'
    bpy.context.view_layer.update()
    record=regions.query(ts,objects,spec(),registry)
    selected=regions.selector(ts,record['id'],objects)
    region=next(r for r in area.regions if r.type=='WINDOW')
    before=snapshot(obj,ts);camera_before=camera.matrix_world.copy()
    with bpy.context.temp_override(area=area,region=region):
        space.region_3d.update();view_before=space.region_3d.view_matrix.copy()
        url=viewport.capture(bpy.context,ts,max_side=512,isolate=True,frame_target=True,
            region_faces={obj:set(json.loads(selected['region_faces']))},region_topologies={obj:selected['region_topology']})
    path=Path(tempfile.gettempdir())/'open-painter-spatial-query-exact-member.png'
    path.write_bytes(base64.b64decode(url.split(',',1)[1]))
    image=bpy.data.images.load(str(path));width,height=image.size
    pixels=np.asarray(image.pixels[:]).reshape(height,width,4)
    green=(pixels[:,:,1]>.3)&(pixels[:,:,1]>pixels[:,:,0]*1.3)
    rows,columns=np.nonzero(green)
    pixel_width=columns.max()-columns.min()+1;pixel_height=rows.max()-rows.min()+1
    assert len(rows)>width*height*.04
    assert .46<pixel_width/pixel_height<.54, ('whole connected L leaked into preview',pixel_width,pixel_height)
    assert green[rows.min():rows.max()+1,columns.min():columns.max()+1].mean()>.98, 'isolated member is not solid rectangle'
    bpy.data.images.remove(image)
    assert snapshot(obj,ts)==before and camera.matrix_world==camera_before and space.region_3d.view_matrix==view_before
    print('DIRECTOR_SPATIAL_QUERY_CAMERA_PIXELS_OK',width,height,len(rows),str(path),flush=True)


open_painter.register()
if bpy.app.timers.is_registered(cache.tick): bpy.app.timers.unregister(cache.tick)
if bpy.app.background:
    checks()
else:
    def run():
        try: pixels()
        except BaseException:
            traceback.print_exc()
            import os
            os._exit(1)
        bpy.ops.wm.quit_blender()
    bpy.app.timers.register(run,first_interval=2.)
