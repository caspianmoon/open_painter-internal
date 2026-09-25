"""Surface observations: rendered views of the actual 3D surface, with identity.

Every observation retains its camera transform, projection, resolution, visible
object, visible triangle, barycentric coordinates, world position, surface normal,
UV coordinate, texture set and material slot, so any pixel of it — rendered or
generated — resolves to an exact point of the artist's mesh.

The registry is owned by one instruction. Buffers never outlive the request that
captured them, and nothing here edits artist geometry, UVs, slots or selection.
"""
import base64
import hashlib
import json
import time

import numpy as np

from ..authoring import schema
from ..geometry import surface_coverage as coverage, surface_observation as observation
from .viewport import png

MAX_OBSERVATIONS = 12
MAX_PACKETS = 3
RENDER_SIDE = 640
EVIDENCE_SIDE = 320
BASE_VIEWS = ('front', 'back', 'left', 'right', 'top', 'bottom')
SUFFICIENT = .9     # area fraction with good coverage that ends automatic camera search
EVALUATION_SIDE = 192
MAX_SECONDS = 240   # bounded wall clock for capturing and rendering observations
_store = {}


def release(request_id):
    """Drop every buffer captured for one instruction."""
    for key in [key for key, value in _store.items() if value['request'] == request_id]:
        _store.pop(key, None)


def view(identifier, texture_set_id):
    """The fusion view record of a registered observation."""
    record = _store.get(identifier)
    if record is None or record['texture_set_id'] != texture_set_id:
        raise schema.AuthoringError('Surface observation ' + identifier + ' is no longer available; capture observations again')
    return record['view']


def registry(request_id, texture_set_id=None):
    """Registered observations of one instruction, as JSON-safe metadata."""
    return {key: value['record'] for key, value in _store.items()
            if value['request'] == request_id and (texture_set_id is None or value['texture_set_id'] == texture_set_id)}


def _image(width, height, rgba, budget=180000):
    """A PNG data URL, progressively reduced until it fits the attachment budget."""
    data = np.clip(np.nan_to_num(np.asarray(rgba, np.float32), nan=0., posinf=1., neginf=0.), 0, 1)
    data = np.round(data * 255).astype(np.uint8)
    while True:
        url = 'data:image/png;base64,' + base64.b64encode(png(width, height, data.tobytes())).decode()
        if len(url) <= budget or min(width, height) <= 64:
            return url
        w, h = max(64, int(width * .75)), max(64, int(height * .75))
        rows = np.minimum(height - 1, ((np.arange(h) + .5) * height / h).astype(int))
        cols = np.minimum(width - 1, ((np.arange(w) + .5) * width / w).astype(int))
        data, width, height = data[rows[:, None], cols], w, h


def scene_geometry(context, ts, selected):
    """Assigned triangles first, then everything visible that can occlude them.

    Occluders take part in the depth test exactly like target geometry, so a pixel
    showing a hidden surface is never attributed to the surface behind it.
    """
    from ..geometry.snapshots import _triangle_arrays, _unit
    graph = context.evaluated_depsgraph_get()
    slots = {}
    for ref in ts.assignments:
        if ref.object:
            slots.setdefault(ref.object, set()).add(ref.slot_index)
    faces = None
    if selected:
        from ..authoring.regions import topology
        faces = json.loads(selected['region_faces'])
    target_positions, target_triangles, target_uv, target_normals = [], [], [], []
    occluder_positions, occluder_triangles = [], []
    target_slot, target_object = [], []
    objects, count = [], 0
    for obj in sorted(context.view_layer.objects, key=lambda item: item.name_full):
        if obj.type not in {'MESH', 'CURVE', 'SURFACE', 'FONT', 'META'} or not obj.visible_get(view_layer=context.view_layer):
            continue
        evaluated = obj.evaluated_get(graph)
        try:
            mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=graph)
            if not mesh or not len(mesh.polygons):
                continue
            mesh.calc_loop_triangles()
            vertices, loops, materials, polygons = _triangle_arrays(mesh)
            if not len(vertices):
                continue
            count += len(vertices)
            if count > observation.MAX_TRIANGLES:
                raise schema.AuthoringError('Surface observation supports at most 1.2 million visible evaluated triangles')
            local = np.empty((len(mesh.vertices), 3), np.float32)
            mesh.vertices.foreach_get('co', local.ravel())
            matrix = np.asarray(evaluated.matrix_world, np.float64)
            positions = local @ matrix[:3, :3].T + matrix[:3, 3]
            mine = np.isin(materials, list(slots.get(obj, ()))) if obj in slots else np.zeros(len(materials), bool)
            if faces is not None:
                if selected['object_name'] != obj.name_full:
                    mine = np.zeros(len(materials), bool)
                else:
                    if topology(mesh) != selected['region_topology']:
                        raise schema.AuthoringError('Region topology changed; inspect the current regions again')
                    mine &= np.isin(polygons, faces)
            if mine.any():
                index = len(objects)
                objects.append(obj.name_full)
                normals = np.empty((len(mesh.loops), 3), np.float32)
                mesh.corner_normals.foreach_get('vector', normals.ravel())
                normals = _unit(normals @ np.linalg.pinv(matrix[:3, :3]))
                layer = next((uv for uv in mesh.uv_layers if uv.active_render), mesh.uv_layers.active)
                if layer is None:
                    raise schema.AuthoringError('Surface observation needs render UVs on ' + obj.name_full)
                from ..core.udim import local_uv
                uv = local_uv(mesh, ts, np.unique(polygons[mine]))
                target_positions.append(positions)
                target_triangles.append(vertices[mine])
                target_uv.append(uv[loops[mine]])
                target_normals.append(normals[loops[mine]])
                target_slot.append(materials[mine])
                target_object.append(np.full(int(mine.sum()), index, np.int32))
            if (~mine).any():
                occluder_positions.append(positions)
                occluder_triangles.append(vertices[~mine])
        finally:
            evaluated.to_mesh_clear()
    if not target_triangles:
        raise schema.AuthoringError('This material has no visible assigned faces to observe')
    positions, triangles, offset = [], [], 0
    for store, chunks, tris in ((positions, target_positions, target_triangles),
                                (positions, occluder_positions, occluder_triangles)):
        for chunk, tri in zip(chunks, tris):
            triangles.append(tri + offset)
            store.append(chunk)
            offset += len(chunk)
    target_count = int(sum(len(item) for item in target_triangles))
    triangles = np.concatenate(triangles)
    positions = np.concatenate(positions)
    normals, areas = coverage.triangle_normals(positions, triangles)
    return dict(positions=positions, triangles=triangles,
                owner=np.where(np.arange(len(triangles)) < target_count, 0, -1).astype(np.int32),
                target_count=target_count, normals=normals, areas=areas,
                uv=np.concatenate(target_uv), corner_normals=np.concatenate(target_normals),
                slot=np.concatenate(target_slot), object_index=np.concatenate(target_object),
                objects=objects)


def rasterize(geometry, direction, up, side, *, points=None):
    """One camera's exact visible-surface buffers over the evaluated scene."""
    frame = observation.frame(points if points is not None else
                              geometry['positions'][np.unique(geometry['triangles'][:geometry['target_count']])],
                              direction, up)
    buffers = observation.rasterize(geometry['positions'], geometry['triangles'], geometry['owner'],
                                    frame['matrix'], frame['projection'], side)
    target = (buffers['triangle'] >= 0) & (buffers['triangle'] < geometry['target_count'])
    silhouette = observation.silhouette_distance(target, buffers['depth'])
    return frame, buffers, target, silhouette


def _record(geometry, frame, buffers, target, silhouette, side):
    """The fusion view plus the JSON-safe description of one observation."""
    pixel_world = 2 * frame['extent'] / side
    axis = np.asarray(frame['axes'][2], np.float32)
    return dict(matrix=frame['matrix'], projection=frame['projection'], depth=buffers['depth'],
                target=target, silhouette=silhouette, axis=axis, pixel_world=float(pixel_world),
                silhouette_limit=3, side=side)


def plan(context, ts, geometry, directions, side):
    """Rasterize the requested cameras and measure what each actually shows."""
    points = geometry['positions'][np.unique(geometry['triangles'][:geometry['target_count']])]
    planned = []
    for label, direction in directions:
        frame, buffers, target, silhouette = rasterize(geometry, direction, coverage.up_for(direction), side, points=points)
        measured = coverage.observe(buffers, silhouette, count=len(geometry['triangles']),
                                    normals=geometry['normals'], areas=geometry['areas'],
                                    axis=frame['axes'][2], pixel_world=2 * frame['extent'] / side)
        planned.append(dict(label=label, direction=np.asarray(direction, np.float64), frame=frame,
                            buffers=buffers, target=target, silhouette=silhouette, measured=measured))
    return planned


def automatic(context, ts, geometry, planned, side, limit):
    """Add cameras for geometry the current set covers weakly, from geometry alone."""
    target = slice(0, geometry['target_count'])
    normals, areas = geometry['normals'][target], geometry['areas'][target]
    accepted = [item['direction'] for item in planned]
    while len(planned) < limit:
        state = coverage.accumulate([dict((key, value[target]) for key, value in item['measured'].items())
                                     for item in planned], [item['label'] for item in planned],
                                    count=geometry['target_count'])
        good = float(areas[state['state'] == coverage.GOOD].sum() / max(areas.sum(), 1e-12))
        needed = state['state'] != coverage.GOOD
        if good >= SUFFICIENT or not needed.any():
            break
        ranked = coverage.rank_directions(normals, areas, needed, accepted, limit=6)
        if not ranked:
            break
        best = None
        for _, direction in ranked:
            trial = plan(context, ts, geometry, [('candidate', direction)], EVALUATION_SIDE)[0]
            gain = float((areas[needed] * trial['measured']['score'][target][needed]).sum())
            if best is None or gain > best[0]:
                best = gain, direction
        if best is None or best[0] <= 0:
            break
        accepted.append(best[1])
        planned.extend(plan(context, ts, geometry, [('auto_' + str(len(planned) + 1), best[1])], side))
    return planned


def _reduced(array, side):
    """Nearest-sample downscale of an observation-sized buffer, for evidence images."""
    if array.shape[0] <= side:
        return array
    index = np.minimum(array.shape[0] - 1, ((np.arange(side) + .5) * array.shape[0] / side).astype(int))
    return array[index[:, None], index]


def _uv_corners(geometry):
    """Per-triangle corner UVs over the whole observed scene, built once."""
    if 'uv_corners' not in geometry:
        corners = np.zeros((len(geometry['triangles']), 3, 2), np.float32)
        corners[:geometry['target_count']] = geometry['uv']
        geometry['uv_corners'] = corners
    return geometry['uv_corners']


def _evidence(ts, geometry, buffers, target, side):
    """Mesh-map evidence sampled through this observation's own UV buffer.

    Every map refers to exactly the surface the rendered view shows, because the
    sample coordinate is the visible triangle's interpolated UV, not a second
    projection of the model.
    """
    from ..core.image_manager import pixels as read_pixels
    from ..geometry.provenance import status
    from .visual_context import MAPS
    uv = _reduced(observation.interpolate(buffers, _uv_corners(geometry)), EVIDENCE_SIDE)
    visible = _reduced(target, EVIDENCE_SIDE)
    small = uv.shape[0]
    images, missing = [], []
    for kind, meaning in MAPS:
        entry = next((item for item in ts.baked_maps if item.kind == kind and item.image), None)
        if entry is None or not entry.image.has_data:
            missing.append(kind)
            continue
        data = read_pixels(entry.image)
        sampled = observation.sample(data[::-1], uv.reshape(-1, 2)).reshape(small, small, 4)
        rgba = np.where(visible[..., None], sampled, 0.)
        rgba[..., 3] = visible
        images.append(dict(kind=kind, provenance_status=status(ts, entry), meaning=meaning,
                           url=_image(small, small, rgba, budget=90000)))
    return images, missing


def _flow(geometry, buffers, target, side):
    """Generic surface-direction evidence for this observation, encoded as color."""
    arrays, encoded = _direction_field(geometry)
    rgb = _reduced(observation.interpolate(buffers, encoded), EVIDENCE_SIDE)
    visible = _reduced(target, EVIDENCE_SIDE)
    side = rgb.shape[0]
    rgba = np.concatenate((np.where(visible[..., None], rgb, 0.), visible[..., None].astype(np.float32)), axis=2)
    count = max(len(arrays['curvature_stable']), 1)
    curved = arrays['curvature_stable']
    parametrized = ~curved & arrays['tangent_stable']
    stable = float(curved.mean()) if len(curved) else 0.
    return dict(url=_image(side, side, rgba, budget=70000), curvature_stable_fraction=stable,
                sources=dict(CURVATURE=stable, UV_TANGENT=float(parametrized.sum() / count),
                             COMPONENT_AXIS=float((~curved & ~parametrized).sum() / count)),
                meaning='Primary surface direction as RGB = direction * 0.5 + 0.5: measurable principal '
                        'curvature where the two curvatures differ, otherwise the UV parametrization '
                        'tangent, otherwise the connected-component principal axis. The secondary '
                        'direction is the surface normal crossed with this one, and the normal itself is '
                        'the World Normal evidence. Geometric evidence only, never semantic grain.')


def _direction_field(geometry):
    """The surface's generic direction evidence, computed once per observed surface."""
    if 'direction_field' not in geometry:
        from ..geometry.surface_tangents import field
        count = geometry['target_count']
        arrays = field(geometry['positions'], geometry['triangles'][:count], geometry['uv'],
                       _vertex_normals(geometry), geometry['normals'][:count])
        direction = np.where(arrays['curvature_stable'][:, None], arrays['curvature_primary'],
                             arrays['primary_tangent'])
        missing = np.linalg.norm(direction, axis=1) < 1e-6
        direction = np.where(missing[:, None], arrays['component_direction'], direction)
        encoded = np.zeros((len(geometry['triangles']), 3, 3), np.float32)
        encoded[:count] = (direction * .5 + .5)[:, None, :]
        geometry['direction_field'] = arrays, encoded
    return geometry['direction_field']


def _vertex_normals(geometry):
    """Area-weighted vertex normals of the whole observed scene, for the shape operator."""
    positions, triangles = geometry['positions'], geometry['triangles'][:geometry['target_count']]
    normals = np.zeros_like(positions)
    weighted = geometry['normals'][:geometry['target_count']] * geometry['areas'][:geometry['target_count'], None]
    for corner in range(3):
        np.add.at(normals, triangles[:, corner], weighted)
    length = np.linalg.norm(normals, axis=1, keepdims=True)
    return np.divide(normals, np.maximum(length, 1e-12))


def _render(context, scene, frame, side):
    """The actual shaded view of the surface from this observation's camera."""
    import gpu
    from mathutils import Matrix
    from .viewport import draw_view
    space, region = context.space_data, context.region
    if space.type != 'VIEW_3D' or space.shading.type not in {'MATERIAL', 'RENDERED'}:
        raise schema.AuthoringError('Open a Material Preview or Eevee rendered 3D view to capture surface observations')
    matrix = Matrix([list(row) for row in frame['matrix']])
    projection = Matrix([list(row) for row in frame['projection']])
    offscreen = gpu.types.GPUOffScreen(side, side, format='RGBA8')
    overlays = space.overlay.show_overlays
    try:
        space.overlay.show_overlays = False
        draw_view(offscreen, scene, context.view_layer, space, region, matrix, projection)
        with offscreen.bind():
            pixels = gpu.state.active_framebuffer_get().read_color(0, 0, side, side, 4, 0, 'UBYTE')
            return np.asarray(pixels, dtype=np.uint8).reshape(side, side, 4)[::-1].copy()
    finally:
        space.overlay.show_overlays = overlays
        offscreen.free()


def budget(captured, *, started=None):
    """Bounded observation time per instruction, checked before and after each camera."""
    if started is not None:
        captured['observation_seconds'] = captured.get('observation_seconds', 0.) + time.monotonic() - started
    if captured.get('observation_seconds', 0.) >= MAX_SECONDS:
        raise schema.AuthoringError('Surface observation time budget exhausted for this instruction; '
                                    'fuse the observations already captured')


def _identity(captured, label, direction, region_id, side, frame):
    """One identity per actual camera: direction, framing, region and resolution.

    Two captures can ask for the same named direction over different regions or at
    a different resolution; they are different observations and must never share a
    key, or a field would silently fuse buffers it did not generate against.
    """
    body = json.dumps([captured['id'], label, region_id, int(side),
                       [round(float(v), 6) for v in direction],
                       [round(float(v), 6) for v in frame['center']], round(float(frame['extent']), 9)],
                      sort_keys=True)
    return 'observation:' + hashlib.sha256(body.encode()).hexdigest()[:24]


def _packet(context, scene, ts, captured, geometry, item, identifier, *, region_id):
    """Everything Codex needs to author appearance for one visible surface."""
    side = item['buffers']['triangle'].shape[0]
    target = item['target']
    render = _render(context, scene, item['frame'], side)
    evidence, missing = _evidence(ts, geometry, item['buffers'], target, side)
    mask = np.zeros((side, side, 4), np.float32)
    mask[..., :3] = target[..., None]
    mask[..., 3] = 1.
    flow = _flow(geometry, item['buffers'], target, side)
    slots = geometry['slot'][np.clip(item['buffers']['triangle'].reshape(-1), 0, max(geometry['target_count'] - 1, 0))]
    slots = slots[target.reshape(-1)]
    measured = item['measured']
    chosen = slice(0, geometry['target_count'])
    areas = geometry['areas'][chosen]
    visible = measured['visible'][chosen]
    images = [dict(id=identifier + ':view', role='observation_view',
                   label='Observation ' + item['label'] + ' — current rendered surface',
                   url=_image(side, side, render.astype(np.float32) / 255, budget=200000)),
              dict(id=identifier + ':mask', role='surface_mask',
                   label='Observation ' + item['label'] + ' — valid surface mask (white is this material)',
                   url=_image(side, side, mask, budget=40000)),
              dict(id=identifier + ':flow', role='surface_direction',
                   label='Observation ' + item['label'] + ' — generic surface directions', url=flow['url'])]
    images.extend(dict(id=identifier + ':' + entry['kind'].lower(), role='observation_evidence',
                       label='Observation ' + item['label'] + ' — ' + entry['kind'] + ' evidence on this exact visible surface',
                       url=entry['url']) for entry in evidence)
    record = dict(observation_id=identifier, label=item['label'], texture_set_id=ts.id, region_id=region_id,
        direction=[float(v) for v in item['direction']], resolution=[side, side],
        camera_transform=[[float(v) for v in row] for row in item['frame']['matrix']],
        projection=[[float(v) for v in row] for row in item['frame']['projection']],
        projection_kind='ORTHOGRAPHIC', origin='TOP_LEFT',
        world_per_pixel=float(2 * item['frame']['extent'] / side),
        visible_objects=sorted({geometry['objects'][index] for index in
                                np.unique(geometry['object_index'][visible]) if index < len(geometry['objects'])}),
        material_slots=sorted({int(value) for value in np.unique(slots)}) if len(slots) else [],
        surface_pixel_fraction=float(target.mean()),
        visible_surface_area_fraction=float(areas[visible].sum() / max(areas.sum(), 1e-12)),
        mean_view_angle=float(np.average(measured['facing'][chosen], weights=areas)) if len(areas) else 0.,
        occluded_area_fraction=float(areas[(measured['occlusion'][chosen] > .5) & visible].sum() / max(areas.sum(), 1e-12)),
        curvature_stable_fraction=flow['curvature_stable_fraction'],
        direction_sources=flow['sources'],
        missing_geometry_maps=missing,
        material_context=dict(name=ts.name, resolution=int(ts.resolution), layers=len(ts.layers),
                              note='The complete editable stack is current_context; this is the material '
                                   'this observation belongs to'),
        retained_identity='Camera transform, projection, resolution and the visible-surface, silhouette and '
                          'depth buffers are retained for this observation; its triangle, barycentric and UV '
                          'buffers are reproduced exactly from the same camera and evaluated mesh.',
        evidence=[dict(kind=entry['kind'], provenance_status=entry['provenance_status'], meaning=entry['meaning'])
                  for entry in evidence],
        direction_evidence=flow['meaning'],
        artist_prompt=captured['prompt'],
        meaning='Author appearance for the surface this observation actually shows. Every image in this '
                'packet refers to the same visible surface and the same pixels. Generated images must keep '
                'this exact camera framing, silhouette and resolution: Open Painter maps each generated '
                'pixel back through its visible triangle and barycentric point to a UV coordinate.',
        limitations=['Orthographic view; background and masked-out pixels are not this material.',
                     'Pixels near the surface mask boundary and behind occluders contribute nothing.',
                     'Direction evidence is geometric, never semantic anatomy.'],
        images=[dict(id=entry['id'], label=entry['label']) for entry in images])
    return record, images


def capture(context, ts, captured, arguments, objects):
    """Plan, render and register surface observations for one material or region."""
    budget(captured)
    started = time.monotonic()
    try:
        return _capture(context, ts, captured, arguments, objects)
    finally:
        budget(captured, started=started)


def _capture(context, ts, captured, arguments, objects):
    from ..authoring import regions
    identifier = arguments['texture_set_id']
    region_id = arguments.get('region_id', '')
    selected = regions.selector(ts, region_id, objects) if region_id else None
    geometry = scene_geometry(context, ts, selected)
    existing = registry(captured['id'], identifier)
    allowance = min(int(arguments.get('max_observations', 8)), MAX_OBSERVATIONS - len(existing))
    if allowance < 1:
        raise schema.AuthoringError('Surface observation budget exhausted for this instruction')
    from .viewport import principal_directions
    requested = arguments.get('directions') or list(BASE_VIEWS)
    labels = [name for name in requested if name in BASE_VIEWS][:allowance]
    directions = [(label, value) for label, value in principal_directions(context) if label in labels]
    if not directions:
        raise schema.AuthoringError('Choose observation directions among front, back, left, right, top and bottom')
    side = min(int(arguments.get('resolution', RENDER_SIDE)), observation.MAX_SIDE)
    planned = plan(context, ts, geometry, directions, side)
    if arguments.get('complete_coverage', True):
        planned = automatic(context, ts, geometry, planned, side, allowance)
    chosen = slice(0, geometry['target_count'])
    records, images = [], []
    packets = min(int(arguments.get('packet_limit', MAX_PACKETS)), MAX_PACKETS)
    ranked = sorted(range(len(planned)), key=lambda index: -float(
        (geometry['areas'][chosen] * planned[index]['measured']['score'][chosen]).sum()))
    best = set(ranked[:packets])
    for position, item in enumerate(planned):
        key = _identity(captured, item['label'], item['direction'], region_id, side, item['frame'])
        detailed = position in best
        record, pictures = _packet(context, context.scene, ts, captured, geometry, item, key, region_id=region_id)
        _store[key] = dict(request=captured['id'], texture_set_id=identifier, region_id=region_id,
                           view=_record(geometry, item['frame'], item['buffers'], item['target'],
                                        item['silhouette'], side),
                           record=record, images=pictures)
        records.append(record if detailed else {k: v for k, v in record.items()
                                                if k not in {'images', 'evidence', 'meaning', 'limitations'}})
        if detailed:
            images.extend(pictures)
    state = coverage.accumulate([dict((key, value[chosen]) for key, value in item['measured'].items())
                                 for item in planned], [item['label'] for item in planned],
                                count=geometry['target_count'])
    summary = coverage.summary(state, geometry['areas'][chosen])
    return dict(texture_set_id=identifier, region_id=region_id, observations=records,
                surface_coverage=summary, camera_selection='Six geometric directions, then cameras chosen '
                'from measured coverage of under-covered triangles while minimizing occlusion, edge-on '
                'viewing, duplicate coverage and silhouette ambiguity.',
                detailed_packets=[record['observation_id'] for record in records if 'images' in record],
                next_step='Generate one image per observation that keeps its exact framing and silhouette, '
                'then submit generate_surface_field listing each observation_id with its generated image. '
                'Request the remaining packets with inspect_surface_observation.',
                _images=images)


def packet(captured, arguments):
    """The full evidence packet of one already registered observation."""
    record = _store.get(arguments['observation_id'])
    if record is None or record['request'] != captured['id']:
        raise schema.AuthoringError('Unknown surface observation; capture observations for this instruction first')
    return dict(record['record'], _images=record['images'])


def surface_coverage(context, ts, captured, arguments, objects):
    """Measured observation coverage of the assigned surface, and its remaining holes."""
    from ..authoring import regions, surface_field
    region_id = arguments.get('region_id', '')
    selected = regions.selector(ts, region_id, objects) if region_id else None
    geometry = scene_geometry(context, ts, selected)
    entries = [value for value in _store.values()
               if value['request'] == captured['id'] and value['texture_set_id'] == ts.id
               and value['region_id'] == region_id]
    if not entries:
        raise schema.AuthoringError('Capture surface observations before inspecting their coverage')
    chosen = slice(0, geometry['target_count'])
    measured = []
    for entry in entries:
        view = entry['view']
        buffers = observation.rasterize(geometry['positions'], geometry['triangles'], geometry['owner'],
                                        view['matrix'], view['projection'], view['side'])
        target = (buffers['triangle'] >= 0) & (buffers['triangle'] < geometry['target_count'])
        silhouette = observation.silhouette_distance(target, buffers['depth'])
        result = coverage.observe(buffers, silhouette, count=len(geometry['triangles']),
                                  normals=geometry['normals'], areas=geometry['areas'],
                                  axis=view['axis'], pixel_world=view['pixel_world'])
        measured.append(dict((key, value[chosen]) for key, value in result.items()))
    state = coverage.accumulate(measured, [entry['record']['label'] for entry in entries],
                                count=geometry['target_count'])
    areas = geometry['areas'][chosen]
    needed = state['state'] != coverage.GOOD
    suggestions = [dict(direction=[float(v) for v in direction], under_covered_area=float(gain))
                   for gain, direction in coverage.rank_directions(
                       geometry['normals'][chosen], areas, needed,
                       [np.asarray(entry['record']['direction']) for entry in entries], limit=3)]
    triangles = geometry['triangles'][chosen]
    holes = []
    if needed.any():
        centers = geometry['positions'][triangles[needed]].mean(axis=1)
        normals = geometry['normals'][chosen][needed]
        span = float(np.max(np.ptp(centers, axis=0))) if len(centers) > 1 else 0.
        cells = np.floor((centers - centers.min(axis=0)) / (span / 5 + 1e-9)).astype(np.int64) if span else np.zeros((len(centers), 3), np.int64)
        keys, groups = np.unique(cells, axis=0, return_inverse=True)
        groups = np.asarray(groups).reshape(-1)
        for index in range(len(keys)):
            members = groups == index
            if not members.any():
                continue
            direction = normals[members].mean(axis=0)
            length = float(np.linalg.norm(direction))
            holes.append(dict(triangles=int(members.sum()),
                area_fraction=float(areas[needed][members].sum() / max(areas.sum(), 1e-12)),
                center=[float(v) for v in centers[members].mean(axis=0)],
                bounds_min=[float(v) for v in centers[members].min(axis=0)],
                bounds_max=[float(v) for v in centers[members].max(axis=0)],
                outward_direction=[float(v) for v in (direction / length)] if length > 1e-9 else [0., 0., 1.]))
        holes.sort(key=lambda item: -item['area_fraction'])
        holes = holes[:8]
    return dict(texture_set_id=ts.id, region_id=region_id, surface_coverage=coverage.summary(state, areas),
                observations=[entry['record']['observation_id'] for entry in entries],
                uncovered_surface=holes, suggested_directions=suggestions,
                fused_fields=surface_field.reports(),
                next_step='Capture a corrective observation for an uncovered area with inspect_surface_area, '
                          'generate its appearance and refuse it into the same field. Do not fill uncovered '
                          'surface by stretching nearby pixels.',
                meaning='Area-weighted observation quality over the assigned surface. MISSING means no '
                        'observation saw that geometry; WEAK means only edge-on, occluded or low-resolution '
                        'observations did.')


def corrective(context, ts, captured, arguments, objects):
    """Register one observation aimed at a specific piece of surface."""
    from ..authoring import regions
    budget(captured)
    started = time.monotonic()
    region_id = arguments.get('region_id', '')
    selected = regions.selector(ts, region_id, objects) if region_id else None
    geometry = scene_geometry(context, ts, selected)
    if len(registry(captured['id'], ts.id)) >= MAX_OBSERVATIONS:
        raise schema.AuthoringError('Surface observation budget exhausted for this instruction')
    center = np.asarray(arguments['center'], np.float64)
    chosen = slice(0, geometry['target_count'])
    triangles = geometry['triangles'][chosen]
    centers = geometry['positions'][triangles].mean(axis=1)
    radius = float(arguments.get('radius', 0.)) or float(np.max(np.ptp(centers, axis=0)) * .2 + 1e-6)
    near = np.linalg.norm(centers - center, axis=1) <= radius
    if not near.any():
        raise schema.AuthoringError('No assigned surface lies within that radius; inspect surface coverage for real uncovered areas')
    direction = np.asarray(arguments.get('direction') or geometry['normals'][chosen][near].mean(axis=0), np.float64)
    if np.linalg.norm(direction) < 1e-9:
        raise schema.AuthoringError('This surface faces in too many directions to average; choose an explicit direction')
    direction /= np.linalg.norm(direction)
    points = geometry['positions'][triangles[near]].reshape(-1, 3)
    side = min(int(arguments.get('resolution', RENDER_SIDE)), observation.MAX_SIDE)
    label = 'corrective_' + hashlib.sha256(center.tobytes()).hexdigest()[:6]
    frame, buffers, target, silhouette = rasterize(geometry, direction, coverage.up_for(direction), side, points=points)
    measured = coverage.observe(buffers, silhouette, count=len(geometry['triangles']),
                                normals=geometry['normals'], areas=geometry['areas'],
                                axis=frame['axes'][2], pixel_world=2 * frame['extent'] / side)
    item = dict(label=label, direction=direction, frame=frame, buffers=buffers,
                target=target, silhouette=silhouette, measured=measured)
    identifier = _identity(captured, label, direction, region_id, side, frame)
    record, images = _packet(context, context.scene, ts, captured, geometry, item, identifier, region_id=region_id)
    record['correction'] = ('Corrective observation framed on the requested surface only. Reproject it into '
                            'the same field together with the earlier observations; fusion weights decide '
                            'where it replaces weaker coverage.')
    _store[identifier] = dict(request=captured['id'], texture_set_id=ts.id, region_id=region_id,
                              view=_record(geometry, frame, buffers, target, silhouette, side),
                              record=record, images=images)
    budget(captured, started=started)
    return dict(record, _images=images)
