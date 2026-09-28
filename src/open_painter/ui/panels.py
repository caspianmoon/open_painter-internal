import bpy

from ..core.project_manager import active_set


class OP_UL_texture_sets(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.prop(item, "name", text="", emboss=False, icon="UV" if item.udim_tile else "MATERIAL")


class OP_UL_assignments(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(
            text=f"{item.object.name if item.object else item.object_name} · slot {item.slot_index + 1}",
            icon="MESH_DATA",
        )


class OP_PT_main(bpy.types.Panel):
    bl_label = "Open Painter"
    bl_idname = "OP_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Open Painter"

    @classmethod
    def poll(cls, context):
        from . import editor
        return not editor.is_area(context.area)

    def draw(self, context):
        layout = self.layout
        if context.workspace.name != "Open Painter":
            layout.operator("open_painter.open_editor", icon="BRUSH_DATA")
        project = context.scene.open_painter
        layout.template_list(
            "OP_UL_texture_sets", "", project, "texture_sets", project, "active_texture_set", rows=2
        )
        row = layout.row(align=True)
        row.operator("open_painter.texture_set", text="New", icon="ADD").action = "ADD"
        row.operator("open_painter.texture_set", text="Copy", icon="DUPLICATE").action = "DUPLICATE"
        row.operator("open_painter.texture_set", text="Remove", icon="REMOVE").action = "DELETE"
        layout.operator("open_painter.texture_set", text="Create UDIM Sets", icon="UV").action = "ADD_UDIM"
        ts = active_set(context)
        if not ts:
            layout.label(text="Create a texture set to begin.")
            return
        if not ts.assignments:
            layout.operator(
                "open_painter.texture_set", text="Assign Selected Mesh Slots", icon="MESH_DATA"
            ).action = "ASSIGN"
        if project.status:
            layout.label(text=project.status, icon="INFO")


class OP_PT_texture_set(bpy.types.Panel):
    bl_label = "Texture Set"
    bl_parent_id = "OP_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Open Painter"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 3

    def draw(self, context):
        layout = self.layout
        project = context.scene.open_painter
        ts = active_set(context)
        if not ts:
            return
        addon = context.preferences.addons.get(__package__.rsplit(".", 1)[0])
        if addon and addon.preferences.developer_mode:
            layout.label(text=f"ID: {ts.id}")
            from ..evaluation import shader_preview
            info = shader_preview.diagnostics(context.scene, ts, details=False)
            box = layout.box()
            box.label(text=f"Shader: {info['state']} · builds {info['graph_builds']} · updates {info['update_calls']}")
            if info.get('units'):
                box.label(text=f"Units {info['units']} · nodes {info.get('shader_nodes')} · largest {info.get('largest_unit')}")
                box.label(text=f"Last build {info.get('build_ms', 0):.0f} ms · reused {info.get('reused_units', 0)} · rebuilt {info.get('rebuilt_units', 0)}")
                limit = info.get('max_textures_frag')
                box.label(text=f"Unique images {info.get('unique_images')} / {limit if limit else '?'} fragment samplers")
            if info.get('rebuild_scope'):
                box.label(text=f"Rebuild pending: {info['rebuild_scope']}"[:96])
            box.label(text=f"Frozen {info.get('frozen', False)} · deferred owners {info.get('deferred_owners', 0)}")
            from ..evaluation import profile
            rows = [row for row in profile.rows(context.scene, ts) if row['profiled'] or row['cost_ms']]
            if rows:
                total = sum(row['cost_ms'] for row in rows)
                box.label(text=f"Layer profile · {total:.0f} ms total")
                for row in rows:
                    box.label(text=f"{row['name'][:22]} · {row['cost_ms']:.1f} ms · {row['state'].lower()}"[:96])
        layout.prop(ts, "name", text="Name")
        if ts.udim_tile:
            layout.label(text=f"UDIM {ts.udim_tile} · {ts.udim_name}", icon="UV")
            layout.prop(ts, 'udim_name', text='Export Name')
            op = layout.operator('open_painter.texture_set', text='Make Independent' if ts.shared_stack_id else 'Link Matching Tile Stacks')
            op.action = 'UNLINK_UDIM' if ts.shared_stack_id else 'LINK_UDIM'
        layout.prop(ts, "resolution")
        from .strokes_panel import pending_resample
        pending = pending_resample(ts)
        if pending:
            row = layout.row(align=True)
            row.label(text=f"{pending} paint image{'s' if pending != 1 else ''} at another size, sampled bilinearly", icon="INFO")
            layout.operator("open_painter.resample_paint_images", icon="IMAGE_DATA")
        layout.prop(ts, "preview_ao")
        layout.prop(ts, "height_distance")
        row = layout.row(align=True)
        row.operator(
            "open_painter.texture_set", text="Refresh", icon="FILE_REFRESH"
        ).action = "REFRESH"
        row.operator("open_painter.texture_set", text="Repair Material").action = "REPAIR"
        layout.template_list(
            "OP_UL_assignments", "", ts, "assignments", ts, "active_assignment", rows=2
        )
        row = layout.row(align=True)
        row.operator("open_painter.texture_set", text="Assign Selected Slots").action = "ASSIGN"
        row.operator("open_painter.texture_set", text="Remove Slot").action = "UNASSIGN"
        layout.operator(
            "open_painter.texture_set", text="Validate Project", icon="CHECKMARK"
        ).action = "VALIDATE"
        if project.status:
            layout.label(text=project.status)
