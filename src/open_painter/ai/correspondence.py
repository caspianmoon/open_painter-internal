"""Focused Eevee/UV/source evidence. All material work is on a disposable candidate."""
import base64
import hashlib
import json
import time
from types import SimpleNamespace

import bpy
import numpy as np

from ..authoring import document, schema
from . import candidates
from .viewport import png

MAX_INSPECTIONS = 8


def image_url(pixels, budget=180000):
    pixels = np.asarray(pixels)
    if pixels.dtype != np.uint8:
        pixels = (np.clip(pixels, 0, 1)*255 + .5).astype(np.uint8)
    if pixels.shape[-1] == 3:
        pixels = np.concatenate((pixels, np.full((*pixels.shape[:2], 1), 255, np.uint8)), axis=2)
    while True:
        h, w = pixels.shape[:2]
        url = 'data:image/png;base64,' + base64.b64encode(png(w, h, pixels.tobytes())).decode()
        if len(url) <= budget:
            return url
        if min(h, w) < 64:
            raise schema.AuthoringError('Correspondence evidence exceeded the image budget')
        pixels = pixels[np.minimum((np.arange(max(1, int(h*.8))) / .8).astype(int), h-1)[:, None],
                        np.minimum((np.arange(max(1, int(w*.8))) / .8).astype(int), w-1)]


def pixels(image):
    data = np.empty(len(image.pixels), np.float32)
    image.pixels.foreach_get(data)
    return data.reshape(image.size[1], image.size[0], 4)


def line(canvas, a, b, color, width=2):
    a, b = np.asarray(a), np.asarray(b)
    if not np.isfinite([a, b]).all():
        return
    count = min(8192, max(2, int(np.linalg.norm(b-a)*2)))
    points = a + np.linspace(0, 1, count)[:, None]*(b-a)
    x, y = points[:, 0].astype(int), points[:, 1].astype(int)
    for dx in range(-width, width+1):
        for dy in range(-width, width+1):
            xx, yy = x+dx, y+dy
            valid = (xx >= 0) & (yy >= 0) & (xx < canvas.shape[1]) & (yy < canvas.shape[0])
            canvas[yy[valid], xx[valid]] = color


def arrow(canvas, a, b, color):
    line(canvas, a, b, color)
    delta = np.asarray(b)-a
    delta /= max(np.linalg.norm(delta), 1e-9)
    side = np.array([-delta[1], delta[0]])
    for sign in (-1, 1):
        line(canvas, b, np.asarray(b)-delta*12+sign*side*6, color)


def project(view, point, size):
    clip = np.asarray(view['projection']) @ np.asarray(view['matrix']) @ [*point, 1]
    ndc = clip[:2] / clip[3]
    return np.array([(ndc[0]+1)*.5*size, (1-ndc[1])*.5*size])


def crop_bounds(side, seam, hit, radius):
    if seam:
        uv = np.asarray(side['uv_edge'])
        center = uv[0] + seam['edge_parameter']*(uv[1]-uv[0])
        length = np.linalg.norm(np.diff(seam['edge_world'], axis=0))
        reach = np.clip(np.linalg.norm(uv[1]-uv[0])*radius/max(length, 1e-9), .012, .2)
    else:
        center, reach = np.asarray(hit['uv']), .05
    lo, hi = np.clip(center-reach, 0, 1), np.clip(center+reach, 0, 1)
    if np.any(hi-lo < 1e-6):
        raise schema.AuthoringError('This inspection currently requires UVs in the texture set\'s 0–1 tile')
    return [*lo.tolist(), *hi.tolist()]


def crop(data, bounds, side=384):
    h, w = data.shape[:2]
    x0, y0, x1, y1 = bounds
    x = np.minimum((np.linspace(x0, x1, side, endpoint=False)*w).astype(int), w-1)
    y = np.minimum((np.linspace(y0, y1, side, endpoint=False)*h).astype(int), h-1)
    return data[y[:, None], x][::-1].copy()


def uv_point(uv, bounds, side):
    lo, hi = np.asarray(bounds[:2]), np.asarray(bounds[2:])
    xy = (np.asarray(uv)-lo) / (hi-lo)
    return np.array([xy[0]*side, (1-xy[1])*side])


def uv_sheet(channels, bounds, seam_side=None):
    sheet = np.zeros((768, 1152, 4), np.float32)
    sheet[..., 3] = 1
    for i, channel in enumerate(candidates.CHANNELS):
        tile = crop(channels[channel], bounds)
        if channel == 'BASE_COLOR':
            rgb = tile[..., :3]
            tile[..., :3] = np.where(rgb <= .0031308, rgb*12.92, 1.055*np.maximum(rgb, 0)**(1/2.4)-.055)
        if seam_side:
            start, end = [uv_point(v, bounds, 384) for v in seam_side['uv_edge']]
            line(tile, start, end, (1., .3, .05, 1.))
            direction = end-start
            direction /= max(np.linalg.norm(direction), 1e-12)
            arrow(tile, np.array([192., 192.])-direction*45, np.array([192., 192.])+direction*45, (1., .65, .05, 1.))
        row, col = divmod(i, 3)
        sheet[row*384:(row+1)*384, col*384:(col+1)*384] = tile
    return sheet


def closeup(context, scene, hit, radius, view, seam, flow):
    import gpu
    from mathutils import Matrix, Vector
    from .viewport import framing_matrices, draw_view
    center = Vector(hit['position'])
    points = [center + Vector((x*radius, y*radius, z*radius))
              for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
    matrix, projection = framing_matrices(SimpleNamespace(view_matrix=Matrix(view['matrix']),
        window_matrix=Matrix(view['projection'])), points, 1.)
    frame = dict(matrix=[list(row) for row in matrix], projection=[list(row) for row in projection])
    size = 768
    space = context.space_data
    overlay = space.overlay.show_overlays
    offscreen = gpu.types.GPUOffScreen(size, size, format='RGBA8')
    try:
        space.overlay.show_overlays = False
        draw_view(offscreen, scene, scene.view_layers[0], space, context.region, matrix, projection)
        with offscreen.bind():
            data = gpu.state.active_framebuffer_get().read_color(0, 0, size, size, 4, 0, 'UBYTE')
            rgba = np.asarray(data, np.uint8).reshape(size, size, 4)[::-1].copy()
    finally:
        offscreen.free()
        space.overlay.show_overlays = overlay
    point = project(frame, hit['position'], size)
    # Annotation is explicitly an overlay, not a change to the candidate material.
    if seam:
        a, b = [project(frame, p, size) for p in seam['edge_world']]
        line(rgba, a, b, (255, 110, 20, 255))
    for direction, color in (('along_component', (50, 240, 80, 255)), ('around_component', (60, 140, 255, 255))):
        arrow(rgba, point, project(frame, center + Vector(flow[direction])*radius*.7, size), color)
    line(rgba, point-5, point+5, (255, 255, 255, 255))
    return rgba


def source_windows(ts, layer, prop, hit, radius, record, mask=None):
    """Conservative source-canvas windows around a local surface neighborhood.

    Uses the same CPU mapping as material evaluation, including metric/component
    frames. Repeating/triplanar sources can influence other surface occurrences;
    the packet discloses that and the next candidate must review all sides.
    """
    from ..geometry.uv_rasterizer import rasterize, interpolate
    from ..geometry.texture_correspondence import component_triangles
    n = min(1024, int(ts.resolution))
    same, _ = component_triangles(record, hit['triangle'])
    chosen = np.flatnonzero(same & np.isin(record['materials'], [slot for slot, value in record['sets'].items() if value == ts.id]))
    rows, cols, tri, weights = rasterize(record['uv'][chosen], n)
    vertices = record['positions'][record['vertices'][chosen[tri]]]
    world = interpolate(weights, vertices)
    normal = interpolate(weights, record['normals'][chosen[tri]])
    near = np.linalg.norm(world-np.asarray(hit['position']), axis=1) < radius*1.6
    if mask is not None:
        sample_rows = np.minimum(((rows+.5)*mask.shape[0]/n).astype(int), mask.shape[0]-1)
        sample_cols = np.minimum(((cols+.5)*mask.shape[1]/n).astype(int), mask.shape[1]-1)
        near &= mask[sample_rows, sample_cols] > .001
    rows, cols, world, normal = rows[near], cols[near], world[near], normal[near]
    if not len(rows):
        return []
    coords = []
    if prop.projection == 'DECAL':
        from ..painting.decals import coverage
        u, v, cover = coverage(layer, world, normal)
        coords = [np.column_stack((u[cover > .01], v[cover > .01]))]
    elif prop.material_mapping_version == 1:
        from ..evaluation.material_space import raster
        p, normals = raster(ts, n, prop, {})
        p = p[rows, cols]*prop.material_meters_per_unit
        extent = np.maximum(np.asarray(prop.material_image_extent_m), 1e-12)
        if prop.material_frame == 'COMPONENT' or prop.material_directional:
            coords = [np.column_stack((np.hypot(p[:, 0], p[:, 2]), p[:, 1])) / extent]
        else:
            weights = np.abs(normals[rows, cols])
            weights = (weights/np.maximum(weights.max(axis=1, keepdims=True), 1e-8))**prop.triplanar_hardness
            weights /= np.maximum(weights.sum(axis=1, keepdims=True), 1e-8)
            coords = [p[weights[:, axis] > .001][:, pair] / extent
                      for axis, pair in enumerate(((1, 2), (0, 2), (0, 1)))]
    else:
        if prop.projection == 'TRIPLANAR':
            weights = np.abs(normal)
            weights = (weights/np.maximum(weights.max(axis=1, keepdims=True), 1e-8))**prop.triplanar_hardness
            weights /= np.maximum(weights.sum(axis=1, keepdims=True), 1e-8)
            coords = [world[weights[:, axis] > .001][:, pair]
                      for axis, pair in enumerate(((1, 2), (0, 2), (0, 1)))]
            center = 0
        else:
            coords = [np.column_stack(((cols+.5)/n, (rows+.5)/n))-.5]
            center = .5
        c, s = np.cos(prop.rotation), np.sin(prop.rotation)
        rotation = np.array([[c, -s], [s, c]])
        coords = [(points*np.asarray(prop.scale)) @ rotation.T + center + np.asarray(prop.offset) for points in coords]
    windows = []
    for points in coords:
        if not len(points):
            continue
        tiling = 'NONE' if prop.projection == 'DECAL' else prop.tiling
        if tiling == 'REPEAT':
            points %= 1
        elif tiling == 'MIRROR':
            points = 1-abs(points % 2-1)
        else:
            points = points[np.all((points >= 0) & (points <= 1), axis=1)]
        if not len(points):
            continue
        # Separate wrapped edges so a seam crossing U=0 does not authorize a full canvas edit.
        for x in (0, 1):
            for y in (0, 1):
                group = points[(points[:, 0] >= .5) == bool(x)]
                group = group[(group[:, 1] >= .5) == bool(y)]
                if len(group):
                    lo, hi = np.maximum(0, group.min(0)-.008), np.minimum(1, group.max(0)+.008)
                    windows.append([*lo.tolist(), *hi.tolist()])
    return windows


def inspect(context, ts, captured, arguments):
    from ..geometry.texture_correspondence import lookup, neighborhood
    from ..core import image_manager
    from ..evaluation.evaluator import LayerEvaluator
    from ..authoring.surface_replacement import mapping_signature
    candidate = captured.get('candidate')
    if not candidate:
        raise schema.AuthoringError('Preview the material before inspecting correspondence')
    _, compiled = candidates.reviewed(context, ts, captured, candidate['proposal'], arguments['candidate_digest'])
    stored = candidate.get('correspondence')
    view = stored and stored['views'].get(arguments['view'])
    if view is None:
        raise schema.AuthoringError('Choose a view returned by the current candidate')
    cache_key = json.dumps(arguments, sort_keys=True)
    prior = captured.setdefault('correspondence_packets', {}).get(cache_key)
    if prior:
        return prior
    if len(captured['correspondence_packets']) >= MAX_INSPECTIONS:
        raise schema.AuthoringError('Correspondence inspection budget exhausted; reuse the existing packets')
    record, hit = lookup(stored['geometry'], view, arguments['x'], arguments['y'], arguments['texture_set_id'])
    seams, radius, flow = neighborhood(record, hit)
    seam = seams[0] if seams else None
    sides = [seam['side_a'], seam['side_b']] if seam else [None, None]
    bounds = [crop_bounds(side, seam, hit, radius) for side in sides]
    identifier = 'correspondence:' + hashlib.sha256(cache_key.encode()).hexdigest()[:24]
    pictures, sources, missing = [], [], []
    def add(role, label, array, budget=180000):
        pictures.append(dict(id=identifier+':'+role, label=label, url=image_url(array, budget)))
    started = time.monotonic()
    elapsed = captured.get('preview_seconds', 0.)
    deadline = started + candidates.MAX_SECONDS - elapsed
    from ..geometry.provenance import status
    original_part = next(item for item in context.scene.open_painter.texture_sets if item.id == arguments['texture_set_id'])
    map_status = {item.kind: status(original_part, item) for item in original_part.baked_maps if item.image}
    try:
        with candidates.scratch(context, compiled, captured['images'], preview_resolution=candidates.PREVIEW_RESOLUTION) as scene:
            candidates._ready(scene, deadline)
            part = next(item for item in scene.open_painter.texture_sets if item.id == arguments['texture_set_id'])
            add('closeup', 'Current Eevee 3D close-up; orange=paired physical seam, green=along component, blue=around component; annotation overlays only',
                closeup(context, scene, hit, radius, view, seam, flow), 250000)
            evaluator = LayerEvaluator(scene)
            channels = {}
            for channel in candidates.CHANNELS:
                for _ in evaluator.channel_steps(part.id, channel):
                    if time.monotonic() > deadline:
                        raise schema.AuthoringError('Correspondence inspection exceeded the candidate review budget')
                channels[channel] = image_manager.pixels(image_manager.output(part, channel).image).copy()
            from ..export.surface_normal import combined
            channels['NORMAL'] = combined(part, channels['NORMAL'], channels['HEIGHT'])
            for side, box, name in zip(sides, bounds, ('a', 'b')):
                add('uv_'+name, 'UV side '+name.upper()+' — current evaluated channels; cells row-major: BASE_COLOR, ROUGHNESS, METALLIC, HEIGHT, NORMAL including relief. Orange arrow follows physical edge 0→1.', uv_sheet(channels, box, side), 230000)
            diagram = np.zeros((384, 768, 4), np.float32); diagram[..., 3] = 1
            for i, (side, box) in enumerate(zip(sides, bounds)):
                diagram[:, i*384:(i+1)*384] = crop(channels['BASE_COLOR'], box)
                if side:
                    a, b = [uv_point(v, box, 384)+[i*384, 0] for v in side['uv_edge']]
                    line(diagram, a, b, (1., .3, .05, 1.))
                    direction = b-a
                    direction /= max(np.linalg.norm(direction), 1e-12)
                    center = np.array([i*384+192., 192.])
                    arrow(diagram, center-direction*55, center+direction*55, (1., .65, .05, 1.))
            add('seam_pair', 'Seam correspondence — LEFT=A, RIGHT=B; matching orange arrows are the same physical direction. See orientation and UV bounds in packet; no seam found means both show the selected region.', diagram)
            for kind in ('AO', 'CURVATURE', 'WORLD_NORMAL', 'POSITION', 'NORMAL'):
                if not captured.get('visual_settings', {}).get('send_maps', True):
                    missing.append(kind); continue
                entry = next((item for item in part.baked_maps if item.kind == kind and item.image), None)
                if not entry:
                    missing.append(kind); continue
                data = pixels(entry.image)
                add('geometry_'+kind, 'Local geometry '+kind+' — LEFT=UV A, RIGHT=UV B; '+str(map_status.get(kind)),
                    np.concatenate([crop(data, box) for box in bounds], axis=1), 100000)
            # Highest visible source-bearing layers first. Coverage is evidence, not semantic attribution.
            from ..authoring.coverage import _buffer
            rowside = int(part.resolution)
            u, v = np.clip(hit['uv'], 0, .999999)
            for layer in reversed(part.layers):
                if len(sources) >= 3:
                    break
                if not layer.visible or layer.opacity <= 0 or layer.type != 'FILL':
                    continue
                prop = next(p for p in layer.channels if p.channel == 'BASE_COLOR')
                if not prop.enabled or not prop.image or not prop.image.get('director_image_id'):
                    continue
                owner = layer
                parents = {item.id: item for item in part.layers}
                visible = True
                while owner.parent_id:
                    owner = parents[owner.parent_id]
                    if not owner.visible or owner.opacity <= 0:
                        visible = False; break
                if not visible:
                    continue
                mask = _buffer(part, layer)
                coverage = float(mask[int(v*rowside), int(u*rowside)])
                if coverage <= .001:
                    continue
                if prop.image.get('director_surface_field'):
                    # A fused field has no flat canvas to edit: its pixels came from
                    # observations of this exact surface, so corrections are made by
                    # observing the surface again, not by repainting a texture source.
                    sources.append(dict(layer_id=layer.id, layer_name=layer.name,
                        source_kind='GENERATED_SURFACE_FIELD',
                        reference_image_id=prop.image.get('director_image_id', ''),
                        observations=json.loads(prop.image.get('director_surface_field_observations', '[]')),
                        fused_coverage=json.loads(prop.image.get('director_surface_field_coverage', '{}')),
                        surface_position=hit['position'], surface_normal=hit['normal'],
                        mapping_signature=mapping_signature(document.snapshot(part)['layers'][list(part.layers).index(layer)]),
                        repeated_mapping=False,
                        local_edit_limit='Fused surface fields are corrected by observation, not by editing a '
                            'source canvas. Use inspect_surface_area at this surface position for a corrective '
                            'observation, or change which observations the field fuses, then regenerate it.'))
                    continue
                windows = source_windows(part, layer, prop, hit, radius, record, mask)
                if not windows:
                    continue
                source_id = prop.image['director_image_id']
                source = dict(layer_id=layer.id, layer_name=layer.name, reference_image_id=source_id,
                    dimensions=list(prop.image.size), projection=prop.projection, source_windows=windows,
                    mapping_signature=mapping_signature(document.snapshot(part)['layers'][list(part.layers).index(layer)]),
                    repeated_mapping=prop.projection == 'TRIPLANAR' or prop.material_mapping_version == 1 or prop.tiling != 'NONE',
                    local_edit_limit='Only these source-canvas windows may change. Reused/tiled pixels may also appear elsewhere; inspect all views after editing.')
                sources.append(source)
                from .visual_context import thumbnail
                reference_preview = thumbnail(prop.image, side=2048, budget=650000)
                source['reference_preview_dimensions'] = [reference_preview['width'], reference_preview['height']]
                pictures.append(dict(id=identifier+':source:'+layer.id,
                    label='CURRENT generated source for layer '+layer.name+' — edit this existing canvas; preserve colors, identity and feature scale',
                    url=reference_preview['url']))
                mask = np.zeros((512, 512, 4), np.float32); mask[..., 3] = 1
                for x0, y0, x1, y1 in windows:
                    mask[int(y0*512):int(np.ceil(y1*512)), int(x0*512):int(np.ceil(x1*512)), :3] = 1
                add('edit_mask:'+layer.id, 'SOURCE edit mask for '+layer.name+' — white=authorized local source windows; black=preserved pixels (not UV mesh coordinates)', mask[::-1], 30000)
            if time.monotonic() > deadline:
                raise schema.AuthoringError('Correspondence inspection exceeded the candidate review budget')
        # Do not retain images/datablocks from scratch, only JSON-safe source IDs and transforms.
        result = dict(correspondence_id=identifier, candidate_digest=candidate['digest'], view=arguments['view'],
            pixel=dict(x=arguments['x'], y=arguments['y'], origin='TOP_LEFT'), surface=hit,
            seam=seam, nearby_seams=seams[1:], uv_bounds=dict(a=bounds[0], b=bounds[1]),
            local_directions=flow, radius_world=radius, sources=sources, missing_geometry_maps=missing,
            nonmanifold_edges_excluded=record['nonmanifold_edges'],
            limitations=['Ray lookup uses evaluated geometry, not shader bump or transparency.',
                        'Principal directions are geometric evidence, not semantic anatomy.',
                        'Source windows are conservative; repeating sources may affect multiple physical regions.'],
            correction_instruction='For local defects, edit the CURRENT source using this close-up, paired UV crops, orientation, geometry maps and edit mask. Preserve canvas, pattern scale, palette, material identity and unrelated detail. Use replace_surface_source with this correspondence_id and mode LOCAL in the complete proposal, then preview all views again. Use GLOBAL only when the source itself is wrong throughout. Mapping failures may require placement changes as well as source edits.',
            images=[dict(id=p['id'], label=p['label']) for p in pictures], _images=pictures)
        # Validate the actual wire size before storing or exposing an unusable packet.
        from .protocol import encode
        encode(dict(event='tool_result', result={k:v for k,v in result.items() if k != '_images'}, images=pictures))
        captured.setdefault('correspondences', {})[identifier] = {k:v for k,v in result.items() if k != '_images'}
        captured['correspondence_packets'][cache_key] = result
        return result
    finally:
        captured['preview_seconds'] = elapsed + time.monotonic()-started
