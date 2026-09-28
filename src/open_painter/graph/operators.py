"""Open Graph operators, header and add menu."""
import bpy

from ..constants import CHANNEL_LABELS
from ..core.project_manager import require_set
from ..ui.operators import SafeOperator
from . import mirror
from .rows import OP_OT_graph_toggle_rows
from .tree import IMAGE_PREFIX, SOCKET_CONTENT, TREE_ID, is_graph


def graph_area(screen, ts):
    for area in screen.areas:
        if area.type == 'NODE_EDITOR' and area.spaces.active.tree_type == TREE_ID:
            tree = area.spaces.active.node_tree
            if is_graph(tree) and tree.get('op_graph_set') == ts.id:
                return area
    return None


def target_node_id(context, ts):
    """The graph node of the Painter session's target (layer, mask or filter), or ''."""
    from ..ui import selection
    target = selection.resolve(context, ts)
    if target.layer is None:
        return ''
    if target.kind == 'MASK' and target.mask is not None:
        index = next(i for i, item in enumerate(target.layer.masks) if item.id == target.mask_id)
        return mirror.mask_node_id(target.layer, index, target.mask)
    if target.kind == 'FILTER' and target.effect is not None and target.channel is not None:
        index = next(i for i, item in enumerate(target.channel.effects) if item.id == target.effect_id)
        return mirror.effect_node_id(target.layer, target.channel_name, index, target.effect)
    return target.layer_id


def frame(area, tree, node_id=None, selected=False):
    """Frame the graph editor on one node (selected) or on everything; the layout is untouched."""
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    if region is None:
        return False
    if node_id:
        with mirror.syncing():
            for item in tree.nodes:
                item.select = getattr(item, 'op_id', '') == node_id
            target = next((item for item in tree.nodes if getattr(item, 'op_id', '') == node_id), None)
            if target is not None:
                tree.nodes.active = target
        tree['op_active_node'] = node_id
    try:
        with bpy.context.temp_override(area=area, region=region):
            if node_id or selected:
                bpy.ops.node.view_selected()
            else:
                bpy.ops.node.view_all()
    except RuntimeError:
        return False   # an area that has not drawn yet cannot be framed; the next open will
    return True


class OP_OT_open_graph(SafeOperator, bpy.types.Operator):
    bl_idname = 'open_painter.open_graph'
    bl_label = 'Edit Nodes'
    bl_description = ('Show the active texture set as a live node graph next to the Painter view, framed on the '
                      'selected layer, mask or filter (Full Graph shows everything)')
    bl_options = {'REGISTER'}
    focus: bpy.props.EnumProperty(name='Open At', default='SELECTED', options={'SKIP_SAVE'}, items=[
        ('SELECTED', 'Selected Item', 'Frame the node of the selected layer, mask or filter'),
        ('ALL', 'Full Graph', 'Frame the whole graph')])

    def run(self, context):
        ts = require_set(context)
        tree = mirror.ensure_tree(ts)
        node_id = target_node_id(context, ts) if self.focus == 'SELECTED' else ''
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
        frame(area, tree, node_id)
        area.tag_redraw()


class OP_OT_graph_view(SafeOperator, bpy.types.Operator):
    bl_idname = 'open_painter.graph_view'
    bl_label = 'Graph View'
    bl_options = {'REGISTER'}
    scope: bpy.props.EnumProperty(items=[('SELECTED', 'Frame Selected', 'Frame the node of the Painter selection'),
                                         ('ALL', 'Full Graph', 'Frame the whole graph')], default='ALL')

    @classmethod
    def description(cls, context, properties):
        return ('Frame the node of the layer, mask or filter selected in the Painter'
                if properties.scope == 'SELECTED' else 'Show the whole graph; node positions stay as they are')

    def run(self, context):
        tree = getattr(context.space_data, 'node_tree', None)
        ts = mirror.texture_set_of(tree, context.scene) if is_graph(tree) else None
        if ts is None:
            raise ValueError('This editor shows no Open Painter graph.')
        node_id = target_node_id(context, ts) if self.scope == 'SELECTED' else ''
        frame(context.area, tree, node_id)


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


class OP_OT_graph_add_image(SafeOperator, bpy.types.Operator):
    """Add an empty Image Texture node to this graph.

    Choose the image on the node itself: Open loads an external file, the
    dropdown lists the blend file's images. Link its output into a fill
    channel input to feed that channel.
    """
    bl_idname = 'open_painter.graph_add_image'
    bl_label = 'Add Image Texture Node'
    bl_description = "Add an empty Image Texture node; choose its image on the node"
    bl_options = {'REGISTER', 'UNDO'}

    def run(self, context):
        from ..utils.ids import new_id
        tree = getattr(context.space_data, 'node_tree', None)
        ts = mirror.texture_set_of(tree, context.scene) if is_graph(tree) else None
        if ts is None:
            raise ValueError('This editor shows no Open Painter graph.')
        try:
            region = next(r for r in context.area.regions if r.type == 'WINDOW')
            vx, vy = region.view2d.region_to_view(region.width // 2, region.height // 2)
        except (AttributeError, RuntimeError, StopIteration):
            vx, vy = 0.0, 0.0
        existing = sum(1 for item in tree.nodes if getattr(item, 'op_kind', '') == 'IMAGE')
        with mirror.syncing():
            node = tree.nodes.new('OPImageNode')
            identifier = IMAGE_PREFIX + new_id()
            node.op_id, node.op_kind = identifier, 'IMAGE'
            node.name = identifier
            node.label = 'Image Texture'
            node.width = 240
            node.location = (vx + (existing % 4) * 24, vy - (existing % 4) * 48)
            # The exit slot exists at birth: the mirror only re-enforces it on
            # its next pass, which is too late to wire the node right away.
            node.outputs.new(SOCKET_CONTENT, 'Image', identifier='out')
        # The node is already in the tree: select it on the next sync,
        # exclusively, so it can be placed immediately.
        mirror.request_select(tree, identifier)
        return {'FINISHED'}


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
        layer = next((item for item in ts.layers if item.id == self.layer_id), None)
        if layer is None:
            raise ValueError('That layer no longer exists.')
        from ..core.layer_stack import channel_for
        from ..ui import selection
        mask = layer.masks[self.mask_index] if 0 <= self.mask_index < len(layer.masks) else None
        prop = channel_for(layer, self.channel) if self.channel else None
        effect = prop.effects[self.effect_index] if prop is not None and 0 <= self.effect_index < len(prop.effects) else None
        selection.select(context, self.layer_id,
                         selection.MASK if mask is not None else selection.FILTER if effect is not None else selection.CONTENT,
                         mask_id=mask.id if mask is not None else None, channel=self.channel or None,
                         effect_id=effect.id if effect is not None else None, ts=ts)
        if is_graph(tree):
            node_id = self.layer_id
            if 0 <= self.mask_index < len(layer.masks):
                node_id = mirror.mask_node_id(layer, self.mask_index, layer.masks[self.mask_index])
            elif self.channel and 0 <= self.effect_index < len(prop.effects):
                node_id = mirror.effect_node_id(layer, self.channel, self.effect_index, prop.effects[self.effect_index])
            tree['op_active'], tree['op_active_node'] = self.layer_id, node_id


def _graph_target(context, require_layer=True):
    """(texture set, active layer) of the graph in this editor, else of the active set."""
    tree = getattr(context.space_data, 'node_tree', None)
    ts = mirror.texture_set_of(tree, context.scene) if is_graph(tree) else None
    ts = ts or require_set(context)
    from ..core.layer_stack import active
    layer = active(ts)
    node = tree.nodes.active if is_graph(tree) else None
    if node is not None and node.select:
        owner = mirror.owner_of(ts, getattr(node, 'op_id', ''))[0]
        if owner is not None:
            layer = owner
    if layer is None and require_layer:
        raise ValueError('Select a layer node first: masks and filters are added to the active layer.')
    return ts, layer


def _add_edge_wear_2(ts, layer):
    """Append the Edge Wear 2.0 recipe, wired and on; return the first node id.

    Edge Wear (0.11 amount, 4.32 contrast, baked edge maps) into Grunge Medium
    (triplanar, hardness 1) into a Levels filter (white 0.28, gamma 0.5), all
    multiplying. A designed preset arrives working, exactly like its reference
    setup; lone mask adds stay parked until wired.
    """
    from ..utils.ids import new_id
    first = None
    for source, name, values in (
            ('GENERATOR', 'Edge Wear', {'generator': 'EDGE_WEAR', 'amount': 0.11, 'contrast': 4.32,
                                        'noise_scale': 1.0, 'edge_source': 'BAKED'}),
            ('PROCEDURAL', 'Grunge Medium', {'procedural': 'GRUNGE_MEDIUM', 'noise_scale': 2.0, 'seed': 1,
                                             'projection': 'TRIPLANAR', 'triplanar_hardness': 1.0,
                                             'scale': (1.0, 1.0), 'rotation': 0.0, 'offset': (0.0, 0.0)}),
            ('FILTER', 'Levels', {'effect_kind': 'LEVELS', 'effect_black': 0.0, 'effect_white': 0.28,
                                  'effect_gamma': 0.5, 'effect_output_black': 0.0, 'effect_output_white': 1.0})):
        mask = layer.masks.add()
        mask.id, mask.source, mask.name = new_id(), source, name
        mask.operation, mask.strength, mask.black, mask.white = 'MULTIPLY', 1.0, 0.0, 1.0
        mask.enabled = True
        if source == 'GENERATOR':
            mask.generator = values['generator']
            mask.amount, mask.contrast, mask.noise_scale = values['amount'], values['contrast'], values['noise_scale']
            mask.edge_source = values['edge_source']
        elif source == 'PROCEDURAL':
            mask.procedural = values['procedural']
            mask.noise_scale, mask.seed = values['noise_scale'], values['seed']
            mask.projection, mask.triplanar_hardness = values['projection'], values['triplanar_hardness']
            mask.scale = values['scale']
            mask.rotation = values['rotation']
            mask.offset = values['offset']
        else:
            mask.effect.kind = values['effect_kind']
            mask.effect.black, mask.effect.white = values['effect_black'], values['effect_white']
            mask.effect.gamma = values['effect_gamma']
            mask.effect.output_black, mask.effect.output_white = (values['effect_output_black'],
                                                                  values['effect_output_white'])
        if first is None:
            first = mirror.mask_node_id(layer, len(layer.masks) - 1, mask)
    layer.active_mask = len(layer.masks) - 1
    return first


class OP_OT_graph_add(SafeOperator, bpy.types.Operator):
    """Add a mask or channel filter node to the active layer, parked until wired."""
    bl_idname = 'open_painter.graph_add'
    bl_label = 'Add Node'
    bl_options = {'REGISTER', 'UNDO'}
    kind: bpy.props.EnumProperty(items=[(k, k.replace('_', ' ').title(), '') for k in (
        'MASK', 'GENERATOR', 'PROCEDURAL', 'SCENE', 'MASK_FILTER', 'REFERENCE', 'MASK_ANCHOR',
        'FILTER', 'CHANNEL_ANCHOR', 'LAYER', 'PROCEDURAL_FILL', 'MESH', 'RECIPE')])
    choice: bpy.props.StringProperty()
    channel: bpy.props.StringProperty()

    def run(self, context):
        from . import authoring
        ts, layer = _graph_target(context, require_layer=False)
        tree = getattr(context.space_data, 'node_tree', None)
        node = tree.nodes.active if is_graph(tree) else None
        mask_index = None
        if node is not None and node.select and layer is not None:
            mask_index = next((i for i, mask in enumerate(layer.masks) if mask.id == getattr(node, 'op_id', '')), None)
        identifier = authoring.add(ts, layer, self.kind, self.choice,
                                   self.channel or context.scene.open_painter.properties_channel, mask_index)
        if self.kind not in {'LAYER', 'PROCEDURAL_FILL', 'RECIPE', 'MASK_ANCHOR', 'CHANNEL_ANCHOR'}:
            _, mask, _, effect = mirror.owner_of(ts, identifier)
            item = mask if mask is not None else effect
            if item is not None:
                item.enabled = False
        _select_added(context, ts, identifier)


def _select_added(context, ts, identifier):
    from ..ui import selection
    layer, mask, prop, effect = mirror.owner_of(ts, identifier)
    selection.select(context, layer.id, selection.CONTENT, ts=ts, synchronize=False,
                     mask_id=mask.id if mask is not None else None,
                     channel=prop.channel if prop is not None else None,
                     effect_id=effect.id if effect is not None else None)
    tree = getattr(context.space_data, 'node_tree', None)
    if is_graph(tree):
        mirror.reconcile(tree, ts)
        with mirror.syncing():
            for node in tree.nodes:
                node.select = getattr(node, 'op_id', '') == identifier
            tree.nodes.active = mirror.nodes_by_id(tree)[identifier]
        tree['op_active'], tree['op_active_node'] = layer.id, identifier
    context.scene.open_painter.status = ''


class OP_OT_graph_template(SafeOperator, bpy.types.Operator):
    bl_idname = 'open_painter.graph_template'
    bl_label = 'Add Graph Template'
    bl_description = 'Insert a connected, editable node group into this texture set'
    bl_options = {'REGISTER', 'UNDO'}
    from .templates import ITEMS
    template: bpy.props.EnumProperty(name='Template', items=ITEMS)

    @classmethod
    def description(cls, context, properties):
        from .templates import TEMPLATES
        return TEMPLATES.get(properties.template, {}).get('description', cls.bl_description)

    def run(self, context):
        from .templates import apply
        from ..core.layer_stack import index_of
        ts, layer = _graph_target(context, require_layer=False)
        parent = (layer.id if layer.type == 'FOLDER' else layer.parent_id) if layer is not None else ''
        if layer is not None:
            ts.active_layer = index_of(ts, layer.id)
        folder = apply(ts, self.template, parent)
        _select_added(context, ts, folder.id)
        tree = getattr(context.space_data, 'node_tree', None)
        if is_graph(tree):
            from ..core.layer_stack import descendants
            members = descendants(ts, folder.id) | {folder.id}
            with mirror.syncing():
                for node in tree.nodes:
                    node.select = getattr(node, 'op_owner', '') in members
            frame(context.area, tree, selected=True)


_SEARCH_ITEMS = []


def _search_items(self, context):
    # Blender retains enum strings: keep the backing list alive between draws.
    if not _SEARCH_ITEMS:
        from ..texture_engine.procedurals import CATALOG as procedurals
        from ..texture_engine.filters import CATALOG as filters
        from ..properties.mesh_selector import KINDS
        from ..properties.mask import OP_Mask
        from ..properties.influencer import OP_Influencer
        from .templates import ITEMS
        def extend(kind, category, items):
            for key, name, description in items:
                _SEARCH_ITEMS.append((kind + ':' + key, category + ' / ' + name, description))
        extend('LAYER', 'Layer', [(k, k.title(), '') for k in ('FILL', 'PAINT', 'FOLDER')])
        extend('MASK', 'Mask', [(k, name, '') for k, name in (
            ('PAINT', 'Painted'), ('FILL', 'Fill Image'), ('BAKED', 'Baked Map'), ('NOISE', 'Noise'),
            ('GROUP', 'Mask Group'), ('GRAPH', 'Custom Graph'), ('STROKES', 'Vector Strokes'))])
        extend('MESH', 'Mesh Mask', KINDS)
        for kind, category, catalog in (('PROCEDURAL', 'Procedural Mask', procedurals),
                                         ('PROCEDURAL_FILL', 'Procedural Fill', procedurals),
                                         ('FILTER', 'Channel Filter', filters), ('MASK_FILTER', 'Mask Filter', filters)):
            extend(kind, category, [(key, key.replace('_', ' ').title(), str(value)) for key, value in catalog.items()])
        for kind, category, rna, field in (('GENERATOR', 'Generator', OP_Mask, 'generator'),
                                          ('SCENE', 'Scene Field', OP_Influencer, 'kind')):
            extend(kind, category, [(i.identifier, i.name, i.description) for i in rna.bl_rna.properties[field].enum_items])
        for kind, name in (('REFERENCE', 'Published Input'), ('MASK_ANCHOR', 'Mask Anchor'),
                           ('CHANNEL_ANCHOR', 'Channel Anchor')):
            extend(kind, 'Anchor', [('', name, '')])
        extend('TEMPLATE', 'Template', ITEMS)
    return _SEARCH_ITEMS


class OP_OT_graph_search(SafeOperator, bpy.types.Operator):
    bl_idname = 'open_painter.graph_search'
    bl_label = 'Search Nodes and Templates'
    bl_options = {'REGISTER', 'UNDO'}
    bl_property = 'node'
    node: bpy.props.EnumProperty(name='Node', items=_search_items)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {'RUNNING_MODAL'}

    def run(self, context):
        kind, _, choice = self.node.partition(':')
        if kind == 'TEMPLATE':
            return bpy.ops.open_painter.graph_template(template=choice)
        return bpy.ops.open_painter.graph_add(kind=kind, choice=choice)


class OP_OT_graph_group(SafeOperator, bpy.types.Operator):
    bl_idname = 'open_painter.graph_group'
    bl_label = 'Group Selected Layers'
    bl_description = 'Put consecutive selected sibling layers into an editable folder'
    bl_options = {'REGISTER', 'UNDO'}

    def run(self, context):
        from ..core import layer_stack as stack
        ts, _ = _graph_target(context, require_layer=False)
        tree = getattr(context.space_data, 'node_tree', None)
        if not is_graph(tree):
            raise ValueError('Select layer nodes in an Open Painter graph first.')
        selected = {node.op_id for node in tree.nodes if node.select and getattr(node, 'op_kind', '') in {'LAYER', 'FOLDER'}}
        roots = stack.selection_roots(ts, selected)
        if not roots:
            raise ValueError('Select the layer nodes to group.')
        parents = {layer.parent_id for layer in ts.layers if layer.id in roots}
        if len(parents) != 1:
            raise ValueError('Group layers in the same folder to preserve their composition order.')
        siblings = [layer.id for layer in ts.layers if layer.parent_id in parents]
        positions = [siblings.index(identifier) for identifier in roots]
        if max(positions) - min(positions) + 1 != len(positions):
            raise ValueError('Select consecutive layers to preserve their composition order.')
        with stack.replay(ts):
            folder = stack.group(ts, roots, 'Node Group')
        _select_added(context, ts, folder.id)


class OP_OT_graph_edit_shader(SafeOperator, bpy.types.Operator):
    bl_idname = 'open_painter.graph_edit_shader'
    bl_label = 'Edit Shader Group'
    bl_description = 'Open this mask’s reusable shader group in a separate editor'
    mask_id: bpy.props.StringProperty()

    def run(self, context):
        ts, _ = _graph_target(context, require_layer=False)
        _, mask, _, _ = mirror.owner_of(ts, self.mask_id)
        if mask is None or mask.source != 'GRAPH' or mask.node_group is None:
            raise ValueError('Choose a Custom Graph mask with a shader group first.')
        group = mask.node_group
        from ..procedural_graph import edits, is_native
        if is_native(group):
            edits.show(context, group)   # a Mask Graph opens in its own editor, not the Shader Editor
            return
        existing = next((area for area in context.screen.areas if area.type == 'NODE_EDITOR'
                         and area.spaces.active.tree_type == 'ShaderNodeTree'
                         and area.spaces.active.edit_tree == group), None)
        if existing is None:
            before = {area.as_pointer() for area in context.screen.areas}
            bpy.ops.screen.area_split(direction='VERTICAL', factor=.5)
            existing = next((area for area in context.screen.areas if area.as_pointer() not in before), None)
            if existing is None:
                raise ValueError('Blender could not split this area. Try a larger editor area.')
            existing.ui_type = 'ShaderNodeTree'
            existing.spaces.active.pin = True
            existing.spaces.active.path.start(group)
        frame(existing, group)


class _AddMenu:
    @classmethod
    def poll(cls, context):
        space = context.space_data
        return getattr(space, 'tree_type', '') == TREE_ID and bool(is_graph(getattr(space, 'edit_tree', None)))

    def item(self, kind, choice, text, icon='NONE', channel=''):
        op = self.layout.operator('open_painter.graph_add', text=text, icon=icon)
        op.kind, op.choice, op.channel = kind, choice, channel


class OP_MT_graph_add_mask(_AddMenu, bpy.types.Menu):
    bl_idname = 'OP_MT_graph_add_mask'
    bl_label = 'Mask'

    def draw(self, context):
        for source, text, icon in (('PAINT', 'Painted', 'BRUSH_DATA'), ('FILL', 'Fill Image', 'IMAGE_DATA'),
                                   ('BAKED', 'Baked Map', 'RENDER_STILL'), ('NOISE', 'Noise', 'FORCE_TURBULENCE'),
                                   ('GROUP', 'Mask Group', 'NODETREE'), ('GRAPH', 'Custom Graph', 'NODETREE'),
                                   ('STROKES', 'Vector Strokes', 'CURVE_DATA')):
            self.item('MASK', source, text, icon)
        self.layout.menu('OP_MT_graph_add_generator', icon='MOD_MASK')
        self.layout.menu('OP_MT_graph_add_procedural', icon='TEXTURE')
        self.layout.menu('OP_MT_graph_add_scene', icon='ORIENTATION_GLOBAL')
        self.layout.menu('OP_MT_graph_add_mesh', icon='MESH_DATA')
        self.layout.menu('OP_MT_graph_add_mask_filter', icon='SHADERFX')
        self.layout.separator()
        self.item('REFERENCE', '', 'Published Input', 'LINKED')
        self.item('MASK_ANCHOR', '', 'Anchor Point', 'ANCHOR_CENTER')


class OP_MT_graph_add_generator(_AddMenu, bpy.types.Menu):
    bl_idname = 'OP_MT_graph_add_generator'
    bl_label = 'Generator'

    def draw(self, context):
        self.item('RECIPE', 'EDGE_WEAR_2', 'Edge Wear 2.0', 'MOD_MASK')
        self.layout.separator()
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
        for identifier, name in _scene_kind_items():
            self.item('SCENE', identifier, name)


def _scene_kind_items():
    """(identifier, name) pairs for scene influencer kinds, registration-proof.

    Menu draws keep running across disable/enable (the reinstall script):
    ``bpy.types.OP_Influencer`` vanishes in that window and every redraw
    logged an AttributeError instead of drawing.
    """
    candidates = [getattr(bpy.types, 'OP_Influencer', None)]
    try:
        from ..properties.influencer import OP_Influencer
        candidates.append(OP_Influencer)
    except ImportError:
        pass
    for candidate in candidates:
        try:
            items = candidate.bl_rna.properties['kind'].enum_items
        except (AttributeError, KeyError):
            continue
        return tuple((item.identifier, item.name) for item in items)
    return ()


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
        self.layout.label(text=CHANNEL_LABELS.get(channel, channel) + ' channel')
        self.item('CHANNEL_ANCHOR', '', 'Anchor Point', 'ANCHOR_CENTER', channel=channel)
        self.layout.separator()
        for kind in CATALOG:
            self.item('FILTER', kind, kind.replace('_', ' ').title(), channel=channel)


class OP_MT_graph_add_mesh(_AddMenu, bpy.types.Menu):
    bl_idname = 'OP_MT_graph_add_mesh'
    bl_label = 'Mesh Mask'

    def draw(self, context):
        from ..properties.mesh_selector import KINDS
        for kind, name, _ in KINDS:
            self.item('MESH', kind, name, 'MESH_DATA')


class OP_MT_graph_add_fill(_AddMenu, bpy.types.Menu):
    bl_idname = 'OP_MT_graph_add_fill'
    bl_label = 'Procedural Fill'

    def draw(self, context):
        from ..texture_engine.procedurals import CATALOG
        for kind in CATALOG:
            self.item('PROCEDURAL_FILL', kind, kind.replace('_', ' ').title(), 'TEXTURE')


class OP_MT_graph_templates(_AddMenu, bpy.types.Menu):
    bl_idname = 'OP_MT_graph_templates'
    bl_label = 'Templates'

    def draw(self, context):
        from .templates import TEMPLATES
        for key, recipe in TEMPLATES.items():
            if key == 'NOISE_RAMP':
                self.layout.separator()
                self.layout.label(text='Shader Groups')
            self.layout.operator('open_painter.graph_template', text=recipe['name'], icon='NODETREE').template = key


_reports = {}   # graph tree address -> (structural signature, errors, warnings)


def _reference_report(tree, ts):
    """Reference errors and warnings of the set, once per structure the graph showed.

    The header draws on every redraw of the editor (each slider step of an edit);
    references change only with the structure, which ``tree['op_signature']`` tracks.
    """
    from ..evaluation.references import graph as reference_graph
    token = tree.get('op_signature')
    key = tree.as_pointer()
    cached = _reports.get(key)
    if cached is None or cached[0] != token or token is None:
        _, problems, notes = reference_graph(ts)
        if len(_reports) > 32:
            _reports.clear()
        cached = _reports[key] = (token, list(problems), list(notes))
    return cached[1], cached[2]


def _mask_graph_header(layout, space, tree):
    """Inside a Mask Graph: which graph, the masks that read it, and the way back."""
    from ..procedural_graph.edits import users
    layout.label(text=f'Mask Graph · {tree.name}', icon='NODETREE')
    readers = [mask.name or 'Custom Graph' for _, mask in users(tree)]
    if readers:
        layout.label(text='Used by ' + ', '.join(readers[:3]) + (' …' if len(readers) > 3 else ''))
    if len(space.path) > 1:
        layout.operator('node.tree_path_parent', text='Back to Texture Set Graph', icon='FILE_PARENT')


def header(self, context):
    space = context.space_data
    if getattr(space, 'tree_type', '') != TREE_ID:
        return
    layout = self.layout
    from ..procedural_graph import is_native
    from ..painting.brush_graph import is_brush
    if is_brush(space.edit_tree):
        layout.label(text=f'Brush Graph · {space.edit_tree.name}', icon='BRUSH_DATA')
        layout.template_ID(context.scene.open_painter.vector_brush, 'node_group')
        return
    if is_native(space.edit_tree):
        _mask_graph_header(layout, space, space.edit_tree)
        return
    tree = space.node_tree
    ts = mirror.texture_set_of(tree, context.scene) if is_graph(tree) else None
    if ts is None:
        layout.label(text='Open Painter Graph: open it from the Layers panel', icon='NODETREE')
        return
    # The scope: the same path the inspector shows, so both views name the same item.
    from ..ui import selection
    target = selection.resolve(context, ts)
    layout.label(text=selection.breadcrumb_text(target), icon='NODETREE')
    layout.operator('open_painter.graph_search', text='', icon='VIEWZOOM')
    layout.menu('OP_MT_graph_templates', icon='NODETREE')
    layout.prop(context.scene.open_painter, 'properties_channel', text='Channel')
    layout.operator('open_painter.graph_group', text='Group', icon='FILE_FOLDER')
    row = layout.row(align=True)
    row.operator('open_painter.graph_view', text='Frame Selected', icon='RESTRICT_SELECT_OFF').scope = 'SELECTED'
    row.operator('open_painter.graph_view', text='Full Graph', icon='VIEW_ORTHO').scope = 'ALL'
    layout.operator('open_painter.graph_auto_layout', text='Auto Layout', icon='SNAP_GRID')
    from . import rows as _rows
    layout.operator('open_painter.graph_toggle_rows', text='Rows', icon='GRID',
                    depress=_rows.is_enabled())
    problems, notes = _reference_report(tree, ts)
    if problems:
        layout.label(text=problems[0], icon='ERROR')
    elif notes:
        layout.label(text=notes[0], icon='INFO')
    status = context.scene.open_painter.status
    if status:
        layout.label(text=status)


def add_menu(self, context):
    space = context.space_data
    if getattr(space, 'tree_type', '') != TREE_ID:
        return
    from ..procedural_graph import is_native
    if is_native(space.edit_tree):
        return   # a Mask Graph's own nodes (procedural_graph.tree.add_menu)
    layout = self.layout
    from ..core.layer_stack import active
    tree = space.node_tree
    ts = mirror.texture_set_of(tree, context.scene) if is_graph(tree) else None
    layer = active(ts) if ts is not None else None
    layout.operator('open_painter.graph_search', icon='VIEWZOOM')
    layout.menu('OP_MT_graph_templates', icon='NODETREE')
    layout.separator()
    layout.label(text=('Add to ' + layer.name) if layer else 'Add a layer to start', icon='NODETREE')
    layout.menu('OP_MT_graph_add_mask', icon='MOD_MASK')
    layout.menu('OP_MT_graph_add_filter', icon='SHADERFX')
    layout.separator()
    layout.operator('open_painter.graph_add_image', text='Image Texture', icon='IMAGE_DATA')
    layout.separator()
    for action, label, icon in (('FILL', 'Fill Layer', 'MATERIAL'), ('PAINT', 'Paint Layer', 'BRUSH_DATA'),
                                ('FOLDER', 'Folder', 'FILE_FOLDER')):
        op = layout.operator('open_painter.graph_add', text=label, icon=icon)
        op.kind, op.choice = 'LAYER', action
    layout.menu('OP_MT_graph_add_fill', icon='TEXTURE')


CLASSES = (OP_OT_open_graph, OP_OT_graph_view, OP_OT_graph_auto_layout, OP_OT_graph_select, OP_OT_graph_add,
           OP_OT_graph_add_image, OP_OT_graph_toggle_rows,
           OP_MT_graph_add_mask, OP_MT_graph_add_generator, OP_MT_graph_add_procedural,
           OP_MT_graph_add_scene, OP_MT_graph_add_mask_filter, OP_MT_graph_add_filter,
           OP_OT_graph_template, OP_OT_graph_search, OP_OT_graph_group, OP_OT_graph_edit_shader,
           OP_MT_graph_templates, OP_MT_graph_add_mesh, OP_MT_graph_add_fill)


def register():
    from . import rows
    rows.register_handlers()
    bpy.types.NODE_HT_header.prepend(header)
    bpy.types.NODE_MT_add.append(add_menu)


def unregister():
    from . import rows
    rows.unregister_handlers()
    mirror.clear_caches()
    bpy.types.NODE_MT_add.remove(add_menu)
    bpy.types.NODE_HT_header.remove(header)
