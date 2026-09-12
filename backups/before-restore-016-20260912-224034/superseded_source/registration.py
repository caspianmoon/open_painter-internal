"""Single ordered registry, reversible without leaking callbacks."""

import bpy

from .preferences import OP_Preferences
from .properties.bake import OP_BakedMap, OP_BakeSettings, OP_ObjectRef
from .properties.channel import OP_Channel, OP_Output
from .properties.effect import OP_Effect
from .properties.export import OP_ExportSettings
from .properties.layer import OP_Layer
from .properties.mask import OP_Mask
from .properties.project import OP_Project
from .properties.texture_set import OP_Assignment, OP_TextureSet
from .ui.assets_panel import (
    OP_OT_import_material,
    OP_OT_library,
    OP_OT_save_material,
    OP_PT_assets,
)
from .ui.baking_panel import OP_OT_bake, OP_PT_bake, OP_UL_high
from .ui.brush_operators import OP_OT_brush_action, OP_OT_import_alpha
from .ui.dock.interaction import OP_OT_dock_events, OP_OT_dock_popup
from .ui.editor import OP_OT_open_editor
from .ui.effects_panel import OP_OT_effect, OP_PT_effects, OP_UL_effects
from .ui.export_panel import OP_OT_export, OP_PT_export
from .ui.layer_list import OP_OT_layer, OP_PT_layers, OP_UL_layers
from .ui.operators import OP_OT_texture_set
from .ui.painting_panel import OP_OT_paint, OP_PT_paint
from .ui.panels import OP_PT_main, OP_PT_texture_set, OP_UL_assignments, OP_UL_texture_sets
from .ui.properties_panel import (
    OP_OT_bitmap_mask,
    OP_OT_link_projection,
    OP_OT_mask,
    OP_PT_masks,
    OP_UL_masks,
)
from .ui.workspace import OP_OT_workspace

CLASSES = (
    OP_OT_open_editor,
    OP_Preferences,
    OP_Channel,
    OP_Output,
    OP_Mask,
    OP_Effect,
    OP_Layer,
    OP_Assignment,
    OP_ObjectRef,
    OP_BakedMap,
    OP_BakeSettings,
    OP_ExportSettings,
    OP_TextureSet,
    OP_Project,
    OP_OT_texture_set,
    OP_UL_texture_sets,
    OP_UL_assignments,
    OP_PT_main,
    OP_PT_texture_set,
    OP_OT_layer,
    OP_UL_layers,
    OP_PT_layers,
    OP_OT_effect,
    OP_UL_effects,
    OP_PT_effects,
    OP_OT_mask,
    OP_OT_bitmap_mask,
    OP_OT_link_projection,
    OP_UL_masks,
    OP_PT_masks,
    OP_OT_bake,
    OP_UL_high,
    OP_PT_bake,
    OP_OT_save_material,
    OP_OT_import_material,
    OP_OT_library,
    OP_PT_assets,
    OP_OT_paint,
    OP_OT_import_alpha,
    OP_OT_brush_action,
    OP_PT_paint,
    OP_OT_export,
    OP_PT_export,
    OP_OT_workspace,
    OP_OT_dock_popup,
    OP_OT_dock_events,
)


_registered = False
_registered_classes = []
_components = []


def register():
    global _registered
    if _registered:
        return
    from .evaluation import cache
    from .ui import editor
    from .ui.dock import lifecycle

    try:
        for cls in CLASSES:
            bpy.utils.register_class(cls)
            _registered_classes.append(cls)
        bpy.types.Scene.open_painter = bpy.props.PointerProperty(type=OP_Project)
        for component in (cache, lifecycle, editor):
            # Record before calling: a component can fail after adding callbacks.
            _components.append(component)
            component.register()
        _registered = True
    except Exception:
        unregister()
        raise


def unregister():
    global _registered
    from .utils.logging import logger

    for component in reversed(_components):
        try:
            component.unregister()
        except Exception:
            logger.exception("Could not fully unregister %s", component.__name__)
    _components.clear()
    if _registered_classes and hasattr(bpy.types.Scene, "open_painter"):
        del bpy.types.Scene.open_painter
    for cls in reversed(_registered_classes):
        if "bl_rna" in cls.__dict__:
            bpy.utils.unregister_class(cls)
    _registered_classes.clear()
    _registered = False
