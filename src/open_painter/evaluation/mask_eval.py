"""Resolve sequential grayscale mask sources with levels and arithmetic."""

from .metrics import measured
from .resource_keys import image_key
from .dependencies import map_dependencies

import numpy as np

from ..core.image_manager import pixels
from .blend_modes import sample_uv
from .image_snapshots import read


def map_pixels(ts, kind, cache):
    prop = next((m for m in ts.baked_maps if m.kind == kind and m.image), None)
    if prop is None:
        raise ValueError(f"This mask requires a baked {kind.title()} map. Bake mesh maps first.")
    key = prop.image.as_pointer()
    if key not in cache:
        cache[key] = read(cache, prop.image)
    return cache[key]


def _image_identity(image):
    return image_key(image)


def _effect_signature(effect):
    fields = ("kind", "enabled", "strength", "radius", "amount", "contrast", "gamma",
              "black", "white", "output_black", "output_white", "hue", "saturation",
              "lightness", "angle", "scale", "seed", "softness", "steps", "samples",
              "mode", "noise")
    result = tuple(getattr(effect, name) for name in fields)
    result += tuple(tuple(getattr(effect, name)) for name in
                    ("color", "color2", "offset", "transform_scale", "matrix", "bias"))
    # Surface space and the world direction pick the seam-aware path and the
    # directional field in apply_effect; a whole-image kind keeps the same
    # (0, n) cache range in both spaces, so only the signature can tell them apart.
    result += (getattr(effect, "space", "UV"), tuple(getattr(effect, "direction", (0., 0., -1.))))
    return result + (_image_identity(effect.image),)


def _live_field_identity(ts, mask):
    """A LIVE field's cache identity: its published image while the job engine
    runs (publication invalidates), the desired inputs when analysis is synchronous."""
    from ..evaluation import field_jobs
    field = mask.influence
    if field_jobs.active():
        return (_image_identity(field.image), field.provenance, getattr(field, 'falloff', 'LINEAR'))
    from ..generators.scene_fields import signature
    return (signature(ts, field), getattr(field, 'falloff', 'LINEAR'))


def _stack_signature(ts, layer, geometry_cache):
    """Parameter identities keep mesh-aware masks reusable across color edits.

    Painted/external fill pixels retain conservative edit-serial invalidation.
    Packed library inputs use image revisions, like channel detail, so changing
    roughness does not regenerate an unchanged image mask. Explicit force
    evaluation clears shared caches for external in-place pixel modifications.
    """
    memo = getattr(geometry_cache, "signatures", None)
    lookup = (layer.id, ts.edit_serial)
    if memo is not None and lookup in memo:
        return memo[lookup]

    result = []
    for mask in layer.masks:
        if not mask.enabled:
            result.append((False,))
            continue
        required = map_dependencies(mask)
        maps = tuple((item.kind, _image_identity(item.image)) for item in ts.baked_maps
                     if item.image and (required is None or item.kind in required))
        common = (mask.source, mask.operation, mask.invert, mask.black, mask.white, mask.strength,
                  getattr(mask, 'parent_id', ''))
        if mask.source == 'TEXT':
            from .text_source import signature
            extra = signature(mask.text)
        elif mask.source == "ANCHOR":
            extra = ()  # Pass-through; binding it to the edit serial would defeat generator caching.
        elif mask.source in {"GENERATOR", "NOISE"}:
            from .anchors import consumed
            anchors = consumed(mask)
            extra = (mask.generator, mask.amount, mask.contrast, mask.noise_scale,
                     mask.noise_strength, mask.seed, tuple(mask.id_color), maps,
                     getattr(mask, "edge_width", 1.0), getattr(mask, "edge_size", 1.0), anchors,
                     (getattr(mask, "detail_radius", 2.0), getattr(mask, "detail_height_strength", 1.0),
                      getattr(mask, "detail_normal_strength", 1.0), ts.edit_serial) if anchors else None,
                     _live_field_identity(ts, mask) if getattr(mask, "edge_source", "BAKED") == "LIVE" else None)
        elif mask.source == "PROCEDURAL":
            from .material_space import identity, enabled
            extra = (mask.procedural, mask.noise_scale, mask.seed, mask.projection,
                     tuple(mask.scale), mask.rotation, tuple(mask.offset), mask.tiling,
                     mask.triplanar_hardness, identity(mask),
                     getattr(geometry_cache, "geometry_stamp", None) if mask.projection == "TRIPLANAR" or enabled(mask) else None)
        elif mask.source == "BAKED":
            extra = (mask.map_type, maps)
        elif mask.source == "FILTER":
            from .operation_contracts import surface
            # A Surface-space filter pads each island from the set's seam map.
            extra = (_effect_signature(mask.effect), ts.edit_serial if mask.effect.image else None,
                     getattr(geometry_cache, "geometry_stamp", None) if surface(mask.effect) else None)
        elif mask.source == 'SCENE':
            extra = (_live_field_identity(ts, mask) if mask.influence.mode == 'LIVE' else
                     (_image_identity(mask.influence.image), mask.influence.update_serial),
                     getattr(mask.influence, 'falloff', 'LINEAR'))
        elif mask.source == 'REFERENCE':
            extra = (ts.edit_serial, mask.reference_layer, mask.reference_mask,
                     mask.reference_channel, mask.reference_stage, mask.reference_anchor)
        elif mask.source == 'STROKES':
            from ..painting.strokes import stroke_identity
            extra = (stroke_identity(ts.id_data.open_painter, mask.stroke_key, ts),
                     getattr(geometry_cache, "geometry_stamp", None))
        elif mask.source == 'MESH':
            from .mesh_masks import sampling_signature as mesh_signature
            extra = (mesh_signature(ts, mask), getattr(geometry_cache, "geometry_stamp", None))
        elif mask.source == 'GRAPH':
            from .custom_graph import signature as graph_signature
            extra = (graph_signature(mask.node_group), getattr(geometry_cache, "geometry_stamp", None))
        elif mask.source == 'GROUP':
            extra = ()
        else:
            # Bundled detail images are shared, read-only authoring inputs.
            # Managed writes still advance their image revision. Keep the old
            # conservative path for user images and native paint canvases.
            library_input = (mask.source == "FILL" and mask.projection == "UV"
                             and mask.image and mask.image.packed_file
                             and mask.image.get("open_painter_generated_source"))
            extra = (None if library_input else ts.edit_serial,
                     _image_identity(mask.image), mask.projection,
                     tuple(mask.scale), mask.rotation, tuple(mask.offset), mask.tiling,
                     mask.filtering, mask.triplanar_hardness,
                     getattr(geometry_cache, "geometry_stamp", None)
                     if mask.projection == "TRIPLANAR" else None)
        result.append(common + extra)
    result = tuple(result)
    if memo is not None:
        memo[lookup] = result
    return result


def evaluate_masks(ts, layer, n, y0, y1, cache, geometry_cache=None):
    geometry_cache = geometry_cache if geometry_cache is not None else {}
    from .channel_eval import band_rows
    from .operation_contracts import mask_footprint
    from .tiles import expand
    halo = mask_footprint(layer.masks)
    identity = ("mask_stack", ts.id, layer.id, _stack_signature(ts, layer, geometry_cache), n)
    if halo is None:
        key = (*identity, 0, n)
        if key in cache:
            return cache[key][y0:y1]
        if key not in geometry_cache:
            value = np.asarray(_evaluate_masks(ts, layer, n, 0, n, cache, geometry_cache), dtype=np.float32)
            geometry_cache[key] = value
        else:
            value = geometry_cache[key]
        # Every strip of the job reads the whole-image stack. The shared cache
        # may refuse or evict it under its allowance, so the job keeps its own
        # reference rather than rebuilding the seam-padded stack per strip.
        cache[key] = value
        return value[y0:y1]
    # A bounded footprint evaluates aligned strips (channel_eval.band_rows) and
    # retains just their own rows: a full sweep keeps n / rows entries in the
    # shared cache instead of one overlapping (16 + 2·halo)-row strip per tile,
    # which filled the allowance during a single 4K evaluation. The job holds
    # the current strip itself in case the allowance refuses it.
    rows = band_rows(halo, n)
    parts = []
    for strip_low in range(y0 // rows * rows, y1, rows):
        strip_high = min(strip_low + rows, n)
        key = (*identity, strip_low, strip_high)
        if key in cache:
            value = cache[key]
        else:
            if key not in geometry_cache:
                low, high = expand(strip_low, strip_high, halo, n)
                value = np.ascontiguousarray(_evaluate_masks(ts, layer, n, low, high, cache, geometry_cache)
                                             [strip_low - low:strip_high - low], dtype=np.float32)
                geometry_cache[key] = value
            else:
                value = geometry_cache[key]
            for stale in [k for k in cache if isinstance(k, tuple) and k[:len(identity)] == identity]:
                del cache[stale]
            cache[key] = value
        parts.append(value[max(y0 - strip_low, 0):min(y1, strip_high) - strip_low])
    return parts[0] if len(parts) == 1 else np.concatenate(parts)



def _combine(result, mask, value):
    """Fold one entry's coverage into the running result with its operation and strength."""
    if result is None:
        return 1 - mask.strength + value * mask.strength
    if mask.source == "FILTER" or mask.operation == "NORMAL":
        return result * (1 - mask.strength) + value * mask.strength
    if mask.operation == "MULTIPLY":
        return result * (1 - mask.strength + value * mask.strength)
    if mask.operation == "MAX":
        return result + (np.maximum(result, value) - result) * mask.strength
    if mask.operation == "MIN":
        return result + (np.minimum(result, value) - result) * mask.strength
    return np.clip(result + value * mask.strength, 0, 1)


def _levels(mask, value):
    value = np.clip((value - mask.black) / max(1e-6, mask.white - mask.black), 0, 1)
    return 1 - value if mask.invert else value


def _source_value(ts, layer, mask, n, y0, y1, cache, geometry_cache, u, v):
    """Raw coverage of one non-group, non-filter entry."""
    if mask.source == 'TEXT':
        from .text_source import ensure
        return sample_uv(pixels(ensure(mask.text, n)), u, v)[..., 0]
    if mask.source == 'REFERENCE':
        from .references import evaluate
        return evaluate(ts, mask, n, y0, y1, cache, geometry_cache)
    if mask.source == 'SCENE':
        from ..generators.scene_fields import evaluate
        return evaluate(ts, mask, u, v, cache)
    if mask.source == 'STROKES':
        from ..painting.stroke_raster import mask_rows
        return mask_rows(ts, mask, n, y0, y1, geometry_cache, cache=cache)
    if mask.source == 'MESH':
        from .mesh_masks import evaluate as evaluate_mesh_mask
        return evaluate_mesh_mask(ts, mask, n, y0, y1, geometry_cache, cache)
    if mask.source == 'GRAPH':
        from ..procedural_graph import is_native
        if is_native(mask.node_group):
            from ..procedural_graph.cpu import evaluate as evaluate_native
            return evaluate_native(ts, mask, n, y0, y1, cache, geometry_cache, u, v)
        from .custom_graph import evaluate as evaluate_graph
        return evaluate_graph(ts, mask, n, y0, y1, geometry_cache, cache)
    if mask.source == "PROCEDURAL":
        from types import SimpleNamespace
        from .channel_eval import _procedural_tile, _coordinates
        from .material_space import FIELDS, enabled
        prop = SimpleNamespace(procedural=mask.procedural,
            procedural_scale=mask.noise_scale, procedural_seed=mask.seed,
            projection=mask.projection, scale=mask.scale, rotation=mask.rotation,
            offset=mask.offset, triplanar_hardness=mask.triplanar_hardness)
        for name in FIELDS:
            if hasattr(mask, name):
                setattr(prop, name, getattr(mask, name))
        value = _procedural_tile(prop, n, y0, y1, ts, geometry_cache)
        if mask.projection == "UV" and mask.tiling == "NONE" and not enabled(mask):
            pu, pv = _coordinates(prop, n, y0, y1)
            value = value * ((pu >= 0) & (pu <= 1) & (pv >= 0) & (pv <= 1))
        return value
    if mask.source == "FILL":
        if not mask.image:
            raise ValueError(f"Fill mask image is missing on {layer.name}.")
        from .channel_eval import source
        return source(mask, "FILL", n, y0, y1, cache, ts,
                      geometry_cache if geometry_cache is not None else {})[..., 0]
    if mask.source == "PAINT":
        if not mask.image:
            raise ValueError(f"Painted mask image is missing on {layer.name}.")
        # Decoded like the shader's Image Texture node (an sRGB-tagged image an
        # artist picked is linearised; managed canvases are Non-Color).
        key = (mask.image.as_pointer(), "linear")
        if key not in cache:
            cache[key] = read(cache, mask.image, linear=True)
        data = cache[key]
        return data[y0:y1, :, 0] if data.shape[:2] == (n, n) else sample_uv(data, u, v)[..., 0]
    if mask.source == "BAKED":
        return sample_uv(map_pixels(ts, mask.map_type, cache), u, v)[..., 0]
    from ..generators.base import evaluate
    return evaluate(ts, mask, u, v, cache, resources=geometry_cache, rows=(n, y0, y1))


def _stack(ts, layer, masks, start, stop, n, y0, y1, cache, geometry_cache, u, v):
    """Coverage of entries [start, stop): groups evaluate their member run recursively."""
    from ..core.mask_stack import block
    result = None
    index = start
    while index < stop:
        mask = masks[index]
        if mask.source == 'GROUP':
            _, run_stop = block(masks, index)
            run_stop = min(run_stop, stop)
            if mask.enabled:
                value = _stack(ts, layer, masks, index + 1, run_stop, n, y0, y1, cache, geometry_cache, u, v)
                if value is None:
                    value = np.ones((y1 - y0, n), dtype=np.float32)
                result = _combine(result, mask, _levels(mask, value))
            index = run_stop
            continue
        index += 1
        if not mask.enabled or mask.source == "ANCHOR":
            continue
        if mask.source == "FILTER":
            from .channel_eval import apply_effect, seam_map_for
            if result is None:
                result = np.ones((y1 - y0, n), dtype=np.float32)
            seams = seam_map_for(ts, n, (mask.effect,)) if (y0, y1) == (0, n) else None
            value = apply_effect(result, mask.effect, seams)
            if value.ndim == 3:
                value = np.sum(value[..., :3] * np.array((.2126, .7152, .0722), dtype=np.float32), axis=-1)
            result = _combine(result, mask, _levels(mask, value))
            continue
        value = _source_value(ts, layer, mask, n, y0, y1, cache, geometry_cache, u, v)
        result = _combine(result, mask, _levels(mask, value))
    return result


@measured('masking')
def _evaluate_masks(ts, layer, n, y0, y1, cache, geometry_cache):
    u, v = np.meshgrid(
        (np.arange(n, dtype=np.float32) + 0.5) / n, (np.arange(y0, y1, dtype=np.float32) + 0.5) / n
    )
    masks = list(layer.masks)
    result = _stack(ts, layer, masks, 0, len(masks), n, y0, y1, cache, geometry_cache, u, v)
    return result if result is not None else np.ones((y1 - y0, n), dtype=np.float32)


def bake_mask_texture(ts, layer):
    """Bake the current grayscale effect stack into a packed, editable UV image."""
    from ..core import image_manager as images
    from .references import issues
    errors = issues(ts)
    if errors:
        raise ValueError(' | '.join(errors))

    n = int(ts.resolution)
    data = np.ones((n, n, 4), dtype=np.float32)
    cache, geometry_cache = {}, {}
    for y0 in range(0, n, 256):
        y1 = min(n, y0 + 256)
        coverage = evaluate_masks(ts, layer, n, y0, y1, cache, geometry_cache)
        data[y0:y1, :, :3] = coverage[..., None]
    image = images.create(f"{ts.name}_{layer.name}_BakedMask", n)
    images.write(image, data)
    return image
