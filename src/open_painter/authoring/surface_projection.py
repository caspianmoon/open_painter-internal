"""Geometry-conditioned source templates and reusable, non-semantic projection frames."""
import base64
import json

from . import document, regions, schema


FRAME = schema.PROJECTION_FRAME


def axes(frame):
    import numpy as np
    schema.check(frame, FRAME, 'projection frame')
    u, v = np.asarray(frame['axis_u']), np.asarray(frame['axis_v'])
    if abs(np.linalg.norm(u)-1) > 1e-4 or abs(np.linalg.norm(v)-1) > 1e-4 or abs(float(u @ v)) > 1e-4:
        raise schema.AuthoringError('Projection axes must be orthonormal; copy the frame returned by inspect_surface')
    return np.stack((u, v, np.cross(u, v)))


def inspect(ts, region_id, objects, normal_direction, up_direction):
    """Read evaluated geometry, rasterize a square orthographic template, mutate nothing."""
    import numpy as np
    from ..geometry.snapshots import targets
    from ..geometry.uv_rasterizer import rasterize
    from ..ai.viewport import png
    selected = regions.selector(ts, region_id, objects)
    n = np.asarray(normal_direction, np.float64)
    if np.linalg.norm(n) < 1e-6:
        raise schema.AuthoringError('Surface viewing direction must be nonzero')
    n /= np.linalg.norm(n)
    v = np.asarray(up_direction, np.float64)
    v -= n * (v @ n)
    if np.linalg.norm(v) < 1e-6:
        raise schema.AuthoringError('Surface up direction must not be parallel to its viewing direction')
    v /= np.linalg.norm(v)
    u = np.cross(v, n)
    basis = np.stack((u, v, n))
    with targets(ts) as found:
        target = next((t for t in found if t['object'] == selected['object']), None)
        if target is None:
            raise schema.AuthoringError('Surface target is no longer assigned')
        triangles = target['positions'][target['tri_vertices'][np.isin(target['tri_polygons'], json.loads(selected['region_faces']))]]
        cross = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
        front = cross @ n > np.linalg.norm(cross, axis=1) * .05
        triangles = triangles[front]
        if not len(triangles):
            raise schema.AuthoringError('No faces point toward this direction; inspect from the opposite side')
        local = triangles @ basis.T
        lo, hi = local.reshape(-1, 3).min(0), local.reshape(-1, 3).max(0)
        span = max(float(max((hi-lo)[:2])) * 1.04, 1e-5)
        center = (hi+lo)/2
        frame = dict(origin=(center @ basis).tolist(), axis_u=u.tolist(), axis_v=v.tolist(),
                     extent=[span, span], depth=max(float(hi[2]-lo[2])*.51, span*.002))
        uv = (local[..., :2]-center[:2])/span + .5
        rows, cols, tri, weights = rasterize(uv.astype(np.float32), 768)
        pixels = np.zeros((768, 768, 4), np.uint8)
        pixels[rows, cols] = (135, 135, 135, 255)
        url = 'data:image/png;base64,' + base64.b64encode(png(768, 768, pixels[::-1].tobytes())).decode()
    return dict(region_id=region_id, frame=frame, template_size=[768, 768],
        meaning='Exact orthographic surface silhouette, neutral gray on transparent background. Image U is axis_u; image V points upward along axis_v. Preserve silhouette and canvas when generating a localized source; reuse this frame unchanged. This is not a UV layout or a style reference.',
        limitations='Only front-facing coverage is shown. No semantic labels or hidden manufactured details are inferred. Existing viewport and mesh maps remain separate evidence.',
        _images=[dict(id='surface-template:'+region_id, label='Surface-aligned generation template', url=url)])


def assign(ts, layer, cmd, images, objects):
    from . import generated_surfaces
    basis = axes(cmd['frame'])
    selected = regions.selector(ts, cmd['region_id'], objects)
    generated_surfaces.assign(ts, layer, dict(cmd, projection='DECAL'), images)
    frame = cmd['frame']
    layer.update(decal_inline=True, decal_object=None, decal_object_name='', decal_origin=frame['origin'],
                 decal_extent=frame['extent'], decal_axes=basis.ravel().tolist(), decal_scale=[1, 1, 1],
                 decal_depth=frame['depth'], decal_falloff=0., decal_facing=True)
    for prop in layer['channels']:
        if prop['source_metadata']:
            metadata = json.loads(prop['source_metadata'])
            metadata.update(projection_frame=frame, region_id=cmd['region_id'], nonrepeating=True)
            prop['source_metadata'] = json.dumps(metadata, sort_keys=True)
        if prop['projection'] == 'DECAL':
            prop['tiling'] = 'NONE'
    identifier = layer['id'] + '.surface-region'
    mask = next((m for m in layer['masks'] if m['id'] == identifier), None)
    if mask is None:
        mask = document.defaults('OP_Mask')
        mask.update(id=identifier, name='Projected surface region', source='MESH', operation='MULTIPLY')
        layer['masks'].append(mask)
    mask['mesh'].update(selected)
