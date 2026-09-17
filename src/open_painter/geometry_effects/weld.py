"""Owned editable weld bead on a selected curve; no automatic joint inference."""
import bpy
from ..utils.ids import new_id
from . import limits
from .manager import TAG


def create(scene, curve, radius=.015, resolution=4, ripple=.3, period=1.35):
    """Rippled weld bead along an editable curve; the curve itself is untouched.

    The profile is a flattened circle sunk slightly into the surface, so the
    bead reads as a raised seam rather than a pipe. The curve is resampled by
    length and its radius modulated by a sine of the arc length plus noise,
    giving the overlapping "stack of dimes" rhythm of a manual weld. ``period``
    is the ripple spacing in bead radii, ``ripple`` its relative depth.
    """
    if curve.type != 'CURVE' or radius <= 0:
        raise ValueError('Select an editable curve and a positive weld radius')
    # Bounded curve evaluation; this initial tube supports poly/Bezier paths.
    if any(s.type not in {'POLY','BEZIER'} for s in curve.data.splines):
        raise ValueError('Weld paths support Poly and Bezier splines')
    profile_points = max(8, min(32, resolution * 4))
    # The tube is a length resample swept by the profile, so its size follows the
    # arc length and the ripple step, not the control-point count (an extracted
    # seam keeps hundreds of points for a few thousand triangles). calc_length
    # falls back to the spline's own resolution_u for Bezier paths.
    step = radius * period / 14
    length = sum(s.calc_length() for s in curve.data.splines)
    segments = int(length / step) + len(curve.data.splines)
    estimate = segments * profile_points * 2 + 2 * len(curve.data.splines) * (profile_points - 2)  # plus filled caps
    limits.check(scene, 0, estimate)
    identifier = new_id()
    from . import materials
    material = materials.create('WELD')
    group = bpy.data.node_groups.new('OP Editable Weld', 'GeometryNodeTree')
    group.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    nodes, links = group.nodes, group.links
    source = nodes.new('GeometryNodeObjectInfo')
    source.inputs['Object'].default_value = curve
    source.transform_space = 'ORIGINAL'
    resample = nodes.new('GeometryNodeResampleCurve')
    resample.inputs['Mode'].default_value = 'Length'
    resample.inputs['Length'].default_value = radius * period / 14
    links.new(source.outputs['Geometry'], resample.inputs['Curve'])
    # Arc length travels with the curve points into the mesh vertices.
    parameter = nodes.new('GeometryNodeSplineParameter')
    store_length = nodes.new('GeometryNodeStoreNamedAttribute')
    store_length.data_type = 'FLOAT'
    store_length.domain = 'POINT'
    store_length.inputs['Name'].default_value = 'op_arc'
    links.new(resample.outputs['Curve'], store_length.inputs['Geometry'])
    links.new(parameter.outputs['Length'], store_length.inputs['Value'])
    # Bead profile: circle flattened across the surface normal and sunk 30%,
    # with its own parameter stored so the ripples can curve across the bead.
    profile = nodes.new('GeometryNodeCurvePrimitiveCircle')
    profile.inputs['Resolution'].default_value = profile_points
    profile.inputs['Radius'].default_value = radius
    profile_parameter = nodes.new('GeometryNodeSplineParameter')
    store_side = nodes.new('GeometryNodeStoreNamedAttribute')
    store_side.data_type = 'FLOAT'
    store_side.domain = 'POINT'
    store_side.inputs['Name'].default_value = 'op_side'
    links.new(profile.outputs['Curve'], store_side.inputs['Geometry'])
    links.new(profile_parameter.outputs['Factor'], store_side.inputs['Value'])
    squash = nodes.new('GeometryNodeTransform')
    squash.inputs['Translation'].default_value = (0, radius * .3, 0)  # profile Y maps to -Z: sink the bead
    squash.inputs['Scale'].default_value = (1, .72, 1)
    links.new(store_side.outputs['Geometry'], squash.inputs['Geometry'])
    tube = nodes.new('GeometryNodeCurveToMesh')
    tube.inputs['Fill Caps'].default_value = True
    links.new(store_length.outputs['Geometry'], tube.inputs['Curve'])
    links.new(squash.outputs['Geometry'], tube.inputs['Profile Curve'])
    # Stack-of-dimes: each ripple is a crescent, so its phase is shifted by the
    # squared lateral position; the bump is pinched (raised to a power) and
    # noise breaks the regularity. Displacement follows the mesh normal.
    arc = nodes.new('GeometryNodeInputNamedAttribute')
    arc.data_type = 'FLOAT'
    arc.inputs['Name'].default_value = 'op_arc'
    side = nodes.new('GeometryNodeInputNamedAttribute')
    side.data_type = 'FLOAT'
    side.inputs['Name'].default_value = 'op_side'
    lateral = nodes.new('ShaderNodeMath')            # cos(2 pi factor): -1..1 across the bead
    lateral.operation = 'COSINE'
    side_angle = nodes.new('ShaderNodeMath')
    side_angle.operation = 'MULTIPLY'
    side_angle.inputs[1].default_value = 2 * 3.141592653589793
    links.new(side.outputs['Attribute'], side_angle.inputs[0])
    links.new(side_angle.outputs[0], lateral.inputs[0])
    lateral_sq = nodes.new('ShaderNodeMath')
    lateral_sq.operation = 'POWER'
    lateral_sq.inputs[1].default_value = 2
    links.new(lateral.outputs[0], lateral_sq.inputs[0])
    crescent = nodes.new('ShaderNodeMath')           # shift the ripple phase towards the edges
    crescent.operation = 'MULTIPLY'
    crescent.inputs[1].default_value = -.6
    links.new(lateral_sq.outputs[0], crescent.inputs[0])
    phase = nodes.new('ShaderNodeMath')
    phase.operation = 'DIVIDE'
    phase.inputs[1].default_value = period * radius
    links.new(arc.outputs['Attribute'], phase.inputs[0])
    noise = nodes.new('ShaderNodeTexNoise')
    noise.inputs['Scale'].default_value = 2.5 / radius
    noise.inputs['Detail'].default_value = 2
    position = nodes.new('GeometryNodeInputPosition')
    links.new(position.outputs['Position'], noise.inputs['Vector'])
    noise_shift = nodes.new('ShaderNodeMath')
    noise_shift.operation = 'MULTIPLY_ADD'
    noise_shift.inputs[1].default_value = .35
    noise_shift.inputs[2].default_value = -.175
    links.new(noise.outputs['Fac'], noise_shift.inputs[0])
    total_phase = nodes.new('ShaderNodeMath')
    total_phase.operation = 'ADD'
    links.new(phase.outputs[0], total_phase.inputs[0])
    links.new(crescent.outputs[0], total_phase.inputs[1])
    total_phase2 = nodes.new('ShaderNodeMath')
    total_phase2.operation = 'ADD'
    links.new(total_phase.outputs[0], total_phase2.inputs[0])
    links.new(noise_shift.outputs[0], total_phase2.inputs[1])
    wave = nodes.new('ShaderNodeMath')
    wave.operation = 'SINE'
    turns = nodes.new('ShaderNodeMath')
    turns.operation = 'MULTIPLY'
    turns.inputs[1].default_value = 2 * 3.141592653589793
    links.new(total_phase2.outputs[0], turns.inputs[0])
    links.new(turns.outputs[0], wave.inputs[0])
    raised = nodes.new('ShaderNodeMath')             # 0..1
    raised.operation = 'MULTIPLY_ADD'
    raised.inputs[1].default_value = .5
    raised.inputs[2].default_value = .5
    links.new(wave.outputs[0], raised.inputs[0])
    pinched = nodes.new('ShaderNodeMath')
    pinched.operation = 'POWER'
    pinched.inputs[1].default_value = 2.4
    links.new(raised.outputs[0], pinched.inputs[0])
    amplitude = nodes.new('ShaderNodeMath')
    amplitude.operation = 'MULTIPLY'
    amplitude.inputs[1].default_value = ripple * radius
    links.new(pinched.outputs[0], amplitude.inputs[0])
    # Only the exposed top half ripples; the sunk underside stays clean.
    exposure = nodes.new('ShaderNodeMath')
    exposure.operation = 'MULTIPLY'
    links.new(amplitude.outputs[0], exposure.inputs[0])
    upward = nodes.new('ShaderNodeMath')            # -sin(2 pi factor) is the upward profile component
    upward.operation = 'SINE'
    links.new(side_angle.outputs[0], upward.inputs[0])
    clamp_up = nodes.new('ShaderNodeClamp')
    negate = nodes.new('ShaderNodeMath')
    negate.operation = 'MULTIPLY'
    negate.inputs[1].default_value = -1
    links.new(upward.outputs[0], negate.inputs[0])
    links.new(negate.outputs[0], clamp_up.inputs['Value'])
    links.new(clamp_up.outputs['Result'], exposure.inputs[1])
    normal = nodes.new('GeometryNodeInputNormal')
    offset = nodes.new('ShaderNodeVectorMath')
    offset.operation = 'SCALE'
    links.new(normal.outputs['Normal'], offset.inputs[0])
    links.new(exposure.outputs[0], offset.inputs[3])
    displace = nodes.new('GeometryNodeSetPosition')
    links.new(tube.outputs['Mesh'], displace.inputs['Geometry'])
    links.new(offset.outputs['Vector'], displace.inputs['Offset'])
    smooth = nodes.new('GeometryNodeSetShadeSmooth')
    links.new(displace.outputs['Geometry'], smooth.inputs['Mesh'])
    assign = nodes.new('GeometryNodeSetMaterial')
    assign.inputs['Material'].default_value = material
    links.new(smooth.outputs['Geometry'], assign.inputs['Geometry'])
    output = nodes.new('NodeGroupOutput')
    links.new(assign.outputs['Geometry'], output.inputs['Geometry'])
    mesh = bpy.data.meshes.new('OP Weld Carrier')
    helper = bpy.data.objects.new('OP Weld · ' + curve.name, mesh)
    scene.collection.objects.link(helper)
    helper.parent = curve
    helper.modifiers.new('Editable weld path', 'NODES').node_group = group
    for item in (helper, mesh, group, material):
        item[TAG] = 'INSTANCE_HELPER' if item == helper else 'OWNED_RESOURCE'
        item['effect_owner'] = identifier
    helper['instance_count'] = 0
    helper['estimated_triangles'] = estimate
    helper['kind'] = 'WELD'
    helper['radius'] = radius
    helper['ripple'] = ripple
    helper['period'] = period
    return helper


def coating_depth(damage, paint=.25, primer=.65):
    """Logical paint/primer/substrate masks, not a physical volumetric cut."""
    import numpy as np
    if not 0 <= paint < primer <= 1:
        raise ValueError('Depth thresholds must satisfy 0 <= paint < primer <= 1')
    value=np.asarray(damage,np.float32)
    return (value<paint).astype(np.float32), ((value>=paint)&(value<primer)).astype(np.float32), (value>=primer).astype(np.float32)
