import bpy

from ..core import texture_set_manager as sets
from ..core.project_manager import require_set
from ..core.validation import validate
from ..utils.logging import logger
from ..constants import RESOLUTIONS


class SafeOperator:
    def execute(self, context):
        try:
            result = self.run(context) or {"FINISHED"}
        except Exception as exc:
            logger.debug("Operator failed", exc_info=True)
            if hasattr(context.scene, "open_painter"):
                context.scene.open_painter.status = str(exc)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if "FINISHED" in result and "UNDO" in self.bl_options:
            # Blender pushes this operator's undo step next, before the depsgraph evaluates: tags
            # still pending then (an image loaded, its colour space set) are stored in the step, and
            # an undo to it tags those images again, which rebuilds their GPU textures. Reported
            # now, they reach the step settled (dependencies.report_updates).
            from ..evaluation.dependencies import report_updates
            report_updates()
        return result


class OP_OT_texture_set(SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.texture_set"
    bl_label = "Texture Set"
    bl_description = "Manage texture sets and explicit active material-slot assignments"
    bl_options = {"REGISTER", "UNDO"}
    udim_name: bpy.props.StringProperty(name='Name', default='UDIM')
    udim_resolution: bpy.props.EnumProperty(name='Resolution per Tile', items=RESOLUTIONS, default='2048')
    action: bpy.props.EnumProperty(
        items=[
            (x, x.title(), "")
            for x in (
                "ADD",
                "ADD_UDIM",
                "LINK_UDIM",
                "UNLINK_UDIM",
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

    def invoke(self, context, event):
        if self.action != 'ADD_UDIM':
            return self.execute(context)
        obj = context.object
        self.udim_name = obj.name if obj and obj.type == 'MESH' else 'UDIM'
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        self.layout.prop(self, 'udim_name')
        self.layout.prop(self, 'udim_resolution')
        self.layout.label(text='One texture set per occupied UV tile')

    def run(self, context):
        if self.action in {'LINK_UDIM', 'UNLINK_UDIM'}:
            from ..core import shared_stacks
            ts = require_set(context)
            if self.action == 'LINK_UDIM':
                shared_stacks.link([item for item in context.scene.open_painter.texture_sets
                                    if item.udim_group and item.udim_group == ts.udim_group])
            else:
                shared_stacks.make_independent(ts)
            return
        if self.action == 'ADD_UDIM':
            from ..core import udim
            meshes = [obj for obj in context.selected_objects if obj.type == 'MESH']
            created = udim.create(context.scene, meshes, self.udim_name.strip() or 'UDIM', int(self.udim_resolution))
            context.scene.open_painter.status = 'Created UDIM tiles ' + ', '.join(str(ts.udim_tile) for ts in created)
            return
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
            from ..core.validation import warnings
            issues = validate(ts)
            # Warnings (a missing anchor, a face selection no modifier stack maps through)
            # do not fail the set, but the artist asked what is wrong with it: list them.
            notes = warnings(ts) if not issues else []
            context.scene.open_painter.status = (
                " | ".join(issues) if issues else
                " | ".join(notes) if notes else "Texture set is valid."
            )
            self.report({"WARNING"} if issues or notes else {"INFO"}, context.scene.open_painter.status)
