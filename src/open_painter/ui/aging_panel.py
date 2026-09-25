"""Material Aging panel (0.10.0): what it is, how old, where it lived, how it was used —
and what the simulation predicts, with art direction on top."""
import bpy

from ..aging import engine
from ..aging.environments import EVENT_LABELS
from ..core.project_manager import active_set, require_set
from ..properties.aging import ENVIRONMENT_ITEMS
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
        ("RESET_ENVIRONMENT", "Reset Environment", "Put the exposure sliders back to the chosen preset's values"),
        ("RESET_SETUP", "Reset Aging Setup", "Age, environment, use, care, events, seed and art direction back to their "
                                            "defaults; the material construction is kept"),
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
        elif self.action == "RESET_ENVIRONMENT":
            base = aging.environment_base if aging.environment_base in {k for k, *_ in ENVIRONMENT_ITEMS} else "OUTDOOR_URBAN"
            with engine.batch():
                aging.environment = base    # the preset's values are copied back by its update
            if aging.folder_id:
                engine.apply(ts)
        elif self.action == "RESET_SETUP":
            with engine.batch():
                for name in ("age_years", "use_history", "environment", "usage", "maintenance", "seed",
                             "direct_corrosion", "direct_fading", "direct_damage", "direct_dirt"):
                    aging.property_unset(name)
                aging.periods.clear()
                aging.events.clear()
                aging.environment = aging.environment      # re-copies the preset's sliders
                aging.usage = aging.usage
                aging.maintenance = aging.maintenance
            if aging.folder_id:
                engine.apply(ts)
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
                event.year = round(aging.age_years, 1)     # "just happened"; move it back to let it re-age
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
        row.prop(item, "year", text="")
        row.prop(item, "kind", text="")
        ago = float(data.age_years) - float(item.year)
        if ago < -1e-6:
            row.label(text="after the age", icon="ERROR")
        else:
            row.label(text="just now" if ago < 0.05 else f"{ago:.0f} y ago" if ago >= 1 else f"{ago * 12:.0f} months ago")


def environment_label(aging):
    if aging.environment != "CUSTOM":
        return None
    labels = {key: label for key, label, *_ in ENVIRONMENT_ITEMS}
    base = labels.get(aging.environment_base, "")
    return f"Custom · modified from {base}" if base else "Custom exposure"


def draw_aging(layout, context, ts):
    """Applied To · Material Construction · Environment · Age first; the details in named sections."""
    aging = ts.aging
    folder = engine.folder_of(ts) if aging.folder_id else None
    driven = folder is not None
    layout.use_property_split = True
    layout.use_property_decorate = False
    # Applied To ------------------------------------------------------------------
    objects = sorted({ref.object.name for ref in ts.assignments if ref.object})
    row = layout.row(align=True)
    row.label(text="Applied to " + (", ".join(objects[:3]) + (" …" if len(objects) > 3 else "") if objects else "no object"),
              icon="MESH_DATA" if objects else "ERROR")
    if driven:
        layout.label(text=f"Drives the folder {folder.name} in {ts.name}", icon="FILE_FOLDER")
    else:
        layout.label(text="No aging folder yet: Age Material builds one", icon="INFO")
    layout.prop(aging, "live", text="Follow Edits Live", toggle=True)
    # Material Construction -------------------------------------------------------
    head, body = layout.panel("painter_aging_construction", default_closed=False)
    head.label(text="Material Construction", icon="MATERIAL")
    if body is not None:
        body.prop(aging, "material")
        body.prop(aging, "conversion", text="Conversion")
        body.prop(aging, "primer", text="Primer")
        row = body.row(align=True)
        row.prop(aging, "paint", text="Paint")
        if aging.paint != "NONE":
            row.prop(aging, "paint_color", text="")
        body.prop(aging, "topcoat", text="Top Coat")
        if aging.paint != "NONE" or aging.primer != "NONE" or aging.topcoat != "NONE":
            body.prop(aging, "coating_condition", text="Starting Condition", slider=True)
    # Environment and age ---------------------------------------------------------
    row = layout.row(align=True)
    row.prop(aging, "environment", text="Environment")
    row.prop(aging, "use_history", text="", icon="TIME", toggle=True)
    modified = environment_label(aging)
    if modified and not aging.use_history:
        row = layout.row(align=True)
        row.label(text=modified, icon="GREASEPENCIL")
        row.operator("open_painter.aging", text="Reset Environment", icon="LOOP_BACK").action = "RESET_ENVIRONMENT"
    layout.prop(aging, "age_years", text="Age in Years")
    if aging.use_history:
        head, body = layout.panel("painter_aging_history", default_closed=False)
        head.label(text=f"Environment History ({len(aging.periods)} periods)", icon="TIME")
        if body is not None:
            row = body.row()
            row.template_list("OP_UL_aging_periods", "", aging, "periods", aging, "active_period", rows=3)
            column = row.column(align=True)
            column.operator("open_painter.aging", text="", icon="ADD").action = "ADD_PERIOD"
            column.operator("open_painter.aging", text="", icon="REMOVE").action = "REMOVE_PERIOD"
            body.label(text="Periods scale to the age", icon="INFO")
    else:
        head, body = layout.panel("painter_aging_exposure", default_closed=True)
        head.label(text="Exposure (relative intensities)", icon="WORLD")
        if body is not None:
            column = body.column(align=True)
            for field, label in EXPOSURE_FIELDS:
                column.prop(aging, field, text=label, slider=True)
            body.label(text="0 none to 1 extreme; the model scales its rates by them, no physical units", icon="INFO")
        head, body = layout.panel("painter_aging_use", default_closed=True)
        head.label(text="Use", icon="HAND")
        if body is not None:
            column = body.column(align=True)
            column.prop(aging, "usage", text="Preset")
            for field, label in USE_FIELDS:
                column.prop(aging, field, text=label, slider=True)
        head, body = layout.panel("painter_aging_care", default_closed=True)
        head.label(text="Maintenance", icon="BRUSH_DATA")
        if body is not None:
            column = body.column(align=True)
            column.prop(aging, "maintenance", text="Preset")
            for field, label in CARE_FIELDS:
                column.prop(aging, field, text=label, slider=True)
    head, body = layout.panel("painter_aging_events", default_closed=not len(aging.events))
    head.label(text=f"Events ({len(aging.events)})" if len(aging.events) else "Events", icon="KEYFRAME")
    if body is not None:
        row = body.row()
        row.template_list("OP_UL_aging_events", "", aging, "events", aging, "active_event", rows=2)
        column = row.column(align=True)
        column.operator("open_painter.aging", text="", icon="ADD").action = "ADD_EVENT"
        column.operator("open_painter.aging", text="", icon="REMOVE").action = "REMOVE_EVENT"
        if any(e.year > aging.age_years + 1e-6 for e in aging.events):
            body.label(text="An event after the age has not happened yet", icon="ERROR")
        elif len(aging.events):
            body.label(text="Year 0 is new; the age is now. Rust and dirt return after an event.", icon="INFO")
    # Prediction ------------------------------------------------------------------
    try:
        result = engine.prediction(ts)
    except Exception as exc:   # a broken setting must not take the panel down
        layout.label(text=f"Prediction unavailable: {exc}", icon="ERROR")
        result = None
    head, body = layout.panel("painter_aging_prediction", default_closed=True)
    head.label(text=f"Simulated State: {result['label'].title()}" if result else "Simulated State", icon="INFO")
    if body is not None and result is not None:
        body.label(text="A model estimate of the history, not a measurement", icon="INFO")
        physical = dict(result["physical"])
        column = body.column(align=True)
        for name, value in result["directed"]:
            row = column.row(align=True)
            row.label(text=name)
            base = physical.get(name)
            row.label(text=f"{value:.0%}" + ("" if base is None or abs(base - value) < 5e-3 else f"  (model {base:.0%})"))
    head, body = layout.panel("painter_aging_art", default_closed=True)
    head.label(text="Art Direction (after the simulation)", icon="BRUSHES_ALL")
    if body is not None:
        body.label(text="Multipliers on the simulated outcomes, not environmental inputs", icon="INFO")
        column = body.column(align=True)
        for field in ("direct_corrosion", "direct_fading", "direct_damage", "direct_dirt"):
            column.prop(aging, field, slider=True)
        row = body.row(align=True)
        row.operator("open_painter.aging", text="Randomize History", icon="FILE_REFRESH").action = "RANDOMIZE"
        row.prop(aging, "seed", text="Seed")
        row = body.row(align=True)
        row.operator("open_painter.aging", text="Reset Art Direction", icon="LOOP_BACK").action = "RESET_ART"
    layout.separator()
    missing = engine.missing_maps(ts) if result is not None else []
    if missing:
        row = layout.row(align=True)
        row.label(text="Bake Mesh Maps first: " + ", ".join(m.replace("_", " ").title() for m in missing), icon="ERROR")
        row.operator("open_painter.show_task", text="", icon="RENDER_STILL").task = "BAKE"
    if result is not None and not missing:
        optional = engine.optional_maps(ts)
        pending = engine.rebuild_pending(ts) if driven else []
        if pending:
            row = layout.row(align=True)
            row.label(text="Maps baked since aging: " + ", ".join(k.replace("_", " ").title() for k in pending), icon="INFO")
            row.operator("open_painter.aging", text="Rebuild Folder", icon="FILE_REFRESH").action = "REBUILD"
        elif optional:
            row = layout.row(align=True)
            row.label(text="Bake " + ", ".join(m.replace("_", " ").title() for m in optional) + " to place dust and streaks", icon="INFO")
            row.operator("open_painter.show_task", text="", icon="RENDER_STILL").task = "BAKE"
    if driven and engine.recipe_version(ts) < engine.document.VERSION:
        box = layout.box()
        box.label(text="Improved aging procedurals available", icon="INFO")
        box.label(text="Rebuild replaces edits inside the aging folder.")
        box.operator("open_painter.aging", text="Rebuild with New Procedurals", icon="FILE_REFRESH").action = "REBUILD"
    row = layout.row(align=True)
    if driven:
        row.operator("open_painter.aging", text="Re-age Material", icon="FILE_REFRESH").action = "APPLY"
        row.operator("open_painter.aging", text="Remove Aging", icon="X").action = "REMOVE"
    else:
        row.enabled = not missing
        row.operator("open_painter.aging", text="Age Material", icon="TIME").action = "APPLY"
    layout.operator("open_painter.aging", text="Reset Aging Setup", icon="LOOP_BACK").action = "RESET_SETUP"


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
