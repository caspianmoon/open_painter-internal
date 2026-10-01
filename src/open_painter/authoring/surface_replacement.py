"""Replace a reviewed surface source while preserving placement and unrelated pixels."""
import hashlib
import json
import struct

from . import document, schema
from .surface_maps import MAPPING, source_info
from ..utils.digest import sha256


def mapping_signature(layer):
    def normalize(value):
        if isinstance(value, (float, int)) and not isinstance(value, bool):
            # RNA stores most coordinates as float32; detached JSON can contain
            # integers or doubles. Compare at the authored storage precision.
            return struct.unpack('f', struct.pack('f', float(value)))[0]
        if isinstance(value, dict) and '$generated' in value:
            return {'generated': value['$generated']}
        if isinstance(value, dict):
            return {k: normalize(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [normalize(v) for v in value]
        if value is not None and hasattr(value, 'bl_rna'):
            if value.bl_rna.identifier == 'Image' and value.get('director_image_id'):
                return {'generated': value['director_image_id']}
            if value.bl_rna.identifier == 'Object':
                return {'object': value.get('open_painter_candidate_source', value.name_full)}
        return value
    base = next(p for p in layer['channels'] if p['channel'] == 'BASE_COLOR')
    value = dict(mapping={key: base[key] for key in MAPPING},
                 decal={key: value for key, value in layer.items() if key.startswith('decal_')},
                 masks=layer['masks'])
    return hashlib.sha256(json.dumps(document.encode(normalize(value), digest=True), sort_keys=True).encode()).hexdigest()


def assign(ts, layer, cmd, images, correspondences):
    from . import surface_maps
    from .generated_textures import placeholder
    packet = (correspondences or {}).get(cmd['correspondence_id'])
    source = next((item for item in packet['sources'] if item['layer_id'] == layer['id']), None) if packet else None
    if (not source or packet['surface']['texture_set_id'] != ts.id or
            source['reference_image_id'] != cmd['reference_image_id']):
        raise schema.AuthoringError('Inspect this candidate surface before replacing its source; use its correspondence_id and reference_image_id')
    if packet.get('review_scopes') and cmd['mode'] not in packet['review_scopes']:
        raise schema.AuthoringError('Source replacement scope differs from the visual critique; classify the defect before correcting it')
    corrections = packet.get('review_corrections') or []
    if corrections and 'TEXTURE_SOURCE' not in corrections:
        raise schema.AuthoringError('The critique classified this defect as ' + ', '.join(corrections) +
                                    ', not a texture-source error. Correct the observation or the fusion instead.')
    if layer['type'] != 'FILL' or mapping_signature(layer) != source['mapping_signature']:
        raise schema.AuthoringError('Surface placement changed since inspection; inspect a fresh candidate before replacing the source')
    base = next(p for p in layer['channels'] if p['channel'] == 'BASE_COLOR')
    if source.get('source_kind') == 'GENERATED_SURFACE_FIELD':
        raise schema.AuthoringError('This layer is a fused surface field, not a flat generated source. '
                                    'Correct it with a new or corrective surface observation and regenerate '
                                    'the field, or change which observations it fuses.')
    old_id, dimensions = source_info(base['image'], images)
    if old_id != cmd['reference_image_id']:
        raise schema.AuthoringError('Replacement reference differs from the inspected source')
    if cmd['image_id'] == old_id:
        raise schema.AuthoringError('A corrected source must be a newly edited image')
    new = placeholder(cmd['image_id'], 'sRGB', images)
    entry = images[cmd['image_id']]
    if abs(entry['width']/entry['height']-dimensions[0]/dimensions[1]) > .002:
        raise schema.AuthoringError('A source edit must preserve the original canvas aspect ratio')
    windows = source['source_windows'] if cmd['mode'] == 'LOCAL' else [[0., 0., 1., 1.]]
    if not windows:
        raise schema.AuthoringError('No local source footprint was found; correct the mapping and inspect again')
    replacement = dict(new, **{'$surface_replacement': cmd['mode'], 'reference': base['image'], 'windows': windows})
    base['image'] = replacement
    for prop in layer['channels']:
        for field in ('image', 'detail_image'):
            value = prop.get(field)
            # Keep supporting detail tied to precisely the same corrected pixels.
            derivation = value.get('$surface_detail') if isinstance(value, dict) else (
                str(value.get('director_surface_derivation', '')).split(':')[2]
                if value and value.get('director_surface_derivation') else None)
            if derivation:
                identifier, _ = source_info(value, images)
                if identifier == old_id:
                    prop[field] = dict(replacement, **{'$surface_detail': derivation})
        if prop.get('material_source_id') == old_id:
            prop['material_source_id'] = cmd['image_id']
        if prop.get('source_metadata'):
            metadata = json.loads(prop['source_metadata'])
            if metadata.get('source_id') == old_id:
                metadata.update(source_id=cmd['image_id'], replacement_of=old_id,
                    correction_mode=cmd['mode'], correspondence_id=cmd['correspondence_id'])
            if metadata.get('reference_image_id') == old_id:
                metadata.update(reference_image_id=cmd['image_id'], alignment_requires_review=True)
            prop['source_metadata'] = json.dumps(metadata, sort_keys=True)
    # Existing authored data maps stay editable; corrected maps use the SAME local windows.
    if cmd.get('maps'):
        previous = {p['channel']: document.clone(p) for p in layer['channels']}
        maps_cmd = dict(reference_image_id=cmd['image_id'], maps=cmd['maps'])
        if 'HEIGHT' in cmd['maps']:
            maps_cmd['height_amplitude'] = cmd.get('height_amplitude', previous['HEIGHT']['procedural_height'])
            if maps_cmd['height_amplitude'] <= 0:
                raise schema.AuthoringError('A corrected height map requires a nonzero height_amplitude')
            if cmd['mode'] == 'LOCAL' and abs(maps_cmd['height_amplitude']-previous['HEIGHT']['procedural_height']) > 1e-7:
                raise schema.AuthoringError('Local height corrections must retain the existing amplitude to preserve unrelated relief')
        surface_maps.assign(layer, maps_cmd, images)
        if cmd['mode'] == 'LOCAL':
            for prop in layer['channels']:
                if prop['channel'] not in cmd['maps']:
                    continue
                field = 'detail_image' if prop['channel'] == 'HEIGHT' else 'image'
                old = previous[prop['channel']][field]
                if old is None:
                    raise schema.AuthoringError('Add and review this data map before attempting a localized replacement')
                original = prop[field]
                prop[field] = dict(original, **{'$surface_replacement': 'LOCAL',
                    'replacement_payload': original, 'reference': old, 'windows': windows})
    # Alpha masks created by add_generated_detail must follow the edited source too.
    for mask in layer['masks']:
        value = mask.get('image')
        derivation = value.get('$surface_detail') if isinstance(value, dict) else (
            'ALPHA' if value and ':ALPHA:' in value.get('director_surface_derivation', '') else None)
        if derivation and source_info(value, images)[0] == old_id:
            mask['image'] = dict(replacement, **{'$surface_detail': derivation})


def window_mask(shape, windows):
    import numpy as np
    h, w = shape
    mask = np.zeros((h, w), np.float32)
    for x0, y0, x1, y1 in windows:
        a, b = max(0, int(x0*w)), min(w, int(np.ceil(x1*w)))
        c, d = max(0, int(y0*h)), min(h, int(np.ceil(y1*h)))
        # Two-texel inward feather, never alters pixels outside the inspected windows.
        xs = np.minimum(np.arange(b-a)+1, np.arange(b-a, 0, -1))/2
        ys = np.minimum(np.arange(d-c)+1, np.arange(d-c, 0, -1))/2
        mask[c:d, a:b] = np.maximum(mask[c:d, a:b], np.minimum(1, ys[:, None]*xs))
    return mask


def load(value, images):
    import bpy
    import numpy as np
    from .assets import prepare
    from ..core.image_manager import pixels, pack_image
    from ..evaluation.image_snapshots import read, stored_encoding
    from ..evaluation.blend_modes import sample_uv
    reference = prepare(value['reference'], images=images)
    original = pixels(reference)
    payload = value.get('replacement_payload', {key:value[key] for key in ('$generated', 'color_space')})
    edited = prepare(payload, images=images)
    rgba = read({}, edited, linear=True)
    if stored_encoding(reference) == 'SRGB':
        rgb = rgba[..., :3]
        rgba[..., :3] = np.where(rgb <= .0031308, rgb*12.92, 1.055*np.maximum(rgb, 0)**(1/2.4)-.055)
    h, w = original.shape[:2]
    if rgba.shape != original.shape:
        u, v = np.meshgrid((np.arange(w, dtype=np.float32)+.5)/w, (np.arange(h, dtype=np.float32)+.5)/h)
        rgba = sample_uv(rgba, u, v, 'BILINEAR', 'NONE')
    mask = window_mask((h, w), value['windows']) if value['$surface_replacement'] == 'LOCAL' else np.ones((h, w), np.float32)
    corrected = original.copy()
    chosen = mask > 0
    corrected[chosen] = original[chosen]*(1-mask[chosen, None]) + rgba[chosen]*mask[chosen, None]
    digest = sha256(corrected.tobytes()).hexdigest()
    key = value['$generated'] + ':' + value['color_space'] + ':' + digest
    for image in bpy.data.images:
        if image.get('director_surface_replacement') == key and not image.is_dirty and image.packed_file:
            return image
    image = bpy.data.images.new('Director corrected surface '+digest[:12], width=w, height=h, float_buffer=reference.is_float)
    image.colorspace_settings.name = reference.colorspace_settings.name
    image.pixels.foreach_set(corrected.ravel())
    image['director_image_id'] = value['$generated']
    image['director_surface_replacement'] = key
    image['director_replaced_source'] = reference.get('director_image_id', '')
    image['director_correction_mode'] = value['$surface_replacement']
    image.use_fake_user = True
    pack_image(image)
    return image
