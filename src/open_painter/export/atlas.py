"""Frozen grid atlas on evaluated copies and a new UV set; originals recoverable.

Each input occupies one square cell, without rotation. This intentionally simple
layout preserves tangent normal orientation. Published final textures are sampled;
procedural world coordinates are frozen rather than silently recomputed.
"""
import math
import bpy
import numpy as np
from ..constants import CHANNELS
from ..core import texture_set_manager as sets,layer_stack as stack,image_manager as images
from ..evaluation.evaluator import LayerEvaluator
from ..evaluation.blend_modes import sample_uv
from ..geometry.texel_density import measure


def minify(data,inner,channel):
    """Box-filter a source down towards its cell before the bilinear pass.

    Cells are usually far smaller than the sources (the 1 GiB guard keeps atlases at
    2048 while sets are commonly 2k–4k), and a bilinear tap at that ratio reads every
    Nth texel and aliases fine detail; the exporter filters the same way.
    """
    h,w=data.shape[:2]; factor=1
    while factor*2<=min(h,w)//inner and h%(factor*2)==0 and w%(factor*2)==0: factor*=2
    if factor==1: return data
    data=data.reshape(h//factor,factor,w//factor,factor,4).mean(axis=(1,3),dtype=np.float32)
    if channel=='NORMAL':
        xyz=data[...,:3]*2-1
        xyz/=np.maximum(np.linalg.norm(xyz,axis=-1,keepdims=True),1e-6)
        data[...,:3]=xyz*.5+.5
    return data


def build(context,sources,resolution=2048,gutter=8):
    if not sources or len(sources)>16 or resolution not in {512,1024,2048,4096,8192}:
        raise ValueError('Atlas supports 1–16 texture sets and an explicit supported resolution')
    # Mandatory RGBA authored channels alone; no claims about compression.
    mandatory=resolution*resolution*16*len(CHANNELS)
    if mandatory>1024**3:
        raise ValueError(f'Atlas outputs alone require {mandatory/1024**2:.0f} MiB; this operation is limited to 1 GiB')
    side=math.ceil(math.sqrt(len(sources))); cell=resolution//side
    inner=cell-2*gutter
    if gutter<1 or inner<16:
        raise ValueError('Atlas gutter leaves too little space in each cell')
    for source in sources:
        if measure(source)['outside_triangles']:
            raise ValueError('Atlas inputs must use UVs inside 0–1; preserve tiled inputs separately')
        LayerEvaluator(source.id_data).evaluate_texture_set(source.id)
    scene=context.scene
    source_ids=[source.id for source in sources]
    atlas=sets.create(scene,'Open Painter Atlas',resolution)
    sources=[next(ts for ts in scene.open_painter.texture_sets if ts.id==identifier) for identifier in source_ids]
    identifier=atlas.id
    initial_images=[output.image for output in atlas.outputs if output.image]
    initial_material=atlas.material
    created=[]; meshes=[]; made_images=[]
    try:
        graph=context.evaluated_depsgraph_get()
        cells=[]
        # A multi-material prop belongs to several sources (one set per slot), so the
        # cell is owned per slot and each object is copied exactly once.
        owners={}
        for index,source in enumerate(sources):
            x=(index%side)*cell+gutter; y=(index//side)*cell+gutter
            cells.append((x,y))
            for ref in source.assignments:
                if ref.object: owners.setdefault(ref.object,{})[ref.slot_index]=(x,y,source)
        for original,slots in owners.items():
            mesh=bpy.data.meshes.new_from_object(original.evaluated_get(graph),preserve_all_data_layers=True,depsgraph=graph)
            meshes.append(mesh)
            obj=bpy.data.objects.new(original.name+' Atlas',mesh); created.append(obj)
            scene.collection.objects.link(obj); obj.matrix_world=original.matrix_world
            render_uv=next((uv for uv in mesh.uv_layers if uv.active_render),mesh.uv_layers.active)
            if render_uv is None: raise ValueError('Evaluated atlas source lost its UV map')
            old=np.empty((len(mesh.loops),2),np.float32); render_uv.data.foreach_get('uv',old.ravel())
            new=mesh.uv_layers.new(name='Open Painter Atlas UV')
            transformed=old.copy()
            from ..core.udim import local_uv
            coordinates={}
            for slot,(_,_,source) in slots.items():
                faces=[p.index for p in mesh.polygons if p.material_index==slot]
                coordinates[slot]=local_uv(mesh,source,faces)
            for polygon in mesh.polygons:
                origin=slots.get(polygon.material_index)
                if origin is not None:
                    indices=list(polygon.loop_indices)
                    x,y,source=origin
                    local=coordinates[polygon.material_index][indices]
                    transformed[indices]=(local*inner+(x,y))/resolution
            new.data.foreach_set('uv',transformed.ravel()); new.active_render=True
            mesh.uv_layers.active=new
            for slot in sorted(slots): sets.assign(scene,atlas,obj,slot)
        layer=stack.add(atlas,'PAINT','Frozen Atlas Channels')
        for channel in CHANNELS:
            result=np.zeros((resolution,resolution,4),np.float32); result[...,3]=1
            for source,(x,y) in zip(sources,cells):
                data=minify(images.pixels(images.output(source,channel).image),inner,channel)
                for start in range(y-gutter,y+inner+gutter,64):
                    stop=min(start+64,y+inner+gutter)
                    u,v=np.meshgrid((np.arange(x-gutter,x+inner+gutter,dtype=np.float32)-x+.5)/inner,
                                    (np.arange(start,stop,dtype=np.float32)-y+.5)/inner)
                    result[start:stop,x-gutter:x+inner+gutter]=sample_uv(data,np.clip(u,0,1),np.clip(v,0,1),tiling='NONE')
            image=images.create('Atlas '+channel,resolution,color_data=channel in {'BASE_COLOR','EMISSION'})
            made_images.append(image); images.write(image,result)
            prop=stack.channel_for(layer,channel); prop.image=image; prop.enabled=True
        stack.invalidate(atlas)
        LayerEvaluator(scene).evaluate_texture_set(atlas.id)
        return atlas,created
    except Exception:
        for obj in created: bpy.data.objects.remove(obj,do_unlink=True)
        for mesh in meshes:
            if mesh.users==0: bpy.data.meshes.remove(mesh)
        for image in made_images:
            bpy.data.images.remove(image)
        index=next((i for i,ts in enumerate(scene.open_painter.texture_sets) if ts.id==identifier),None)
        if index is not None: scene.open_painter.texture_sets.remove(index)
        if initial_material:
            bpy.data.materials.remove(initial_material)
        for image in initial_images:
            bpy.data.images.remove(image)
        raise
