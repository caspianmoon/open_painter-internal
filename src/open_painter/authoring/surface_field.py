"""A generated surface field: fused surface observations as ordinary UV textures.

Appearance is authored against rendered views of the real surface, then converted
into the existing UV atlas through exact geometry correspondence. Every channel of
one field is reprojected through the same pixel -> triangle -> barycentric point ->
UV mapping, so Base Color, Roughness, Height and Metallic cannot drift apart.

The result is a normal Open Painter source: editable, maskable and exportable. The
field descriptor records which observations and generated images produced it, which
region it affects and which channels it drives.
"""
import hashlib
import json
from collections import OrderedDict

from .schema import AuthoringError
from ..utils.digest import sha256

FIELD = '$surface_field'
VERSION = 1
MAX_OBSERVATIONS = 8
MAX_RESOLUTION = 2048
DERIVATION = 'LOCAL_LUMINANCE_CONTRAST_V1'
CHANNEL_IMAGES = dict(BASE_COLOR='base_color', ROUGHNESS='roughness', HEIGHT='height', METALLIC='metallic')
_fused = OrderedDict()
_reports = OrderedDict()


def _key(value):
    body = {k: v for k, v in value.items() if k not in {'channel', 'color_space', 'derivation'}}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def field_id(entries):
    """A stable identity for one fused field, shared by every channel it drives."""
    body = [[item['observation_id']] + [item.get(name, '') for name in sorted(CHANNEL_IMAGES.values())]
            for item in entries]
    return 'surface-field:' + hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:32]


def descriptor(ts, cmd, selected, entries, channel, field, *, derivation=''):
    """The JSON-safe record a fused channel is rebuilt from, identical per channel."""
    return {FIELD: VERSION, 'channel': channel, 'derivation': derivation,
            'color_space': 'sRGB' if channel == 'BASE_COLOR' and not derivation else 'Non-Color',
            'texture_set_id': ts.id, 'resolution': min(int(ts.resolution), MAX_RESOLUTION),
            'region_id': cmd.get('region_id', ''),
            'object_name': selected['object_name'] if selected else '',
            'region_faces': selected['region_faces'] if selected else '',
            'region_topology': selected['region_topology'] if selected else '',
            'observations': [dict(id=item['observation_id'],
                                  images={key: item[name] for key, name in CHANNEL_IMAGES.items() if item.get(name)})
                             for item in entries],
            'field': field}


def channels_for(cmd):
    """The channels this field drives: generated images first, derived scalars after."""
    provided = {channel for item in cmd['observations'] for channel, name in CHANNEL_IMAGES.items() if item.get(name)}
    if 'BASE_COLOR' not in provided:
        raise AuthoringError('Every surface observation needs generated Base Color appearance for its visible surface')
    result = {'BASE_COLOR': ''}
    for channel in ('ROUGHNESS', 'HEIGHT', 'METALLIC'):
        if channel in provided:
            result[channel] = ''
        elif channel == 'ROUGHNESS' and cmd.get('roughness_range'):
            result[channel] = DERIVATION
        elif channel == 'HEIGHT' and cmd.get('height_amplitude'):
            result[channel] = DERIVATION
    return result


def assign(ts, layer, cmd, images, observations, objects):
    """Attach one fused surface field to an editable fill layer."""
    from . import document, regions
    if layer['type'] != 'FILL':
        raise AuthoringError('A generated surface field belongs on an editable fill layer')
    entries = cmd['observations']
    if not 1 <= len(entries) <= MAX_OBSERVATIONS:
        raise AuthoringError(f'A surface field fuses between one and {MAX_OBSERVATIONS} observations')
    if len({item['observation_id'] for item in entries}) != len(entries):
        raise AuthoringError('List each observation once; fusion already weights repeated coverage')
    known = observations or {}
    for item in entries:
        record = known.get(item['observation_id'])
        if record is None or record['texture_set_id'] != ts.id:
            raise AuthoringError('Capture surface observations for this material before generating its surface field')
        if record.get('region_id', '') != cmd.get('region_id', ''):
            raise AuthoringError('Observation region differs from the generation region; capture observations for that region')
        for name in CHANNEL_IMAGES.values():
            if not item.get(name):
                continue
            if item[name] not in (images or {}):
                raise AuthoringError('Call list_generated_images and use an image_id returned for this instruction')
            entry = images[item[name]]
            # An observation is square; a differently shaped image would be stretched
            # across the surface instead of matching the framing it was authored against.
            if entry['width'] != entry['height']:
                raise AuthoringError('A generated observation image must keep the observation\'s square '
                                     'framing; generate it at the observation resolution')
    if any(item.get('height') for item in entries) and not cmd.get('height_amplitude'):
        raise AuthoringError('A generated height observation needs a nonzero height_amplitude; '
                             'inspect the relief in the candidate closeup')
    low, high = cmd.get('roughness_range', [.3, .6])
    if low > high:
        raise AuthoringError('Roughness range must be increasing')
    if high - low > .7:
        raise AuthoringError('Keep generated roughness variation within a 0.7 range')
    selected = regions.selector(ts, cmd['region_id'], objects) if cmd.get('region_id') else None
    field = field_id(entries)
    channels = channels_for(cmd)
    relief = cmd.get('height_amplitude', 0)
    metadata = dict(version=1, type='GENERATED_SURFACE_FIELD', name=cmd['name'], purpose=cmd['purpose'],
        channels=sorted(channels), region_id=cmd.get('region_id', ''),
        field_id=field, observations=[item['observation_id'] for item in entries],
        generated_images=sorted({item[name] for item in entries for name in CHANNEL_IMAGES.values() if item.get(name)}),
        derived_channels=sorted(key for key, value in channels.items() if value),
        provenance='SURFACE_OBSERVATION_REPROJECTION_V1',
        correspondence='pixel -> visible triangle -> barycentric point -> UV, shared by every channel',
        fusion='Confidence-weighted multi-view fusion using facing quality, visibility, projected '
               'resolution and silhouette confidence; occluded observations contribute nothing',
        calibrated_bundle=False,
        limitation='Derived scalar channels are art-directed luminance approximations, not measured PBR')
    encoded = json.dumps(metadata, sort_keys=True)
    for prop in layer['channels']:
        channel = prop['channel']
        if channel == 'HEIGHT' and 'HEIGHT' not in channels:
            # A field explicitly without relief must not keep unrelated procedural height.
            prop.update(enabled=False, image=None, detail_image=None, procedural='', anchor='')
            continue
        if channel not in channels:
            continue
        value = descriptor(ts, cmd, selected, entries, channel, field, derivation=channels[channel])
        prop.update(projection='UV', tiling='NONE', scale=[1, 1], offset=[0, 0], rotation=0,
                    material_mapping_version=0, enabled=True, image=None, detail_image=None,
                    procedural='', anchor='', material_source_id=value['field'],
                    source_metadata=encoded)
        if channel == 'BASE_COLOR':
            prop['image'] = value
        elif channel == 'HEIGHT':
            # Relief always drives the composed height, never a second normal map.
            prop.update(detail_image=value, value=.5, procedural_height=relief)
        elif channels[channel]:
            # A luminance approximation modulates the authored value; it does not replace it.
            prop.update(detail_image=value, value=(low + high) / 2, procedural_detail=(high - low) / .35)
        else:
            prop['image'] = value
    mask = document.defaults('OP_Mask')
    identifier = layer['id'] + '.surface-field-coverage'
    mask.update(id=identifier, name='Fused surface coverage', source='FILL', operation='MULTIPLY',
                projection='UV', tiling='NONE',
                image=descriptor(ts, cmd, selected, entries, 'COVERAGE', field))
    layer['masks'] = [item for item in layer['masks'] if item['id'] != identifier] + [mask]
    if selected:
        region = document.defaults('OP_Mask')
        region.update(id=layer['id'] + '.surface-field-region', name='Generated surface region',
                      source='MESH', operation='MULTIPLY')
        region['mesh'].update(selected)
        layer['masks'] = [item for item in layer['masks'] if item['id'] != region['id']] + [region]


def texture_set(identifier):
    """The artist's texture set, never a disposable candidate copy of it."""
    import bpy
    scenes = [scene for scene in bpy.data.scenes if not scene.get('open_painter_candidate')]
    current = getattr(bpy.context, 'scene', None)
    if current is not None and not current.get('open_painter_candidate'):
        scenes.insert(0, current)
    for scene in scenes:
        for ts in scene.open_painter.texture_sets:
            if ts.id == identifier:
                return ts
    raise AuthoringError('The surface field\'s material is no longer available; run the instruction again')


def _surface(ts, value):
    """Concatenated assigned triangles, their UVs, corner normals and paired seams."""
    import numpy as np
    from ..geometry.snapshots import targets
    from ..geometry.texture_correspondence import seam_pairs
    from .regions import topology
    positions, triangles, uv, normals, seams = [], [], [], [], []
    offset = 0
    with targets(ts) as found:
        for target in found:
            keep = np.ones(len(target['tri_vertices']), bool)
            if value['object_name'] and target['object'].name_full == value['object_name']:
                if topology(target['mesh']) != value['region_topology']:
                    raise AuthoringError('Region topology changed since the observations were captured')
                keep = np.isin(target['tri_polygons'], json.loads(value['region_faces']))
            elif value['object_name']:
                continue
            if not keep.any():
                continue
            triangles.append(target['tri_vertices'][keep] + offset)
            uv.append(target['uv'][target['tri_loops'][keep]])
            normals.append(target['world_normals'][target['tri_loops'][keep]])
            positions.append(target['positions'])
            offset += len(target['positions'])
            pairs, _, _ = seam_pairs(target, target['object'].name_full, ts.id)
            seams.extend(pairs)
    if not triangles:
        raise AuthoringError('The surface field has no assigned geometry to reproject onto')
    return (np.concatenate(positions), np.concatenate(triangles), np.concatenate(uv),
            np.concatenate(normals), seams)


def _images(value, images):
    """Top-left-origin linear pixels of every generated observation image, per channel."""
    import numpy as np
    from ..evaluation.image_snapshots import read
    from .generated_textures import load as load_generated
    wanted = sorted({channel for item in value['observations'] for channel in item['images']})
    result = {channel: [] for channel in wanted}
    for item in value['observations']:
        for channel in wanted:
            identifier = item['images'].get(channel)
            if identifier is None:
                result[channel].append(None)
                continue
            space = 'sRGB' if channel == 'BASE_COLOR' else 'Non-Color'
            rgba = np.asarray(read({}, load_generated({'$generated': identifier, 'color_space': space}, images),
                                   linear=True), np.float32)
            if not np.isfinite(rgba).all():
                raise AuthoringError('A generated observation image contains invalid pixels')
            pixels = rgba[::-1, :, :3] if channel == 'BASE_COLOR' else rgba[::-1, :, :1]
            result[channel].append(np.ascontiguousarray(pixels))
    return result


def resolve(value, images):
    """Fuse every channel of one field once; channels share the same correspondence."""
    import numpy as np
    from ..geometry import surface_fusion as fusion, surface_seams as seams_module
    from ..ai import surface_observations
    key = _key(value)
    found = _fused.get(key)
    if found is not None:
        return found
    ts = texture_set(value['texture_set_id'])
    resolution = int(value['resolution'])
    positions, triangles, uv, normals, seams = _surface(ts, value)
    texels = fusion.texels(uv, positions, triangles, normals, resolution)
    texel_world = fusion.texel_world_size(texels, positions, triangles, uv)
    sources = _images(value, images)
    views = []
    for index, item in enumerate(value['observations']):
        record = surface_observations.view(item['id'], value['texture_set_id'])
        generated = next((channel[index] for channel in sources.values() if channel[index] is not None), None)
        # A generated image coarser than its observation resolves less surface detail,
        # and its contribution weight must say so rather than assume the camera's rate.
        scale = record['side'] / max(generated.shape[0], 1) if generated is not None else 1.
        views.append(dict(record, pixel_world=record['pixel_world'] * max(scale, 1.)))
    result = fusion.fuse(texels, views, sources, texel_world=texel_world)
    state = fusion.classify(result)
    covered = np.zeros((resolution, resolution), bool)
    covered[texels['rows'], texels['cols']] = state != fusion.MISSING
    atlases = {}
    for channel, fused in result['values'].items():
        atlas = np.zeros((resolution, resolution, fused.shape[1]), np.float32)
        fusion.write(atlas, texels, fused)
        atlases[channel] = atlas
    carried = seams_module.carry(list(atlases.values()), covered, seams, resolution)
    while len(_fused) >= 2:   # Fused atlases are large; keep only what a layer needs.
        _fused.pop(next(iter(_fused)))
    report = dict(fusion.report(state, resolution=resolution), field_id=value['field'],
                  region_id=value['region_id'], seam_carried_texels=carried,
                  observations=[item['id'] for item in value['observations']],
                  world_per_texel=texel_world, holes=fusion.hole_clusters(texels, state))
    _fused[key] = dict(atlases=atlases, covered=covered, texels=texels, state=state,
                       report=report, texel_world=texel_world)
    if len(_reports) > 8:
        _reports.pop(next(iter(_reports)))
    _reports[value['field']] = report
    return _fused[key]


def reports():
    """Measured fusion coverage of every field resolved during this instruction."""
    return {key: dict(value) for key, value in _reports.items()}


def forget():
    """Drop fused atlases and their reports; owned by one instruction."""
    _fused.clear()
    _reports.clear()


def coverage(value, images):
    """Measured fusion coverage of a field, for hole detection and correction."""
    return resolve(value, images)['report']


def load(value, images):
    """Resolve one fused channel into a packed, editable UV image datablock."""
    import bpy
    import numpy as np
    from ..core.image_manager import pack_image
    from ..evaluation.uv_gutters import padded
    state = resolve(value, images)
    channel = value['channel']
    resolution = int(value['resolution'])
    covered = state['covered']
    if channel == 'COVERAGE':
        data = covered.astype(np.float32)[..., None].repeat(3, axis=2)
    else:
        # Sampling gutters use the exporter's ownership; seam continuation already
        # carried appearance across island boundaries before this fills the rest.
        # Derived detail reads the padded colour, so an island edge is not a cliff.
        ts = texture_set(value['texture_set_id'])
        if value.get('derivation') == DERIVATION:
            from .generated_surfaces import scalar_detail
            data = scalar_detail(padded(ts, state['atlases']['BASE_COLOR']))[..., None].repeat(3, axis=2)
        else:
            source = state['atlases'].get(channel)
            if source is None:
                raise AuthoringError('This surface field has no generated ' + channel + ' observations')
            data = padded(ts, source if source.shape[2] == 3 else source[..., :1].repeat(3, axis=2))
    rgba = np.ones((resolution, resolution, 4), np.float32)
    rgba[..., :3] = np.clip(np.nan_to_num(data, nan=0., posinf=1., neginf=0.), 0, None)
    digest = sha256(rgba.tobytes()).hexdigest()
    identity = _key(value) + ':' + channel + ':' + str(value.get('derivation', '')) + ':' + digest
    for image in bpy.data.images:
        if image.get('director_surface_field') == identity and not image.is_dirty and image.packed_file:
            return image
    image = bpy.data.images.new('Director surface field ' + channel.title() + ' ' + digest[:12],
                                width=resolution, height=resolution, float_buffer=True)
    image.colorspace_settings.name = 'Linear Rec.709' if value['color_space'] == 'sRGB' else 'Non-Color'
    image.pixels.foreach_set(rgba.ravel())
    image['director_image_id'] = value['field'] or ('surface-field:' + _key(value)[:24])
    image['director_surface_field'] = identity
    image['director_surface_field_channel'] = channel
    image['director_surface_field_observations'] = json.dumps([item['id'] for item in value['observations']])
    image['director_surface_field_coverage'] = json.dumps(state['report']['classes'])
    image['director_surface_note'] = ('Fused from surface observations through exact geometry '
                                      'correspondence; derived scalars are approximations, not measured PBR')
    image.use_fake_user = True
    pack_image(image)
    return image
