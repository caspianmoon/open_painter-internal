"""Material Aging panel (0.10.0): what it is, how old, where it lived, how it was used —
and what the simulation predicts, with art direction on top."""
import bpy

from ..aging import engine
from ..aging.environments import EVENT_LABELS
from ..core.project_manager import active_set, require_set
from .operators import SafeOperator

EXPOSURE_FIELDS = (("uv", "UV Exposure"), ("humidity", "Humidity"), ("rain", "Rain / Wetness"), ("temperature", "Temperature"),
                   ("temperature_cycles", "Temperature Cycles"), ("salt", "Salt Exposure"), ("pollution", "Pollution"),
                   ("soil", "Soil / Dirt"), ("wind", "Wind"))
USE_FIELDS = (("use_handling", "Handling"), ("use_abrasion", "Abrasion"), ("use_impact", "Impact Damage"), ("use_cleaning", "Cleaning"))
CARE_FIELDS = (("care_cleaning", "Cleaning Frequency"), ("care_protective_treatment", "Protective Treatment"),
               ("care_polishing", "Polishing"), ("care_rust_removal", "Rust Removal"), ("care_wood_treatment", "Wood Treatment"))


class OP_OT_aging(SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.aging"
    bl_label = "Material Aging"
    bl_options = {"REGISTER", "UNDO"}
    action: bpy.props.EnumProperty(items=[
        ("APPLY", "Age Material", "Simulate the history and build or re-drive the Material Aging folder"),
        ("REBUILD", "Rebuild", "Rebuild the folder from scratch (drops artist edits inside it)"),
        ("RANDOMIZE", "Randomize History", "Another history of the same kind"),
        ("RESET_ART", "Reset Art Direction", "Forget artist overrides of driven values and re-drive them"),
        ("REMOVE", "Remove Aging", "Delete the Material Aging folder"),
        ("ADD_PERIOD", "Add Period", "Append a period to the environment history"),
        ("REMOVE_PERIOD", "Remove Period", "Remove the selected period"),
        ("ADD_EVENT", "Add Event", "Add a maintenance event"),
        ("REMOVE_EVENT", "Remove Event", "Remove the selected event")], default="APPLY")

    @classmethod
    def description(cls, context, properties):
        return {item.identifier: item.description for item in cls.bl_rna.properties["action"].enum_items}.get(properties.action, "")

    def run(self, context):
        ts = require_set(context)
        aging = ts.aging
        if self.action in {"APPLY", "REBUILD"}:
            result = engine.apply(ts, rebuild=self.action == "REBUILD")
            context.scene.open_painter.status = f"Material aging: {result['label'].title()} at {aging.age_years:.1f} years"
        elif self.action == "RANDOMIZE":
            engine.randomize(ts)
            if not aging.folder_id:
                engine.apply(ts)
        elif self.action == "RESET_ART":
            engine.reset_overrides(ts)
        elif self.action == "REMOVE":
            engine.remove(ts)
        elif self.action == "ADD_PERIOD":
            with engine.batch():
                period = aging.periods.add()
                period.years = max(1.0, aging.age_years / max(1, len(aging.periods)))
                aging.active_period = len(aging.periods) - 1
                aging.use_history = True
            if aging.folder_id:
                engine.apply(ts)
        elif self.action == "REMOVE_PERIOD":
            if len(aging.periods):
                with engine.batch():
                    aging.periods.remove(min(aging.active_period, len(aging.periods) - 1))
                    aging.active_period = max(0, min(aging.active_period, len(aging.periods) - 1))
                if aging.folder_id:
                    engine.apply(ts)
        elif self.action == "ADD_EVENT":
            with engine.batch():
                event = aging.events.add()
                event.year = round(aging.age_years / 2, 1)
                aging.active_event = len(aging.events) - 1
            if aging.folder_id:
                engine.apply(ts)
        elif self.action == "REMOVE_EVENT":
            if len(aging.events):
                with engine.batch():
                    aging.events.remove(min(aging.active_event, len(aging.events) - 1))
                    aging.active_event = max(0, min(aging.active_event, len(aging.events) - 1))
                if aging.folder_id:
                    engine.apply(ts)


class OP_UL_aging_periods(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "years", text="")
        row.prop(item, "environment", text="")
        row.prop(item, "usage", text="")
        row.prop(item, "maintenance", text="")


class OP_UL_aging_events(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "year", text="Year")
        row.prop(item, "kind", text="")


def draw_aging(layout, context, ts):
    aging = ts.aging
    driven = bool(aging.folder_id) and engine.folder_of(ts) is not None
    header = layout.row(align=True)
    header.label(text="What it is", icon="MATERIAL")
    header.prop(aging, "live", text="", icon="PLAY" if aging.live else "PAUSE")
    what = layout.column(align=True)
    what.use_property_split = True
    what.use_property_decorate = False
    what.prop(aging, "material")
    what.prop(aging, "conversion", text="Conversion")
    what.prop(aging, "primer", text="Primer")
    row = what.row(align=True)
    row.prop(aging, "paint", text="Paint")
    if aging.paint != "NONE":
        row.prop(aging, "paint_color", text="")
    what.prop(aging, "topcoat", text="Top Coat")
    if aging.paint != "NONE" or aging.primer != "NONE" or aging.topcoat != "NONE":
        what.prop(aging, "coating_condition", text="Condition", slider=True)
    layout.separator()
    layout.prop(aging, "age_years", text="Age (years)")
    row = layout.row(align=True)
    row.prop(aging, "environment", text="")
    row.prop(aging, "use_history", text="", icon="TIME", toggle=True)
    if aging.use_history:
        row = layout.row()
        row.template_list("OP_UL_aging_periods", "", aging, "periods", aging, "active_period", rows=3)
        column = row.column(align=True)
        column.operator("open_painter.aging", text="", icon="ADD").action = "ADD_PERIOD"
        column.operator("open_painter.aging", text="", icon="REMOVE").action = "REMOVE_PERIOD"
        layout.label(text="Periods scale to the age", icon="INFO")
    else:
        head, body = layout.panel("painter_aging_exposure", default_closed=False)
        head.label(text="Exposure", icon="WORLD")
        if body:
            column = body.column(align=True)
            for field, label in EXPOSURE_FIELDS:
                column.prop(aging, field, text=label, slider=True)
        head, body = layout.panel("painter_aging_use", default_closed=False)
        head.label(text="Use", icon="HAND")
        if body:
            column = body.column(align=True)
            column.prop(aging, "usage", text="")
            for field, label in USE_FIELDS:
                column.prop(aging, field, text=label, slider=True)
        head, body = layout.panel("painter_aging_care", default_closed=True)
        head.label(text="Maintenance", icon="BRUSH_DATA")
        if body:
            column = body.column(align=True)
            column.prop(aging, "maintenance", text="")
            for field, label in CARE_FIELDS:
                column.prop(aging, field, text=label, slider=True)
    head, body = layout.panel("painter_aging_events", default_closed=not len(aging.events))
    head.label(text=f"Events ({len(aging.events)})" if len(aging.events) else "Events", icon="KEYFRAME")
    if body:
        row = body.row()
        row.template_list("OP_UL_aging_events", "", aging, "events", aging, "active_event", rows=2)
        column = row.column(align=True)
        column.operator("open_painter.aging", text="", icon="ADD").action = "ADD_EVENT"
        column.operator("open_painter.aging", text="", icon="REMOVE").action = "REMOVE_EVENT"
    # Prediction ------------------------------------------------------------------
    try:
        result = engine.prediction(ts)
    except Exception as exc:   # a broken setting must not take the panel down
        layout.label(text=f"Prediction unavailable: {exc}", icon="ERROR")
        result = None
    head, body = layout.panel("painter_aging_prediction", default_closed=False)
    head.label(text=f"Predicted: {result['label'].title()}" if result else "Predicted State", icon="INFO")
    if body and result is not None:
        physical = dict(result["physical"])
        column = body.column(align=True)
        for name, value in result["directed"]:
            row = column.row(align=True)
            row.label(text=name)
            base = physical.get(name)
            row.label(text=f"{value:.0%}" + ("" if base is None or abs(base - value) < 5e-3 else f"  (physics {base:.0%})"))
    head, body = layout.panel("painter_aging_art", default_closed=False)
    head.label(text="Art Direction", icon="BRUSHES_ALL")
    if body:
        column = body.column(align=True)
        for field in ("direct_corrosion", "direct_fading", "direct_damage", "direct_dirt"):
            column.prop(aging, field, slider=True)
        row = body.row(align=True)
        row.operator("open_painter.aging", text="Randomize History", icon="FILE_REFRESH").action = "RANDOMIZE"
        row.prop(aging, "seed", text="Seed")
    layout.separator()
    missing = engine.missing_maps(ts) if result is not None else []
    if missing:
        row = layout.row(align=True)
        row.label(text="Bake Mesh Maps first: " + ", ".join(m.replace("_", " ").title() for m in missing), icon="ERROR")
        row.popover(panel="OP_PT_native_bake", text="", icon="RENDER_STILL")
    if result is not None and not missing:
        optional = engine.optional_maps(ts)
        pending = engine.rebuild_pending(ts) if driven else []
        if pending:
            row = layout.row(align=True)
            row.label(text="Maps baked since aging: " + ", ".join(k.replace("_", " ").title() for k in pending), icon="INFO")
            row.operator("open_painter.aging", text="", icon="FILE_REFRESH").action = "REBUILD"
        elif optional:
            row = layout.row(align=True)
            row.label(text="Bake " + ", ".join(m.replace("_", " ").title() for m in optional) + " to place dust and streaks", icon="INFO")
            row.popover(panel="OP_PT_native_bake", text="", icon="RENDER_STILL")
    row = layout.row(align=True)
    if driven:
        row.operator("open_painter.aging", text="Re-age", icon="FILE_REFRESH").action = "APPLY"
        row.operator("open_painter.aging", text="Reset Art", icon="LOOP_BACK").action = "RESET_ART"
        row.operator("open_painter.aging", text="", icon="X").action = "REMOVE"
    else:
        row.enabled = not missing
        row.operator("open_painter.aging", text="Age Material", icon="TIME").action = "APPLY"


class OP_PT_native_aging(bpy.types.Panel):
    """Between Properties and Bake Mesh Maps in the Painter sidebar (same order as Properties: registered after it)."""
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Painter"
    bl_label = "Material Aging"
    bl_order = 2
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        from . import editor
        return editor.is_area(context.area) and active_set(context) is not None

    def draw(self, context):
        draw_aging(self.layout, context, active_set(context))


CLASSES = (OP_OT_aging, OP_UL_aging_periods, OP_UL_aging_events, OP_PT_native_aging)
