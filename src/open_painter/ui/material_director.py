"""Material Director's native Blender controls and undo boundaries."""

import json
from textwrap import wrap

import bpy

from ..ai import session
from ..authoring import commands, transactions
from ..core.layer_stack import active
from ..core.project_manager import active_set, require_set
from .native import PainterPanel


class DirectorOperator:
    def execute(self, context):
        try:
            self.run(context)
            session.redraw()
            return {"FINISHED"}
        except Exception as exc:
            session.state["status"] = str(exc)[:500]
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class OP_DirectorSettings(bpy.types.PropertyGroup):
    prompt: bpy.props.StringProperty(name="Instruction", maxlen=5000, options={"SKIP_SAVE"},
        description="Describe a material or a change, for example: reduce the existing rust and make the bottom dirtier")
    mode: bpy.props.EnumProperty(name="Mode", default="REFINE", options={"SKIP_SAVE"}, items=[
        ("CREATE", "Create", "Construct an editable material in the selected texture set"),
        ("REFINE", "Refine", "Edit existing layers and controls"),
        ("PLAN", "Plan", "Prepare a draft to review before applying"),
        ("EXPLAIN", "Explain", "Discuss this material without proposing changes")])
    scope: bpy.props.EnumProperty(name="Scope", default="SET", options={"SKIP_SAVE"}, items=[
        ("SET", "Texture Set", "Work within the active texture set"),
        ("SCENE", "Scene Materials", "Inspect and coordinate all assigned Open Painter texture sets in this scene (up to 16); apply and undo as one group"),
        ("LAYER", "Selected Layer / Folder", "Edit only the selected layer or the contents of its folder")])
    model: bpy.props.EnumProperty(name="Model", items=session.model_items, options={"SKIP_SAVE"})
    send_view: bpy.props.BoolProperty(name="Send Current 3D View", default=True, options={"SKIP_SAVE"},
        description="Send a model view and an isolated view of the editable parts, up to 768 px, without UI panels")
    send_maps: bpy.props.BoolProperty(name="Send Mesh Maps", default=True, options={"SKIP_SAVE"},
        description="Send bounded AO, curvature, normal, world-normal, position and thickness map previews with freshness information")
    reference_path: bpy.props.StringProperty(name="Reference Image", subtype="FILE_PATH", maxlen=4096,
        options={"SKIP_SAVE"}, description="Optional PNG, JPEG or WebP appearance reference sent to Codex and available to native image generation")
    generate_textures: bpy.props.BoolProperty(name="Generate Surface Textures", default=True, options={"SKIP_SAVE"},
        description="Let Codex generate image textures for editable layers and masks using your connected account; generation can take several minutes")


class OP_OT_director_connection(DirectorOperator, bpy.types.Operator):
    bl_idname = "open_painter.director_connection"
    bl_label = "Codex Connection"
    action: bpy.props.EnumProperty(items=[(k, k.title(), "") for k in
                                          ("CONNECT", "DISCONNECT", "LOGIN", "LOGOUT", "CANCEL_LOGIN")])

    def run(self, context):
        if self.action == "CONNECT":
            session.connect(context)
        elif self.action == "DISCONNECT":
            session.disconnect()
        else:
            session.account_action(self.action.lower())


class OP_OT_director_run(DirectorOperator, bpy.types.Operator):
    bl_idname = "open_painter.director_run"
    bl_label = "Direct Material"
    bl_description = "Send this instruction and the selected material context to Codex"

    def run(self, context):
        session.run(context)


class OP_OT_director_stop(DirectorOperator, bpy.types.Operator):
    bl_idname = "open_painter.director_stop"
    bl_label = "Stop"
    bl_description = "Cancel this instruction and discard its pending draft"

    def run(self, context):
        session.stop()


class OP_OT_director_apply(DirectorOperator, bpy.types.Operator):
    bl_idname = "open_painter.director_apply"
    bl_label = "Apply Material Director Draft"
    bl_description = "Apply the validated draft as one Blender undo step"
    bl_options = {"UNDO"}
    request_id: bpy.props.StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def run(self, context):
        session.apply_draft(self.request_id)


class OP_OT_director_undo(DirectorOperator, bpy.types.Operator):
    bl_idname = "open_painter.director_undo"
    bl_label = "Undo Last AI Change"
    bl_description = "Reverse the latest AI change while keeping later manual edits on conflicting controls"
    bl_options = {"UNDO"}

    def run(self, context):
        session.stop()
        result = transactions.undo(require_set(context))
        suffix = f"; kept {len(result['conflicts'])} later edits" if result["conflicts"] else ""
        session.state.update(status=result["summary"] + suffix, changes=result["changes"])


def protection_path(context, kind):
    ts = require_set(context)
    layer = active(ts)
    if layer is None:
        raise ValueError("Select a layer first")
    path = ["layers", layer.id]
    if kind == "CHANNEL":
        path += ["channels", context.scene.open_painter.properties_channel]
    elif kind == "MASK":
        if not 0 <= layer.active_mask < len(layer.masks):
            raise ValueError("Select a mask first")
        path += ["masks", layer.masks[layer.active_mask].id]
    return path


class OP_OT_director_protect(DirectorOperator, bpy.types.Operator):
    bl_idname = "open_painter.director_protect"
    bl_label = "Protect from AI Changes"
    bl_description = "Toggle protection for the selected content; paint layers are always protected"
    bl_options = {"UNDO"}
    kind: bpy.props.EnumProperty(items=[(k, k.title(), "") for k in ("LAYER", "CHANNEL", "MASK")])

    def run(self, context):
        ts = require_set(context)
        path = protection_path(context, self.kind)
        saved = [list(p) for p in commands.locks(ts)]
        if path in saved:
            saved.remove(path)
        else:
            saved.append(path)
        ts.director_locks = json.dumps(saved)


class OP_OT_director_report(bpy.types.Operator):
    bl_idname = "open_painter.director_report"
    bl_label = "Material Director Changes"
    bl_description = "Show actual parameter changes from the draft or latest committed transaction"
    copy: bpy.props.BoolProperty(default=False, options={"SKIP_SAVE"})
    response: bpy.props.BoolProperty(default=False, options={"SKIP_SAVE"})

    def invoke(self, context, event):
        lines = session.state["response"].splitlines() if self.response else session.state["changes"]
        if not lines:
            ts = active_set(context)
            try:
                records = transactions.history(ts) if ts else []
                lines = records[-1]["changes"] if records else ["No changes recorded"]
            except ValueError as exc:
                lines = [str(exc)]
        self._lines = list(lines)
        if self.copy:
            context.window_manager.clipboard = "\n".join(lines)
            return {"FINISHED"}
        return context.window_manager.invoke_props_dialog(self, width=680)

    def draw(self, context):
        lines = getattr(self, "_lines", [])
        for text in lines[:50]:
            for line in wrap(text, 100) or [""]:
                self.layout.label(text=line)
        if len(lines) > 50:
            self.layout.label(text="Use Copy Changes for the full report")

    def execute(self, context):
        return {"FINISHED"}


class OP_OT_director_clear_history(DirectorOperator, bpy.types.Operator):
    bl_idname = "open_painter.director_clear_history"
    bl_label = "Clear AI History"
    bl_description = "Clear AI change receipts and the current conversation; keep the material layers"
    bl_options = {"UNDO"}

    def run(self, context):
        session.stop()
        session.clear_conversation()
        require_set(context).director_history = "[]"
        session.state.update(status="AI change history cleared", changes=[])


def text_lines(layout, text, width=38, limit=20):
    lines = [line for paragraph in text.splitlines() for line in (wrap(paragraph, width) or [""])]
    for line in lines[:limit]:
        layout.label(text=line)
    if len(lines) > limit:
        layout.label(text="…")


def connection_settings(layout, prefs):
    layout.prop(prefs, "director_codex_binary")
    layout.prop(prefs, "director_credentials")
    text_lines(layout, "Codex receives your instruction and selected material/mesh summaries. Model views and mesh-map previews are enabled by default; their checkboxes control sharing. A chosen reference image is also sent. Your .blend stays local.")
    layout.prop(prefs, "director_share_context")


class OP_PT_material_director(PainterPanel, bpy.types.Panel):
    bl_label = "Material Director"
    bl_category = "Director"
    bl_order = 0

    def draw(self, context):
        layout = self.layout
        status = session.state
        settings = context.window_manager.open_painter_director
        text_lines(layout, status["status"], limit=4)
        ts = active_set(context)
        layout.label(text="Texture Set: " + ts.name if ts else "Select a texture set in Painter")
        if settings.scope == "LAYER" and ts and active(ts):
            layout.label(text="Layer: " + active(ts).name)
        row = layout.row(align=True)
        if session.connection is None:
            row.operator("open_painter.director_connection", text="Connect Codex", icon="LINKED").action = "CONNECT"
        else:
            if status["connected"] and not status["signed_in"]:
                if status["login"]:
                    row.operator("open_painter.director_connection", text="Cancel Sign-in").action = "CANCEL_LOGIN"
                else:
                    row.operator("open_painter.director_connection", text="Sign in with ChatGPT").action = "LOGIN"
            elif status["signed_in"]:
                row.operator("open_painter.director_connection", text="Sign Out").action = "LOGOUT"
            row.operator("open_painter.director_connection", text="", icon="UNLINKED").action = "DISCONNECT"
        if status["plan"]:
            layout.label(text="ChatGPT plan: " + status["plan"])
        for name in ("primary", "secondary"):
            limit = status["limits"].get(name)
            if isinstance(limit, dict) and "usedPercent" in limit:
                layout.label(text=f"{name.title()} allowance used: {limit['usedPercent']}%")
        layout.prop(settings, "mode", expand=True)
        layout.prop(settings, "scope")
        if settings.scope == "SCENE":
            count = sum(bool(ts.assignments) for ts in context.scene.open_painter.texture_sets)
            layout.label(text=f"Scene: {count} managed material regions")
        if status["models"]:
            layout.prop(settings, "model")
        layout.prop(settings, "prompt", text="")
        layout.prop(settings, "send_view")
        layout.prop(settings, "send_maps")
        layout.prop(settings, "reference_path")
        if status.get("visual_inputs"):
            text_lines(layout, "Last sent: " + ", ".join(item["id"] for item in status["visual_inputs"]))
        row = layout.row()
        row.enabled = settings.mode != "EXPLAIN"
        row.prop(settings, "generate_textures")
        running = bool(session.request and session.request["running"])
        row = layout.row()
        if running:
            row.operator("open_painter.director_stop", icon="CANCEL")
        else:
            row.enabled = status["signed_in"] and active_set(context) is not None
            row.operator("open_painter.director_run", text={"PLAN": "Prepare Draft", "EXPLAIN": "Explain Material"}.get(settings.mode, "Direct Material"), icon="PLAY")
        if session.request and not running and session.request["draft"]:
            row = layout.row(align=True)
            row.operator("open_painter.director_apply", text="Apply Draft", icon="CHECKMARK").request_id = session.request["id"]
            row.operator("open_painter.director_stop", text="Discard")
        if status["response"]:
            box = layout.box()
            text_lines(box, status["response"], limit=24)
            op = box.operator("open_painter.director_report", text="Copy Reply", icon="COPYDOWN")
            op.response = op.copy = True
        row = layout.row(align=True)
        row.operator("open_painter.director_report", text="Show Changes", icon="TEXT")
        row.operator("open_painter.director_report", text="", icon="COPYDOWN").copy = True
        row = layout.row()
        row.enabled = active_set(context) is not None and not running
        row.operator("open_painter.director_undo", icon="LOOP_BACK")


class OP_PT_director_protection(PainterPanel, bpy.types.Panel):
    bl_label = "Protected Content"
    bl_category = "Director"
    bl_parent_id = "OP_PT_material_director"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        ts = active_set(context)
        if not ts:
            self.layout.label(text="Select a texture set")
            return
        self.layout.label(text="Paint layers are always protected")
        for kind in ("LAYER", "CHANNEL", "MASK"):
            row = self.layout.row()
            try:
                path = tuple(protection_path(context, kind))
                locked = path in commands.locks(ts)
            except ValueError:
                row.enabled = False
                locked = False
            row.operator("open_painter.director_protect", text=("Unlock " if locked else "Protect ") + kind.title(),
                         icon="LOCKED" if locked else "UNLOCKED").kind = kind


class OP_PT_director_settings(PainterPanel, bpy.types.Panel):
    bl_label = "Connection Settings"
    bl_category = "Director"
    bl_parent_id = "OP_PT_material_director"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        prefs = session.preferences(context)
        if prefs:
            connection_settings(self.layout, prefs)
        else:
            self.layout.label(text="Install the extension to configure Codex")
        self.layout.operator("open_painter.director_clear_history", icon="TRASH")


CLASSES = (OP_DirectorSettings, OP_OT_director_connection, OP_OT_director_run, OP_OT_director_stop,
           OP_OT_director_apply, OP_OT_director_undo, OP_OT_director_protect, OP_OT_director_report,
           OP_OT_director_clear_history,
           OP_PT_material_director, OP_PT_director_protection, OP_PT_director_settings)
