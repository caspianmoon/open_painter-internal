"""Instances along an extracted curve: stitches, or any prototype kind, spaced by length."""
import bpy
import numpy as np

from ..utils.ids import new_id
from . import limits, materials, prototypes
from .manager import TAG, _prototype_objects


def create(scene, curve, kind='STITCH', spacing=.02, size=.01, seed=1, source=None, replacing=None):
    """Prototype instances resampled along ``curve`` every ``spacing``; aligned to the tangent and,
    when ``source`` (the surface object) is given, to its nearest surface normal."""
    if curve.type != 'CURVE' or spacing <= 0 or size <= 0:
        raise ValueError('Select an extracted curve and positive spacing and size')
    length = sum(spline.calc_length() for spline in curve.data.splines)
    count = int(length / spacing) + len(curve.data.splines)
    triangles = count * 96
    limits.check(scene, count, triangles, replacing=replacing)
    identifier = new_id()
    created = []
    try:
        material = materials.create(kind if kind in materials.BUILDERS else 'WELD')
        created.append(material)
        variant_objects = _prototype_objects(kind, seed, material, created)
        group = bpy.data.node_groups.new('OP Curve Instances', 'GeometryNodeTree')
        created.append(group)
        group.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
        nodes, links = group.nodes, group.links
        output_node = nodes.new('NodeGroupOutput')
        info = nodes.new('GeometryNodeObjectInfo')
        info.inputs['Object'].default_value = curve
        info.transform_space = 'RELATIVE'
        resample = nodes.new('GeometryNodeResampleCurve')
        resample.inputs['Mode'].default_value = 'Length'
        resample.inputs['Length'].default_value = spacing
        links.new(info.outputs['Geometry'], resample.inputs['Curve'])
        to_points = nodes.new('GeometryNodeCurveToPoints')
        to_points.mode = 'EVALUATED'
        links.new(resample.outputs['Curve'], to_points.inputs['Curve'])
        join = nodes.new('GeometryNodeJoinGeometry')
        for prototype in variant_objects:
            proto = nodes.new('GeometryNodeObjectInfo')
            proto.inputs['Object'].default_value = prototype
            proto.transform_space = 'ORIGINAL'
            wrap = nodes.new('GeometryNodeGeometryToInstance')
            links.new(proto.outputs['Geometry'], wrap.inputs['Geometry'])
            links.new(wrap.outputs['Instances'], join.inputs['Geometry'])
        instance = nodes.new('GeometryNodeInstanceOnPoints')
        instance.inputs['Pick Instance'].default_value = True
        instance.inputs['Scale'].default_value = (size, size, size)
        links.new(to_points.outputs['Points'], instance.inputs['Points'])
        links.new(join.outputs['Geometry'], instance.inputs['Instance'])
        # Rotation: X along the curve tangent, Z along the surface normal (or the curve normal).
        align = nodes.new('FunctionNodeAlignRotationToVector')
        align.axis, align.pivot_axis = 'X', 'AUTO'
        links.new(to_points.outputs['Tangent'], align.inputs['Vector'])
        up = nodes.new('FunctionNodeAlignRotationToVector')
        up.axis, up.pivot_axis = 'Z', 'X'
        links.new(align.outputs['Rotation'], up.inputs['Rotation'])
        if source is not None and source.type == 'MESH':
            surface = nodes.new('GeometryNodeObjectInfo')
            surface.inputs['Object'].default_value = source
            surface.transform_space = 'RELATIVE'
            normal = nodes.new('GeometryNodeInputNormal')
            sample = nodes.new('GeometryNodeSampleNearestSurface')
            sample.data_type = 'FLOAT_VECTOR'
            links.new(surface.outputs['Geometry'], sample.inputs['Mesh'])
            links.new(normal.outputs['Normal'], sample.inputs['Value'])
            position = nodes.new('GeometryNodeInputPosition')
            links.new(position.outputs['Position'], sample.inputs['Sample Position'])
            links.new(sample.outputs['Value'], up.inputs['Vector'])
        else:
            links.new(to_points.outputs['Normal'], up.inputs['Vector'])
        links.new(up.outputs['Rotation'], instance.inputs['Rotation'])
        random_index = nodes.new('FunctionNodeRandomValue')
        random_index.data_type = 'INT'
        random_index.inputs['Min'].default_value = 0
        random_index.inputs['Max'].default_value = max(0, len(variant_objects) - 1)
        random_index.inputs['Seed'].default_value = seed
        links.new(random_index.outputs['Value'], instance.inputs['Instance Index'])
        links.new(instance.outputs['Instances'], output_node.inputs['Geometry'])
        mesh = bpy.data.meshes.new('OP Curve Instances')
        created.append(mesh)
        helper = bpy.data.objects.new(f'OP {kind.title()} · {curve.name}', mesh)
        created.append(helper)
        scene.collection.objects.link(helper)
        helper.parent = curve
        helper.modifiers.new('Open Painter Curve Instances', 'NODES').node_group = group
        for item in created:
            item[TAG] = 'INSTANCE_HELPER' if item == helper else 'OWNED_RESOURCE'
            item['effect_owner'] = identifier
        helper['instance_count'] = count
        helper['estimated_triangles'] = triangles
        helper['kind'] = kind
        helper['spacing'] = spacing
        helper['size'] = size
        helper['seed'] = seed
        helper['frozen'] = False
        return helper
    except Exception:
        for item in reversed(created):
            if isinstance(item, bpy.types.Object):
                bpy.data.objects.remove(item, do_unlink=True)
            elif isinstance(item, bpy.types.Mesh):
                bpy.data.meshes.remove(item)
            elif isinstance(item, bpy.types.NodeTree):
                bpy.data.node_groups.remove(item)
            elif isinstance(item, bpy.types.Material):
                bpy.data.materials.remove(item)
        raise
