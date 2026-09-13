# --- START OF FILE utils.py ---

# easy_surface/utils.py

import bpy
import bmesh
import collections
import math
import os
from mathutils import Vector
from mathutils.kdtree import KDTree

# --- Helper Functions (Easy Surface & Boolean Cleanup) ---

def get_boolean_modifiers(obj):
    """Returns a list of Boolean modifiers on the object, or an empty list."""
    if obj and obj.type == 'MESH' and obj.modifiers:
        return [m for m in obj.modifiers if m.type == 'BOOLEAN']
    return []

def get_first_modifier_of_type(obj, mod_type):
    """Returns the first modifier of the specified type on the object, or None."""
    if obj and obj.type == 'MESH' and obj.modifiers:
        for m in obj.modifiers:
            if m.type == mod_type:
                return m
    return None

# --- NEW HELPER FUNCTION ---
def get_last_modifier_of_type(obj, mod_type):
    """Returns the LAST modifier of the specified type on the object, or None."""
    if obj and obj.type == 'MESH' and obj.modifiers:
        # Iterate in reverse to find the last one first
        for m in reversed(obj.modifiers):
            if m.type == mod_type:
                return m
    return None
# --- END NEW HELPER FUNCTION ---

# --- NEW HELPER FUNCTION for polling edit mode selection ---
def _poll_edit_mesh_selection(context):
    """Common poll function for edit mode operators requiring selection."""
    obj = context.active_object
    if not (obj and obj.type == 'MESH' and context.mode == 'EDIT_MESH'):
        return False
    # Check for selection using BMesh
    try:
        # Use a temporary BMesh without modifying the mesh
        bm = bmesh.from_edit_mesh(obj.data)
        has_selection = any(v.select for v in bm.verts) or \
                        any(e.select for e in bm.edges) or \
                        any(f.select for f in bm.faces)
        # bm.free() # Not needed when using from_edit_mesh like this
        return has_selection
    except ValueError:
        # This can happen if the mesh is invalid or during certain operations
        # print("Warning: Could not get BMesh for polling selection.")
        return False # Assume no selection if BMesh fails
    except Exception as e:
        print(f"Error during BMesh poll: {e}")
        return False
# --- END NEW HELPER FUNCTION ---


# --- Boolean Cleanup Helper Functions ---
# ... (get_connected_edges, find_loop_ends, perform_group_assignment remain unchanged) ...
def get_connected_edges(start_edges):
    """Finds connected components within a set of BMesh edges."""
    if not start_edges: return []
    edge_components = []
    visited_overall = set()
    # Filter for valid edges before creating dict
    valid_start_edges = [e for e in start_edges if e.is_valid]
    start_edges_dict = {e.index: e for e in valid_start_edges}
    current_start_edges = list(valid_start_edges) # Use the filtered list

    while current_start_edges:
        start_edge = current_start_edges.pop(0) # Use pop(0) for queue-like behavior
        # Check if already visited (could happen if components merge)
        if start_edge in visited_overall: continue
        # Ensure edge is still valid before starting component search
        if not start_edge.is_valid: continue

        current_component = set()
        queue = collections.deque([start_edge])
        visited_component = {start_edge} # Track visited within this component search
        visited_overall.add(start_edge) # Mark as visited globally

        while queue:
            current_edge = queue.popleft()
            # Ensure edge is still valid before processing
            if not current_edge.is_valid: continue

            current_component.add(current_edge)

            for v in current_edge.verts:
                 # Ensure vertex is valid
                 if not v.is_valid: continue
                 for linked_edge in v.link_edges:
                     # Ensure linked edge is valid and in the initial valid set, and not yet visited globally
                     if linked_edge.is_valid and linked_edge.index in start_edges_dict and linked_edge not in visited_overall:
                         # Get the actual BMEdge object from the dictionary
                         actual_linked_edge = start_edges_dict[linked_edge.index]
                         # Double check if the actual edge is still valid
                         if actual_linked_edge.is_valid:
                             visited_component.add(actual_linked_edge) # Add to component visited set
                             visited_overall.add(actual_linked_edge) # Add to global visited set
                             queue.append(actual_linked_edge) # Add to queue for processing

        if current_component:
            edge_components.append(current_component)

    return edge_components


def find_loop_ends(selected_edges):
    """Checks if a set of BMesh edges forms an open loop and returns ends."""
    if not selected_edges or len(selected_edges) < 1: return False, set(), set()
    vert_edge_counts = collections.defaultdict(int)
    all_verts = set()
    # Ensure we only process valid edges with exactly 2 valid vertices
    valid_edges = {e for e in selected_edges if e.is_valid and len(e.verts) == 2 and e.verts[0].is_valid and e.verts[1].is_valid}

    if not valid_edges: return False, set(), set() # Return early if no valid edges

    for edge in valid_edges:
        v1, v2 = edge.verts
        # Counts based on valid vertices
        vert_edge_counts[v1] += 1; all_verts.add(v1)
        vert_edge_counts[v2] += 1; all_verts.add(v2)

    # Find vertices connected to only one edge *within the valid selection*
    end_vertices = {v for v in all_verts if vert_edge_counts[v] == 1}

    # An open loop must have exactly two end vertices
    if len(end_vertices) == 2:
        end_edges = set()
        # Find the valid edges connected to these end vertices
        for edge in valid_edges:
            v1, v2 = edge.verts
            is_v1_end = v1 in end_vertices
            is_v2_end = v2 in end_vertices
            # Edge connects one end vert OR it's the only edge and connects both ends
            if (is_v1_end != is_v2_end) or (is_v1_end and is_v2_end and len(valid_edges) == 1):
                 end_edges.add(edge)

        # Check the condition carefully
        if (len(end_edges) == 2 and len(valid_edges) > 1) or \
           (len(end_edges) == 1 and len(valid_edges) == 1):
             return True, end_vertices, end_edges
        else:
             # Handles cases like two edges sharing one end vertex but not forming a simple loop
             return False, set(), set()
    else:
        # Not an open loop (could be closed, multiple ends, or zero ends)
        return False, set(), set()


def perform_group_assignment(context, operator):
    """Performs the complex edge selection and vertex group assignment for Boolean Cleanup."""
    obj = context.active_object; me = obj.data; current_mode = context.mode
    # Ensure Edit Mode
    if current_mode != 'EDIT_MESH':
        try: bpy.ops.object.mode_set(mode='EDIT')
        except RuntimeError as e: operator.report({'ERROR'}, f"Cannot switch to Edit Mode: {e}"); return False
    # Get BMesh
    try: bm = bmesh.from_edit_mesh(me)
    except ValueError: operator.report({'ERROR'}, "Could not get BMesh."); return False

    bm.verts.ensure_lookup_table(); bm.edges.ensure_lookup_table(); bm.faces.ensure_lookup_table()
    initial_selected_edges_bm = {e for e in bm.edges if e.select and e.is_valid} # Added validity check
    if not initial_selected_edges_bm:
        operator.report({'WARNING'}, "No valid edges selected.")
        bmesh.update_edit_mesh(me) # Update mesh even on failure
        # bm.free() # Not needed with from_edit_mesh
        return False

    initial_selected_edge_indices = {e.index for e in initial_selected_edges_bm}
    initial_selected_vert_indices = {v.index for edge in initial_selected_edges_bm for v in edge.verts if v.is_valid} # Added validity check

    # Use helper functions defined in this file
    is_open_loop, end_vertices_bm, end_edges_bm = find_loop_ends(initial_selected_edges_bm)
    end_edge_indices = {e.index for e in end_edges_bm}

    if is_open_loop:
        operator.report({'INFO'}, "Open edge loop detected.")
        indices_for_ops = initial_selected_edge_indices - end_edge_indices
        if not indices_for_ops:
            operator.report({'WARNING'}, "Initial selection too short after removing ends. Treating as closed.");
            is_open_loop = False # Fallback to closed loop logic
            indices_for_ops = initial_selected_edge_indices
    else:
        operator.report({'INFO'}, "Closed edge loop or complex selection detected.")
        indices_for_ops = initial_selected_edge_indices

    # --- Boundary Selection Logic ---
    try:
        # Go to OBJECT mode to safely modify mesh data selection flags
        bpy.ops.object.mode_set(mode='OBJECT')
        # Deselect everything on the mesh data level
        for v in me.vertices: v.select = False
        for e in me.edges: e.select = False
        for f in me.polygons: f.select = False
        # Select the edges needed for the operator
        selected_count_ops = 0
        for edge_index in indices_for_ops:
             if edge_index < len(me.edges): # Check index bounds
                  me.edges[edge_index].select = True
                  selected_count_ops += 1
        operator.report({'INFO'}, f"Set {selected_count_ops} edges for operator input.")
        # Return to EDIT mode
        bpy.ops.object.mode_set(mode='EDIT')
        # Ensure Edge select mode before running ops
        bpy.ops.mesh.select_mode(use_extend=False, use_expand=False, type='EDGE')
        # Run selection sequence
        bpy.ops.mesh.select_more(use_face_step=True)
        bpy.ops.mesh.select_mode(use_extend=False, use_expand=False, type='FACE')
        bpy.ops.mesh.region_to_loop()
    except Exception as e:
         operator.report({'ERROR'}, f"Selection ops failed: {e}")
         # Try to return to Edit mode if possible
         if bpy.context.mode != 'EDIT_MESH':
             try: bpy.ops.object.mode_set(mode='EDIT')
             except: pass # Ignore errors trying to return
         # bm.free() # Not needed with from_edit_mesh
         return False # Indicate failure

    # --- Process Boundary Edges ---
    # Re-acquire BMesh as mesh data/selection changed
    try: bm = bmesh.from_edit_mesh(me)
    except ValueError:
        operator.report({'ERROR'}, "Could not get BMesh after selection ops.")
        # bm.free() # Ensure free even if previous one existed? No, from_edit_mesh handles it.
        return False
    bm.verts.ensure_lookup_table(); bm.edges.ensure_lookup_table()

    # Get the newly selected boundary edges, ensure validity
    boundary_edges_step3 = {e for e in bm.edges if e.select and e.is_valid}
    if not boundary_edges_step3:
        operator.report({'WARNING'}, "Boundary loop selection resulted in no valid edges.")
        bmesh.update_edit_mesh(me)
        # bm.free()
        return False

    final_boundary_edges = boundary_edges_step3.copy()
    deselected_count = 0

    # --- Deselection Logic (Original Version) ---
    if is_open_loop:
        operator.report({'INFO'}, "Open Loop - Deselecting boundary edges touching initial vertices.")
        edges_to_deselect_open = set()
        for edge in boundary_edges_step3:
            # Check validity before accessing verts/indices
            if not edge.is_valid or len(edge.verts) != 2: continue
            v1, v2 = edge.verts
            if not v1.is_valid or not v2.is_valid: continue
            v1_idx, v2_idx = v1.index, v2.index
            if v1_idx in initial_selected_vert_indices or v2_idx in initial_selected_vert_indices:
                edges_to_deselect_open.add(edge)

        operator.report({'INFO'}, f"Open Loop - Found {len(edges_to_deselect_open)} boundary edges to deselect.")
        for edge_to_deselect in edges_to_deselect_open:
            # Check validity before deselecting
            if edge_to_deselect.is_valid:
                if edge_to_deselect in final_boundary_edges:
                    edge_to_deselect.select_set(False)
                    final_boundary_edges.remove(edge_to_deselect)
                    deselected_count += 1
                elif edge_to_deselect.select: # Deselect even if not in set (shouldn't happen)
                    edge_to_deselect.select_set(False)
    else: # Closed Loop / Complex
        operator.report({'INFO'}, "Closed Loop/Complex - Checking for overlap and deselecting neighbors.")
        # Get initial edges in the current BMesh state, check validity
        initial_selected_edges_in_current_bm = {bm.edges[idx] for idx in initial_selected_edge_indices if idx < len(bm.edges) and bm.edges[idx].is_valid}
        overlapping_edges = initial_selected_edges_in_current_bm.intersection(boundary_edges_step3)

        if overlapping_edges:
            operator.report({'INFO'}, f"Closed Loop - Overlap found ({len(overlapping_edges)} edges).")
            edges_to_deselect_closed = overlapping_edges.copy()
            for edge_overlap in overlapping_edges:
                if not edge_overlap.is_valid: continue
                for v in edge_overlap.verts:
                    if not v.is_valid: continue
                    for linked_edge in v.link_edges:
                        # Check linked edge validity before adding
                        if linked_edge.is_valid and linked_edge in boundary_edges_step3:
                            edges_to_deselect_closed.add(linked_edge)
            operator.report({'INFO'}, f"Closed Loop - Total edges for deselection: {len(edges_to_deselect_closed)}")
            for edge_to_deselect in edges_to_deselect_closed:
                 # Check validity before deselecting
                 if edge_to_deselect.is_valid:
                     if edge_to_deselect in final_boundary_edges:
                         edge_to_deselect.select_set(False)
                         final_boundary_edges.remove(edge_to_deselect)
                         deselected_count += 1
                     elif edge_to_deselect.select:
                         edge_to_deselect.select_set(False)
        else:
            operator.report({'INFO'}, "Closed Loop - No overlap found. Skipping deselection.")

    operator.report({'INFO'}, f"Step 4 - Actually deselected {deselected_count} edges.")

    # Check if any edges remain after deselection
    if not final_boundary_edges:
        operator.report({'WARNING'}, "Deselection removed all boundary edges.")
        bmesh.update_edit_mesh(me)
        # bm.free()
        return False
    else:
        operator.report({'INFO'}, f"{len(final_boundary_edges)} edges remain in final boundary.")

    # --- Group Assignment ---
    # Use helper function defined in this file
    edge_components = get_connected_edges(final_boundary_edges)
    operator.report({'INFO'}, f"Found {len(edge_components)} connected components in the final boundary.")

    edge_group_A_edges = set(); edge_group_B_edges = set()
    if len(edge_components) >= 1:
        edge_group_A_edges = edge_components[0]
        operator.report({'INFO'}, f"Assigned {len(edge_group_A_edges)} edges to Group A.")
    if len(edge_components) >= 2:
        for i in range(1, len(edge_components)):
            edge_group_B_edges.update(edge_components[i])
        operator.report({'INFO'}, f"Assigned {len(edge_group_B_edges)} edges to Group B from {len(edge_components) - 1} component(s).")
    elif len(edge_components) == 1:
        operator.report({'INFO'}, "Only one boundary loop remains. Group B will be empty.")

    verts_group_A = {v for edge in edge_group_A_edges if edge.is_valid for v in edge.verts if v.is_valid}
    verts_group_B = {v for edge in edge_group_B_edges if edge.is_valid for v in edge.verts if v.is_valid}
    operator.report({'INFO'}, f"Group A has {len(verts_group_A)} verts, Group B has {len(verts_group_B)} verts.")

    # Re-get initial verts in current BMesh state
    initial_selected_verts_in_current_bm = {bm.verts[idx] for idx in initial_selected_vert_indices if idx < len(bm.verts) and bm.verts[idx].is_valid}
    verts_assigned_to_A_indices = []; verts_assigned_to_B_indices = []

    # Proximity check
    for v_initial in initial_selected_verts_in_current_bm:
        if not v_initial.is_valid: continue # Ensure initial vertex is valid
        is_near_A, is_near_B = False, False
        for linked_edge in v_initial.link_edges:
            if not linked_edge.is_valid: continue
            other_v = linked_edge.other_vert(v_initial)
            if not other_v or not other_v.is_valid: continue # Ensure other vertex is valid

            if not is_near_A and other_v in verts_group_A: is_near_A = True
            if not is_near_B and verts_group_B and other_v in verts_group_B: is_near_B = True # Check if Group B exists
            if is_near_A and is_near_B: break # Optimization

        if is_near_A: verts_assigned_to_A_indices.append(v_initial.index)
        if is_near_B: verts_assigned_to_B_indices.append(v_initial.index)

    operator.report({'INFO'}, f"Proximity check: {len(verts_assigned_to_A_indices)} near A, {len(verts_assigned_to_B_indices)} near B.")

    # --- Apply Vertex Groups (Object Mode) ---
    bmesh.update_edit_mesh(me) # Write changes before switching mode
    # bm.free() # Not needed

    try:
        if bpy.context.mode == 'EDIT_MESH': bpy.ops.object.mode_set(mode='OBJECT')
    except Exception as e:
        operator.report({'ERROR'}, f"Failed to switch to Object Mode: {e}")
        # Attempt to return to Edit mode if possible
        try: bpy.ops.object.mode_set(mode='EDIT')
        except: pass
        return False

    try:
        # Remove existing groups first
        vg_A_existing = obj.vertex_groups.get("A");
        if vg_A_existing:
            operator.report({'INFO'}, "Removing existing vertex group 'A'.")
            obj.vertex_groups.remove(vg_A_existing)
        vg_B_existing = obj.vertex_groups.get("B");
        if vg_B_existing:
            operator.report({'INFO'}, "Removing existing vertex group 'B'.")
            obj.vertex_groups.remove(vg_B_existing)

        # Create new groups
        vg_A = obj.vertex_groups.new(name="A")
        vg_B = obj.vertex_groups.new(name="B")

        # Assign vertices
        if verts_assigned_to_A_indices: vg_A.add(verts_assigned_to_A_indices, 1.0, 'ADD')
        if verts_assigned_to_B_indices: vg_B.add(verts_assigned_to_B_indices, 1.0, 'ADD')

        # Remove overlap
        set_A = set(verts_assigned_to_A_indices)
        set_B = set(verts_assigned_to_B_indices)
        overlapping_indices = list(set_A.intersection(set_B))
        if overlapping_indices:
            operator.report({'INFO'}, f"Found {len(overlapping_indices)} vertices assigned to both A and B. Removing them.")
            try:
                vg_A.remove(overlapping_indices)
                vg_B.remove(overlapping_indices)
            except RuntimeError as e_remove:
                operator.report({'WARNING'}, f"Could not remove overlapping vertices: {e_remove}")
        else:
            operator.report({'INFO'}, "No vertices assigned to both groups.")
    except Exception as e:
        operator.report({'ERROR'}, f"Vertex group assignment failed: {e}")
        # Attempt to return to Edit mode on failure
        if bpy.context.mode != 'EDIT_MESH':
             try: bpy.ops.object.mode_set(mode='EDIT')
             except: pass
        return False

    # --- Final Check and Return ---
    try:
         if bpy.context.mode != 'EDIT_MESH': bpy.ops.object.mode_set(mode='EDIT')
    except Exception as e:
        operator.report({'WARNING'}, f"Failed return to edit mode: {e}")
        pass # Non-critical failure

    # Re-query groups after assignment for final count
    final_count_A = 0; final_count_B = 0
    vg_A_final = obj.vertex_groups.get("A"); vg_B_final = obj.vertex_groups.get("B")
    if vg_A_final:
        # A more reliable way to count vertices in a group
        for v_idx in range(len(me.vertices)):
             try:
                  # Use a small tolerance for float comparison
                  if vg_A_final.weight(v_idx) > 0.5:
                      final_count_A += 1
             except RuntimeError: pass # Vertex index might be invalid if mesh changed drastically
    if vg_B_final:
        for v_idx in range(len(me.vertices)):
             try:
                  if vg_B_final.weight(v_idx) > 0.5:
                      final_count_B += 1
             except RuntimeError: pass

    operator.report({'INFO'}, f"Finished Assign Phase: Final counts - A: {final_count_A}, B: {final_count_B}.")
    return True # Success


# --- Baking Helper Functions ---
# ... (get_collection, add_object_to_collection, get_poly_count, create_cage_from_duplicate remain unchanged) ...
def get_collection(name, parent_collection=None):
    """Gets or creates a collection."""
    if parent_collection is None: parent_collection = bpy.context.scene.collection
    if name in bpy.data.collections: return bpy.data.collections[name]
    else:
        new_col = bpy.data.collections.new(name)
        parent_collection.children.link(new_col)
        return new_col

def add_object_to_collection(obj, collection):
    """Adds object to collection, removing from others in main hierarchy."""
    current_collections = list(obj.users_collection)
    if collection in current_collections and len(current_collections) == 1: return

    if collection not in current_collections:
        try: collection.objects.link(obj)
        except RuntimeError as e: print(f"Warning: Could not link {obj.name} to {collection.name}: {e}"); return

    scene_collection_root = bpy.context.scene.collection
    collections_to_unlink = []
    for col in obj.users_collection:
         is_top_level_or_direct_child = (col == scene_collection_root or col.name in scene_collection_root.children)
         if col != collection and is_top_level_or_direct_child:
             collections_to_unlink.append(col)

    for col in collections_to_unlink:
        try: col.objects.unlink(obj)
        except RuntimeError: pass


def move_object_to_collection(obj, collection):
    if not obj or not collection:
        return False

    if collection not in obj.users_collection:
        collection.objects.link(obj)

    for col in list(obj.users_collection):
        if col == collection:
            continue
        try:
            col.objects.unlink(obj)
        except RuntimeError:
            pass

    return True


def get_poly_count(obj):
    """Returns the polygon count of an object's mesh."""
    if obj and obj.type == 'MESH' and obj.data: return len(obj.data.polygons)
    return 0

def create_cage_from_duplicate(obj, cage_name, collection=None):
    """Creates a cage mesh as a simple duplicate of the source object, removing materials."""
    print(f"Attempting to create cage for {obj.name} as {cage_name}")
    if not obj or obj.type != 'MESH' or not obj.data:
        print(f"Skipping cage creation for {obj.name} (invalid object/data).")
        return None
    try:
        cage = obj.copy()
        cage.data = obj.data.copy()
        cage.name = cage_name
        if cage.data.materials: cage.data.materials.clear()
    except Exception as e:
        print(f"Error duplicating object or clearing materials for {obj.name}: {e}")
        if 'cage' in locals() and cage.name in bpy.data.objects:
             try:
                 if cage.name in bpy.context.scene.collection.objects: bpy.context.scene.collection.objects.unlink(cage)
                 bpy.data.objects.remove(cage, do_unlink=True)
             except (ReferenceError, RuntimeError): pass
        return None

    try: bpy.context.collection.objects.link(cage)
    except Exception as e: print(f"Note: Linking cage {cage.name} to context collection potentially redundant or failed: {e}")
    if collection: add_object_to_collection(cage, collection)

    cage.display_type = 'SOLID'
    cage.color = (0.0, 1.0, 0.0, 0.5) # Green RGBA
    cage.show_transparent = True
    cage.hide_render = True
    cage.update_tag()
    print(f"Successfully created cage base: {cage.name} (Solid, Green, Transparent, No Materials)")
    return cage
# --- END OF FILE utils.py ---