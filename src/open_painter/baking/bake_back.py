"""Bake generated geometry back into the set's Normal and AO maps.

Helpers (displaced copies, instanced bolts, stitches, welds…) are staged as
high-poly sources paired to their source object for one cooperative bake of
the existing NORMAL/AO pipeline; the set's bake settings are restored
afterwards, and the helpers can be kept, hidden or removed.
"""
from contextlib import contextmanager

import bpy

from ..geometry_effects.manager import TAG


def helpers_of(ts, scene=None):
    scene = scene or ts.id_data
    return [obj for obj in scene.objects if obj.get(TAG) == 'INSTANCE_HELPER'
            and (obj.get('source_texture_set') == ts.id or _source_of(obj) is not None
                 and any(ref.object == _source_of(obj) for ref in ts.assignments))]


def _source_of(helper):
    """The assigned mesh a helper decorates: its parent, or its curve's parent."""
    parent = helper.parent
    if parent is None:
        return None
    if parent.type == 'CURVE':
        return parent.parent
    return parent


@contextmanager
def staged(ts, helpers, ray_margin=1.5):
    """Temporarily list the helpers as high-poly sources of their targets."""
    settings = ts.bake
    # Rows of helpers that were regenerated or removed dangle: drop them first.
    for index in range(len(settings.high_objects) - 1, -1, -1):
        if settings.high_objects[index].object is None:
            settings.high_objects.remove(index)
    saved = dict(selected_to_active=settings.selected_to_active, ray_distance=settings.ray_distance,
                 normal=settings.normal, ao=settings.ao, curvature=settings.curvature,
                 rows=[(ref.object, ref.name, ref.target, ref.cage) for ref in settings.high_objects])
    saved_utility = {name: getattr(settings, name) for name in ('position', 'world_normal', 'thickness', 'uv_border',
                                                                 'object_normal', 'material_id', 'object_id',
                                                                 'uv_island', 'mean_curvature', 'selected_faces',
                                                                 'bevel_normal', 'bevel_mask')
                     if hasattr(settings, name)}
    added = []
    try:
        for helper in helpers:
            target = _source_of(helper)
            if target is None or any(ref.object == helper for ref in settings.high_objects):
                continue
            ref = settings.high_objects.add()
            ref.object, ref.name, ref.target = helper, helper.name, target
            added.append(ref)
        settings.selected_to_active = True
        reach = max(float(helper.get('amplitude', 0.0)) for helper in helpers) if helpers else 0.0
        sizes = [float(helper.get('size', 0.0)) * 2 for helper in helpers]
        settings.ray_distance = max(settings.ray_distance, reach * ray_margin, max(sizes, default=0.0) * ray_margin)
        settings.normal, settings.ao, settings.curvature = True, True, False
        for name in saved_utility:
            setattr(settings, name, False)
        yield added
    finally:
        # The helpers stay listed as sources (keep/hide) so the maps' provenance matches
        # until a helper regenerates; a removed helper's row would dangle, so drop it.
        kept = [(ref.object, ref.name, ref.target, ref.cage) for ref in added
                if ref.object is not None and ref.object.name in bpy.data.objects]
        settings.high_objects.clear()
        for obj, name, target, cage in saved['rows'] + kept:
            ref = settings.high_objects.add()
            ref.object, ref.name, ref.target, ref.cage = obj, name, target, cage
        # Sources kept ⇒ the maps' provenance (sources, ray distance, projection) must keep matching.
        settings.selected_to_active = saved['selected_to_active'] or bool(kept)
        if not kept:
            settings.ray_distance = saved['ray_distance']
        settings.normal, settings.ao, settings.curvature = saved['normal'], saved['ao'], saved['curvature']
        for name, value in saved_utility.items():
            setattr(settings, name, value)


def steps(context, ts, helpers, then='KEEP'):
    """Generator: bake NORMAL and AO from the helpers, then keep/hide/remove them."""
    from .bake_manager import bake_steps
    if not helpers:
        raise ValueError('Generate geometry on this set first (Material → Geometry).')
    context.view_layer.update()   # freshly generated helpers join the view layer on the next update
    with staged(ts, helpers):
        for kind in bake_steps(context, ts):
            yield kind
    for entry in ts.baked_maps:
        if entry.kind in {'NORMAL', 'AO'} and entry.image:
            entry.image['open_painter_bake_back'] = [helper.get('effect_owner', '') for helper in helpers]
    if then == 'HIDE':
        for helper in helpers:
            helper.hide_viewport = helper.hide_render = True
    elif then == 'REMOVE':
        from ..geometry_effects.manager import remove
        for helper in helpers:
            remove(helper)
        for index in range(len(ts.bake.high_objects) - 1, -1, -1):
            if ts.bake.high_objects[index].object is None:
                ts.bake.high_objects.remove(index)


def run(context, ts, helpers, then='KEEP'):
    for _ in steps(context, ts, helpers, then):
        pass
