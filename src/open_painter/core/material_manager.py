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
    if roles.get('AO Preview'):
        roles['AO Preview'].inputs[0].default_value = 1 if ts.preview_ao else 0


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
    if image_overrides is None and shader_preview.requested(ts):
        graph = shader_preview.ensure(ts.id_data, ts)
        colors = graph.outputs
    else:
        for i, channel in enumerate(CHANNELS):
            texture = node(channel, "ShaderNodeTexImage", (-600, 400 - i * 250))
            image = next((o.image for o in ts.outputs if o.channel == channel), None)
            if image_overrides and channel in image_overrides:
                image = image_overrides[channel]
            if texture.image != image:
                texture.image = image
            colors[channel] = texture.outputs['Color']
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
    connect(colors["BASE_COLOR"], ao.inputs[1])
    connect(colors["AO"], ao.inputs[2])
    connect(ao.outputs[0], bsdf.inputs["Base Color"])
    for channel, socket in (("ROUGHNESS", "Roughness"), ("METALLIC", "Metallic")):
        connect(colors[channel], bsdf.inputs[socket])
    normal = node("Normal", "ShaderNodeNormalMap", (0, -200))
    bump = node("Height", "ShaderNodeBump", (250, -200))
    if bump.inputs["Distance"].default_value != ts.height_distance:
        bump.inputs["Distance"].default_value = ts.height_distance
    connect(colors["NORMAL"], normal.inputs["Color"])
    connect(normal.outputs["Normal"], bump.inputs["Normal"])
    connect(colors["HEIGHT"], bump.inputs["Height"])
    connect(bump.outputs["Normal"], bsdf.inputs["Normal"])
    for ref in ts.assignments:
        if ref.object and ref.slot_index < len(ref.object.material_slots):
            slot = ref.object.material_slots[ref.slot_index]
            if slot.material is None:
                slot.material = material
    if image_overrides is None and shader_preview.requested(ts):
        # A saved/detached graph remains usable without the extension. Remove it
        # only after a successful replacement has been wired to the surface.
        for old in list(nodes):
            if old.get(OWNER) and old.get('role') == 'Procedural Graph' and old != graph.instance:
                tree = old.node_tree
                nodes.remove(old)
                if tree and not tree.users and tree.get(OWNER):
                    bpy.data.node_groups.remove(tree)
        shader_preview.mark_attached(ts.id_data, ts, graph)
    else:
        shader_preview.mark_attached(ts.id_data, ts)
    return material
