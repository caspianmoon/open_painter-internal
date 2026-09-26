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


def show(context, tree):
    """Show ``tree`` in a node editor: one already showing it, or half of the current area
    (a node editor or 3D view when the call has no area). Raises ``ValueError`` when no area can be split."""
    from .tree import TREE_ID
    screen = context.screen
    area = next((a for a in screen.areas if a.type == 'NODE_EDITOR' and a.spaces.active.tree_type == TREE_ID
                 and a.spaces.active.node_tree == tree), None)
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
        area.spaces.active.pin = True
        area.spaces.active.node_tree = tree
    with context.temp_override(area=area, region=next(r for r in area.regions if r.type == 'WINDOW')):
        try:
            bpy.ops.node.view_all()
        except RuntimeError:
            pass
    return area


class OP_OT_mask_graph_edit(bpy.types.Operator):
    """Open this mask's Mask Graph in a node editor beside this one"""
    bl_idname = 'open_painter.mask_graph_edit'
    bl_label = 'Edit Mask Graph'
    bl_options = {'REGISTER'}

    tree: bpy.props.StringProperty()

    def execute(self, context):
        from .tree import TREE_ID
        tree = bpy.data.node_groups.get(self.tree)
        if tree is None or tree.bl_idname != TREE_ID:
            self.report({'ERROR'}, 'Choose a Custom Graph mask with a Mask Graph first')
            return {'CANCELLED'}
        try:
            show(context, tree)
        except ValueError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


CLASSES = (OP_OT_mask_graph_edit,)
