"""Mask Graph edits reach the texture sets whose masks use the graph.

A numeric edit (a slider, an unlinked socket, a ramp stop) invalidates like a mask
slider: the stack is marked dirty and the engine binds the new uniform (a graph
node's bindings answer to the mask, see ``Graph.native``). A structural edit (a
link, a node, a kind or operation) rebuilds the graph's own unit only.
"""
import bpy

_structures = {}   # tree pointer -> ``program.unit_signature`` last seen by ``tree_changed``


def users(tree):
    """(masks' layer's texture set, mask) of every Custom Graph mask reading ``tree``."""
    for scene in bpy.data.scenes:
        project = getattr(scene, 'open_painter', None)
        if project is None:
            continue
        for ts in project.texture_sets:
            for layer in ts.layers:
                for mask in layer.masks:
                    if mask.source == 'GRAPH' and mask.node_group == tree:
                        yield ts, mask


def _notify(tree, structural):
    from ..core.layer_stack import replaying
    if replaying():
        return
    from ..properties.layer import _changed
    for _, mask in list(users(tree)):
        _changed(mask, bpy.context, structural)


def changed(owner, structural):
    """A node or socket property edit (``tree`` property callbacks)."""
    tree = owner.id_data
    if structural:
        from .program import unit_signature
        _structures[tree.as_pointer()] = unit_signature(tree)
    _notify(tree, structural)


def values_changed(tree):
    _notify(tree, False)


def tree_changed(tree):
    """``NodeTree.update``: links and nodes changed. Only a changed structure (or a node
    replaced by an identical one) rebuilds; moving a node or adding an unlinked one does not."""
    from .program import unit_signature
    signature = unit_signature(tree)
    pointer = tree.as_pointer()
    if _structures.get(pointer) == signature:
        return
    _structures[pointer] = signature
    _notify(tree, True)


def forget():
    _structures.clear()


def _window_region(area):
    return next(region for region in area.regions if region.type == 'WINDOW')


def show(context, tree):
    """Edit ``tree`` in the Open Painter Graph editor, entered from the graph of the texture set
    whose mask reads it (the breadcrumb and Tab lead back to it): an editor already editing it,
    that set's graph editor, the current graph editor, or half of the current area. Raises
    ``ValueError`` when no area can be split. Returns the area."""
    from ..graph import mirror
    from ..graph.tree import TREE_ID, is_graph
    screen = context.screen
    editors = [a for a in screen.areas if a.type == 'NODE_EDITOR' and a.spaces.active.tree_type == TREE_ID]
    area = next((a for a in editors if a.spaces.active.edit_tree == tree), None)
    if area is None:
        owner = next((ts for ts, _ in users(tree)), None)
        area = next((a for a in editors if owner is not None and is_graph(a.spaces.active.node_tree)
                     and a.spaces.active.node_tree.get('op_graph_set') == owner.id), None)
        if area is None and context.area is not None and any(a == context.area for a in editors):
            area = context.area
        if area is None:
            before = {a.as_pointer() for a in screen.areas}
            target = context.area if context.area is not None else next(
                (a for a in screen.areas if a.type in {'NODE_EDITOR', 'VIEW_3D'}), None)
            if target is None:
                raise ValueError('Open a node editor or a 3D view to split for the graph')
            with context.temp_override(area=target):
                bpy.ops.screen.area_split(direction='VERTICAL', factor=.5)
            area = next((a for a in screen.areas if a.as_pointer() not in before), None)
            if area is None:
                raise ValueError('Blender could not split this area; try a larger editor')
            area.ui_type = TREE_ID
            space = area.spaces.active
            space.pin = True
            space.node_tree = mirror.ensure_tree(owner) if owner is not None else tree
        space = area.spaces.active
        if space.edit_tree != tree:
            with context.temp_override(area=area, region=_window_region(area)):
                space.path.append(tree)   # entered from the set's graph, like a node group
    with context.temp_override(area=area, region=_window_region(area)):
        try:
            bpy.ops.node.view_all()
        except RuntimeError:
            pass
    area.tag_redraw()
    _frame_after_draw(area, tree)
    return area


def _frame_after_draw(area, tree):
    """Frame the graph again once the editor has drawn it: node sizes are known only then,
    so framing at once can zoom into a corner of the graph."""
    pointer, name = area.as_pointer(), tree.name

    def frame():
        for window in bpy.context.window_manager.windows:
            for item in window.screen.areas:
                if item.as_pointer() != pointer or item.type != 'NODE_EDITOR':
                    continue
                edit = item.spaces.active.edit_tree
                if edit is not None and edit.name == name:
                    with bpy.context.temp_override(window=window, area=item, region=_window_region(item)):
                        try:
                            bpy.ops.node.view_all()
                        except RuntimeError:
                            pass
        return None

    bpy.app.timers.register(frame, first_interval=0.2)


class OP_OT_mask_graph_edit(bpy.types.Operator):
    """Edit this mask's Mask Graph in the Open Painter Graph editor (Tab returns to the texture set's graph)"""
    bl_idname = 'open_painter.mask_graph_edit'
    bl_label = 'Edit Mask Graph'
    bl_options = {'REGISTER'}

    tree: bpy.props.StringProperty()

    def execute(self, context):
        from . import is_native
        tree = bpy.data.node_groups.get(self.tree)
        if not is_native(tree):
            self.report({'ERROR'}, 'Choose a Custom Graph mask with a Mask Graph first')
            return {'CANCELLED'}
        try:
            show(context, tree)
        except ValueError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


CLASSES = (OP_OT_mask_graph_edit,)
