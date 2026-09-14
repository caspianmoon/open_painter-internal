"""Owned, bounded Geometry Nodes instances on separate source-parented helpers.

Placement is deterministic for unchanged evaluated topology. Re-evaluated topology
may reseed triangle selection. Analysis uses source assignments, never helpers.

Every effect instances several prototype variants with per-point rotation,
scale, tilt and a random value the materials read, so nothing repeats one
silhouette or one texture patch. Prototypes and materials are owned resources.
"""
import math

import bpy
import numpy as np
from mathutils import Quaternion, Vector
from ..geometry.snapshots import targets
from ..evaluation.blend_modes import sample_uv
from ..core.image_manager import pixels
from ..utils.ids import new_id
from . import limits, materials, prototypes

TAG = 'open_painter_geometry_effect'

# spacing: minimum centre distance as a fraction of Size (0 allows overlap).
# tilt: maximum random lean off the surface normal, degrees.
# scale: uniform random size range as fractions of Size.
# slope: exponent on the upward-facing term (0 ignores orientation).
# triangles: estimate per instance for the project limits.
# align: yaw follows the source's local X projected on the surface (wood grain)
# with this much jitter in degrees, instead of a fully random yaw.
KINDS = {
    'RUST': dict(spacing=.5, tilt=14, scale=(.35, 1.8), slope=0, triangles=192, flat=True, align=None),
    'MOSS': dict(spacing=.3, tilt=10, scale=(.7, 1.4), slope=.6, triangles=260, flat=False, align=None),
    'SNOW': dict(spacing=.42, tilt=2, scale=(.5, 2.2), slope=2.5, triangles=320, flat=False, align=None),
    'SPLINTERS': dict(spacing=.4, tilt=4, scale=(.5, 1.8), slope=0, triangles=128, flat=True, align=14),
    'PEEL': dict(spacing=.6, tilt=5, scale=(.5, 1.5), slope=0, triangles=192, flat=True, align=None),
}


def _thin(positions, minimum, rng):
    """Greedy blue-noise thinning: keep points at least ``minimum`` apart."""
    if minimum <= 0 or len(positions) < 2:
        return np.ones(len(positions), bool)
    order = rng.permutation(len(positions))
    cells = {}
    keep = np.zeros(len(positions), bool)
    for index in order:
        cell = tuple(np.floor(positions[index] / minimum).astype(np.int64))
        near = False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for other in cells.get((cell[0] + dx, cell[1] + dy, cell[2] + dz), ()):
                        if np.linalg.norm(positions[other] - positions[index]) < minimum:
                            near = True
                            break
                    if near:
                        break
                if near:
                    break
            if near:
                break
        if not near:
            keep[index] = True
            cells.setdefault(cell, []).append(index)
    return keep


def scatter(ts, obj, mask, density, seed, replacing=None, kind='RUST', size=None):
    """Return local positions and normals; kind settings add slope bias and spacing."""
    if density < 0:
        raise ValueError('Density cannot be negative')
    settings = KINDS.get(kind, KINDS['RUST'])
    values = pixels(mask)[..., 0]
    with targets(ts) as entries:
        target = next((entry for entry in entries if entry['object'] == obj), None)
        if target is None:
            raise ValueError('Effect source must be assigned to the texture set')
        triangles = target['triangles']
        indices = np.asarray([tuple(t.vertices) for t in triangles])
        coords = target['positions'][indices]
        areas = np.linalg.norm(np.cross(coords[:, 1] - coords[:, 0], coords[:, 2] - coords[:, 0]), axis=1) * .5
        total = areas.sum()
        count = int(np.ceil(total * density))
        if count > 4_000_000:
            raise ValueError('Density times surface area exceeds four million candidate points')
        if not count or total <= 1e-12:
            return np.empty((0, 3), np.float32), np.empty((0, 3), np.float32)
        rng = np.random.default_rng(seed)
        selected = rng.choice(len(triangles), count, p=areas / total)
        root = np.sqrt(rng.random(count))
        second = rng.random(count)
        weights = np.stack((1 - root, root * (1 - second), root * second), axis=1)
        loops = np.asarray([tuple(triangles[i].loops) for i in selected])
        uv = np.sum(target['uv'][loops] * weights[..., None], axis=1)
        probability = sample_uv(values[..., None], uv[:, 0], uv[:, 1])[..., 0]
        normal = np.sum(target['world_normals'][loops] * weights[..., None], axis=1)
        normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-8)
        if settings['slope']:
            # Snow settles and moss thrives on faces that look up; the mask still rules.
            probability = probability * np.clip(normal[:, 2], 0, 1) ** settings['slope']
        keep = rng.random(count) < np.clip(probability, 0, 1)
        positions = np.sum(coords[selected] * weights[..., None], axis=1)[keep]
        normal = normal[keep]
        if size and settings['spacing']:
            thinned = _thin(positions, settings['spacing'] * size, rng)
            positions, normal = positions[thinned], normal[thinned]
        # Limits apply to the instances that survive the mask and spacing, not
        # to the candidate points the mask rejected.
        limits.check(ts.id_data, len(positions), len(positions) * settings['triangles'], replacing=replacing)
        inverse = np.asarray(obj.matrix_world.inverted_safe())
        local_positions = positions @ inverse[:3, :3].T + inverse[:3, 3]
        local_normals = normal @ np.asarray(obj.matrix_world)[:3, :3]
        local_normals /= np.maximum(np.linalg.norm(local_normals, axis=1, keepdims=True), 1e-8)
        return local_positions, local_normals


def _orientations(normals, rng, settings):
    """Euler per point: surface alignment, yaw (random or grain-aligned), small lean."""
    result = np.empty((len(normals), 3), np.float32)
    lean = math.radians(settings['tilt'])
    jitter = math.radians(settings['align']) if settings.get('align') is not None else None
    for index, normal in enumerate(normals):
        rotation = Vector(normal).to_track_quat('Z', 'Y')
        if jitter is None:
            yaw = rng.uniform(0, math.tau)
        else:
            # Grain direction: local +X projected onto the surface, in the
            # frame the track quaternion established, plus jitter and flips.
            local_x = rotation.inverted() @ Vector((1, 0, 0))
            yaw = math.atan2(local_x.y, local_x.x) - math.pi / 2 + rng.uniform(-jitter, jitter)
            yaw += math.pi if rng.random() < .5 else 0
        rotation = rotation @ Quaternion((0, 0, 1), yaw)
        if lean:
            axis = Vector((math.cos(a := rng.uniform(0, math.tau)), math.sin(a), 0))
            rotation = rotation @ Quaternion(axis, rng.uniform(0, lean))
        result[index] = rotation.to_euler()[:]
    return result


def _prototype_objects(kind, seed, material, created):
    """Data-only variant objects the node group instances by index."""
    objects = []
    for vertices, faces, uvs, smooth, name, rim in prototypes.variants(kind, seed):
        mesh = bpy.data.meshes.new('OP Effect Prototype')
        created.append(mesh)
        mesh.from_pydata(np.asarray(vertices, float).tolist(), [], [tuple(face) for face in faces])
        if uvs:
            layer = mesh.uv_layers.new(name='UVMap')
            flat = [coordinate for face in uvs for corner in face for coordinate in corner]
            layer.data.foreach_set('uv', flat)
        if rim is not None:
            attribute = mesh.attributes.new('op_rim', 'FLOAT', 'POINT')
            attribute.data.foreach_set('value', np.asarray(rim, np.float32).tolist())
        if smooth:
            mesh.polygons.foreach_set('use_smooth', [True] * len(mesh.polygons))
        mesh.materials.append(material)
        mesh.update()
        obj = bpy.data.objects.new('OP ' + kind.title() + ' · ' + name, mesh)
        created.append(obj)
        obj.use_fake_user = True  # data-only: Object Info can read it without linking it to the scene
        objects.append(obj)
    return objects


def _node_group(kind, variant_objects, created):
    group = bpy.data.node_groups.new('OP ' + kind + ' Instances', 'GeometryNodeTree')
    created.append(group)
    group.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    group.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    nodes, links = group.nodes, group.links
    input_node = nodes.new('NodeGroupInput')
    output_node = nodes.new('NodeGroupOutput')
    join = nodes.new('GeometryNodeJoinGeometry')
    for prototype in variant_objects:
        info = nodes.new('GeometryNodeObjectInfo')
        info.inputs['Object'].default_value = prototype
        info.transform_space = 'ORIGINAL'
        wrap = nodes.new('GeometryNodeGeometryToInstance')
        links.new(info.outputs['Geometry'], wrap.inputs['Geometry'])
        links.new(wrap.outputs['Instances'], join.inputs['Geometry'])
    instance = nodes.new('GeometryNodeInstanceOnPoints')
    instance.inputs['Pick Instance'].default_value = True
    for name, socket, data_type in (('op_rotation', 'Rotation', 'FLOAT_VECTOR'), ('op_scale', 'Scale', 'FLOAT_VECTOR'),
                                    ('op_variant', 'Instance Index', 'INT')):
        attribute = nodes.new('GeometryNodeInputNamedAttribute')
        attribute.data_type = data_type
        attribute.inputs['Name'].default_value = name
        links.new(attribute.outputs['Attribute'], instance.inputs[socket])
    links.new(input_node.outputs['Geometry'], instance.inputs['Points'])
    links.new(join.outputs['Geometry'], instance.inputs['Instance'])
    links.new(instance.outputs['Instances'], output_node.inputs['Geometry'])
    return group


def create(ts, obj, mask, kind='RUST', density=30, size=.02, seed=1, lift=0, material=None, replacing=None):
    if kind not in KINDS or size <= 0:
        raise ValueError('Choose a supported geometry effect and a positive instance size')
    settings = KINDS[kind]
    positions, normals = scatter(ts, obj, mask, density, seed, replacing, kind, size)
    if not len(positions):
        return None
    triangles = len(positions) * settings['triangles']
    limits.check(ts.id_data, len(positions), triangles, replacing=replacing)
    identifier = new_id()
    created = []
    rng = np.random.default_rng(seed * 7 + 3)
    try:
        owned_material = material or materials.create(kind, ts=ts)
        if material is None:
            created.append(owned_material)
        variant_objects = _prototype_objects(kind, seed, owned_material, created)
        point_mesh = bpy.data.meshes.new('OP Effect Points')
        created.append(point_mesh)
        point_mesh.from_pydata((positions + normals * lift).tolist(), [], [])
        count = len(positions)
        rotation = point_mesh.attributes.new('op_rotation', 'FLOAT_VECTOR', 'POINT')
        rotation.data.foreach_set('vector', _orientations(normals, rng, settings).ravel())
        low, high = settings['scale']
        # Many small pieces and a few large ones, as debris distributes.
        uniform = low + (high - low) * rng.random(count) ** 1.6
        uniform = uniform * size
        # Flat prototypes vary their footprint anisotropically; clumps stay proportional.
        aspect = rng.uniform(.75, 1.3, count) if settings['flat'] else np.ones(count)
        scales = np.stack((uniform * aspect, uniform / aspect, uniform), 1).astype(np.float32)
        scale = point_mesh.attributes.new('op_scale', 'FLOAT_VECTOR', 'POINT')
        scale.data.foreach_set('vector', scales.ravel())
        variant = point_mesh.attributes.new('op_variant', 'INT', 'POINT')
        variant.data.foreach_set('value', rng.integers(0, len(variant_objects), count).tolist())
        random = point_mesh.attributes.new('op_random', 'FLOAT', 'POINT')
        random.data.foreach_set('value', rng.random(count).astype(np.float32).tolist())
        helper = bpy.data.objects.new('OP ' + kind + ' · ' + obj.name, point_mesh)
        created.append(helper)
        ts.id_data.collection.objects.link(helper)
        helper.parent = obj
        group = _node_group(kind, variant_objects, created)
        helper.modifiers.new('Open Painter Instances', 'NODES').node_group = group
        for item in created:
            item[TAG] = 'INSTANCE_HELPER' if item == helper else 'OWNED_RESOURCE'
            item['effect_owner'] = identifier
        helper['instance_count'] = count
        helper['estimated_triangles'] = triangles
        helper['source_texture_set'] = ts.id
        helper['kind'] = kind
        helper['density'] = density
        helper['size'] = size
        helper['seed'] = seed
        helper['lift'] = lift
        helper['mask_image'] = mask.name
        helper['variants'] = len(variant_objects)
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


def remove(helper):
    if helper.get(TAG) != 'INSTANCE_HELPER':
        raise ValueError('Select an Open Painter effect helper')
    owner = helper.get('effect_owner')
    bpy.data.objects.remove(helper, do_unlink=True)
    shared = any(obj.get('effect_owner') == owner and obj.get(TAG) == 'INSTANCE_HELPER' for obj in bpy.data.objects)
    if shared:
        return
    # Remove the managed node group before the prototypes it references, then
    # the prototype meshes, and finally materials nothing uses any more.
    for group in list(bpy.data.node_groups):
        if group.get('effect_owner') == owner and group.get(TAG) and group.users == 0:
            bpy.data.node_groups.remove(group)
    for obj in list(bpy.data.objects):
        if obj.get('effect_owner') == owner and obj.get(TAG) == 'OWNED_RESOURCE':
            obj.use_fake_user = False
            if obj.users == 0:
                bpy.data.objects.remove(obj, do_unlink=True)
    for collection in (bpy.data.meshes, bpy.data.node_groups, bpy.data.materials):
        for item in list(collection):
            if item.get('effect_owner') == owner and item.get(TAG) and item.users == 0:
                collection.remove(item)
    for image in list(bpy.data.images):
        if image.get('open_painter_geometry_texture') and image.users == 0:
            bpy.data.images.remove(image)


def regenerate(ts, helper):
    if helper.get('frozen'):
        raise ValueError('Unfreeze the helper before regenerating')
    mask = bpy.data.images.get(helper.get('mask_image', ''))
    if not mask or not helper.parent:
        raise ValueError('Effect source or published mask is missing')
    # Replacing first would lose a good result on failure. Reserve against the
    # existing result conservatively until the replacement succeeds.
    replacement = create(ts, helper.parent, mask, helper['kind'], helper['density'], helper['size'],
                         helper['seed'], helper['lift'], replacing=helper)
    remove(helper)
    return replacement
