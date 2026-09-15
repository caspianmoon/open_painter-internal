import bpy


class OP_ObjectRef(bpy.types.PropertyGroup):
    object: bpy.props.PointerProperty(type=bpy.types.Object)
    name: bpy.props.StringProperty()
    target: bpy.props.PointerProperty(name="Low Poly Target", type=bpy.types.Object)
    cage: bpy.props.PointerProperty(name="Target Cage", type=bpy.types.Object)


def map_changed(self, context):
    project = getattr(self.id_data, "open_painter", None)
    if project:
        for ts in project.texture_sets:
            if any(item.as_pointer() == self.as_pointer() for item in ts.baked_maps):
                from ..core.layer_stack import invalidate
                invalidate(ts)
                from ..evaluation.shader_preview import notify
                notify(ts, context)
                return


class OP_BakedMap(bpy.types.PropertyGroup):
    provenance: bpy.props.StringProperty()
    # Append values: existing .blend enum indices must remain stable.
    kind: bpy.props.EnumProperty(items=[(x, x.replace("_", " ").title(), "") for x in (
        "NORMAL", "AO", "CURVATURE", "POSITION", "WORLD_NORMAL", "OBJECT_NORMAL",
        "MATERIAL_ID", "OBJECT_ID", "UV_ISLAND", "UV_BORDER", "THICKNESS",
        "MEAN_CURVATURE", "SELECTION", "BEVEL_NORMAL", "BEVEL_MASK",
        "CURVATURE_THIN", "CURVATURE_WIDE", "CURVATURE_XWIDE",
    )], update=map_changed)
    image: bpy.props.PointerProperty(type=bpy.types.Image, update=map_changed)


class OP_BakeSettings(bpy.types.PropertyGroup):
    high_objects: bpy.props.CollectionProperty(type=OP_ObjectRef)
    auto_edge_rebake: bpy.props.BoolProperty(
        name="Auto-rebake Edge Maps", default=False,
        description="Rebake the edge map set in the background when a target mesh's geometry changes "
                    "(not on transforms); the four-reach bake takes seconds to a minute on dense meshes",
    )
    active_high: bpy.props.IntProperty()
    source_mode: bpy.props.EnumProperty(name="Source Matching", items=[
        ('ALL', 'All Sources', 'Project all listed high-poly objects onto each target (legacy behavior)'),
        ('MATCHED', 'By Target / Name', 'Use explicit targets, otherwise matching _low / _high names; missing pairs are errors'),
    ], default='ALL')
    ao_scope: bpy.props.EnumProperty(name="AO Occluders", items=[
        ('SET', 'Texture Set Assembly', 'Include other listed parts at their authored positions; exclude unrelated scene objects'),
        ('PAIR', 'Individual Parts', 'Only the target and its matched high-poly sources contribute occlusion'),
    ], default='SET')
    bevel_normal: bpy.props.BoolProperty(
        name="Bevel Normal", default=False,
        description="Rounded-edge shading normal from the Cycles Bevel node, baked to a tangent normal map so EEVEE and export see it",
    )
    bevel_mask: bpy.props.BoolProperty(
        name="Bevel Mask", default=False,
        description="Edge amount: distance between the Bevel-node normal and the geometry normal, baked as a grayscale mask",
    )
    bevel_radius: bpy.props.FloatProperty(
        name="Bevel Radius", default=0.02, min=0.0001, max=10.0, subtype="DISTANCE",
        description="World-space Bevel node radius for the Bevel Normal and Bevel Mask maps",
    )
    bevel_samples: bpy.props.IntProperty(
        name="Bevel Samples", default=8, min=2, max=64,
        description="Bevel node samples per shading point; more is smoother and slower",
    )
    supersample: bpy.props.EnumProperty(
        name="Anti-Aliasing", default="1",
        items=[("1", "Off", "Bake at the authored resolution"),
               ("2", "2x Supersample", "Bake at twice the size with scaled margin, then box-filter down; four times the bake memory")],
        description="Supersampled Cycles bakes reduce edge aliasing on Normal and AO maps; output stays the authored size",
    )
    ao_distance: bpy.props.FloatProperty(
        name="AO Distance", default=10.0, min=0.0, subtype="DISTANCE",
        description="World-space occlusion reach for the AO bake; 10 matches previous bakes made without an explicit world",
    )
    device: bpy.props.EnumProperty(name="Bake Device", items=[
        ('CPU', 'CPU', 'Use CPU without changing scene settings or device preferences'),
        ('SCENE', 'Scene Device', 'Use the current scene Cycles device and existing device preferences'),
    ], default='CPU')
    separate_cage_distance: bpy.props.BoolProperty(name="Separate Cage Extrusion", default=False,
        description="Use a separate ray-origin extrusion; older projects retain Ray Distance for both values")
    cage_extrusion: bpy.props.FloatProperty(name="Cage Extrusion", default=.1, min=0, subtype='DISTANCE')
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
    mean_curvature: bpy.props.BoolProperty(
        name="Mean Curvature (Signed)", default=False,
        description="Smooth signed curvature of the evaluated target: 0.5 flat, brighter convex, darker concave",
    )
    mean_curvature_distance: bpy.props.FloatProperty(
        name="Curvature Radius", default=0.02, min=0.0001, max=1000.0, subtype="DISTANCE",
        description="World-space radius within which surface turning is measured for the Curvature and Mean Curvature maps; edge bands are about this wide",
    )
    selection: bpy.props.BoolProperty(
        name="Selected Faces", default=False,
        description="Current face selection of the assigned meshes as a 0/1 mask; needs a face count unchanged by modifiers",
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
