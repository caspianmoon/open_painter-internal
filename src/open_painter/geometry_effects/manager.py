"""Owned, bounded Geometry Nodes instances on separate source-parented helpers.

Placement is deterministic for unchanged evaluated topology. Re-evaluated topology
may reseed triangle selection. Analysis uses source assignments, never helpers.
"""
import bpy
import numpy as np
from mathutils import Vector
from ..geometry.snapshots import targets
from ..evaluation.blend_modes import sample_uv
from ..core.image_manager import pixels
from ..utils.ids import new_id
from . import limits

TAG = 'open_painter_geometry_effect'


def scatter(ts, obj, mask, density, seed, replacing=None):
    if density < 0:
        raise ValueError('Density cannot be negative')
    values = pixels(mask)[...,0]
    with targets(ts) as entries:
        target = next((entry for entry in entries if entry['object']==obj),None)
        if target is None:
            raise ValueError('Effect source must be assigned to the texture set')
        triangles = target['triangles']
        indices = np.asarray([tuple(t.vertices) for t in triangles])
        coords = target['positions'][indices]
        areas = np.linalg.norm(np.cross(coords[:,1]-coords[:,0],coords[:,2]-coords[:,0]),axis=1)*.5
        total = areas.sum()
        count = int(np.ceil(total*density))
        limits.check(ts.id_data,count,count*12,replacing=replacing)
        if not count or total <= 1e-12:
            return np.empty((0,3),np.float32),np.empty((0,3),np.float32)
        rng = np.random.default_rng(seed)
        selected = rng.choice(len(triangles),count,p=areas/total)
        root = np.sqrt(rng.random(count)); second = rng.random(count)
        weights = np.stack((1-root,root*(1-second),root*second),axis=1)
        loops = np.asarray([tuple(triangles[i].loops) for i in selected])
        uv = np.sum(target['uv'][loops]*weights[...,None],axis=1)
        probability = sample_uv(values[...,None],uv[:,0],uv[:,1])[...,0]
        keep = rng.random(count) < np.clip(probability,0,1)
        positions = np.sum(coords[selected]*weights[...,None],axis=1)[keep]
        normal = np.sum(target['world_normals'][loops]*weights[...,None],axis=1)[keep]
        inverse = np.asarray(obj.matrix_world.inverted_safe())
        local_positions = positions @ inverse[:3,:3].T + inverse[:3,3]
        local_normals = normal @ np.asarray(obj.matrix_world)[:3,:3]
        local_normals /= np.maximum(np.linalg.norm(local_normals,axis=1,keepdims=True),1e-8)
        return local_positions,local_normals


def create(ts, obj, mask, kind='RUST', density=30, size=.02, seed=1, lift=0, material=None, replacing=None):
    if kind not in {'RUST','MOSS','SNOW','SPLINTERS','PEEL'} or size <= 0:
        raise ValueError('Choose a supported geometry effect and a positive instance size')
    positions,normals = scatter(ts,obj,mask,density,seed,replacing)
    if not len(positions):
        return None
    limits.check(ts.id_data,len(positions),len(positions)*12,replacing=replacing)
    from . import rust_flakes,moss
    if kind in {'RUST','MOSS'}:
        vertices,faces = (rust_flakes if kind=='RUST' else moss).mesh_data()
    else:
        from .advanced import mesh_data
        vertices,faces=mesh_data(kind)
    identifier=new_id()
    created=[]
    try:
        proto_mesh=bpy.data.meshes.new('OP Effect Prototype'); created.append(proto_mesh)
        proto_mesh.from_pydata(vertices,[],faces)
        if material or ts.material:
            proto_mesh.materials.append(material or ts.material)
        prototype=bpy.data.objects.new('OP '+kind+' Prototype',proto_mesh); created.append(prototype)
        # A data-only object can supply Object Info without polluting the scene.
        prototype.use_fake_user=True
        point_mesh=bpy.data.meshes.new('OP Effect Points'); created.append(point_mesh)
        point_mesh.from_pydata((positions+normals*lift).tolist(),[],[])
        rotations=point_mesh.attributes.new('op_rotation','FLOAT_VECTOR','POINT')
        rotation_values=np.array([Vector(normal).to_track_quat('Z','Y').to_euler()[:] for normal in normals],np.float32)
        rotations.data.foreach_set('vector',rotation_values.ravel())
        helper=bpy.data.objects.new('OP '+kind+' · '+obj.name,point_mesh); created.append(helper)
        ts.id_data.collection.objects.link(helper)
        helper.parent=obj
        group=bpy.data.node_groups.new('OP '+kind+' Instances','GeometryNodeTree'); created.append(group)
        group.interface.new_socket(name='Geometry',in_out='INPUT',socket_type='NodeSocketGeometry')
        group.interface.new_socket(name='Geometry',in_out='OUTPUT',socket_type='NodeSocketGeometry')
        nodes,links=group.nodes,group.links
        input_node=nodes.new('NodeGroupInput'); output_node=nodes.new('NodeGroupOutput')
        info=nodes.new('GeometryNodeObjectInfo'); info.inputs['Object'].default_value=prototype
        info.transform_space='ORIGINAL'
        instance=nodes.new('GeometryNodeInstanceOnPoints')
        orientation=nodes.new('GeometryNodeInputNamedAttribute'); orientation.data_type='FLOAT_VECTOR'
        orientation.inputs['Name'].default_value='op_rotation'
        instance.inputs['Scale'].default_value=(size,size,size)
        links.new(input_node.outputs['Geometry'],instance.inputs['Points'])
        links.new(info.outputs['Geometry'],instance.inputs['Instance'])
        links.new(orientation.outputs['Attribute'],instance.inputs['Rotation'])
        links.new(instance.outputs['Instances'],output_node.inputs['Geometry'])
        helper.modifiers.new('Open Painter Instances','NODES').node_group=group
        for item in created:
            item[TAG]='INSTANCE_HELPER' if item==helper else 'OWNED_RESOURCE'
            item['effect_owner']=identifier
        helper['instance_count']=len(positions)
        helper['estimated_triangles']=len(positions)*12
        helper['source_texture_set']=ts.id
        helper['kind']=kind; helper['density']=density; helper['size']=size; helper['seed']=seed; helper['lift']=lift
        helper['mask_image']=mask.name
        helper['frozen']=False
        return helper
    except Exception:
        for item in reversed(created):
            if isinstance(item,bpy.types.Object): bpy.data.objects.remove(item,do_unlink=True)
            elif isinstance(item,bpy.types.Mesh): bpy.data.meshes.remove(item)
            elif isinstance(item,bpy.types.NodeTree): bpy.data.node_groups.remove(item)
        raise


def remove(helper):
    if helper.get(TAG) != 'INSTANCE_HELPER':
        raise ValueError('Select an Open Painter effect helper')
    owner=helper.get('effect_owner')
    bpy.data.objects.remove(helper,do_unlink=True)
    shared = any(obj.get('effect_owner')==owner and obj.get(TAG)=='INSTANCE_HELPER' for obj in bpy.data.objects)
    if shared:
        return
    # Remove the managed node group before the prototype it references.
    for group in list(bpy.data.node_groups):
        if group.get('effect_owner')==owner and group.get(TAG) and group.users==0:
            bpy.data.node_groups.remove(group)
    for obj in list(bpy.data.objects):
        if obj.get('effect_owner')==owner and obj.get(TAG)=='OWNED_RESOURCE':
            obj.use_fake_user=False
            if obj.users==0:
                bpy.data.objects.remove(obj,do_unlink=True)
    for collection in (bpy.data.meshes,bpy.data.node_groups):
        for item in list(collection):
            if item.get('effect_owner')==owner and item.get(TAG) and item.users==0:
                collection.remove(item)


def regenerate(ts, helper):
    if helper.get('frozen'):
        raise ValueError('Unfreeze the helper before regenerating')
    mask=bpy.data.images.get(helper.get('mask_image',''))
    if not mask or not helper.parent:
        raise ValueError('Effect source or published mask is missing')
    # Replacing first would lose a good result on failure. Reserve against the
    # existing result conservatively until the replacement succeeds.
    replacement=create(ts,helper.parent,mask,helper['kind'],helper['density'],helper['size'],helper['seed'],helper['lift'],replacing=helper)
    remove(helper)
    return replacement
