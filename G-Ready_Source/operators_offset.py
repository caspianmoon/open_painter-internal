# --- START OF FILE operators_offset.py ---

# gready/operators_offset.py

import math
import traceback

import blf
import bmesh
import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty
from bpy.types import Operator
from bpy_extras.view3d_utils import region_2d_to_location_3d
from mathutils import Matrix, Vector

from . import shared_state


SOLVER_ITEMS = (
    ("FAST", "Fast", "Use Blender's fast boolean mesh solver"),
    ("EXACT", "Exact", "Use Blender's exact boolean mesh solver"),
)

EPSILON = 1e-8


def set_blf_size(font_id, size):
    try:
        blf.size(font_id, size)
    except TypeError:
        blf.size(font_id, size, bpy.context.preferences.system.dpi or 72)


def draw_shadowed_text(font_id, x, y, text, size, color=(1.0, 1.0, 1.0, 1.0)):
    set_blf_size(font_id, size)
    blf.color(font_id, 0.0, 0.0, 0.0, 0.62)
    blf.position(font_id, x + 1, y - 1, 0)
    blf.draw(font_id, text)
    blf.color(font_id, *color)
    blf.position(font_id, x, y, 0)
    blf.draw(font_id, text)


def is_navigation_event(event):
    if event.type == "MIDDLEMOUSE" or event.type.startswith("NDOF"):
        return True
    return event.alt and event.type in {"LEFTMOUSE", "RIGHTMOUSE"} and event.value == "PRESS"


class OffsetError(Exception):
    pass


def popup_message(message, title="Offset", icon="ERROR"):
    def draw(self, _context):
        if isinstance(message, (list, tuple)):
            for line in message:
                self.layout.label(text=str(line))
        else:
            self.layout.label(text=str(message))

    bpy.context.window_manager.popup_menu(draw, title=title, icon=icon)


def average_location(coords):
    if not coords:
        return Vector()
    total = Vector()
    for co in coords:
        total += co
    return total / len(coords)


def rotate_list(items, amount):
    if not items:
        return items

    amount = amount % len(items)
    if amount:
        items[:] = items[amount:] + items[:amount]
    return items


def collect_selected_edge_sequences(bm):
    selected_edges = [e for e in bm.edges if e.select and e.is_valid]
    if not selected_edges:
        raise OffsetError("Select one or more connected edges first.")
    if any(not edge.link_faces for edge in selected_edges):
        raise OffsetError("Offset needs selected edges that belong to mesh faces.")

    remaining = set(selected_edges)
    sequences = []

    while remaining:
        seed = next(iter(remaining))
        island_edges = set()
        stack = [seed]

        while stack:
            edge = stack.pop()
            if edge in island_edges:
                continue

            island_edges.add(edge)
            for vert in edge.verts:
                for linked in vert.link_edges:
                    if linked in remaining and linked not in island_edges:
                        stack.append(linked)

        remaining -= island_edges

        verts = {v for edge in island_edges for v in edge.verts}
        adjacency = {v: sorted([e for e in v.link_edges if e in island_edges], key=lambda edge: edge.index) for v in verts}
        degrees = {v: len(edges) for v, edges in adjacency.items()}

        if any(degree > 2 for degree in degrees.values()):
            raise OffsetError("Offset needs simple edge chains or loops. Branched selections are not supported.")

        endpoints = sorted([v for v, degree in degrees.items() if degree == 1], key=lambda vert: vert.index)

        if len(endpoints) == 2:
            cyclic = False
            start = endpoints[0]
        elif not endpoints and all(degree == 2 for degree in degrees.values()):
            cyclic = True
            start = min(verts, key=lambda vert: vert.index)
        else:
            raise OffsetError("Selection must be an open chain with two ends or a closed loop.")

        sequence = [start]
        visited_edges = set()
        current = start
        previous_edge = None

        while True:
            candidates = [edge for edge in adjacency[current] if edge != previous_edge and edge not in visited_edges]
            if not candidates:
                break

            edge = candidates[0]
            visited_edges.add(edge)
            next_vert = edge.other_vert(current)

            if cyclic and next_vert == start:
                break

            sequence.append(next_vert)
            previous_edge = edge
            current = next_vert

        if len(visited_edges) != len(island_edges):
            raise OffsetError("Could not walk the selected edge island cleanly.")

        if len(sequence) < 2:
            raise OffsetError("Offset needs at least one valid selected edge.")

        sequences.append((sequence, cyclic))

    return sequences


def selected_sequences_max_dimension(sequences):
    max_dim = 0.0
    for sequence, _cyclic in sequences:
        coords = [v.co for v in sequence]
        if not coords:
            continue
        xs = [co.x for co in coords]
        ys = [co.y for co in coords]
        zs = [co.z for co in coords]
        max_dim = max(max_dim, max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))
    return max_dim


def create_circle_section(radius, count, tilt_degrees):
    count = max(3, int(count))
    rotation = Matrix.Rotation(math.radians(tilt_degrees), 4, "Z")
    coords = []
    normals = []

    for idx in range(count):
        angle = idx * (2.0 * math.pi / count)
        co = rotation @ Vector((math.sin(angle) * radius, math.cos(angle) * radius, 0.0))
        coords.append(co)
        normals.append(co.normalized() * 0.05 if co.length > EPSILON else Vector((0.0, 0.0, 0.05)))

    return coords, normals


def resample_centerline(coords, cyclic, segments=None, shift=0):
    if len(coords) < 2:
        return coords[:]

    if not segments:
        segments = len(coords) - 1
    segments = int(segments)

    if not cyclic and shift != 0:
        shift = 0

    cumulative = [0.0]
    total_length = 0.0

    for idx in range(len(coords) - 1):
        total_length += (coords[idx + 1] - coords[idx]).length
        cumulative.append(total_length)

    if cyclic:
        total_length += (coords[0] - coords[-1]).length
        cumulative.append(total_length)
        segments += 1

    if total_length <= EPSILON:
        return coords[:]

    if cyclic:
        result = [None] * segments
    else:
        result = [None] * (segments + 1)
        result[0] = coords[0].copy()
        result[-1] = coords[-1].copy()

    start_index = 0

    for idx in range(0, segments - 1 + (1 if cyclic else 0)):
        desired_raw = (
            (idx + 1 - (1 if cyclic else 0)) / segments * total_length +
            shift * total_length / segments
        )

        if desired_raw > total_length:
            desired_length = desired_raw - total_length
        elif desired_raw < 0.0:
            desired_length = total_length + desired_raw
        else:
            desired_length = desired_raw

        for segment_idx in range(start_index, len(coords) + 1):
            if cumulative[segment_idx] > desired_length:
                break
        else:
            segment_idx = len(coords)

        local_distance = desired_length - cumulative[segment_idx - 1]
        start = coords[segment_idx - 1]
        end = coords[segment_idx] if segment_idx < len(coords) else coords[0]
        direction = end - start

        if direction.length <= EPSILON:
            sampled = start.copy()
        else:
            sampled = start + direction.normalized() * local_distance

        result[idx + 1 - (1 if cyclic else 0)] = sampled

    return result


def smooth_centerline(coords, cyclic, iterations):
    result = [co.copy() for co in coords]

    for _idx in range(max(0, int(iterations))):
        smoothed = []
        for idx, co in enumerate(result):
            if idx == 0:
                smoothed.append(average_location([result[-1], result[1]]) if cyclic else co.copy())
            elif idx == len(result) - 1:
                smoothed.append(average_location([result[-2], result[0]]) if cyclic else co.copy())
            else:
                smoothed.append(average_location([result[idx - 1], result[idx + 1]]))
        result = smoothed

    return result


def optimize_centerline(coords, cyclic, limit_degrees):
    if len(coords) < 3:
        return coords[:]

    optimized = []

    for idx, co in enumerate(coords):
        if idx == 0:
            if not cyclic:
                optimized.append(co)
                continue
            prev_co = coords[-1]
            next_co = coords[1]
        elif idx == len(coords) - 1:
            if not cyclic:
                optimized.append(co)
                continue
            prev_co = coords[-2]
            next_co = coords[0]
        else:
            prev_co = coords[idx - 1]
            next_co = coords[idx + 1]

        vec_prev = prev_co - co
        vec_next = next_co - co
        if vec_prev.length <= EPSILON or vec_next.length <= EPSILON:
            optimized.append(co)
            continue

        angle = math.degrees(vec_prev.angle(vec_next))
        if angle < limit_degrees:
            optimized.append(co)

    minimum = 3 if cyclic else 2
    return optimized if len(optimized) >= minimum else coords[:]


def create_centerline_coords(sequence, cyclic, resample, factor, smooth, iterations, optimize, angle):
    coords = [v.co.copy() for v in sequence]

    if resample:
        coords = resample_centerline(coords, cyclic, segments=max(1, int(len(coords) * factor)))

    if smooth:
        coords = smooth_centerline(coords, cyclic, iterations)

    if optimize:
        coords = optimize_centerline(coords, cyclic, angle)

    return coords


def extend_open_centerline(coords, cyclic, extend):
    if cyclic or len(coords) < 2 or extend <= 0.0:
        return coords[:]

    extended = [co.copy() for co in coords]
    start_vec = extended[0] - extended[1]
    end_vec = extended[-1] - extended[-2]

    if start_vec.length > EPSILON:
        extended[0] = extended[0] + start_vec.normalized() * extend
    if end_vec.length > EPSILON:
        extended[-1] = extended[-1] + end_vec.normalized() * extend

    return extended


def rotation_from_direction(direction):
    vec = direction.copy()
    if vec.length <= EPSILON:
        vec = Vector((0.0, 0.0, 1.0))
    vec.normalize()

    if (vec - Vector((0.0, 0.0, 1.0))).length <= EPSILON:
        tangent = Vector((1.0, 0.0, 0.0))
        binormal = Vector((0.0, 1.0, 0.0))
    elif (vec - Vector((0.0, 0.0, -1.0))).length <= EPSILON:
        tangent = Vector((-1.0, 0.0, 0.0))
        binormal = Vector((0.0, 1.0, 0.0))
    else:
        tangent = Vector((0.0, 0.0, 1.0)).cross(vec).normalized()
        binormal = tangent.cross(-vec).normalized()

    matrix = Matrix.Identity(4)
    matrix[0].xyz = tangent
    matrix[1].xyz = binormal
    matrix[2].xyz = vec
    return matrix.transposed().to_3x3()


def create_pipe_rings(coords, cyclic, circle_coords, circle_normals):
    rings = []

    for idx, co in enumerate(coords):
        previous_co = coords[-1] if idx == 0 else coords[idx - 1]
        next_co = coords[0] if idx == len(coords) - 1 else coords[idx + 1]

        if cyclic or idx not in {0, len(coords) - 1}:
            prev_vec = co - previous_co
            next_vec = next_co - co
            direction = Vector()
            if prev_vec.length > EPSILON:
                direction += prev_vec.normalized()
            if next_vec.length > EPSILON:
                direction += next_vec.normalized()
        elif idx == 0:
            direction = next_co - co
        else:
            direction = co - previous_co

        rotation = rotation_from_direction(direction)
        ring = []
        for section_co, section_normal in zip(circle_coords, circle_normals):
            ring.append((co + (rotation @ section_co), rotation @ section_normal))
        rings.append(ring)

    return rings


def make_temp_layers(bm):
    edge_layer = bm.edges.layers.string.get("GReadyOffsetEdges")
    face_layer = bm.faces.layers.int.get("GReadyOffsetFaces")

    if edge_layer:
        bm.edges.layers.string.remove(edge_layer)
    if face_layer:
        bm.faces.layers.int.remove(face_layer)

    return bm.edges.layers.string.new("GReadyOffsetEdges"), bm.faces.layers.int.new("GReadyOffsetFaces")


def remove_temp_layers(bm, edge_layer, face_layer):
    try:
        if edge_layer:
            bm.edges.layers.string.remove(edge_layer)
    except Exception:
        pass

    try:
        if face_layer:
            bm.faces.layers.int.remove(face_layer)
    except Exception:
        pass


class OBJECT_OT_gready_offset(Operator):
    bl_idname = "object.gready_offset"
    bl_label = "Offset"
    bl_description = "Create an offset cut around selected edge chains"
    bl_options = {"REGISTER", "UNDO"}

    width: FloatProperty(name="Width", default=0.1, min=0.00001, step=0.01, precision=5)

    def update_resample(self, _context):
        if not self.resample and self.solver == "FAST":
            self.solver = "EXACT"

    resample: BoolProperty(name="Resample", default=True, update=update_resample)
    factor: FloatProperty(name="Factor", default=1.0, min=0.5)
    smooth: BoolProperty(name="Smooth", default=False)
    iterations: IntProperty(name="Iterations", default=1, min=1)
    optimize: BoolProperty(name="Optimize", default=True)
    angle: FloatProperty(name="Angle", default=180.0, min=0.0, max=180.0)
    extend: FloatProperty(name="Extend", default=0.2, min=0.0, step=0.1)
    override: BoolProperty(name="Spread", default=False)
    rails: IntProperty(name="Precision", default=18, min=7)
    tilt: FloatProperty(name="Wiggle", default=1.0)
    shift: BoolProperty(name="Shift", default=True)
    solver: EnumProperty(name="Solver", items=SOLVER_ITEMS, default="FAST")
    use_self: BoolProperty(
        name="Use Self",
        description="Use self intersection for the Exact solver. This is slower but can help on difficult cuts",
        default=False,
    )
    shade_smooth: BoolProperty(default=False, options={"HIDDEN"})
    mark_sharp: BoolProperty(default=False, options={"HIDDEN"})
    all_cyclic: BoolProperty(default=False, options={"HIDDEN"})
    try_exact_solver: BoolProperty(default=False, options={"HIDDEN"})
    try_use_self: BoolProperty(default=False, options={"HIDDEN"})
    interactive: BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})
    sensitivity: FloatProperty(default=1.0, options={"HIDDEN", "SKIP_SAVE"})

    obj = None
    init_bm = None
    last_mouse_x = 0
    last_mouse_y = 0
    region_offset_x = 0
    region_offset_y = 0
    hud_x = 0
    hud_y = 0
    zoom_factor = 0.001
    passthrough = False
    _handle_draw_2d = None

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return bool(obj and obj.type == "MESH" and context.mode == "EDIT_MESH")

    def draw(self, _context):
        layout = self.layout
        column = layout.column(align=True)

        column.prop(self, "width")

        if not self.all_cyclic:
            split = column.split(factor=0.7, align=True)
            split.prop(self, "extend")
            split.prop(self, "override", toggle=True)

        column.separator()

        row = column.row(align=True)
        row.prop(self, "resample", toggle=True)
        factor_row = row.row(align=True)
        factor_row.active = self.resample
        factor_row.prop(self, "factor")

        row = column.row(align=True)
        row.prop(self, "smooth", toggle=True)
        smooth_row = row.row(align=True)
        smooth_row.active = self.smooth
        smooth_row.prop(self, "iterations")

        row = column.row(align=True)
        row.prop(self, "optimize", toggle=True)
        angle_row = row.row(align=True)
        angle_row.active = self.optimize
        angle_row.prop(self, "angle")

        if self.try_exact_solver:
            row = column.row(align=True)
            row.alignment = "CENTER"
            row.alert = True
            row.label(text="Try the Exact solver", icon="INFO")

        row = column.row(align=True)
        row.prop(self, "solver", expand=True)

        if self.solver == "EXACT":
            split = column.split(align=True)
            if self.try_use_self:
                warning = split.row(align=True)
                warning.alignment = "RIGHT"
                warning.alert = True
                warning.label(text="Try Use Self", icon="INFO")
            else:
                split.separator()
            split.prop(self, "use_self", text="Use Self", toggle=True)

        column.separator()

        row = column.row(align=True)
        row.prop(self, "rails")
        row.prop(self, "tilt")
        row.prop(self, "shift", toggle=True)

    def invoke(self, context, event):
        self.obj = context.active_object

        try:
            bm = bmesh.from_edit_mesh(self.obj.data)
            sequences = collect_selected_edge_sequences(bm)
        except OffsetError as exc:
            popup_message(str(exc), title="Offset")
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            popup_message(f"Could not read selected edges: {exc}", title="Offset")
            return {"CANCELLED"}

        max_dim = selected_sequences_max_dimension(sequences)
        if max_dim > EPSILON:
            self.width = max(min(self.width, max_dim / 5.0), max_dim / 10.0)

        if self.interactive:
            bmesh.update_edit_mesh(self.obj.data)
            self._store_snapshot(context)
            self._init_cursor(event)
            self.zoom_factor = self._calculate_zoom_factor(context)

            if not self._evaluate_interactive(context, show_errors=True):
                self._restore_snapshot(context)
                self.finish(context)
                return {"CANCELLED"}

            self._add_draw_handler(context)
            self._set_header(context)
            context.window_manager.modal_handler_add(self)
            if context.area:
                context.area.tag_redraw()
            return {"RUNNING_MODAL"}

        return self.execute(context)

    def execute(self, context):
        ok = self._run_offset(context, show_errors=True)
        return {"FINISHED"} if ok else {"CANCELLED"}

    def modal(self, context, event):
        if not context.area:
            self._restore_snapshot(context)
            self.finish(context)
            self.report({"WARNING"}, "Context area lost, cancelled Offset.")
            return {"CANCELLED"}

        context.area.tag_redraw()

        if is_navigation_event(event):
            self.passthrough = True
            return {"PASS_THROUGH"}

        if event.type in {"WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            if self._change_sampling(context, 1 if event.type == "WHEELUPMOUSE" else -1, event):
                self._set_header(context)
                return {"RUNNING_MODAL"}

            self._restore_snapshot(context)
            self.finish(context)
            return {"CANCELLED"}

        if event.type == "MOUSEMOVE":
            self._wrap_cursor(context, event)
            self._update_hud_location(event)

            if self.passthrough:
                self.passthrough = False
            else:
                delta = event.mouse_x - self.last_mouse_x
                if delta:
                    divisor = 100.0 if event.shift else 1.0 if event.ctrl else 10.0
                    self.width = max(0.00001, self.width + (delta / divisor) * self.zoom_factor * self.sensitivity)
                    if not self._evaluate_interactive(context, show_errors=False):
                        self._restore_snapshot(context)
                        self.finish(context)
                        return {"CANCELLED"}

        elif event.type in {"LEFTMOUSE", "SPACE"} and event.value == "PRESS":
            self.finish(context)
            self.report({"INFO"}, f"Offset applied: width {self.width:.5f}")
            return {"FINISHED"}

        elif event.type in {"RIGHTMOUSE", "ESC"}:
            self._restore_snapshot(context)
            self.finish(context)
            self.report({"INFO"}, "Offset cancelled.")
            return {"CANCELLED"}

        self.last_mouse_x = event.mouse_x
        self.last_mouse_y = event.mouse_y
        self._set_header(context)

        return {"RUNNING_MODAL"}

    def _change_sampling(self, context, direction, event):
        if not self.resample:
            self.resample = True

        step = 0.05 if event.shift else 0.25 if event.ctrl else 0.10
        self.factor = max(0.5, self.factor + (step * direction))

        if not self._evaluate_interactive(context, show_errors=False):
            self.report({"ERROR"}, "Offset preview failed while changing sampling.")
            return False

        if context.area:
            context.area.tag_redraw()

        return True

    def _run_offset(self, context, show_errors=True):
        self.try_exact_solver = False
        self.try_use_self = False

        obj = context.active_object
        if not obj or obj.type != "MESH" or context.mode != "EDIT_MESH":
            return False

        try:
            bm = bmesh.from_edit_mesh(obj.data)
            was_manifold = bool(bm.edges) and all(e.is_manifold for e in bm.edges)
            is_manifold = self.offset_cut(context, obj)

            if was_manifold and not is_manifold:
                if self.solver == "FAST":
                    self.try_exact_solver = True
                    if show_errors:
                        self.report({"WARNING"}, "Offset produced non-manifold geometry. Try the Exact solver.")
                elif not self.use_self:
                    self.try_use_self = True
                    if show_errors:
                        self.report({"WARNING"}, "Offset produced non-manifold geometry. Try Exact with Use Self.")

        except OffsetError as exc:
            if show_errors:
                popup_message(str(exc), title="Offset")
                self.report({"WARNING"}, str(exc))
            return False
        except Exception as exc:
            traceback.print_exc()
            if show_errors:
                popup_message(f"Offset failed: {exc}", title="Offset")
                self.report({"ERROR"}, f"Offset failed: {exc}")
            return False

        return True

    def _evaluate_interactive(self, context, show_errors=False):
        self._restore_snapshot(context)
        return self._run_offset(context, show_errors=show_errors)

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
            return max(self.width * 0.08, 0.0001)

    def _init_cursor(self, event):
        self.last_mouse_x = event.mouse_x
        self.last_mouse_y = event.mouse_y
        self.region_offset_x = event.mouse_x - event.mouse_region_x
        self.region_offset_y = event.mouse_y - event.mouse_region_y
        self.hud_x = event.mouse_region_x + 22
        self.hud_y = event.mouse_region_y + 22

    def _update_hud_location(self, event):
        self.hud_x = event.mouse_x - self.region_offset_x + 22
        self.hud_y = event.mouse_y - self.region_offset_y + 22

    def _wrap_cursor(self, context, event):
        if event.mouse_region_x <= 0:
            context.window.cursor_warp(context.region.width + self.region_offset_x - 10, event.mouse_y)
        elif event.mouse_region_x >= context.region.width - 1:
            context.window.cursor_warp(self.region_offset_x + 10, event.mouse_y)

    def _add_draw_handler(self, context):
        if self._handle_draw_2d is None:
            self._handle_draw_2d = bpy.types.SpaceView3D.draw_handler_add(self.draw_hud, (context,), "WINDOW", "POST_PIXEL")
            shared_state.offset_draw_handles.append(self._handle_draw_2d)

    def _remove_draw_handler(self):
        if not self._handle_draw_2d:
            return

        if self._handle_draw_2d in shared_state.offset_draw_handles:
            shared_state.offset_draw_handles.remove(self._handle_draw_2d)

        try:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle_draw_2d, "WINDOW")
        except ValueError:
            pass

        self._handle_draw_2d = None

    def _set_header(self, context):
        if context.area:
            context.area.header_text_set("Offset: Mouse L/R Width | Wheel Sampling | LMB/Space Apply | RMB/Esc Cancel")

    def _store_snapshot(self, context):
        self._free_snapshot()
        self.init_bm = bmesh.new()

        previous_mode = context.mode
        try:
            if context.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            self.init_bm.from_mesh(self.obj.data)
        finally:
            if previous_mode == "EDIT_MESH" and context.mode != "EDIT_MESH":
                try:
                    bpy.ops.object.mode_set(mode="EDIT")
                except Exception:
                    pass

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
        self._remove_draw_handler()
        if context.area:
            context.area.header_text_set(None)
            context.area.tag_redraw()
        self._free_snapshot()

    def draw_hud(self, _context):
        x = self.hud_x
        y = self.hud_y
        font_id = 0
        draw_shadowed_text(font_id, x, y + 66, "Offset", 16, (0.95, 0.98, 1.0, 1.0))
        draw_shadowed_text(font_id, x, y + 44, f"Width: {self.width:.5f}", 14, (0.38, 0.94, 1.0, 1.0))
        draw_shadowed_text(font_id, x, y + 24, f"Sampling: {self.factor:.2f}x", 14, (0.98, 0.74, 0.36, 1.0))
        draw_shadowed_text(
            font_id,
            x,
            y + 4,
            "Mouse L/R Width | Wheel Sampling | LMB/Space Apply | RMB/Esc Cancel",
            12,
            (0.78, 0.82, 0.86, 1.0),
        )

    def offset_cut(self, context, obj):
        mesh = obj.data
        bm = bmesh.from_edit_mesh(mesh)
        bm.normal_update()
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()

        sequences = collect_selected_edge_sequences(bm)
        selected_edges = [e for e in bm.edges if e.select and e.is_valid]
        reference_edge = selected_edges[0]
        reference_face = reference_edge.link_faces[0] if reference_edge.link_faces else None
        self.mark_sharp = not reference_edge.smooth
        self.shade_smooth = bool(reference_face and reference_face.smooth)
        self.all_cyclic = all(cyclic for _seq, cyclic in sequences)

        edge_layer, face_layer = make_temp_layers(bm)

        try:
            circle_coords, circle_normals = create_circle_section(self.width, self.rails, self.tilt)
            pipes = []
            all_pipe_faces = []

            for pipe_idx, (sequence, cyclic) in enumerate(sequences):
                coords = create_centerline_coords(
                    sequence,
                    cyclic,
                    self.resample,
                    self.factor,
                    self.smooth,
                    self.iterations,
                    self.optimize,
                    self.angle,
                )
                extended_coords = extend_open_centerline(coords, cyclic, self.extend)
                ring_coords = create_pipe_rings(extended_coords, cyclic, circle_coords, circle_normals)
                vert_rings = self.create_pipe_verts(bm, ring_coords, cyclic)
                pipe_faces = self.create_pipe_faces(
                    bm,
                    vert_rings,
                    cyclic,
                    edge_layer,
                    face_layer,
                    pipe_idx,
                    self.shift,
                    self.shade_smooth,
                )
                all_pipe_faces.extend(pipe_faces)
                pipes.append((coords, cyclic))

            if not all_pipe_faces:
                raise OffsetError("Could not create offset cutter geometry.")

            bmesh.ops.recalc_face_normals(bm, faces=all_pipe_faces)
            bmesh.update_edit_mesh(mesh)

            for pipe_idx in range(len(pipes)):
                self.boolean_pipe(bm, face_layer, pipe_idx)
                bm = bmesh.from_edit_mesh(mesh)
                bm.faces.ensure_lookup_table()
                bm.edges.ensure_lookup_table()
                bm.verts.ensure_lookup_table()

            bpy.ops.mesh.select_all(action="DESELECT")

            merge_verts = set()
            junk_edges = set()

            for pipe_idx, (coords, cyclic) in enumerate(pipes):
                pipe_faces = [f for f in bm.faces if f.is_valid and f[face_layer] == pipe_idx + 1]
                if not pipe_faces:
                    continue

                pipe_edges = {e for f in pipe_faces for e in f.edges if e.is_valid}
                extended = bmesh.ops.region_extend(bm, geom=pipe_faces, use_faces=True).get("geom", [])
                border_faces = {item for item in extended if isinstance(item, bmesh.types.BMFace) and item.is_valid}
                border_edges = {e for f in border_faces for e in f.edges if e.is_valid}
                border_verts = {v for f in border_faces for v in f.verts if v.is_valid}

                sweeps, non_sweep_edges, has_caps = self.get_sorted_sweep_edges(len(coords), pipe_edges, edge_layer, pipe_idx)
                end_rail_edges = self.set_end_sweeps(sweeps, border_verts, border_edges) if not cyclic and len(sweeps) > 2 else set()

                junk_edges.update(self.collect_junk_edges(non_sweep_edges, border_edges, border_verts, end_rail_edges))
                merge_verts.update(self.recreate_hard_edges(sweeps, cyclic, coords, border_verts, self.override))
                self.mark_end_sweep_edges_sharp(self.mark_sharp, cyclic, has_caps, border_edges, merge_verts)

            valid_junk = [e for e in junk_edges if e.is_valid]
            if valid_junk:
                bmesh.ops.dissolve_edges(bm, edges=valid_junk, use_verts=True)

            valid_merge = [v for v in merge_verts if v.is_valid]
            if valid_merge:
                bmesh.ops.remove_doubles(bm, verts=valid_merge, dist=0.00001)

            bm.select_flush(True)
            self.mark_selected_sharp(bm, self.mark_sharp)

            is_manifold = bool(bm.edges) and all(e.is_manifold for e in bm.edges)

        finally:
            remove_temp_layers(bm, edge_layer, face_layer)
            bmesh.update_edit_mesh(mesh)
            if context.area:
                context.area.tag_redraw()

        return is_manifold

    def create_pipe_verts(self, bm, ring_coords, cyclic):
        vert_rings = []

        for ring_idx, ring in enumerate(ring_coords):
            if ring_idx == len(ring_coords) - 1 and not cyclic:
                verts = [bm.verts.new(co) for co, _normal in ring]
                vert_rings.append((verts, 0))
                continue

            next_ring = ring_coords[0] if ring_idx == len(ring_coords) - 1 else ring_coords[ring_idx + 1]
            first_co, first_normal = ring[0]
            _ = first_co
            shift_amount = max(
                ((idx, first_normal.dot(normal)) for idx, (_co, normal) in enumerate(next_ring)),
                key=lambda item: item[1],
            )[0]

            verts = [bm.verts.new(co) for co, _normal in ring]
            vert_rings.append((verts, shift_amount))

        return vert_rings

    def create_pipe_faces(self, bm, vert_rings, cyclic, edge_layer, face_layer, pipe_idx, shift, smooth):
        pipe_faces = []

        for ring_idx, (verts, shift_amount) in enumerate(vert_rings):
            if cyclic:
                next_verts = vert_rings[0][0] if ring_idx == len(vert_rings) - 1 else vert_rings[ring_idx + 1][0]
            else:
                if ring_idx in {0, len(vert_rings) - 1}:
                    face = bm.faces.new(verts)
                    face[face_layer] = pipe_idx + 1
                    pipe_faces.append(face)
                    for edge in face.edges:
                        edge[edge_layer] = f"{pipe_idx}:{ring_idx}".encode()

                    if ring_idx == len(vert_rings) - 1:
                        continue

                next_verts = vert_rings[ring_idx + 1][0]

            if shift and shift_amount:
                next_verts = rotate_list(next_verts.copy(), shift_amount)

            rail_count = len(verts)
            for vert_idx, vert in enumerate(verts):
                next_idx = (vert_idx + 1) % rail_count
                face = bm.faces.new([vert, verts[next_idx], next_verts[next_idx], next_verts[vert_idx]])
                face.smooth = smooth
                face[face_layer] = pipe_idx + 1

                if cyclic or ring_idx > 0:
                    face.edges[0][edge_layer] = f"{pipe_idx}:{ring_idx}".encode()

                pipe_faces.append(face)

        return pipe_faces

    def boolean_pipe(self, bm, face_layer, pipe_idx):
        bpy.ops.mesh.select_all(action="DESELECT")

        for face in bm.faces:
            if face.is_valid and face[face_layer] == pipe_idx + 1:
                face.select_set(True)

        boolean_solver = "FLOAT" if self.solver == "FAST" and bpy.app.version >= (5, 0, 0) else self.solver
        bpy.ops.mesh.intersect_boolean(
            operation="DIFFERENCE",
            solver=boolean_solver,
            use_self=self.use_self if self.solver == "EXACT" else False,
        )

    def get_sorted_sweep_edges(self, sweep_count, edges, layer, pipe_idx):
        sweeps = [None] * sweep_count
        non_sweep_edges = set()

        for edge in edges:
            if not edge.is_valid:
                continue

            raw_value = edge[layer]
            tag = raw_value.decode() if raw_value else ""

            if tag:
                try:
                    tag_pipe, tag_sweep = tag.split(":", 1)
                    tag_pipe = int(tag_pipe)
                    tag_sweep = int(tag_sweep)
                except Exception:
                    non_sweep_edges.add(edge)
                    continue

                if tag_pipe == pipe_idx and 0 <= tag_sweep < sweep_count:
                    if sweeps[tag_sweep] is None:
                        sweeps[tag_sweep] = []
                    sweeps[tag_sweep].append(edge)
                else:
                    non_sweep_edges.add(edge)
            else:
                non_sweep_edges.add(edge)

        has_caps = bool(sweeps and sweeps[0] and sweeps[-1])
        return sweeps, non_sweep_edges, has_caps

    def set_end_sweeps(self, sweeps, border_verts, border_edges):
        end_rail_edges = set()

        def collect_from_neighbor(neighbor_sweep):
            collected = []
            if not neighbor_sweep:
                return collected

            for edge in neighbor_sweep:
                if not edge.is_valid or any(v in border_verts for v in edge.verts):
                    continue

                for loop in edge.link_loops:
                    start_loop = loop
                    current_loop = loop

                    while True:
                        current_loop = current_loop.link_loop_next
                        if current_loop.edge in border_edges:
                            collected.append(current_loop.edge)
                        else:
                            end_rail_edges.add(current_loop.edge)

                        if current_loop == start_loop:
                            break

            return collected

        start_sweep = collect_from_neighbor(sweeps[1] if len(sweeps) > 1 else None)
        if start_sweep:
            if sweeps[0] is None:
                sweeps[0] = start_sweep
            else:
                sweeps[0].extend(start_sweep)

        end_sweep = collect_from_neighbor(sweeps[-2] if len(sweeps) > 1 else None)
        if end_sweep:
            if sweeps[-1] is None:
                sweeps[-1] = end_sweep
            else:
                sweeps[-1].extend(end_sweep)

        return end_rail_edges

    def collect_junk_edges(self, non_sweep_edges, border_edges, border_verts, end_rail_edges):
        junk = set()
        protected = border_edges | end_rail_edges

        for edge in non_sweep_edges:
            if edge.is_valid and edge not in protected and any(v in border_verts for v in edge.verts):
                junk.add(edge)

        return junk

    def recreate_hard_edges(self, sweeps, cyclic, coords, border_verts, override):
        merge = set()

        for idx, co in enumerate(coords):
            sweep = sweeps[idx] if idx < len(sweeps) else None
            if not sweep:
                continue

            sweep_verts = {v for edge in sweep if edge.is_valid for v in edge.verts if v.is_valid}
            if cyclic or 0 < idx < len(sweeps) - 1 or override:
                sweep_verts -= border_verts

            for vert in sweep_verts:
                vert.co = co
                vert.select_set(True)
                merge.add(vert)

        return merge

    def mark_end_sweep_edges_sharp(self, mark_sharp, cyclic, has_caps, border_edges, merge_verts):
        if cyclic or has_caps or not mark_sharp:
            return

        merge_verts = {v for v in merge_verts if v.is_valid}
        for edge in border_edges:
            if edge.is_valid and any(v in merge_verts for v in edge.verts):
                edge.smooth = False

    def mark_selected_sharp(self, bm, mark_sharp):
        if not mark_sharp:
            return

        for edge in bm.edges:
            if edge.is_valid and edge.select:
                edge.smooth = False


# --- END OF FILE operators_offset.py ---
