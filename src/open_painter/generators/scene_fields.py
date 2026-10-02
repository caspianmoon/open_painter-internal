"""Art-directed scene fields rasterized to persistent grayscale UV textures.

``compute_steps`` is the analysis as a cooperative generator (geometry is read
in the first step while the depsgraph is current; the rest is numpy in row
strips); ``compute`` drains it and is the reference. ``publish`` swaps the
last-good image and provenance. ``evaluate`` samples the published image; a
LIVE field whose inputs changed asks ``evaluation.field_jobs`` for a new
analysis instead of blocking the evaluation, unless no timer runs (background
scripts), where it stays synchronous.
"""
import numpy as np
from ..geometry import influencers, queries
from ..evaluation.blend_modes import sample_uv
from ..evaluation import metrics

QUERY_KINDS = frozenset({'DISTANCE', 'CONTACT', 'EXPOSURE', 'PROXIMITY'})
LIVE_KINDS = frozenset({'CONVEXITY', 'CONCAVITY', 'EDGE'})
RESOLUTIONS = (64, 128, 256, 512, 1024)
STRIP = 64


def signature(ts, field):
    from ..geometry.provenance import record
    return (record(ts, 'SCENE_FIELD'), influencers.signature(ts, field))


def _attribute(ts, field, n):
    from ..geometry.snapshots import targets
    from ..geometry.uv_rasterizer import interpolate, rasterize
    result = np.zeros((n,n),np.float32)
    with targets(ts) as meshes:
        meshes = list(meshes)
        # The field's object narrows the attribute to one of the set's own meshes (the Material
        # Director names one). Any other object is an influencer a previous kind left behind (a
        # Contact field's bolt); the panel hides it for attributes, so it must not hide the meshes.
        source = getattr(field, 'object', None)
        if source is not None and not any(target['object'] == source for target in meshes):
            source = None
        for target in meshes:
            mesh, obj = target['mesh'], target['object']
            if source is not None and obj != source:
                continue
            if field.kind == 'VERTEX_GROUP':
                group = obj.vertex_groups.get(field.attribute)
                if group is None:
                    raise ValueError(f'{obj.name}: missing vertex group {field.attribute}')
                values = np.zeros(len(mesh.vertices),np.float32)
                for vertex in mesh.vertices:
                    values[vertex.index] = next((g.weight for g in vertex.groups if g.group == group.index), 0)
                domain = 'POINT'
            else:
                attribute = mesh.attributes.get(field.attribute)
                if attribute is None or attribute.domain not in {'POINT','CORNER','FACE'}:
                    raise ValueError(f'{obj.name}: missing supported point/corner/face attribute {field.attribute}')
                domain = attribute.domain
                if attribute.data_type == 'FLOAT':
                    values = np.empty(len(attribute.data),np.float32)
                    attribute.data.foreach_get('value', values)
                elif attribute.data_type in {'FLOAT_COLOR','BYTE_COLOR'}:
                    colors = np.empty((len(attribute.data),4),np.float32)
                    attribute.data.foreach_get('color',colors.ravel())
                    values = colors[:,:3] @ np.array((.2126,.7152,.0722),np.float32)
                else:
                    raise ValueError(f'{obj.name}: attribute must be float or color')
            from ..native_bridge.lifecycle import cpu_library
            lib = cpu_library()
            if lib is not None and hasattr(lib, 'op_uv_raster_interpolate') and values.dtype == np.float32:
                # One native pass: each triangle's three corner values interpolated into the map,
                # as the samples below are (a face's value repeated on its corners).
                from ..native_bridge.geometry import raster_interpolate
                if domain == 'FACE':
                    corners = np.repeat(values[target['tri_polygons']][:, None], 3, axis=1)
                else:
                    corners = values[target['tri_vertices'] if domain == 'POINT' else target['tri_loops']]
                raster_interpolate(lib, target['uv'][target['tri_loops']], n,
                                   [(corners[..., None], result.reshape(n, n, 1))])
                continue
            rows, cols, tri, weights = rasterize(target['uv'][target['tri_loops']], n)
            if not len(rows):
                continue
            if domain == 'FACE':
                corners = np.repeat(values[target['tri_polygons'][tri]][:, None], 3, axis=1)
            else:
                corners = values[(target['tri_vertices'] if domain == 'POINT' else target['tri_loops'])[tri]]
            # Interpolation expects a component axis, even for scalar weights.
            # Passing (texels, 3) broadcasts into a quadratic texels-by-texels array.
            result[rows, cols] = interpolate(weights, corners[..., None])[:, 0]
    return result


def _direction(field):
    direction = np.asarray(field.direction, np.float32)
    length = np.linalg.norm(direction)
    if length < 1e-6:
        raise ValueError('Scene field direction cannot be zero')
    return direction / length


def _query(ts, field, positions, normals, valid, direction):
    """Batched influencer queries: (value rows for valid texels)."""
    from ..geometry import gn_queries
    objects = influencers.objects(ts, field)
    if not objects:
        raise ValueError('Choose an existing external mesh or collection for scene analysis')
    radii = influencers.radii(objects, field.distance)
    points, point_normals = positions[valid], normals[valid]
    metrics.count('scene_query_samples', int(valid.sum()))
    kind = field.kind
    try:
        target = gn_queries.combined_target(ts.id_data, objects, radii, field.distance)
        if target is None:
            raise ValueError('Influencers contain no evaluated triangles')
        if kind == 'EXPOSURE':
            value, _ = gn_queries.evaluate(ts.id_data, target, points, kind, field.distance, point_normals, direction)
            return value
        distances, near_radius = gn_queries.evaluate(ts.id_data, target, points, kind, max(field.distance, radii and max(radii.values()) or 0.0))
        metrics.count('scene_query_batched')
    except ValueError:
        raise
    except Exception as error:   # any node/socket mismatch: the per-point reference path
        from ..utils.logging import logger
        logger.warning('Geometry Nodes field query unavailable (%s); using BVH queries', error)
        metrics.count('scene_query_fallback')
        with queries.acceleration(ts.id_data, objects) as trees:
            if not trees:
                raise ValueError('Influencers contain no evaluated triangles')
            if kind == 'EXPOSURE':
                return queries.exposure(trees, points, point_normals, direction, field.distance)
            maximum = max(field.distance, max(radii.values()) if radii else 0.0)
            distances, which = queries.nearest(trees, points, maximum)
            per_tree = np.array([radii[obj.name_full] for obj in objects], np.float32)
            near_radius = np.where(which >= 0, per_tree[np.maximum(which, 0)], field.distance).astype(np.float32)
    if kind == 'PROXIMITY':
        return np.clip(1 - distances / np.maximum(near_radius, 1e-9), 0, 1).astype(np.float32)
    normalized = np.clip(distances / field.distance, 0, 1)
    return normalized if kind == 'DISTANCE' else 1 - normalized


def compute_steps(ts, field):
    """Analysis as a generator yielding progress (0..1); returns the (n, n) float32 field."""
    from ..painting.projection import geometry
    n = int(field.resolution)
    if n not in RESOLUTIONS:
        raise ValueError('Scene analysis supports bounded 64…1024 grids')
    if field.kind in QUERY_KINDS and n > 512:
        raise ValueError('Object queries analyse at 512 or less; choose a smaller Analysis Resolution')
    if field.kind in {'ATTRIBUTE','VERTEX_GROUP'}:
        yield 0.5
        return np.clip(_attribute(ts,field,n),0,1)
    if field.kind in LIVE_KINDS:
        return (yield from _live_curvature_steps(ts, field, n))
    positions, normals, valid = geometry(ts,n,coverage=True)
    normals /= np.maximum(np.linalg.norm(normals,axis=-1,keepdims=True),1e-8)
    direction = _direction(field)
    yield 0.1
    kind = field.kind
    if kind in QUERY_KINDS:
        value = np.zeros((n, n), np.float32)
        value[valid] = _query(ts, field, positions, normals, valid, direction)
        yield 0.9
        return np.asarray(value * valid, np.float32)
    value = np.zeros((n, n), np.float32)
    origin = None
    if kind in {'HEIGHT','HEAT','MOISTURE','SOOT'}:
        if field.object is None:
            raise ValueError('Scene field reference object is missing')
        origin = np.asarray(field.object.matrix_world.translation, np.float32)
    ground = 0.0
    if kind == 'GROUND':
        ground_object = getattr(field, 'ground_object', None)
        ground = float((np.asarray(ground_object.matrix_world.translation, np.float32) @ direction)
                       if ground_object is not None else 0.0)
    for start in range(0, n, STRIP):
        stop = min(start + STRIP, n)
        rows_p, rows_n = positions[start:stop], normals[start:stop]
        if kind in {'UP','DOWN'}:
            strip = np.clip(rows_n @ direction * (1 if kind == 'UP' else -1), 0, 1)
        elif kind == 'HEIGHT':
            lo, hi = field.bounds
            if hi <= lo:
                raise ValueError('Height bounds must be increasing')
            strip = np.clip(((rows_p - origin) @ direction - lo) / (hi - lo), 0, 1)
        elif kind in {'HEAT', 'MOISTURE', 'SOOT'}:
            relative = rows_p - origin
            strip = np.clip(1 - np.linalg.norm(relative, axis=-1) / field.distance, 0, 1)
            if kind == 'SOOT':
                strip *= np.clip((relative @ direction) / field.distance, 0, 1)
        elif kind == 'WORLD_HEIGHT':
            lo, hi = field.bounds
            if hi <= lo:
                raise ValueError('Height bounds must be increasing')
            strip = np.clip((rows_p @ direction - lo) / (hi - lo), 0, 1)
        elif kind == 'GROUND':
            # 1 at the ground plane, 0 at Distance above it: mud accumulates low.
            strip = np.clip(1 - (rows_p @ direction - ground) / field.distance, 0, 1)
        else:
            raise ValueError(f'Unknown scene field {kind}')
        value[start:stop] = strip
        yield 0.1 + 0.8 * stop / n
    return np.asarray(value * valid, np.float32)


def _live_curvature_steps(ts, field, n):
    """CONVEXITY / CONCAVITY from per-vertex turning; EDGE adds crease lines."""
    from math import radians
    from ..geometry.snapshots import targets
    from ..geometry import live_curvature
    from ..baking.maps.curvature import prepare_creases, rasterize_steps
    radius = float(field.distance)
    kind = field.kind
    with targets(ts) as meshes:
        # No yields while evaluated meshes are alive; density per vertex is cheap.
        # The live edge threshold is fixed: the bake setting it once followed
        # was retired with the geometric edge-set bake.
        densities = [live_curvature.vertex_density(target, radius, radians(20.0) if kind == 'EDGE' else None)
                     for target in meshes]
        arrays = [{key: target[key] for key in ('tri_vertices', 'tri_loops', 'uv')} for target in meshes]
        creases = []
        if kind == 'EDGE':
            width = max(1.0, radius * _texels_per_unit(meshes, n))
            creases = [prepare_creases(target['mesh'], target['object'].matrix_world, target['slots'], n,
                                       20.0, width) for target in meshes]
    yield 0.1
    result = np.zeros((n, n), np.float32)
    covered = np.zeros((n, n), bool)
    total = max(1, sum(len(a['tri_vertices']) for a in arrays))
    done = 0
    from ..geometry.uv_rasterizer import interpolate, rasterize
    for (density, _), arrays_of in zip(densities, arrays):
        tri_vertices, tri_loops, uv = arrays_of['tri_vertices'], arrays_of['tri_loops'], arrays_of['uv']
        for start in range(0, len(tri_vertices), live_curvature.CHUNK):
            stop = min(start + live_curvature.CHUNK, len(tri_vertices))
            rows, cols, tri, weights = rasterize(uv[tri_loops[start:stop]], n)
            if len(rows):
                corners = density[tri_vertices[start:stop][tri]]
                result[rows, cols] = interpolate(weights, corners[..., None])[:, 0]
                covered[rows, cols] = True
            done += stop - start
            yield 0.1 + 0.6 * done / total
    rel = live_curvature.relative(result, covered)
    value = live_curvature.signed_to_unit(rel, -1.0 if kind == 'CONCAVITY' else 1.0)
    for index, prepared in enumerate(creases):
        steps = rasterize_steps(prepared)
        while True:
            try:
                yield 0.7 + 0.3 * (index + next(steps)) / len(creases)
            except StopIteration as lines:
                np.maximum(value, lines.value, out=value)
                break
    return np.asarray(value * covered, np.float32)


def _texels_per_unit(meshes, n):
    """Texels per world unit from the assigned triangles' UV and world areas."""
    world = uv = 0.0
    for target in meshes:
        xyz = target['positions'][target['tri_vertices']]
        world += float(np.linalg.norm(np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0]), axis=1).sum() * .5)
        st = target['uv'][target['tri_loops']]
        a, b = st[:, 1] - st[:, 0], st[:, 2] - st[:, 0]
        uv += float(np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]).sum() * .5)
    return n * np.sqrt(uv / max(world, 1e-12))


@metrics.measured('scene_analysis')
def compute(ts, field):
    steps = compute_steps(ts, field)
    while True:
        try:
            next(steps)
        except StopIteration as done:
            return done.value


def publish(ts, mask, value, token):
    """Swap in the analysed raster as the field's last-good image with its provenance."""
    from ..core import image_manager as images
    field = mask.influence
    n = value.shape[0]
    rgba = np.ones((n,n,4),np.float32); rgba[...,:3]=value[...,None]
    image = images.create(f'{ts.name} · {mask.name} Analysis',n)
    images.write(image,rgba)
    previous = field.image
    field.image = image
    field.provenance = token
    field.error = ''
    if previous and previous.users <= 1:
        import bpy
        bpy.data.images.remove(previous)
    return value


def update(ts, mask):
    field = mask.influence
    token = repr(signature(ts,field))
    try:
        value = compute(ts,field)
    except Exception as exc:
        field.error = str(exc)
        raise
    return publish(ts, mask, value, token)


def falloff(value, kind):
    if kind == 'SMOOTH':
        return value * value * (3 - 2 * value)
    if kind == 'SQUARED':
        return value * value
    if kind == 'ROOT':
        return np.sqrt(np.clip(value, 0, None))
    return value


def evaluate(ts, mask, u, v, cache):
    from ..evaluation.image_snapshots import read
    from ..evaluation import field_jobs
    field = mask.influence
    if field.mode == 'FROZEN' and not field.image:
        raise ValueError('Frozen analysis has no texture; update analysis first')
    desired = repr(signature(ts,field)) if field.mode == 'LIVE' or not field.image else field.provenance
    if not field.image or (field.mode == 'LIVE' and desired != field.provenance):
        # Mutate only the derived analysis resource on the main-thread author,
        # never the immutable parameters used by the compositor.
        scene = ts.id_data
        author = next(t for t in scene.open_painter.texture_sets if t.id == ts.id)
        original = next(m for layer in author.layers for m in layer.masks if m.id == mask.id)
        if original.influence.provenance != desired or not original.influence.image:
            if field.mode == 'LIVE' and field_jobs.active():
                # Keep the last published raster; the job publishes and invalidates.
                field_jobs.request(scene, author, original, desired)
                if not original.influence.image:
                    original.influence.error = 'Analysing…'
                    return np.zeros(np.broadcast(u, v).shape, np.float32)
            else:
                update(author,original)
        image = original.influence.image
    else:
        image = field.image
    key = ('scene_image',image.as_pointer(),image.get('open_painter_pixel_revision',0))
    if key not in cache:
        from ..evaluation.uv_gutters import padded
        # Extend only the disposable sampling buffer. Source pixels/provenance
        # stay unchanged, and CPU evaluation and shader boundaries read the
        # same occupied values with no false inverted coverage at UV seams.
        cache[key] = padded(ts, read(cache,image))
    return falloff(sample_uv(cache[key],u,v)[...,0], getattr(field, 'falloff', 'LINEAR'))
