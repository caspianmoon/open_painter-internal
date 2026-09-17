"""Explicit bounded offline analysis; no timeline handlers or physical wear claims."""
import numpy as np
from ..generators.scene_fields import compute


def contact_history(ts,field,start,end,step=1):
    if field.kind!='CONTACT' or field.object is None:
        raise ValueError('Choose a contact field with an explicit object pair')
    frames=list(range(start,end+1,step)) if step>0 and end>=start else []
    if not frames or len(frames)>120:
        raise ValueError('Offline contact analysis supports 1–120 explicit samples')
    scene=ts.id_data; old_frame=scene.frame_current; old_subframe=scene.frame_subframe
    objects=list(dict.fromkeys(ref.object for ref in ts.assignments if ref.object))
    if not objects: raise ValueError('Assign a source surface before offline analysis')
    from ..geometry.snapshots import depsgraph_for
    topology={}
    n=int(field.resolution)
    duration=np.zeros((n,n),np.float32); proximity=np.zeros_like(duration)
    travel=0.; previous=None
    try:
        for frame in frames:
            scene.frame_set(frame)
            # The layer the field's sampling (``compute``) evaluates the sources in; the
            # first layer may exclude them and hand back the base mesh every frame.
            depsgraph=depsgraph_for(scene,objects)
            for obj in objects:
                evaluated=obj.evaluated_get(depsgraph)
                mesh=evaluated.to_mesh()
                try:
                    signature=(len(mesh.vertices),tuple(tuple(face.vertices) for face in mesh.polygons))
                    if obj.as_pointer() in topology and signature!=topology[obj.as_pointer()]:
                        raise ValueError('Offline analysis requires stable evaluated topology')
                    topology[obj.as_pointer()]=signature
                finally: evaluated.to_mesh_clear()
            # Conservative world bounds reject obviously separated pairs before
            # sampling UV geometry and issuing nearest-surface queries.
            from mathutils import Vector
            source_bounds=np.array([obj.matrix_world@Vector(corner) for obj in objects for corner in obj.bound_box])
            other_bounds=np.array([field.object.matrix_world@Vector(corner) for corner in field.object.bound_box])
            separated=np.any(source_bounds.max(axis=0)+field.distance<other_bounds.min(axis=0)) or np.any(other_bounds.max(axis=0)+field.distance<source_bounds.min(axis=0))
            values=np.zeros((n,n),np.float32) if separated else compute(ts,field)
            proximity+=values; duration+=(values>.5)
            relative=np.asarray(field.object.matrix_world.translation)-np.asarray(objects[0].matrix_world.translation)
            if previous is not None: travel+=float(np.linalg.norm(relative-previous))
            previous=relative
    finally:
        scene.frame_set(old_frame,subframe=old_subframe)
    return dict(contact_fraction=duration/len(frames),mean_proximity=proximity/len(frames),
        frames=frames,relative_origin_travel=travel,
        interpretation='Artistic proximity duration and origin travel; not friction or contact force')


def strain(rest,deformed,edges):
    """Mean incident-edge extension against a supplied rest shape, same topology."""
    rest=np.asarray(rest,np.float64); deformed=np.asarray(deformed,np.float64); edges=np.asarray(edges,np.int32)
    if rest.shape!=deformed.shape or rest.ndim!=2 or rest.shape[1]!=3:
        raise ValueError('Rest and deformed positions must have identical topology')
    if edges.ndim!=2 or edges.shape[1]!=2 or edges.min(initial=0)<0 or edges.max(initial=0)>=len(rest):
        raise ValueError('Invalid rest-state edge indices')
    lengths=np.linalg.norm(rest[edges[:,1]]-rest[edges[:,0]],axis=1)
    if np.any(lengths<1e-10): raise ValueError('Degenerate rest edges cannot define strain')
    extension=np.linalg.norm(deformed[edges[:,1]]-deformed[edges[:,0]],axis=1)/lengths-1
    result=np.zeros(len(rest),np.float64); counts=np.zeros(len(rest),np.float64)
    for side in (0,1):
        np.add.at(result,edges[:,side],extension); np.add.at(counts,edges[:,side],1)
    return np.divide(result,counts,out=result,where=counts>0).astype(np.float32)


def mark_faces(obj,indices,name='open_painter_cut_surface'):
    """Explicit authored face marking, not inferred Boolean history."""
    selected=set(indices)
    if obj.type!='MESH' or any(index<0 or index>=len(obj.data.polygons) for index in selected):
        raise ValueError('Choose valid mesh face indices')
    attribute=obj.data.attributes.get(name)
    if attribute and (attribute.domain!='FACE' or attribute.data_type!='FLOAT'):
        raise ValueError('Existing attribute has a different domain or type')
    if obj.mode=='EDIT':
        # Attribute data reads as empty while an edit-mesh exists, so the
        # write has to go through the BMesh face layer instead.
        import bmesh
        bm=bmesh.from_edit_mesh(obj.data)
        layer=bm.faces.layers.float.get(name) or bm.faces.layers.float.new(name)
        for index,face in enumerate(bm.faces): face[layer]=float(index in selected)
        bmesh.update_edit_mesh(obj.data)
        return obj.data.attributes.get(name)
    attribute=attribute or obj.data.attributes.new(name,'FLOAT','FACE')
    data=np.array([float(i in selected) for i in range(len(obj.data.polygons))],np.float32)
    attribute.data.foreach_set('value',data)
    return attribute
