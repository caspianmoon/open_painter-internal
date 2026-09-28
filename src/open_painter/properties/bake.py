import bpy


class OP_ObjectRef(bpy.types.PropertyGroup):
    object: bpy.props.PointerProperty(type=bpy.types.Object)
    name: bpy.props.StringProperty()
    target: bpy.props.PointerProperty(name="Low Poly Target", type=bpy.types.Object)
    cage: bpy.props.PointerProperty(name="Target Cage", type=bpy.types.Object)
    ignorable: bpy.props.BoolProperty(name="Ignore Brush Excludes", default=False,
        description="Where the Ignore High Poly correction is painted, this source is left out of the projection")


CORRECTION_KINDS = (
    ('OFFSET', 'Projection Distance', 'Signed world-space distance added to the cage extrusion'),
    ('SKEW', 'Skew Correction', 'Straighten rays toward the dominant face normal (0 keeps the smooth normal)'),
    ('DIRECTION', 'Projection Direction', 'Turn rays toward the chosen direction vector'),
    ('IGNORE', 'Ignore High Poly', 'Leave the sources marked "Ignore Brush Excludes" out of the projection'),
    ('AO_EXCLUDE', 'AO Exclusion', 'Bake occlusion from the part alone, without the rest of the assembly'),
)
NEUTRAL = {'OFFSET': .5, 'SKEW': 0., 'DIRECTION': 0., 'IGNORE': 0., 'AO_EXCLUDE': 0.}


def _brush_value(self):
    from ..painting.brush_bridge import get_value
    grey = get_value(self.id_data.open_painter)
    if self.active == 'OFFSET':
        return (grey - .5) * 2 * self.offset_range
    return grey


def _set_brush_value(self, value):
    from ..painting.brush_bridge import set_value
    if self.active == 'OFFSET':
        limit = max(self.offset_range, 1e-9)
        set_value(self.id_data.open_painter, min(1., max(0., .5 + value / (2 * limit))))
    else:
        set_value(self.id_data.open_painter, min(1., max(0., value)))


def _active_changed(self, context):
    """Switching the painted map re-targets a running correction session."""
    project = self.id_data.open_painter
    if project.paint_correction and project.paint_correction != self.active:
        from ..baking.corrections import begin_brush
        # Only the painted set's selector: the dock may show another set while the session runs.
        ts = next((item for item in project.texture_sets if item.id == project.paint_set_id), None)
        if ts is not None and ts.bake.corrections.as_pointer() == self.as_pointer():
            try:
                begin_brush(context, ts, self.active)
            except Exception as exc:
                project.status = str(exc)


class OP_BakeCorrections(bpy.types.PropertyGroup):
    """Painted per-region bake fixes (0.9.1): images at the set resolution, neutral where unpainted."""
    offset_image: bpy.props.PointerProperty(type=bpy.types.Image)
    skew_image: bpy.props.PointerProperty(type=bpy.types.Image)
    direction_image: bpy.props.PointerProperty(type=bpy.types.Image)
    ignore_image: bpy.props.PointerProperty(type=bpy.types.Image)
    ao_exclude_image: bpy.props.PointerProperty(type=bpy.types.Image)
    active: bpy.props.EnumProperty(name="Correction", items=CORRECTION_KINDS, default='OFFSET', update=_active_changed)
    offset_range: bpy.props.FloatProperty(name="Distance Range", default=.05, min=1e-5, subtype='DISTANCE',
        description="World-space distance a fully white or black Projection Distance stroke adds or removes")
    direction_vector: bpy.props.FloatVectorProperty(name="Direction", default=(0., 0., 1.), subtype='DIRECTION',
        description="World-space direction rays turn toward where Projection Direction is painted")
    value: bpy.props.FloatProperty(name="Value", get=_brush_value, set=_set_brush_value, step=0.1, precision=4,
        description="What the brush paints: a signed distance for Projection Distance, a 0-1 weight otherwise")
    live_rebake: bpy.props.BoolProperty(name="Rebake Painted Region After Stroke", default=True,
        description="After each correction stroke, rebake only the faces the stroke touched into the existing maps")
    show_cage: bpy.props.BoolProperty(name="Show Corrected Cage", default=True,
        description="Draw the cage the corrections produce as a wire overlay while painting")


def map_changed(self, context):
    project = getattr(self.id_data, "open_painter", None)
    if project:
        for ts in project.texture_sets:
            if any(item.as_pointer() == self.as_pointer() for item in ts.baked_maps):
                from ..smart_materials.portable import bind_mesh_maps
                bind_mesh_maps(ts)
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
    corrections: bpy.props.PointerProperty(type=OP_BakeCorrections)
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
    # Keep the saved CPU=0 / SCENE=1 enum values; append AUTO.
    device: bpy.props.EnumProperty(name="Bake Device", items=[
        ('CPU', 'CPU', 'Always bake on CPU, whatever devices are enabled'),
        ('SCENE', 'Scene Device',
         "Use the current scene's Cycles device; falls back to CPU when no Cycles GPU device is enabled"),
        ('AUTO', 'Auto (GPU if available)',
         'Bake on an enabled Cycles GPU device whenever one is available, otherwise CPU'),
    ], default='AUTO')
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
        name="Curvature Radius", default=0.025, min=0.0001, max=1000.0, subtype="DISTANCE",
        description="Bevel node radius for the Curvature edge set (scaled per reach: thin, normal, wide, extreme); also the measuring radius of the Mean Curvature map",
    )
    selection: bpy.props.BoolProperty(
        name="Selected Faces", default=False,
        description="Current face selection of the assigned meshes as a 0/1 mask; needs a face count unchanged by modifiers",
    )
    thickness_max_distance: bpy.props.FloatProperty(
        name="Thickness Distance", default=0, min=0, subtype="DISTANCE",
        description="Maximum inward intersection distance and white value; zero uses the largest complete target-mesh bounds diagonal",
    )
    selected_to_active: bpy.props.BoolProperty(name="Bake from High Poly", default=False)
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
