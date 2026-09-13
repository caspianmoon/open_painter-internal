# --- START OF FILE operators_vertex_color.py ---

# gready/operators_vertex_color.py

# <pep8 compliant>
# -----------------------------------------------------------------------------
#   Name: Vertex Color Ops Tools (Integrated into G-Ready)
#   Description: Sets custom or unique random vertex colors via mode switching & ops.
#                Includes Select by Color and sets Viewport Shading.
#   Blender: 4.4
#   Version: 2.7.1 (Fixed bmesh import for Select by Color) - Integration v1.0
#   Author: Farid Mammadov
# -----------------------------------------------------------------------------

import bpy
import random
import math # For isclose
import bmesh

from bpy.props import FloatVectorProperty, FloatProperty # Added FloatProperty for tolerance
from bpy.types import Operator, Panel, PropertyGroup # Panel, PropertyGroup not used here but needed for context if properties were here

# --- Helper for Uniqueness Check ---

def is_color_close(color1, color2, tolerance=1e-4):
    """Checks if two RGB or RGBA colors are close within a tolerance."""
    len1 = len(color1)
    len2 = len(color2)
    if len1 < 3 or len2 < 3: # Need at least RGB
        return False
    compare_len = min(len1, len2)
    return all(math.isclose(c1, c2, abs_tol=tolerance) for c1, c2 in zip(color1[:compare_len], color2[:compare_len]))


def get_active_color_layer(mesh_data):
    """Return the active color attribute/layer across Blender versions."""
    color_attributes = getattr(mesh_data, "color_attributes", None)
    if color_attributes is not None:
        active_attr = getattr(color_attributes, "active_color", None) or getattr(color_attributes, "active", None)
        if active_attr is not None:
            return active_attr

    vertex_colors = getattr(mesh_data, "vertex_colors", None)
    if vertex_colors is not None:
        return vertex_colors.active
    return None


def get_corner_color_layer(mesh_data):
    color_attributes = getattr(mesh_data, "color_attributes", None)
    if color_attributes is not None:
        for attr in color_attributes:
            if getattr(attr, "domain", None) == 'CORNER':
                return attr

    active_layer = get_active_color_layer(mesh_data)
    if active_layer is not None and getattr(active_layer, "domain", 'CORNER') == 'CORNER':
        return active_layer

    return None


def get_bmesh_color_layer(bm, layer_name):
    """Return a BMesh loop color layer for byte or float color data."""
    return bm.loops.layers.color.get(layer_name) or bm.loops.layers.float_color.get(layer_name)


def ensure_active_color_layer(mesh_data):
    active_layer = get_corner_color_layer(mesh_data)
    if active_layer is not None:
        return active_layer

    color_attributes = getattr(mesh_data, "color_attributes", None)
    if color_attributes is not None:
        active_layer = color_attributes.new(name="GReady_Color", type='BYTE_COLOR', domain='CORNER')
        try:
            color_attributes.active_color = active_layer
        except Exception:
            try:
                color_attributes.active_color_index = list(color_attributes).index(active_layer)
            except Exception:
                pass
        return active_layer

    vertex_colors = getattr(mesh_data, "vertex_colors", None)
    if vertex_colors is not None:
        return vertex_colors.new(name="GReady_Color")

    return None


def get_or_create_bmesh_color_layer(bm, layer_name, data_type=None):
    color_layer = get_bmesh_color_layer(bm, layer_name)
    if color_layer is not None:
        return color_layer

    if data_type == 'FLOAT_COLOR':
        return bm.loops.layers.float_color.new(layer_name)

    return bm.loops.layers.color.new(layer_name)


def selected_face_color_loops(bm):
    selected_faces = [face for face in bm.faces if face.select]
    if selected_faces:
        loops = []
        for face in selected_faces:
            loops.extend(face.loops)
        return loops, len(selected_faces), "face"
    return [], 0, "face"


def selected_edge_color_loops(bm):
    selected_edges = {edge for edge in bm.edges if edge.select}
    if selected_edges:
        loops = []
        for face in bm.faces:
            for loop in face.loops:
                if loop.edge in selected_edges:
                    loops.append(loop)
        return loops, len(selected_edges), "edge"
    return [], 0, "edge"


def selected_vertex_color_loops(bm):
    selected_verts = {vert for vert in bm.verts if vert.select}
    if selected_verts:
        loops = []
        for face in bm.faces:
            for loop in face.loops:
                if loop.vert in selected_verts:
                    loops.append(loop)
        return loops, len(selected_verts), "vertex"
    return [], 0, "vertex"


def iter_selected_color_loops(bm, select_mode):
    if select_mode and select_mode[2]:
        loops, count, kind = selected_face_color_loops(bm)
        if loops:
            return loops, count, kind

    if select_mode and select_mode[1]:
        loops, count, kind = selected_edge_color_loops(bm)
        if loops:
            return loops, count, kind

    if select_mode and select_mode[0]:
        loops, count, kind = selected_vertex_color_loops(bm)
        if loops:
            return loops, count, kind

    for getter in (selected_face_color_loops, selected_edge_color_loops, selected_vertex_color_loops):
        loops, count, kind = getter(bm)
        if loops:
            return loops, count, kind

    return [], 0, "element"


def selected_element_indices(bm, select_mode):
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    selected_faces = {face.index for face in bm.faces if face.select}
    selected_edges = {edge.index for edge in bm.edges if edge.select}
    selected_verts = {vert.index for vert in bm.verts if vert.select}

    if select_mode and select_mode[2] and selected_faces:
        return selected_faces, selected_edges, selected_verts, len(selected_faces), "face"
    if select_mode and select_mode[1] and selected_edges:
        return selected_faces, selected_edges, selected_verts, len(selected_edges), "edge"
    if select_mode and select_mode[0] and selected_verts:
        return selected_faces, selected_edges, selected_verts, len(selected_verts), "vertex"
    if selected_faces:
        return selected_faces, selected_edges, selected_verts, len(selected_faces), "face"
    if selected_edges:
        return selected_faces, selected_edges, selected_verts, len(selected_edges), "edge"
    if selected_verts:
        return selected_faces, selected_edges, selected_verts, len(selected_verts), "vertex"
    return selected_faces, selected_edges, selected_verts, 0, "element"


def restore_edit_selection(context, obj, select_mode, face_indices, edge_indices, vert_indices):
    if context.mode != 'EDIT_MESH':
        try:
            bpy.ops.object.mode_set(mode='EDIT')
        except Exception:
            return

    try:
        context.tool_settings.mesh_select_mode = select_mode
    except Exception:
        pass

    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    for edge in bm.edges:
        edge.select_set(False)
    for face in bm.faces:
        face.select_set(False)
    for vert in bm.verts:
        vert.select_set(False)

    for vert in bm.verts:
        if vert.index in vert_indices:
            vert.select_set(True)
    for edge in bm.edges:
        if edge.index in edge_indices:
            edge.select_set(True)
    for face in bm.faces:
        if face.index in face_indices:
            face.select_set(True)

    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)


def apply_native_vertex_color(context, obj, color_rgb):
    mesh_data = obj.data
    original_mode = context.mode
    original_select_mode = tuple(context.tool_settings.mesh_select_mode)
    original_face_mask = getattr(mesh_data, "use_paint_mask", False)
    original_vertex_mask = getattr(mesh_data, "use_paint_mask_vertex", False)

    bm = bmesh.from_edit_mesh(mesh_data)
    face_indices, edge_indices, vert_indices, selected_count, selection_kind = selected_element_indices(
        bm,
        original_select_mode,
    )
    if selected_count == 0:
        bmesh.update_edit_mesh(mesh_data, loop_triangles=False, destructive=False)
        return 0, None, "No selected faces, edges, or vertices to color."

    if selection_kind == "edge":
        edge_vert_indices = {
            vert.index
            for edge in bm.edges
            if edge.index in edge_indices
            for vert in edge.verts
        }
        for face in bm.faces:
            face.select_set(False)
        for edge in bm.edges:
            edge.select_set(False)
        for vert in bm.verts:
            vert.select_set(vert.index in edge_vert_indices)
        try:
            context.tool_settings.mesh_select_mode = (True, False, False)
        except Exception:
            pass

    bmesh.update_edit_mesh(mesh_data, loop_triangles=False, destructive=False)

    try:
        if bpy.context.mode == 'EDIT_MESH':
            bpy.ops.object.mode_set(mode='OBJECT')

        if ensure_active_color_layer(mesh_data) is None:
            return 0, None, "Could not create or access a corner color attribute."

        if bpy.context.mode != 'VERTEX_PAINT':
            bpy.ops.object.mode_set(mode='VERTEX_PAINT')

        mesh_data.use_paint_mask = selection_kind == "face"
        mesh_data.use_paint_mask_vertex = selection_kind in {"vertex", "edge"}

        paint_settings = context.scene.tool_settings.vertex_paint
        unified_settings = paint_settings.unified_paint_settings
        unified_settings.color = color_rgb
        if getattr(paint_settings, "brush", None):
            paint_settings.brush.color = color_rgb

        result = bpy.ops.paint.vertex_color_set()
        if 'FINISHED' not in result:
            return 0, None, "Blender vertex color set operation failed."

    finally:
        mesh_data.use_paint_mask = original_face_mask
        mesh_data.use_paint_mask_vertex = original_vertex_mask
        if original_mode == 'EDIT_MESH':
            restore_edit_selection(context, obj, original_select_mode, face_indices, edge_indices, vert_indices)
        elif original_mode != bpy.context.mode:
            try:
                bpy.ops.object.mode_set(mode=original_mode)
            except Exception:
                pass

    set_viewport_shading_to_vertex_color(context)
    return selected_count, selection_kind, None


def apply_color_to_selected_elements(context, obj, color_rgb):
    return apply_native_vertex_color(context, obj, color_rgb)

# --- Helper to set Viewport Shading ---
def set_viewport_shading_to_vertex_color(context):
    """Attempts to set the 3D Viewport shading to show Vertex Colors."""
    try:
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        if hasattr(space.shading, 'color_type'):
                            # Only change if it's not already set to Vertex
                            if space.shading.color_type != 'VERTEX':
                                space.shading.color_type = 'VERTEX'
                                print("Set viewport shading to Vertex Color.")
                        else:
                            print(f"Viewport shading type '{space.shading.type}' does not support vertex color display directly.")
                        # break # Optional
                # break # Optional
    except AttributeError as e:
        print(f"Could not set viewport shading: {e}")
    except Exception as e:
        print(f"An unexpected error occurred while setting viewport shading: {e}")


# --- Operators ---

# Custom Color Operator
class OBJECT_OT_set_custom_vertex_color_ops(Operator):
    """Applies the custom color using Vertex Paint ops"""
    bl_idname = "object.set_custom_vertex_color_ops"
    bl_label = "Apply Custom Color (Ops)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and
                obj.type == 'MESH' and
                hasattr(obj, 'data') and
                obj.data is not None and
                context.mode == 'EDIT_MESH')

    def execute(self, context):
        obj = context.active_object
        mesh_data = obj.data
        # Access property through the G-Ready structure
        if not hasattr(context.scene, 'gready_vcolor_ops_settings'):
             self.report({'ERROR'}, "G-Ready Vertex Color properties not found.")
             return {'CANCELLED'}
        custom_color_rgb = tuple(context.scene.gready_vcolor_ops_settings.custom_color)

        try:
            selected_count, selection_kind, error_message = apply_color_to_selected_elements(context, obj, custom_color_rgb)
        except Exception as e:
            self.report({'ERROR'}, f"Could not apply vertex color: {e}")
            return {'CANCELLED'}

        if error_message:
            self.report({'WARNING'}, error_message)
            return {'CANCELLED'}

        self.report({'INFO'}, f"Applied custom vertex color to {selected_count} selected {selection_kind}(s).")
        return {'FINISHED'}


# Random Color Operator
class OBJECT_OT_set_random_vertex_color_ops(Operator):
    """Applies a unique random color using Vertex Paint ops"""
    bl_idname = "object.set_random_vertex_color_ops"
    bl_label = "Apply Unique Random Color (Ops)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and
                obj.type == 'MESH' and
                hasattr(obj, 'data') and
                obj.data is not None and
                context.mode == 'EDIT_MESH')

    def execute(self, context):
        obj = context.active_object
        mesh_data = obj.data
        max_retries = 1000
        tolerance = 1e-4

        # --- Uniqueness Check ---
        print("--- Checking for existing colors ---")
        existing_colors_rgb = set()
        active_vcol = get_active_color_layer(mesh_data)
        if active_vcol:
            print(f"Reading colors from layer: {active_vcol.name}")
            color_domain = getattr(active_vcol, "domain", 'CORNER')
            for poly in mesh_data.polygons:
                for loop_index in poly.loop_indices:
                    data_index = loop_index
                    if color_domain == 'POINT':
                        data_index = mesh_data.loops[loop_index].vertex_index
                    try:
                        loop_color_rgba = active_vcol.data[data_index].color
                        existing_colors_rgb.add(tuple(loop_color_rgba[:3]))
                    except (IndexError, AttributeError):
                        pass
            print(f"Found {len(existing_colors_rgb)} unique existing RGB colors (approx).")
        else:
            print("No active vertex color layer found. Any random color is unique.")

        unique_random_color_rgb = None
        for attempt in range(max_retries):
            temp_random_rgb = (random.random(), random.random(), random.random())
            is_unique = True
            for existing_rgb in existing_colors_rgb:
                if is_color_close(temp_random_rgb, existing_rgb, tolerance):
                    is_unique = False
                    break
            if is_unique:
                unique_random_color_rgb = temp_random_rgb
                print(f"Found unique random color after {attempt + 1} attempts: {unique_random_color_rgb}")
                break

        if unique_random_color_rgb is None:
            self.report({'ERROR'}, f"Could not find a unique random color after {max_retries} attempts.")
            return {'CANCELLED'}

        try:
            selected_count, selection_kind, error_message = apply_color_to_selected_elements(context, obj, unique_random_color_rgb)
        except Exception as e:
            self.report({'ERROR'}, f"Could not apply random vertex color: {e}")
            return {'CANCELLED'}

        if error_message:
            self.report({'WARNING'}, error_message)
            return {'CANCELLED'}

        self.report({'INFO'}, f"Applied random vertex color to {selected_count} selected {selection_kind}(s).")
        return {'FINISHED'}


# Select by Vertex Color Operator
class OBJECT_OT_select_by_vertex_color(Operator):
    """Selects faces with the same average vertex color as the initial selection"""
    bl_idname = "object.select_by_vertex_color"
    bl_label = "Select by Color"
    bl_options = {'REGISTER', 'UNDO'}

    color_tolerance: FloatProperty( # Use FloatProperty directly
        name="Color Tolerance",
        description="How close colors must be to be considered the same",
        default=1e-3,
        min=0.0,
        max=0.1,
        subtype='FACTOR', # Use FACTOR for better slider display
        precision=4 # Display more decimal places
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and
                obj.type == 'MESH' and
                hasattr(obj, 'data') and
                obj.data is not None and
                get_active_color_layer(obj.data) is not None and
                context.mode == 'EDIT_MESH')

    # Add invoke method to show tolerance in redo panel
    def invoke(self, context, event):
        # This allows the tolerance to be adjusted in the Redo Last panel
        return self.execute(context)

    def draw(self, context):
        # Draw the tolerance property in the Redo Last panel
        layout = self.layout
        layout.prop(self, "color_tolerance")


    def execute(self, context):
        obj = context.active_object
        mesh_data = obj.data
        # Ensure active layer exists (poll should guarantee this)
        active_layer = get_active_color_layer(mesh_data)
        if not active_layer:
             self.report({'ERROR'}, "No active vertex color layer found.")
             return {'CANCELLED'}
        vertex_color_layer = active_layer
        original_mode = context.mode

        bm = None
        try:
            # --- Use BMesh for Edit Mode Selection ---
            bm = bmesh.from_edit_mesh(mesh_data)
            bm.faces.ensure_lookup_table()
            vcol_layer_bm = get_bmesh_color_layer(bm, vertex_color_layer.name)
            if not vcol_layer_bm:
                 self.report({'ERROR'}, f"Could not find BMesh layer corresponding to '{vertex_color_layer.name}'.")
                 bmesh.update_edit_mesh(mesh_data) # Release bmesh
                 return {'CANCELLED'}


            selected_faces_bm = [f for f in bm.faces if f.select]

            if not selected_faces_bm:
                self.report({'INFO'}, "No faces selected initially.")
                bmesh.update_edit_mesh(mesh_data)
                return {'CANCELLED'}

            reference_color_avg = None
            first_face_avg_color = None

            # Calculate average color for the first selected face
            first_face = selected_faces_bm[0]
            try:
                # Use bmesh layer access
                colors = [loop[vcol_layer_bm] for loop in first_face.loops]
                num_channels = len(colors[0]) if colors else 0
                if num_channels >= 3:
                    first_face_avg_color = [sum(ch[i] for ch in colors) / len(colors) for i in range(num_channels)]
                else:
                     self.report({'WARNING'}, f"Face {first_face.index} has invalid color data.")
                     bmesh.update_edit_mesh(mesh_data)
                     return {'CANCELLED'}

            except (IndexError, TypeError, KeyError) as e: # Added KeyError for layer access
                 self.report({'ERROR'}, f"Could not read color for first selected face: {e}")
                 bmesh.update_edit_mesh(mesh_data)
                 return {'CANCELLED'}

            # Check if all selected faces have the *same* average color
            reference_color_avg = first_face_avg_color
            for face in selected_faces_bm[1:]:
                 try:
                     colors = [loop[vcol_layer_bm] for loop in face.loops]
                     num_channels = len(colors[0]) if colors else 0
                     if num_channels >= 3:
                         avg_color = [sum(ch[i] for ch in colors) / len(colors) for i in range(num_channels)]
                         if not is_color_close(avg_color, reference_color_avg, self.color_tolerance):
                             self.report({'INFO'}, "Initial selection contains faces with different average colors.")
                             reference_color_avg = None
                             break
                     else:
                          print(f"Warning: Face {face.index} has invalid color data during check.")
                          reference_color_avg = None
                          break

                 except (IndexError, TypeError, KeyError) as e:
                      print(f"Warning: Could not read color for face {face.index} during check: {e}")
                      reference_color_avg = None
                      break

            if reference_color_avg is None:
                # Don't report error here, just info, as it might be intentional
                # self.report({'INFO'}, "Could not establish a single reference color from initial selection.")
                bmesh.update_edit_mesh(mesh_data)
                return {'CANCELLED'}

            print(f"Reference average color: {[round(c, 4) for c in reference_color_avg]}")

            # --- Select Matching Faces ---
            faces_selected_count = 0
            # Deselect all first for cleaner selection
            for f in bm.faces:
                f.select_set(False)

            for face in bm.faces:
                try:
                    colors = [loop[vcol_layer_bm] for loop in face.loops]
                    num_channels = len(colors[0]) if colors else 0
                    if num_channels >= 3:
                        avg_color = [sum(ch[i] for ch in colors) / len(colors) for i in range(num_channels)]
                        if is_color_close(avg_color, reference_color_avg, self.color_tolerance):
                            face.select_set(True)
                            faces_selected_count += 1
                    # else: face remains deselected

                except (IndexError, TypeError, KeyError):
                    # face remains deselected
                    pass

            # Update the mesh from bmesh changes
            bmesh.update_edit_mesh(mesh_data)
            self.report({'INFO'}, f"Selected {faces_selected_count} faces matching the reference color.")

        except Exception as e:
             self.report({'ERROR'}, f"An error occurred during selection: {e}")
             # Ensure bmesh is potentially freed even on error
             if bm and mesh_data: # Check if mesh_data is still valid
                 try:
                     bmesh.update_edit_mesh(mesh_data)
                 except:
                     pass
             return {'CANCELLED'}

        return {'FINISHED'}

# --- END OF FILE operators_vertex_color.py ---
