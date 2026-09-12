import bpy


def changed(self, context):
    from .layer import changed as invalidate

    invalidate(self, context)


class OP_Mask(bpy.types.PropertyGroup):
    id: bpy.props.StringProperty()
    name: bpy.props.StringProperty(default="Mask")
    enabled: bpy.props.BoolProperty(default=True, update=changed)
    source: bpy.props.EnumProperty(
        items=[
            ("PAINT", "Painted", "Paint a grayscale mask"),
            ("BAKED", "Baked Map", "Use a mesh map"),
            ("GENERATOR", "Generator", "Procedural mesh-aware mask"),
            ("NOISE", "Noise", "Procedural noise"),
        ],
        update=changed,
    )
    image: bpy.props.PointerProperty(type=bpy.types.Image, update=changed)
    map_type: bpy.props.EnumProperty(
        name="Map",
        items=[(x, x.title(), "") for x in ("AO", "CURVATURE", "NORMAL")],
        update=changed,
    )
    generator: bpy.props.EnumProperty(
        items=[
            ("EDGE_WEAR", "Edge Wear", "Requires Curvature"),
            ("DIRT", "Dirt", "Requires AO and Curvature"),
            ("DUST", "Dust (Normal Approximation)", "Uses tangent normal Z; not world up"),
        ],
        update=changed,
    )
    operation: bpy.props.EnumProperty(
        name="Combine", items=[("MULTIPLY", "Multiply", ""), ("ADD", "Add", "")], update=changed
    )
    invert: bpy.props.BoolProperty(name="Invert", update=changed)
    black: bpy.props.FloatProperty(name="Black Level", min=0, max=1, update=changed)
    white: bpy.props.FloatProperty(name="White Level", default=1, min=0, max=1, update=changed)
    strength: bpy.props.FloatProperty(name="Strength", default=1, min=0, max=1, update=changed)
    amount: bpy.props.FloatProperty(name="Amount", default=0.5, min=0, max=1, update=changed)
    contrast: bpy.props.FloatProperty(name="Contrast", default=2, min=0.01, max=20, update=changed)
    noise_scale: bpy.props.FloatProperty(
        name="Noise Scale", default=12, min=1, max=512, update=changed
    )
    noise_strength: bpy.props.FloatProperty(
        name="Noise Strength", default=0.2, min=0, max=1, update=changed
    )
    seed: bpy.props.IntProperty(name="Seed", default=1, min=0, update=changed)
