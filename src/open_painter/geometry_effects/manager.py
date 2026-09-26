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
    # Material → Geometry kinds (0.7.0): bolts sit square, scales overlap downward, rocks and bark scatter.
    'BOLT': dict(spacing=1.0, tilt=0, scale=(.9, 1.1), slope=0, triangles=60, flat=False, align=None),
    'SCALE': dict(spacing=.55, tilt=2, scale=(.85, 1.15), slope=0, triangles=64, flat=True, align=12),
    'ROCK': dict(spacing=.8, tilt=8, scale=(.4, 1.6), slope=.4, triangles=80, flat=False, align=None),
    'BARK': dict(spacing=.5, tilt=3, scale=(.8, 1.3), slope=0, triangles=64, flat=True, align=10),
}


_NEIGHBOURS = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)]


def _thin(positions, minimum, rng, limit=None):
    """Greedy blue-noise thinning: keep points at least ``minimum`` apart.

    The pass is sequential by nature (each decision depends on the points kept
    before it), so the per-point work runs on plain Python numbers prepared in
    one vectorised step rather than on per-point numpy arrays. ``limit`` stops
    the pass once the project's budget of points is kept, without walking the
    remaining candidates (where a large masked area used to freeze Blender).
    The candidates are visited in random order, so the points kept spread over
    the whole area. Returns (keep, capped): capped when candidates were left.
    """
    if minimum <= 0 or len(positions) < 2:
        return np.ones(len(positions), bool), False
    order = rng.permutation(len(positions))
    keep = np.zeros(len(positions), bool)
    cells = np.floor(positions / minimum).astype(np.int64).tolist()
    coords = positions.tolist()
    occupied = {}
    threshold = minimum * minimum
    kept = 0
    capped = False
    for index in order.tolist():
        if limit is not None and kept >= limit:
            capped = True
            break
        cx, cy, cz = cells[index]
        x, y, z = coords[index]
        near = False
        for dx, dy, dz in _NEIGHBOURS:
            for ox, oy, oz in occupied.get((cx + dx, cy + dy, cz + dz), ()):
                if (x - ox) * (x - ox) + (y - oy) * (y - oy) + (z - oz) * (z - oz) < threshold:
                    near = True
                    break
            if near:
                break
        if not near:
            keep[index] = True
            occupied.setdefault((cx, cy, cz), []).append((x, y, z))
            kept += 1
    return keep, capped


def field_of(mask):
    """A density raster from an Image or a 2-D array."""
    if isinstance(mask, np.ndarray):
        return mask if mask.ndim == 2 else mask[..., 0]
    return pixels(mask)[..., 0]


def scatter(ts, obj, mask, density, seed, replacing=None, kind='RUST', size=None):
    """Return world positions and normals; kind settings add slope bias and spacing."""
    result = scatter_points(ts, obj, mask, density, seed, replacing, kind, size)
    return result['positions'], result['normals']


def scatter_points(ts, obj, mask, density, seed, replacing=None, kind='RUST', size=None, direction=None, slope=True):
    """World positions, normals and UVs of the instances (and, with a ``direction`` field, the
    world direction each one faces); kind settings add spacing and, unless ``slope`` is off
    (a material placement already knows which faces it wants), the kind's upward-facing bias."""
    if density < 0:
        raise ValueError('Density cannot be negative')
    settings = KINDS.get(kind, KINDS['RUST'])
    values = field_of(mask)
    with targets(ts) as entries:
        target = next((entry for entry in entries if entry['object'] == obj), None)
        if target is None:
            raise ValueError('Effect source must be assigned to the texture set')
        indices = target['tri_vertices']
        coords = target['positions'][indices]
        areas = np.linalg.norm(np.cross(coords[:, 1] - coords[:, 0], coords[:, 2] - coords[:, 0]), axis=1) * .5
        total = areas.sum()
        count = int(np.ceil(total * density))
        if count > 4_000_000:
            raise ValueError('Density times surface area exceeds four million candidate points')
        if not count or total <= 1e-12:
            return dict(positions=np.empty((0, 3), np.float32), normals=np.empty((0, 3), np.float32),
                        uv=np.empty((0, 2), np.float32), facing=None)
        rng = np.random.default_rng(seed)
        selected = rng.choice(len(indices), count, p=areas / total)
        root = np.sqrt(rng.random(count))
        second = rng.random(count)
        weights = np.stack((1 - root, root * (1 - second), root * second), axis=1)
        loops = target['tri_loops'][selected]
        uv = np.sum(target['uv'][loops] * weights[..., None], axis=1)
        probability = sample_uv(values[..., None], uv[:, 0], uv[:, 1])[..., 0]
        normal = np.sum(target['world_normals'][loops] * weights[..., None], axis=1)
        normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-8)
        if settings['slope'] and slope:
            # Snow settles and moss thrives on faces that look up; the mask still rules.
            probability = probability * np.clip(normal[:, 2], 0, 1) ** settings['slope']
        keep = rng.random(count) < np.clip(probability, 0, 1)
        positions = np.sum(coords[selected] * weights[..., None], axis=1)[keep]
        normal = normal[keep]
        uv_kept = uv[keep]
        facing = None
        if direction is not None:
            # A UV-space direction becomes a world direction through the triangle's tangent frame.
            st = target['uv'][loops[keep]].astype(np.float64)
            p = coords[selected][keep].astype(np.float64)
            e1, e2 = p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]
            f1, f2 = st[:, 1] - st[:, 0], st[:, 2] - st[:, 0]
            det = f1[:, 0] * f2[:, 1] - f1[:, 1] * f2[:, 0]
            det = np.where(np.abs(det) < 1e-12, 1e-12, det)
            tangent = (e1 * f2[:, 1:2] - e2 * f1[:, 1:2]) / det[:, None]
            bitangent = (e2 * f1[:, 0:1] - e1 * f2[:, 0:1]) / det[:, None]
            d = sample_uv(direction, uv_kept[:, 0], uv_kept[:, 1])
            facing = tangent * d[:, 0:1] + bitangent * d[:, 1:2]
            facing -= normal * np.sum(facing * normal, axis=1, keepdims=True)
            length = np.linalg.norm(facing, axis=1, keepdims=True)
            facing = np.where(length > 1e-8, facing / np.maximum(length, 1e-8), 0).astype(np.float32)
        capped = False
        if size and settings['spacing']:
            instances_left, triangles_left = limits.headroom(ts.id_data, replacing)
            limit = max(0, min(instances_left, triangles_left // settings['triangles']))
            if limit == 0 and len(positions):
                raise ValueError(f'The project\'s geometry effects already reach the {limits.MAX_TRIANGLES:,} '
                                 'estimated triangle limit: remove an effect before adding another')
            # A dense placement on a large area (a heavily aged surface) takes as many pieces as
            # the project's budget allows, spread over the area, rather than refusing.
            thinned, capped = _thin(positions, settings['spacing'] * size, rng, limit)
            positions, normal, uv_kept = positions[thinned], normal[thinned], uv_kept[thinned]
            if facing is not None:
                facing = facing[thinned]
        # Limits apply to the instances that survive the mask and spacing, not
        # to the candidate points the mask rejected.
        limits.check(ts.id_data, len(positions), len(positions) * settings['triangles'], replacing=replacing)
        return dict(positions=positions, normals=normal, uv=uv_kept.astype(np.float32), facing=facing, capped=capped)


def _orientations(normals, rng, settings, grain=Vector((1, 0, 0)), facing=None):
    """Euler per point: surface alignment, yaw (random, grain-aligned or toward a per-point
    ``facing`` direction with a little jitter and no flips), small lean."""
    result = np.empty((len(normals), 3), np.float32)
    lean = math.radians(settings['tilt'])
    jitter = math.radians(settings['align']) if settings.get('align') is not None else None
    for index, normal in enumerate(normals):
        rotation = Vector(normal).to_track_quat('Z', 'Y')
        if facing is not None and any(facing[index]):
            # The prototype's +Y (its hinge, its attached edge) turns toward the direction given.
            local = rotation.inverted() @ Vector(facing[index])
            yaw = math.atan2(local.y, local.x) - math.pi / 2 + rng.uniform(-1, 1) * math.radians(settings.get('facing_jitter', 8))
        elif jitter is None:
            yaw = rng.uniform(0, math.tau)
        else:
            # Grain direction: the source's X axis (``grain``) projected onto the
            # surface, in the frame the track quaternion established, plus jitter and flips.
            local_x = rotation.inverted() @ grain
            yaw = math.atan2(local_x.y, local_x.x) - math.pi / 2 + rng.uniform(-jitter, jitter)
            yaw += math.pi if rng.random() < .5 else 0
        rotation = rotation @ Quaternion((0, 0, 1), yaw)
        if lean:
            axis = Vector((math.cos(a := rng.uniform(0, math.tau)), math.sin(a), 0))
            rotation = rotation @ Quaternion(axis, rng.uniform(0, lean))
        result[index] = rotation.to_euler()[:]
    return result


def _collection_prototypes(collection):
    """The artist's own prototypes: the collection's mesh objects, each with the factor that
    brings its largest dimension to one unit so Size means the same as for built-in shapes."""
    objects = [obj for obj in collection.all_objects if obj.type == 'MESH' and len(obj.data.polygons)]
    if not objects:
        raise ValueError(f'{collection.name} has no mesh objects to instance')
    factors = [1.0 / max(max(obj.dimensions), 1e-6) for obj in objects]
    return objects, factors


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
        # Data-only: the node group's Object Info sockets hold the users that keep
        # these saved while a helper exists. No fake user, so a helper deleted with
        # X (never through remove()) leaves prototypes that orphan purge can reap.
        obj = bpy.data.objects.new('OP ' + kind.title() + ' · ' + name, mesh)
        created.append(obj)
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


def _surface_colors(ts, uv, n=512):
    """The set's Base Color under each instance, so a flake, chip or plate matches the
    surface it sits on (linear RGB, one per point)."""
    from . import sources
    if uv is None or not len(uv):
        return None
    raster = sources.channel_raster(ts, 'BASE_COLOR', min(n, int(ts.resolution)))
    return sample_uv(raster, uv[:, 0], uv[:, 1]).astype(np.float32)


def create(ts, obj, mask, kind='RUST', density=30, size=.02, seed=1, lift=0, material=None, replacing=None,
           placement=None, prototypes=None, match=None, follow=False, layer_id=''):
    """Scatter ``kind`` on ``obj``: from a mask image (the pre-0.10.1 call) or a ``placement``
    (``sources.Placement``: a density field, an optional facing direction, or painted spots).
    ``prototypes`` is a collection of the artist's own meshes instead of the built-in shapes;
    ``match`` (0–1) tints each instance toward the surface colour under it; ``follow`` lets the
    helper regenerate when the material changes."""
    if kind not in KINDS or size <= 0:
        raise ValueError('Choose a supported geometry effect and a positive instance size')
    settings = KINDS[kind]
    field = placement.density if placement is not None else mask
    facing = None
    if placement is not None and placement.spots:
        # One instance per painted spot: its centre, its normal, its size.
        positions = np.array([spot[0] for spot in placement.spots], np.float32)
        normals = np.array([spot[1] for spot in placement.spots], np.float32)
        diameters = np.array([spot[2] for spot in placement.spots], np.float32) * size
        uv = np.array([spot[3] for spot in placement.spots], np.float32)
        instances_left, triangles_left = limits.headroom(ts.id_data, replacing)
        if len(positions) > min(instances_left, triangles_left // settings['triangles']):
            raise ValueError(f'{len(positions)} spots exceed the project instance limit')
        capped = False   # painted spots are the artist's: all of them or an error, never a subset
    else:
        scattered = scatter_points(ts, obj, field, density, seed, replacing, kind, size,
                                   direction=placement.direction if placement is not None else None,
                                   slope=placement is None or placement.source != 'MATERIAL')
        positions, normals, uv, facing = scattered['positions'], scattered['normals'], scattered['uv'], scattered['facing']
        capped = scattered.get('capped', False)
        diameters = None
    if not len(positions):
        return None
    triangles = len(positions) * settings['triangles']
    limits.check(ts.id_data, len(positions), triangles, replacing=replacing)
    identifier = new_id()
    created = []
    rng = np.random.default_rng(seed * 7 + 3)
    if match is None:
        match = materials.MATCH.get(kind, 0.0)
    try:
        owned_material = material or materials.create(kind, ts=ts)
        if material is None:
            created.append(owned_material)
        if prototypes is not None:
            variant_objects, factors = _collection_prototypes(prototypes)
        else:
            variant_objects = _prototype_objects(kind, seed, owned_material, created)
            factors = [1.0] * len(variant_objects)
        point_mesh = bpy.data.meshes.new('OP Effect Points')
        created.append(point_mesh)
        # Points, lift and scale are world metres: the helper's world matrix is the
        # identity (parent inverse below), so a scaled or sheared source neither
        # resizes the instances nor shears them, and spacing stays comparable to size.
        point_mesh.from_pydata((positions + normals * lift).tolist(), [], [])
        count = len(positions)
        grain = (obj.matrix_world.to_3x3() @ Vector((1, 0, 0))).normalized()
        rotation = point_mesh.attributes.new('op_rotation', 'FLOAT_VECTOR', 'POINT')
        rotation.data.foreach_set('vector', _orientations(normals, rng, settings, grain, facing).ravel())
        variants = rng.integers(0, len(variant_objects), count)
        if diameters is not None:
            uniform = diameters
            aspect = np.ones(count)
        else:
            low, high = settings['scale']
            # Many small pieces and a few large ones, as debris distributes.
            uniform = (low + (high - low) * rng.random(count) ** 1.6) * size
            # Flat prototypes vary their footprint anisotropically; clumps stay proportional.
            aspect = rng.uniform(.75, 1.3, count) if settings['flat'] else np.ones(count)
        uniform = uniform * np.asarray(factors, np.float32)[variants]
        scales = np.stack((uniform * aspect, uniform / aspect, uniform), 1).astype(np.float32)
        scale = point_mesh.attributes.new('op_scale', 'FLOAT_VECTOR', 'POINT')
        scale.data.foreach_set('vector', scales.ravel())
        variant = point_mesh.attributes.new('op_variant', 'INT', 'POINT')
        variant.data.foreach_set('value', variants.tolist())
        random = point_mesh.attributes.new('op_random', 'FLOAT', 'POINT')
        random.data.foreach_set('value', rng.random(count).astype(np.float32).tolist())
        colors = None
        if match > 0 and placement is not None and placement.color is not None:
            colors = np.tile(np.asarray(placement.color, np.float32), (count, 1))
        elif match > 0:
            sample_uv_at = uv
            if placement is not None and placement.direction is not None and placement.color_offset and len(uv):
                # Read the colour a little way toward where the piece came from (a chip: the paint beside it).
                step = sample_uv(placement.direction, uv[:, 0], uv[:, 1]) * (placement.color_offset / placement.n)
                sample_uv_at = uv + step.astype(np.float32)
            colors = _surface_colors(ts, sample_uv_at)
        color = point_mesh.attributes.new('op_color', 'FLOAT_COLOR', 'POINT')
        rgba = np.ones((count, 4), np.float32)
        if colors is not None:
            rgba[:, :3] = colors[:, :3]
        rgba[:, 3] = match       # the material mixes toward op_color by this much
        color.data.foreach_set('color', rgba.ravel())
        helper = bpy.data.objects.new('OP ' + kind + ' · ' + obj.name, point_mesh)
        created.append(helper)
        ts.id_data.collection.objects.link(helper)
        helper.parent = obj
        helper.matrix_parent_inverse = obj.matrix_world.inverted()
        group = _node_group(kind, variant_objects, created)
        helper.modifiers.new('Open Painter Instances', 'NODES').node_group = group
        for item in created:
            item[TAG] = 'INSTANCE_HELPER' if item == helper else 'OWNED_RESOURCE'
            item['effect_owner'] = identifier
        helper['instance_count'] = count
        helper['estimated_triangles'] = triangles
        helper['capped'] = bool(capped)   # the project's budget stopped the placement
        helper['source_texture_set'] = ts.id
        helper['kind'] = kind
        helper['density'] = density
        helper['size'] = size
        helper['seed'] = seed
        helper['lift'] = lift
        helper['match'] = match
        helper['follow'] = follow
        helper['source'] = placement.source if placement is not None else 'MASK'
        helper['source_layer'] = placement.layer_id if placement is not None else layer_id
        helper['source_label'] = placement.label if placement is not None else 'the published mask'
        if prototypes is not None:
            helper['prototypes'] = prototypes
        if not isinstance(mask, np.ndarray) and mask is not None:
            helper['mask_image'] = mask   # an ID pointer follows renames; older files hold the name
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


def _image(reference):
    """The image an ID pointer or a legacy name property refers to, if it still exists."""
    if isinstance(reference, bpy.types.Image):
        return reference
    return bpy.data.images.get(reference) if isinstance(reference, str) and reference else None


def release_snapshot(reference):
    """Drop a published mask/height snapshot once no helper keeps it for Regenerate.

    Published images carry a fake user (they are packed, artist-visible data),
    so they outlive every helper unless released here. An image the artist has
    wired into something else keeps its users and stays.
    """
    image = _image(reference)
    if image is None or not (image.get('open_painter_published_layer') or image.get('open_painter_published_channel')):
        return
    for obj in bpy.data.objects:
        if obj.get(TAG) == 'INSTANCE_HELPER' and any(_image(obj.get(key)) == image for key in ('mask_image', 'source_image')):
            return
    if image.users <= int(image.use_fake_user):
        image.use_fake_user = False
        bpy.data.images.remove(image)


def remove(helper):
    if helper.get(TAG) != 'INSTANCE_HELPER':
        raise ValueError('Select an Open Painter effect helper')
    owner = helper.get('effect_owner')
    snapshots = [helper.get('mask_image'), helper.get('source_image')]
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
    # The displacement group sampled its snapshot; with it gone the image is free.
    for reference in snapshots:
        release_snapshot(reference)
    reap_orphans()


def reap_orphans():
    """Release owned data whose helper went away without remove(): deleted with X or
    with its source, or saved by a version whose prototypes carried a fake user, which
    Purge Unused Data honours. The node group goes first so the prototypes it
    instanced lose their last user."""
    live = {obj.get('effect_owner') for obj in bpy.data.objects if obj.get(TAG) == 'INSTANCE_HELPER'}
    def orphaned(item):
        return item.get(TAG) == 'OWNED_RESOURCE' and item.get('effect_owner') not in live and not item.library
    for group in list(bpy.data.node_groups):
        if orphaned(group) and group.users == 0:
            bpy.data.node_groups.remove(group)
    for obj in list(bpy.data.objects):
        if orphaned(obj):
            obj.use_fake_user = False
            if obj.users == 0:
                bpy.data.objects.remove(obj, do_unlink=True)
    for collection in (bpy.data.meshes, bpy.data.node_groups, bpy.data.materials):
        for item in list(collection):
            if orphaned(item) and item.users == 0:
                collection.remove(item)


def texture_set_of(ts, helper):
    """The set the helper was generated for, so a regenerate stays on its faces and
    keeps its attribution whichever set the panel has active; ``ts`` serves helpers
    from files that predate the record."""
    recorded = helper.get('source_texture_set')
    if not recorded:
        return ts
    match = next((s for s in ts.id_data.open_painter.texture_sets if s.id == recorded), None)
    if match is None:
        raise ValueError('The texture set this helper was generated for no longer exists')
    return match


def placement_of(ts, helper):
    """The placement a helper was made from, evaluated again on the material as it is now."""
    from . import sources
    source = helper.get('source', 'MASK')
    layer_id = helper.get('source_layer', '')
    layer = next((l for l in ts.layers if l.id == layer_id), None) if layer_id else None
    if source in {'MASK', 'SPOTS'} and layer is None:
        mask = _image(helper.get('mask_image'))
        if mask is None:
            raise ValueError('The layer this helper followed is gone and no published mask remains')
        return None, mask     # the layer is gone: the snapshot it left behind still regenerates
    return sources.placement(ts, helper['kind'], source, layer), None


def regenerate(ts, helper):
    if helper.get('frozen'):
        raise ValueError('Unfreeze the helper before regenerating')
    ts = texture_set_of(ts, helper)
    if not helper.parent:
        raise ValueError('Effect source is missing')
    placement, mask = placement_of(ts, helper)
    prototypes = helper.get('prototypes')
    prototypes = prototypes if isinstance(prototypes, bpy.types.Collection) else None
    # Replacing first would lose a good result on failure. Reserve against the
    # existing result conservatively until the replacement succeeds.
    replacement = create(ts, helper.parent, mask, helper['kind'], helper['density'], helper['size'],
                         helper['seed'], helper['lift'], replacing=helper, placement=placement, prototypes=prototypes,
                         match=helper.get('match'), follow=bool(helper.get('follow')), layer_id=helper.get('source_layer', ''))
    remove(helper)
    return replacement


def helpers_of(ts, scene=None, following=False):
    """The instance helpers generated for ``ts`` (optionally only those that follow the material)."""
    scene = scene or ts.id_data
    return [obj for obj in scene.objects if obj.get(TAG) == 'INSTANCE_HELPER' and obj.get('source_texture_set') == ts.id
            and obj.get('kind') in KINDS and (not following or obj.get('follow')) and not obj.get('frozen')]


def follow(ts):
    """Regenerate every helper of the set that follows the material; returns the new helpers."""
    result = []
    for helper in helpers_of(ts, following=True):
        replacement = regenerate(ts, helper)
        if replacement is not None:
            result.append(replacement)
    return result


_pending = {}      # (scene name, set id) -> monotonic deadline; one timer per key


def schedule_follow(ts, delay=.6):
    """Regenerate the set's following helpers once the edits settle (a slider drag re-drives
    the aging folder many times a second; the instances catch up ``delay`` after the last edit).
    Returns whether anything follows."""
    from time import monotonic
    if not helpers_of(ts, following=True):
        return False
    scene_name, set_id = ts.id_data.name, ts.id
    key = (scene_name, set_id)
    armed = key in _pending
    _pending[key] = monotonic() + delay
    if armed:
        return True

    def run():
        deadline = _pending.get(key)
        if deadline is None:
            return None
        remaining = deadline - monotonic()
        if remaining > 0:
            return remaining
        _pending.pop(key, None)
        scene_now = bpy.data.scenes.get(scene_name)
        set_now = next((s for s in scene_now.open_painter.texture_sets if s.id == set_id), None) if scene_now else None
        if set_now is not None:
            try:
                follow(set_now)
            except Exception:
                from ..utils.logging import logger
                logger.exception('Geometry effects could not follow the material')
        return None
    bpy.app.timers.register(run, first_interval=delay)
    return True


def follow_now(ts):
    """Run a pending follow immediately (tests, save)."""
    _pending.pop((ts.id_data.name, ts.id), None)
    return follow(ts)
