"""The focused bake view (UI plan phase 4): Setup, Maps and Results as freely accessible
sections in the Painter sidebar's *Bake* tab, with Bake, progress, Stop and Back to Painting
always in reach. The bake operators, checks, map methods and the queue are unchanged; only
the presentation moves out of the tall popover.

Map states are only what is known: *Missing*, *Up to date*, *Out of date*, *Baking*,
*Failed* and *Not checked* when a map has no provenance record to compare against.
"""
import bpy

from ..core.project_manager import active_set
from . import editor

CATEGORY = "Bake"

# (settings key, map kind, full name, method, group)
MAPS = (
    ("normal", "NORMAL", "Normal", "Cycles projection", "cycles"),
    ("ao", "AO", "Ambient Occlusion", "Cycles occlusion", "cycles"),
    ("curvature", "CURVATURE", "Curvature (edge set)", "Cycles Bevel node, four reaches", "cycles"),
    ("bevel_normal", "BEVEL_NORMAL", "Bevel Normal", "Cycles Bevel node, tangent space", "cycles"),
    ("bevel_mask", "BEVEL_MASK", "Bevel Mask", "Cycles Bevel node, edge amount", "cycles"),
    ("mean_curvature", "MEAN_CURVATURE", "Mean Curvature (signed)", "Evaluated target, radius", "target"),
    ("position", "POSITION", "Position", "Evaluated target UVs", "target"),
    ("world_normal", "WORLD_NORMAL", "World Space Normal", "Evaluated target UVs", "target"),
    ("object_normal", "OBJECT_NORMAL", "Object Space Normal", "Evaluated target UVs", "target"),
    ("material_id", "MATERIAL_ID", "Material ID", "Evaluated target UVs", "target"),
    ("object_id", "OBJECT_ID", "Object ID", "Evaluated target UVs", "target"),
    ("uv_island", "UV_ISLAND", "UV Island ID", "Evaluated target UVs", "target"),
    ("uv_border", "UV_BORDER", "UV Border", "Evaluated target UVs", "target"),
    ("thickness", "THICKNESS", "Thickness", "Inward rays against the target", "target"),
    ("selection", "SELECTION", "Selected Faces", "Face selection of the targets", "target"),
)
KIND_TO_KEY = {kind: key for key, kind, *_ in MAPS}
STATE_ICONS = {"Up to date": "CHECKMARK", "Out of date": "TIME", "Missing": "BLANK1", "Baking": "SORTTIME",
               "Failed": "CANCEL", "Not checked": "QUESTION"}


def is_bake_view(context):
    return editor.is_area(getattr(context, "area", None))


def running_set(ts):
    from ..baking.jobs import OP_OT_bake_queue
    return OP_OT_bake_queue.running_set_id == ts.id


def map_state(ts, kind):
    """(label, detail) of one map, from what is actually known."""
    from ..baking import history
    from ..geometry.provenance import status
    live = history.running(ts) if running_set(ts) else None
    if live is not None and live["current"] == kind:
        return "Baking", ""
    record = history.last(ts)
    prop = next((m for m in ts.baked_maps if m.kind == kind), None)
    if record and record.get("failed") == kind and (prop is None or not prop.image or kind not in record["done"]):
        return "Failed", record.get("error", "")
    if prop is None or not prop.image:
        return "Missing", ""
    text = status(ts, prop)
    if text == "Ready":
        return "Up to date", ""
    if text.startswith("Unverified"):
        return "Not checked", "no inputs record to compare; rebake to verify"
    if text.startswith("Stale"):
        return "Out of date", text.split("·", 1)[1].strip() if "·" in text else "inputs changed"
    return "Not checked", text


def recommended(ts):
    """Map kinds the material's generators and baked-map masks read."""
    from ..generators.base import REQUIRED_MAPS
    kinds = set()
    for layer in ts.layers:
        for mask in layer.masks:
            if mask.source == "GENERATOR":
                if mask.generator in {"EDGE_WEAR", "CHIPS"} and mask.edge_source == "LIVE":
                    continue
                kinds.update(REQUIRED_MAPS.get(mask.generator, ()))
            elif mask.source == "BAKED":
                kinds.add(mask.map_type)
    return sorted(kinds & set(KIND_TO_KEY))


def model_dimensions(ts):
    """World-space size of the assigned objects together, or None without any."""
    from mathutils import Vector
    corners = []
    for ref in ts.assignments:
        obj = ref.object
        if obj is None:
            continue
        corners.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
    if not corners:
        return None
    low = Vector((min(c.x for c in corners), min(c.y for c in corners), min(c.z for c in corners)))
    high = Vector((max(c.x for c in corners), max(c.y for c in corners), max(c.z for c in corners)))
    return high - low


def _dimensions_text(ts):
    size = model_dimensions(ts)
    if size is None:
        return "no assigned object"
    return " × ".join(f"{value:.2f}" for value in size) + " m"


def uses_cycles(settings):
    return settings.normal or settings.ao or settings.curvature or settings.bevel_normal or settings.bevel_mask


def draw_actions(layout, context, ts):
    """Bake, progress and a truthful stop; the same row in every section."""
    from ..baking import history
    box = layout.box()
    row = box.row(align=True)
    if running_set(ts):
        live = history.running(ts) or {}
        done, kinds = len(live.get("done", [])), len(live.get("kinds", []))
        current = (live.get("current") or "").replace("_", " ").title()
        row.label(text=f"Baking {current}… {done}/{kinds} maps", icon="SORTTIME")
        row.operator("open_painter.bake_stop", text="Stop After This Map", icon="CANCEL")
        box.label(text="A Cycles map that is baking runs to its end; Esc also stops between maps", icon="INFO")
    else:
        row.operator("open_painter.bake_queue", text="Bake Selected Maps", icon="RENDER_STILL")
        row.operator("open_painter.show_task", text="", icon="LOOP_BACK").task = "PAINTER"
    return box


def draw_setup(layout, context, ts, *, compact=False):
    settings = ts.bake
    layout.use_property_split = True
    layout.use_property_decorate = False
    objects = [ref.object for ref in ts.assignments if ref.object]
    col = layout.column(align=True)
    names = ", ".join(sorted({obj.name for obj in objects})[:4]) + (" …" if len({o.name for o in objects}) > 4 else "")
    col.label(text=f"Targets: {names}" if objects else "Targets: none assigned", icon="MESH_DATA" if objects else "ERROR")
    col.label(text=f"Model size {_dimensions_text(ts)}", icon="DRIVER_DISTANCE")
    layout.prop(ts, "resolution", text="Resolution")
    layout.prop(settings, "margin")
    # Sources only matter when maps project from high-poly objects.
    layout.prop(settings, "selected_to_active", text="Bake from High Poly")
    if settings.selected_to_active:
        head, body = layout.panel("painter_bake_sources", default_closed=False)
        head.label(text=f"High-Poly Sources ({len(settings.high_objects)})", icon="MESH_MONKEY")
        if body is not None:
            body.template_list("OP_UL_high", "", settings, "high_objects", settings, "active_high", rows=2)
            row = body.row(align=True)
            row.operator("open_painter.bake", text="Add Selected", icon="ADD").action = "ADD_HIGH"
            row.operator("open_painter.bake", text="", icon="REMOVE").action = "REMOVE_HIGH"
            body.prop(settings, "source_mode")
            body.operator("open_painter.bake", text="Match Sources by Name", icon="LINKED").action = "MATCH_SOURCES"
            if settings.high_objects:
                ref = settings.high_objects[min(settings.active_high, len(settings.high_objects) - 1)]
                body.prop(ref, "target")
                if settings.cage_mode == "CUSTOM" and len({r.object for r in ts.assignments if r.object}) > 1:
                    body.prop(ref, "cage")
        head, body = layout.panel("painter_bake_projection", default_closed=False)
        head.label(text="Projection", icon="ORIENTATION_NORMAL")
        if body is not None:
            body.prop(settings, "cage_mode")
            size = model_dimensions(ts)
            hint = f" (model {max(size):.2f} m)" if size is not None else ""
            body.prop(settings, "ray_distance", text="Ray Distance" + hint)
            if settings.cage_mode == "AUTO":
                body.prop(settings, "separate_cage_distance")
                if settings.separate_cage_distance:
                    body.prop(settings, "cage_extrusion", text="Cage Extrusion" + hint)
            if settings.cage_mode == "CUSTOM":
                if len({ref.object for ref in ts.assignments if ref.object}) == 1:
                    body.prop(settings, "cage_object")
                else:
                    body.label(text="Choose each cage on its high-poly source row", icon="INFO")
            body.operator("open_painter.bake", text="Create Editable Cages", icon="MESH_CUBE").action = "CREATE_CAGES"
            body.label(text="Editable cages show the projection volume in the viewport", icon="INFO")
    else:
        layout.label(text="Maps come from the targets alone; no sources or cage needed", icon="INFO")
    if uses_cycles(settings):
        head, body = layout.panel("painter_bake_cycles", default_closed=True)
        head.label(text="Cycles Sampling", icon="RENDER_STILL")
        if body is not None:
            body.prop(settings, "samples")
            body.prop(settings, "device")
            body.prop(settings, "supersample")
    from .bake_corrections import draw_corrections
    draw_corrections(layout, context, ts)


def draw_maps(layout, context, ts):
    settings = ts.bake
    wanted = recommended(ts)
    row = layout.row(align=True)
    if wanted:
        row.label(text="The material reads: " + ", ".join(k.replace("_", " ").title() for k in wanted), icon="INFO")
        row.operator("open_painter.bake", text="Select", icon="CHECKMARK").action = "SELECT_RECOMMENDED"
    else:
        row.label(text="No layer reads a mesh map yet", icon="INFO")
    layout.use_property_split = False
    for key, kind, name, method, group in MAPS:
        head, body = layout.panel(f"painter_bake_map_{key}", default_closed=True)
        head.prop(settings, key, text="")
        head.label(text=name)
        state, detail = map_state(ts, kind)
        head.label(text=state, icon=STATE_ICONS[state])
        if body is None:
            continue
        body.use_property_split = True
        body.use_property_decorate = False
        body.label(text=f"Method: {method}", icon="BLANK1")
        if detail:
            body.label(text=detail, icon="INFO")
        if key == "ao":
            body.prop(settings, "ao_scope")
            body.prop(settings, "ao_distance", text="AO Distance")
        elif key == "curvature":
            body.prop(settings, "mean_curvature_distance", text="Curvature Radius")
            body.prop(settings, "auto_edge_rebake")
            body.label(text="Cycles Bevel shader; bakes thin, normal, wide and extreme reaches; Edge Size blends them live", icon="INFO")
            body.operator("open_painter.bake", text="Rebake Edge Map Set", icon="IMAGE_DATA").action = "CURVATURE"
        elif key in {"bevel_normal", "bevel_mask"}:
            body.prop(settings, "bevel_radius")
            body.prop(settings, "bevel_samples")
            body.label(text="Engine independent: baked with Cycles, read by EEVEE and export", icon="INFO")
        elif key == "mean_curvature":
            body.prop(settings, "mean_curvature_distance", text="Curvature Radius")
            body.label(text="Signed and smooth; not the edge set the generators read", icon="INFO")
        elif key == "uv_border":
            body.prop(settings, "uv_border_width")
        elif key == "thickness":
            body.prop(settings, "thickness_resolution")
            body.prop(settings, "thickness_samples")
            body.prop(settings, "thickness_max_distance")
            body.label(text="Closed surfaces; sampled inward rays", icon="INFO")
        elif key == "selection":
            body.label(text="Face count must survive the modifiers", icon="INFO")
        elif key in {"normal"}:
            body.label(text="Tangent-space normal from the high-poly sources (or the target itself)", icon="INFO")
        else:
            body.label(text="Uses the evaluated target UVs", icon="INFO")


def draw_results(layout, context, ts):
    from ..baking import history
    from .baking_panel import draw_bake_doctor
    record = history.last(ts)
    box = layout.box()
    if running_set(ts):
        live = history.running(ts) or {}
        box.label(text="Baking… previous maps stay until each replacement lands", icon="SORTTIME")
        if live.get("done"):
            box.label(text="Landed: " + ", ".join(k.replace("_", " ").title() for k in live["done"]), icon="CHECKMARK")
    elif record is None:
        box.label(text="No bake in this session yet", icon="INFO")
    else:
        done = ", ".join(k.replace("_", " ").title() for k in record["done"]) or "nothing"
        box.label(text=f"Last bake {record['when']} · landed: {done}", icon="CHECKMARK" if record["done"] else "INFO")
        if record["failed"]:
            box.label(text=f"{record['failed'].replace('_', ' ').title()} failed: {record['error']}"[:120], icon="CANCEL")
            box.label(text="The previous map of that kind is kept", icon="INFO")
        elif record["error"]:
            box.label(text=record["error"][:120], icon="CANCEL")
        if record["stopped"]:
            box.label(text="Stopped by request; " + (", ".join(k.replace("_", " ").title() for k in record["skipped"]) or "no map") + " not baked", icon="INFO")
    column = layout.column(align=True)
    shown = False
    for prop in ts.baked_maps:
        if prop.kind not in KIND_TO_KEY and not prop.kind.startswith("CURVATURE"):
            continue
        state, detail = map_state(ts, prop.kind)
        row = column.row(align=True)
        row.label(text=prop.kind.replace("_", " ").title(), icon="IMAGE_DATA" if prop.image else "BLANK1")
        row.label(text=state, icon=STATE_ICONS[state])
        if prop.image is not None:
            op = row.operator("open_painter.bake_view_map", text="", icon="IMAGE_DATA")
            op.kind = prop.kind
        shown = True
    if not shown:
        column.label(text="No maps registered yet", icon="BLANK1")
    draw_bake_doctor(layout, context, ts)
    row = layout.row(align=True)
    row.operator("open_painter.bake", text="Add Baked Normal and AO Layer", icon="MATERIAL").action = "USE_MAPS"
    if any(prop.kind == "SELECTION" and prop.image for prop in ts.baked_maps):
        row.operator("open_painter.bake", text="Reselect Baked Faces", icon="RESTRICT_SELECT_OFF").action = "RESELECT_MAP"


def draw_compact(layout, context, ts):
    """Every section in one column: the legacy panel and the header popover."""
    draw_actions(layout, context, ts)
    head, body = layout.panel("painter_bake_compact_setup", default_closed=False)
    head.label(text="Setup", icon="PREFERENCES")
    if body is not None:
        draw_setup(body, context, ts, compact=True)
    head, body = layout.panel("painter_bake_compact_maps", default_closed=False)
    head.label(text="Maps", icon="IMAGE_DATA")
    if body is not None:
        draw_maps(body, context, ts)
    head, body = layout.panel("painter_bake_compact_results", default_closed=True)
    head.label(text="Results", icon="CHECKMARK")
    if body is not None:
        draw_results(body, context, ts)


class OP_OT_show_task(bpy.types.Operator):
    """Switch the Painter sidebar to a task tab (Bake, Export, Assistant) or back to painting."""
    bl_idname = "open_painter.show_task"
    bl_label = "Show Task"
    bl_options = {"REGISTER"}
    task: bpy.props.EnumProperty(items=[("PAINTER", "Painting", "Layers and inspector"),
                                        ("BAKE", "Bake Mesh Maps", "Setup, maps and results"),
                                        ("EXPORT", "Export", "Contents, scope, destination and output preview"),
                                        ("ASSISTANT", "Material Assistant", "The running instruction and its progress")],
                                 default="PAINTER")

    @classmethod
    def description(cls, context, properties):
        return {"PAINTER": "Back to painting: layers and the inspector",
                "BAKE": "Open the bake view: setup, maps and results",
                "EXPORT": "Open the export view",
                "ASSISTANT": "Open the Material Assistant: what Codex is doing and every step so far"}[properties.task]

    def execute(self, context):
        from . import selection
        area = selection.session_area(context)
        if area is None:
            self.report({"ERROR"}, "Open Painter in an area first.")
            return {"CANCELLED"}
        space = area.spaces.active
        if hasattr(space, "show_region_ui"):
            space.show_region_ui = True
        region = next((r for r in area.regions if r.type == "UI"), None)
        category = {"PAINTER": "Painter", "BAKE": CATEGORY, "EXPORT": "Export", "ASSISTANT": "Assistant"}[self.task]
        if region is not None:
            try:
                with context.temp_override(area=area, region=region):
                    region.active_panel_category = category
            except (AttributeError, TypeError):
                self.report({"WARNING"}, f"Choose the {category} tab in the sidebar")
            region.tag_redraw()
        area.tag_redraw()
        return {"FINISHED"}


class OP_OT_bake_view_map(bpy.types.Operator):
    bl_idname = "open_painter.bake_view_map"
    bl_label = "View Baked Map"
    bl_description = "Show this baked map in the Painter's 2D view (a source image, not a composite)"
    bl_options = {"REGISTER"}
    kind: bpy.props.StringProperty()

    def execute(self, context):
        ts = active_set(context)
        prop = next((m for m in ts.baked_maps if m.kind == self.kind), None) if ts else None
        if prop is None or prop.image is None:
            self.report({"ERROR"}, "That map has no image.")
            return {"CANCELLED"}
        from . import selection
        area = selection.session_area(context)
        if area is None:
            return {"CANCELLED"}
        if area.type != "IMAGE_EDITOR":
            with context.temp_override(area=area):
                bpy.ops.open_painter.native_view(view="IMAGE_EDITOR")
        area.spaces.active.image = prop.image
        area.spaces.active.ui_mode = "VIEW"
        area.tag_redraw()
        return {"FINISHED"}


class BakePanel:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = CATEGORY

    @classmethod
    def poll(cls, context):
        return is_bake_view(context) and active_set(context) is not None


class OP_PT_bake_setup(BakePanel, bpy.types.Panel):
    bl_label = "Setup"
    bl_order = 0

    def draw_header(self, context):
        self.layout.label(text="", icon="RENDER_STILL")

    def draw(self, context):
        ts = active_set(context)
        row = self.layout.row(align=True)
        row.label(text=f"Bake Mesh Maps · {ts.name}", icon="TEXTURE")
        row.operator("open_painter.show_task", text="Back to Painting", icon="LOOP_BACK").task = "PAINTER"
        draw_actions(self.layout, context, ts)
        draw_setup(self.layout, context, ts)


class OP_PT_bake_maps(BakePanel, bpy.types.Panel):
    bl_label = "Maps"
    bl_order = 1

    def draw(self, context):
        draw_maps(self.layout, context, active_set(context))


class OP_PT_bake_results(BakePanel, bpy.types.Panel):
    bl_label = "Results"
    bl_order = 2

    def draw(self, context):
        ts = active_set(context)
        draw_results(self.layout, context, ts)
        draw_actions(self.layout, context, ts)


IMAGE_CLASSES = tuple(
    type(cls.__name__.replace("bake_", "image_bake_"), (BakePanel, bpy.types.Panel),
         {"__module__": __name__, "bl_space_type": "IMAGE_EDITOR", "bl_label": cls.bl_label,
          "bl_order": cls.bl_order, "bl_idname": cls.__name__.replace("bake_", "image_bake_"), "draw": cls.draw,
          **({"draw_header": cls.draw_header} if "draw_header" in cls.__dict__ else {})})
    for cls in (OP_PT_bake_setup, OP_PT_bake_maps, OP_PT_bake_results))

CLASSES = (OP_OT_show_task, OP_OT_bake_view_map, OP_PT_bake_setup, OP_PT_bake_maps, OP_PT_bake_results) + IMAGE_CLASSES
