import bpy


class OP_ObjectRef(bpy.types.PropertyGroup):
    object: bpy.props.PointerProperty(type=bpy.types.Object)
    name: bpy.props.StringProperty()


class OP_BakedMap(bpy.types.PropertyGroup):
    kind: bpy.props.EnumProperty(items=[(x, x.title(), "") for x in ("NORMAL", "AO", "CURVATURE")])
    image: bpy.props.PointerProperty(type=bpy.types.Image)


class OP_BakeSettings(bpy.types.PropertyGroup):
    high_objects: bpy.props.CollectionProperty(type=OP_ObjectRef)
    active_high: bpy.props.IntProperty()
    normal: bpy.props.BoolProperty(name="Normal", default=True)
    ao: bpy.props.BoolProperty(name="Ambient Occlusion", default=True)
    curvature: bpy.props.BoolProperty(name="Curvature (from Normal)", default=True)
    selected_to_active: bpy.props.BoolProperty(name="Bake from High Poly", default=True)
    margin: bpy.props.IntProperty(name="Margin", default=16, min=0, max=128)
    ray_distance: bpy.props.FloatProperty(
        name="Ray Distance", default=0.1, min=0, subtype="DISTANCE"
    )
    cage_mode: bpy.props.EnumProperty(
        name="Cage",
        items=[
            ("NONE", "None", ""),
            ("AUTO", "Automatic Distance", "Expand ray origin using the distance"),
            ("CUSTOM", "Custom Cage", "Single low-poly mesh with matching topology"),
        ],
        default="AUTO",
    )
    cage_object: bpy.props.PointerProperty(name="Cage Object", type=bpy.types.Object)
    samples: bpy.props.IntProperty(name="Samples", default=16, min=1, max=1024)
