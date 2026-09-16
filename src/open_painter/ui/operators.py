import bpy

from ..core import texture_set_manager as sets
from ..core.project_manager import require_set
from ..core.validation import validate
from ..utils.logging import logger


class SafeOperator:
    def execute(self, context):
        try:
            result = self.run(context)
            return result or {"FINISHED"}
        except Exception as exc:
            logger.debug("Operator failed", exc_info=True)
            if hasattr(context.scene, "open_painter"):
                context.scene.open_painter.status = str(exc)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class OP_OT_texture_set(SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.texture_set"
    bl_label = "Texture Set"
    bl_description = "Manage texture sets and explicit active material-slot assignments"
    bl_options = {"REGISTER", "UNDO"}
    action: bpy.props.EnumProperty(
        items=[
            (x, x.title(), "")
            for x in (
                "ADD",
                "DELETE",
                "ASSIGN",
                "UNASSIGN",
                "VALIDATE",
                "REFRESH",
                "REPAIR",
                "DUPLICATE",
            )
        ]
    )

    def run(self, context):
        if self.action == "ADD":
            obj = context.object
            if obj and obj.type == "MESH":
                if not obj.data.uv_layers:
                    raise ValueError("Unwrap the selected mesh before creating its texture set.")
                if obj.data.users > 1:
                    raise ValueError("Make the selected mesh single-user first.")
                existing = next(
                    (
                        item
                        for item in context.scene.open_painter.texture_sets
                        if any(
                            ref.object == obj and ref.slot_index == obj.active_material_index
                            for ref in item.assignments
                        )
                    ),
                    None,
                )
                if existing:
                    raise ValueError(
                        f"This material slot already belongs to {existing.name}. Select another slot or mesh."
                    )
            ts = sets.create(
                context.scene, context.object.name if context.object else "Texture Set"
            )
            if context.object and context.object.type == "MESH":
                sets.assign(context.scene, ts, context.object)
            return
        ts = require_set(context)
        if self.action == "DUPLICATE":
            sets.duplicate(context.scene, context.scene.open_painter.active_texture_set)
        elif self.action == "DELETE":
            sets.remove(context.scene, context.scene.open_painter.active_texture_set)
        elif self.action == "ASSIGN":
            objects = [o for o in context.selected_objects if o.type == "MESH"]
            if not objects:
                raise ValueError("Select a UV-unwrapped mesh first.")
            for obj in objects:
                sets.assign(context.scene, ts, obj)
        elif self.action == "UNASSIGN":
            if ts.assignments:
                sets.unassign(ts, min(ts.active_assignment, len(ts.assignments) - 1))
        elif self.action in {"REFRESH", "REPAIR", "DUPLICATE"}:
            from ..evaluation.evaluator import LayerEvaluator

            if self.action == "REPAIR":
                from ..core.material_manager import relink_slots
                relink_slots(ts)
            LayerEvaluator(context.scene).evaluate_texture_set(ts.id, force=True)
        elif self.action == "VALIDATE":
            issues = validate(ts)
            context.scene.open_painter.status = (
                " | ".join(issues) if issues else "Texture set is valid."
            )
            self.report({"WARNING"} if issues else {"INFO"}, context.scene.open_painter.status)
