import bpy

from ..core.project_manager import active_set, require_set
from .operators import SafeOperator


class OP_OT_bake(SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.bake"
    bl_label = "Bake Selected Mesh Maps"
    bl_description = "Bake mesh information into packed UV textures"
    bl_options = {"REGISTER", "UNDO"}
    action: bpy.props.EnumProperty(
        items=[
            ("BAKE", "Bake", ""),
            ("ADD_HIGH", "Add Selected High Poly", ""),
            ("REMOVE_HIGH", "Remove High Poly", ""),
            ("USE_MAPS", "Use Baked Maps", ""),
            (
                "CURVATURE",
                "Rebake Edge Map",
                "Bake target-mesh geometric creases into UV texture pixels",
            ),
            ('MATCH_SOURCES', 'Match Sources by Name', 'Match assigned _low objects with scene-local _high and _cage partners'),
            ('CREATE_CAGES', 'Create Editable Cages', 'Create owned render-evaluated cage meshes without modifying source objects'),
            ('INSPECT', 'Inspect Bake Setup', 'Check assignments, scale, cages and evaluated UVs for degenerate, outside-tile and exactly overlapping faces; changes nothing'),
            ('SELECT_ISSUES', 'Select Problem Faces', 'Select the faces reported by the last inspection on their base meshes'),
            ('RESELECT_MAP', 'Reselect Baked Faces', 'Restore the face selection recorded by the Selected Faces map'),
        ]
    )
    cage_offset: bpy.props.FloatProperty(name='World-Space Offset', default=.01, min=0, subtype='DISTANCE')

    def invoke(self, context, event):
        if self.action == 'CREATE_CAGES':
            return context.window_manager.invoke_props_dialog(self)
        return self.execute(context)

    def draw(self, context):
        if self.action == 'CREATE_CAGES':
            self.layout.prop(self, 'cage_offset')
            self.layout.label(text='Creates editable copies; originals stay unchanged')

    def run(self, context):
        ts = require_set(context)
        if self.action == 'INSPECT':
            from ..baking import diagnostics
            issues = diagnostics.run(ts)
            worst = {'ERROR': 'ERROR', 'WARNING': 'WARNING'}.get(
                next((i.severity for i in issues if i.severity == 'ERROR'), None)
                or next((i.severity for i in issues if i.severity == 'WARNING'), None), 'INFO')
            self.report({worst}, f'{len(issues)} finding(s)' if issues else 'Bake setup has no findings')
        elif self.action == 'SELECT_ISSUES':
            from ..baking import diagnostics
            issues = diagnostics.last(ts)
            if not any(i.faces for i in issues):
                raise ValueError('Run Inspect Bake Setup first; no faces were reported.')
            counts, skipped = diagnostics.select_faces(ts, issues)
            message = ', '.join(f'{name}: {count} face(s)' for name, count in counts.items()) or 'No selectable faces'
            if skipped:
                message += '; evaluated topology differs on ' + ', '.join(skipped)
            self.report({'INFO'}, message)
        elif self.action == 'RESELECT_MAP':
            import json
            from ..baking.diagnostics import Issue, select_faces
            prop = next((m for m in ts.baked_maps if m.kind == 'SELECTION' and m.image), None)
            if prop is None or not prop.image.get('selected_faces'):
                raise ValueError('Bake the Selected Faces map first.')
            recorded = json.loads(prop.image['selected_faces'])
            issues = [Issue('INFO', 'Recorded selection', name, tuple(faces)) for name, faces in recorded.items()]
            counts, skipped = select_faces(ts, issues)
            message = ', '.join(f'{name}: {count} face(s)' for name, count in counts.items()) or 'No recorded faces'
            if skipped:
                message += '; evaluated topology differs on ' + ', '.join(skipped)
            self.report({'INFO'}, message)
        elif self.action == 'MATCH_SOURCES':
            from ..baking.pairing import match_sources
            parts = match_sources(ts, context.scene)
            self.report({'INFO'}, f'Matched {len(parts)} target(s); {len(ts.bake.high_objects)} high-poly source(s)')
        elif self.action == 'CREATE_CAGES':
            from ..baking.cages import create
            result = create(context, ts, self.cage_offset)
            self.report({'INFO'}, f'Created {len(result)} editable cage(s)')
        elif self.action == "CURVATURE":
            from ..baking.maps.curvature import bake_curvature_set
            from ..baking.bake_manager import register_maps
            from ..core.layer_stack import invalidate

            if not any(ref.object for ref in ts.assignments):
                raise ValueError("Assign the target mesh before baking its edge map.")
            register_maps(ts, bake_curvature_set(ts))
            invalidate(ts)
        elif self.action == "ADD_HIGH":
            for obj in context.selected_objects:
                if obj.type == "MESH" and not any(r.object == obj for r in ts.bake.high_objects):
                    ref = ts.bake.high_objects.add()
                    ref.object = obj
                    ref.name = obj.name
        elif self.action == "USE_MAPS":
            from ..core.layer_stack import add, channel_for

            if not ts.baked_maps:
                raise ValueError("Bake mesh maps first.")
            layer = add(ts, "FILL", "Baked Mesh Maps")
            for channel in layer.channels:
                channel.enabled = False
            for prop in ts.baked_maps:
                if prop.kind in {"NORMAL", "AO"} and prop.image:
                    channel = channel_for(layer, prop.kind)
                    channel.enabled = True
                    channel.image = prop.image
        elif self.action == "REMOVE_HIGH":
            if ts.bake.high_objects:
                ts.bake.high_objects.remove(min(ts.bake.active_high, len(ts.bake.high_objects) - 1))
        else:
            from ..baking.bake_manager import bake_selected

            context.window_manager.progress_begin(0, 1)
            try:
                bake_selected(context, ts)
            finally:
                context.window_manager.progress_end()
            self.report({"INFO"}, "Mesh maps baked. Add them to a fill layer or generator mask.")


class OP_OT_bake_doctor(SafeOperator, bpy.types.Operator):
    bl_idname = 'open_painter.bake_doctor'
    bl_label = 'Bake Doctor'
    bl_options = {'REGISTER', 'UNDO'}
    action: bpy.props.EnumProperty(items=[
        ('CHECK', 'Bake Check', 'Run every exact pre-bake check (the same ones that run before a bake)'),
        ('FULL', 'Full Check', 'Pre-bake checks plus the sampled cage-ray coverage'),
        ('SELECT', 'Select', 'Select the geometry or objects this finding reports'),
        ('SELECT_ALL', 'Select All Findings', 'Select everything the last check reported'),
        ('CLEAR', 'Clear', 'Forget the last report')], default='CHECK')
    index: bpy.props.IntProperty(default=-1)

    @classmethod
    def description(cls, context, properties):
        if properties.action == 'SELECT' and properties.index >= 0:
            from ..baking import doctor
            ts = active_set(context)
            report = doctor.last(ts) if ts else None
            if report and properties.index < len(report.findings):
                finding = report.findings[properties.index]
                return f'Select: {finding.object_name + ": " if finding.object_name else ""}{finding.message}'
        return dict(CHECK='Run every exact pre-bake check', FULL='Pre-bake checks plus sampled cage-ray coverage',
                    SELECT='Select what this finding reports', SELECT_ALL='Select everything the last check reported',
                    CLEAR='Forget the last report')[properties.action]

    def run(self, context):
        from ..baking import doctor
        ts = require_set(context)
        if self.action in {'CHECK', 'FULL'}:
            report = doctor.examine(ts, 'FULL' if self.action == 'FULL' else 'PRE_BAKE')
            errors, warnings = len(report.errors), len(report.warnings)
            level = 'ERROR' if errors else 'WARNING' if warnings else 'INFO'
            text = (f'{errors} error(s), {warnings} warning(s) in {report.elapsed_ms:.0f} ms' if report.findings
                    else f'Bake check passed in {report.elapsed_ms:.0f} ms')
            self.report({level}, text)
            context.scene.open_painter.status = 'Bake check: ' + text
        elif self.action == 'CLEAR':
            doctor.forget(ts)
        else:
            report = doctor.last(ts)
            if report is None:
                raise ValueError('Run Bake Check first')
            if self.action == 'SELECT_ALL':
                message = doctor.select_all(ts, report, context)
            else:
                if not 0 <= self.index < len(report.findings):
                    raise ValueError('That finding is gone; run Bake Check again')
                message = doctor.select(ts, report.findings[self.index], context)
            self.report({'INFO'}, message)
            context.scene.open_painter.status = message
        if context.area is not None:
            context.area.tag_redraw()


STATUS_ICONS = {'PASS': 'CHECKMARK', 'INFO': 'CHECKMARK', 'WARN': 'ERROR', 'FAIL': 'CANCEL', 'SKIP': 'BLANK1'}
SEVERITY_ICONS = {'ERROR': 'CANCEL', 'WARNING': 'ERROR', 'INFO': 'DOT'}


def draw_bake_doctor(layout, context, ts):
    """The Bake Check box: one line per check, then clickable findings."""
    from ..baking import doctor
    box = layout.box()
    row = box.row(align=True)
    row.label(text='Bake Check', icon='VIEWZOOM')
    row.operator('open_painter.bake_doctor', text='Check').action = 'CHECK'
    row.operator('open_painter.bake_doctor', text='', icon='SHADING_RENDERED').action = 'FULL'
    report = doctor.last(ts)
    if report is None:
        box.label(text='Runs before every bake; click a finding to select its geometry', icon='INFO')
        return
    column = box.column(align=True)
    for key, label in doctor.CHECKS:
        status = report.status(key)
        if status == 'SKIP' and key == 'coverage':
            column.label(text=f'{label} (Full Check)', icon='BLANK1')
            continue
        count = sum(1 for f in report.findings if f.check == key and f.severity != 'INFO')
        text = label if status in {'PASS', 'INFO', 'SKIP'} else f'{label} · {count}'
        column.label(text=text, icon=STATUS_ICONS[status])
    if report.findings:
        column = box.column(align=True)
        row = column.row(align=True)
        row.label(text=f'{len(report.findings)} finding(s) · {report.elapsed_ms:.0f} ms')
        if any(f.selectable for f in report.findings):
            row.operator('open_painter.bake_doctor', text='', icon='RESTRICT_SELECT_OFF').action = 'SELECT_ALL'
        for index, finding in enumerate(report.findings[:12]):
            text = finding.message
            if finding.object_name:
                text = f'{finding.object_name}: {text}'
            if finding.count and not finding.objects:
                text = f'{finding.count} · {text}'
            if finding.selectable:
                op = column.operator('open_painter.bake_doctor', text=text[:90], icon=SEVERITY_ICONS[finding.severity],
                                     emboss=False)
                op.action, op.index = 'SELECT', index
            else:
                column.label(text=text[:90], icon=SEVERITY_ICONS[finding.severity])
        if len(report.findings) > 12:
            column.label(text=f'{len(report.findings) - 12} more finding(s)')
    else:
        column.label(text=f'No findings · {report.elapsed_ms:.0f} ms', icon='CHECKMARK')


class OP_UL_high(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(
            text=item.object.name if item.object else f"Missing: {item.name}", icon="MESH_DATA"
        )


class OP_PT_bake(bpy.types.Panel):
    bl_order = 4
    bl_label = "Bake Mesh Maps"
    bl_parent_id = "OP_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Open Painter"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        ts = active_set(context)
        layout = self.layout
        if not ts:
            return
        settings = ts.bake
        draw_bake_doctor(layout, context, ts)
        layout.label(text=f"Low Poly: {len(ts.assignments)} assigned slots")
        layout.prop(settings, "selected_to_active")
        layout.template_list(
            "OP_UL_high", "", settings, "high_objects", settings, "active_high", rows=2
        )
        row = layout.row(align=True)
        row.operator("open_painter.bake", text="Add Selected High Poly").action = "ADD_HIGH"
        row.operator("open_painter.bake", text="", icon="REMOVE").action = "REMOVE_HIGH"
        layout.prop(settings, 'source_mode')
        layout.operator('open_painter.bake', text='Match Sources by Name', icon='LINKED').action = 'MATCH_SOURCES'
        if settings.high_objects:
            ref = settings.high_objects[min(settings.active_high, len(settings.high_objects)-1)]
            layout.prop(ref, 'target')
            if settings.cage_mode == 'CUSTOM' and len({r.object for r in ts.assignments if r.object}) > 1:
                layout.prop(ref, 'cage')
        layout.prop(ts, "resolution")
        for key in ("margin", "cage_mode", "ray_distance", "samples", "device", "supersample"):
            layout.prop(settings, key)
        if settings.cage_mode == 'AUTO':
            layout.prop(settings, 'separate_cage_distance')
            if settings.separate_cage_distance:
                layout.prop(settings, 'cage_extrusion')
        if settings.cage_mode == "CUSTOM":
            if len({ref.object for ref in ts.assignments if ref.object}) == 1:
                layout.prop(settings, "cage_object")
            else:
                layout.label(text='Choose each cage on its high-poly source row')
        layout.operator('open_painter.bake', text='Create Editable Cages', icon='MESH_CUBE').action = 'CREATE_CAGES'
        for key in ("normal", "ao", "curvature"):
            layout.prop(settings, key)
        if settings.ao:
            layout.prop(settings, 'ao_scope')
            layout.prop(settings, 'ao_distance')
        if settings.curvature:
            layout.prop(settings, "curvature_angle")
            layout.prop(settings, "curvature_width")
            layout.prop(settings, "mean_curvature_distance")
            layout.prop(settings, "auto_edge_rebake")
            layout.label(text="Creases plus smooth bevels within the radius; target mesh only.")
            layout.operator(
                "open_painter.bake", text="Rebake Edge Map", icon="IMAGE_DATA"
            ).action = "CURVATURE"
        bevel = layout.column(align=True)
        bevel.label(text="Bevel Maps (Cycles-baked, engine independent)")
        bevel.prop(settings, "bevel_normal")
        bevel.prop(settings, "bevel_mask")
        if settings.bevel_normal or settings.bevel_mask:
            bevel.prop(settings, "bevel_radius")
            bevel.prop(settings, "bevel_samples")
        section = layout.column(align=True)
        section.label(text="Target Mesh Utility Maps")
        for key in ("position", "world_normal", "object_normal", "material_id", "object_id", "uv_island", "uv_border", "thickness", "mean_curvature", "selection"):
            section.prop(settings, key)
        if settings.uv_border:
            section.prop(settings, "uv_border_width")
        if settings.mean_curvature and not settings.curvature:
            section.prop(settings, "mean_curvature_distance")
        if settings.selection:
            section.label(text="Face selection of the assigned meshes; face count must survive modifiers.")
        if settings.thickness:
            section.prop(settings, "thickness_resolution")
            section.prop(settings, "thickness_samples")
            section.prop(settings, "thickness_max_distance")
            section.label(text="Closed surfaces; sampled inward rays.")
        section.label(text="Utility maps use evaluated target UVs.")
        from .bake_corrections import draw_corrections
        draw_corrections(layout, context, ts)
        layout.operator('open_painter.bake_queue', icon='RENDER_STILL')
        layout.label(text='Esc stops between maps; Cycles maps block.')
        layout.operator(
            "open_painter.bake", text="Use Baked Normal + AO in Stack"
        ).action = "USE_MAPS"
        for prop in ts.baked_maps:
            from ..geometry.provenance import status
            layout.label(
                text=f"{prop.kind}: {status(ts, prop)}", icon="IMAGE_DATA"
            )
        if any(prop.kind == 'SELECTION' and prop.image for prop in ts.baked_maps):
            layout.operator('open_painter.bake', text='Reselect Baked Faces', icon='RESTRICT_SELECT_OFF').action = 'RESELECT_MAP'
