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
    curvature: bpy.props.BoolProperty(name="Curvature (Target Mesh Edges)", default=True)
    curvature_angle: bpy.props.FloatProperty(
        name="Minimum Edge Angle",
        default=20,
        min=1,
        max=179,
        description="Convex target-mesh creases below this angle are excluded before intensity is applied",
    )
    curvature_width: bpy.props.FloatProperty(
        name="Edge Width (px)",
        default=4,
        min=1,
        max=64,
        description="Width of baked edge coverage in texture pixels",
    )
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
