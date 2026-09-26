"""Owned surface adapter for procedural authoring and explicit baked previews."""

import bpy

from ..constants import CHANNELS, OWNER


def update_surface(ts):
    """Update surface uniforms without compiling layers or touching images."""
    if not ts.material or not ts.material.use_nodes:
        return
    roles = {node.get('role'): node for node in ts.material.node_tree.nodes if node.get(OWNER)}
    bsdf = roles.get('Surface')
    if bsdf:
        for socket, value in (("Emission Strength", 1), ("Anisotropic", ts.anisotropy),
                              ("Coat Weight", ts.coat_weight), ("Coat Roughness", ts.coat_roughness)):
            if bsdf.inputs[socket].default_value != value:
                bsdf.inputs[socket].default_value = value
    if roles.get('Height'):
        roles['Height'].inputs['Distance'].default_value = ts.height_distance
    from .texture_set_node import sync
    sync(ts)   # the texture set node's own Normal output bumps by the same distance
    if roles.get('AO Preview'):
        project = getattr(ts.id_data, 'open_painter', None)
        indirect = False
        if project is not None and project.engine_preview and project.engine_preview_ao_indirect:
            from ..targets import get
            indirect = get(project.target).ao_usage == 'INDIRECT'
        roles['AO Preview'].inputs[0].default_value = 0 if indirect else (1 if ts.preview_ao else 0)


def _ensure_output(ts, channel):
    """The set's channel image the engine writes into: half float on the GPU, full float for
    Height (bump detail is finer than a half-float step)."""
    from . import image_manager
    from ..evaluation.shader_resources import new_leaves_frozen
    prop = next((o for o in ts.outputs if o.channel == channel), None)
    if (prop is None or prop.image is None) and new_leaves_frozen():
        # Between undo steps no datablock may be created for the material to reference.
        return None
    image = image_manager.output(ts, channel).image
    half = channel != 'HEIGHT'
    if image.use_half_precision != half:
        image.use_half_precision = half
    if not image.get('op_engine_output'):
        image['op_engine_output'] = True
    return image


def relink_slots(ts):
    """Put the set's material back on the assigned slots another material was dropped on.

    Explicit (the Repair Material button): render and save handlers also repair the
    material's graph, and a slot the artist swapped for a render of their own must stay
    theirs. Returns the objects whose slots changed.
    """
    material = ts.material
    changed = []
    if material is None:
        return changed
    for ref in ts.assignments:
        obj = ref.object
        if obj is not None and ref.slot_index < len(obj.material_slots) \
                and obj.material_slots[ref.slot_index].material != material:
            obj.material_slots[ref.slot_index].material = material
            changed.append(obj)
    return changed


def repair(ts, image_overrides=None):
    material = ts.material
    # Independent recipes cannot share one mutable material shader. Preserve
    # shared source images while separating the material for distinct slots.
    others = [other for scene in bpy.data.scenes if hasattr(scene, 'open_painter')
              for other in scene.open_painter.texture_sets
              if other.as_pointer() != ts.as_pointer() and material and other.material == material]
    if others:
        slots = {(ref.object.as_pointer(), ref.slot_index) for ref in ts.assignments if ref.object}
        if any((ref.object.as_pointer(), ref.slot_index) in slots
               for other in others for ref in other.assignments if ref.object):
            raise ValueError('Independent texture sets share an object material slot. Use separate objects or material slots.')
        previous = material
        material = material.copy()
        material['texture_set_id'] = ts.id
        ts.material = material
        # A scene copy shares the set id: its channel images and its texture set node's tree
        # are separated too, or two engines would write one image and one tree would show both.
        from .texture_set_node import TREE_PREFIX
        shared = {out.image.as_pointer() for other in others for out in other.outputs if out.image}
        for out in ts.outputs:
            if out.image is not None and out.image.as_pointer() in shared:
                out.image = None
        ts['op_node_tree'] = f'{TREE_PREFIX}{ts.id} · {ts.id_data.name}'
        for ref in ts.assignments:
            if ref.object and ref.slot_index < len(ref.object.material_slots):
                slot = ref.object.material_slots[ref.slot_index]
                if slot.material == previous:
                    slot.material = material
    if not material or not material.get(OWNER):
        material = bpy.data.materials.new(f"OP · {ts.name}")
        material[OWNER] = True
        material["texture_set_id"] = ts.id
        ts.material = material
    if not material.use_nodes:
        material.use_nodes = True
    nodes, links = material.node_tree.nodes, material.node_tree.links

    def node(role, kind, location):
        found = next(
            (n for n in nodes if n.get(OWNER) and n.get("role") == role and n.bl_idname == kind),
            None,
        )
        if found is None:
            found = nodes.new(kind)
            found[OWNER], found["role"] = True, role
            found.label = "Open Painter · " + role
        if tuple(found.location) != location:
            found.location = location
        return found

    def connect(source, target):
        if not any(link.from_socket == source for link in target.links):
            links.new(source, target)

    output = node("Output", "ShaderNodeOutputMaterial", (800, 0))
    if not output.is_active_output:
        output.is_active_output = True
    bsdf = node("Surface", "ShaderNodeBsdfPrincipled", (500, 0))
    connect(bsdf.outputs["BSDF"], output.inputs["Surface"])
    from ..evaluation import shader_preview
    colors = {}
    engine = image_overrides is None and shader_preview.requested(ts)
    if engine:
        # The set's channels come from one Open Painter Texture Set node: the engine evaluates
        # the stack into the set's images, the node samples them.
        from . import texture_set_node
        for channel in CHANNELS:
            _ensure_output(ts, channel)
        with texture_set_node.creating():
            source = node("Texture Set", texture_set_node.NODE_ID, (-300, 150))
        texture_set_node.bind(source, ts)
        colors = {channel: source.outputs[texture_set_node.CHANNEL_SOCKETS[channel]] for channel in CHANNELS}
        for old in list(nodes):
            # Per-channel image nodes of the previous adapter; the node reads the same images.
            if old.get(OWNER) and old.get("role") in CHANNELS and old.bl_idname == "ShaderNodeTexImage":
                nodes.remove(old)
    else:
        # The explicit CPU reference (and a raster consumer's overrides) binds the images to
        # one node per channel, which its reduced-resolution previews swap.
        for old in list(nodes):
            if old.get(OWNER) and old.get("role") == "Texture Set":
                tree = old.node_tree
                nodes.remove(old)
                if tree is not None and tree.get('op_texture_set_node') is not None and not tree.users:
                    bpy.data.node_groups.remove(tree)   # rebuilt when the engine adapter returns
        uv_socket = None
        if ts.udim_tile:
            from ..geometry.udim import tile_origin
            uv = node('UDIM Source UV', 'ShaderNodeUVMap', (-1000, 400))
            uv.uv_map = ts.udim_source_uv
            translated = node('UDIM Local UV', 'ShaderNodeVectorMath', (-800, 400))
            translated.operation = 'SUBTRACT'
            translated.inputs[1].default_value = (*tile_origin(ts.udim_tile), 0)
            connect(uv.outputs['UV'], translated.inputs[0])
            uv_socket = translated.outputs['Vector']
        for i, channel in enumerate(CHANNELS):
            texture = node(channel, "ShaderNodeTexImage", (-600, 400 - i * 250))
            image = next((o.image for o in ts.outputs if o.channel == channel), None)
            if image_overrides and channel in image_overrides:
                image = image_overrides[channel]
            if texture.image != image:
                texture.image = image
            if uv_socket is not None:
                connect(uv_socket, texture.inputs['Vector'])
            colors[channel] = texture.outputs['Color']
    from ..targets import preview as engine_preview
    project = getattr(ts.id_data, 'open_painter', None)
    engine_preview.remove(nodes)
    connect(colors['EMISSION'], bsdf.inputs['Emission Color'])
    connect(colors['OPACITY'], bsdf.inputs['Alpha'])
    for socket, value in (("Emission Strength",1),("Anisotropic",ts.anisotropy),
                          ("Coat Weight",ts.coat_weight),("Coat Roughness",ts.coat_roughness)):
        if bsdf.inputs[socket].default_value != value:
            bsdf.inputs[socket].default_value = value
    tangent = node("UV Tangent", "ShaderNodeTangent", (250, -450))
    if tangent.direction_type != "UV_MAP":
        tangent.direction_type = "UV_MAP"
    connect(tangent.outputs["Tangent"], bsdf.inputs["Tangent"])
    ao = node("AO Preview", "ShaderNodeMixRGB", (0, 300))
    if ao.blend_type != "MULTIPLY":
        ao.blend_type = "MULTIPLY"
    factor = 1 if ts.preview_ao else 0
    if ao.inputs[0].default_value != factor:
        ao.inputs[0].default_value = factor
    if bsdf.inputs['Specular IOR Level'].default_value != .5:
        bsdf.inputs['Specular IOR Level'].default_value = .5
    display = colors
    if project is not None and project.engine_preview:
        display = engine_preview.adapt(ts, project, colors, node, connect, bsdf, ao)
    connect(display["BASE_COLOR"], ao.inputs[1])
    connect(colors["AO"], ao.inputs[2])
    connect(ao.outputs[0], bsdf.inputs["Base Color"])
    for channel, socket in (("ROUGHNESS", "Roughness"), ("METALLIC", "Metallic")):
        connect(display[channel], bsdf.inputs[socket])
    normal = node("Normal", "ShaderNodeNormalMap", (0, -200))
    bump = node("Height", "ShaderNodeBump", (250, -200))
    if bump.inputs["Distance"].default_value != ts.height_distance:
        bump.inputs["Distance"].default_value = ts.height_distance
    connect(display["NORMAL"], normal.inputs["Color"])
    connect(normal.outputs["Normal"], bump.inputs["Normal"])
    connect(colors["HEIGHT"], bump.inputs["Height"])
    connect(bump.outputs["Normal"], bsdf.inputs["Normal"])
    # View Mask (ui/mask_view.py): while a mask snapshot is shown, the surface reads it instead.
    from ..ui import mask_view
    mask_view.wire(ts, nodes, links, node, connect, bsdf)
    for ref in ts.assignments:
        if ref.object and ref.slot_index < len(ref.object.material_slots):
            slot = ref.object.material_slots[ref.slot_index]
            if slot.material is None:
                slot.material = material
    # Materials compiled by earlier versions held the whole stack as a "Procedural Graph"
    # group of node groups: the node above replaces it.
    for old in list(nodes):
        if old.get(OWNER) and old.get('role') == 'Procedural Graph':
            tree = old.node_tree
            nodes.remove(old)
            if tree and not tree.users and tree.get(OWNER):
                bpy.data.node_groups.remove(tree)
    from ..evaluation.shader_graph import remove_orphan_units
    remove_orphan_units(ts.id)
    shader_preview.mark_attached(ts.id_data, ts, engine)
    return material
