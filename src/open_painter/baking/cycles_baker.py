"""Cycles mesh-map baking with matched sources and isolated evaluated staging."""
import bpy
from ..core.image_manager import create, pack_image
from ..utils.logging import logger


def _run_bake(**kwargs):
    """Single synchronous Cycles call; explicit seam for failure regressions."""
    result = bpy.ops.object.bake(**kwargs)
    if 'FINISHED' not in result:
        raise RuntimeError('Cycles did not finish the bake')


def bake(context: bpy.types.Context, ts, kind: str) -> bpy.types.Image:
    from .pairing import groups
    from .staging import prepared
    if kind not in {'NORMAL', 'AO'}:
        raise ValueError('Cycles mesh maps support Normal and AO')
    settings = ts.bake
    parts = groups(ts)
    if not parts:
        raise ValueError('Assign a low-poly target before baking')
    objects = {obj for part in parts for obj in (part.low, *part.sources, part.cage) if obj}
    if any(obj.name not in context.view_layer.objects for obj in objects):
        raise ValueError('Every bake target, source and active cage must be in the active view layer')
    from .cages import validate_topology
    graph = context.evaluated_depsgraph_get()
    for part in parts:
        if part.cage:
            validate_topology(part.low, part.cage, graph)
    image = scratch = None
    try:
        image = create(f'{ts.name}_Baked{kind.title()}', int(ts.resolution),
                       (.5, .5, 1, 1) if kind == 'NORMAL' else (1, 1, 1, 1))
        scratch = create('OP Bake Unassigned Slots', 32)
        with prepared(context, ts, parts, image, scratch) as stage:
            bake_settings = stage.scene.render.bake
            bake_settings.target = 'IMAGE_TEXTURES'
            bake_settings.use_selected_to_active = settings.selected_to_active
            bake_settings.normal_space = 'TANGENT'
            # Internal normals stay +Y; engine handedness conversion is export-only.
            bake_settings.normal_r = 'POS_X'
            bake_settings.normal_g = 'POS_Y'
            bake_settings.normal_b = 'POS_Z'
            bake_settings.margin = settings.margin
            bake_settings.use_clear = False  # Fresh neutral image shared by parts.
            bake_settings.use_cage = settings.selected_to_active and settings.cage_mode != 'NONE'
            extrusion = settings.cage_extrusion if settings.separate_cage_distance else settings.ray_distance
            bake_settings.cage_extrusion = extrusion if settings.cage_mode == 'AUTO' else 0
            bake_settings.max_ray_distance = settings.ray_distance
            for part in parts:
                bake_settings.cage_object = stage.copies[part.cage] if part.cage else None
                with stage.target(part, kind) as target:
                    uv = next((uv for uv in target.data.uv_layers if uv.active_render), target.data.uv_layers.active)
                    logger.info('Baking %s for %s from %d source(s)', kind, part.low.name, len(part.sources))
                    _run_bake(type=kind, uv_layer=uv.name)
        pack_image(image)
        return image
    except Exception:
        if image:
            bpy.data.images.remove(image)
        raise
    finally:
        if scratch:
            bpy.data.images.remove(scratch)
