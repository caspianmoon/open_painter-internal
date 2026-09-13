# --- START OF FILE operators_bake_explode.py ---

# gready/operators_bake_explode.py
# Rewritten using object-local matrix storage based on Texport patterns

import bpy
from bpy.props import BoolProperty, CollectionProperty, StringProperty
from bpy.types import PropertyGroup, Operator
from mathutils import Matrix
from collections import defaultdict

# --- Constants ---
ORIGINAL_TRANSFORM_PROP = "_gready_bake_explode_original_transform"
ALTERNATE_TRANSFORM_PROP = "_gready_bake_explode_alternate_transform"
KEEPER_SUFFIX = "_visible"

# --- Matrix Helper Functions (from Texport) ---
def flatten_matrix(matrix):
    """Flatten a 4x4 matrix to a list of 16 floats."""
    return [value for row in matrix for value in row]


def matrix_from_values(values):
    """Rebuild a 4x4 matrix from a list of 16 floats."""
    if not values or len(values) != 16:
        return None
    return Matrix((
        values[0:4],
        values[4:8],
        values[8:12],
        values[12:16],
    ))


def get_object_matrix_values(obj):
    """Get the object's current matrix_basis as flattened values."""
    return flatten_matrix(obj.matrix_basis)


def set_object_matrix_values(obj, values):
    """Set the object's matrix_basis from flattened values."""
    matrix = matrix_from_values(values)
    if matrix:
        obj.matrix_basis = matrix


def transforms_match(values_a, values_b, tolerance=1e-6):
    """Compare two transform value lists with tolerance."""
    if not values_a or not values_b or len(values_a) != 16 or len(values_b) != 16:
        return False
    return all(abs(a - b) <= tolerance for a, b in zip(values_a, values_b))


# --- Object Property Helpers ---
def has_bake_explode_data(obj):
    """Check if object has Bake Explode transform data stored."""
    return obj.get(ORIGINAL_TRANSFORM_PROP) is not None


def get_stored_transform(obj, prop_name):
    """Get stored transform values from object property."""
    values = obj.get(prop_name)
    if not values or len(values) != 16:
        return None
    return list(values)


def clear_bake_explode_data(obj):
    """Remove all Bake Explode properties from an object."""
    for prop in [ORIGINAL_TRANSFORM_PROP, ALTERNATE_TRANSFORM_PROP]:
        if prop in obj:
            try:
                del obj[prop]
            except:
                pass


def store_original_transform(obj):
    """Store the object's current transform as the original."""
    obj[ORIGINAL_TRANSFORM_PROP] = get_object_matrix_values(obj)


def store_alternate_transform(obj):
    """Store the object's current transform as the alternate."""
    obj[ALTERNATE_TRANSFORM_PROP] = get_object_matrix_values(obj)


# --- Linked Duplicate Helpers ---
def has_keeper_suffix(name):
    """Check if object name has the _visible suffix."""
    return name.casefold().endswith(KEEPER_SUFFIX)


def strip_keeper_suffix(name):
    """Remove _visible suffix from name if present."""
    if has_keeper_suffix(name):
        return name[:-len(KEEPER_SUFFIX)]
    return name


def get_stable_keeper(objects):
    """Get a deterministic keeper from a list of objects."""
    return min(
        objects,
        key=lambda obj: (
            obj.name.casefold(),
            tuple(round(v, 6) for v in obj.location),
        ),
    )


def apply_keeper_suffix(group_objects, keeper):
    """Apply _visible suffix to keeper, remove from others."""
    # First clean previous keeper tags from non-keepers
    for obj in group_objects:
        if obj != keeper and has_keeper_suffix(obj.name):
            obj.name = strip_keeper_suffix(obj.name)

    # Apply suffix to keeper
    if not has_keeper_suffix(keeper.name):
        keeper.name = strip_keeper_suffix(keeper.name) + KEEPER_SUFFIX


def resolve_keeper(group_objects, selected_objects):
    """Determine which object should be the keeper.

    Rules:
    1. If any object has _visible suffix, use the first one
    2. If exactly one object from group is selected, use that
    3. Otherwise use deterministic stable choice
    """
    tagged = [obj for obj in group_objects if has_keeper_suffix(obj.name)]
    if tagged:
        keeper = get_stable_keeper(tagged)
    else:
        sel_in_group = [o for o in group_objects if o in selected_objects]
        if len(sel_in_group) == 1:
            keeper = sel_in_group[0]
        else:
            keeper = get_stable_keeper(group_objects)

    apply_keeper_suffix(group_objects, keeper)
    return keeper


# --- Visualization State ---
draw_handler = None
visualization_arrows = []
visualization_objects = []  # List of (original_obj, ghost_obj) tuples
VISUALIZATION_COLLECTION = "BakeExplode_Viz"
GHOST_COLOR = (0.181089, 1.0, 0.29559, 0.4)  # Green with 40% opacity as specified
ARROW_COLOR = (0.181089, 1.0, 0.29559, 1.0)  # Solid green for arrows


def get_or_create_viz_collection():
    """Get or create the visualization collection."""
    if VISUALIZATION_COLLECTION in bpy.data.collections:
        return bpy.data.collections[VISUALIZATION_COLLECTION]

    collection = bpy.data.collections.new(VISUALIZATION_COLLECTION)
    bpy.context.scene.collection.children.link(collection)
    # Hide from renders
    collection.hide_render = True
    return collection


def remove_ghost_object(original_obj):
    """Remove the ghost object associated with an original object."""
    global visualization_objects
    ghost_name = f"{original_obj.name}_ghost"
    ghost_obj = bpy.data.objects.get(ghost_name)

    if ghost_obj:
        # Remove from collection
        for col in ghost_obj.users_collection:
            col.objects.unlink(ghost_obj)
        # Delete the object
        bpy.data.objects.remove(ghost_obj, do_unlink=True)

    # Update tracking list
    visualization_objects = [
        (orig, ghost) for orig, ghost in visualization_objects
        if orig != original_obj
    ]


def create_ghost_object(original_obj):
    """Create a ghost mesh at the stored location."""
    global visualization_objects

    # Remove existing ghost if any
    remove_ghost_object(original_obj)

    # Get stored transform
    stored_values = get_stored_transform(original_obj, ORIGINAL_TRANSFORM_PROP)
    if not stored_values:
        return None

    # Check current transform to see if object has moved
    current_values = get_object_matrix_values(original_obj)
    if transforms_match(current_values, stored_values):
        # Object is at stored position, no need for ghost
        return None

    collection = get_or_create_viz_collection()

    # Create ghost object name
    ghost_name = f"{original_obj.name}_ghost"
    ghost_obj = bpy.data.objects.get(ghost_name)

    if ghost_obj:
        # Update existing
        ghost_obj.matrix_world = matrix_from_values(stored_values)
    else:
        # Create new linked duplicate using direct data operations
        # This avoids selection issues with hidden objects
        ghost_mesh = original_obj.data
        ghost_obj = bpy.data.objects.new(ghost_name, ghost_mesh)

        # Set ghost appearance (40% opacity green)
        ghost_obj.color = GHOST_COLOR

        # Make non-selectable
        ghost_obj.hide_select = True

        # Link to viz collection
        collection.objects.link(ghost_obj)

        # Set to stored position
        ghost_obj.matrix_world = matrix_from_values(stored_values)

    # Track this pair
    visualization_objects.append((original_obj, ghost_obj))

    return ghost_obj


def update_visualization_objects():
    """Update or create ghost objects for all stored transforms."""
    global visualization_objects

    # Clear existing
    clear_ghost_objects()

    # Get all objects with stored transforms
    for obj in bpy.data.objects:
        if obj.type == 'MESH' and has_bake_explode_data(obj):
            create_ghost_object(obj)


def clear_ghost_objects():
    """Remove all ghost visualization objects."""
    global visualization_objects

    for original_obj, ghost_obj in visualization_objects:
        if ghost_obj and ghost_obj.name in bpy.data.objects:
            # Remove from collection
            for col in ghost_obj.users_collection:
                col.objects.unlink(ghost_obj)
            # Delete the object
            bpy.data.objects.remove(ghost_obj, do_unlink=True)

    visualization_objects = []

    # Remove empty collection
    if VISUALIZATION_COLLECTION in bpy.data.collections:
        collection = bpy.data.collections[VISUALIZATION_COLLECTION]
        if len(collection.objects) == 0:
            # Unlink from all parent collections
            for scene in bpy.data.scenes:
                if collection.name in scene.collection.children:
                    scene.collection.children.unlink(collection)
            # Check if collection is now unused
            if collection.users == 0:
                bpy.data.collections.remove(collection)


def draw_arrow_2d(shader, from_pos, to_pos, color):
    """Draw a 2D line with arrow on the viewport."""
    try:
        from gpu_extras.batch import batch_for_shader
        import gpu
        import math

        # Create line vertices
        vertices = [from_pos, to_pos]

        # Create batch and draw
        batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": vertices})
        shader.bind()
        shader.uniform_float("color", color)
        batch.draw(shader)

        # Draw arrow tip (small triangle)
        direction = (to_pos[0] - from_pos[0], to_pos[1] - from_pos[1])
        length = math.sqrt(direction[0]**2 + direction[1]**2)

        if length > 0:
            # Normalize direction
            dir_norm = (direction[0] / length, direction[1] / length)

            # Perpendicular vector
            perp = (-dir_norm[1], dir_norm[0])

            # Arrow size
            arrow_size = 15

            # Arrow head points
            arrow_point_1 = (to_pos[0] - dir_norm[0] * arrow_size - perp[0] * arrow_size * 0.4,
                            to_pos[1] - dir_norm[1] * arrow_size - perp[1] * arrow_size * 0.4)
            arrow_point_2 = (to_pos[0] - dir_norm[0] * arrow_size + perp[0] * arrow_size * 0.4,
                            to_pos[1] - dir_norm[1] * arrow_size + perp[1] * arrow_size * 0.4)

            # Draw arrow head
            arrow_verts = [arrow_point_1, to_pos, arrow_point_2]
            batch = batch_for_shader(shader, 'TRIS', {"pos": arrow_verts})
            batch.draw(shader)
    except Exception as e:
        print(f"Warning: Error drawing arrow: {e}")


def update_arrow_positions(context):
    """Update arrow screen positions based on 3D world positions."""
    global visualization_arrows

    try:
        from bpy_extras import view3d_utils
        import mathutils

        # Get viewport and region
        space = context.space_data
        region = context.region

        if not space or not region or space.type != 'VIEW_3D':
            return

        # Update each arrow
        for arrow_data in visualization_arrows:
            if not arrow_data.get('enabled', True):
                continue

            original_obj = arrow_data.get('original_obj')
            ghost_obj = arrow_data.get('ghost_obj')

        if original_obj and ghost_obj and original_obj.name in bpy.data.objects:
            # Get world positions - REVERSED: from current position to stored (ghost) position
            from_pos_3d = mathutils.Vector(original_obj.matrix_world.translation)  # Start at current position
            to_pos_3d = mathutils.Vector(ghost_obj.matrix_world.translation)      # End at stored (ghost) position

            # Project to screen
            from_2d = view3d_utils.location_3d_to_region_2d(region, space.region_3d, from_pos_3d)
            to_2d = view3d_utils.location_3d_to_region_2d(region, space.region_3d, to_pos_3d)

            if from_2d and to_2d:
                arrow_data['from_2d'] = from_2d
                arrow_data['to_2d'] = to_2d
    except Exception as e:
        pass


def draw_viewport_arrows(scene):
    """Draw all visualization arrows on the viewport."""
    global visualization_arrows

    # Exit early if no arrows to draw
    if not visualization_arrows:
        return

    try:
        from gpu_extras.batch import batch_for_shader
        import gpu

        # Get context
        context = bpy.context
        if not context.space_data or context.space_data.type != 'VIEW_3D':
            return

        # Update arrow positions EVERY FRAME
        update_arrow_positions(context)

        # Setup shader
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')

        # Draw each arrow
        for arrow_data in visualization_arrows:
            if not arrow_data.get('enabled', True):
                continue

            from_2d = arrow_data['from_2d']
            to_2d = arrow_data['to_2d']
            color = arrow_data.get('color', ARROW_COLOR)

            draw_arrow_2d(shader, from_2d, to_2d, color)
    except Exception as e:
        pass


def register_draw_handler(context):
    """Register the viewport draw handler."""
    global draw_handler

    try:
        # Remove existing handler if any
        if draw_handler:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(draw_handler, 'WINDOW')
            except Exception as e:
                print(f"Warning: Could not remove old handler: {e}")
            finally:
                draw_handler = None

        # Add new handler
        draw_handler = bpy.types.SpaceView3D.draw_handler_add(
            draw_viewport_arrows, (context.scene,), 'WINDOW', 'POST_PIXEL'
        )
        print(f"Draw handler registered: {draw_handler}")
    except Exception as e:
        print(f"Warning: Could not register draw handler: {e}")
        draw_handler = None


def unregister_draw_handler():
    """Unregister the viewport draw handler and clear arrow data."""
    global draw_handler, visualization_arrows

    try:
        # Remove handler FIRST before clearing data
        if draw_handler:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(draw_handler, 'WINDOW')
            except Exception as e:
                print(f"Warning: Error removing draw handler: {e}")
            finally:
                draw_handler = None

        # Clear arrow data after handler is removed
        visualization_arrows.clear()

        # Force immediate redraw of all 3D viewports
        screen = bpy.context.screen
        if screen:
            for area in screen.areas:
                if area.type == 'VIEW_3D':
                    for region in area.regions:
                        if region.type == 'WINDOW':
                            region.tag_redraw()
    except Exception as e:
        print(f"Warning: Could not unregister draw handler: {e}")
        draw_handler = None


def refresh_visualization_arrows():
    """Rebuild arrow data for all ghost objects."""
    global visualization_arrows, visualization_objects

    # Clear existing arrow data
    visualization_arrows = []

    # Create arrow data for each ghost/original pair
    for original_obj, ghost_obj in visualization_objects:
        if original_obj.name in bpy.data.objects and ghost_obj.name in bpy.data.objects:
            arrow_data = {
                'original_obj': original_obj,
                'ghost_obj': ghost_obj,
                'from_2d': (0, 0),
                'to_2d': (0, 0),
                'color': ARROW_COLOR,
                'enabled': True
            }
            visualization_arrows.append(arrow_data)


def cleanup_all_visualization():
    """Emergency cleanup function to remove all visualization data and handlers."""
    global draw_handler, visualization_arrows

    # Clear ghost objects
    clear_ghost_objects()

    # Clear arrow data
    visualization_arrows = []

    # Unregister draw handler
    try:
        if draw_handler:
            bpy.types.SpaceView3D.draw_handler_remove(draw_handler, 'WINDOW')
    except:
        pass
    draw_handler = None

    # Force viewport redraw
    try:
        for area in bpy.context.screen.areas:
            if area.type == 'VIEW_3D':
                for region in area.regions:
                    if region.type == 'WINDOW':
                        region.tag_redraw()
    except:
        pass

    print("Bake Explode visualization cleaned up")


# --- Settings PropertyGroup ---
class BakeExplodeSettings(PropertyGroup):
    """Scene-level settings for Bake Explode (UI state only)."""
    show_stored_objects: BoolProperty(name="Show Stored Objects", default=False)
    visualization_enabled: BoolProperty(name="Visualization", default=False)
    previous_color_type: StringProperty(name="Previous Color Type", default="")


# --- Operators ---
class BAKE_EXPLODE_OT_Store(Operator):
    """Store the current transform of selected mesh objects."""
    bl_idname = "bake_explode.store"
    bl_label = "Store"
    bl_description = "Store the current transform (location, rotation, scale) of selected objects"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        objs = [obj for obj in context.selected_objects if obj.type == 'MESH']

        if not objs:
            self.report({'WARNING'}, "No mesh objects selected")
            return {'CANCELLED'}

        stored_count = 0
        for obj in objs:
            # Store current transform as original
            store_original_transform(obj)
            # Clear any existing alternate transform
            if ALTERNATE_TRANSFORM_PROP in obj:
                del obj[ALTERNATE_TRANSFORM_PROP]
            stored_count += 1

        self.report({'INFO'}, f"Stored transforms for {stored_count} object(s)")
        return {'FINISHED'}


class BAKE_EXPLODE_OT_Reset(Operator):
    """Toggle between stored and current transforms."""
    bl_idname = "bake_explode.reset"
    bl_label = "Reset"
    bl_description = "Toggle between original stored position and alternate position"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        # Get selected mesh objects, or all objects with stored data if nothing selected
        selected = [obj for obj in context.selected_objects if obj.type == 'MESH']

        if selected:
            # Only affect selected objects that have stored data
            objs_to_process = [obj for obj in selected if has_bake_explode_data(obj)]
        else:
            # Affect all objects with stored data
            objs_to_process = [obj for obj in bpy.data.objects if obj.type == 'MESH' and has_bake_explode_data(obj)]

        if not objs_to_process:
            self.report({'WARNING'}, "No stored transforms found")
            return {'CANCELLED'}

        toggled_count = 0
        for obj in objs_to_process:
            original = get_stored_transform(obj, ORIGINAL_TRANSFORM_PROP)
            alternate = get_stored_transform(obj, ALTERNATE_TRANSFORM_PROP)
            current = get_object_matrix_values(obj)

            if original is None:
                continue

            # Check if currently at original position
            at_original = transforms_match(current, original)

            if at_original:
                # Currently at original, restore alternate if exists
                if alternate is not None:
                    set_object_matrix_values(obj, alternate)
                    toggled_count += 1
            else:
                # Currently not at original, save current as alternate and restore original
                store_alternate_transform(obj)
                set_object_matrix_values(obj, original)
                toggled_count += 1

        self.report({'INFO'}, f"Toggled {toggled_count} object(s)")
        return {'FINISHED'}


class BAKE_EXPLODE_OT_Clear(Operator):
    """Clear stored transforms for selected objects or all objects."""
    bl_idname = "bake_explode.clear"
    bl_label = "Clear"
    bl_description = "Clear stored transforms for selected objects, or all if nothing is selected"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        selected = [obj for obj in context.selected_objects if obj.type == 'MESH']

        if selected:
            # Clear only selected objects
            objs_to_clear = [obj for obj in selected if has_bake_explode_data(obj)]
        else:
            # Clear all objects with stored data
            objs_to_clear = [obj for obj in bpy.data.objects if obj.type == 'MESH' and has_bake_explode_data(obj)]

        if not objs_to_clear:
            self.report({'WARNING'}, "Nothing to clear")
            return {'CANCELLED'}

        cleared_count = 0
        for obj in objs_to_clear:
            clear_bake_explode_data(obj)
            cleared_count += 1

        self.report({'INFO'}, f"Cleared {cleared_count} object(s)")
        return {'FINISHED'}


class BAKE_EXPLODE_OT_HideLinkedDuplicates(Operator):
    """Toggle visibility of linked duplicate objects, keeping one visible."""
    bl_idname = "bake_explode.hide_linked_duplicates"
    bl_label = "Hide Same Data"
    bl_description = "Hide linked duplicates, keeping one visible object marked with _visible suffix. Automatically stores transforms for all affected objects"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        selected = context.selected_objects

        if not selected:
            self.report({'WARNING'}, "Select at least one object")
            return {'CANCELLED'}

        # Group objects by shared mesh data
        data_groups = defaultdict(list)
        for obj in context.scene.objects:
            if obj.type == 'MESH' and obj.data:
                data_groups[obj.data].append(obj)

        processed_any = False
        stored_count = 0

        for data, objs in data_groups.items():
            # Skip single objects (no duplicates)
            if len(objs) <= 1:
                continue

            # Only process groups where at least one object is selected
            sel_in_group = [o for o in objs if o in selected]
            if not sel_in_group:
                continue

            processed_any = True

            # Determine keeper
            keeper = resolve_keeper(objs, selected)

            # Store transforms for ALL objects in this group (including keeper and those to be hidden)
            for obj in objs:
                # Store current transform as original
                store_original_transform(obj)
                # Clear any existing alternate transform
                if ALTERNATE_TRANSFORM_PROP in obj:
                    del obj[ALTERNATE_TRANSFORM_PROP]
                stored_count += 1

            # Ensure keeper is visible
            keeper.hide_set(False)

            # Hide others using eye icon
            for obj in objs:
                if obj != keeper:
                    obj.hide_set(True)

        if not processed_any:
            self.report({'WARNING'}, "No linked duplicates in selection")
            return {'CANCELLED'}

        self.report({'INFO'}, f"Linked duplicates hidden, stored transforms for {stored_count} object(s)")
        return {'FINISHED'}


class BAKE_EXPLODE_OT_UnhideLinkedDuplicates(Operator):
    """Unhide all objects that were hidden by Hide Same Data."""
    bl_idname = "bake_explode.unhide_linked_duplicates"
    bl_label = "Unhide All"
    bl_description = "Unhide all linked duplicates"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        selected = context.selected_objects

        if not selected:
            self.report({'WARNING'}, "Select at least one object")
            return {'CANCELLED'}

        # Group objects by shared mesh data
        data_groups = defaultdict(list)
        for obj in context.scene.objects:
            if obj.type == 'MESH' and obj.data:
                data_groups[obj.data].append(obj)

        processed_any = False

        for data, objs in data_groups.items():
            # Skip single objects
            if len(objs) <= 1:
                continue

            # Only process groups where at least one object is selected
            sel_in_group = [o for o in objs if o in selected]
            if not sel_in_group:
                continue

            processed_any = True

            # Unhide all objects in this group
            for obj in objs:
                obj.hide_set(False)

            # NOTE: We intentionally keep the _visible suffix on the keeper
            # so that subsequent hide operations will select the same keeper.
            # Do NOT remove the suffix here.

        if not processed_any:
            self.report({'WARNING'}, "No linked duplicates in selection")
            return {'CANCELLED'}

        self.report({'INFO'}, "Linked duplicates unhidden")
        return {'FINISHED'}


class BAKE_EXPLODE_OT_ToggleVisualization(Operator):
    """Toggle visualization of stored locations with ghost meshes and arrows."""
    bl_idname = "bake_explode.toggle_visualization"
    bl_label = "Toggle Visualization"
    bl_description = "Toggle visualization showing ghost meshes at stored locations and arrows"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        be = context.scene.bake_explode_settings
        be.visualization_enabled = not be.visualization_enabled

        if be.visualization_enabled:
            # Enable visualization - save current color type and switch to VERTEX
            space = context.space_data
            if space and space.type == 'VIEW_3D':
                # Save previous color type
                be.previous_color_type = space.shading.color_type
                # Switch to VERTEX color mode to show ghost colors
                space.shading.color_type = 'VERTEX'

            register_draw_handler(context)
            update_visualization_objects()
            refresh_visualization_arrows()
            self.report({'INFO'}, "Visualization enabled")
        else:
            # Disable visualization - restore previous color type
            space = context.space_data
            if space and space.type == 'VIEW_3D' and be.previous_color_type:
                try:
                    space.shading.color_type = be.previous_color_type
                except:
                    pass

            unregister_draw_handler()
            clear_ghost_objects()
            self.report({'INFO'}, "Visualization disabled")

        return {'FINISHED'}


# --- END OF FILE operators_bake_explode.py ---
