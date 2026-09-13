# --- START OF FILE operators_lod.py ---

# --- START OF FILE operators_lod.py ---

# gready/operators_lod.py

import bpy
import math

# --- Helper Functions ---
# (get_or_create_collection, move_object_to_collection remain unchanged)
def get_or_create_collection(collection_name, parent_collection=None):
    """
    Checks if a collection exists, creates it if not.
    Returns the collection object.
    Links the new collection to the specified parent or the scene's master collection.
    """
    if collection_name in bpy.data.collections:
        return bpy.data.collections[collection_name]
    else:
        new_collection = bpy.data.collections.new(collection_name)
        if parent_collection:
            try:
                 if parent_collection.name in bpy.data.collections:
                     parent_collection.children.link(new_collection)
                 else:
                     print(f"Warning: Parent collection '{parent_collection.name}' not found. Linking to scene collection.")
                     bpy.context.scene.collection.children.link(new_collection)
            except ReferenceError:
                 print(f"Warning: Parent collection '{parent_collection.name}' reference error. Linking to scene collection.")
                 bpy.context.scene.collection.children.link(new_collection)
            except Exception as e:
                 print(f"Warning: Could not link collection '{collection_name}' to parent '{parent_collection.name}': {e}. Linking to scene collection.")
                 bpy.context.scene.collection.children.link(new_collection)
        else:
            bpy.context.scene.collection.children.link(new_collection)
        return new_collection

def move_object_to_collection(obj, target_collection):
    """
    Moves an object to the target collection, removing it from others
    in the main scene collection hierarchy.
    """
    if not obj or obj.name not in bpy.data.objects:
        print(f"Warning: Object '{obj.name if obj else 'None'}' is invalid for moving.")
        return
    if not target_collection or target_collection.name not in bpy.data.collections:
        print(f"Warning: Target collection '{target_collection.name if target_collection else 'None'}' is invalid for moving.")
        return

    current_collections = [col for col in obj.users_collection]

    for col in current_collections:
        try:
            if col != target_collection:
                col.objects.unlink(obj)
        except (RuntimeError, ReferenceError) as e:
            print(f"Info: Could not unlink {obj.name} from {col.name}: {e}")

    try:
        if obj.name not in target_collection.objects:
            target_collection.objects.link(obj)
    except (RuntimeError, ReferenceError) as e:
         print(f"Error linking {obj.name} to {target_collection.name}: {e}")
    except Exception as e:
         print(f"Unexpected error linking {obj.name} to {target_collection.name}: {e}")


# --- Operator ---

class OBJECT_OT_generate_lods(bpy.types.Operator):
    """Generates LODs for selected objects following the specified pipeline"""
    bl_idname = "object.generate_lods"
    bl_label = "Generate LODs"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and context.active_object and \
               any(obj.type == 'MESH' for obj in context.selected_objects)

    def _process_lods_batch(self, context, lods_1_to_N, all_lods_for_object):
        """Post-process LOD1-N (and apply weighted normals on all LODs) for one base object."""
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        bpy.ops.object.select_all(action='DESELECT')
        valid_batch_lods = []
        for obj in lods_1_to_N:
            if obj and obj.name in bpy.data.objects:
                obj.select_set(True)
                valid_batch_lods.append(obj)
            else:
                print(f"Warning: LOD object {obj.name if obj else 'None'} became invalid before batch processing.")

        if not valid_batch_lods:
            raise ValueError("No valid LODs (1-N) found for batch processing.")

        context.view_layer.objects.active = valid_batch_lods[0]

        print("Applying batch sequence to LODs 1-N...")

        print("  Step 1: Clearing custom split normals...")
        for obj in valid_batch_lods:
            context.view_layer.objects.active = obj
            bpy.ops.object.select_all(action='DESELECT')
            obj.select_set(True)
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.mesh.customdata_custom_splitnormals_clear()
            bpy.ops.object.mode_set(mode='OBJECT')

        print("  Step 2: Applying Auto Smooth...")
        for obj in valid_batch_lods:
            context.view_layer.objects.active = obj
            bpy.ops.object.select_all(action='DESELECT')
            obj.select_set(True)
            bpy.ops.object.shade_auto_smooth()

        print("  Step 3: Applying Smooth by Angle modifier...")
        for obj in valid_batch_lods:
            context.view_layer.objects.active = obj
            smooth_mod = next((m for m in obj.modifiers if m.name.startswith("Smooth by Angle")), None)
            if smooth_mod:
                try:
                    bpy.ops.object.modifier_apply(modifier=smooth_mod.name)
                except Exception as e:
                    print(f"    Warning: Could not apply Smooth by Angle modifier to {obj.name}: {e}")

        print("  Step 4: Adding and applying Weighted Normal modifier...")
        active_obj_before_mod = context.view_layer.objects.active
        for obj in valid_batch_lods:
            context.view_layer.objects.active = obj
            try:
                bpy.ops.object.modifier_add(type='WEIGHTED_NORMAL')
                wn_mod = next((m for m in obj.modifiers if m.name.startswith("WeightedNormal")), None)
                if wn_mod:
                    wn_mod.keep_sharp = True
                    bpy.ops.object.modifier_apply(modifier=wn_mod.name, report=True)
                    print(f"    Applied Weighted Normal modifier to {obj.name}")
                else:
                    print(f"    Warning: Could not find Weighted Normal modifier on {obj.name}")
            except Exception as mod_err:
                print(f"    Warning: Could not process Weighted Normal modifier for {obj.name}: {mod_err}")
        context.view_layer.objects.active = active_obj_before_mod

        print("  Step 5: Ensuring Keep Sharp is enabled on all Weighted Normal modifiers...")
        for obj in valid_batch_lods:
            for mod in obj.modifiers:
                if mod.name.startswith("WeightedNormal"):
                    mod.keep_sharp = True
                    print(f"    Enabled Keep Sharp on {mod.name} for {obj.name}")

        print("  Step 6: Applying all Weighted Normal modifiers to all LODs...")
        for obj in all_lods_for_object:
            if not obj or obj.name not in bpy.data.objects:
                continue
            context.view_layer.objects.active = obj
            weighted_mods = [m for m in obj.modifiers if m.name.startswith("WeightedNormal")]
            for mod in weighted_mods:
                try:
                    bpy.ops.object.modifier_apply(modifier=mod.name, report=True)
                    print(f"    Applied {mod.name} to {obj.name}")
                except Exception as e:
                    print(f"    Warning: Could not apply {mod.name} to {obj.name}: {e}")

    def execute(self, context):
        scene = context.scene
        if not hasattr(scene, "lod_generator_levels"):
             self.report({'ERROR'}, "LOD Generator levels property not found on scene.")
             return {'CANCELLED'}
        num_lods = scene.lod_generator_levels

        original_mode = context.mode
        if original_mode != 'OBJECT':
            try:
                bpy.ops.object.mode_set(mode='OBJECT')
            except RuntimeError as e:
                self.report({'ERROR'}, f"Could not switch to Object Mode: {e}")
                return {'CANCELLED'}

        selected_original_refs = [obj for obj in context.selected_objects if obj.type == 'MESH']

        if not selected_original_refs:
            self.report({'WARNING'}, "No mesh objects selected.")
            return {'CANCELLED'}

        bpy.ops.object.select_all(action='DESELECT')
        all_generated_lods = []  # All LODs from every processed base (for final selection)
        original_active = context.view_layer.objects.active
        master_collection = context.scene.collection
        processed_bases = set()
        original_objects_map = {obj.name: obj for obj in selected_original_refs}

        for original_name, original_obj in original_objects_map.items():
            if not original_obj or original_obj.name not in bpy.data.objects:
                 print(f"Warning: Original object '{original_name}' no longer exists. Skipping.")
                 continue
            original_obj = bpy.data.objects.get(original_obj.name)
            if not original_obj:
                 print(f"Warning: Could not re-fetch original object '{original_name}'. Skipping.")
                 continue

            base_name = original_obj.name
            if "_LOD" in base_name:
                parts = base_name.split("_LOD")
                if len(parts) > 1 and parts[-1].isdigit():
                    base_name = parts[0]
                elif base_name.endswith("_LOD"):
                     base_name = base_name[:-4]

            if base_name in processed_bases:
                print(f"Info: Base name '{base_name}' already processed. Skipping {original_obj.name}.")
                continue
            processed_bases.add(base_name)

            object_all_lods = []
            object_lods_1_to_N = []

            # --- Handle LOD0 ---
            lod0_name = f"{base_name}_LOD0"
            if original_obj.name != lod0_name:
                original_obj.name = lod0_name
            lod0_obj = bpy.data.objects.get(lod0_name)
            if not lod0_obj:
                print(f"ERROR: Failed to find object after renaming to {lod0_name}. Aborting LODs for {base_name}.")
                continue

            lod0_coll = get_or_create_collection("LOD0", parent_collection=master_collection)
            move_object_to_collection(lod0_obj, lod0_coll)
            object_all_lods.append(lod0_obj)
            print(f"Processed LOD0: {lod0_name} into collection {lod0_coll.name}")

            # Get original triangle count from evaluated mesh with temporary triangulation
            # Add temporary Triangulate modifier to get accurate triangle count
            temp_tri_mod = lod0_obj.modifiers.new(name="TempTriangulate", type='TRIANGULATE')
            temp_tri_mod.quad_method = 'BEAUTY'
            temp_tri_mod.ngon_method = 'BEAUTY'
            
            # Get triangle count from evaluated mesh with triangulation
            depsgraph = context.evaluated_depsgraph_get()
            eval_obj = lod0_obj.evaluated_get(depsgraph)
            eval_mesh = eval_obj.data
            original_tris = len(eval_mesh.polygons)
            
            # Remove temporary triangulate modifier
            lod0_obj.modifiers.remove(temp_tri_mod)
            print(f"Calculated triangle count for {lod0_name}: {original_tris} triangles")

            # --- Generate LOD1 to N (Decimate Only) ---
            for lod_level in range(1, num_lods + 1):
                if scene.lod_mode == 'TARGET_TRIS':
                    target_tris = getattr(scene, f"lod_tris_{lod_level}", 100)
                    ratio = target_tris / original_tris if original_tris > 0 else 0.5
                else:
                    ratio = getattr(scene, f"lod_ratio_{lod_level}", 0.5)
                lod_name = f"{base_name}_LOD{lod_level}"
                lod_coll_name = f"LOD{lod_level}"

                if lod_name in bpy.data.objects:
                    print(f"Info: Object '{lod_name}' already exists. Skipping generation for this level.")
                    existing_lod_obj = bpy.data.objects[lod_name]
                    lod_coll = get_or_create_collection(lod_coll_name, parent_collection=master_collection)
                    move_object_to_collection(existing_lod_obj, lod_coll)
                    if existing_lod_obj not in object_all_lods:
                        object_all_lods.append(existing_lod_obj)
                    if existing_lod_obj not in object_lods_1_to_N:
                        object_lods_1_to_N.append(existing_lod_obj)
                    continue

                # --- Step 1: Create LOD (Duplicate LOD0 & Decimate) ---
                lod_obj = None
                try:
                    bpy.ops.object.select_all(action='DESELECT')
                    if not lod0_obj or lod0_obj.name not in bpy.data.objects:
                        print(f"Error: LOD0 object '{lod0_name}' is missing. Aborting further LODs for {base_name}.")
                        break

                    lod0_obj.select_set(True)
                    context.view_layer.objects.active = lod0_obj
                    bpy.ops.object.duplicate_move(OBJECT_OT_duplicate={"linked": False, "mode": 'TRANSLATION'}, TRANSFORM_OT_translate={"value":(0, 0, 0)})
                    lod_obj = context.active_object

                    if not lod_obj:
                        self.report({'ERROR'}, f"Failed to duplicate LOD0 for {lod_name}")
                        continue

                    lod_obj.name = lod_name
                    object_all_lods.append(lod_obj)
                    object_lods_1_to_N.append(lod_obj)

                    dec_mod = lod_obj.modifiers.new(name="LODDecimate", type='DECIMATE')
                    dec_mod.decimate_type = 'COLLAPSE'
                    dec_mod.ratio = ratio
                    dec_mod.use_collapse_triangulate = True
                    bpy.ops.object.modifier_apply(modifier=dec_mod.name)
                    print(f"Step 1: Created and Decimated {lod_name} from {lod0_name} (Ratio: {ratio})")

                    # --- Move to LOD Collection (Do this after decimate) ---
                    lod_coll = get_or_create_collection(lod_coll_name, parent_collection=master_collection)
                    move_object_to_collection(lod_obj, lod_coll)
                    print(f"Moved {lod_name} to collection {lod_coll.name}")

                except Exception as e:
                     print(f"Error during Step 1 (Create/Decimate/Move) for {lod_name}: {e}")
                     self.report({'WARNING'}, f"Failed Step 1 for {lod_name}. Skipping this LOD.")
                     if lod_obj and lod_obj.name in bpy.data.objects:
                         bpy.data.objects.remove(lod_obj, do_unlink=True)
                     if lod_obj in object_all_lods:
                         object_all_lods.remove(lod_obj)
                     if lod_obj in object_lods_1_to_N:
                         object_lods_1_to_N.remove(lod_obj)
                     continue

            # --- Post-process this base object's LODs before moving to the next selection ---
            if object_lods_1_to_N:
                print(f"\nPost-processing {base_name} ({len(object_lods_1_to_N)} LOD1-N mesh(es))...")
                try:
                    self._process_lods_batch(context, object_lods_1_to_N, object_all_lods)
                except Exception as e:
                    print(f"ERROR during post-processing for {base_name}: {e}")
                    self.report({'ERROR'}, f"Failed post-processing for {base_name}: {e}")
                    if context.mode != 'OBJECT':
                        try:
                            bpy.ops.object.mode_set(mode='OBJECT')
                        except RuntimeError:
                            pass
            else:
                print(f"No LODs (1-N) generated for {base_name}; skipping post-processing.")

            all_generated_lods.extend(object_all_lods)

        # --- Final Selection & Active Object Restoration ---
        bpy.ops.object.select_all(action='DESELECT')

        final_selection_count = 0
        valid_final_objects = []
        # Select ALL generated LODs (0 to N) that are still valid
        for obj_ref in all_generated_lods:
             obj = bpy.data.objects.get(obj_ref.name if obj_ref else None)
             if obj:
                 try:
                     obj.select_set(True)
                     final_selection_count += 1
                     valid_final_objects.append(obj)
                 except ReferenceError:
                     print(f"Warning: Could not select object {obj.name} (ReferenceError).")

        # Restore active object logic (same as before)
        if original_active and original_active.name in bpy.data.objects and original_active in valid_final_objects:
            context.view_layer.objects.active = original_active
        elif valid_final_objects:
             lod0_candidates = [obj for obj in valid_final_objects if obj.name.endswith("_LOD0")]
             if lod0_candidates:
                 active_base_name = original_active.name.split("_LOD")[0] if original_active and "_LOD" in original_active.name else None
                 active_lod0 = next((obj for obj in lod0_candidates if obj.name.startswith(active_base_name)), None) if active_base_name else None
                 context.view_layer.objects.active = active_lod0 if active_lod0 else lod0_candidates[-1]
             elif valid_final_objects:
                 context.view_layer.objects.active = valid_final_objects[-1]

        # Switch back mode if needed
        if original_mode != 'OBJECT' and context.mode == 'OBJECT':
            try: bpy.ops.object.mode_set(mode=original_mode)
            except RuntimeError: pass

        self.report({'INFO'}, f"LOD Generation complete for {len(processed_bases)} base object(s) up to LOD{num_lods}.")
        return {'FINISHED'}


# --- END OF FILE operators_lod.py ---
