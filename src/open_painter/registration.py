"""Single ordered registry, reversible without leaking callbacks."""

import bpy

from .preferences import OP_Preferences, update_debug
from .baking.jobs import OP_OT_bake_queue, OP_OT_bake_stop, OP_OT_edge_map_refresh
from .properties.influencer import OP_Influencer
from .properties.mesh_selector import OP_MeshSelector
from .ui.references import OP_OT_reference_source
from .ui.anchors import (OP_OT_anchor, OP_OT_anchor_pick, OP_OT_anchor_locate, OP_OT_anchor_select_consumer,
                         OP_OT_anchor_channel)
from .graph.tree import CLASSES as GRAPH_TREE_CLASSES
from .core.texture_set_node import CLASSES as TEXTURE_SET_NODE_CLASSES
from .procedural_graph.tree import CLASSES as MASK_GRAPH_CLASSES
from .procedural_graph.edits import CLASSES as MASK_GRAPH_EDIT_CLASSES
from .graph.operators import CLASSES as GRAPH_OPERATOR_CLASSES
from .ui.modules import CLASSES as MODULE_CLASSES
from .ui.scene_fields import CLASSES as SCENE_FIELD_CLASSES
from .ui.target_panel import CLASSES as TARGET_CLASSES
from .ui.geometry_effects import (OP_OT_geometry_effect, OP_OT_geometry_select, OP_PT_geometry_effects,
                                  OP_MT_geometry_effect_add)
from .ui.mask_view import CLASSES as MASK_VIEW_CLASSES
from .ui.bake_view import CLASSES as BAKE_VIEW_CLASSES
from .ui import export_view
from .ui.export_view import CLASSES as EXPORT_VIEW_CLASSES
from .ui.clean_view import CLASSES as CLEAN_VIEW_CLASSES
from .ui.companion import CLASSES as COMPANION_CLASSES
from .ui.material_stamp import OP_OT_material_stamp
from .ui.production_export import OP_OT_production_export
from .ui.offline_analysis import OP_OT_offline_analysis
from .properties.bake import OP_BakeCorrections, OP_BakedMap, OP_BakeSettings, OP_ObjectRef
from .properties.channel import OP_Channel, OP_Output
from .properties.export import OP_ExportSettings
from .properties.aging import CLASSES as AGING_PROPERTY_CLASSES
from .properties.layer import OP_Layer, OP_LayerCache
from .properties.mask import OP_Mask
from .properties.text import OP_TextSettings
from .painting.brush_graph import CLASSES as BRUSH_GRAPH_CLASSES
from .properties.project import OP_PaintCanvas, OP_Project
from .properties.stroke import OP_Stroke, OP_VectorBrushSettings
from .ui.vector_brush import CLASSES as VECTOR_BRUSH_CLASSES
from .ui.strokes_panel import CLASSES as STROKE_PANEL_CLASSES
from .properties.texture_set import OP_Assignment, OP_LayerStore, OP_TextureSet
from .ui.assets_panel import OP_OT_import_material, OP_OT_library, OP_OT_save_material, OP_PT_assets
from .ui.baking_panel import OP_OT_bake, OP_OT_bake_doctor, OP_PT_bake, OP_UL_high
from .ui.bake_corrections import CLASSES as BAKE_CORRECTION_CLASSES
from .ui.physical_scale import CLASSES as PHYSICAL_SCALE_CLASSES
from .ui.decals import CLASSES as DECAL_CLASSES
from .ui.brush_operators import OP_OT_brush_action, OP_OT_import_alpha
from .ui.dock.interaction import OP_OT_dock_events, OP_OT_dock_popup
from .ui.editor import OP_EditorType, OP_OT_close_editor, OP_OT_open_editor
from .ui.export_panel import OP_OT_export, OP_PT_export
from .ui.layer_list import OP_OT_layer, OP_PT_layers, OP_UL_layers
from .ui.operators import OP_OT_texture_set
from .ui.painting_panel import OP_OT_paint, OP_PT_paint
from .ui.panels import OP_PT_main, OP_PT_texture_set, OP_UL_assignments, OP_UL_texture_sets
from .ui.properties_panel import OP_OT_mask, OP_OT_mask_paint, OP_PT_masks, OP_UL_masks
from .ui.workspace import OP_OT_workspace

from .ui.native import CLASSES as NATIVE_CLASSES
from .ui.aging_panel import CLASSES as AGING_UI_CLASSES
from .ui.performance import CLASSES as PERFORMANCE_CLASSES
from .ui.texture_operations import CLASSES as TEXTURE_UI_CLASSES

from .properties.effect import OP_TextureEffect
from .ui.material_director import CLASSES as DIRECTOR_CLASSES, OP_DirectorSettings

CLASSES = (
    OP_TextureEffect,
    OP_EditorType,
    OP_OT_open_editor,
    OP_OT_close_editor,
    OP_Preferences,
    OP_Channel,
    OP_Output,
    OP_Influencer,
    OP_MeshSelector,
    OP_TextSettings,
    OP_Mask,
    OP_LayerCache,
    OP_Layer,
    OP_Assignment,
    OP_ObjectRef,
    OP_BakedMap,
    OP_BakeCorrections,
    OP_BakeSettings,
    OP_ExportSettings,
    *AGING_PROPERTY_CLASSES,
    OP_LayerStore,
    OP_TextureSet,
    OP_Stroke,
    OP_VectorBrushSettings,
    *BRUSH_GRAPH_CLASSES,
    OP_PaintCanvas,
    OP_Project,
    OP_OT_texture_set,
    OP_UL_texture_sets,
    OP_UL_assignments,
    OP_PT_main,
    OP_PT_texture_set,
    OP_OT_layer,
    OP_UL_layers,
    OP_PT_layers,
    OP_OT_mask,
    OP_OT_mask_paint,
    OP_OT_reference_source,
    OP_OT_anchor,
    OP_OT_anchor_pick,
    OP_OT_anchor_locate,
    OP_OT_anchor_select_consumer,
    OP_OT_anchor_channel,
    OP_OT_geometry_effect,
    OP_OT_geometry_select,
    OP_MT_geometry_effect_add,
    OP_PT_geometry_effects,
    OP_OT_material_stamp,
    OP_OT_production_export,
    OP_OT_offline_analysis,
    OP_UL_masks,
    OP_PT_masks,
    OP_OT_bake,
    OP_OT_bake_doctor,
    OP_OT_bake_queue,
    OP_OT_bake_stop,
    OP_OT_edge_map_refresh,
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
) + TEXTURE_UI_CLASSES + NATIVE_CLASSES + MASK_VIEW_CLASSES + COMPANION_CLASSES + BAKE_VIEW_CLASSES + EXPORT_VIEW_CLASSES + CLEAN_VIEW_CLASSES + AGING_UI_CLASSES + TEXTURE_SET_NODE_CLASSES + MASK_GRAPH_CLASSES + MASK_GRAPH_EDIT_CLASSES + GRAPH_TREE_CLASSES + GRAPH_OPERATOR_CLASSES + MODULE_CLASSES + SCENE_FIELD_CLASSES + TARGET_CLASSES + VECTOR_BRUSH_CLASSES + STROKE_PANEL_CLASSES + DIRECTOR_CLASSES + PERFORMANCE_CLASSES + BAKE_CORRECTION_CLASSES + PHYSICAL_SCALE_CLASSES + DECAL_CLASSES


_registered = False
_registered_classes = []
_cleanups = []


def register():
    global _registered
    if _registered:
        return
    if hasattr(bpy.types.Scene, 'open_painter'):
        raise RuntimeError('Open Painter scene properties are already owned by another registration')
    from .evaluation import cache
    from .ui.dock import lifecycle
    from .ui import editor, workspace
    from .graph import operators as graph_operators
    from .core import texture_set_node
    from .procedural_graph import tree as mask_graph_tree
    from .ui.native import toolbar, asset_type_buttons, layer_context_menu
    from .ai import session as director
    from .ui import vector_brush
    from .smart_materials import portable
    from .painting import brush_graph
    from .native_bridge import lifecycle as native_lifecycle
    try:
        for cls in CLASSES:
            bpy.utils.register_class(cls)
            _registered_classes.append(cls)
        addon = bpy.context.preferences.addons.get(__package__)
        if addon:
            # Saved preferences restore without their update callbacks: a
            # persisted Developer Mode must reach the logger before any output.
            update_debug(addon.preferences, None)
        bpy.types.Palette.open_painter_layers = bpy.props.PointerProperty(type=OP_LayerStore)
        bpy.types.Scene.open_painter = bpy.props.PointerProperty(type=OP_Project)
        bpy.types.WindowManager.open_painter_director = bpy.props.PointerProperty(type=OP_DirectorSettings)
        _cleanups.append(lambda: delattr(bpy.types.WindowManager, 'open_painter_director'))
        export_view.register()
        _cleanups.append(export_view.unregister)
        for initialize, cleanup in ((native_lifecycle.register,native_lifecycle.unregister),
                                    (cache.register,cache.unregister),
                                    (lifecycle.register,lifecycle.unregister),
                                    (editor.register,editor.unregister),
                                    (graph_operators.register,graph_operators.unregister),
                                    (texture_set_node.register,texture_set_node.unregister),
                                    (mask_graph_tree.register,mask_graph_tree.unregister),
                                    (director.register,director.unregister),
                                    (vector_brush.register,vector_brush.unregister),
                                    (portable.register,portable.unregister),
                                    (brush_graph.register,brush_graph.unregister)):
            # Install rollback before initialization, including partial failures.
            _cleanups.append(cleanup)
            initialize()
        _cleanups.append(workspace.unregister)
        from .ui import shelf_library
        try:
            shelf_library.ensure()   # the built-in shelf entries, outside the artist's file
        except Exception:
            from .utils.logging import logger
            logger.exception('Shelf library unavailable; the shelf shows local handles only')
        for owner, method, callback in ((bpy.types.VIEW3D_HT_tool_header,'prepend',asset_type_buttons),
                                        (bpy.types.IMAGE_HT_tool_header,'prepend',asset_type_buttons),
                                        (bpy.types.VIEW3D_HT_tool_header,'append',toolbar),
                                        (bpy.types.UI_MT_list_item_context_menu,'append',layer_context_menu)):
            getattr(owner,method)(callback)
            _cleanups.append(lambda owner=owner,callback=callback: owner.remove(callback))
        _registered = True
    except Exception:
        try:
            unregister()
        except Exception:
            from .utils.logging import logger
            logger.exception('Registration rollback encountered a cleanup error')
        raise


def unregister():
    global _registered
    _registered = False   # suppress graph callbacks while their RNA types are removed
    errors=[]
    while _cleanups:
        try:
            _cleanups.pop()()
        except Exception as exc:
            errors.append(str(exc))
    if _registered_classes and hasattr(bpy.types.Scene, 'open_painter'):
        del bpy.types.Scene.open_painter
    if _registered_classes and hasattr(bpy.types.Palette, 'open_painter_layers'):
        del bpy.types.Palette.open_painter_layers
    while _registered_classes:
        cls=_registered_classes.pop()
        try:
            if 'bl_rna' in cls.__dict__:
                bpy.utils.unregister_class(cls)
        except Exception as exc:
            errors.append(str(exc))
    _registered=False
    if errors:
        raise RuntimeError('Open Painter cleanup failed: '+' | '.join(errors))
