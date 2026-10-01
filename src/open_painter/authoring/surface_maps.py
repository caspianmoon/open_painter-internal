"""Reference-conditioned scalar maps sharing the original surface's placement.

Registration is a provider claim to inspect, not a guarantee inferred from size.
Normal relief is evaluated/exported from HEIGHT by the existing surface pipeline.
"""
import base64
import json

from .schema import AuthoringError
from ..utils.digest import sha256

MAPPING = ('projection', 'scale', 'offset', 'rotation', 'tiling', 'filtering',
           'triplanar_hardness', 'material_mapping_version', 'material_meters_per_unit',
           'material_image_extent_m', 'material_frame', 'material_origin',
           'material_axis_u', 'material_axis_v', 'material_directional')


def source_info(source, images):
    if isinstance(source, dict) and '$generated' in source:
        entry = (images or {}).get(source['$generated'])
        if entry:
            return source['$generated'], (entry['width'], entry['height'])
    elif source is not None and not isinstance(source, dict):
        return source.get('director_image_id', ''), tuple(source.size)
    raise AuthoringError('A matching map set needs an existing generated base-color image')


def assign(layer, cmd, images):
    from .generated_textures import placeholder
    if layer['type'] != 'FILL':
        raise AuthoringError('Surface maps belong on the fill carrying their reference albedo')
    base = next(p for p in layer['channels'] if p['channel'] == 'BASE_COLOR')
    reference_id, size = source_info(base['image'], images)
    if not reference_id or reference_id != cmd['reference_image_id']:
        raise AuthoringError('Reference does not match this layer. Call inspect_surface_source first')
    maps = cmd['maps']
    if not maps:
        raise AuthoringError('Assign at least one matching scalar map')
    if 'HEIGHT' in maps and 'height_amplitude' not in cmd:
        raise AuthoringError('A height map needs a nonzero height_amplitude; inspect relief in the preview')
    if 'HEIGHT' not in maps and 'height_amplitude' in cmd:
        raise AuthoringError('height_amplitude requires a HEIGHT map')
    for channel, identifier in maps.items():
        original = placeholder(identifier, 'Non-Color', images)
        entry = images[identifier]
        if identifier == reference_id:
            raise AuthoringError('Generate a scalar map; do not reuse the albedo as physical data')
        if abs(entry['width'] / entry['height'] - size[0] / size[1]) > .002:
            raise AuthoringError('Scalar map must preserve the reference canvas aspect ratio')
        prop = next(p for p in layer['channels'] if p['channel'] == channel)
        prop.update({key: base[key] for key in MAPPING})
        prop.update(enabled=True, anchor='', procedural='', image=None, detail_image=None,
                    material_source_id=identifier)
        resolved = dict(original, **{'$surface_map': channel, 'reference': base['image']})
        if channel == 'HEIGHT':
            prop.update(detail_image=resolved, value=.5, procedural_height=cmd['height_amplitude'])
            if prop['material_mapping_version']:
                raise AuthoringError('Metric surface maps require a physical relief amplitude; use native mapping for this command')
        else:
            prop['image'] = resolved
        prop['source_metadata'] = json.dumps(dict(version=1, type='GENERATED_SURFACE_MAP',
            source_id=identifier, reference_image_id=reference_id, channels=[channel],
            provenance='REFERENCE_CONDITIONED_GENERATED_MAP', calibrated_bundle=False,
            mapping_frame={key: prop[key] for key in MAPPING},
            source_dimensions=[entry['width'], entry['height']], reference_dimensions=list(size),
            normal_derivation='COMPOSED_HEIGHT_SURFACE_GRADIENT' if channel == 'HEIGHT' else None,
            limitation='Shared placement and reference coverage enforced; semantic alignment requires visual inspection'), sort_keys=True)


def inspect(ts, layer_id):
    from ..ai.visual_context import thumbnail
    layer = next((item for item in ts.layers if item.id == layer_id), None)
    if layer is None:
        raise AuthoringError('Surface layer is outside this material')
    prop = next(p for p in layer.channels if p.channel == 'BASE_COLOR')
    identifier, size = source_info(prop.image, {})
    if not identifier:
        raise AuthoringError('Choose a layer with generated base color')
    # Original packed PNG is the generation reference, without a lossy viewport crop.
    data = bytes(prop.image.packed_file.data) if prop.image.packed_file and not prop.image.is_dirty else b''
    if data.startswith(b'\x89PNG\r\n\x1a\n') and len(data) <= 8 * 1024 * 1024:
        url = 'data:image/png;base64,' + base64.b64encode(data).decode()
    else:
        url = thumbnail(prop.image, side=2048, budget=8 * 1024 * 1024)['url']
    return dict(layer_id=layer_id, reference_image_id=identifier, dimensions=list(size),
        projection=prop.projection, height_distance=ts.height_distance,
        meaning='Exact original albedo canvas. Generate separate grayscale HEIGHT, ROUGHNESS and METALLIC maps with the same silhouette, orientation and feature positions. Height: 0.5 is the flat substrate, lighter raised, darker recessed; no illumination, color/pigment, AO or cast shadows. Roughness: black smooth, white rough. Metallic: white exposed metal, black wood/polymer/paint/rust/dirt. Preserve canvas aspect and placement; do not redraw or invent features. Normal is derived from height, not independently painted.',
        _images=[dict(id='surface-source:' + layer_id, label='Original surface reference', url=url)])


def load(value, images):
    import bpy
    import numpy as np
    from .assets import prepare
    from .generated_textures import load as load_original
    from ..core.image_manager import pack_image
    source = load_original({key: value[key] for key in ('$generated', 'color_space')}, images)
    reference = prepare(value['reference'], images=images)

    def pixels(image):
        buffer = np.empty(len(image.pixels), np.float32)
        image.pixels.foreach_get(buffer)
        return buffer.reshape(image.size[1], image.size[0], 4)

    rgba, original = pixels(source), pixels(reference)
    if not np.isfinite(rgba).all():
        raise AuthoringError('Scalar map contains invalid pixels')
    if float(np.percentile(np.ptp(rgba[..., :3], axis=2), 99)) > .04:
        raise AuthoringError('Scalar maps must be grayscale data, not colored material renders or normal maps')
    # Same normalized canvas even when the provider chooses a different resolution.
    # Bilinear reference alpha prevents an opaque data background covering other layers.
    h, w = rgba.shape[:2]
    rh, rw = original.shape[:2]
    x = np.clip((np.arange(w) + .5) * rw / w - .5, 0, rw - 1)
    y = np.clip((np.arange(h) + .5) * rh / h - .5, 0, rh - 1)
    x0, y0 = x.astype(int), y.astype(int)
    x1, y1 = np.minimum(x0+1, rw-1), np.minimum(y0+1, rh-1)
    fx, fy = (x-x0)[None, :], (y-y0)[:, None]
    alpha = original[..., 3]
    coverage = ((alpha[y0[:, None], x0] * (1-fx) + alpha[y0[:, None], x1] * fx) * (1-fy)
                + (alpha[y1[:, None], x0] * (1-fx) + alpha[y1[:, None], x1] * fx) * fy)
    rgba[..., :3] = rgba[..., :3].mean(axis=2, keepdims=True)
    rgba[..., 3] = coverage * rgba[..., 3]
    digest = sha256(rgba.tobytes()).hexdigest()
    key = value['$generated'] + ':' + value['$surface_map'] + ':' + digest
    for image in bpy.data.images:
        if image.get('director_surface_map') == key and not image.is_dirty and image.packed_file:
            return image
    image = bpy.data.images.new('Director ' + value['$surface_map'] + ' ' + digest[:12], width=w, height=h, float_buffer=True)
    image.colorspace_settings.name = 'Non-Color'
    image.pixels.foreach_set(rgba.ravel())
    image['director_image_id'] = value['$generated']
    image['director_surface_map'] = key
    image['director_surface_reference'] = reference.get('director_image_id', '')
    image.use_fake_user = True
    pack_image(image)
    return image
