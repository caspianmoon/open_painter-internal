"""Owned node materials for effect prototypes; standard nodes only (EEVEE + Cycles).

Instances read the ``op_random`` point attribute through an Instancer Attribute
node, so procedural textures differ per instance instead of repeating per
variant. Moss uses the CC0 Poly Haven ``moss_01`` textures.
"""
from pathlib import Path

import bpy

from .prototypes import ASSETS

MOSS_TEXTURES = {
    "diffuse": ("moss_01_diff_1k.jpg", "sRGB"),
    "alpha": ("moss_01_alpha_1k.png", "Non-Color"),
    "normal": ("moss_01_nor_gl_1k.png", "Non-Color"),
    "roughness": ("moss_01_rough_1k.png", "Non-Color"),
}


def _texture(name, colorspace):
    """Packed shared copy of a bundled texture, reused across effects."""
    path = ASSETS / "moss_01" / name
    for image in bpy.data.images:
        if image.get("open_painter_geometry_texture") == name and image.packed_file:
            return image
    image = bpy.data.images.load(str(path), check_existing=False)
    image.name = "Open Painter Moss - " + name.split("_")[2].title()
    image.colorspace_settings.name = colorspace
    image["open_painter_geometry_texture"] = name
    image["open_painter_source_provenance"] = "Poly Haven moss_01, CC0; see assets/geometry/moss_01/provenance.json"
    image.pack()
    return image


def _instance_random(nodes, links):
    attribute = nodes.new("ShaderNodeAttribute")
    attribute.attribute_type = "INSTANCER"
    attribute.attribute_name = "op_random"
    coordinates = nodes.new("ShaderNodeTexCoord")
    mapping = nodes.new("ShaderNodeMapping")
    links.new(coordinates.outputs["Object"], mapping.inputs["Vector"])
    offset = nodes.new("ShaderNodeVectorMath")
    offset.operation = "SCALE"
    offset.inputs[3].default_value = 37.0
    links.new(attribute.outputs["Vector"], offset.inputs[0])
    links.new(offset.outputs["Vector"], mapping.inputs["Location"])
    return mapping.outputs["Vector"], attribute.outputs["Fac"]


def _new(name):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    for node in list(nodes):
        nodes.remove(node)
    output = nodes.new("ShaderNodeOutputMaterial")
    principled = nodes.new("ShaderNodeBsdfPrincipled")
    material.node_tree.links.new(principled.outputs[0], output.inputs["Surface"])
    return material, principled


def _ramp(nodes, stops):
    ramp = nodes.new("ShaderNodeValToRGB")
    elements = ramp.color_ramp.elements
    while len(elements) < len(stops):
        elements.new(0)
    for element, (position, color) in zip(elements, stops):
        element.position = position
        element.color = (*color, 1)
    return ramp


def rust():
    material, principled = _new("OP Rust Flake")
    nodes, links = material.node_tree.nodes, material.node_tree.links
    vector, _ = _instance_random(nodes, links)
    noise = nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 26
    noise.inputs["Detail"].default_value = 7
    noise.inputs["Roughness"].default_value = .62
    links.new(vector, noise.inputs["Vector"])
    ramp = _ramp(nodes, [(.3, (.012, .005, .002)), (.5, (.075, .022, .007)), (.66, (.2, .06, .016)), (.85, (.4, .13, .035))])
    links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    # Broken edges are lighter, drier oxide dust; the rim attribute comes from the prototype.
    rim = nodes.new("ShaderNodeAttribute")
    rim.attribute_type = "GEOMETRY"
    rim.attribute_name = "op_rim"
    dust = nodes.new("ShaderNodeMix")
    dust.data_type = "RGBA"
    dust.inputs["B"].default_value = (.55, .25, .08, 1)
    links.new(ramp.outputs["Color"], dust.inputs["A"])
    rim_amount = nodes.new("ShaderNodeMath")
    rim_amount.operation = "MULTIPLY"
    rim_amount.inputs[1].default_value = .4
    links.new(rim.outputs["Fac"], rim_amount.inputs[0])
    links.new(rim_amount.outputs[0], dust.inputs["Factor"])
    links.new(dust.outputs["Result"], principled.inputs["Base Color"])
    principled.inputs["Roughness"].default_value = .95
    principled.inputs["Specular IOR Level"].default_value = .25
    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = .35
    bump.inputs["Distance"].default_value = .002
    fine = nodes.new("ShaderNodeTexNoise")
    fine.inputs["Scale"].default_value = 90
    fine.inputs["Detail"].default_value = 6
    links.new(vector, fine.inputs["Vector"])
    links.new(fine.outputs["Fac"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], principled.inputs["Normal"])
    return material


def moss():
    material, principled = _new("OP Moss Clump")
    nodes, links = material.node_tree.nodes, material.node_tree.links
    coordinates = nodes.new("ShaderNodeTexCoord")
    images = {}
    for key, (name, colorspace) in MOSS_TEXTURES.items():
        node = nodes.new("ShaderNodeTexImage")
        node.image = _texture(name, colorspace)
        node.interpolation = "Linear"
        links.new(coordinates.outputs["UV"], node.inputs["Vector"])
        images[key] = node
    _, random = _instance_random(nodes, links)
    hue = nodes.new("ShaderNodeHueSaturation")
    hue.inputs["Saturation"].default_value = 1.05
    links.new(images["diffuse"].outputs["Color"], hue.inputs["Color"])
    # Slight per-clump brightness variation breaks up repeated cards.
    value = nodes.new("ShaderNodeMath")
    value.operation = "MULTIPLY_ADD"
    value.inputs[1].default_value = .3
    value.inputs[2].default_value = .85
    links.new(random, value.inputs[0])
    links.new(value.outputs[0], hue.inputs["Value"])
    links.new(hue.outputs["Color"], principled.inputs["Base Color"])
    links.new(images["roughness"].outputs["Color"], principled.inputs["Roughness"])
    links.new(images["alpha"].outputs["Color"], principled.inputs["Alpha"])
    normal_map = nodes.new("ShaderNodeNormalMap")
    links.new(images["normal"].outputs["Color"], normal_map.inputs["Color"])
    links.new(normal_map.outputs["Normal"], principled.inputs["Normal"])
    principled.inputs["Specular IOR Level"].default_value = .3
    # Thin cards: translucency lets light through the tufts (the Poly Haven shader does the same).
    translucent = nodes.new("ShaderNodeBsdfTranslucent")
    links.new(hue.outputs["Color"], translucent.inputs["Color"])
    links.new(normal_map.outputs["Normal"], translucent.inputs["Normal"])
    mix = nodes.new("ShaderNodeMixShader")
    mix.inputs["Fac"].default_value = .35
    links.new(principled.outputs[0], mix.inputs[1])
    links.new(translucent.outputs[0], mix.inputs[2])
    transparent = nodes.new("ShaderNodeBsdfTransparent")
    alpha_mix = nodes.new("ShaderNodeMixShader")
    links.new(images["alpha"].outputs["Color"], alpha_mix.inputs["Fac"])
    links.new(transparent.outputs[0], alpha_mix.inputs[1])
    links.new(mix.outputs[0], alpha_mix.inputs[2])
    output = next(n for n in nodes if n.bl_idname == "ShaderNodeOutputMaterial")
    links.new(alpha_mix.outputs[0], output.inputs["Surface"])
    material.surface_render_method = "DITHERED"
    material.use_backface_culling = False
    return material


def snow():
    material, principled = _new("OP Snow Clump")
    nodes, links = material.node_tree.nodes, material.node_tree.links
    vector, _ = _instance_random(nodes, links)
    principled.inputs["Base Color"].default_value = (.93, .95, .99, 1)
    principled.inputs["Roughness"].default_value = .82
    principled.inputs["Specular IOR Level"].default_value = .35
    principled.inputs["Subsurface Weight"].default_value = .7
    principled.inputs["Subsurface Radius"].default_value = (.03, .035, .045)
    principled.inputs["Subsurface Scale"].default_value = .08
    # Granular crust: fine noise bump; a coarser one breaks the silhouette shading.
    sparkle = nodes.new("ShaderNodeTexNoise")
    sparkle.inputs["Scale"].default_value = 260
    sparkle.inputs["Detail"].default_value = 3
    links.new(vector, sparkle.inputs["Vector"])
    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = .3
    bump.inputs["Distance"].default_value = .0015
    links.new(sparkle.outputs["Fac"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], principled.inputs["Normal"])
    return material


def wood():
    material, principled = _new("OP Wood Splinter")
    nodes, links = material.node_tree.nodes, material.node_tree.links
    vector, _ = _instance_random(nodes, links)
    stretch = nodes.new("ShaderNodeVectorMath")
    stretch.operation = "MULTIPLY"
    stretch.inputs[1].default_value = (14, 1.2, 14)
    links.new(vector, stretch.inputs[0])
    grain = nodes.new("ShaderNodeTexNoise")
    grain.inputs["Scale"].default_value = 3
    grain.inputs["Detail"].default_value = 5
    links.new(stretch.outputs["Vector"], grain.inputs["Vector"])
    ramp = _ramp(nodes, [(.35, (.22, .12, .05)), (.55, (.45, .29, .13)), (.75, (.62, .45, .24))])
    links.new(grain.outputs["Fac"], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], principled.inputs["Base Color"])
    principled.inputs["Roughness"].default_value = .78
    principled.inputs["Specular IOR Level"].default_value = .3
    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = .4
    bump.inputs["Distance"].default_value = .002
    links.new(grain.outputs["Fac"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], principled.inputs["Normal"])
    return material


def surface_color(ts):
    """Average authored base colour of the set's output, when it exists."""
    try:
        import numpy as np
        from ..core.image_manager import pixels
        output = next((o for o in ts.outputs if o.channel == "BASE_COLOR" and o.image and all(o.image.size)), None)
        if output is None:
            return None
        rgba = pixels(output.image)
        weights = rgba[..., 3:4]
        if weights.sum() <= 0:
            return None
        mean = (rgba[..., :3] * weights).sum(axis=(0, 1)) / weights.sum()
        return tuple(float(c) for c in mean)
    except Exception:
        return None


def peel(paint=None, primer=(.56, .52, .46), ts=None):
    """Two-sided sheet: painted face outward, primer/bare underside inward."""
    if paint is None:
        paint = (surface_color(ts) if ts is not None else None) or (.15, .2, .28)
    material, principled = _new("OP Peel Fragment")
    nodes, links = material.node_tree.nodes, material.node_tree.links
    geometry = nodes.new("ShaderNodeNewGeometry")
    color = nodes.new("ShaderNodeMix")
    color.data_type = "RGBA"
    color.inputs["A"].default_value = (*paint, 1)
    color.inputs["B"].default_value = (*primer, 1)
    links.new(geometry.outputs["Backfacing"], color.inputs["Factor"])
    links.new(color.outputs["Result"], principled.inputs["Base Color"])
    roughness = nodes.new("ShaderNodeMix")
    roughness.data_type = "FLOAT"
    roughness.inputs["A"].default_value = .35
    roughness.inputs["B"].default_value = .85
    links.new(geometry.outputs["Backfacing"], roughness.inputs["Factor"])
    links.new(roughness.outputs["Result"], principled.inputs["Roughness"])
    material.use_backface_culling = False
    return material


def weld():
    material, principled = _new("OP Weld Bead")
    nodes, links = material.node_tree.nodes, material.node_tree.links
    coordinates = nodes.new("ShaderNodeTexCoord")
    noise = nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 120
    noise.inputs["Detail"].default_value = 4
    links.new(coordinates.outputs["Object"], noise.inputs["Vector"])
    # Heat tint: dull steel with straw/blue oxide bands varying along the bead.
    # Heat tint bands follow the bead's lateral position (op_side from the
    # profile): straw at the crown, blue and brown oxides towards the toes.
    side = nodes.new("ShaderNodeAttribute")
    side.attribute_type = "GEOMETRY"
    side.attribute_name = "op_side"
    lateral = nodes.new("ShaderNodeMath")
    lateral.operation = "COSINE"
    side_angle = nodes.new("ShaderNodeMath")
    side_angle.operation = "MULTIPLY"
    side_angle.inputs[1].default_value = 6.283185307
    links.new(side.outputs["Fac"], side_angle.inputs[0])
    links.new(side_angle.outputs[0], lateral.inputs[0])
    edge = nodes.new("ShaderNodeMath")
    edge.operation = "ABSOLUTE"
    links.new(lateral.outputs[0], edge.inputs[0])
    wobble = nodes.new("ShaderNodeTexNoise")
    wobble.inputs["Scale"].default_value = 14
    wobble.inputs["Detail"].default_value = 2
    links.new(coordinates.outputs["Object"], wobble.inputs["Vector"])
    mixed = nodes.new("ShaderNodeMath")
    mixed.operation = "MULTIPLY_ADD"
    mixed.inputs[1].default_value = .45
    links.new(wobble.outputs["Fac"], mixed.inputs[0])
    links.new(edge.outputs[0], mixed.inputs[2])
    ramp = _ramp(nodes, [(.0, (.33, .32, .3)), (.55, (.34, .32, .29)), (.78, (.36, .3, .2)), (.95, (.25, .27, .34)), (1.15, (.22, .17, .12))])
    links.new(mixed.outputs[0], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], principled.inputs["Base Color"])
    principled.inputs["Metallic"].default_value = 1
    principled.inputs["Roughness"].default_value = .42
    rough = nodes.new("ShaderNodeMath")
    rough.operation = "MULTIPLY_ADD"
    rough.inputs[1].default_value = .3
    rough.inputs[2].default_value = .3
    links.new(noise.outputs["Fac"], rough.inputs[0])
    links.new(rough.outputs[0], principled.inputs["Roughness"])
    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = .5
    bump.inputs["Distance"].default_value = .0015
    links.new(noise.outputs["Fac"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], principled.inputs["Normal"])
    return material


def _simple(name, color, roughness, metallic=0.0, bump_scale=None):
    material, principled = _new(name)
    nodes, links = material.node_tree.nodes, material.node_tree.links
    vector, random = _instance_random(nodes, links)
    shade = nodes.new("ShaderNodeMix")
    shade.data_type = "RGBA"
    shade.inputs["A"].default_value = (*color, 1)
    shade.inputs["B"].default_value = (*[c * .6 for c in color], 1)
    links.new(random, shade.inputs["Factor"])
    links.new(shade.outputs["Result"], principled.inputs["Base Color"])
    principled.inputs["Roughness"].default_value = roughness
    principled.inputs["Metallic"].default_value = metallic
    if bump_scale:
        noise = nodes.new("ShaderNodeTexNoise")
        noise.inputs["Scale"].default_value = bump_scale
        noise.inputs["Detail"].default_value = 5
        links.new(vector, noise.inputs["Vector"])
        bump = nodes.new("ShaderNodeBump")
        bump.inputs["Strength"].default_value = .3
        bump.inputs["Distance"].default_value = .002
        links.new(noise.outputs["Fac"], bump.inputs["Height"])
        links.new(bump.outputs["Normal"], principled.inputs["Normal"])
    return material


def bolt():
    return _simple("OP Bolt", (.42, .43, .44), .45, metallic=1.0, bump_scale=200)


def stitch():
    material = _simple("OP Stitch Thread", (.72, .6, .35), .75)
    principled = next(n for n in material.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    principled.inputs["Anisotropic"].default_value = .6
    return material


def scale():
    return _simple("OP Scale", (.28, .33, .22), .55, bump_scale=60)


def rock():
    return _simple("OP Rock", (.36, .34, .31), .9, bump_scale=30)


def bark():
    return _simple("OP Bark", (.24, .16, .1), .95, bump_scale=45)


BUILDERS = {"RUST": rust, "MOSS": moss, "SNOW": snow, "SPLINTERS": wood, "PEEL": peel, "WELD": weld,
            "BOLT": bolt, "STITCH": stitch, "SCALE": scale, "ROCK": rock, "BARK": bark}

# How much each kind's colour follows the surface under it by default (0.10.1): rust scale is
# the rust it lifts from, a paint chip is the paint it left, a splinter is the wood; snow,
# moss, bolts and thread are their own things.
MATCH = {"RUST": .2, "PEEL": 1.0, "SPLINTERS": .8, "SCALE": .5, "BARK": .6, "ROCK": .35, "MOSS": 0.0, "SNOW": 0.0, "BOLT": 0.0}


def match_surface(material):
    """Mix the material's base colour toward the instance's ``op_color`` (the surface colour under
    it) by the attribute's alpha, which the helper sets to its Match Surface amount. A peel
    fragment keeps its primer underside: only the painted face takes the colour."""
    tree = material.node_tree
    nodes, links = tree.nodes, tree.links
    principled = next(n for n in nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    into = principled.inputs["Base Color"]
    attribute = nodes.new("ShaderNodeAttribute")
    attribute.attribute_type = "INSTANCER"
    attribute.attribute_name = "op_color"
    attribute.name = "OP Surface Color"
    mix = nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.name = "OP Match Surface"
    link = next((l for l in links if l.to_socket == into), None)
    if link is not None:
        links.new(link.from_socket, mix.inputs["A"])
        links.remove(link)
    else:
        mix.inputs["A"].default_value = into.default_value[:]
    links.new(attribute.outputs["Color"], mix.inputs["B"])
    links.new(attribute.outputs["Alpha"], mix.inputs["Factor"])
    links.new(mix.outputs["Result"], into)
    return material


def create(kind, ts=None):
    if kind == "PEEL":
        material = peel(ts=ts)
        # The painted face only: the primer underside stays primer.
        nodes, links = material.node_tree.nodes, material.node_tree.links
        color = next(n for n in nodes if n.bl_idname == "ShaderNodeMix" and n.data_type == "RGBA")
        attribute = nodes.new("ShaderNodeAttribute")
        attribute.attribute_type = "INSTANCER"
        attribute.attribute_name = "op_color"
        attribute.name = "OP Surface Color"
        face = nodes.new("ShaderNodeMix")
        face.data_type = "RGBA"
        face.name = "OP Match Surface"
        face.inputs["A"].default_value = color.inputs["A"].default_value[:]
        links.new(attribute.outputs["Color"], face.inputs["B"])
        links.new(attribute.outputs["Alpha"], face.inputs["Factor"])
        links.new(face.outputs["Result"], color.inputs["A"])
        return material
    return match_surface(BUILDERS[kind]())
