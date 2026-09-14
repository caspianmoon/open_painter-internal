"""Art-directed scene fields rasterized to persistent grayscale UV textures."""
import numpy as np
from ..geometry import influencers, queries
from ..evaluation.blend_modes import sample_uv
from ..evaluation import metrics


def signature(ts, field):
    from ..geometry.provenance import record
    return (record(ts, 'SCENE_FIELD'), influencers.signature(ts, field))


def _attribute(ts, field, n):
    from ..geometry.snapshots import targets
    from ..geometry.uv_rasterizer import interpolate, rasterize
    result = np.zeros((n,n),np.float32)
    with targets(ts) as meshes:
        for target in meshes:
            mesh, obj = target['mesh'], target['object']
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
            rows, cols, tri, weights = rasterize(target['uv'][target['tri_loops']], n)
            if not len(rows):
                continue
            if domain == 'FACE':
                corners = np.repeat(values[target['tri_polygons'][tri]][:, None], 3, axis=1)
            else:
                corners = values[(target['tri_vertices'] if domain == 'POINT' else target['tri_loops'])[tri]]
            result[rows, cols] = interpolate(weights, corners)
    return result


@metrics.measured('scene_analysis')
def compute(ts, field):
    from ..painting.projection import geometry
    n = int(field.resolution)
    if n not in {64,128,256}:
        raise ValueError('Scene analysis supports bounded 64/128/256 grids')
    if field.kind in {'ATTRIBUTE','VERTEX_GROUP'}:
        return np.clip(_attribute(ts,field,n),0,1)
    positions, normals, valid = geometry(ts,n,coverage=True)
    normals /= np.maximum(np.linalg.norm(normals,axis=-1,keepdims=True),1e-8)
    direction = np.asarray(field.direction,np.float32)
    length = np.linalg.norm(direction)
    if length < 1e-6:
        raise ValueError('Scene field direction cannot be zero')
    direction /= length
    if field.kind in {'UP','DOWN'}:
        value = np.clip(normals @ direction * (1 if field.kind == 'UP' else -1),0,1)
    elif field.kind in {'HEIGHT','HEAT','MOISTURE','SOOT'}:
        if field.object is None:
            raise ValueError('Scene field reference object is missing')
        origin = np.asarray(field.object.matrix_world.translation,np.float32)
        relative = positions-origin
        along = relative @ direction
        if field.kind == 'HEIGHT':
            lo, hi = field.bounds
            if hi <= lo:
                raise ValueError('Height bounds must be increasing')
            value = np.clip((along-lo)/(hi-lo),0,1)
        else:
            value = np.clip(1-np.linalg.norm(relative,axis=-1)/field.distance,0,1)
            if field.kind == 'SOOT':
                value *= np.clip(along/field.distance,0,1)
    else:
        objects = influencers.objects(ts,field)
        if not objects:
            raise ValueError('Choose an existing external mesh or collection for scene analysis')
        value = np.zeros((n,n),np.float32)
        with queries.acceleration(ts.id_data, objects) as trees:
            if not trees:
                raise ValueError('Influencers contain no evaluated triangles')
            metrics.count('scene_query_samples',int(valid.sum()))
            if field.kind == 'EXPOSURE':
                value[valid] = queries.exposure(trees,positions[valid],normals[valid],direction,field.distance)
            else:
                distances = queries.distance(trees,positions[valid],field.distance)
                normalized = np.clip(distances/field.distance,0,1)
                value[valid] = normalized if field.kind == 'DISTANCE' else 1-normalized
    return np.asarray(value*valid,np.float32)


def update(ts, mask):
    from ..core import image_manager as images
    field = mask.influence
    token = repr(signature(ts,field))
    try:
        value = compute(ts,field)
    except Exception as exc:
        field.error = str(exc)
        raise
    # Allocate/replace the last-good image only after successful analysis.
    n = int(field.resolution)
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


def evaluate(ts, mask, u, v, cache):
    from ..evaluation.image_snapshots import read
    field = mask.influence
    if field.mode == 'FROZEN' and not field.image:
        raise ValueError('Frozen analysis has no texture; update analysis first')
    desired = repr(signature(ts,field)) if field.mode == 'LIVE' or not field.image else field.provenance
    if not field.image or (field.mode == 'LIVE' and desired != field.provenance):
        # Mutate only the derived analysis resource on the main-thread author,
        # never the immutable parameters used by the compositor.
        author = next(t for t in ts.id_data.open_painter.texture_sets if t.id == ts.id)
        original = next(m for layer in author.layers for m in layer.masks if m.id == mask.id)
        if original.influence.provenance != desired or not original.influence.image:
            update(author,original)
        image = original.influence.image
    else:
        image = field.image
    key = ('scene_image',image.as_pointer(),image.get('open_painter_pixel_revision',0))
    if key not in cache:
        cache[key] = read(cache,image)
    return sample_uv(cache[key],u,v)[...,0]
