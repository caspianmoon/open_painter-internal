"""Horizontal row guides and Y-order for the Open Graph.

The Open Graph is a projection of the layer stack (``graph/mirror.py``): today
sibling layers are chained with ``below`` links, which always draw sideways
(right output into left input) even when the nodes sit in a vertical column.
In a layer-stack workflow the vertical position itself should carry the
order: a layer on a higher row is above (on top of) the layers below it.

This module adds that in two additive pieces (no existing behavior removed
except root Backdrop chaining, which Y rows replace):

* ``POST_VIEW`` row guides: faint static horizontal lines across the node
  editor's empty space, so rows can be seen and aimed at. (``BACKDROP``
  handlers draw in region pixel space, so the grid would stick to the screen
  under zoom; ``POST_VIEW`` draws in node-view coordinates and pans/zooms
  with the canvas.) Grid lines are fixed band boundaries at ``k * ROW_HEIGHT``;
  nothing follows the nodes: layer rows live *between* the lines.
* ``order_from_y``: called from ``mirror.sync`` (the 0.15 s lifecycle tick).
  When the tree is settled (signature matches, no pending link edits), the
  root siblings' top-to-bottom order is read from ``location.y`` (higher = on
  top) and written back into ``ts.layers`` (stored bottom-first) through the
  same ``move`` + ``invalidate`` path the panel uses. Nested children keep
  their link-driven order. The next sync then reconciles the output links to
  match, so link edits and Y drags converge instead of fighting.

Root layers fan out into the Texture Set Output node (one stack socket each)
instead of chaining through Backdrop: with a single-link input per socket,
only per-root sockets can show every root feeding the same sink.
"""

import bpy

ROW_HEIGHT = 320.0
GRID_HALF_X = 12000.0
GRID_RANGE = 40  # lines at k * ROW_HEIGHT for k in [-GRID_RANGE, GRID_RANGE]
GRID_COLOR = (1.0, 1.0, 1.0, 0.07)
# Two root rows closer than this count as tied: never reorder on ambiguity.
TIE_EPSILON = 8.0

_show_rows = True
_handler = None
_shader = None
_batch = None


# ---- pure helpers (no bpy data: unit-testable) -------------------------------

# Rows are horizontal bands between grid lines. Boundaries sit at multiples of
# ROW_HEIGHT; row ``i`` spans [-(i+1)*ROW_HEIGHT, -i*ROW_HEIGHT] and its centre
# (where layer nodes snap) is ``snapped_y(i)``. Index grows downwards, so a
# higher row (larger Y) is a lower index and therefore higher in the stack.


def row_index_for_y(y):
    """Band index containing view Y (floor: row 0 is [-H, 0])."""
    from math import floor
    return int(floor(-float(y) / ROW_HEIGHT))


def snapped_y(index):
    """View Y of the centre of row ``index`` (between two grid lines)."""
    return -float(index) * ROW_HEIGHT - ROW_HEIGHT / 2.0


def grid_ys(count=GRID_RANGE):
    """Y positions of the fixed empty-space grid lines (band boundaries)."""
    return [k * ROW_HEIGHT for k in range(-count, count + 1)]


def desired_root_order_bottom_first(ids, y_by_id):
    """Root layer ids, bottom-first, from node heights (higher Y = on top).

    ``ids`` are root layer ids, ``y_by_id`` maps each to its node Y.
    Storage is bottom-first, so the lowest node comes first. Ties break by
    id for determinism.
    """
    return sorted(ids, key=lambda lid: (float(y_by_id[lid]), lid))


def is_ambiguous(y_values):
    """True when any two rows are too close to read an order from."""
    ordered = sorted(float(y) for y in y_values)
    return any(b - a < TIE_EPSILON for a, b in zip(ordered, ordered[1:]))


# ---- visibility toggle -------------------------------------------------------

def is_enabled():
    return _show_rows


def set_enabled(value):
    global _show_rows
    _show_rows = bool(value)


class OP_OT_graph_toggle_rows(bpy.types.Operator):
    bl_idname = 'open_painter.graph_toggle_rows'
    bl_label = 'Row Guides'
    bl_description = 'Show or hide the horizontal layer rows behind the Open Painter graph'
    bl_options = {'REGISTER'}

    def execute(self, context):
        set_enabled(not is_enabled())
        for area in getattr(context.screen, 'areas', ()):
            if getattr(area, 'type', '') == 'NODE_EDITOR':
                area.tag_redraw()
        return {'FINISHED'}


# ---- node-view drawing --------------------------------------------------------

def _draw():
    """POST_VIEW handler: a fixed grid in node-view coordinates, behind the nodes.

    Deliberately static: the lines never read node positions, so dragging a
    layer never drags a row with it.
    """
    try:
        if not _show_rows:
            return
        space = bpy.context.space_data
        if space is None or getattr(space, 'tree_type', '') != 'OpenPainterGraph':
            return
        tree = getattr(space, 'node_tree', None)
        if tree is None or getattr(tree, 'bl_idname', '') != 'OpenPainterGraph':
            return
        import gpu
        from gpu_extras.batch import batch_for_shader
        global _shader, _batch
        if _shader is None:
            _shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        if _batch is None:
            # Node-view coordinates are fixed: panning and zooming only change
            # Blender's view matrix. Reuse the GPU buffer across redraws.
            lines = [(x, y) for y in grid_ys() for x in (-GRID_HALF_X, GRID_HALF_X)]
            _batch = batch_for_shader(_shader, 'LINES', {'pos': lines})
        blend = gpu.state.blend_get()
        gpu.state.blend_set('ALPHA')
        try:
            _shader.bind()
            _shader.uniform_float('color', GRID_COLOR)
            _batch.draw(_shader)
        finally:
            gpu.state.blend_set(blend)
    except (AttributeError, ImportError):
        return


def register_handlers():
    """Add the POST_VIEW handler once; a no-op in background (tests)."""
    global _handler
    if _handler is not None or getattr(bpy.app, 'background', False):
        return
    try:
        _handler = bpy.types.SpaceNodeEditor.draw_handler_add(_draw, (), 'WINDOW', 'POST_VIEW')
    except (AttributeError, ValueError):
        _handler = None


def unregister_handlers():
    global _handler, _shader, _batch
    _batch = None
    _shader = None
    if _handler is None:
        return
    try:
        bpy.types.SpaceNodeEditor.draw_handler_remove(_handler, 'WINDOW')
    except (AttributeError, ValueError):
        pass
    _handler = None


def remember_positions(tree, ts):
    from .mirror import nodes_by_id
    nodes = nodes_by_id(tree)
    roots = [layer.id for layer in ts.layers if not layer.parent_id and layer.id in nodes]
    previous = list(tree.get('op_root_order', []))
    if previous and previous != roots and set(previous) == set(roots):
        # A panel reorder moves the root nodes into the corresponding visual
        # slots. A drag already has this order, so keeps its authored positions.
        heights = {lid: float(nodes[lid].location_absolute.y) for lid in roots}
        if desired_root_order_bottom_first(roots, heights) != roots:
            for lid, y in zip(roots, sorted(heights.values())):
                node = nodes[lid]
                if node.op_kind == 'FOLDER' and node.parent is not None:
                    node.parent.location.y += y - node.location_absolute.y
                else:
                    node.location_absolute.y = y
    tree['op_root_order'] = roots
    tree['op_row_y'] = {layer.id: float(nodes[layer.id].location_absolute.y)
                        for layer in ts.layers if not layer.parent_id and layer.id in nodes}


# ---- Y-order -----------------------------------------------------------------

def order_from_y(tree, ts):
    """Reorder root siblings from node Y; True when the stack changed.

    Higher nodes are higher in the stack: the root layers' bottom-first
    storage order follows their ascending view Y. Nested children keep their
    link-driven order (their ``below`` links still apply); only ``parent_id
    == ''`` layers are touched, so the two mechanisms never fight. Safe
    no-ops (all return False): not a graph tree, our own reconcile in
    flight, fewer than two rooted nodes, tree not settled (signature mismatch
    or pending link edits), tied rows, or an order that already matches. The
    write path is ``ts.layers.move`` + one ``invalidate``, preserving the
    active layer.
    """
    try:
        from .tree import is_graph
        if not is_graph(tree):
            return False
        from .mirror import is_syncing, nodes_by_id, signature
        if is_syncing():
            return False
        if len(ts.layers) < 2:
            return False
        if tree.get('op_links_dirty'):
            return False  # a panel or link edit is still waiting for its reconcile
        by_node = nodes_by_id(tree)
        y_by_id = {}
        for layer in ts.layers:
            node = by_node.get(layer.id)
            if node is None or getattr(node, 'op_kind', 'LAYER') not in ('LAYER', 'FOLDER'):
                continue
            try:
                loc = getattr(node, 'location_absolute', None) or node.location
                y_by_id[layer.id] = float(loc[1])
            except (AttributeError, TypeError, IndexError):
                continue
        roots = [layer.id for layer in ts.layers if not layer.parent_id and layer.id in y_by_id]
        previous = tree.get('op_row_y', {})
        if len(roots) < 2 or not any(lid in previous and abs(y_by_id[lid] - previous[lid]) > .01 for lid in roots):
            return False
        # An idle graph has no row drag to adopt. Only fingerprint the stack
        # after a node actually moved; mirror.sync already checks it each tick.
        if tree.get('op_signature') != signature(ts):
            return False  # a panel edit must reconcile before node order can win
        if is_ambiguous(y_by_id[lid] for lid in roots):
            return False
        desired = desired_root_order_bottom_first(roots, y_by_id)
        member_set = set(roots)
        if [layer.id for layer in ts.layers if layer.id in member_set] == desired:
            return False
        from ..core.layer_stack import index_of, invalidate
        active_id = ''
        if 0 <= ts.active_layer < len(ts.layers):
            active_id = ts.layers[ts.active_layer].id
        # Fix the subsequence position by position: later root items always sit
        # after the current one in storage order, so each move lands exactly.
        for position, lid in enumerate(desired):
            ordered = [layer.id for layer in ts.layers if layer.id in member_set]
            if ordered[position] == lid:
                continue
            ts.layers.move(index_of(ts, lid), index_of(ts, ordered[position]))
        if active_id:
            ts.active_layer = index_of(ts, active_id)
        invalidate(ts)
        remember_positions(tree, ts)
        return True
    except Exception as error:  # never break the lifecycle tick
        try:
            from ..utils.logging import logger
            logger.error('Open Graph row order: %s', error)
        except ImportError:
            pass
        return False
