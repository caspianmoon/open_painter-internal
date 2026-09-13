"""Owned editable weld tube on a selected curve; no automatic joint inference."""
import bpy
from ..utils.ids import new_id
from . import limits
from .manager import TAG


def create(scene, curve, radius=.015, resolution=4):
    if curve.type != 'CURVE' or radius <= 0:
        raise ValueError('Select an editable curve and a positive weld radius')
    # Bounded curve evaluation; this initial tube supports poly/Bezier paths.
    if any(s.type not in {'POLY','BEZIER'} for s in curve.data.splines):
        raise ValueError('Weld paths support Poly and Bezier splines')
    count = sum(len(s.bezier_points) if s.type=='BEZIER' else len(s.points) for s in curve.data.splines)
    estimate = max(1,count)*max(1,curve.data.resolution_u)*8*resolution
    limits.check(scene,0,estimate)
    identifier = new_id()
    group=bpy.data.node_groups.new('OP Editable Weld','GeometryNodeTree')
    group.interface.new_socket(name='Geometry',in_out='OUTPUT',socket_type='NodeSocketGeometry')
    nodes,links=group.nodes,group.links
    source=nodes.new('GeometryNodeObjectInfo'); source.inputs['Object'].default_value=curve
    source.transform_space='ORIGINAL'
    profile=nodes.new('GeometryNodeCurvePrimitiveCircle'); profile.inputs['Resolution'].default_value=max(3,min(16,resolution*2))
    profile.inputs['Radius'].default_value=radius
    tube=nodes.new('GeometryNodeCurveToMesh')
    tube.inputs['Fill Caps'].default_value=True
    output=nodes.new('NodeGroupOutput')
    links.new(source.outputs['Geometry'],tube.inputs['Curve'])
    links.new(profile.outputs['Curve'],tube.inputs['Profile Curve'])
    links.new(tube.outputs['Mesh'],output.inputs['Geometry'])
    mesh=bpy.data.meshes.new('OP Weld Carrier')
    helper=bpy.data.objects.new('OP Weld · '+curve.name,mesh)
    scene.collection.objects.link(helper); helper.parent=curve
    helper.modifiers.new('Editable weld path','NODES').node_group=group
    for item in (helper,mesh,group):
        item[TAG]='INSTANCE_HELPER' if item==helper else 'OWNED_RESOURCE'
        item['effect_owner']=identifier
    helper['instance_count']=0; helper['estimated_triangles']=estimate; helper['kind']='WELD'
    return helper


def coating_depth(damage, paint=.25, primer=.65):
    """Logical paint/primer/substrate masks, not a physical volumetric cut."""
    import numpy as np
    if not 0 <= paint < primer <= 1:
        raise ValueError('Depth thresholds must satisfy 0 <= paint < primer <= 1')
    value=np.asarray(damage,np.float32)
    return (value<paint).astype(np.float32), ((value>=paint)&(value<primer)).astype(np.float32), (value>=primer).astype(np.float32)
