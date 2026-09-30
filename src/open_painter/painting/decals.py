"""Decal objects (0.9.6): an Empty is the decal; its matrix is the projection.

``OP_Layer.decal_object`` points at an Empty whose local XY plane (-0.5..0.5)
carries the layer's channel images and whose -Z is the projection direction.
Channels with ``projection == 'DECAL'`` sample their image at the decal-local
(x + 0.5, y + 0.5) of every texel's world position, within ``decal_depth`` of
the plane, on front-facing surfaces, with an optional soft border. Normal
images are rotated from the decal frame into the target's tangent frame. The
shader reads the same frame natively through a Texture Coordinate node bound
to the object plus the mirrored ``decal_axes`` uniforms, so moving, rotating,
scaling or duplicating the Empty in the viewport is the edit.
"""
import numpy as np

import bpy

from ..constants import OWNER

MARKER = 'open_painter_decal'      # on the Empty: the layer id it drives
PREFIX = 'OP_Decal_'
_linked = {}   # layer id -> (name, session_uid) of the Empty its pointer held while this session ran


def layers_of(ts):
    return [layer for layer in ts.layers if layer.type == 'FILL' and (layer.decal_object is not None or layer.decal_object_name)]


def forget():
    _linked.clear()


def lost_pointer(layer):
    """True when the layer's Empty was freed under it (Delete Global, the outliner, a script):
    Blender nulls the pointer without its update callback, so the name mirror survives and
    would otherwise re-link the layer to the next Empty that takes the name. A layer loaded
    or imported with a name and no pointer (its Empty is not in this file, or a module update
    re-created the layer while the Empty stayed) is not this."""
    if getattr(layer, 'decal_object', None) is not None:
        return False
    held = _linked.get(getattr(layer, 'id', ''))
    name = getattr(layer, 'decal_object_name', '')
    if held is None or held[0] != name:
        return False
    namesake = bpy.data.objects.get(name)
    if namesake is not None and namesake.session_uid == held[1]:
        return False
    return not any(obj.session_uid == held[1] for obj in bpy.data.objects)


def resolve(layer):
    obj = getattr(layer, 'decal_object', None)
    if obj is not None:
        _linked[layer.id] = (obj.name, obj.session_uid)
        return obj
    name = getattr(layer, 'decal_object_name', '')
    if not name or lost_pointer(layer):
        return None
    return bpy.data.objects.get(name)


def axes(obj):
    """Unit world axes (X, Y, Z rows) of the decal frame, and the world size along X/Y."""
    matrix = np.asarray(obj.matrix_world, np.float64)[:3, :3]
    lengths = np.linalg.norm(matrix, axis=0)
    unit = matrix / np.maximum(lengths, 1e-12)
    return unit.T.copy(), lengths


def aspect(obj):
    """Fraction of the display size the drawn picture spans along local X and Y. Blender keeps
    the displayed image's aspect (its longer side spans the display size, pixel size times
    ``display_aspect``); no image, or one without pixels yet, draws a square."""
    image = obj.data if obj.type == 'EMPTY' and obj.empty_display_type == 'IMAGE' else None
    if image is None or not image.size[0] or not image.size[1]:
        return 1.0, 1.0
    w = float(image.size[0]) * float(image.display_aspect[0])
    h = float(image.size[1]) * float(image.display_aspect[1])
    return (1.0, h / w) if w >= h else (w / h, 1.0)


def extent(obj):
    """Local X and Y spans of the drawn picture: display size scaled by the image aspect."""
    size = float(getattr(obj, 'empty_display_size', 1.0)) or 1.0
    ax, ay = aspect(obj)
    return size * ax, size * ay


def signature(layer):
    """Repr-stable transform identity of a decal layer (world matrix, image empty size and
    the aspect of the picture it draws)."""
    if getattr(layer, 'decal_inline', False):
        return ('inline', tuple(layer.decal_origin), tuple(layer.decal_extent), tuple(layer.decal_axes),
                float(layer.decal_depth), float(layer.decal_falloff), bool(layer.decal_facing))
    obj = resolve(layer)
    if obj is None:
        return None
    return (obj.name, tuple(round(v, 6) for row in obj.matrix_world for v in row),
            round(float(getattr(obj, 'empty_display_size', 1.0)), 6),
            tuple(round(float(v), 6) for v in getattr(obj, 'empty_image_offset', (0., 0.))),
            round(float(layer.decal_depth), 6), round(float(layer.decal_falloff), 6), bool(layer.decal_facing),
            tuple(round(v, 6) for v in aspect(obj)))


def local_coordinates(layer, positions):
    """Decal-local coordinates of world positions; the image spans -0.5..0.5 in local XY."""
    if getattr(layer, 'decal_inline', False):
        local = (np.asarray(positions) - np.asarray(layer.decal_origin)) @ np.asarray(layer.decal_axes).reshape(3, 3).T
        return local[..., 0] / layer.decal_extent[0] + .5, local[..., 1] / layer.decal_extent[1] + .5, local[..., 2]
    obj = resolve(layer)
    inverse = np.asarray(obj.matrix_world.inverted_safe(), np.float64)
    flat = positions.reshape(-1, 3).astype(np.float64)
    local = flat @ inverse[:3, :3].T + inverse[:3, 3]
    width, height = extent(obj)
    offset = np.asarray(getattr(obj, 'empty_image_offset', (-.5, -.5)), np.float64)
    # Image empties draw the image from offset*extent; -0.5 centres it on the origin.
    u = local[:, 0] / width - offset[0]
    v = local[:, 1] / height - offset[1]
    return u.reshape(positions.shape[:-1]), v.reshape(positions.shape[:-1]), local[:, 2].reshape(positions.shape[:-1])


def coverage(layer, positions, normals):
    """(u, v, coverage 0..1) of the decal on texels with these world positions and normals."""
    u, v, z = local_coordinates(layer, positions)
    obj = resolve(layer)
    world_axes, lengths = (np.asarray(layer.decal_axes).reshape(3, 3), np.ones(3)) if getattr(layer, 'decal_inline', False) else axes(obj)
    inside = ((u >= 0) & (u <= 1) & (v >= 0) & (v <= 1)).astype(np.float32)
    depth = float(layer.decal_depth)
    if depth > 0:
        # z is in the object's local units; depth is world distance along the decal's Z.
        inside *= (np.abs(z) * lengths[2] <= depth).astype(np.float32)
    else:
        inside *= (np.abs(z) * lengths[2] <= 1e-6).astype(np.float32)
    if layer.decal_facing:
        facing = normals.reshape(-1, 3).astype(np.float64) @ world_axes[2]
        inside *= (facing.reshape(positions.shape[:-1]) > 0).astype(np.float32)
    falloff = float(layer.decal_falloff)
    if falloff > 0:
        border = np.minimum(np.minimum(u, 1 - u), np.minimum(v, 1 - v)) / falloff
        inside *= np.clip(border, 0, 1).astype(np.float32)
    return u, v, inside


def sample(prop, layer, n, y0, y1, ts, geometry_cache, cache, image=None):
    """RGBA rows of a DECAL channel: the image sampled through the decal, alpha = coverage × image alpha."""
    from ..painting.projection import geometry
    from .projection import tangents
    from ..evaluation.channel_eval import sample_uv
    from ..evaluation.image_snapshots import read
    if 'geometry' in geometry_cache:
        positions, normals = geometry_cache['geometry']
    else:
        positions, normals = geometry(ts, n)
        geometry_cache['geometry'] = (positions, normals)
    obj = resolve(layer)
    image = image if image is not None else prop.image
    if (obj is None and not getattr(layer, 'decal_inline', False)) or not image:
        return np.array([0, 0, 0, 0], np.float32)
    u, v, cover = coverage(layer, positions[y0:y1], normals[y0:y1])
    key = (image.as_pointer(), 'linear')
    if key not in cache:
        cache[key] = read(cache, image, linear=True)
    rgba = sample_uv(cache[key], u.astype(np.float32), v.astype(np.float32), prop.filtering, 'NONE')
    rgba = np.array(rgba, np.float32, copy=True)
    if prop.channel == 'NORMAL':
        # Decal normals live in the decal frame (X, Y, out along +Z); express them in the
        # target's tangent frame so they combine like any authored tangent normal.
        world_axes = np.asarray(layer.decal_axes).reshape(3, 3) if getattr(layer, 'decal_inline', False) else axes(obj)[0]
        n_local = rgba[..., :3] * 2 - 1
        n_world = n_local @ world_axes            # rows: decal X, Y, Z axes in world
        frame = tangents(ts, n, geometry_cache)
        t, b = frame[0][y0:y1], frame[1][y0:y1]
        nn = normals[y0:y1]
        rgba[..., 0] = np.einsum('ijk,ijk->ij', n_world, t)
        rgba[..., 1] = np.einsum('ijk,ijk->ij', n_world, b)
        rgba[..., 2] = np.einsum('ijk,ijk->ij', n_world, nn)
        rgba[..., :3] = rgba[..., :3] * .5 + .5
    rgba[..., 3] *= cover
    return rgba


# ---- objects -----------------------------------------------------------------

def create_object(context, name, image=None, location=None, rotation=None, size=1.0):
    """An image Empty that draws the decal in the viewport; -Z is its projection direction."""
    empty = bpy.data.objects.new(name, None)
    empty.empty_display_type = 'IMAGE'
    empty.empty_display_size = float(size)
    empty.empty_image_offset = (-.5, -.5)
    empty.empty_image_depth = 'FRONT'
    empty.empty_image_side = 'FRONT'
    empty.use_empty_image_alpha = True
    if image is not None:
        empty.data = image
    empty[OWNER] = True
    context.scene.collection.objects.link(empty)
    if location is not None:
        empty.location = location
    if rotation is not None:
        empty.rotation_euler = rotation
    return empty


def show_image(layer):
    """Keep the Empty displaying the layer's base colour image."""
    obj = resolve(layer)
    if obj is None or obj.type != 'EMPTY':
        return
    from ..core.layer_stack import channel_for
    prop = channel_for(layer, 'BASE_COLOR')
    image = prop.image if prop is not None and prop.enabled else None
    if obj.empty_display_type == 'IMAGE' and obj.data != image:
        obj.data = image


def sync_axes(layer):
    """Mirror the decal's world axes and scale into the layer uniforms; True when they moved."""
    obj = resolve(layer)
    if obj is None:
        return False
    world_axes, lengths = axes(obj)
    flat = tuple(float(v) for v in world_axes.reshape(-1))
    moved = False
    if any(abs(a - b) > 1e-6 for a, b in zip(flat, layer.decal_axes)):
        layer.decal_axes = flat
        moved = True
    scale = tuple(float(v) for v in lengths)
    if any(abs(a - b) > 1e-6 for a, b in zip(scale, layer.decal_scale)):
        layer.decal_scale = scale
        moved = True
    return moved


def show_images(ts, layers=None):
    for layer in layers_of(ts) if layers is None else layers:
        show_image(layer)


def sync(ts):
    """Mirror axes and viewport images for every decal layer; returns the layers whose frame moved."""
    moved = []
    for layer in layers_of(ts):
        if layer.decal_object is None and layer.decal_object_name:
            if lost_pointer(layer):
                layer.decal_object_name = ''
                _linked.pop(layer.id, None)
                continue
            obj = bpy.data.objects.get(layer.decal_object_name)
            if obj is not None:
                layer.decal_object = obj
        if sync_axes(layer):
            moved.append(layer.id)
        show_image(layer)
    return moved


def release(layer):
    """Remove the Empty a layer being deleted created, unless another layer still projects
    from it (a copy of the layer shares the Empty: it inherits the marker, so deleting it
    last still removes the Empty). An Empty the artist made and picked by hand carries no
    marker and stays."""
    obj = getattr(layer, 'decal_object', None)
    if obj is None or obj.get(MARKER) != layer.id:
        return False
    for scene in bpy.data.scenes:
        project = getattr(scene, 'open_painter', None)
        if project is None:
            continue
        for ts in project.texture_sets:
            for other in ts.layers:
                if other.as_pointer() != layer.as_pointer() and other.decal_object == obj:
                    obj[MARKER] = other.id
                    return False
    layer.decal_object = None
    bpy.data.objects.remove(obj)
    return True


def adopt_duplicates(scene, layers=layers_of):
    """A duplicated decal Empty (Shift+D) becomes its own layer, a copy of the original's.
    ``layers`` lists a set's decal layers (``layers_of``, or the scheduler's memoised one)."""
    project = scene.open_painter
    created = []
    marked = [obj for obj in scene.objects if obj.type == 'EMPTY' and obj.get(MARKER)]
    if not marked:
        return created
    for ts in project.texture_sets:
        by_object = {layer.decal_object.as_pointer(): layer for layer in layers(ts) if layer.decal_object is not None}
        for obj in marked:
            source_id = obj.get(MARKER)
            if not source_id or obj.as_pointer() in by_object:
                continue
            source = next((layer for layer in ts.layers if layer.id == source_id), None)
            if source is None or source.decal_object == obj:
                continue
            from ..core.layer_stack import duplicate, index_of
            copy = duplicate(ts, index_of(ts, source.id))
            copy.name = obj.name.removeprefix(PREFIX) or copy.name
            copy.decal_object = obj
            copy.decal_object_name = obj.name
            obj[MARKER] = copy.id
            by_object[obj.as_pointer()] = copy
            created.append(copy.id)
    return created
