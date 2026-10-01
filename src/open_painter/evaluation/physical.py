"""Physical scale awareness (0.9.5): procedural sizes in metres, resolved per asset.

Blender knows the assigned meshes' dimensions, so a texture set knows its
**density** — world length per UV tile, √(3D area / UV area) over the assigned
faces — and its texel size. A channel, mask or effect switched to *Physical*
units carries sizes in world distance (``feature_size`` of a procedural cell,
``physical_radius`` of a filter); ``sync`` derives the texture-unit values the
evaluators and shader bindings read (``procedural_scale``, ``noise_scale``,
``radius``, a warp's driving-noise ``scale``) and rewrites them whenever the
physical value, the geometry, the assignments or the resolution change. A
material authored in millimetres therefore looks the same on a coffee mug and
on a truck, and existing materials can be locked to their current appearance.
"""
import numpy as np

_density = {}   # (scene pointer, set id, assignment records) -> metres per UV tile
TOLERANCE = 1e-4


def _key(ts):
    from ..geometry.provenance import object_record
    records = []
    for ref in ts.assignments:
        if not ref.object:
            continue
        record = object_record(ref.object)
        # Moving or rotating the object leaves its surface area alone: only the scale part
        # of the matrix takes part, so a drag (or an animation) never re-measures the mesh.
        record['matrix'] = [round(float(np.linalg.norm([row[i] for row in record['matrix'][:3]])), 6) for i in range(3)]
        records.append((repr(record), ref.slot_index))
    return (ts.id_data.as_pointer(), ts.id, tuple(records))


def measure(ts):
    """(world area, UV area) of the assigned faces."""
    from ..geometry.snapshots import targets
    world = uv_area = 0.0
    with targets(ts) as entries:
        for entry in entries:
            xyz = entry['positions'][entry['tri_vertices']].astype(np.float64)
            uv = entry['uv'][entry['tri_loops']].astype(np.float64)
            world += float(np.linalg.norm(np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0]), axis=1).sum() * .5)
            a, b = uv[:, 1] - uv[:, 0], uv[:, 2] - uv[:, 0]
            uv_area += float(np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]).sum() * .5)
    return world, uv_area


def density(ts):
    """Metres per UV tile of the set's assigned meshes, or None without assigned faces."""
    if not any(ref.object for ref in ts.assignments):
        return None
    key = _key(ts)
    if key in _density:
        return _density[key]
    # An unresolvable set (no faces in the assigned slot, evaluated mesh without a UV map,
    # degenerate UVs) is remembered too: measuring copies every evaluated mesh, and the panel
    # asks on each redraw. The key carries the mesh signature, so fixing the mesh re-measures.
    try:
        world, uv_area = measure(ts)
        found = float(np.sqrt(world / uv_area)) if world > 1e-12 and uv_area > 1e-12 else None
    except ValueError:
        found = None
    if len(_density) > 64:
        _density.clear()
    _density[key] = found
    return found


def texel_size(ts):
    """Metres per texel at the set's resolution, or None."""
    tile = density(ts)
    return None if tile is None else tile / int(ts.resolution)


def describe(ts):
    """Short human text for the panel: texel size and tile extent."""
    tile = density(ts)
    if tile is None:
        return 'Physical scale: assign a mesh'
    texel = tile / int(ts.resolution)
    unit = lambda metres: f'{metres * 1000:.2f} mm' if metres < .01 else f'{metres * 100:.1f} cm' if metres < 1 else f'{metres:.2f} m'
    return f'Texel {unit(texel)} · UV tile {unit(tile)}'


def _clamp(owner, field, value):
    prop = owner.bl_rna.properties[field]
    return min(max(value, prop.hard_min), prop.hard_max)


def expected(owner, ts):
    """{texture field: value} a Physical owner should carry, or {} when not Physical / unresolvable."""
    if getattr(owner, 'units', 'TEXTURE') != 'PHYSICAL':
        return {}
    tile = density(ts)
    if tile is None:
        return {}
    texel = tile / int(ts.resolution)
    kind = owner.bl_rna.identifier
    result = {}
    if kind == 'OP_Channel':
        if getattr(owner, 'procedural', ''):
            size = max(float(owner.feature_size), 1e-6)
            # UV: repeats per tile; triplanar: repeats per world unit (scale multiplies world positions).
            result['procedural_scale'] = _clamp(owner, 'procedural_scale', (tile if owner.projection == 'UV' else 1.0) / size)
    elif kind == 'OP_Mask':
        if owner.source in {'PROCEDURAL', 'NOISE', 'GENERATOR'}:
            size = max(float(owner.feature_size), 1e-6)
            result['noise_scale'] = _clamp(owner, 'noise_scale', (tile if owner.projection == 'UV' else 1.0) / size)
    elif kind == 'OP_TextureEffect':
        from .operation_contracts import POINTWISE
        if owner.kind not in POINTWISE:
            result['radius'] = _clamp(owner, 'radius', float(owner.physical_radius) / max(texel, 1e-12))
        if owner.kind in {'WARP', 'SLOPE_BLUR', 'DIRECTIONAL_WARP', 'VECTOR_WARP', 'UV_DISTORTION'}:
            result['scale'] = _clamp(owner, 'scale', tile / max(float(owner.feature_size), 1e-6))
    return result


def _owners(ts, layers=None):
    """Every scalable owner of the set, or of the layers with these ids."""
    for layer in ts.layers:
        if layers is not None and layer.id not in layers:
            continue
        for prop in layer.channels:
            yield prop
            yield from prop.effects
        for mask in layer.masks:
            yield mask
            yield mask.effect


def uses(ts):
    return any(getattr(owner, 'units', 'TEXTURE') == 'PHYSICAL' for owner in _owners(ts))


def sync_owner(owner, ts):
    """Write the derived texture-unit values of one owner; returns the fields changed."""
    changed = []
    for field, value in expected(owner, ts).items():
        if abs(float(getattr(owner, field)) - value) > TOLERANCE * max(1.0, abs(value)):
            setattr(owner, field, value)
            changed.append(field)
    return changed


def sync(ts, layers=None):
    """Resolve every Physical owner of the set, or of the layers with these ids (a replay that
    only added them: an owner's values follow it and the set's texel density alone); returns
    the number of fields rewritten."""
    layers = None if layers is None else set(layers)
    owners = [owner for owner in _owners(ts, layers) if getattr(owner, 'units', 'TEXTURE') == 'PHYSICAL']
    return sum(len(sync_owner(owner, ts)) for owner in owners)


def sync_all(scene):
    project = getattr(scene, 'open_painter', None)
    if project is None:
        return 0
    return sum(sync(ts) for ts in project.texture_sets)


def lock(ts):
    """Switch every scalable owner to Physical, deriving its metre sizes from the current values.

    The set keeps its current look; the sizes now travel with the material.
    """
    tile = density(ts)
    if tile is None:
        raise ValueError('Assign a mesh with UVs first: physical scale needs the surface area')
    texel = tile / int(ts.resolution)
    from .operation_contracts import POINTWISE
    count = 0
    for owner in list(_owners(ts)):
        kind = owner.bl_rna.identifier
        if kind == 'OP_Channel' and owner.procedural:
            owner.feature_size = (tile if owner.projection == 'UV' else 1.0) / max(owner.procedural_scale, 1e-6)
        elif kind == 'OP_Mask' and owner.source in {'PROCEDURAL', 'NOISE', 'GENERATOR'}:
            owner.feature_size = (tile if owner.projection == 'UV' else 1.0) / max(owner.noise_scale, 1e-6)
        elif kind == 'OP_TextureEffect' and owner.kind not in POINTWISE and owner.kind != 'ANCHOR':
            owner.physical_radius = owner.radius * texel
            if owner.kind in {'WARP', 'SLOPE_BLUR', 'DIRECTIONAL_WARP', 'VECTOR_WARP', 'UV_DISTORTION'}:
                owner.feature_size = tile / max(owner.scale, 1e-6)
        else:
            continue
        owner.units = 'PHYSICAL'
        count += 1
    return count


def unlock(ts):
    count = 0
    for owner in list(_owners(ts)):
        if getattr(owner, 'units', 'TEXTURE') == 'PHYSICAL':
            owner.units = 'TEXTURE'
            count += 1
    return count


def forget(ts=None):
    if ts is None:
        _density.clear()
    else:
        for key in [k for k in _density if k[:2] == (ts.id_data.as_pointer(), ts.id)]:
            _density.pop(key, None)
