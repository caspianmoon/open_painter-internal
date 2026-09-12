"""A fixed-size Principled preview graph; preserve all unrelated nodes."""

import bpy

from ..constants import CHANNELS, OWNER


def repair(ts):
    material = ts.material
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
    tex = {}
    for i, channel in enumerate(CHANNELS):
        tex[channel] = node(channel, "ShaderNodeTexImage", (-600, 400 - i * 250))
        image = next((o.image for o in ts.outputs if o.channel == channel), None)
        if tex[channel].image != image:
            tex[channel].image = image
    ao = node("AO Preview", "ShaderNodeMixRGB", (0, 300))
    if ao.blend_type != "MULTIPLY":
        ao.blend_type = "MULTIPLY"
    factor = 1 if ts.preview_ao else 0
    if ao.inputs[0].default_value != factor:
        ao.inputs[0].default_value = factor
    connect(tex["BASE_COLOR"].outputs["Color"], ao.inputs[1])
    connect(tex["AO"].outputs["Color"], ao.inputs[2])
    connect(ao.outputs[0], bsdf.inputs["Base Color"])
    for channel, socket in (("ROUGHNESS", "Roughness"), ("METALLIC", "Metallic")):
        connect(tex[channel].outputs["Color"], bsdf.inputs[socket])
    normal = node("Normal", "ShaderNodeNormalMap", (0, -200))
    bump = node("Height", "ShaderNodeBump", (250, -200))
    if bump.inputs["Distance"].default_value != ts.height_distance:
        bump.inputs["Distance"].default_value = ts.height_distance
    connect(tex["NORMAL"].outputs["Color"], normal.inputs["Color"])
    connect(normal.outputs["Normal"], bump.inputs["Normal"])
    connect(tex["HEIGHT"].outputs["Color"], bump.inputs["Height"])
    connect(bump.outputs["Normal"], bsdf.inputs["Normal"])
    for ref in ts.assignments:
        if ref.object and ref.slot_index < len(ref.object.material_slots):
            slot = ref.object.material_slots[ref.slot_index]
            if slot.material is None:
                slot.material = material
    return material
