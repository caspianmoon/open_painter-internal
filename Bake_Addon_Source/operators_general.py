# --- START OF FILE operators_general.py ---

# --- START OF FILE operators_general.py ---

import blf
import bpy
import bmesh
import math
from collections import deque # For Surface Select
from bpy.types import Operator
from bpy.props import BoolProperty, IntProperty, StringProperty, FloatProperty # Added FloatProperty

# Import helper functions from utils.py
from . import shared_state, utils

HUD_OFFSET_X = 76
HUD_OFFSET_Y = 38


def _set_blf_size(font_id, size):
    try:
        blf.size(font_id, size)
    except TypeError:
        blf.size(font_id, size, bpy.context.preferences.system.dpi or 72)


def _draw_shadowed_text(font_id, x, y, text, size, color=(1.0, 1.0, 1.0, 1.0)):
    _set_blf_size(font_id, size)
    blf.color(font_id, 0.0, 0.0, 0.0, 0.6)
    blf.position(font_id, x + 1, y - 1, 0)
    blf.draw(font_id, text)
    blf.color(font_id, *color)
    blf.position(font_id, x, y, 0)
    blf.draw(font_id, text)


def _draw_angle_hud(title, angle_degrees, footer):
    font_id = 0
    x = 15 + HUD_OFFSET_X
    y = HUD_OFFSET_Y
    _draw_shadowed_text(font_id, x, y + 54, title, 16, (1.0, 1.0, 1.0, 1.0))
    _draw_shadowed_text(font_id, x, y + 32, f"Threshold: {angle_degrees:.1f} deg", 14, (0.82, 0.95, 1.0, 1.0))
    _draw_shadowed_text(font_id, x, y + 12, footer, 12, (0.78, 0.78, 0.78, 1.0))


def _register_angle_draw_handle(callback, args):
    handle = bpy.types.SpaceView3D.draw_handler_add(callback, args, 'WINDOW', 'POST_PIXEL')
    shared_state.angle_hud_draw_handles.append(handle)
    return handle


def _remove_angle_draw_handle(handle):
    if not handle:
        return

    try:
        bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
    except ValueError:
        pass

    if handle in shared_state.angle_hud_draw_handles:
        shared_state.angle_hud_draw_handles.remove(handle)

# --- Modifier Operators ---
# (AddMirrorModifier, AddBevelModifier, AddSubDModifier, AddDecimateModifierLinked remain unchanged)
class AddMirrorModifier(Operator):
    bl_idname = "object.add_mirror_modifier"; bl_label = "Add Mirror"; bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Add a Mirror Modifier (X-Axis, Bisect, Clip) to active object if missing"
    report_if_exists: BoolProperty(default=False, options={'SKIP_SAVE'})
    @classmethod
    def poll(cls, context): return (context.mode == 'OBJECT' or context.mode == 'EDIT_MESH') and context.object and context.object.type == 'MESH'
    def execute(self, context):
        obj = context.object
        if any(m.type == 'MIRROR' for m in obj.modifiers):
            if self.report_if_exists: self.report({'WARNING'}, "Mirror modifier already exists on active object.")
            return {'CANCELLED'}
        mod = obj.modifiers.new(name="Mirror", type='MIRROR')
        mod.use_axis = (True, False, False); mod.use_bisect_axis[0] = True; mod.use_clip = True
        self.report({'INFO'}, "Added Mirror Modifier to active"); return {'FINISHED'}

class AddBevelModifier(Operator):
    bl_idname = "object.add_bevel_modifier"; bl_label = "Add Bevel"; bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Add a Bevel Modifier (Weight, Segments=1) to active object if missing" # <-- Updated description
    report_if_exists: BoolProperty(default=False, options={'SKIP_SAVE'})
    @classmethod
    def poll(cls, context): return (context.mode == 'OBJECT' or context.mode == 'EDIT_MESH') and context.object and context.object.type == 'MESH'
    def execute(self, context):
        obj = context.object
        if any(m.type == 'BEVEL' for m in obj.modifiers):
            if self.report_if_exists: self.report({'WARNING'}, "Bevel modifier already exists on active object.")
            return {'CANCELLED'}
        mod = obj.modifiers.new(name="Bevel", type='BEVEL')
        # --- MODIFIED LINE ---
        mod.segments = 1 # Changed from 2 to 1
        # --- END MODIFIED LINE ---
        mod.limit_method = 'WEIGHT'; mod.affect = 'EDGES'; mod.profile = 0.5; mod.width = 0.02
        self.report({'INFO'}, "Added Bevel Modifier (Segments=1) to active"); return {'FINISHED'} # <-- Updated report

class AddSubDModifier(Operator):
    bl_idname = "object.add_subd_modifier"; bl_label = "Add SubD"; bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Add a Subdivision Surface Modifier (Levels=2) to active object if missing"
    report_if_exists: BoolProperty(default=False, options={'SKIP_SAVE'})
    @classmethod
    def poll(cls, context): return (context.mode == 'OBJECT' or context.mode == 'EDIT_MESH') and context.object and context.object.type == 'MESH'
    def execute(self, context):
        obj = context.object
        if any(m.type == 'SUBSURF' for m in obj.modifiers):
            if self.report_if_exists: self.report({'WARNING'}, "Subdivision modifier already exists on active object.")
            return {'CANCELLED'}
        mod = obj.modifiers.new(name="Subdivision", type='SUBSURF')
        mod.levels = 2; mod.render_levels = 2; mod.quality = 3
        mod_index = len(obj.modifiers) - 1
        if mod_index > 0:
            target_index = mod_index
            for i, m in enumerate(obj.modifiers):
                if m.type in ('BEVEL', 'MIRROR'): target_index = i; break
            if target_index < mod_index:
                 with context.temp_override(object=obj, modifier=mod): bpy.ops.object.modifier_move_to_index(index=target_index)
        self.report({'INFO'}, "Added Subdivision Modifier to active"); return {'FINISHED'}

class AddDecimateModifierLinked(Operator):
    bl_idname = "object.add_decimate_modifier_linked"; bl_label = "Add Decimate"; bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Adds Decimate (Un-Sub, It=2) to selected/linked objects if missing"
    report_if_exists: BoolProperty(default=False, options={'SKIP_SAVE'})
    @classmethod
    def poll(cls, context): return (context.mode == 'OBJECT' or context.mode == 'EDIT_MESH') and context.selected_objects and any(o.type == 'MESH' for o in context.selected_objects)
    def execute(self, context):
        initial_selection_meshes = [o for o in context.selected_objects if o.type == 'MESH']
        if not initial_selection_meshes: self.report({'WARNING'}, "No mesh objects initially selected."); return {'CANCELLED'}
        selected_mesh_data = {obj.data for obj in initial_selection_meshes if obj.data}
        if not selected_mesh_data: self.report({'WARNING'}, "Selected mesh objects have no mesh data?"); return {'CANCELLED'}
        objects_to_check = {obj for obj in context.scene.objects if obj.type == 'MESH' and obj.data in selected_mesh_data}
        added_count, processed_count, already_exist_count = 0, 0, 0
        for obj in objects_to_check:
            processed_count += 1
            has_decimate = any(m.type == 'DECIMATE' for m in obj.modifiers)
            if not has_decimate:
                try:
                    mod = obj.modifiers.new(name="Decimate", type='DECIMATE')
                    mod.decimate_type = 'UNSUBDIV'; mod.iterations = 2; added_count += 1
                except Exception as e: print(f"Error adding Decimate modifier to '{obj.name}': {e}"); self.report({'ERROR'}, f"Failed to add Decimate to {obj.name}")
            else: already_exist_count +=1
        if added_count > 0: self.report({'INFO'}, f"Added Decimate modifier to {added_count} selected/linked object(s).")
        elif already_exist_count == processed_count and self.report_if_exists: self.report({'INFO'}, f"Checked {processed_count} selected/linked object(s). Decimate modifier already present.")
        elif already_exist_count < processed_count and added_count == 0: self.report({'INFO'}, f"Checked {processed_count} selected/linked object(s). No new Decimate modifiers added.")
        return {'FINISHED'}

# --- Control Operators ---
class ChangeSubDSettings(Operator):
    bl_idname = "object.change_subd_settings"; bl_label = "Change SubD Level (Selected + Linked)"; bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Increase/decrease SubD levels for selected meshes and linked-data objects"
    level_delta: IntProperty(name="Level Change", default=0)
    @classmethod
    def poll(cls, context): return (context.mode == 'OBJECT' or context.mode == 'EDIT_MESH') and context.selected_objects and any(o.type == 'MESH' for o in context.selected_objects)
    def execute(self, context):
        initial_selection_meshes = [o for o in context.selected_objects if o.type == 'MESH']
        if not initial_selection_meshes: self.report({'WARNING'}, "No mesh objects initially selected."); return {'CANCELLED'}
        if self.level_delta == 0: self.report({'INFO'}, "No SubD level change requested."); return {'FINISHED'}
        selected_mesh_data = {obj.data for obj in initial_selection_meshes if obj.data}
        if not selected_mesh_data: self.report({'WARNING'}, "Selected mesh objects have no mesh data?"); return {'CANCELLED'}
        objects_to_modify = {obj for obj in context.scene.objects if obj.type == 'MESH' and obj.data in selected_mesh_data}
        count, changed = 0, False; action = f"level by {self.level_delta:+d}"
        for obj in objects_to_modify:
            smod = utils.get_first_modifier_of_type(obj, 'SUBSURF')
            if smod:
                obj_changed = False; n_lvl = max(0, smod.levels + self.level_delta); n_ren = max(0, smod.render_levels + self.level_delta)
                if smod.levels != n_lvl: smod.levels = n_lvl; obj_changed = True
                if smod.render_levels != n_ren: smod.render_levels = n_ren; obj_changed = True
                if obj_changed: count += 1; changed = True
        if changed: self.report({'INFO'}, f"Changed SubD {action} for {count} object(s)")
        else: self.report({'INFO'}, "No SubD modifiers found or levels already at limit on selected/linked objects.")
        return {'FINISHED'}

class SyncSubDSettings(Operator):
    bl_idname = "object.sync_subd_settings"; bl_label = "Sync SubD Levels (to Active)"; bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Sync SubD levels of selected/linked objects to match the active object"
    @classmethod
    def poll(cls, context):
        if context.mode != 'OBJECT' and context.mode != 'EDIT_MESH': return False
        active_obj = context.object
        if not active_obj or active_obj.type != 'MESH': return False
        if not utils.get_first_modifier_of_type(active_obj, 'SUBSURF'): return False
        if not context.selected_objects or not any(o.type == 'MESH' for o in context.selected_objects): return False
        return True
    def execute(self, context):
        active_obj = context.object
        if not active_obj or active_obj.type != 'MESH': self.report({'ERROR'}, "Active object is not a mesh."); return {'CANCELLED'}
        source_subd = utils.get_first_modifier_of_type(active_obj, 'SUBSURF')
        if not source_subd: self.report({'ERROR'}, "Active object has no Subdivision modifier."); return {'CANCELLED'}
        target_level, target_render_level = source_subd.levels, source_subd.render_levels
        initial_selection_meshes = [o for o in context.selected_objects if o.type == 'MESH']
        if not initial_selection_meshes: self.report({'WARNING'}, "No mesh objects selected (excluding active?)."); return {'CANCELLED'}
        selected_mesh_data = {obj.data for obj in initial_selection_meshes if obj.data}
        if not selected_mesh_data: self.report({'WARNING'}, "Selected mesh objects have no mesh data?"); return {'CANCELLED'}
        objects_to_modify = {obj for obj in context.scene.objects if obj.type == 'MESH' and obj.data in selected_mesh_data}
        count, processed_count = 0, 0
        for obj in objects_to_modify:
            target_subd = utils.get_first_modifier_of_type(obj, 'SUBSURF')
            if target_subd:
                processed_count += 1; obj_changed = False
                if target_subd.levels != target_level: target_subd.levels = target_level; obj_changed = True
                if target_subd.render_levels != target_render_level: target_subd.render_levels = target_render_level; obj_changed = True
                if obj_changed: count += 1
        if count > 0: self.report({'INFO'}, f"Synced SubD levels to {target_level} (View/Render) for {count} object(s).")
        elif processed_count > 0: self.report({'INFO'}, f"SubD levels already synced to {target_level} for {processed_count} object(s).")
        else: self.report({'INFO'}, "No target objects with SubD modifiers found to sync.")
        return {'FINISHED'}

class ToggleBooleanModifiers(Operator):
    bl_idname = "object.toggle_boolean_modifiers"; bl_label = "Toggle Booleans (Selected + Linked)"; bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Toggle ALL Boolean modifier visibility for selected meshes + linked objects; Hide/Unhide linked-only objects"
    @classmethod
    def poll(cls, context): return (context.mode == 'OBJECT' or context.mode == 'EDIT_MESH') and context.selected_objects and any(o.type == 'MESH' for o in context.selected_objects)
    def execute(self, context):
        initial_selection_names = {o.name for o in context.selected_objects if o.type == 'MESH'}
        if not initial_selection_names: self.report({'WARNING'}, "No mesh objects initially selected."); return {'CANCELLED'}
        initial_selection_meshes = [o for o in context.scene.objects if o.name in initial_selection_names and o.type == 'MESH']
        if not initial_selection_meshes: self.report({'WARNING'}, "Could not find initially selected mesh objects in scene."); return {'CANCELLED'}
        target_state, selected_mesh_data, first_obj_with_bools = None, set(), None
        for obj in initial_selection_meshes:
            if obj.data: selected_mesh_data.add(obj.data)
            if first_obj_with_bools is None:
                bool_mods = utils.get_boolean_modifiers(obj)
                if bool_mods: first_obj_with_bools = obj; target_state = not bool_mods[0].show_viewport
        if first_obj_with_bools is None: self.report({'INFO'}, "No Boolean modifiers found on initially selected mesh object(s)."); return {'FINISHED'}
        if not selected_mesh_data: self.report({'WARNING'}, "Selected mesh objects have no mesh data?"); return {'CANCELLED'}
        objects_to_modify = {obj for obj in context.scene.objects if obj.type == 'MESH' and obj.data in selected_mesh_data}
        proc_bool_cnt, chg_bool_cnt, vis_chg_cnt = 0, 0, 0
        act_bool = "Enabled" if target_state else "Disabled"; act_vis = "Shown" if target_state else "Hidden"; tgt_hide = not target_state
        for obj in objects_to_modify:
            obj_name = obj.name; bool_mods = utils.get_boolean_modifiers(obj)
            if bool_mods:
                proc_bool_cnt += 1; obj_bool_chg = False
                for mod in bool_mods:
                    if mod.show_viewport != target_state: mod.show_viewport = target_state; obj_bool_chg = True
                if obj_bool_chg: chg_bool_cnt += 1
            if obj_name not in initial_selection_names:
                vis_needs_chg = False
                if obj.hide_viewport != tgt_hide: obj.hide_viewport = tgt_hide; vis_needs_chg = True
                if obj.hide_render != tgt_hide: obj.hide_render = tgt_hide; vis_needs_chg=True
                if vis_needs_chg: vis_chg_cnt += 1
        report_parts = []
        if chg_bool_cnt > 0: report_parts.append(f"{act_bool} Booleans on {chg_bool_cnt} object(s)")
        elif proc_bool_cnt > 0: report_parts.append(f"Booleans already {act_bool.lower()} on {proc_bool_cnt} object(s)")
        if vis_chg_cnt > 0: report_parts.append(f"{act_vis} {vis_chg_cnt} linked-only object(s)")
        if report_parts: self.report({'INFO'}, ". ".join(report_parts))
        elif proc_bool_cnt == 0: self.report({'INFO'}, "No Boolean modifiers found on selected or linked objects.")
        return {'FINISHED'}

class ToggleSubDBevelModifiers(Operator):
    bl_idname = "object.toggle_subd_bevel_modifiers"; bl_label = "Toggle SubD & Bevel"; bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Toggle SubD/Bevel visibility for selected meshes + linked objects"
    @classmethod
    def poll(cls, context): return (context.mode == 'OBJECT' or context.mode == 'EDIT_MESH') and context.selected_objects and any(o.type == 'MESH' for o in context.selected_objects)
    def execute(self, context):
        initial_selection_meshes = [o for o in context.selected_objects if o.type == 'MESH']
        if not initial_selection_meshes: self.report({'WARNING'}, "No mesh objects initially selected."); return {'CANCELLED'}
        target_state, first_obj_with_mod, selected_mesh_data = None, None, set()
        for obj in initial_selection_meshes:
            if obj.data: selected_mesh_data.add(obj.data)
            if first_obj_with_mod is None:
                mod = utils.get_first_modifier_of_type(obj, 'SUBSURF')
                if not mod: mod = utils.get_first_modifier_of_type(obj, 'BEVEL')
                if mod: first_obj_with_mod = obj; target_state = not mod.show_viewport
        if first_obj_with_mod is None: self.report({'INFO'}, "No SubD or Bevel modifiers found on initially selected mesh object(s)."); return {'FINISHED'}
        if not selected_mesh_data: self.report({'WARNING'}, "Selected mesh objects have no mesh data?"); return {'CANCELLED'}
        objects_to_modify = {obj for obj in context.scene.objects if obj.type == 'MESH' and obj.data in selected_mesh_data}
        changed_count, processed_count = 0, 0; action = "Enabled" if target_state else "Disabled"
        for obj in objects_to_modify:
            obj_changed = False; mods_to_toggle = [m for m in obj.modifiers if m.type in ('SUBSURF', 'BEVEL')]
            if mods_to_toggle:
                processed_count += 1
                for mod in mods_to_toggle:
                    if mod.show_viewport != target_state: mod.show_viewport = target_state; obj_changed = True
                if obj_changed: changed_count += 1
        if changed_count > 0: self.report({'INFO'}, f"{action} SubD/Bevel modifiers on {changed_count} object(s).")
        elif processed_count > 0: self.report({'INFO'}, f"SubD/Bevel modifiers already {action.lower()} on {processed_count} object(s).")
        else: self.report({'INFO'}, "No SubD or Bevel modifiers found on selected or linked objects.")
        return {'FINISHED'}

class ToggleDecimateModifiers(Operator):
    bl_idname = "object.toggle_decimate_modifiers"; bl_label = "Toggle Decimate"; bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Toggle Decimate visibility for selected meshes + linked objects"
    @classmethod
    def poll(cls, context): return (context.mode == 'OBJECT' or context.mode == 'EDIT_MESH') and context.selected_objects and any(o.type == 'MESH' for o in context.selected_objects)
    def execute(self, context):
        initial_selection_meshes = [o for o in context.selected_objects if o.type == 'MESH']
        if not initial_selection_meshes: self.report({'WARNING'}, "No mesh objects initially selected."); return {'CANCELLED'}
        target_state, first_obj_with_mod, selected_mesh_data = None, None, set()
        for obj in initial_selection_meshes:
            if obj.data: selected_mesh_data.add(obj.data)
            if first_obj_with_mod is None:
                mod = utils.get_first_modifier_of_type(obj, 'DECIMATE')
                if mod: first_obj_with_mod = obj; target_state = not mod.show_viewport
        if first_obj_with_mod is None: self.report({'INFO'}, "No Decimate modifiers found on initially selected mesh object(s)."); return {'FINISHED'}
        if not selected_mesh_data: self.report({'WARNING'}, "Selected mesh objects have no mesh data?"); return {'CANCELLED'}
        objects_to_modify = {obj for obj in context.scene.objects if obj.type == 'MESH' and obj.data in selected_mesh_data}
        changed_count, processed_count = 0, 0; action = "Enabled" if target_state else "Disabled"
        for obj in objects_to_modify:
            obj_changed = False; mods_to_toggle = [m for m in obj.modifiers if m.type == 'DECIMATE']
            if mods_to_toggle:
                processed_count += 1
                for mod in mods_to_toggle:
                    if mod.show_viewport != target_state: mod.show_viewport = target_state; obj_changed = True
                if obj_changed: changed_count += 1
        if changed_count > 0: self.report({'INFO'}, f"{action} Decimate modifiers on {changed_count} object(s).")
        elif processed_count > 0: self.report({'INFO'}, f"Decimate modifiers already {action.lower()} on {processed_count} object(s).")
        else: self.report({'INFO'}, "No Decimate modifiers found on selected or linked objects.")
        return {'FINISHED'}

class ToggleLastBooleanModifier(Operator):
    bl_idname = "object.toggle_last_boolean_modifier"; bl_label = "Toggle Last Boolean"; bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Toggle LAST Boolean visibility for selected/linked meshes (if operand matches active's last boolean)"
    @classmethod
    def poll(cls, context): return (context.mode == 'OBJECT' or context.mode == 'EDIT_MESH') and context.selected_objects and any(o.type == 'MESH' for o in context.selected_objects)
    def execute(self, context):
        initial_selection_meshes = [o for o in context.selected_objects if o.type == 'MESH']
        if not initial_selection_meshes: self.report({'WARNING'}, "No mesh objects initially selected."); return {'CANCELLED'}
        active_obj = context.active_object
        reference_obj = active_obj if (active_obj and active_obj.type == 'MESH' and active_obj in initial_selection_meshes) else (initial_selection_meshes[0] if initial_selection_meshes else None)
        if not reference_obj: self.report({'ERROR'}, "Could not determine reference object."); return {'CANCELLED'}
        ref_last_bool = utils.get_last_modifier_of_type(reference_obj, 'BOOLEAN')
        if not ref_last_bool: self.report({'INFO'}, f"Reference object '{reference_obj.name}' has no Boolean modifiers."); return {'FINISHED'}
        reference_operand = ref_last_bool.object; target_state = not ref_last_bool.show_viewport; action = "Enabled" if target_state else "Disabled"
        selected_mesh_data = {obj.data for obj in initial_selection_meshes if obj.data}
        if not selected_mesh_data: self.report({'WARNING'}, "Selected mesh objects have no mesh data?"); return {'CANCELLED'}
        objects_to_modify = {obj for obj in context.scene.objects if obj.type == 'MESH' and obj.data in selected_mesh_data}
        changed_count, processed_count = 0, 0
        for obj in objects_to_modify:
            last_bool = utils.get_last_modifier_of_type(obj, 'BOOLEAN')
            if last_bool:
                processed_count += 1
                if last_bool.object == reference_operand:
                    if last_bool.show_viewport != target_state: last_bool.show_viewport = target_state; changed_count += 1
        operand_name = f"'{reference_operand.name}'" if reference_operand else "'None'"
        if changed_count > 0: self.report({'INFO'}, f"{action} last Boolean (operand: {operand_name}) on {changed_count} object(s).")
        elif processed_count > 0: self.report({'INFO'}, f"Last Boolean (operand: {operand_name}) already {action.lower()} or operand mismatch on {processed_count} object(s).")
        else: self.report({'INFO'}, "No matching last Boolean modifiers found on selected or linked objects.")
        return {'FINISHED'}

# --- Selection Operators ---
class OBJECT_OT_select_factor_loop(Operator):
    bl_idname = "object.select_factor_loop"
    bl_label = "Select Connected by Angle Factor"
    bl_options = {'REGISTER', 'UNDO', 'GRAB_CURSOR', 'BLOCKING'}
    
    # Fixed sensitivity property
    sensitivity: FloatProperty(
        name="Mouse Sensitivity",
        default=0.001,
        options={'HIDDEN'}  # Hide from UI
    )
    
    # Internal state variables
    obj: bpy.types.Object = None
    bm: bmesh.types.BMesh = None
    initial_threshold: float = 0.167  # Changed from 0.5 to 0.167 (30 degrees)
    _handle_draw_2d = None
    initial_selection: set = None
    _ignore_initial_left_release: bool = False
    
    @classmethod
    def poll(cls, context):
        return (context.mode == 'EDIT_MESH' and 
                context.active_object is not None and 
                context.active_object.type == 'MESH')
    
    def modal(self, context, event):
        if event.type == 'MOUSEMOVE':
            # Calculate mouse movement delta
            delta_x = event.mouse_x - event.mouse_prev_x
            
            # Update threshold based on movement
            delta_threshold = delta_x * self.sensitivity
            new_threshold = max(0.0, min(1.0, self.initial_threshold + delta_threshold))
            
            # Update initial threshold for next movement
            self.initial_threshold = new_threshold
            
            # Re-run selection with new threshold
            self.execute_selection(context, new_threshold)
            
        elif event.type == 'LEFTMOUSE' and event.value == 'RELEASE' and self._ignore_initial_left_release:
            self._ignore_initial_left_release = False
            return {'RUNNING_MODAL'}

        elif event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            self.finish(context)
            if context.area:
                context.area.header_text_set(None)
            return {'FINISHED'}

        elif event.type == 'RIGHTMOUSE' and event.value == 'PRESS':
            self.restore_initial_selection(context)
            self.finish(context)
            if context.area:
                context.area.header_text_set(None)
            return {'CANCELLED'}

        elif event.type in {'ESC'}:
            self.restore_initial_selection(context)
            self.finish(context)
            if context.area:
                context.area.header_text_set(None)
            return {'CANCELLED'}
            
        return {'RUNNING_MODAL'}
    
    def invoke(self, context, event):
        # Store initial values
        self.initial_threshold = 0.167  # Start with 30 degrees (0.167 * 180 = 30)
        self._ignore_initial_left_release = event.type == 'LEFTMOUSE' and event.value == 'PRESS'
        
        # Get the active object and BMesh
        self.obj = context.active_object
        try:
            self.bm = bmesh.from_edit_mesh(self.obj.data)
        except ValueError:
            self.report({'ERROR'}, "Could not get BMesh from edit mesh")
            return {'CANCELLED'}
            
        # Store initial selection
        self.initial_selection = {e for e in self.bm.edges if e.select and e.is_valid}
        if not self.initial_selection:
            self.report({'WARNING'}, "No valid edges selected as starting point.")
            return {'CANCELLED'}
            
        # Add draw handler for visual feedback
        if self._handle_draw_2d is None:
            self._handle_draw_2d = _register_angle_draw_handle(self.draw_callback_2d, ())
            
        # Set header text
        context.area.header_text_set("Easy Loop: Move Mouse L/R=Threshold | LMB=Apply | RMB/ESC=Cancel")
        
        # Start modal operation
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}
    
    def finish(self, context):
        # Remove draw handler
        if self._handle_draw_2d:
            _remove_angle_draw_handle(self._handle_draw_2d)
            self._handle_draw_2d = None
            
        # Force redraw to remove any remaining visual feedback
        if context.area:
            context.area.tag_redraw()

    def restore_initial_selection(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH' or context.mode != 'EDIT_MESH' or not self.initial_selection:
            return

        try:
            bm = bmesh.from_edit_mesh(obj.data)
            bm.edges.ensure_lookup_table()
            for edge in bm.edges:
                edge.select_set(False)
            for edge in self.initial_selection:
                if edge.is_valid:
                    edge.select_set(True)
            bmesh.update_edit_mesh(obj.data)
        except Exception as exc:
            print(f"Easy Loop: failed to restore initial selection: {exc}")
    
    def draw_callback_2d(self):
        if not hasattr(self, 'initial_threshold'):
            return

        _draw_angle_hud(
            "Easy Loop",
            self.initial_threshold * 180.0,
            "LMB Apply | RMB/Esc Cancel | Mouse L/R Adjust",
        )
    
    def execute_selection(self, context, threshold):
        obj = context.active_object
        mesh = obj.data
        try:
            bm = bmesh.from_edit_mesh(mesh)
        except ValueError:
            self.report({'ERROR'}, "Could not get BMesh from edit mesh (is it valid?)")
            return {'CANCELLED'}
            
        max_degrees = 180.0
        angle_limit_degrees = threshold * max_degrees
        
        def extend_selection(start_edge, start_vert, limit_degrees, selected_in_run):
            current_edge = start_edge
            if not current_edge.is_valid or len(current_edge.verts) != 2:
                return
            v1, v2 = current_edge.verts
            if not v1.is_valid or not v2.is_valid:
                return
            far_vert = current_edge.other_vert(start_vert)
            near_vert = start_vert
            if not far_vert or not near_vert:
                return
                
            while True:
                candidates = []
                if not far_vert.is_valid:
                    break
                linked_edges = [e for e in far_vert.link_edges if e.is_valid and e != current_edge]
                if not linked_edges:
                    break
                if not near_vert.is_valid or (far_vert.co - near_vert.co).length_squared < 1e-12:
                    break
                    
                vec_current = (far_vert.co - near_vert.co).normalized()
                for edge in linked_edges:
                    if edge in selected_in_run:
                        continue
                    next_vert = edge.other_vert(far_vert)
                    if not next_vert or not next_vert.is_valid or (next_vert.co - far_vert.co).length_squared < 1e-12:
                        continue
                        
                    vec_candidate = (next_vert.co - far_vert.co).normalized()
                    dot_product = max(-1.0, min(1.0, vec_current.dot(vec_candidate)))
                    try:
                        angle_rad = math.acos(dot_product)
                        angle_deg = math.degrees(angle_rad)
                    except ValueError:
                        continue
                    if angle_deg <= limit_degrees:
                        candidates.append((angle_deg, edge, next_vert))
                        
                if not candidates:
                    break
                    
                candidates.sort(key=lambda x: x[0])
                best_angle_deg, best_edge, next_far_vert = candidates[0]
                best_edge.select_set(True)
                selected_in_run.add(best_edge)
                near_vert = far_vert
                far_vert = next_far_vert
                current_edge = best_edge
        
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        
        # Clear previous selection
        for edge in bm.edges:
            edge.select_set(False)
            
        # Use stored initial selection
        if not self.initial_selection:
            self.report({'WARNING'}, "No valid edges selected as starting point.")
            bmesh.update_edit_mesh(mesh)
            return {'CANCELLED'}
            
        # First select the initial edges
        for edge in self.initial_selection:
            if edge.is_valid:
                edge.select_set(True)
            
        globally_selected_edges = set(self.initial_selection)
        edges_processed_count = 0
        
        for start_edge in list(self.initial_selection):
            if not start_edge.is_valid:
                continue
            if len(start_edge.verts) == 2 and start_edge.verts[0].is_valid and start_edge.verts[1].is_valid:
                v1, v2 = start_edge.verts
                if (v1.co - v2.co).length_squared > 1e-12:
                    extend_selection(start_edge, v1, angle_limit_degrees, globally_selected_edges)
                    extend_selection(start_edge, v2, angle_limit_degrees, globally_selected_edges)
                    edges_processed_count += 1
                else:
                    print(f"Factor Select Warning: Skipping degenerate edge {start_edge.index}.")
            else:
                print(f"Factor Select Warning: Skipping invalid edge {start_edge.index} (verts: {len(start_edge.verts)}).")
                
        bmesh.update_edit_mesh(mesh)
        return {'FINISHED'}
    
    def execute(self, context):
        return self.execute_selection(context, 0.167)  # Default to 30 degrees if called directly

# Helper functions for Surface Select real-time updates
def _capture_surface_seed(bm):
    active_face = bm.faces.active
    return {
        "active_face": active_face.index if active_face and active_face.is_valid and active_face.select else None,
        "verts": {v.index for v in bm.verts if v.select and v.is_valid},
        "edges": {e.index for e in bm.edges if e.select and e.is_valid},
        "faces": {f.index for f in bm.faces if f.select and f.is_valid},
    }


def _update_surface_selection(context, angle_degrees, seed=None):
    obj = context.active_object
    if not obj or obj.type != 'MESH' or context.mode != 'EDIT_MESH' or not obj.data:
        return

    try:
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()

        selected_verts = set(seed.get("verts", set())) if seed else {v.index for v in bm.verts if v.select}
        selected_edges = set(seed.get("edges", set())) if seed else {e.index for e in bm.edges if e.select}
        selected_faces = set(seed.get("faces", set())) if seed else {f.index for f in bm.faces if f.select}
        active_face_index = seed.get("active_face") if seed else None

        # Get the current angle threshold
        limit_radians = math.radians(angle_degrees)

        # Get the active face if one is selected, otherwise find any selected face
        start_faces = []
        if active_face_index is not None and 0 <= active_face_index < len(bm.faces):
            start_faces = [bm.faces[active_face_index]]
        elif selected_faces:
            start_faces = [bm.faces[idx] for idx in selected_faces if 0 <= idx < len(bm.faces)]
        elif selected_edges:
            start_faces = list(set(
                f for e_idx in selected_edges if 0 <= e_idx < len(bm.edges)
                for f in bm.edges[e_idx].link_faces
            ))
        elif selected_verts:
            start_faces = list(set(
                f for v_idx in selected_verts if 0 <= v_idx < len(bm.verts)
                for f in bm.verts[v_idx].link_faces
            ))

        if not start_faces:
            return

        selected_faces_set = set(f.index for f in start_faces)
        faces_to_check = deque(start_faces)

        # Deselect all faces initially to ensure only the new selection is active
        for f in bm.faces: f.select = False

        # Select the starting faces
        for f in start_faces:
            f.select = True

        # Perform propagation
        while faces_to_check:
            current_face = faces_to_check.popleft()

            for edge in current_face.edges:
                if len(edge.link_faces) == 2:
                    other_face = edge.link_faces[0] if edge.link_faces[0] != current_face else edge.link_faces[1]

                    if other_face.index not in selected_faces_set:
                        angle = math.acos(max(-1.0, min(1.0, current_face.normal.dot(other_face.normal))))

                        if angle <= limit_radians:
                            other_face.select = True
                            selected_faces_set.add(other_face.index)
                            faces_to_check.append(other_face)

        # Update the mesh with the new selection
        bmesh.update_edit_mesh(obj.data)

    except Exception as e:
        print(f"Error in _update_surface_selection: {e}")


# Surface Select uses the shared angle HUD while preserving the public bl_idname.
class MESH_OT_surface_select(Operator):
    bl_idname = "mesh.surface_select"
    bl_label = "Select Surface"
    bl_options = {'REGISTER', 'UNDO', 'GRAB_CURSOR', 'BLOCKING'}

    initial_angle: FloatProperty(
        name="Angle",
        default=30.0,
        min=0.0,
        max=180.0,
        subtype='ANGLE',
        unit='NONE'
    )
    sensitivity: FloatProperty(
        name="Mouse Sensitivity",
        default=0.2,
        options={'HIDDEN'}
    )
    _handle_draw_2d = None
    initial_seed = None
    _ignore_initial_left_release = False

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'MESH' and context.mode == 'EDIT_MESH' and obj.data

    def invoke(self, context, event):
        obj = context.active_object
        if not self.poll(context):
            return {'CANCELLED'}

        self._ignore_initial_left_release = event.type == 'LEFTMOUSE' and event.value == 'PRESS'
        self.initial_angle = 30.0

        try:
            bm = bmesh.from_edit_mesh(obj.data)
            bm.verts.ensure_lookup_table()
            bm.edges.ensure_lookup_table()
            bm.faces.ensure_lookup_table()
            self.initial_seed = _capture_surface_seed(bm)
        except Exception:
            self.initial_seed = None

        _update_surface_selection(context, self.initial_angle, self.initial_seed)

        if self._handle_draw_2d is None:
            self._handle_draw_2d = _register_angle_draw_handle(self.draw_callback_2d, ())

        context.window_manager.modal_handler_add(self)

        if context.area:
            context.area.header_text_set("Select Surface: Move Mouse L/R=Angle | LMB=Apply | RMB/ESC=Cancel")

        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'MOUSEMOVE':
            delta_x = event.mouse_x - event.mouse_prev_x
            new_angle = max(0.0, min(180.0, self.initial_angle + delta_x * self.sensitivity))

            _update_surface_selection(context, new_angle, self.initial_seed)
            self.initial_angle = new_angle

            if context.area:
                context.area.header_text_set(f"Select Surface: Angle: {new_angle:.1f} deg | LMB=Apply | RMB/ESC=Cancel")
                context.area.tag_redraw()

            return {'RUNNING_MODAL'}

        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE' and self._ignore_initial_left_release:
            self._ignore_initial_left_release = False
            return {'RUNNING_MODAL'}

        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            return self.finish(context)

        if event.type == 'ESC' or (event.type == 'RIGHTMOUSE' and event.value == 'PRESS'):
            return self.finish(context, cancelled=True)

        return {'PASS_THROUGH'}

    def finish(self, context, cancelled=False):
        if context.area:
            context.area.header_text_set(None)
        if self._handle_draw_2d:
            _remove_angle_draw_handle(self._handle_draw_2d)
            self._handle_draw_2d = None
        if context.area:
            context.area.tag_redraw()
        return {'CANCELLED'} if cancelled else {'FINISHED'}

    def draw_callback_2d(self):
        _draw_angle_hud(
            "Surface Select",
            self.initial_angle,
            "LMB Apply | RMB/Esc Cancel | Mouse L/R Adjust",
        )

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH' or context.mode != 'EDIT_MESH' or not obj.data:
            return {'CANCELLED'}

        _update_surface_selection(context, 30.0)
        return {'FINISHED'}


# --- Operator for Copying Email ---
class BAKEADDON_OT_copy_email(Operator): # <-- Corrected Class Name
    """Copies the contact email address to the clipboard"""
    bl_idname = "bakeaddon.copy_email" # <-- Corrected ID
    bl_label = "Copy Email"
    bl_options = {'REGISTER', 'INTERNAL'}

    email_to_copy: StringProperty(default="")

    def execute(self, context):
        context.window_manager.clipboard = self.email_to_copy
        self.report({'INFO'}, f"Email copied to clipboard: {self.email_to_copy}")
        return {'FINISHED'}


# --- Bevel Weight and Crease Operators ---

def _poll_edit_mesh_selection(context):
    """Common poll function for edit mode operators requiring selection."""
    obj = context.active_object
    if not (obj and obj.type == 'MESH' and context.mode == 'EDIT_MESH'):
        return False
    # Check for selection using BMesh
    try:
        bm = bmesh.from_edit_mesh(obj.data)
        has_selection = any(v.select for v in bm.verts) or \
                        any(e.select for e in bm.edges) or \
                        any(f.select for f in bm.faces)
        # No need to update_edit_mesh if we only read selection
        # bm.free() # Not needed with from_edit_mesh context manager
        return has_selection
    except ValueError:
        return False # Cannot get BMesh

class BAKEADDON_OT_apply_bevel_weight(Operator):
    """Applies Bevel Weight to selected edges using the value from Scene properties"""
    bl_idname = "bakeaddon.apply_bevel_weight"
    bl_label = "Bevel Weight"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        # Use common poll function and check for properties
        return _poll_edit_mesh_selection(context) and hasattr(context.scene, 'edge_data_props')

    def execute(self, context):
        obj = context.active_object
        edge_props = context.scene.edge_data_props
        target_bevel_weight = edge_props.bevel_weight_value # Read value from scene props

        try:
            bm = bmesh.from_edit_mesh(obj.data)
        except ValueError:
            self.report({'ERROR'}, "Could not get BMesh.")
            return {'CANCELLED'}

        bw_layer = bm.edges.layers.float.get("bevel_weight_edge") or bm.edges.layers.float.new("bevel_weight_edge")
        # Apply to selected edges only
        selected_edges = [e for e in bm.edges if e.select]

        if not selected_edges:
            self.report({'WARNING'}, "No edges selected.")
            bmesh.update_edit_mesh(obj.data) # Ensure BMesh is freed
            return {'CANCELLED'}

        count = 0
        for edge in selected_edges:
            edge[bw_layer] = target_bevel_weight
            count += 1

        bmesh.update_edit_mesh(obj.data)
        self.report({'INFO'}, f"Set Bevel Weight to {target_bevel_weight:.2f} for {count} edge(s).")
        return {'FINISHED'}

class BAKEADDON_OT_reset_bevel_weight(Operator):
    """Resets Bevel Weight to 0 for selected edges"""
    bl_idname = "bakeaddon.reset_bevel_weight"
    bl_label = "X"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll_edit_mesh_selection(context) # Use common poll

    def execute(self, context):
        # No need to re-check selection here, poll handles it
        try:
            # Use BMesh to set value to 0 for precision, operator can be less reliable
            obj = context.active_object
            bm = bmesh.from_edit_mesh(obj.data)
            bw_layer = bm.edges.layers.float.get("bevel_weight_edge")
            if bw_layer: # Only act if the layer exists
                selected_edges = [e for e in bm.edges if e.select]
                count = 0
                for edge in selected_edges:
                    if edge[bw_layer] != 0.0:
                        edge[bw_layer] = 0.0
                        count += 1
                bmesh.update_edit_mesh(obj.data)
                if count > 0:
                    self.report({'INFO'}, f"Reset Bevel Weight for {count} selected edge(s).")
                else:
                    self.report({'INFO'}, "Selected edges already have 0 Bevel Weight.")
            else:
                bmesh.update_edit_mesh(obj.data) # Free bmesh
                self.report({'INFO'}, "No Bevel Weight data found on mesh.")

            # Fallback or alternative using operator (less precise for setting exactly 0?)
            # bpy.ops.transform.edge_bevelweight(value=-1.0)
            # self.report({'INFO'}, "Reset Bevel Weight for selected edges.")
        except Exception as e:
            self.report({'ERROR'}, f"Failed to reset Bevel Weight: {e}")
            # Ensure BMesh is freed in case of error during BMesh operations
            if 'bm' in locals() and bm.is_valid:
                try: bmesh.update_edit_mesh(obj.data)
                except: pass
            return {'CANCELLED'}
        return {'FINISHED'}

class BAKEADDON_OT_apply_crease(Operator):
    """Applies Crease to selected edges using the value from Scene properties"""
    bl_idname = "bakeaddon.apply_crease"
    bl_label = "Crease"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        # Use common poll function and check for properties
        return _poll_edit_mesh_selection(context) and hasattr(context.scene, 'edge_data_props')

    def execute(self, context):
        obj = context.active_object
        edge_props = context.scene.edge_data_props
        target_crease = edge_props.crease_value # Read value from scene props

        try:
            bm = bmesh.from_edit_mesh(obj.data)
        except ValueError:
            self.report({'ERROR'}, "Could not get BMesh.")
            return {'CANCELLED'}

        crease_layer = bm.edges.layers.float.get("crease_edge") or bm.edges.layers.float.new("crease_edge") # Correct layer name is crease_edge
        # Apply to selected edges only
        selected_edges = [e for e in bm.edges if e.select]

        if not selected_edges:
            self.report({'WARNING'}, "No edges selected.")
            bmesh.update_edit_mesh(obj.data) # Ensure BMesh is freed
            return {'CANCELLED'}

        count = 0
        for edge in selected_edges:
            edge[crease_layer] = target_crease # Directly set the value
            count += 1

        bmesh.update_edit_mesh(obj.data)
        self.report({'INFO'}, f"Set Crease to {target_crease:.2f} for {count} edge(s).")
        return {'FINISHED'}

class BAKEADDON_OT_reset_crease(Operator):
    """Resets Crease to 0 for selected edges"""
    bl_idname = "bakeaddon.reset_crease"
    bl_label = "X"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _poll_edit_mesh_selection(context) # Use common poll

    def execute(self, context):
        # No need to re-check selection here, poll handles it
        try:
            # Use BMesh to set value to 0 for precision
            obj = context.active_object
            bm = bmesh.from_edit_mesh(obj.data)
            crease_layer = bm.edges.layers.float.get("crease_edge")
            if crease_layer: # Only act if the layer exists
                selected_edges = [e for e in bm.edges if e.select]
                count = 0
                for edge in selected_edges:
                    if edge[crease_layer] != 0.0:
                        edge[crease_layer] = 0.0
                        count += 1
                bmesh.update_edit_mesh(obj.data)
                if count > 0:
                    self.report({'INFO'}, f"Reset Crease for {count} selected edge(s).")
                else:
                    self.report({'INFO'}, "Selected edges already have 0 Crease.")
            else:
                bmesh.update_edit_mesh(obj.data) # Free bmesh
                self.report({'INFO'}, "No Crease data found on mesh.")

            # Fallback or alternative using operator
            # bpy.ops.transform.edge_crease(value=-1.0) # Value of -1 sets crease to 0
            # self.report({'INFO'}, "Reset Crease for selected edges.")
        except Exception as e:
            self.report({'ERROR'}, f"Failed to reset Crease: {e}")
            # Ensure BMesh is freed in case of error during BMesh operations
            if 'bm' in locals() and bm.is_valid:
                try: bmesh.update_edit_mesh(obj.data)
                except: pass
            return {'CANCELLED'}
        return {'FINISHED'}

# --- END OF FILE operators_general.py ---
