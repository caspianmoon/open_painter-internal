"""Open Graph operators, header and add menu."""
import bpy

from ..constants import CHANNEL_LABELS
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
    bl_description = 'Select this layer (and mask or filter) in the Painter panels'
    bl_options = {'REGISTER', 'UNDO'}
    layer_id: bpy.props.StringProperty()
    mask_index: bpy.props.IntProperty(default=-1)
    channel: bpy.props.StringProperty()
    effect_index: bpy.props.IntProperty(default=-1)

    def run(self, context):
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
        layer = ts.layers[index]
        if 0 <= self.mask_index < len(layer.masks):
            layer.active_mask = self.mask_index
        if self.channel:
            from ..core.layer_stack import channel_for
            prop = channel_for(layer, self.channel)
            if prop is not None and layer.type != 'PAINT':
                context.scene.open_painter.properties_channel = self.channel
            if prop is not None and 0 <= self.effect_index < len(prop.effects):
                prop.active_effect = self.effect_index
        from ..ui.dock import state
        for area in context.screen.areas:
            if area.type in {'VIEW_3D', 'IMAGE_EDITOR'}:
                ui = state.get(area)
                ui.selected_layers, ui.mask_selected = {self.layer_id}, self.mask_index >= 0
                area.tag_redraw()
        if is_graph(tree):
            node_id = self.layer_id
            if 0 <= self.mask_index < len(layer.masks):
                node_id = mirror.mask_node_id(layer, self.mask_index, layer.masks[self.mask_index])
            elif self.channel and 0 <= self.effect_index < len(prop.effects):
                node_id = mirror.effect_node_id(layer, self.channel, self.effect_index, prop.effects[self.effect_index])
            tree['op_active'], tree['op_active_node'] = self.layer_id, node_id


def _graph_target(context):
    """(texture set, active layer) of the graph in this editor, else of the active set."""
    tree = getattr(context.space_data, 'node_tree', None)
    ts = mirror.texture_set_of(tree, context.scene) if is_graph(tree) else None
    ts = ts or require_set(context)
    from ..core.layer_stack import active
    layer = active(ts)
    if layer is None:
        raise ValueError('Select a layer node first: masks and filters are added to the active layer.')
    return ts, layer


class OP_OT_graph_add(SafeOperator, bpy.types.Operator):
    """Add a mask or channel filter node to the active layer (appended to its chain)."""
    bl_idname = 'open_painter.graph_add'
    bl_label = 'Add Node'
    bl_options = {'REGISTER', 'UNDO'}
    kind: bpy.props.EnumProperty(items=[(k, k.replace('_', ' ').title(), '') for k in (
        'MASK', 'GENERATOR', 'PROCEDURAL', 'SCENE', 'MASK_FILTER', 'REFERENCE', 'MASK_ANCHOR',
        'FILTER', 'CHANNEL_ANCHOR')])
    choice: bpy.props.StringProperty()
    channel: bpy.props.StringProperty()

    def run(self, context):
        from ..core.layer_stack import channel_for, invalidate
        from ..utils.ids import new_id
        ts, layer = _graph_target(context)
        title = self.choice.replace('_', ' ').title()
        if self.kind in {'FILTER', 'CHANNEL_ANCHOR'}:
            if layer.type == 'FOLDER':
                raise ValueError('Channel filters belong on fill and paint layers.')
            prop = channel_for(layer, self.channel or context.scene.open_painter.properties_channel)
            if prop is None:
                raise ValueError('Choose a channel for the filter.')
            effect = prop.effects.add()
            if self.kind == 'CHANNEL_ANCHOR':
                from ..ui.anchors import _next_name
                effect.id, effect.name, effect.kind = new_id(), _next_name(ts), 'ANCHOR'
            else:
                effect.kind, effect.name = self.choice, title
                effect.amount = 0 if self.choice in {'BRIGHTNESS_CONTRAST', 'TEMPERATURE'} else \
                    1 if self.choice in {'NORMAL_STRENGTH', 'NORMAL_COMBINE', 'HEIGHT_TO_NORMAL', 'AO_STRENGTH'} else .5
            prop.active_effect = len(prop.effects) - 1
            if layer.type != 'PAINT':
                context.scene.open_painter.properties_channel = prop.channel
            selected_mask = False
        else:
            mask = layer.masks.add()
            mask.id = new_id()
            if self.kind == 'MASK':
                mask.source = self.choice
                mask.name = self.choice.title() + ' Mask'
                if self.choice in {'PAINT', 'FILL'}:
                    from ..core.image_manager import create
                    mask.image = create(f'{ts.name}_{layer.name}_Mask',
                                        int(ts.resolution) if self.choice == 'PAINT' else 32, (1, 1, 1, 1))
                    mask.image.pack()
            elif self.kind == 'GENERATOR':
                mask.source, mask.generator, mask.name = 'GENERATOR', self.choice, title
            elif self.kind == 'PROCEDURAL':
                mask.source, mask.procedural, mask.name = 'PROCEDURAL', self.choice, title
            elif self.kind == 'SCENE':
                mask.source, mask.name = 'SCENE', title
                mask.influence.kind, mask.influence.mode = self.choice, 'LIVE'
            elif self.kind == 'MASK_FILTER':
                mask.source, mask.name, mask.effect.kind = 'FILTER', title, self.choice
            elif self.kind == 'REFERENCE':
                mask.source, mask.name = 'REFERENCE', 'Published Input'
            elif self.kind == 'MASK_ANCHOR':
                from ..ui.anchors import _next_name
                mask.source, mask.name = 'ANCHOR', _next_name(ts)
            layer.active_mask = len(layer.masks) - 1
            selected_mask = True
        invalidate(ts)
        from ..ui.dock import state
        for area in context.screen.areas:
            if area.type in {'VIEW_3D', 'IMAGE_EDITOR'}:
                ui = state.get(area)
                ui.selected_layers, ui.mask_selected = {layer.id}, selected_mask
                area.tag_redraw()


class _AddMenu:
    @classmethod
    def poll(cls, context):
        return getattr(context.space_data, 'tree_type', '') == TREE_ID

    def item(self, kind, choice, text, icon='NONE', channel=''):
        op = self.layout.operator('open_painter.graph_add', text=text, icon=icon)
        op.kind, op.choice, op.channel = kind, choice, channel


class OP_MT_graph_add_mask(_AddMenu, bpy.types.Menu):
    bl_idname = 'OP_MT_graph_add_mask'
    bl_label = 'Mask'

    def draw(self, context):
        for source, text, icon in (('PAINT', 'Painted', 'BRUSH_DATA'), ('FILL', 'Fill Image', 'IMAGE_DATA'),
                                   ('BAKED', 'Baked Map', 'RENDER_STILL'), ('NOISE', 'Noise', 'FORCE_TURBULENCE')):
            self.item('MASK', source, text, icon)
        self.layout.menu('OP_MT_graph_add_generator', icon='MOD_MASK')
        self.layout.menu('OP_MT_graph_add_procedural', icon='TEXTURE')
        self.layout.menu('OP_MT_graph_add_scene', icon='ORIENTATION_GLOBAL')
        self.layout.menu('OP_MT_graph_add_mask_filter', icon='SHADERFX')
        self.layout.separator()
        self.item('REFERENCE', '', 'Published Input', 'LINKED')
        self.item('MASK_ANCHOR', '', 'Anchor Point', 'ANCHOR_CENTER')


class OP_MT_graph_add_generator(_AddMenu, bpy.types.Menu):
    bl_idname = 'OP_MT_graph_add_generator'
    bl_label = 'Generator'

    def draw(self, context):
        from ..generators.base import REQUIRED_MAPS
        for kind in REQUIRED_MAPS:
            self.item('GENERATOR', kind, kind.replace('_', ' ').title())


class OP_MT_graph_add_procedural(_AddMenu, bpy.types.Menu):
    bl_idname = 'OP_MT_graph_add_procedural'
    bl_label = 'Procedural'

    def draw(self, context):
        from ..texture_engine.procedurals import CATALOG
        for kind in CATALOG:
            self.item('PROCEDURAL', kind, kind.replace('_', ' ').title())


class OP_MT_graph_add_scene(_AddMenu, bpy.types.Menu):
    bl_idname = 'OP_MT_graph_add_scene'
    bl_label = 'Scene Field'

    def draw(self, context):
        for item in bpy.types.OP_Influencer.bl_rna.properties['kind'].enum_items:
            self.item('SCENE', item.identifier, item.name)


class OP_MT_graph_add_mask_filter(_AddMenu, bpy.types.Menu):
    bl_idname = 'OP_MT_graph_add_mask_filter'
    bl_label = 'Filter Mask'

    def draw(self, context):
        from ..texture_engine.filters import CATALOG
        for kind in CATALOG:
            self.item('MASK_FILTER', kind, kind.replace('_', ' ').title())


class OP_MT_graph_add_filter(_AddMenu, bpy.types.Menu):
    bl_idname = 'OP_MT_graph_add_filter'
    bl_label = 'Channel Filter'

    def draw(self, context):
        from ..texture_engine.filters import CATALOG
        channel = context.scene.open_painter.properties_channel
        self.layout.label(text=CHANNEL_LABELS.get(channel, channel) + ' channel (change it in the Painter panel)')
        self.item('CHANNEL_ANCHOR', '', 'Anchor Point', 'ANCHOR_CENTER', channel=channel)
        self.layout.separator()
        for kind in CATALOG:
            self.item('FILTER', kind, kind.replace('_', ' ').title(), channel=channel)


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
    from ..core.layer_stack import active
    tree = context.space_data.node_tree
    ts = mirror.texture_set_of(tree, context.scene) if is_graph(tree) else None
    layer = active(ts) if ts is not None else None
    layout.label(text=('Add to ' + layer.name) if layer else 'Select a layer node first', icon='NODETREE')
    layout.menu('OP_MT_graph_add_mask', icon='MOD_MASK')
    layout.menu('OP_MT_graph_add_filter', icon='SHADERFX')
    layout.separator()
    for action, label, icon in (('FILL', 'Fill Layer', 'MATERIAL'), ('PAINT', 'Paint Layer', 'BRUSH_DATA'),
                                ('FOLDER', 'Folder', 'FILE_FOLDER')):
        layout.operator('open_painter.layer', text=label, icon=icon).action = action


CLASSES = (OP_OT_open_graph, OP_OT_graph_auto_layout, OP_OT_graph_select, OP_OT_graph_add,
           OP_MT_graph_add_mask, OP_MT_graph_add_generator, OP_MT_graph_add_procedural,
           OP_MT_graph_add_scene, OP_MT_graph_add_mask_filter, OP_MT_graph_add_filter)


def register():
    bpy.types.NODE_HT_header.prepend(header)
    bpy.types.NODE_MT_add.append(add_menu)


def unregister():
    bpy.types.NODE_MT_add.remove(add_menu)
    bpy.types.NODE_HT_header.remove(header)
