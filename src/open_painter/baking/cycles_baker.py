"""Cycles mesh-map baking with matched sources and isolated evaluated staging."""
import bpy
import numpy as np
from ..core.image_manager import create, pack_image, pixels, write
from ..utils.logging import logger

# Supersampled bakes allocate a Blender float image plus a NumPy copy of it;
# refuse beyond this rather than letting a 4K set silently request 8 GiB.
SUPERSAMPLE_BYTE_LIMIT = 2 * 2**30


def supersample_preflight(resolution, factor):
    """Return the working size or raise with the bytes it would require."""
    if factor <= 1:
        return resolution
    working = resolution * factor
    required = working * working * 16 * 2
    if required > SUPERSAMPLE_BYTE_LIMIT:
        raise ValueError(f'{factor}x supersampling at {resolution} needs a {working} bake image '
                         f'({required / 2**30:.1f} GiB with its working copy); lower the factor or resolution.')
    return working


def downsample(values, factor, kind):
    """Box-filter a (working, working, 4) bake into the authored size.

    Normals are averaged as unit vectors and renormalized, not as colors.
    Alpha keeps the mean coverage so margins stay partial rather than binary.
    """
    n = values.shape[0] // factor
    blocks = values.reshape(n, factor, n, factor, 4)
    if kind == 'NORMAL':
        vectors = blocks[..., :3] * 2 - 1
        mean = vectors.mean(axis=(1, 3))
        length = np.linalg.norm(mean, axis=-1, keepdims=True)
        mean = np.where(length > 1e-8, mean / np.maximum(length, 1e-8), (0., 0., 1.))
        result = np.empty((n, n, 4), np.float32)
        result[..., :3] = mean * .5 + .5
        result[..., 3] = blocks[..., 3].mean(axis=(1, 3))
        return result
    return blocks.mean(axis=(1, 3)).astype(np.float32)


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
    factor = int(settings.supersample)
    resolution = int(ts.resolution)
    working = supersample_preflight(resolution, factor)
    image = scratch = None
    try:
        image = create(f'{ts.name}_Baked{kind.title()}', working,
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
            # Margin scales with the working size so the authored-size result
            # keeps the same padding after downsampling (Ucupaint does the same).
            bake_settings.margin = settings.margin * factor
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
        if working != resolution:
            reduced = downsample(pixels(image), factor, kind)
            image.scale(resolution, resolution)  # keep the datablock identity; pixels are replaced below
            write(image, reduced, pack=False)
            image['open_painter_supersample'] = factor
        pack_image(image)
        return image
    except Exception:
        if image:
            bpy.data.images.remove(image)
        raise
    finally:
        if scratch:
            bpy.data.images.remove(scratch)
