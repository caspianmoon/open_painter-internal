# --- START OF FILE operators_cleanup.py ---

# bakeaddon/operators_cleanup.py

import traceback

import blf
import bmesh
import bpy
import gpu
from bpy.props import BoolProperty, EnumProperty, FloatProperty
from bpy.types import Operator
from bpy_extras.view3d_utils import region_2d_to_location_3d
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from . import shared_state


SIDE_ITEMS = (
    ("A", "A", "Keep side A fixed"),
    ("B", "B", "Keep side B fixed"),
)

COLOR_FIXED = (0.25, 1.0, 0.25, 1.0)
COLOR_UNMOVED = (1.0, 0.25, 0.25, 1.0)
COLOR_HUD = (1.0, 1.0, 1.0, 1.0)
COLOR_HUD_DIM = (0.72, 0.72, 0.72, 1.0)
COLOR_SHADOW = (0.0, 0.0, 0.0, 0.55)


class CleanupError(Exception):
    def __init__(self, message, title="Boolean Cleanup"):
        super().__init__(message)
        self.message = message
        self.title = title


def popup_message(message, title="Boolean Cleanup", icon="ERROR"):
    def draw(self, _context):
        self.layout.label(text=message)

    bpy.context.window_manager.popup_menu(draw, title=title, icon=icon)


def get_point_shader():
    names = ("POINT_UNIFORM_COLOR", "UNIFORM_COLOR")
    for name in names:
        try:
            return gpu.shader.from_builtin(name)
        except Exception:
            continue
    raise RuntimeError("No compatible point shader found.")


def set_blf_size(font_id, size):
    try:
        blf.size(font_id, size)
    except TypeError:
        blf.size(font_id, size, bpy.context.preferences.system.dpi or 72)


def average_location(coords):
    if not coords:
        return Vector()
    total = Vector()
    for co in coords:
        total += co
    return total / len(coords)


def distance_between_verts(v1, v2):
    return (v1.co - v2.co).length


def step_side(current, step):
    items = [item[0] for item in SIDE_ITEMS]
    idx = items.index(current)
    return items[(idx + step) % len(items)]


def is_navigation_event(event):
    if event.type == "MIDDLEMOUSE" or event.type.startswith("NDOF"):
        return True
    return event.alt and event.type in {"LEFTMOUSE", "RIGHTMOUSE"} and event.value == "PRESS"


def is_scroll_up(event):
    if event.value == "PRESS" and event.type in {"WHEELUPMOUSE", "UP_ARROW", "PLUS", "NUMPAD_PLUS", "ONE"}:
        return True
    return event.type == "TRACKPADPAN" and event.mouse_y > event.mouse_prev_y and event.mouse_y - event.mouse_prev_y >= 5


def is_scroll_down(event):
    if event.value == "PRESS" and event.type in {"WHEELDOWNMOUSE", "DOWN_ARROW", "MINUS", "NUMPAD_MINUS", "TWO"}:
        return True
    return event.type == "TRACKPADPAN" and event.mouse_y < event.mouse_prev_y and event.mouse_prev_y - event.mouse_y >= 5


def get_selected_cleanup_geometry(bm):
    """Return a validated single selected edge chain/loop."""
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    explicit_verts = {v for v in bm.verts if v.select and v.is_valid}
    explicit_edges = {e for e in bm.edges if e.select and e.is_valid}

    if not explicit_verts:
        raise CleanupError("Select a boolean edge loop or chain first.", "No Selection")

    if explicit_edges:
        edges = explicit_edges
        edge_verts = {v for e in edges for v in e.verts if v.is_valid}
        stray_verts = explicit_verts - edge_verts
        if stray_verts:
            raise CleanupError("Selection contains vertices outside the selected edge chain.", "Illegal Selection")
        verts = edge_verts
    else:
        verts = explicit_verts
        edges = {e for e in bm.edges if e.is_valid and e.verts[0] in verts and e.verts[1] in verts}
        for e in edges:
            e.select_set(True)

    if not edges:
        raise CleanupError("Select connected edges or vertices that form a boolean loop.", "No Edge Chain")

    if any(not e.is_manifold for e in edges):
        raise CleanupError("Selected edges must be manifold to determine cleanup sides.", "Non-Manifold Selection")

    adjacency = {v: set() for v in verts}
    for e in edges:
        v1, v2 = e.verts
        if v1 not in verts or v2 not in verts:
            raise CleanupError("Selected edge chain contains inconsistent vertices.", "Illegal Selection")
        adjacency[v1].add(v2)
        adjacency[v2].add(v1)

    if any(len(linked) > 2 for linked in adjacency.values()):
        raise CleanupError("Branched selections are not supported. Select one loop or chain.", "Illegal Selection")

    visited = set()
    stack = [next(iter(verts))]
    while stack:
        v = stack.pop()
        if v in visited:
            continue
        visited.add(v)
        stack.extend(adjacency[v] - visited)

    if visited != verts:
        raise CleanupError("Only one selected edge island is supported.", "Multiple Islands")

    ends = [v for v, linked in adjacency.items() if len(linked) == 1]
    if len(ends) == 0:
        cyclic = True
    elif len(ends) == 2:
        cyclic = False
    else:
        raise CleanupError("Selection must be either cyclic or have exactly two ends.", "Illegal Selection")

    return sorted(verts, key=lambda v: v.index), sorted(edges, key=lambda e: e.index), cyclic, sorted(ends, key=lambda v: v.index)


def find_start_loops(vert, edges):
    edge_set = set(edges)
    loops = []
    for edge in edges:
        if vert not in edge.verts:
            continue
        for loop in edge.link_loops:
            if loop.vert == vert and loop.edge in edge_set:
                loops.append(loop)
    return loops


def add_side_entry(side, entry_by_vert, vert, selected_edge=None, side_edge=None, face=None):
    entry = entry_by_vert.get(vert)
    if entry is None:
        entry = {"vert": vert, "selected_edge": selected_edge, "side_edges": [], "faces": []}
        entry_by_vert[vert] = entry
        side.append(entry)

    if selected_edge is not None:
        entry["selected_edge"] = selected_edge
    if side_edge is not None and side_edge not in entry["side_edges"]:
        entry["side_edges"].append(side_edge)
    if face is not None and face not in entry["faces"]:
        entry["faces"].append(face)

    return entry


def walk_cleanup_side(bm, selected_verts, selected_edges, start_vert, start_loop, end_vert=None, pass_edges=None, reverse=False, rotate=0):
    """Walk one side of the selected cleanup chain using face-loop topology."""
    pass_edges = pass_edges or set()
    selected_vert_set = set(selected_verts)
    selected_edge_set = set(selected_edges)
    travelled = {start_loop.edge}
    side = []
    entry_by_vert = {}

    start_side_edge = None
    if end_vert is not None and start_loop.link_loop_prev.edge not in selected_edge_set:
        start_side_edge = start_loop.link_loop_prev.edge

    add_side_entry(side, entry_by_vert, start_vert, selected_edge=start_loop.edge, side_edge=start_side_edge, face=start_loop.face)

    loop = start_loop
    max_steps = sum(len(face.loops) for face in bm.faces) + len(selected_edges) * 8 + 16

    for _step in range(max_steps):
        current_entry = entry_by_vert.get(loop.vert)
        if loop.vert == end_vert and current_entry is not None:
            break

        loop = loop.link_loop_next
        vert = loop.vert
        edge = loop.edge
        face = loop.face

        if not edge.is_manifold:
            raise CleanupError("A non-manifold edge near the selection blocks side detection.", "Non-Manifold Geometry")

        if edge in travelled:
            break

        if vert in selected_vert_set:
            if edge in selected_edge_set:
                travelled.add(edge)
                add_side_entry(side, entry_by_vert, vert, selected_edge=edge, face=face)
            else:
                add_side_entry(side, entry_by_vert, vert, side_edge=edge, face=face)

            if edge in pass_edges:
                loop = loop.link_loop_radial_next
        else:
            loop = loop.link_loop_prev.link_loop_radial_next
    else:
        raise CleanupError("Side walk did not terminate. Check the selected topology.", "Illegal Selection")

    if end_vert is not None and (not side or side[-1]["vert"] != end_vert):
        raise CleanupError("Could not walk the selected chain from one end to the other.", "Illegal Selection")

    if reverse:
        side.reverse()

    if rotate and side:
        rotate = rotate % len(side)
        side = side[-rotate:] + side[:-rotate]

    return side


def detect_cleanup_sides(bm, verts, edges, cyclic, ends):
    """Split the selected chain into two cleanup sides."""
    if cyclic:
        start_vert = verts[0]
        start_loops = find_start_loops(start_vert, edges)
        if len(start_loops) < 2:
            raise CleanupError("Could not find two sides for the cyclic selection.", "Illegal Selection")

        side_a = walk_cleanup_side(bm, verts, edges, start_vert, start_loops[0])
        side_b = walk_cleanup_side(bm, verts, edges, start_vert, start_loops[1], reverse=True, rotate=1)
    else:
        end_a, end_b = ends
        loops_a = find_start_loops(end_a, edges)
        loops_b = find_start_loops(end_b, edges)
        if not loops_a or not loops_b:
            raise CleanupError("Could not find side loops at the selected ends.", "Illegal Selection")

        side_a = walk_cleanup_side(bm, verts, edges, end_a, loops_a[0], end_vert=end_b)
        side_a_signature = side_signature(side_a)
        side_b = None
        last_error = None

        for loop in loops_b:
            try:
                candidate = walk_cleanup_side(bm, verts, edges, end_b, loop, end_vert=end_a, reverse=True)
            except CleanupError as err:
                last_error = err
                continue

            side_b = candidate
            if side_signature(candidate) != side_a_signature:
                break

        if side_b is None:
            if last_error:
                raise last_error
            raise CleanupError("Could not determine the opposite cleanup side.", "Illegal Selection")

    if not side_a or not side_b:
        raise CleanupError("Failed to determine both cleanup sides.", "Illegal Selection")

    return side_a, side_b


def build_cleanup_edge_graph(verts, edges, fixed_indices):
    graph = {
        v.index: {
            "fixed": v.index in fixed_indices,
            "connected": [],
            "children": [],
        }
        for v in verts
    }

    for edge in edges:
        v1, v2 = edge.verts
        if v1.index not in graph or v2.index not in graph:
            continue
        length = edge.calc_length()
        graph[v1.index]["connected"].append({"index": v2.index, "fixed": v2.index in fixed_indices, "distance": length})
        graph[v2.index]["connected"].append({"index": v1.index, "fixed": v1.index in fixed_indices, "distance": length})

    return graph


def replace_connection(graph, source_idx, old_idx, new_idx, is_fixed, distance):
    for connection in graph[source_idx]["connected"]:
        if connection["index"] == old_idx:
            connection["index"] = new_idx
            connection["fixed"] = is_fixed
            connection["distance"] = distance
            return


def collapse_cleanup_graph(bm, graph, threshold):
    fixed_coords = []
    unmoved_coords = []
    moved_indices = set()

    bm.verts.ensure_lookup_table()

    for vidx in sorted(graph):
        node = graph[vidx]
        vert = bm.verts[vidx]

        if node["fixed"]:
            fixed_coords.append(vert.co.copy())
            continue

        if len(node["connected"]) < 2:
            unmoved_coords.append(vert.co.copy())
            continue

        closest, furthest = sorted(node["connected"], key=lambda item: item["distance"])[:2]
        if closest["distance"] > threshold:
            unmoved_coords.append(vert.co.copy())
            continue

        closest_idx = closest["index"]
        furthest_idx = furthest["index"]
        closest_vert = bm.verts[closest_idx]
        furthest_vert = bm.verts[furthest_idx]

        vert.co = closest_vert.co
        moved_indices.add(vidx)

        for child_idx in list(node["children"]):
            bm.verts[child_idx].co = closest_vert.co
            moved_indices.add(child_idx)

        graph[closest_idx]["children"].append(vidx)
        graph[closest_idx]["children"].extend(node["children"])

        new_distance = distance_between_verts(closest_vert, furthest_vert)
        replace_connection(graph, closest_idx, vidx, furthest_idx, graph[furthest_idx]["fixed"], new_distance)
        replace_connection(graph, furthest_idx, vidx, closest_idx, graph[closest_idx]["fixed"], new_distance)

    return fixed_coords, unmoved_coords, moved_indices


def side_vertex_indices(side):
    indices = {entry["vert"].index for entry in side if entry["side_edges"]}
    if not indices:
        indices = {entry["vert"].index for entry in side}
    return indices


def side_faces(side):
    faces = []
    seen = set()
    for entry in side:
        for face in entry["faces"]:
            if face.is_valid and face not in seen:
                seen.add(face)
                faces.append(face)
    return faces


def side_signature(side):
    edge_ids = {edge.index for entry in side for edge in entry["side_edges"] if edge.is_valid}
    face_ids = {face.index for entry in side for face in entry["faces"] if face.is_valid}
    return edge_ids, face_ids


class OBJECT_OT_BooleanCleanup(Operator):
    """Merge boolean cleanup vertices using selected-loop topology."""

    bl_idname = "object.boolean_cleanup"
    bl_label = "Boolean Cleanup"
    bl_options = {"REGISTER", "UNDO"}

    side: EnumProperty(
        name="Side",
        items=SIDE_ITEMS,
        default="A",
        description="Side to keep fixed during cleanup",
    )
    threshold: FloatProperty(
        name="Threshold",
        default=0.0,
        min=0.0,
        step=0.1,
        precision=5,
        description="Maximum selected-edge distance to collapse",
    )
    flip: BoolProperty(
        name="Flip",
        default=False,
        description="Invert the fixed and moving vertices",
    )
    triangulate: BoolProperty(
        name="Triangulate",
        default=False,
        description="Triangulate faces on the side opposite the fixed side",
    )

    obj = None
    init_bm = None
    area = None
    _handle_draw_3d = None
    _handle_draw_2d = None
    fixed_coords = None
    unmoved_coords = None
    allow_threshold = True
    passthrough = False
    sharp_selected = False
    zoom_factor = 0.001
    last_mouse_x = 0
    last_mouse_y = 0
    region_offset_x = 0
    region_offset_y = 0
    hud_x = 0
    hud_y = 0
    _last_error = None

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if not (obj and obj.type == "MESH" and context.mode == "EDIT_MESH"):
            return False

        mode = tuple(context.tool_settings.mesh_select_mode)
        if mode not in {(True, False, False), (False, True, False)}:
            return False

        try:
            bm = bmesh.from_edit_mesh(obj.data)
            return any(v.select for v in bm.verts)
        except Exception:
            return False

    def draw(self, _context):
        layout = self.layout
        col = layout.column(align=True)
        col.prop(self, "side", expand=True)
        col.prop(self, "threshold")
        row = col.row(align=True)
        row.prop(self, "flip", toggle=True)
        row.prop(self, "triangulate", toggle=True)

    def invoke(self, context, event):
        self.obj = context.active_object
        self.obj.update_from_editmode()

        self.side = "A"
        self.threshold = 0.0
        self.flip = False
        self.allow_threshold = True
        self.passthrough = False
        self.fixed_coords = []
        self.unmoved_coords = []
        self._last_error = None

        self.init_bm = bmesh.new()
        self.init_bm.from_mesh(self.obj.data)

        self._init_cursor(event)
        self.zoom_factor = self._calculate_zoom_factor(context)

        if not self._evaluate_cleanup(context, restore_snapshot=True, show_errors=True):
            self._free_snapshot()
            return {"CANCELLED"}

        self.area = context.area
        self._add_draw_handlers(context)
        self._set_header(context)
        context.window_manager.modal_handler_add(self)
        context.area.tag_redraw()

        return {"RUNNING_MODAL"}

    def execute(self, context):
        self.obj = context.active_object
        self.fixed_coords = []
        self.unmoved_coords = []
        self._last_error = None

        try:
            ok = self._evaluate_cleanup(context, restore_snapshot=False, show_errors=True)
        except Exception as exc:
            traceback.print_exc()
            self.report({"ERROR"}, f"Boolean cleanup failed: {exc}")
            return {"CANCELLED"}

        return {"FINISHED"} if ok else {"CANCELLED"}

    def modal(self, context, event):
        if not context.area:
            self._restore_snapshot(context)
            self.finish(context)
            self.report({"WARNING"}, "Context area lost, cancelled Boolean Cleanup.")
            return {"CANCELLED"}

        context.area.tag_redraw()

        if event.type == "MOUSEMOVE":
            self._wrap_cursor(context, event)
            self._update_hud_location(event)

        should_recalculate = False

        if is_navigation_event(event):
            self.passthrough = True
            return {"PASS_THROUGH"}

        if event.type == "MOUSEMOVE" and self.allow_threshold:
            if self.passthrough:
                self.passthrough = False
            else:
                divisor = 100.0 if event.shift else 1.0 if event.ctrl else 10.0
                delta = event.mouse_x - self.last_mouse_x
                self.threshold = max(0.0, self.threshold + (delta / divisor) * self.zoom_factor)
                should_recalculate = True

        elif event.type == "W" and event.value == "PRESS":
            if event.alt:
                self.allow_threshold = False
                self.threshold = 0.0
            else:
                self.allow_threshold = not self.allow_threshold
            should_recalculate = True

        elif event.type == "T" and event.value == "PRESS":
            self.triangulate = not self.triangulate
            should_recalculate = True

        elif event.type == "F" and event.value == "PRESS":
            self.flip = not self.flip
            should_recalculate = True

        elif is_scroll_up(event):
            self.side = step_side(self.side, 1)
            should_recalculate = True

        elif is_scroll_down(event):
            self.side = step_side(self.side, -1)
            should_recalculate = True

        elif event.type in {"LEFTMOUSE", "SPACE"} and event.value == "PRESS":
            self.finish(context)
            self.report({"INFO"}, "Boolean cleanup applied.")
            return {"FINISHED"}

        elif event.type in {"RIGHTMOUSE", "ESC"}:
            self._restore_snapshot(context)
            self.finish(context)
            self.report({"INFO"}, "Boolean cleanup cancelled.")
            return {"CANCELLED"}

        if should_recalculate:
            if not self._evaluate_cleanup(context, restore_snapshot=True, show_errors=True):
                self._restore_snapshot(context)
                self.finish(context)
                return {"CANCELLED"}

        self.last_mouse_x = event.mouse_x
        self.last_mouse_y = event.mouse_y
        self._set_header(context)

        return {"RUNNING_MODAL"}

    def _evaluate_cleanup(self, context, restore_snapshot=False, show_errors=False):
        if not self.obj or self.obj.name not in bpy.data.objects:
            self._last_error = CleanupError("Active object no longer exists.", "Object Missing")
            return False

        mesh = self.obj.data
        previous_mode = context.mode

        try:
            if context.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")

            if restore_snapshot and self.init_bm is not None:
                self.init_bm.to_mesh(mesh)
                mesh.update()

            bm = bmesh.new()
            try:
                bm.from_mesh(mesh)
                bm.normal_update()
                bm.verts.ensure_lookup_table()
                bm.edges.ensure_lookup_table()
                bm.faces.ensure_lookup_table()

                verts, edges, cyclic, ends = get_selected_cleanup_geometry(bm)
                selected_indices = {v.index for v in verts}
                selected_edge_keys = {tuple(sorted((edge.verts[0].index, edge.verts[1].index))) for edge in edges}
                self.sharp_selected = any(not edge.smooth for edge in edges)

                side_a, side_b = detect_cleanup_sides(bm, verts, edges, cyclic, ends)

                fixed_indices = side_vertex_indices(side_a if self.side == "A" else side_b)
                if not cyclic and side_a:
                    fixed_indices.add(side_a[0]["vert"].index)
                    fixed_indices.add(side_a[-1]["vert"].index)

                if self.flip:
                    fixed_indices = selected_indices - fixed_indices

                graph = build_cleanup_edge_graph(verts, edges, fixed_indices)
                self.fixed_coords, self.unmoved_coords, moved_indices = collapse_cleanup_graph(bm, graph, self.threshold)

                if self.triangulate:
                    triangulate_faces = side_faces(side_b if self.side == "A" else side_a)
                    if triangulate_faces:
                        bmesh.ops.triangulate(bm, faces=triangulate_faces)

                bm.verts.ensure_lookup_table()
                merge_verts = [bm.verts[idx] for idx in selected_indices if idx < len(bm.verts) and bm.verts[idx].is_valid]
                if merge_verts:
                    bmesh.ops.remove_doubles(bm, verts=merge_verts, dist=0.00001)

                if self.triangulate and self.sharp_selected:
                    for edge in bm.edges:
                        edge_key = tuple(sorted((edge.verts[0].index, edge.verts[1].index)))
                        if edge.select or edge_key in selected_edge_keys:
                            edge.smooth = False

                bm.to_mesh(mesh)
                mesh.update()
                self._last_error = None
                return True

            except CleanupError as err:
                self._last_error = err
                if restore_snapshot and self.init_bm is not None:
                    self.init_bm.to_mesh(mesh)
                    mesh.update()
                if show_errors:
                    popup_message(err.message, title=err.title, icon="ERROR")
                    self.report({"WARNING"}, err.message)
                return False
            finally:
                bm.free()

        finally:
            if previous_mode == "EDIT_MESH" and context.mode != "EDIT_MESH":
                try:
                    bpy.ops.object.mode_set(mode="EDIT")
                except Exception:
                    pass

    def _calculate_zoom_factor(self, context):
        try:
            selected = [v.co for v in self.init_bm.verts if v.select]
            local_depth = average_location(selected)
            depth = self.obj.matrix_world @ local_depth
            center = Vector((context.region.width / 2.0, context.region.height / 2.0))
            offset = center + Vector((10.0, 0.0))
            center_3d = region_2d_to_location_3d(context.region, context.region_data, center, depth)
            offset_3d = region_2d_to_location_3d(context.region, context.region_data, offset, depth)
            world_factor = (center_3d - offset_3d).length
            local_vector = self.obj.matrix_world.to_3x3().inverted_safe() @ Vector((world_factor, 0.0, 0.0))
            return max(local_vector.length, 0.000001)
        except Exception:
            return 0.001

    def _init_cursor(self, event):
        self.last_mouse_x = event.mouse_x
        self.last_mouse_y = event.mouse_y
        self.region_offset_x = event.mouse_x - event.mouse_region_x
        self.region_offset_y = event.mouse_y - event.mouse_region_y
        self.hud_x = event.mouse_region_x + 20
        self.hud_y = event.mouse_region_y + 20

    def _update_hud_location(self, event):
        self.hud_x = event.mouse_x - self.region_offset_x + 20
        self.hud_y = event.mouse_y - self.region_offset_y + 20

    def _wrap_cursor(self, context, event):
        if event.mouse_region_x <= 0:
            context.window.cursor_warp(context.region.width + self.region_offset_x - 10, event.mouse_y)
        elif event.mouse_region_x >= context.region.width - 1:
            context.window.cursor_warp(self.region_offset_x + 10, event.mouse_y)

        if event.mouse_region_y <= 0:
            context.window.cursor_warp(event.mouse_x, context.region.height + self.region_offset_y - 10)
        elif event.mouse_region_y >= context.region.height - 1:
            context.window.cursor_warp(event.mouse_x, self.region_offset_y + 100)

    def _add_draw_handlers(self, context):
        if self._handle_draw_3d is None:
            self._handle_draw_3d = bpy.types.SpaceView3D.draw_handler_add(self.draw_view3d, (context,), "WINDOW", "POST_VIEW")
            shared_state.cleanup_draw_handles.append(self._handle_draw_3d)

        if self._handle_draw_2d is None:
            self._handle_draw_2d = bpy.types.SpaceView3D.draw_handler_add(self.draw_hud, (context,), "WINDOW", "POST_PIXEL")
            shared_state.cleanup_draw_handles.append(self._handle_draw_2d)

    def _remove_draw_handlers(self):
        for attr in ("_handle_draw_3d", "_handle_draw_2d"):
            handle = getattr(self, attr, None)
            if not handle:
                continue
            if handle in shared_state.cleanup_draw_handles:
                shared_state.cleanup_draw_handles.remove(handle)
            try:
                bpy.types.SpaceView3D.draw_handler_remove(handle, "WINDOW")
            except ValueError:
                pass
            setattr(self, attr, None)

    def _set_header(self, context):
        if context.area:
            context.area.header_text_set(
                "Boolean Cleanup: LMB/Space Apply | RMB/Esc Cancel | Wheel Side | W Threshold | T Triangulate | F Flip"
            )

    def _restore_snapshot(self, context):
        if not self.obj or self.init_bm is None:
            return

        previous_mode = context.mode
        try:
            if context.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            self.init_bm.to_mesh(self.obj.data)
            self.obj.data.update()
        finally:
            if previous_mode == "EDIT_MESH" and context.mode != "EDIT_MESH":
                try:
                    bpy.ops.object.mode_set(mode="EDIT")
                except Exception:
                    pass

    def _free_snapshot(self):
        if self.init_bm is not None:
            try:
                self.init_bm.free()
            except Exception:
                pass
            self.init_bm = None

    def finish(self, context):
        self._remove_draw_handlers()
        self._free_snapshot()
        if context.area:
            context.area.header_text_set(None)

    def draw_view3d(self, _context):
        if not self.obj or not self.obj.visible_get():
            return

        matrix = self.obj.matrix_world
        self._draw_points(self.fixed_coords or [], matrix, COLOR_FIXED, 7)
        self._draw_points(self.unmoved_coords or [], matrix, COLOR_UNMOVED, 9)

    def _draw_points(self, coords, matrix, color, size):
        if not coords:
            return

        try:
            shader = get_point_shader()
            positions = [matrix @ co for co in coords]
            gpu.state.depth_test_set("NONE")
            gpu.state.blend_set("ALPHA")
            gpu.state.point_size_set(size)
            shader.bind()
            shader.uniform_float("color", color)
            batch = batch_for_shader(shader, "POINTS", {"pos": positions})
            batch.draw(shader)
        finally:
            gpu.state.point_size_set(1.0)
            gpu.state.blend_set("NONE")
            gpu.state.depth_test_set("NONE")

    def draw_hud(self, _context):
        font_id = 0
        x = self.hud_x
        y = self.hud_y

        self._draw_text(font_id, x, y, "Boolean Cleanup", 18, COLOR_HUD)
        self._draw_text(font_id, x + 20, y - 28, f"Side: {self.side}", 13, COLOR_HUD)
        self._draw_text(
            font_id,
            x + 20,
            y - 48,
            f"Threshold: {self.threshold:.5f}",
            13,
            COLOR_HUD if self.allow_threshold else COLOR_HUD_DIM,
        )
        self._draw_text(font_id, x + 20, y - 68, f"Flip: {self.flip}", 13, COLOR_HUD)
        self._draw_text(font_id, x + 20, y - 88, f"Triangulate: {self.triangulate}", 13, COLOR_HUD)
        self._draw_text(
            font_id,
            x,
            y - 118,
            "LMB/Space Apply | RMB/Esc Cancel | Wheel Side | W Threshold | T Triangulate | F Flip",
            11,
            COLOR_HUD_DIM,
        )

    def _draw_text(self, font_id, x, y, text, size, color):
        set_blf_size(font_id, size)
        blf.color(font_id, *COLOR_SHADOW)
        blf.position(font_id, x + 1, y - 1, 0)
        blf.draw(font_id, text)
        blf.color(font_id, *color)
        blf.position(font_id, x, y, 0)
        blf.draw(font_id, text)


# --- END OF FILE operators_cleanup.py ---
