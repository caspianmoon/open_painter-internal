# --- START OF FILE operators_matrix_bake.py --- [MODIFIED]

# gready/operators_matrix_bake.py

import bpy
import bmesh
import random
import math
import re
from mathutils import Vector, Matrix

# Import necessary components from other G-Ready 2.0 modules
from . import utils # For get_collection, move_object_to_collection, etc.
from .operators_baking import BAKER_OT_Bake # To call the bake operator

# --- Helper Functions ---
# [ ... keep existing helper functions: get_object_world_bbox_corners, get_world_bbox_min_max, bboxes_overlap ... ]
def get_object_world_bbox_corners(obj):
    """Calculates the 8 corners of an object's world-space bounding box."""
    if not obj or not hasattr(obj, 'bound_box'):
        return None
    local_corners = [Vector(corner) for corner in obj.bound_box]
    world_matrix = obj.matrix_world
    world_corners = [world_matrix @ corner for corner in local_corners]
    return world_corners

def get_world_bbox_min_max(world_corners):
    """Calculates the min and max vectors of a world-space bounding box from its corners."""
    if not world_corners or len(world_corners) != 8:
        return None, None
    min_vec = Vector((min(c.x for c in world_corners),
                      min(c.y for c in world_corners),
                      min(c.z for c in world_corners)))
    max_vec = Vector((max(c.x for c in world_corners),
                      max(c.y for c in world_corners),
                      max(c.z for c in world_corners)))
    return min_vec, max_vec

def bboxes_overlap(min1, max1, min2, max2, margin=0.1):
    """Checks if two AABB bounding boxes overlap, with an optional margin."""
    overlap_x = (min1.x < max2.x + margin) and (max1.x > min2.x - margin)
    overlap_y = (min1.y < max2.y + margin) and (max1.y > min2.y - margin)
    overlap_z = (min1.z < max2.z + margin) and (max1.z > min2.z - margin)
    return overlap_x and overlap_y and overlap_z

# --- Matrix Bake Operator ---

class MATRIXBAKE_OT_matrix_bake(bpy.types.Operator):
    """Duplicates low/high/cage OR selected meshes, distributes them in a non-overlapping matrix, converts, joins, bakes, and optionally cleans up."""
    bl_idname = "matrixbake.matrix_bake"
    bl_label = "Matrix Bake"
    bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Distributes duplicates (low/high/cage or selected) in a matrix, joins them, bakes, and cleans up"

    # Property defined in __init__.py on Scene, accessed via context.scene

    @classmethod
    def poll(cls, context):
        return any(o.type == 'MESH' for o in context.selected_objects)

    # ------------------------------------------------------------------
    # Helper utilities for building matrix bake entries
    # ------------------------------------------------------------------

    def _find_scene_counterpart(self, base_name, suffix):
        """Find an object in the scene with name pattern base_suffix(.###)."""
        direct_name = f"{base_name}_{suffix}"
        direct_obj = bpy.data.objects.get(direct_name)
        if direct_obj and direct_obj.type == 'MESH':
            return direct_obj

        pattern = re.compile(rf"^{re.escape(base_name)}_{suffix}(?:\.\d+)?$")
        for obj in bpy.data.objects:
            if obj.type == 'MESH' and pattern.match(obj.name):
                return obj
        return None

    def _base_name_from_unsuffixed_object(self, obj):
        """Return the logical bake base for a mesh without a role suffix."""
        return obj.name.split('.')[0]

    def _ensure_unique_name(self, base_name):
        """Generate a unique object name based on base_name."""
        if base_name not in bpy.data.objects:
            return base_name
        index = 1
        new_name = f"{base_name}_{index}"
        while new_name in bpy.data.objects:
            index += 1
            new_name = f"{base_name}_{index}"
        return new_name

    def _duplicate_for_matrix(self, context, source_obj, base_tag, role):
        """Duplicate an object for matrix processing with unique naming."""
        dup = source_obj.copy()
        if source_obj.data:
            dup.data = source_obj.data.copy()
        dup.animation_data_clear()
        dup.name = self._ensure_unique_name(f"temp_{base_tag}_{role}")

        if source_obj.users_collection:
            source_obj.users_collection[0].objects.link(dup)
        else:
            context.scene.collection.objects.link(dup)

        dup.location = source_obj.location
        dup.rotation_euler = source_obj.rotation_euler
        dup.scale = source_obj.scale
        return dup

    def _create_fallback_cage_from_low(self, context, low_obj, base_tag, scene):
        if not low_obj or low_obj.type != 'MESH' or not low_obj.data:
            return None

        cage = low_obj.copy()
        cage.data = low_obj.data.copy()
        cage.animation_data_clear()
        cage.name = self._ensure_unique_name(f"temp_{base_tag}_cage")
        if cage.data.materials:
            cage.data.materials.clear()

        if low_obj.users_collection:
            low_obj.users_collection[0].objects.link(cage)
        else:
            context.scene.collection.objects.link(cage)

        cage.location = low_obj.location
        cage.rotation_euler = low_obj.rotation_euler
        cage.scale = low_obj.scale
        cage.display_type = 'SOLID'
        cage.color = (0.0, 1.0, 0.0, 0.5)
        cage.show_transparent = True
        cage.hide_render = True

        offset_cm = min(abs(getattr(scene, "baker_cage_offset", 0.1)), 0.1)
        if offset_cm <= 0.0:
            offset_cm = 0.1
        offset_m = offset_cm / 100.0

        bm = bmesh.new()
        try:
            bm.from_mesh(cage.data)
            bm.normal_update()
            for vert in bm.verts:
                vert.co += vert.normal * offset_m
            bm.to_mesh(cage.data)
        finally:
            bm.free()

        cage.data.update()
        cage.update_tag()
        self.report({'INFO'}, f"Created temporary fallback cage for '{base_tag}' with {offset_cm:.3f} cm offset.")
        return cage

    def _collect_entries(self, context, selected_meshes, bake_selected_only=False):
        """Build processing entries from the current selection."""
        entries = []
        warnings = []

        low_pattern = re.compile(r"^(.*)_low(?:\.\d+)?$")
        high_pattern = re.compile(r"^(.*)_high(?:\.\d+)?$")
        cage_pattern = re.compile(r"^(.*)_cage(?:\.\d+)?$")

        selected_lows = []
        selected_no_suffix = []
        selected_highs = {}
        selected_cages = {}

        for obj in selected_meshes:
            name = obj.name
            low_match = low_pattern.match(name)
            if low_match:
                selected_lows.append((low_match.group(1), obj))
                continue

            high_match = high_pattern.match(name)
            if high_match:
                base = high_match.group(1)
                selected_highs.setdefault(base, []).append(obj)
                continue

            cage_match = cage_pattern.match(name)
            if cage_match:
                base = cage_match.group(1)
                selected_cages.setdefault(base, []).append(obj)
                continue

            selected_no_suffix.append(obj)

        base_counts = {}

        def next_unique_base(base_label):
            count = base_counts.get(base_label, 0) + 1
            base_counts[base_label] = count
            return base_label if count == 1 else f"{base_label}_{count}"

        # Build entries for selected _low meshes
        for base, low_obj in selected_lows:
            entry_base = next_unique_base(base)
            high_obj = self._find_scene_counterpart(base, 'high')
            if base in selected_highs and selected_highs[base]:
                high_obj = selected_highs[base][0]

            cage_obj = self._find_scene_counterpart(base, 'cage')
            if base in selected_cages and selected_cages[base]:
                cage_obj = selected_cages[base][0]

            entries.append({
                "base_name": entry_base,
                "source_base": base,
                "low_source": low_obj,
                "high_source": high_obj,
                "cage_source": cage_obj,
                "has_low_suffix": True,
                "from_no_suffix": False
            })

        explicit_low_bases = {base for base, _low_obj in selected_lows}
        if not bake_selected_only:
            for obj in bpy.data.objects:
                if obj.type != 'MESH':
                    continue
                low_match = low_pattern.match(obj.name)
                if low_match:
                    explicit_low_bases.add(low_match.group(1))

        # Build entries for meshes without _low suffix
        for obj in selected_no_suffix:
            base = self._base_name_from_unsuffixed_object(obj)
            entry_base = next_unique_base(base)

            if base in explicit_low_bases:
                high_obj = None
                cage_obj = None
            else:
                high_obj = self._find_scene_counterpart(base, 'high')
                cage_obj = self._find_scene_counterpart(base, 'cage')

            entries.append({
                "base_name": entry_base,
                "source_base": base,
                "low_source": obj,
                "high_source": high_obj,
                "cage_source": cage_obj,
                "has_low_suffix": False,
                "from_no_suffix": True
            })

        handled_bases = {entry["source_base"] for entry in entries if entry.get("has_low_suffix")}
        for base, highs in selected_highs.items():
            if base not in handled_bases:
                warnings.append(f"Ignoring selected high object(s) for base '{base}' because no matching _low selection was found.")

        for base, cages in selected_cages.items():
            if base not in handled_bases:
                warnings.append(f"Ignoring selected cage object(s) for base '{base}' because no matching _low selection was found.")

        has_low_suffix = any(entry["has_low_suffix"] for entry in entries)
        return entries, has_low_suffix, warnings

    def execute(self, context):
        # Check if in Local view mode
        for area in (context.screen.areas if context.screen else ()):
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        if space.local_view:
                            self.report({'ERROR'}, "Cannot bake in Local View mode! Press '/' to exit Local View before baking.")
                            return {'CANCELLED'}
        
        scene = context.scene

        selected_meshes = [o for o in context.selected_objects if o.type == 'MESH']
        if not selected_meshes:
            self.report({'ERROR'}, "Select at least one mesh object before running Matrix Bake.")
            return {'CANCELLED'}

        bake_selected_only = bool(getattr(scene, "baker_matrix_bake_selected", False))
        if bake_selected_only:
            self.report({'INFO'}, "Bake Selected enabled: Matrix Bake will use only selected mesh roots and matching _high/_cage partners.")

        entries, has_low_suffix, warnings = self._collect_entries(context, selected_meshes, bake_selected_only)
        for warning_msg in warnings:
            self.report({'WARNING'}, warning_msg)

        if not entries:
            self.report({'ERROR'}, "No valid mesh entries found for Matrix Bake. Select meshes with '_low' suffix or regular meshes.")
            return {'CANCELLED'}

        # Determine if any selected object has a _high counterpart in the scene
        any_high_present = any(e.get("high_source") and e["high_source"].name in bpy.data.objects for e in entries)

        # Toggle Blender's Selected to Active mode based on presence of any _high counterparts
        bake_settings = scene.render.bake
        original_use_selected_to_active = bake_settings.use_selected_to_active
        try:
            bake_settings.use_selected_to_active = True if any_high_present else False
            self.report({'INFO'}, f"Selected to Active set to {'ON' if bake_settings.use_selected_to_active else 'OFF'} (any _high present: {any_high_present}).")
        except Exception as e:
            self.report({'WARNING'}, f"Could not set 'Selected to Active': {e}")

        use_high_to_low = True  # Always use HIGH_TO_LOW mode (operator logic depends on this mode)
        bake_mode = 'HIGH_TO_LOW'

        # --- 0. Preparation ---
        self.report({'INFO'}, f"Starting Matrix Bake process (Mode: {bake_mode})...")
        original_active = context.active_object
        original_selection = context.selected_objects[:]
        original_mode = context.mode
        if original_mode != 'OBJECT':
            try: bpy.ops.object.mode_set(mode='OBJECT')
            except RuntimeError:
                self.report({'ERROR'}, "Could not switch to Object Mode.")
                return {'CANCELLED'}

        # --- Variables ---
        combined_low, combined_high, combined_cage = None, None, None
        bake_triggered = False
        original_render_visibility = {}
        backup_collections_names = []
        backed_up_objects_by_collection = {}
        collections_created_by_bake = []  # Track which collections were created, so we can clean them up later

        # ==============================================================
        # --- HIGH_TO_LOW Mode Path ---
        # ==============================================================
        if bake_mode == 'HIGH_TO_LOW':
            self.report({'INFO'}, "Executing HIGH_TO_LOW path...")

            # Define collection names
            low_col_name = "Low"
            high_col_name = "High"
            cage_col_name = "Cage"
            backup_collections_names = [f"{low_col_name}Backup", f"{high_col_name}Backup", f"{cage_col_name}Backup"]

            # Check which collections existed BEFORE bake (to avoid deleting user's existing collections)
            existing_collections = {low_col_name, high_col_name, cage_col_name}
            for col_name in list(existing_collections):
                if col_name not in bpy.data.collections:
                    existing_collections.discard(col_name)

            # Get or create primary collections
            low_col = utils.get_collection(low_col_name)
            high_col = utils.get_collection(high_col_name) if any_high_present else None
            cage_col = utils.get_collection(cage_col_name) if any_high_present and scene.render.bake.use_cage else None

            # Track which collections were created by Matrix Bake (for later cleanup)
            if low_col_name not in existing_collections:
                collections_created_by_bake.append(low_col_name)
            if any_high_present:
                if high_col_name not in existing_collections:
                    collections_created_by_bake.append(high_col_name)
                if scene.render.bake.use_cage and cage_col_name not in existing_collections:
                    collections_created_by_bake.append(cage_col_name)

            # --- 1. Backup Existing Collections ---
            self.report({'INFO'}, "Backing up existing Low/High/Cage collections...")
            objects_moved_to_backup = 0
            for col_name in [low_col_name, high_col_name, cage_col_name]:
                col = bpy.data.collections.get(col_name)
                if col and col.objects:
                    backup_col_name = f"{col_name}Backup"
                    backup_col = utils.get_collection(backup_col_name)
                    backed_up_objects_by_collection.setdefault(col_name, [])
                    objects_to_move = list(col.objects)
                    for obj in objects_to_move:
                        try:
                            if backup_col not in obj.users_collection or len(obj.users_collection) > 1:
                                utils.move_object_to_collection(obj, backup_col)
                                backed_up_objects_by_collection[col_name].append(obj)
                                objects_moved_to_backup += 1
                        except Exception as e:
                            self.report({'WARNING'}, f"Could not move '{obj.name}' to '{backup_col_name}': {e}")
            if objects_moved_to_backup > 0:
                self.report({'INFO'}, f"Moved {objects_moved_to_backup} objects to backup collections.")
            else:
                self.report({'INFO'}, "No objects found in Low/High/Cage to back up.")

            # --- 2. Build trio data from selected entries ---
            temp_trio_list = []
            for entry in entries:
                low_src = entry.get("low_source")
                if not low_src:
                    self.report({'WARNING'}, f"Skipping entry '{entry.get('base_name', 'unknown')}' because no low source mesh was found.")
                    continue

                base_tag = entry.get("base_name", low_src.name)
                temp_low = self._duplicate_for_matrix(context, low_src, base_tag, "low")

                temp_high = None
                if any_high_present:
                    high_src = entry.get("high_source")
                    if high_src and high_src.name in bpy.data.objects:
                        temp_high = self._duplicate_for_matrix(context, high_src, base_tag, "high")
                    else:
                        # Maintain current behavior for mixed sets: synthesize a high from low when at least one real _high exists
                        temp_high = self._duplicate_for_matrix(context, low_src, base_tag, "high")
                        self.report({'INFO'}, f"No '_high' mesh found for '{base_tag}'. Using duplicated low mesh as the high counterpart.")

                temp_cage = None
                # Only consider cages when doing Selected to Active workflow (requires _high)
                if any_high_present and scene.render.bake.use_cage:
                    cage_src = entry.get("cage_source")
                    if cage_src and cage_src.name in bpy.data.objects:
                        temp_cage = self._duplicate_for_matrix(context, cage_src, base_tag, "cage")
                    else:
                        self.report({'INFO'}, f"No '_cage' mesh found for '{base_tag}'.")

                temp_trio_list.append({
                    "base_name": base_tag,
                    "low": temp_low,
                    "high": temp_high,
                    "cage": temp_cage
                })

            if not temp_trio_list:
                self.report({'ERROR'}, "Could not prepare any temporary low/high trios from the current selection.")
                return {'CANCELLED'}

            # Compute scenario classification for progress display
            low_only = not any_high_present
            missing_high_original = any(
                (not entry.get("high_source"))
                for entry in entries
                if entry.get("low_source")
            )
            hybrid = any_high_present and missing_high_original
            scenario_hint = 'Low only' if low_only else ('Hybrid' if hybrid else 'High to Low')
            scene['_gready_scenario_hint'] = scenario_hint
            self.report({'INFO'}, f"Matrix Bake Scenario: {scenario_hint}")

            # --- 4. Matrix Distribution (Trios) ---
            # Check if baking AO - if so, keep meshes at their current location instead of spreading
            is_pureplay_ao = (
                getattr(scene, "baker_export_preset", 'CUSTOM') == 'PUREPLAY_ORD'
                and getattr(scene, "baker_pureplay_bake_ao", True)
            )
            is_ao_bake = scene.bake_map_type == 'AO' or is_pureplay_ao
            if is_ao_bake:
                self.report({'INFO'}, "Ambient Occlusion output detected: Keeping all trios at their current positions...")
                # Don't move objects - they stay at their duplicated (current) positions
                self.report({'INFO'}, "All trios positioned at their current locations.")
            else:
                self.report({'INFO'}, "Distributing temporary trios in a matrix...")
                placed_bboxes = []
                # --- MODIFIED SPACING ---
                max_placement_attempts = 100; current_pos = Vector((0.0, 0.0, 0.0)); max_coord_range = 10.0; spacing_factor = 15.0 # Increased spacing_factor from 1.5
                # --- END MODIFIED SPACING ---
                for i, trio_dict in enumerate(temp_trio_list):
                    temp_low = trio_dict.get("low"); temp_high = trio_dict.get("high"); temp_cage = trio_dict.get("cage")
                    if not temp_low: self.report({'WARNING'}, f"Skipping placement for trio '{trio_dict.get('base_name')}' - missing temp_low."); continue
                    placed = False
                    for attempt in range(max_placement_attempts):
                        original_loc = temp_low.location.copy(); temp_low.location = current_pos; context.view_layer.update()
                        world_corners = get_object_world_bbox_corners(temp_low)
                        if not world_corners: self.report({'WARNING'}, f"Could not get bbox for '{temp_low.name}'. Skipping placement."); temp_low.location = original_loc; break
                        bbox_min, bbox_max = get_world_bbox_min_max(world_corners); bbox_size = bbox_max - bbox_min
                        overlap = any(bboxes_overlap(bbox_min, bbox_max, p_min, p_max, margin=0.1) for p_min, p_max in placed_bboxes)
                        if not overlap:
                            temp_low.location = current_pos;
                            if temp_high: temp_high.location = current_pos
                            if temp_cage: temp_cage.location = current_pos
                            placed_bboxes.append((bbox_min, bbox_max)); placed = True
                            current_pos.x += max(bbox_size.x, 1.0) * spacing_factor
                            if i > 0 and i % math.ceil(math.sqrt(len(temp_trio_list))) == 0: current_pos.x = 0.0; current_pos.y += max(bbox_size.y, 1.0) * spacing_factor
                            break
                        else: current_pos = Vector((random.uniform(-max_coord_range, max_coord_range), random.uniform(-max_coord_range, max_coord_range), random.uniform(-max_coord_range, max_coord_range)));
                        if attempt > max_placement_attempts // 2: max_coord_range *= 1.1
                    if not placed: self.report({'ERROR'}, f"Failed to place trio '{trio_dict.get('base_name')}' after {max_placement_attempts} attempts. Aborting."); return {'CANCELLED'}
                self.report({'INFO'}, "Matrix distribution complete.")

            # --- 5. Convert to Mesh (Trios) ---
            self.report({'INFO'}, "Converting temporary objects to mesh data...")
            bpy.ops.object.select_all(action='DESELECT')
            temp_objects_to_convert = []
            for trio_dict in temp_trio_list:
                if trio_dict.get("low"): temp_objects_to_convert.append(trio_dict["low"])
                if trio_dict.get("high"): temp_objects_to_convert.append(trio_dict["high"])
                if trio_dict.get("cage"): temp_objects_to_convert.append(trio_dict["cage"])
            valid_temp_objects_to_convert = [obj for obj in temp_objects_to_convert if obj and obj.name in bpy.data.objects]
            if valid_temp_objects_to_convert:
                for obj in valid_temp_objects_to_convert: obj.select_set(True)
                context.view_layer.objects.active = valid_temp_objects_to_convert[0]
                try: bpy.ops.object.convert(target='MESH'); self.report({'INFO'}, "Conversion to mesh complete.")
                except Exception as e: self.report({'ERROR'}, f"Failed to convert objects to mesh: {e}")
            else: self.report({'WARNING'}, "No valid temporary objects found for mesh conversion.")

            if any_high_present and scene.render.bake.use_cage:
                fallback_cages_created = 0
                fallback_cage_failed = False
                for trio_dict in temp_trio_list:
                    temp_low = trio_dict.get("low")
                    if trio_dict.get("cage") or not temp_low or temp_low.name not in bpy.data.objects:
                        continue
                    fallback_cage = self._create_fallback_cage_from_low(context, temp_low, trio_dict.get("base_name", temp_low.name), scene)
                    if fallback_cage:
                        trio_dict["cage"] = fallback_cage
                        fallback_cages_created += 1
                    else:
                        fallback_cage_failed = True
                        self.report({'ERROR'}, f"Could not create fallback cage for '{trio_dict.get('base_name', 'unknown')}'.")
                if fallback_cages_created:
                    self.report({'INFO'}, f"Created {fallback_cages_created} fallback cage mesh(es) for Matrix Bake.")
                if fallback_cage_failed:
                    return {'CANCELLED'}

            # --- 6. Join Meshes (Trios) ---
            self.report({'INFO'}, "Joining temporary meshes...")
            low_to_join = [trio["low"] for trio in temp_trio_list if trio.get("low") and trio["low"].name in bpy.data.objects]
            if low_to_join:
                bpy.ops.object.select_all(action='DESELECT'); [obj.select_set(True) for obj in low_to_join]; context.view_layer.objects.active = low_to_join[0]
                try: bpy.ops.object.join(); combined_low = context.active_object; combined_low.name = "combined_low"; self.report({'INFO'}, f"Joined {len(low_to_join)} low meshes into '{combined_low.name}'.")
                except Exception as e: self.report({'ERROR'}, f"Failed to join low meshes: {e}")
            if any_high_present:
                high_to_join = [trio["high"] for trio in temp_trio_list if trio.get("high") and trio["high"].name in bpy.data.objects]
                if high_to_join:
                    bpy.ops.object.select_all(action='DESELECT'); [obj.select_set(True) for obj in high_to_join]; context.view_layer.objects.active = high_to_join[0]
                    try: bpy.ops.object.join(); combined_high = context.active_object; combined_high.name = "combined_high"; self.report({'INFO'}, f"Joined {len(high_to_join)} high meshes into '{combined_high.name}'.")
                    except Exception as e: self.report({'ERROR'}, f"Failed to join high meshes: {e}")
                cage_to_join = []
                if scene.render.bake.use_cage:
                    cage_to_join = [trio["cage"] for trio in temp_trio_list if trio.get("cage") and trio["cage"].name in bpy.data.objects]
                if cage_to_join:
                    bpy.ops.object.select_all(action='DESELECT'); [obj.select_set(True) for obj in cage_to_join]; context.view_layer.objects.active = cage_to_join[0]
                    try: bpy.ops.object.join(); combined_cage = context.active_object; combined_cage.name = "combined_cage"; self.report({'INFO'}, f"Joined {len(cage_to_join)} cage meshes into '{combined_cage.name}'.")
                    except Exception as e: self.report({'ERROR'}, f"Failed to join cage meshes: {e}")

            # --- 7. Move Combined to Collections (Trios) ---
            self.report({'INFO'}, "Moving combined meshes to Low/High/Cage collections...")
            if combined_low and low_col: utils.add_object_to_collection(combined_low, low_col)
            if combined_high and high_col: utils.add_object_to_collection(combined_high, high_col)
            if combined_cage and cage_col: utils.add_object_to_collection(combined_cage, cage_col)

            # --- 8. Trigger Bake (Trios) ---
            # Allow baking with only combined_low when no _high counterparts exist at all
            if combined_low and (combined_high or not any_high_present):
                self.report({'INFO'}, "Preparing to trigger standard bake (High->Low)...")
                bpy.ops.object.select_all(action='DESELECT')
                # Hide non-combined objects
                self.report({'INFO'}, "Hiding non-combined objects for render...")
                combined_names = {combined_low.name}
                if combined_high: combined_names.add(combined_high.name)
                if combined_cage: combined_names.add(combined_cage.name)
                hidden_count = 0
                for obj in bpy.data.objects:
                    if obj.name not in combined_names:
                        original_render_visibility[obj.name] = obj.hide_render
                        if not obj.hide_render: obj.hide_render = True; hidden_count += 1
                self.report({'INFO'}, f"Hid {hidden_count} objects for render.")
                # Select and activate combined low
                combined_low.select_set(True); context.view_layer.objects.active = combined_low
                # Temporarily rename for bake operator matching
                temp_bake_low_name = "matrix_bake_target_low"; temp_bake_high_name = "matrix_bake_target_high"; temp_bake_cage_name = "matrix_bake_target_cage"
                original_combined_names = {"low": combined_low.name, "high": combined_high.name if combined_high else None, "cage": combined_cage.name if combined_cage else None}
                try:
                    combined_low.name = temp_bake_low_name
                    if combined_high: combined_high.name = temp_bake_high_name
                    if combined_cage: combined_cage.name = temp_bake_cage_name
                    self.report({'INFO'}, f"Triggering bake for '{temp_bake_low_name}' (Scenario: {scene.get('_gready_scenario_hint', 'Unknown')})...")
                    original_bake_mode_scene = scene.baker_bake_mode
                    scene.baker_bake_mode = 'HIGH_TO_LOW' # Ensure mode
                    try:
                        bake_result = bpy.ops.baker.bake()
                        bake_triggered = 'FINISHED' in bake_result
                        if not bake_triggered:
                            raise RuntimeError("The standard bake operator cancelled.")
                    finally:
                        scene.baker_bake_mode = original_bake_mode_scene # Restore mode
                    self.report({'INFO'}, "Standard bake call finished.")
                except Exception as e: self.report({'ERROR'}, f"Failed during standard bake call: {e}")
                finally: # Restore names
                    try:
                        low_obj_check = bpy.data.objects.get(temp_bake_low_name); high_obj_check = bpy.data.objects.get(temp_bake_high_name); cage_obj_check = bpy.data.objects.get(temp_bake_cage_name)
                        if low_obj_check and original_combined_names["low"]: low_obj_check.name = original_combined_names["low"]
                        if high_obj_check and original_combined_names["high"]: high_obj_check.name = original_combined_names["high"]
                        if cage_obj_check and original_combined_names["cage"]: cage_obj_check.name = original_combined_names["cage"]
                    except Exception as name_restore_e: self.report({'WARNING'}, f"Could not restore original combined names: {name_restore_e}")
                    # Clear scenario hint after baking
                    try:
                        if '_gready_scenario_hint' in scene.keys():
                            del scene['_gready_scenario_hint']
                    except Exception:
                        pass
            else: self.report({'ERROR'}, "Cannot trigger bake - missing combined_low or (required) combined_high.")

        # --- Restore Render Visibility (Common) ---
        if original_render_visibility:
            self.report({'INFO'}, "Restoring original render visibility...")
            restored_count = 0
            for obj_name, original_state in original_render_visibility.items():
                obj = bpy.data.objects.get(obj_name)
                if obj and obj.hide_render != original_state: obj.hide_render = original_state; restored_count += 1
            self.report({'INFO'}, f"Restored visibility for {restored_count} objects.")

        # --- Cleanup Combined (Conditional & Common) ---
        object_to_delete = None
        if bake_mode == 'HIGH_TO_LOW':
            object_to_delete = [combined_low, combined_high, combined_cage]

        if bake_triggered and object_to_delete:
            self.report({'INFO'}, "Cleaning up combined meshes...")
            bpy.ops.object.select_all(action='DESELECT')
            objects_deleted_count = 0
            valid_objects_to_delete = [obj for obj in object_to_delete if obj and obj.name in bpy.data.objects]

            if valid_objects_to_delete:
                for obj in valid_objects_to_delete: obj.select_set(True)
                try:
                    bpy.ops.object.delete(use_global=False, confirm=False)
                    objects_deleted_count = len(valid_objects_to_delete)
                    self.report({'INFO'}, f"Deleted {objects_deleted_count} combined mesh(es).")
                except Exception as e: self.report({'ERROR'}, f"Failed to delete combined meshes: {e}")
            else: self.report({'INFO'}, "No valid combined meshes found to delete.")

            # --- Cleanup Collections Created by Matrix Bake ---
            self.report({'INFO'}, "Cleaning up Matrix Bake collections...")
            for col_name in collections_created_by_bake:
                try:
                    col = bpy.data.collections.get(col_name)
                    if col:
                        # Delete the collection (Blender will handle orphaned meshes)
                        bpy.data.collections.remove(col)
                        self.report({'INFO'}, f"Deleted collection '{col_name}'.")
                except Exception as e:
                    self.report({'WARNING'}, f"Could not delete collection '{col_name}': {e}")
        elif not bake_triggered: 
            self.report({'INFO'}, "Skipping combined mesh cleanup as bake was not triggered or failed.")

        # --- Restore State (Common) ---
        self.report({'INFO'}, "Restoring original selection and mode...")
        bpy.ops.object.select_all(action='DESELECT')
        for obj in original_selection:
            try:
                if obj and obj.name in bpy.data.objects: obj.select_set(True)
            except ReferenceError: pass
        try:
            if original_active and original_active.name in bpy.data.objects: context.view_layer.objects.active = original_active
        except ReferenceError: pass
        if context.mode != original_mode:
             try: bpy.ops.object.mode_set(mode=original_mode)
             except RuntimeError: pass

        # --- Restore Backed Up Objects (Only if High->Low was run) ---
        if bake_mode == 'HIGH_TO_LOW' and backed_up_objects_by_collection:
            self.report({'INFO'}, "Restoring backed up Low/High/Cage objects...")
            restored_backup_objects = 0
            for collection_name, objects_to_restore in backed_up_objects_by_collection.items():
                target_collection = bpy.data.collections.get(collection_name)
                if not target_collection:
                    continue
                for obj in objects_to_restore:
                    try:
                        if obj and obj.name in bpy.data.objects:
                            utils.move_object_to_collection(obj, target_collection)
                            restored_backup_objects += 1
                    except Exception as e:
                        self.report({'WARNING'}, f"Could not restore '{obj.name if obj else 'None'}' to '{collection_name}': {e}")
            if restored_backup_objects > 0:
                self.report({'INFO'}, f"Restored {restored_backup_objects} backed up object(s).")

        # --- Remove Backup Collections (Only if High->Low was run) ---
        if bake_mode == 'HIGH_TO_LOW' and backup_collections_names:
            self.report({'INFO'}, "Removing backup collections...")
            removed_backup_count = 0
            for backup_name in backup_collections_names:
                backup_col = bpy.data.collections.get(backup_name)
                if backup_col:
                    try: bpy.data.collections.remove(backup_col); removed_backup_count += 1
                    except Exception as e: self.report({'WARNING'}, f"Could not remove backup collection '{backup_name}': {e}")
            if removed_backup_count > 0: self.report({'INFO'}, f"Removed {removed_backup_count} backup collections.")

        # Restore Selected to Active to the user's original preference
        try:
            bake_settings.use_selected_to_active = original_use_selected_to_active
        except Exception:
            pass

        self.report({'INFO'}, "Matrix Bake process finished.")
        return {'FINISHED'}

# --- END OF FILE operators_matrix_bake.py ---
