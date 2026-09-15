"""Open Graph operators, header and add menu."""
import bpy

from ..core.project_manager import require_set
from ..ui.operators import SafeOperator
from . import mirror
from .tree import TREE_ID, is_graph


def graph_area(screen, ts):
    for area in screen.areas:
        if area.type == 'NODE_EDITOR' and area.spaces.active.tree_type == TREE_ID:
            tree = area.spaces.active.node_tree
            if is_graph(tree) and tree.get('op_graph_set') == ts.id:
                return area
    return None


class OP_OT_open_graph(SafeOperator, bpy.types.Operator):
    bl_idname = 'open_painter.open_graph'
    bl_label = 'Open Graph'
    bl_description = 'Show the active texture set as a live node graph next to the Painter view'
    bl_options = {'REGISTER'}

    def run(self, context):
        ts = require_set(context)
        tree = mirror.ensure_tree(ts)
        area = graph_area(context.screen, ts)
        if area is None:
            host = context.area if context.area and context.area.type in {'VIEW_3D', 'IMAGE_EDITOR', 'NODE_EDITOR'} \
                else next((a for a in context.screen.areas if a.type == 'VIEW_3D'), None)
            if host is None:
                raise ValueError('Open the graph from a Painter area.')
            before = {a.as_pointer() for a in context.screen.areas}
            with context.temp_override(area=host):
                bpy.ops.screen.area_split(direction='VERTICAL', factor=0.5)
            area = next((a for a in context.screen.areas if a.as_pointer() not in before), None)
            if area is None:
                raise ValueError('Blender could not split this area. Open the graph in a larger area.')
            area.ui_type = TREE_ID
        space = area.spaces.active
        space.node_tree = tree
        space.pin = True
        if hasattr(space, 'show_region_ui'):
            space.show_region_ui = False   # the group sidebar has nothing to say about a mirror
        tree['op_active'] = ''
        area.tag_redraw()


class OP_OT_graph_auto_layout(SafeOperator, bpy.types.Operator):
    bl_idname = 'open_painter.graph_auto_layout'
    bl_label = 'Auto Layout'
    bl_description = 'Place every node by stack order and dependency rank'
    bl_options = {'REGISTER', 'UNDO'}

    def run(self, context):
        tree = getattr(context.space_data, 'node_tree', None)
        ts = mirror.texture_set_of(tree, context.scene) if is_graph(tree) else None
        if ts is None:
            raise ValueError('This editor shows no Open Painter graph.')
        mirror.reconcile(tree, ts, force_layout=True)


class OP_OT_graph_select(SafeOperator, bpy.types.Operator):
    bl_idname = 'open_painter.graph_select'
    bl_label = 'Edit in Panel'
    bl_description = 'Select this layer in the Painter panels'
    bl_options = {'REGISTER', 'UNDO'}
    layer_id: bpy.props.StringProperty()

    def run(self, context):
        from ..ui.anchors import _select_layer
        ts = require_set(context)
        tree = getattr(context.space_data, 'node_tree', None)
        if is_graph(tree):
            owner = mirror.texture_set_of(tree, context.scene)
            if owner is not None:
                ts = owner
        index = next((i for i, layer in enumerate(ts.layers) if layer.id == self.layer_id), None)
        if index is None:
            raise ValueError('That layer no longer exists.')
        ts.active_layer = index
        from ..ui.dock import state
        for area in context.screen.areas:
            if area.type in {'VIEW_3D', 'IMAGE_EDITOR'}:
                ui = state.get(area)
                ui.selected_layers, ui.mask_selected = {self.layer_id}, False
                area.tag_redraw()
        if is_graph(tree):
            tree['op_active'] = self.layer_id


def header(self, context):
    space = context.space_data
    if getattr(space, 'tree_type', '') != TREE_ID:
        return
    layout = self.layout
    tree = space.node_tree
    ts = mirror.texture_set_of(tree, context.scene) if is_graph(tree) else None
    if ts is None:
        layout.label(text='Open Painter Graph: open it from the Layers panel', icon='NODETREE')
        return
    layout.label(text=ts.name, icon='NODETREE')
    layout.operator('open_painter.graph_auto_layout', text='Auto Layout', icon='SNAP_GRID')
    from ..evaluation.references import issues, warnings
    problems = issues(ts)
    if problems:
        layout.label(text=problems[0], icon='ERROR')
    else:
        notes = warnings(ts)
        if notes:
            layout.label(text=notes[0], icon='INFO')
    status = context.scene.open_painter.status
    if status:
        layout.label(text=status)


def add_menu(self, context):
    if getattr(context.space_data, 'tree_type', '') != TREE_ID:
        return
    layout = self.layout
    layout.label(text='Layers are added in the Painter panel; the graph mirrors them.', icon='INFO')
    for action, label, icon in (('FILL', 'Fill Layer', 'MATERIAL'), ('PAINT', 'Paint Layer', 'BRUSH_DATA'),
                                ('FOLDER', 'Folder', 'FILE_FOLDER')):
        layout.operator('open_painter.layer', text=label, icon=icon).action = action


CLASSES = (OP_OT_open_graph, OP_OT_graph_auto_layout, OP_OT_graph_select)


def register():
    bpy.types.NODE_HT_header.prepend(header)
    bpy.types.NODE_MT_add.append(add_menu)


def unregister():
    bpy.types.NODE_MT_add.remove(add_menu)
    bpy.types.NODE_HT_header.remove(header)
