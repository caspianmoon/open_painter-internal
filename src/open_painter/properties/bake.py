import bpy


class OP_ObjectRef(bpy.types.PropertyGroup):
    object: bpy.props.PointerProperty(type=bpy.types.Object)
    name: bpy.props.StringProperty()


def map_changed(self, context):
    project = getattr(self.id_data, "open_painter", None)
    if project:
        for ts in project.texture_sets:
            if any(item.as_pointer() == self.as_pointer() for item in ts.baked_maps):
                from ..core.layer_stack import invalidate
                invalidate(ts)
                return


class OP_BakedMap(bpy.types.PropertyGroup):
    # Append values: existing .blend enum indices must remain stable.
    kind: bpy.props.EnumProperty(items=[(x, x.replace("_", " ").title(), "") for x in (
        "NORMAL", "AO", "CURVATURE", "POSITION", "WORLD_NORMAL", "OBJECT_NORMAL",
        "MATERIAL_ID", "OBJECT_ID", "UV_ISLAND", "UV_BORDER", "THICKNESS",
    )], update=map_changed)
    image: bpy.props.PointerProperty(type=bpy.types.Image, update=map_changed)


class OP_BakeSettings(bpy.types.PropertyGroup):
    high_objects: bpy.props.CollectionProperty(type=OP_ObjectRef)
    active_high: bpy.props.IntProperty()
    normal: bpy.props.BoolProperty(name="Normal", default=True)
    ao: bpy.props.BoolProperty(name="Ambient Occlusion", default=True)
    curvature: bpy.props.BoolProperty(name="Curvature (Target Mesh Edges)", default=True)
    position: bpy.props.BoolProperty(name="Position", default=False)
    world_normal: bpy.props.BoolProperty(name="World Space Normal", default=False)
    object_normal: bpy.props.BoolProperty(name="Object Space Normal", default=False)
    material_id: bpy.props.BoolProperty(name="Material ID", default=False)
    object_id: bpy.props.BoolProperty(name="Object ID", default=False)
    uv_island: bpy.props.BoolProperty(name="UV Island ID", default=False)
    uv_border: bpy.props.BoolProperty(name="UV Border", default=False)
    uv_border_width: bpy.props.FloatProperty(name="Border Width (px)", default=4, min=1, max=64)
    thickness: bpy.props.BoolProperty(name="Thickness (Raycast)", default=False)
    thickness_resolution: bpy.props.EnumProperty(
        name="Thickness Sampling",
        items=[(str(n), f"{n} × {n}", "Ray sampling grid, stored at the texture set resolution") for n in (64, 128, 256)],
        default="128",
    )
    thickness_samples: bpy.props.EnumProperty(
        name="Rays per Texel", items=[(str(n), str(n), "Inward rays against the evaluated target mesh") for n in (1, 4, 8)],
        default="1",
    )
    thickness_max_distance: bpy.props.FloatProperty(
        name="Thickness Distance", default=0, min=0, subtype="DISTANCE",
        description="Maximum inward intersection distance and white value; zero uses the largest complete target-mesh bounds diagonal",
    )
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
