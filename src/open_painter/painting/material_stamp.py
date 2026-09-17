"""Deterministic multi-channel surface stamps, committed as one logical edit.

Projection follows the hit normal onto front-facing assigned triangles. A bounded
per-texel visibility test prevents paint-through within the target mesh. UV seams
are evaluated through geometry, not a single image-space circle.
"""
import bpy
import numpy as np
from mathutils.bvhtree import BVHTree
from ..geometry.snapshots import targets
from ..geometry.uv_rasterizer import tiles
from ..core import image_manager as images,layer_stack as stack

MAX_SAMPLES=131072
MAX_WORKING_BYTES=1024*1024*1024


def coverage(ts,obj,center,normal,radius,pressure=1,hardness=.8):
    if radius<=0 or not 0<=pressure<=1 or not 0<=hardness<=1:
        raise ValueError('Invalid stamp radius, pressure or hardness')
    n=int(ts.resolution)
    result=np.zeros((n,n),np.float32)
    center=np.asarray(center,np.float64); normal=np.asarray(normal,np.float64)
    normal/=max(np.linalg.norm(normal),1e-10)
    if np.linalg.norm(normal)<.5:
        raise ValueError('Stamp hit normal is invalid')
    count=0
    with targets(ts) as entries:
        target=next((entry for entry in entries if entry['object']==obj),None)
        if target is None:
            raise ValueError('Stamp target is not assigned to this texture set')
        mesh=target['mesh']; positions=target['positions']
        # Every evaluated triangle occludes, including unassigned slots: the
        # visibility test must not paint through protected geometry.
        occluders=np.empty(len(mesh.loop_triangles)*3,np.int32)
        mesh.loop_triangles.foreach_get('vertices',occluders)
        tree=BVHTree.FromPolygons(positions,occluders.reshape(-1,3).tolist(),all_triangles=True)
        # Cull with the Target arrays; per-triangle RNA work would cost seconds
        # per click on dense assets no matter how small the stamp is.
        tri_loops=target['tri_loops']; corners=positions[target['tri_vertices']]
        chosen=np.flatnonzero(np.all(corners.max(axis=1)>=center-radius,axis=1)&np.all(corners.min(axis=1)<=center+radius,axis=1))
        for i in chosen:
            coords=corners[i]
            normals=target['world_normals'][tri_loops[i]]
            for region,inside,weights,_,_ in tiles(target['uv'][tri_loops[i]],n):
                points=weights@coords
                relative=points-center
                depth=relative@normal
                radial=np.linalg.norm(relative-depth[...,None]*normal,axis=-1)/radius
                facing=(weights@normals)@normal>.05
                candidates=inside & facing & (radial<1) & (np.abs(depth)<radius)
                count+=int(candidates.sum())
                if count>MAX_SAMPLES:
                    raise ValueError(f'Stamp exceeds {MAX_SAMPLES:,} visibility samples; reduce radius or stamp in separate gestures')
                visible=np.zeros(inside.shape,np.bool_)
                epsilon=max(radius*1e-4,1e-6)
                for y,x in np.argwhere(candidates):
                    point=points[y,x]
                    hit=tree.ray_cast(point+normal*radius*2,-normal,radius*4)[0]
                    visible[y,x]=hit is not None and np.linalg.norm(np.asarray(hit)-point)<epsilon
                alpha=np.clip((1-radial)/max(1-hardness,1e-5),0,1)*pressure*visible
                np.maximum(result[region],alpha,out=result[region])
    return result


def apply(ts,layer,obj,center,normal,radius,values,pressure=1,hardness=.8):
    if layer.type!='PAINT':
        raise ValueError('Material stamps require a paint layer')
    supported={'BASE_COLOR','ROUGHNESS','METALLIC','HEIGHT'}
    if not values or not set(values)<=supported:
        raise ValueError('Material stamps support Base Color, Roughness, Metallic and Height')
    n=int(ts.resolution)
    estimated=n*n*(4+len(values)*32)
    if estimated>MAX_WORKING_BYTES:
        raise ValueError(f'Stamp needs approximately {estimated/1024**2:.0f} MiB of temporary buffers; limit is {MAX_WORKING_BYTES/1024**2:.0f} MiB')
    mask=coverage(ts,obj,center,normal,radius,pressure,hardness)
    if not np.any(mask):
        return False
    staged=[]
    for channel,value in values.items():
        prop=stack.channel_for(layer,channel)
        original=images.pixels(prop.image) if prop.image else np.zeros((n,n,4),np.float32)
        if original.shape!=(n,n,4):
            raise ValueError('Authored paint image size differs from texture set; resize explicitly before stamping')
        color=np.broadcast_to(np.asarray(value,np.float32), (3,))
        if not np.isfinite(color).all():
            raise ValueError('Stamp channel values must be finite')
        alpha=mask[...,None]
        output=original.copy()
        output[...,:3]=color*alpha+original[...,:3]*(1-alpha)
        output[...,3]=mask+original[...,3]*(1-mask)
        staged.append((prop,prop.image,prop.enabled,original,output))
    created=[]
    try:
        for prop,image,_,_,output in staged:
            # Python Image.pixels writes are not native paint-undo tiles. Swap
            # owned immutable snapshots so Blender's datablock undo restores the
            # previous source, including an existing painted image.
            replacement=images.create(f'{ts.name}_{layer.name}_{prop.channel}',n,color_data=prop.channel=='BASE_COLOR')
            replacement.use_fake_user=False
            replacement['open_painter_stamp_snapshot']=True
            created.append(replacement)
            # Packed pixels are retained by Blender's memfile redo as well as
            # pointer undo; an unpacked generated Image can redo as blank.
            images.write(replacement,output,pack=True)
            prop.image=replacement; prop.enabled=True
    except Exception:
        for prop,image,enabled,original,_ in staged:
            prop.image=image; prop.enabled=enabled
        for image in created:
            bpy.data.images.remove(image)
        stack.invalidate(ts)
        raise
    stack.invalidate(ts)
    return True


def decal(ts,name,image,offset=(0,0),scale=(1,1),rotation=0):
    """Editable UV decal using existing non-repeating fill projection."""
    layer=stack.add(ts,'FILL',name)
    for prop in layer.channels: prop.enabled=prop.channel=='BASE_COLOR'
    prop=stack.channel_for(layer,'BASE_COLOR')
    prop.image=image; prop.tiling='NONE'; prop.offset=offset; prop.scale=scale; prop.rotation=rotation
    stack.invalidate(ts)
    return layer
