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
    # A scripted bake fires no bake handler: the engine's results reach the images' pixels
    # (Cycles reads those, not the GPU textures) for any material that samples a set.
    from ..evaluation.shader_preview import sync_pixels
    sync_pixels()
    result = bpy.ops.object.bake(**kwargs)
    if 'FINISHED' not in result:
        raise RuntimeError('Cycles did not finish the bake')


# Bevel maps sample the target's own geometry: the Cycles Bevel node rounds
# the shading normal within a radius, and the distance between that normal and
# the geometry normal is the edge amount. Baked once, both are ordinary
# textures that EEVEE, Cycles and export all read; the shader Bevel node itself
# only evaluates in Cycles.
BEVEL_KINDS = {'BEVEL_NORMAL', 'BEVEL_MASK'}
# The Curvature edge set bakes through the same own-surface Cycles path: the
# exact transient shader below, one reach per radius multiple. Sharing the
# Bevel constants keeps both sides agreeing without an import cycle (the edge
# module imports this one, never the reverse).
CURVATURE_METHOD = 'CYCLES_BEVEL'
CURVATURE_REACHES = frozenset({'CURVATURE_THIN', 'CURVATURE', 'CURVATURE_WIDE', 'CURVATURE_XWIDE'})
CURVATURE_SAMPLES = 12
DEFAULT_COLORS = {'NORMAL': (.5, .5, 1, 1), 'BEVEL_NORMAL': (.5, .5, 1, 1),
                  'AO': (1, 1, 1, 1), 'BEVEL_MASK': (0, 0, 0, 1),
                  'CURVATURE_THIN': (0, 0, 0, 1), 'CURVATURE': (0, 0, 0, 1),
                  'CURVATURE_WIDE': (0, 0, 0, 1), 'CURVATURE_XWIDE': (0, 0, 0, 1)}


def bevel_graph(material, kind, radius, samples):
    """Wire the transient bake material so the Bevel node drives the pass.

    BEVEL_NORMAL: Bevel -> Diffuse BSDF normal, baked as a tangent normal.
    BEVEL_MASK: distance(Bevel normal, geometry normal) -> Emission, baked
    as emission, clamped to 0..1 (a right angle reaches about 1.41).
    """
    tree = material.node_tree
    nodes, links = tree.nodes, tree.links
    output = next((n for n in nodes if n.bl_idname == 'ShaderNodeOutputMaterial'), None)
    if output is None:
        output = nodes.new('ShaderNodeOutputMaterial')
    bevel = nodes.new('ShaderNodeBevel')
    bevel.samples = samples
    bevel.inputs['Radius'].default_value = radius
    if kind == 'BEVEL_NORMAL':
        shader = nodes.new('ShaderNodeBsdfDiffuse')
        links.new(bevel.outputs['Normal'], shader.inputs['Normal'])
    else:
        geometry = nodes.new('ShaderNodeNewGeometry')
        distance = nodes.new('ShaderNodeVectorMath')
        distance.operation = 'DISTANCE'
        links.new(bevel.outputs['Normal'], distance.inputs[0])
        links.new(geometry.outputs['Normal'], distance.inputs[1])
        clamp = nodes.new('ShaderNodeMath')
        clamp.operation = 'MINIMUM'
        clamp.inputs[1].default_value = 1.0
        links.new(distance.outputs['Value'], clamp.inputs[0])
        shader = nodes.new('ShaderNodeEmission')
        links.new(clamp.outputs['Value'], shader.inputs['Color'])
        shader.inputs['Strength'].default_value = 1.0
    links.new(shader.outputs[0], output.inputs['Surface'])


def curvature_graph(material, radius):
    """Wire the transient bake material for one Curvature edge reach.

    The reference edge-wear procedure: Texture Coordinate (Normal) into the
    Bevel node, the bevel Normal crossed with the same Normal vector, its
    length through a Float Map Range (SMOOTHERSTEP 0..1 to 0..1) into a
    Diffuse BSDF at Roughness 1, out as the map. Baked as DIFFUSE with the
    COLOR pass only (direct/indirect off); the pass reads only this material,
    never the scene's.
    """
    tree = material.node_tree
    nodes, links = tree.nodes, tree.links
    output = next((n for n in nodes if n.bl_idname == 'ShaderNodeOutputMaterial'), None)
    if output is None:
        output = nodes.new('ShaderNodeOutputMaterial')
    coord = nodes.new('ShaderNodeTexCoord')
    bevel = nodes.new('ShaderNodeBevel')
    bevel.samples = CURVATURE_SAMPLES
    bevel.inputs['Radius'].default_value = radius
    links.new(coord.outputs['Normal'], bevel.inputs['Normal'])
    cross = nodes.new('ShaderNodeVectorMath')
    cross.operation = 'CROSS_PRODUCT'
    links.new(bevel.outputs['Normal'], cross.inputs[0])
    links.new(coord.outputs['Normal'], cross.inputs[1])
    length = nodes.new('ShaderNodeVectorMath')
    length.operation = 'LENGTH'
    links.new(cross.outputs['Vector'], length.inputs[0])
    remap = nodes.new('ShaderNodeMapRange')
    remap.data_type = 'FLOAT'
    try:
        remap.interpolation_type = 'SMOOTHERSTEP'
    except AttributeError:
        pass
    try:
        remap.interpolation = 'SMOOTHERSTEP'
    except AttributeError:
        pass
    remap.inputs['From Min'].default_value = 0.0
    remap.inputs['From Max'].default_value = 1.0
    remap.inputs['To Min'].default_value = 0.0
    remap.inputs['To Max'].default_value = 1.0
    links.new(length.outputs['Value'], remap.inputs['Value'])
    shader = nodes.new('ShaderNodeBsdfDiffuse')
    shader.inputs['Roughness'].default_value = 1.0
    links.new(remap.outputs['Result'], shader.inputs['Color'])
    links.new(shader.outputs['BSDF'], output.inputs['Surface'])


def bake(context: bpy.types.Context, ts, kind: str, flush=False) -> bpy.types.Image:
    """Bake one map for every part of the set; painted corrections shape the cage and passes.

    ``flush`` loads the originals' Edit Mode geometry into their datablocks before staging
    (``render_geometry.flush_edit_meshes``): the bakes a user action starts pass it, the
    scheduler's never do.
    """
    image = _bake(context, ts, kind, flush=flush)
    if kind in {'NORMAL', 'AO'}:
        from . import corrections
        corrections.remember(ts, kind)
    return image


def bake_faces(context, ts, kind, regions, polygons=None, flush=False):
    """Bake ``kind`` with only ``regions`` ({object: faces}) on the real image; the rest hits scratch.

    ``polygons`` ({object: face count}) says which evaluation the faces index;
    a staged copy of another count raises ``staging.TopologyMismatch``.
    ``flush`` as in ``bake``.
    """
    return _bake(context, ts, kind, regions=regions, polygons=polygons, flush=flush)


def bake_curvature_reach(context, ts, kind, radius, flush=False):
    """Bake one Curvature edge reach with an explicit Bevel radius (see ``curvature_graph``)."""
    return _bake(context, ts, kind, flush=flush, radius=radius)


def _bake(context, ts, kind, regions=None, polygons=None, flush=False, radius=None):
    from .pairing import groups
    from .staging import prepared
    from . import corrections
    if kind not in {'NORMAL', 'AO', *BEVEL_KINDS, *CURVATURE_REACHES}:
        raise ValueError('Cycles mesh maps support Normal, AO, Bevel Normal, Bevel Mask and the Curvature edge set')
    settings = ts.bake
    bevel = kind in BEVEL_KINDS
    edge = kind in CURVATURE_REACHES
    if bevel or edge:
        # The target's own surface is baked; high-poly sources, cages and the
        # projection settings play no part.
        from .pairing import Group, _targets
        parts = [Group(low, (), None) for low in _targets(ts)]
    else:
        parts = groups(ts)
    if not parts:
        raise ValueError('Assign a low-poly target before baking')
    objects = {obj for part in parts for obj in (part.low, *part.sources, part.cage) if obj}
    if any(obj.name not in context.view_layer.objects for obj in objects):
        raise ValueError('Every bake target, source and active cage must be in the active view layer')
    # Cage correspondence is checked on the staged copies in BakeStage.prepare, the
    # geometry Cycles actually receives; a separate render-evaluation scope per map
    # only repeated that work on the originals.
    factor = int(settings.supersample)
    resolution = int(ts.resolution)
    working = supersample_preflight(resolution, factor)
    projecting = settings.selected_to_active and not bevel and not edge
    geometric = corrections.world_rasters(ts) if projecting else {}
    ignore = corrections.raster(ts, 'IGNORE') if projecting and kind in {'NORMAL', 'AO'} else None
    ignorable = {ref.object for ref in settings.high_objects if ref.object and getattr(ref, 'ignorable', False)}
    if ignore is not None and not any(src in ignorable for part in parts for src in part.sources):
        ignore = None
    # A set baked from its lows still sees the other parts' contact under SET scope, so the
    # exclusion pass is not tied to projection the way the cage and source passes are.
    exclude = corrections.raster(ts, 'AO_EXCLUDE') if kind == 'AO' and settings.ao_scope == 'SET' else None
    image = scratch = alternative = None
    canonical = f"{ts.name}_Baked{kind.title().replace('_', '')}"
    try:
        image = create(canonical, working, DEFAULT_COLORS[kind])
        image['op_bake_result'] = ts.id
        scratch = create('OP Bake Unassigned Slots', 32)
        with prepared(context, ts, parts, image, scratch, flush=flush) as stage:
            bake_settings = stage.scene.render.bake
            bake_settings.target = 'IMAGE_TEXTURES'
            bake_settings.use_selected_to_active = projecting
            bake_settings.normal_space = 'TANGENT'
            # Internal normals stay +Y; engine handedness conversion is export-only.
            bake_settings.normal_r = 'POS_X'
            bake_settings.normal_g = 'POS_Y'
            bake_settings.normal_b = 'POS_Z'
            # Margin scales with the working size so the authored-size result
            # keeps the same padding after downsampling (Ucupaint does the same).
            bake_settings.margin = settings.margin * factor
            bake_settings.use_clear = False  # Fresh neutral image shared by parts.
            bake_settings.use_cage = projecting and settings.cage_mode != 'NONE'
            if bevel:
                for material in stage.materials:
                    bevel_graph(material, kind, settings.bevel_radius, int(settings.bevel_samples))
            elif edge:
                if radius is None:
                    raise ValueError('A Curvature reach needs an explicit Bevel radius')
                for material in stage.materials:
                    curvature_graph(material, radius)
            extrusion = settings.cage_extrusion if settings.separate_cage_distance else settings.ray_distance
            bake_settings.cage_extrusion = extrusion if settings.cage_mode == 'AUTO' else 0
            bake_settings.max_ray_distance = settings.ray_distance
            if regions is not None:
                for part in parts:
                    stage.restrict(part, regions.get(part.low, ()), (polygons or {}).get(part.low))
            if geometric:
                # Painted cage corrections become one owned cage per part.
                for part in parts:
                    cage = corrections.corrected_cage(ts, stage.copies[part.low], geometric,
                                                      stage.copies[part.cage] if part.cage else None)
                    if cage is not None:
                        stage.add_cage(part, cage)

            def run_pass(target_image, sources_of=None, ao_scope=None):
                for part in parts:
                    cage = stage.cages.get(part.low)
                    bake_settings.max_ray_distance = settings.ray_distance
                    if cage is not None:
                        bake_settings.use_cage = True
                        bake_settings.cage_object = cage
                        bake_settings.cage_extrusion = 0
                        if settings.ray_distance > 0:
                            # Cycles measures the limit from the cage point: a painted lift
                            # would otherwise stop the rays that far above the surface.
                            bake_settings.max_ray_distance = settings.ray_distance + cage.get(corrections.LIFT, 0.0)
                    else:
                        bake_settings.cage_object = stage.copies[part.cage] if part.cage else None
                    sources = part.sources if sources_of is None else sources_of(part)
                    # Generated helpers (Bake Back) decorate a low that has no high-poly of its
                    # own: Cycles never hits the active object, so the texels the helpers miss
                    # would keep the neutral default and the low's own occlusion would be lost.
                    # The low bakes its own surface directly first; the helpers then project on
                    # top of that result (use_clear is off, so a miss keeps the direct bake).
                    layered = projecting and stage.self_baked(part, sources)
                    steps = ((False, ()), (True, sources)) if layered else ((projecting, sources),)
                    for project, step_sources in steps:
                        bake_settings.use_selected_to_active = project
                        # The helper pass extends nothing: its margin would halo every helper
                        # footprint over the direct bake inside the islands, and the direct
                        # pass already padded the island borders.
                        bake_settings.margin = 0 if layered and project else settings.margin * factor
                        with stage.target(part, kind, sources=step_sources, ao_scope=ao_scope, project=project) as target:
                            for material in target.data.materials:
                                node = material.node_tree.nodes.active if material and material.use_nodes else None
                                if node is not None and node.image is image:
                                    node.image = target_image
                            uv = next((uv for uv in target.data.uv_layers if uv.active_render), target.data.uv_layers.active)
                            logger.info('Baking %s for %s from %d source(s)', kind, part.low.name, len(step_sources))
                            if kind == 'BEVEL_NORMAL':
                                bake_type = 'NORMAL'
                            elif kind == 'BEVEL_MASK':
                                bake_type = 'EMIT'
                            elif edge:
                                bake_type = 'DIFFUSE'
                            else:
                                bake_type = kind
                            if edge:
                                # The reference procedure: Diffuse COLOR pass only, no direct/indirect light.
                                bake_settings.use_pass_direct = False
                                bake_settings.use_pass_indirect = False
                                bake_settings.use_pass_color = True
                                _run_bake(type=bake_type, pass_filter={'COLOR'}, uv_layer=uv.name)
                            else:
                                _run_bake(type=bake_type, uv_layer=uv.name)
                            for material in target.data.materials:
                                node = material.node_tree.nodes.active if material and material.use_nodes else None
                                if node is not None and node.image is target_image:
                                    node.image = image

            run_pass(image)
            passes = []
            if ignore is not None:
                passes.append((ignore, lambda part: tuple(s for s in part.sources if s not in ignorable) or part.sources, None))
            if exclude is not None:
                passes.append((exclude, None, 'PAIR'))
            for weight, sources_of, ao_scope in passes:
                alternative = create(f'{ts.name} Bake Alternative', working, DEFAULT_COLORS[kind])
                run_pass(alternative, sources_of, ao_scope)
                blended = corrections.composite(pixels(image), pixels(alternative), corrections.resample(weight, working))
                image.pixels.foreach_set(blended.ravel())
                image.update()
                bpy.data.images.remove(alternative)
                alternative = None
        # Cycles writes pixels without touching the managed revision the caches
        # key on; record the write before publishing the staged image.
        image['open_painter_pixel_revision'] = image.get('open_painter_pixel_revision', 0) + 1
        if working != resolution:
            reduced = downsample(pixels(image), factor, 'NORMAL' if kind in {'NORMAL', 'BEVEL_NORMAL'} else kind)
            image.scale(resolution, resolution)  # keep the datablock identity; pixels are replaced below
            write(image, reduced, pack=False)
            image['open_painter_supersample'] = factor
        if bevel:
            image['open_painter_mesh_map'] = kind
            image['bevel_radius'] = float(settings.bevel_radius)
            image['bevel_samples'] = int(settings.bevel_samples)
        elif edge:
            image['open_painter_curvature_method'] = CURVATURE_METHOD
            image['mean_curvature_distance'] = float(radius)
            image['open_painter_edge_kind'] = kind
        if regions is None:
            pack_image(image)
        return image
    except Exception:
        if image:
            bpy.data.images.remove(image)
        if alternative:
            bpy.data.images.remove(alternative)
        raise
    finally:
        if scratch:
            bpy.data.images.remove(scratch)
