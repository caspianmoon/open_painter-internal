"""Batched world-space proximity and occlusion queries through Geometry Nodes.

``mathutils.bvhtree`` answers one point per Python call; a 512² field is a
quarter million calls. Geometry Nodes evaluates *Geometry Proximity* and
*Raycast* for every point of a mesh in one multithreaded pass, so the analysis
builds a disposable scene holding a point-cloud carrier (one vertex per texel)
and one target mesh (the influencers' evaluated triangles with a per-face
radius attribute), evaluates the carrier once and reads the stored attributes
back with ``foreach_get``. Everything created here is removed in ``finally``;
any failure raises so the caller can fall back to ``geometry.queries``.
"""
from contextlib import contextmanager

import bpy
import numpy as np

SCRATCH = 'open_painter_analysis_scratch'


def _socket(sockets, *names):
    for name in names:
        socket = sockets.get(name)
        if socket is not None:
            return socket
    raise KeyError(f'Geometry node socket not found: {names}')


def combined_target(scene, objects, radii, default_radius):
    """(vertices (V,3) world, triangles (T,3), radius per triangle) of evaluated influencers."""
    from .snapshots import depsgraph_for
    graph = depsgraph_for(scene, objects)
    vertices, triangles, radius = [], [], []
    offset = 0
    for obj in objects:
        evaluated = obj.evaluated_get(graph)
        mesh = evaluated.to_mesh(preserve_all_data_layers=False, depsgraph=graph)
        try:
            mesh.calc_loop_triangles()
            nv, nt = len(mesh.vertices), len(mesh.loop_triangles)
            if nv == 0 or nt == 0:
                continue
            local = np.empty(nv * 3, np.float32)
            mesh.vertices.foreach_get('co', local)
            world = np.asarray(evaluated.matrix_world, np.float64)
            coords = local.reshape(-1, 3) @ world[:3, :3].T + world[:3, 3]
            tris = np.empty(nt * 3, np.int32)
            mesh.loop_triangles.foreach_get('vertices', tris)
            vertices.append(coords.astype(np.float32))
            triangles.append(tris.reshape(-1, 3) + offset)
            radius.append(np.full(nt, radii.get(obj.name_full, default_radius), np.float32))
            offset += nv
        finally:
            evaluated.to_mesh_clear()
    if not vertices:
        return None
    return np.concatenate(vertices), np.concatenate(triangles), np.concatenate(radius)


def _mesh_from_arrays(name, vertices, triangles=None):
    mesh = bpy.data.meshes.new(name)
    mesh.vertices.add(len(vertices))
    mesh.vertices.foreach_set('co', np.ascontiguousarray(vertices, np.float32).ravel())
    if triangles is not None and len(triangles):
        count = len(triangles)
        mesh.loops.add(count * 3)
        mesh.loops.foreach_set('vertex_index', np.ascontiguousarray(triangles, np.int32).ravel())
        mesh.polygons.add(count)
        mesh.polygons.foreach_set('loop_start', np.arange(0, count * 3, 3, dtype=np.int32))
        mesh.polygons.foreach_set('loop_total', np.full(count, 3, np.int32))
    mesh.update()
    return mesh


@contextmanager
def _scratch(name):
    scene = bpy.data.scenes.new('OP Field Analysis')
    objects, meshes, groups = [], [], []
    try:
        yield scene, objects, meshes, groups
    finally:
        for obj in objects:
            try:
                bpy.data.objects.remove(obj, do_unlink=True)
            except ReferenceError:
                pass
        for mesh in meshes:
            try:
                bpy.data.meshes.remove(mesh)
            except ReferenceError:
                pass
        for group in groups:
            try:
                bpy.data.node_groups.remove(group)
            except ReferenceError:
                pass
        bpy.data.scenes.remove(scene)


def evaluate(scene, target, points, kind, maximum, normals=None, direction=None):
    """Batched query of ``points`` (N,3) against ``target`` from ``combined_target``.

    DISTANCE/CONTACT/PROXIMITY → (distance clipped to ``maximum``, radius of the
    nearest face); EXPOSURE → (1 where the ray along ``direction`` is free, 0
    where it hits within ``maximum``). Raises on any node/socket mismatch.
    """
    vertices, triangles, radius = target
    points = np.ascontiguousarray(points, np.float32)
    with _scratch('field') as (temp, objects, meshes, groups):
        target_mesh = _mesh_from_arrays('OP Field Target', vertices, triangles)
        meshes.append(target_mesh)
        attribute = target_mesh.attributes.new('op_radius', 'FLOAT', 'FACE')
        attribute.data.foreach_set('value', np.ascontiguousarray(radius, np.float32))
        target_obj = bpy.data.objects.new('OP Field Target', target_mesh)
        target_obj[SCRATCH] = True
        objects.append(target_obj)
        temp.collection.objects.link(target_obj)
        carrier_mesh = _mesh_from_arrays('OP Field Carrier', points)
        meshes.append(carrier_mesh)
        if kind == 'EXPOSURE':
            normal_attribute = carrier_mesh.attributes.new('op_normal', 'FLOAT_VECTOR', 'POINT')
            normal_attribute.data.foreach_set('vector', np.ascontiguousarray(normals, np.float32).ravel())
        carrier = bpy.data.objects.new('OP Field Carrier', carrier_mesh)
        carrier[SCRATCH] = True
        objects.append(carrier)
        temp.collection.objects.link(carrier)
        tree = bpy.data.node_groups.new('OP Field Query', 'GeometryNodeTree')
        groups.append(tree)
        tree.interface.new_socket('Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
        tree.interface.new_socket('Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
        nodes, links = tree.nodes, tree.links
        group_in, group_out = nodes.new('NodeGroupInput'), nodes.new('NodeGroupOutput')
        info = nodes.new('GeometryNodeObjectInfo')
        info.transform_space = 'RELATIVE'
        _socket(info.inputs, 'Object').default_value = target_obj
        geometry = group_in.outputs['Geometry']
        if kind == 'EXPOSURE':
            named = nodes.new('GeometryNodeInputNamedAttribute')
            named.data_type = 'FLOAT_VECTOR'
            _socket(named.inputs, 'Name').default_value = 'op_normal'
            position = nodes.new('GeometryNodeInputPosition')
            scale = nodes.new('ShaderNodeVectorMath')
            scale.operation = 'SCALE'
            links.new(named.outputs['Attribute'], scale.inputs[0])
            _socket(scale.inputs, 'Scale').default_value = max(maximum * 1e-5, 1e-6)
            add = nodes.new('ShaderNodeVectorMath')
            add.operation = 'ADD'
            links.new(position.outputs['Position'], add.inputs[0])
            links.new(scale.outputs['Vector'], add.inputs[1])
            ray = nodes.new('GeometryNodeRaycast')
            links.new(info.outputs['Geometry'], _socket(ray.inputs, 'Target Geometry', 'Target'))
            links.new(add.outputs['Vector'], _socket(ray.inputs, 'Source Position'))
            _socket(ray.inputs, 'Ray Direction').default_value = tuple(float(v) for v in direction)
            _socket(ray.inputs, 'Ray Length').default_value = float(maximum)
            store = nodes.new('GeometryNodeStoreNamedAttribute')
            store.data_type, store.domain = 'BOOLEAN', 'POINT'
            _socket(store.inputs, 'Name').default_value = 'op_hit'
            links.new(geometry, _socket(store.inputs, 'Geometry'))
            links.new(_socket(ray.outputs, 'Is Hit'), _socket(store.inputs, 'Value'))
            links.new(store.outputs['Geometry'], group_out.inputs['Geometry'])
        else:
            proximity = nodes.new('GeometryNodeProximity')
            proximity.target_element = 'FACES'
            links.new(info.outputs['Geometry'], _socket(proximity.inputs, 'Target', 'Target Geometry', 'Geometry'))
            store = nodes.new('GeometryNodeStoreNamedAttribute')
            store.data_type, store.domain = 'FLOAT', 'POINT'
            _socket(store.inputs, 'Name').default_value = 'op_distance'
            links.new(geometry, _socket(store.inputs, 'Geometry'))
            links.new(_socket(proximity.outputs, 'Distance'), _socket(store.inputs, 'Value'))
            named = nodes.new('GeometryNodeInputNamedAttribute')
            named.data_type = 'FLOAT'
            _socket(named.inputs, 'Name').default_value = 'op_radius'
            sample = nodes.new('GeometryNodeSampleNearestSurface')
            sample.data_type = 'FLOAT'
            links.new(info.outputs['Geometry'], _socket(sample.inputs, 'Mesh'))
            links.new(named.outputs['Attribute'], _socket(sample.inputs, 'Value'))
            links.new(_socket(proximity.outputs, 'Position'), _socket(sample.inputs, 'Sample Position'))
            store2 = nodes.new('GeometryNodeStoreNamedAttribute')
            store2.data_type, store2.domain = 'FLOAT', 'POINT'
            _socket(store2.inputs, 'Name').default_value = 'op_near_radius'
            links.new(store.outputs['Geometry'], _socket(store2.inputs, 'Geometry'))
            links.new(_socket(sample.outputs, 'Value'), _socket(store2.inputs, 'Value'))
            links.new(store2.outputs['Geometry'], group_out.inputs['Geometry'])
        modifier = carrier.modifiers.new('OP Field', 'NODES')
        modifier.node_group = tree
        with bpy.context.temp_override(scene=temp, view_layer=temp.view_layers[0]):
            graph = bpy.context.evaluated_depsgraph_get()
            graph.update()
        evaluated = carrier.evaluated_get(graph).data
        count = len(points)
        if kind == 'EXPOSURE':
            hit = np.zeros(count, np.bool_)
            evaluated.attributes['op_hit'].data.foreach_get('value', hit)
            return np.where(hit, 0.0, 1.0).astype(np.float32), None
        distances = np.empty(count, np.float32)
        evaluated.attributes['op_distance'].data.foreach_get('value', distances)
        near = np.empty(count, np.float32)
        evaluated.attributes['op_near_radius'].data.foreach_get('value', near)
        return np.minimum(distances, maximum).astype(np.float32), near
