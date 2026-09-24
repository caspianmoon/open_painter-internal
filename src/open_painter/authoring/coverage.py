"""Measured effect coverage and monotonic native Levels-based reductions.

Surface samples use deterministic triangle-area weights, not UV pixel counts.
No hidden semantic annotations, generated replacement masks, or scene edits.
"""
import math

from .schema import AuthoringError
from ..utils.digest import sha256

THRESHOLD = .5
MAX_SAMPLES = 262144
MIN_COVERAGE = .001


def _layer(ts, identifier):
    layer = next((layer for layer in ts.layers if layer.id == identifier), None)
    if layer is None:
        raise AuthoringError('Choose an existing effect layer or folder')
    if not any(mask.enabled for mask in layer.masks):
        raise AuthoringError('This layer has no placement mask to inspect or reduce')
    return layer


def _buffer(ts, layer, *, omit=''):
    import numpy as np
    from ..evaluation.mask_eval import evaluate_masks
    from ..evaluation.snapshots import capture
    from ..evaluation.raster_cache import RasterCache
    from ..evaluation.resource_keys import stable_keys
    from ..generators.scene_fields import signature
    n = int(ts.resolution)
    if n > 4096:
        raise AuthoringError('Coverage analysis supports authored resolutions up to 4096')
    # Scene-field evaluate() can otherwise publish images onto the live author,
    # even when given captured parameters. Compilation is read-only: require
    # settled derived fields first, including fields reached through references.
    for owner in ts.layers:
        for mask in owner.masks:
            if not mask.enabled:
                continue
            if mask.source == 'GRAPH':
                raise AuthoringError('Custom graph mask coverage requires a settled scratch preview')
            if mask.source == 'SCENE' or (mask.source == 'GENERATOR' and mask.edge_source == 'LIVE'):
                field = mask.influence
                if not field.image or (field.mode == 'LIVE' and field.provenance != repr(signature(ts, field))):
                    raise AuthoringError('Wait for scene-field analysis to finish before measuring coverage')
    view = capture(layer)
    if omit:
        view = view.replaced(masks=tuple(mask for mask in view.masks if mask.id != omit))
    # A detached candidate with the coverage filter omitted shares the artist's
    # edit serial, so it must not reuse the live per-serial signature memo.
    resources = RasterCache()
    with stable_keys():
        values = evaluate_masks(ts, view, n, 0, n, {}, resources)
    values = np.asarray(values, np.float32)
    if values.shape != (n, n) or not np.isfinite(values).all():
        raise AuthoringError('Effect coverage could not be evaluated as finite mask values')
    return np.clip(values, 0, 1)


def _surface_samples(ts):
    import numpy as np
    from ..geometry.snapshots import targets
    uv_triangles, areas = [], []
    with targets(ts) as meshes:
        for target in meshes:
            xyz = target['positions'][target['tri_vertices']]
            areas.append(np.linalg.norm(np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0]), axis=1) * .5)
            uv_triangles.append(target['uv'][target['tri_loops']])
    areas, uv = np.concatenate(areas), np.concatenate(uv_triangles)
    if len(areas) > MAX_SAMPLES:
        raise AuthoringError('Coverage analysis supports at most 262144 assigned triangles per texture set')
    total = float(areas.sum())
    if not math.isfinite(total) or total <= 1e-15:
        raise AuthoringError('The assigned region has no measurable surface area')
    duv1, duv2 = uv[:, 1] - uv[:, 0], uv[:, 2] - uv[:, 0]
    uv_area = np.abs(duv1[:, 0] * duv2[:, 1] - duv1[:, 1] * duv2[:, 0])
    valid = np.isfinite(uv).all(axis=(1, 2)) & (uv_area > 1e-12) & (uv >= 0).all(axis=(1, 2)) & (uv <= 1).all(axis=(1, 2))
    # Every triangle remains in the denominator. Invalid charts get no inferred
    # coverage and their exact area is disclosed; the solver refuses these inputs.
    counts = 1 + np.floor(areas / total * (MAX_SAMPLES - len(areas))).astype(np.int64)
    triangle = np.repeat(np.arange(len(areas)), counts)
    index = np.arange(len(triangle), dtype=np.float64) + .5
    root = np.sqrt(np.mod(index * .7548776662466927, 1.))
    second = np.mod(index * .5698402909980532, 1.)
    bary = np.stack((1-root, root*(1-second), root*second), axis=1)
    coordinates = np.einsum('ni,nij->nj', bary, uv[triangle])
    weight = np.repeat(areas / counts, counts)
    sample_valid = valid[triangle]
    return coordinates, weight, sample_valid, dict(surface_area_world=total,
        invalid_uv_surface_area_world=float(areas[~valid].sum()),
        invalid_uv_surface_fraction=float(areas[~valid].sum() / total),
        triangles=len(areas), samples=len(triangle),
        sampling='DETERMINISTIC_AREA_ALLOCATED_TRIANGLE_SAMPLES_V1',
        invalid_uv_policy='RETAIN_AREA_IN_DENOMINATOR_ZERO_UNKNOWN_COVERAGE',
        seam_policy='BARYCENTRIC_INTERIOR_LINEAR_CLAMP_SAMPLING')


def _sample(buffer, uv, valid):
    import numpy as np
    n = len(buffer)
    coords = np.clip(np.nan_to_num(uv), 0, 1) * n - .5
    lower = np.floor(coords).astype(np.int64)
    fraction = coords - lower
    x0, y0 = np.clip(lower[:, 0], 0, n-1), np.clip(lower[:, 1], 0, n-1)
    x1, y1 = np.clip(lower[:, 0]+1, 0, n-1), np.clip(lower[:, 1]+1, 0, n-1)
    fx, fy = fraction[:, 0], fraction[:, 1]
    values = (buffer[y0,x0]*(1-fx)+buffer[y0,x1]*fx)*(1-fy)+(buffer[y1,x0]*(1-fx)+buffer[y1,x1]*fx)*fy
    return np.where(valid, values, 0.)


def inspect(ts, layer_id):
    layer = _layer(ts, layer_id)
    buffer = _buffer(ts, layer)
    uv, weights, valid, info = _surface_samples(ts)
    values = _sample(buffer, uv, valid)
    occupied = float(weights[values > THRESHOLD].sum())
    return dict(layer_id=layer_id, threshold=THRESHOLD, coverage=occupied / info['surface_area_world'],
                occupied_surface_area_world=occupied, evaluated_mask_sha256=sha256(buffer.tobytes()).hexdigest(),
                source_mask_ids=[mask.id for mask in layer.masks], opacity_excluded=True, **info)


def reduction(ts, layer_id, ratio=.6, tolerance=.06):
    import numpy as np
    from types import SimpleNamespace
    from . import document
    from ..evaluation.channel_eval import apply_effect
    if not math.isfinite(ratio) or not 0 < ratio < 1 or not 0 < tolerance <= .1:
        raise AuthoringError('Coverage reduction needs a ratio between zero and one and tolerance at most 0.1')
    layer = _layer(ts, layer_id)
    identifier = layer_id + '.coverage'
    existing = next((mask for mask in layer.masks if mask.id == identifier), None)
    if existing is not None and layer.masks[-1].id != identifier:
        raise AuthoringError('Keep the coverage control last in this effect mask stack before measuring a reduction')
    current = _buffer(ts, layer)
    base = _buffer(ts, layer, omit=identifier) if existing is not None else current
    uv, weights, valid, info = _surface_samples(ts)
    if info['invalid_uv_surface_fraction'] > 1e-6:
        raise AuthoringError('Repair invalid render UVs before solving surface coverage; their area cannot be discarded')
    current_values = _sample(current, uv, valid)
    before = float(weights[current_values > THRESHOLD].sum() / info['surface_area_world'])
    if before < MIN_COVERAGE:
        raise AuthoringError('Coverage is near zero; a reduction ratio is not meaningful')
    # Only an existing monotonic control that this module produced can be updated.
    if existing is not None:
        e = existing.effect
        if not (existing.source == 'FILTER' and existing.enabled and existing.strength == 1 and
                not existing.invert and existing.black == 0 and existing.white == 1 and
                e.kind == 'LEVELS' and e.enabled and e.strength == 1 and e.white == 1 and
                e.gamma == 1 and e.output_black == 0 and e.output_white == 1):
            raise AuthoringError('The existing coverage control was edited; inspect it before another automatic reduction')
    lower = float(existing.effect.black) if existing is not None else 0.
    goal = before * ratio
    # Search actual filtered pixel buffers: filtering before bilinear sampling can
    # differ from thresholding sampled values, particularly at UV seams.
    best = None
    lo, hi = lower, 1. - 1e-6
    native_effect = document.defaults('OP_TextureEffect')
    native_effect.update(kind='LEVELS', enabled=True, strength=1., white=1., gamma=1.,
                         output_black=0., output_white=1.)
    for _ in range(24):
        # Blender stores FloatProperty controls as float32; solve that exact
        # persisted value so preview/export samples do not differ by rounding.
        black = float(np.float32((lo + hi) * .5))
        native_effect['black'] = black
        output = np.clip(apply_effect(base, SimpleNamespace(**native_effect)), 0, 1)
        samples = _sample(output, uv, valid)
        after = float(weights[samples > THRESHOLD].sum() / info['surface_area_world'])
        error = abs(after / before - ratio)
        if best is None or error < best[0]:
            best = error, black, after, output
        if after > goal: lo = black
        else: hi = black
    error, black, after, output = best
    if error > tolerance:
        raise AuthoringError('This mask cannot attain the requested occupied-area ratio by threshold alone; preserve it and revise the placement source explicitly')
    if np.any((output > THRESHOLD) & (current <= THRESHOLD)) or np.any(output > current + 1e-6):
        raise AuthoringError('Coverage reduction would add new support; source mask has changed')
    mask = document.defaults('OP_Mask')
    mask.update(id=identifier, name='Surface area coverage', source='FILTER')
    mask['effect'].update(kind='LEVELS', enabled=True, strength=1., black=float(black), white=1.,
                          gamma=1., output_black=0., output_white=1.)
    return dict(mask=mask, measurements=dict(layer_id=layer_id, threshold=THRESHOLD,
        coverage_before=before, coverage_after=after, actual_ratio=after/before,
        requested_ratio=ratio, ratio_tolerance=tolerance, new_support_pixels=0,
        source_mask_sha256=sha256(base.tobytes()).hexdigest(),
        before_mask_sha256=sha256(current.tobytes()).hexdigest(),
        after_mask_sha256=sha256(output.tobytes()).hexdigest(),
        opacity_unchanged=True, source_masks_unchanged=True, **info))
