import bpy

from ..core.image_manager import create
from ..core.layer_stack import active, invalidate
from ..core.project_manager import active_set, require_set
from ..utils.ids import new_id
from .operators import SafeOperator


def draw_mesh_selector(layout, ts, mask):
    """Mesh mask (0.9.2): pick what Blender structure drives the coverage."""
    selector = mask.mesh
    layout.prop(selector, 'kind')
    kind = selector.kind
    if kind in {'OBJECT', 'UV_ISLAND', 'REGION'}:
        layout.prop(selector, 'object', text='Object' if kind == 'OBJECT' else 'On Object')
        if selector.object is None and selector.object_name:
            layout.label(text=f"'{selector.object_name}' is not in this file", icon='ERROR')
    if kind == 'COLLECTION':
        layout.prop(selector, 'collection')
        layout.prop(selector, 'include_children')
        collection = selector.resolve('collection')
        if collection is not None:
            assigned = {r.object for r in ts.assignments if r.object}
            members = [o for o in (collection.all_objects if selector.include_children else collection.objects)
                       if o in assigned]
            layout.label(text=f'{len(members)} assigned object(s) in the collection', icon='OUTLINER_COLLECTION')
        elif selector.collection_name:
            layout.label(text=f"'{selector.collection_name}' is not in this file", icon='ERROR')
    elif kind == 'MATERIAL':
        layout.prop(selector, 'material')
    elif kind in {'VERTEX_GROUP', 'ATTRIBUTE'}:
        objects = [r.object for r in ts.assignments if r.object]
        if kind == 'VERTEX_GROUP' and objects:
            layout.prop_search(selector, 'attribute', objects[0], 'vertex_groups', text='Group')
        elif objects:
            layout.prop_search(selector, 'attribute', objects[0].data, 'attributes', text='Attribute')
        else:
            layout.prop(selector, 'attribute')
    elif kind == 'FACE_SET':
        layout.prop(selector, 'face_set')
    elif kind == 'UV_ISLAND':
        layout.prop(selector, 'island')
        layout.label(text='-1 uses the islands of the selected faces', icon='INFO')
    elif kind == 'NORMAL_DIRECTION':
        layout.prop(selector, 'direction')
        layout.prop(selector, 'angle')
        layout.prop(selector, 'softness')
    elif kind == 'FACE_SELECTION':
        layout.label(text='Follows the face selection of the assigned meshes', icon='RESTRICT_SELECT_OFF')
    elif kind == 'REGION':
        import json
        try:
            count = len(json.loads(selector.region_faces))
        except (ValueError, TypeError):
            count = 0
        layout.label(text=f'{count} saved faces', icon='FACESEL')
        prefix = 'Frozen spatial query: '
        try:
            query = json.loads(selector.region_provenance[len(prefix):]) if selector.region_provenance.startswith(prefix) else None
        except (ValueError, TypeError):
            query = None
        if isinstance(query, dict):
            layout.label(text=f"Frozen {query.get('space', '')} / {query.get('selection', '')} query", icon='INFO')
            for key, label in (('bounds_min', 'Minimum'), ('bounds_max', 'Maximum')):
                values = query.get(key, [])
                if isinstance(values, list) and len(values) == 3 and all(type(value) in (int, float) for value in values):
                    layout.label(text=label + ': ' + ', '.join(f'{value:.4g}' for value in values))
            if 'normal_direction' in query:
                layout.label(text='Surface-normal filter recorded in query provenance')
            layout.label(text='Whole faces; bounds do not cut polygons', icon='INFO')
        else:
            layout.label(text=selector.region_provenance or 'Artist face region', icon='INFO')
        layout.operator('open_painter.mask', text='Replace with Selected Faces', icon='FACESEL').action = 'REGION_SELECTION'
        layout.label(text='View Mask to inspect coverage; replacement is undoable', icon='INFO')
    if kind in {'ATTRIBUTE', 'FACE_SELECTION'}:
        from .offline_analysis import draw_action
        box = layout.box()
        box.label(text='Cut surfaces', icon='MOD_BOOLEAN')
        draw_action(box, 'MARK', text='Mark Selected Cut Faces', icon='FACESEL')
        box.label(text='Writes open_painter_cut_surface on the active mesh; read it as the attribute', icon='INFO')
        if kind == 'ATTRIBUTE':
            draw_action(box, 'STRAIN', text='Rest-State Strain…', icon='MOD_SIMPLEDEFORM')
    layout.label(text='Frozen membership through render UVs' if kind == 'REGION' else 'Exact through the render UVs; follows scene changes live', icon='INFO')


class OP_OT_mask(SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.mask"
    bl_label = "Edit Mask"
    bl_options = {"REGISTER", "UNDO"}
    action: bpy.props.EnumProperty(
        items=[(x, x.replace('_', ' ').title(), "") for x in ("ADD", "DELETE", "UP", "DOWN", "BAKE", 'ANALYZE', 'FREEZE',
                                                              'INTO_GROUP', 'OUT_OF_GROUP', 'DISSOLVE', 'NEW_GRAPH', 'REGION_SELECTION')]
    )
    black: bpy.props.BoolProperty(name="Black Mask", default=False)
    source: bpy.props.EnumProperty(
        items=[(x, x.title(), "") for x in ("PAINT", "FILL", "BAKED", "GENERATOR", "NOISE", "PROCEDURAL", "FILTER", 'SCENE', 'REFERENCE', 'MESH', 'GROUP', 'GRAPH')]
    )

    @classmethod
    def description(cls, context, properties):
        return {'INTO_GROUP': 'Move the mask into the group directly above it (masks feed masks)',
                'OUT_OF_GROUP': 'Lift the mask out of its group, after the group',
                'DISSOLVE': 'Remove the group but keep its members in place',
                'NEW_GRAPH': 'Create a starter shader node group for this Custom Graph mask',
                'ADD': f'Add a {properties.source.replace("_", " ").title()} mask source'}.get(properties.action, '')

    def run(self, context):
        ts = require_set(context)
        layer = active(ts)
        if layer is None:
            raise ValueError("Select a layer or folder first.")
        if self.action == 'REGION_SELECTION':
            import json
            from ..authoring.regions import capture, selector
            mask = layer.masks[layer.active_mask]
            obj = mask.mesh.resolve('object')
            if obj is None:
                raise ValueError('Choose the assigned object whose selected faces replace this region.')
            regions = capture(ts, {'object:selection': obj})
            selected = [item for item in regions if item['kind'] == 'ARTIST_SELECTION']
            if not selected:
                raise ValueError('Select faces on the assigned mesh first.')
            values = selector(ts, selected[0]['id'], {'object:selection': obj})
            faces = set(json.loads(values['region_faces']))
            for item in selected[1:]:
                faces.update(json.loads(selector(ts, item['id'], {'object:selection': obj})['region_faces']))
            values['region_faces'] = json.dumps(sorted(faces))
            values['region_id'] = new_id()
            for key, value in values.items():
                setattr(mask.mesh, key, value)
            invalidate(ts)
            return
        if self.action in {'ANALYZE','FREEZE'}:
            mask = layer.masks[layer.active_mask]
            from ..generators.scene_fields import update
            if self.action == 'ANALYZE':
                update(ts, mask)
                mask.influence.update_serial += 1
            else:
                if not mask.influence.image:
                    update(ts,mask)
                mask.influence.mode = 'FROZEN'
        elif self.action == "BAKE":
            if not layer.masks:
                raise ValueError("Select a layer with a mask first.")
            from ..evaluation.mask_eval import bake_mask_texture
            from .anchors import broken_labels, report_broken

            image = bake_mask_texture(ts, layer)
            broken = broken_labels(ts, [m.id for m in layer.masks if m.source == "ANCHOR"])
            # Commit only after evaluation and packing have succeeded.
            layer.masks.clear()
            report_broken(self, context, broken)
            mask = layer.masks.add()
            mask.id, mask.name, mask.source = new_id(), "Baked Mask", "PAINT"
            mask.image = image
            layer.active_mask = 0
        elif self.action == "ADD":
            anchor = layer.active_mask if layer.masks else None
            mask = layer.masks.add()
            mask.id, mask.source = new_id(), self.source
            mask.name = self.source.title() + " Mask"
            if self.source == "FILTER":
                mask.name = "Levels"
                mask.effect.kind = "LEVELS"
            elif self.source == 'MESH':
                mask.name = 'Mesh Mask'
                mask.mesh.kind = 'COLLECTION'
                mask.mesh.object = next((r.object for r in ts.assignments if r.object == context.object), None)
            elif self.source == 'GROUP':
                mask.name = 'Group'
            elif self.source == 'GRAPH':
                mask.name = 'Custom Graph'
                mask.node_group = starter_graph(f'{ts.name} {layer.name} Mask Graph')
            elif self.source == "PROCEDURAL":
                mask.procedural = "FBM"
                mask.name = "FBM"
            if self.source in {"PAINT", "FILL"}:
                mask.image = create(
                    f"{ts.name}_{layer.name}_Mask",
                    int(ts.resolution) if self.source == "PAINT" else 32,
                    (0, 0, 0, 1) if self.black else (1, 1, 1, 1),
                )
                mask.image.pack()
            from ..core import mask_stack
            layer.active_mask = mask_stack.adopt(layer, len(layer.masks) - 1, anchor)
            from ..graph import mirror
            tree = mirror.find_tree(ts)
            if tree is not None:
                # The node appears on the next sync: select it then, exclusively,
                # so it can be placed immediately.
                index = layer.active_mask
                mirror.request_select(tree, mirror.mask_node_id(layer, index, layer.masks[index]))
        elif layer.masks:
            from ..core import mask_stack
            i = max(0, min(layer.active_mask, len(layer.masks) - 1))
            if self.action == "DELETE":
                from .anchors import broken_labels, report_broken
                start, stop = mask_stack.block(layer.masks, i)
                broken = broken_labels(ts, [m.id for m in list(layer.masks)[start:stop] if m.source == "ANCHOR"])
                mask_stack.remove_block(layer, i)
                layer.active_mask = max(0, i - 1)
                report_broken(self, context, broken)
            elif self.action == 'INTO_GROUP':
                if mask_stack.into_group_above(layer, i) is None:
                    raise ValueError('Place the mask directly under a group (or its members) first')
            elif self.action == 'OUT_OF_GROUP':
                layer.active_mask = mask_stack.out_of_group(layer, i)
            elif self.action == 'DISSOLVE':
                if layer.masks[i].source != 'GROUP':
                    raise ValueError('Select a mask group to dissolve')
                mask_stack.dissolve(layer, i)
                layer.active_mask = min(i, len(layer.masks) - 1)
            elif self.action == 'NEW_GRAPH':
                mask = layer.masks[i]
                if mask.source != 'GRAPH':
                    raise ValueError('Select a Custom Graph mask first')
                mask.node_group = starter_graph(f'{ts.name} {layer.name} Mask Graph')
            else:
                layer.active_mask = mask_stack.move(layer, i, -1 if self.action == "UP" else 1)
        invalidate(ts)
        from ..painting.paint_session import synchronize
        from .dock import state

        if context.area:
            ui = state.get(context.area)
            ui.mask_selected = bool(layer.masks)
            ui.properties_scroll = 0
        synchronize(context, mask=True)


class OP_OT_mask_paint(SafeOperator, bpy.types.Operator):
    """Paint over a procedural mask without rasterising it (UI plan phase 2).

    The generator stays; a paint mask joins the stack on top of it with an explicit
    role: *Add Coverage* is a black image combined with Add (paint white where the
    layer should show), *Remove Coverage* a white image combined with Multiply (paint
    black where it should not).
    """
    bl_idname = "open_painter.mask_paint"
    bl_label = "Add Paint to Mask"
    bl_description = "Add a paint mask above the selected mask stack so corrections are painted, not rasterised"
    bl_options = {"REGISTER", "UNDO"}
    role: bpy.props.EnumProperty(name="Role", default="REMOVE", items=[
        ("ADD", "Add Coverage", "Black paint mask combined with Add: paint white where the layer should show"),
        ("REMOVE", "Remove Coverage", "White paint mask combined with Multiply: paint black where the layer should not show")])
    start_painting: bpy.props.BoolProperty(name="Start Painting", default=True,
                                           description="Enter native texture painting on the new mask")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        self.layout.prop(self, "role", expand=True)
        self.layout.label(text={"ADD": "Paint white to add coverage; the generator is kept",
                                "REMOVE": "Paint black to remove coverage; the generator is kept"}[self.role], icon="INFO")
        self.layout.prop(self, "start_painting")

    def run(self, context):
        ts = require_set(context)
        layer = active(ts)
        if layer is None:
            raise ValueError("Select a layer with a mask first.")
        mask = layer.masks.add()
        mask.id, mask.source = new_id(), "PAINT"
        mask.name = "Paint · " + ("Add Coverage" if self.role == "ADD" else "Remove Coverage")
        mask.operation = "ADD" if self.role == "ADD" else "MULTIPLY"
        black = self.role == "ADD"
        mask.image = create(f"{ts.name}_{layer.name}_Paint", int(ts.resolution),
                            (0, 0, 0, 1) if black else (1, 1, 1, 1))
        mask.image.pack()
        mask_id = mask.id
        layer.active_mask = len(layer.masks) - 1
        invalidate(ts)
        from ..graph import mirror
        tree = mirror.find_tree(ts)
        if tree is not None:
            mirror.request_select(tree, mirror.mask_node_id(layer, layer.active_mask, mask))
        from . import selection
        target = selection.select(context, layer.id, selection.MASK, mask_id=mask_id, ts=ts, synchronize=False)
        if self.start_painting:
            from ..painting.paint_session import begin
            try:
                begin(context, ts, context.scene.open_painter.paint_channel, True)
            except ValueError as exc:
                self.report({"INFO"}, f"Paint mask added; painting not started: {exc}")
        context.scene.open_painter.status = f"{mask.name} added above {target.layer.name}'s generators"


MASK_ICONS = {'GROUP': 'FILE_FOLDER', 'GRAPH': 'NODETREE', 'MESH': 'OUTLINER_COLLECTION', 'PAINT': 'BRUSH_DATA',
              'FILTER': 'MODIFIER', 'SCENE': 'WORLD', 'REFERENCE': 'LINKED', 'STROKES': 'GREASEPENCIL',
              'GENERATOR': 'PHYSICS', 'PROCEDURAL': 'TEXTURE', 'BAKED': 'IMAGE_DATA'}


def starter_graph(name):
    """A new Mask Graph (Noise → Ramp → Mask Output), ready to edit."""
    from ..procedural_graph.templates import build
    return build('NOISE_RAMP', name)


class OP_UL_masks(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        from ..core.mask_stack import depth
        level = depth(data.masks, index)
        row = layout.row(align=True)
        if level:
            row.separator(factor=2.0 * level)
        if item.source == "ANCHOR":
            row.label(text="", icon="BLANK1")
            row.prop(item, "name", text="", emboss=False, icon="ANCHOR_CENTER")
            return
        row.prop(item, "enabled", text="")
        row.prop(item, "name", text="", emboss=False, icon=MASK_ICONS.get(item.source, 'MOD_MASK'))
        if item.source not in {'GROUP', 'FILTER'} or index:
            row.label(text='', icon={'MULTIPLY': 'X', 'ADD': 'ADD', 'NORMAL': 'IPO_LINEAR', 'MAX': 'SORT_DESC',
                                     'MIN': 'SORT_ASC'}.get(item.operation, 'DOT'))


def draw_mask_stack(layout, context, ts, layer):
    """The mask stack list with its add, order, group and delete controls."""
    row = layout.row(align=True)
    row.menu("OP_MT_native_masks", text="Add Mask Effect", icon="ADD")
    op = row.operator("open_painter.choose_filter", text="Filter", icon="MODIFIER")
    op.target, op.create = "MASK", True
    layout.template_list("OP_UL_masks", "", layer, "masks", layer, "active_mask", rows=2)
    row = layout.row(align=True)
    for action, icon in (("UP", "TRIA_UP"), ("DOWN", "TRIA_DOWN"), ('INTO_GROUP', 'IMPORT'),
                         ('OUT_OF_GROUP', 'EXPORT'), ("DELETE", "TRASH")):
        row.operator("open_painter.mask", text="", icon=icon).action = action


class OP_PT_masks(bpy.types.Panel):
    bl_order = 2
    bl_label = "Masks"
    bl_parent_id = "OP_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Open Painter"

    def draw(self, context):
        ts = active_set(context)
        layer = active(ts) if ts else None
        layout = self.layout
        if not layer:
            layout.label(text="Select a layer to add a mask.")
            return
        draw_mask_stack(layout, context, ts, layer)
        if not layer.masks:
            return
        mask = layer.masks[min(layer.active_mask, len(layer.masks) - 1)]
        draw_mask_settings(layout, context, ts, layer, mask)


def draw_mask_settings(layout, context, ts, layer, mask):
    """Controls of one mask entry: its source, the source's own settings, then combine and levels."""
    if mask.source == 'GROUP':
        layout.prop(mask, 'source', text='Source')
        from ..core.mask_stack import members
        count = len(members(layer.masks, min(layer.active_mask, len(layer.masks) - 1)))
        layout.label(text=f'{count} member(s) combine first, then the group blends into the stack', icon='INFO')
        layout.operator('open_painter.mask', text='Dissolve Group').action = 'DISSOLVE'
        for name in ('operation', 'strength', 'black', 'white', 'invert'):
            layout.prop(mask, name)
        return
    if mask.source == 'GRAPH':
        layout.prop(mask, 'source', text='Source')
        row = layout.row(align=True)
        row.template_ID(mask, 'node_group')
        row.operator('open_painter.mask', text='', icon='ADD').action = 'NEW_GRAPH'
        from ..procedural_graph import is_native
        if is_native(mask.node_group):
            layout.operator('open_painter.mask_graph_edit', icon='NODETREE').tree = mask.node_group.name
        elif mask.node_group is not None:
            layout.label(text='A Blender shader group from an earlier version: baked on the CPU', icon='INFO')
        for name in ('operation', 'strength', 'black', 'white', 'invert'):
            layout.prop(mask, name)
        return
    layout.operator(
        "open_painter.mask", text="Bake Mask Stack to Texture", icon="IMAGE_DATA"
    ).action = "BAKE"
    layout.prop(mask, "source", text="Source")
    if mask.source == 'ANCHOR':
        from .anchors import draw_anchor_settings
        draw_anchor_settings(layout, ts, mask)
        layout.label(text='Publishes the mask result up to here; changes nothing itself', icon='INFO')
        return
    if mask.source == 'REFERENCE':
        from .anchors import draw_anchor_field, draw_anchor_channel
        anchor = draw_anchor_field(layout, ts, 'REFERENCE_MASK', mask.reference_anchor, text='Anchor')
        if anchor is not None:
            draw_anchor_channel(layout, anchor, mask)
        if not mask.reference_anchor:
            source = next((item for item in ts.layers if item.id == mask.reference_layer),None)
            layout.label(text='Legacy layer source')
            layout.operator('open_painter.reference_source',text=source.name if source else 'Choose Source Layer',icon='LINKED')
            layout.prop(mask,'reference_channel')
            layout.prop(mask,'reference_stage')
        layout.label(text='Source layer input, before stack blending')
    elif mask.source == 'MESH':
        draw_mesh_selector(layout, ts, mask)
    elif mask.source == 'SCENE':
        field = mask.influence
        layout.menu('OP_MT_scene_field_presets', text='Presets', icon='PRESET')
        layout.prop(field, 'kind')
        layout.prop(field, 'mode')
        layout.prop(field, 'resolution')
        kind = field.kind
        if kind in {'DISTANCE', 'CONTACT', 'EXPOSURE', 'PROXIMITY'}:
            layout.prop(field, 'scope')
            if field.scope == 'SCENE':
                layout.prop(field, 'exclude_collection')
                layout.prop(field, 'max_objects')
            else:
                layout.prop(field, 'object')
                layout.prop(field, 'collection')
            layout.label(text='Per-object radius: custom property open_painter_field_radius', icon='INFO')
        elif kind in {'HEIGHT', 'HEAT', 'MOISTURE', 'SOOT'}:
            layout.prop(field, 'object')
        elif kind == 'GROUND':
            layout.prop(field, 'ground_object')
        if kind in {'UP', 'DOWN', 'HEIGHT', 'WORLD_HEIGHT', 'GROUND', 'EXPOSURE', 'SOOT'}:
            layout.prop(field, 'direction')
        if kind in {'HEIGHT', 'WORLD_HEIGHT'}:
            layout.prop(field, 'bounds_m' if field.physical_mapping_version else 'bounds')
        if kind in {'DISTANCE', 'CONTACT', 'PROXIMITY', 'EXPOSURE', 'HEAT', 'MOISTURE', 'SOOT', 'GROUND',
                    'CONVEXITY', 'CONCAVITY', 'EDGE'}:
            layout.prop(field, 'distance_m' if field.physical_mapping_version else 'distance',
                        text=('Reach' if kind in {'CONVEXITY', 'CONCAVITY', 'EDGE'} else 'Distance / Radius') +
                             (' (m)' if field.physical_mapping_version else ''))
        if kind in {'ATTRIBUTE', 'VERTEX_GROUP'}:
            layout.prop(field, 'attribute')
        layout.prop(field, 'falloff')
        row = layout.row(align=True)
        row.operator('open_painter.mask',text='Update Analysis').action = 'ANALYZE'
        row.operator('open_painter.mask',text='Freeze Analysis').action = 'FREEZE'
        layout.label(text='Sampled UV field; authored textures unchanged')
        if field.error:
            layout.label(text=field.error, icon='TIME' if field.error.startswith('Analysing') else 'ERROR')
        from .offline_analysis import draw_action
        if kind == 'CONTACT':
            # Contact-driven wear over time: the offline sampler freezes a duration image into this field.
            box = layout.box()
            box.label(text='Contact over the animation', icon='TIME')
            draw_action(box, 'CONTACT', text='Sample Contact History…', icon='RENDER_ANIMATION')
            box.label(text='Frames in, a frozen contact-duration image on this mask out', icon='INFO')
        elif kind in {'ATTRIBUTE', 'VERTEX_GROUP'}:
            box = layout.box()
            box.label(text='Deformation-driven wear', icon='MOD_SIMPLEDEFORM')
            draw_action(box, 'STRAIN', text='Rest-State Strain…', icon='MOD_SIMPLEDEFORM')
            box.label(text='Writes open_painter_strain on the active mesh; read it as the attribute', icon='INFO')
    elif mask.source == 'TEXT':
        from ..evaluation.text_source import draw
        draw(layout, mask.text)
    elif mask.source == "FILL":
        layout.template_ID(mask, "image")
        for name in ("projection", "filtering", "tiling", "scale", "rotation", "offset"):
            layout.prop(mask, name)
        if mask.projection == "TRIPLANAR":
            layout.prop(mask, "triplanar_hardness")
    elif mask.source == "PAINT":
        layout.template_ID(mask, "image")
        if hasattr(bpy.types, "OP_OT_paint"):
            layout.operator(
                "open_painter.paint", text="Paint Mask", icon="BRUSH_DATA"
            ).mask = True
    elif mask.source == "BAKED":
        layout.prop(mask, "map_type")
    elif mask.source == 'STROKES':
        from ..painting import strokes as stroke_records
        keys = {item.stroke_key: item.name for other in context.scene.open_painter.texture_sets
                for item in other.layers if item.stroke_key}
        if mask.stroke_key:
            count = len(stroke_records.strokes_for(context.scene.open_painter, mask.stroke_key, ts))
            # A key minted for the mask (New Key) is named after the mask itself.
            layout.label(text=f'{count} strokes of "{keys.get(mask.stroke_key, mask.name)}"', icon='CURVE_BEZCURVE')
        else:
            layout.label(text='No strokes linked yet', icon='INFO')
        row = layout.row(align=True)
        row.operator('open_painter.mask_strokes', text="This Layer's Strokes", icon='LINKED').action = 'OWN'
        row.operator('open_painter.mask_strokes', text='New Key', icon='ADD').action = 'NEW'
        row.operator('open_painter.mask_strokes', text='', icon='X').action = 'CLEAR'
        if mask.stroke_key and hasattr(bpy.types, 'OP_OT_vector_brush'):
            from .strokes_panel import draw_strokes
            layout.operator('open_painter.vector_brush', text='Paint Vector Strokes', icon='BRUSH_DATA').mask = True
            draw_strokes(layout, context, ts, layer, mask=mask)
    elif mask.source == "PROCEDURAL":
        op = layout.operator("open_painter.choose_procedural", text=mask.procedural.replace("_", " ").title(), icon="TEXTURE")
        op.target, op.create = "MASK", False
        from .physical_scale import draw_size
        if mask.material_mapping_version:
            layout.prop(mask, 'chip_radius_m' if mask.procedural == 'DOTS' else 'material_period_m')
            layout.prop(mask, 'material_frame')
            if mask.material_frame == 'WORLD':
                for name in ('material_origin', 'material_axis_u', 'material_axis_v'):
                    layout.prop(mask, name)
            from ..authoring.physical_space import resolution_diagnostic
            diagnostic = resolution_diagnostic(ts, mask.material_period_m)
            if diagnostic['status'] == 'UNDER_RESOLVED':
                layout.label(text='Pattern is under-resolved by these UVs', icon='ERROR')
            if mask.procedural == 'DOTS':
                layout.label(text='Circular radius before geometry clipping')
            layout.prop(mask, 'seed')
        else:
            for name in ("projection", "tiling"):
                layout.prop(mask, name)
            draw_size(layout, ts, mask, "noise_scale", "feature_size", "Feature Size")
            for name in ("seed", "scale", "rotation", "offset"):
                layout.prop(mask, name)
        if mask.projection == "TRIPLANAR":
            layout.prop(mask,"triplanar_hardness")
    elif mask.source == "FILTER":
        from .texture_operations import draw_effect_settings
        op = layout.operator("open_painter.choose_filter", text=mask.effect.kind.replace("_", " ").title(), icon="MODIFIER")
        op.target, op.create = "MASK", False
        layout.prop(mask.effect, "enabled", text="Enabled")
        draw_effect_settings(layout, mask.effect, "MASK")
        for name in ("invert", "black", "white", "strength"):
            layout.prop(mask, name)
        return
    elif mask.source == "GENERATOR":
        layout.prop(mask, "generator")
        if mask.generator in {"MATERIAL_ID","OBJECT_ID","UV_ISLAND"}:
            row = layout.row(align=True)
            row.prop(mask,"id_color",text="Selected ID Color")
            row.operator("open_painter.choose_baked_id", text="", icon="VIEWZOOM")
        if mask.generator in {"EDGE_WEAR", "CHIPS"}:
            from ..constants import OWNER

            if mask.edge_source == 'LIVE':
                layout.prop(mask.influence, 'distance_m' if mask.influence.physical_mapping_version else 'distance',
                            text='Live Reach (m)' if mask.influence.physical_mapping_version else 'Live Reach')

            image = next(
                (m.image for m in ts.baked_maps if m.kind == "CURVATURE" and m.image), None
            )
            if (
                image
                and image.get(OWNER)
                and "BakedCurvature" in image.name
                and image.get("open_painter_curvature_method") != "CYCLES_BEVEL"
            ):
                layout.label(text="Pre-Cycles curvature: rebake edge map for the Bevel set.", icon="ERROR")
            from ..baking.maps.curvature import edge_map_set_complete
            radius = ts.bake.mean_curvature_distance
            if edge_map_set_complete(ts):
                layout.prop(mask, "edge_size", text=f"Edge Size (thin {radius * 50:.1f} cm ↔ extreme {radius * 400:.1f} cm)", slider=True)
            else:
                layout.label(text="Rebake the edge maps for a live Edge Size control", icon="INFO")
            layout.prop(mask, "edge_width", text="Edge Width", slider=True)
            box = layout.box()
            box.label(text="Baked edge maps: thin, normal, wide, extreme", icon="IMAGE_DATA")
            box.prop(ts.bake, "mean_curvature_distance", text="Curvature Radius")
            box.operator("open_painter.edge_map_refresh", text="Rebake Edge Map", icon="IMAGE_DATA").set_id = ts.id
        from ..generators.micro_details import KINDS as MICRO_KINDS
        if mask.generator in MICRO_KINDS:
            from .anchors import draw_anchor_field
            box = layout.box()
            box.label(text="Micro Details", icon="ANCHOR_CENTER")
            for target, field, strength, text in (
                    ("DETAIL_HEIGHT", "detail_height_anchor", "detail_height_strength", "Micro Height"),
                    ("DETAIL_NORMAL", "detail_normal_anchor", "detail_normal_strength", "Micro Normal")):
                draw_anchor_field(box, ts, target, getattr(mask, field), text=text)
                if getattr(mask, field):
                    box.prop(mask, strength, slider=True)
            if mask.detail_height_anchor or mask.detail_normal_anchor:
                box.prop(mask, "detail_radius", text="Micro Radius (px)")

    if mask.source in {"GENERATOR", "NOISE"}:
        from .physical_scale import draw_size
        for name in ("amount", "contrast"):
            layout.prop(mask, name)
        draw_size(layout, ts, mask, "noise_scale", "feature_size", "Feature Size")
        for name in ("noise_strength", "seed"):
            layout.prop(mask, name)
    for name in ("operation", "invert", "black", "white", "strength"):
        layout.prop(mask, name)
