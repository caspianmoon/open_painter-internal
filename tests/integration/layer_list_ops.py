"""The Layers panel buttons act on what the artist sees (0.9.7).

The selection follows the active index (Group and Bypass Selected read it),
Up and Down step through the visible hierarchy, and a mask flag left over
from another layer never turns a shelf click into a mask.
"""
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.core import layer_stack as stack, texture_set_manager as sets
from open_painter.ui import performance
from open_painter.ui.dock import state

open_painter.register()


def by_name(ts, name):
    return next(layer for layer in ts.layers if layer.name == name)


def visible(ts):
    return [(ts.layers[index].name, depth) for index, depth in stack.visible_rows(ts)]


def run():
    ts = sets.create(bpy.context.scene, 'Layer list ops', 512)
    ui = state.get(bpy.context.area)

    def layer(action, **kw):
        result = bpy.ops.open_painter.layer(action=action, **kw)
        assert result == {'FINISHED'}, (action, result, bpy.context.scene.open_painter.status)

    for name in ['A', 'B', 'C']:
        stack.add(ts, 'FILL', name)

    # --- Selection follows the active index -------------------------------
    # '+ Paint' selects the new layer; activating another row (the legacy list,
    # the arrow keys) moves the selection with it, so Group wraps that row.
    ts.active_layer = stack.index_of(ts, by_name(ts, 'C').id)
    layer('PAINT')
    added_id = stack.active(ts).id
    assert ui.selected_layers == {added_id}
    ts.active_layer = stack.index_of(ts, by_name(ts, 'A').id)
    assert ui.selected_layers == {by_name(ts, 'A').id}
    layer('GROUP')
    folder = stack.active(ts)
    assert folder.type == 'FOLDER' and by_name(ts, 'A').parent_id == folder.id
    assert stack.active(ts).id in ui.selected_layers and not ui.mask_selected
    assert next(item for item in ts.layers if item.id == added_id).parent_id == ''
    layer('UNGROUP')
    assert by_name(ts, 'A').parent_id == '' and not any(item.type == 'FOLDER' for item in ts.layers)
    assert stack.active(ts) is not None and ui.selected_layers == {stack.active(ts).id}

    # Deleting the selected layer selects the one that becomes active; a
    # stale id (an undone layer) never leaves Group with an empty folder.
    ts.active_layer = stack.index_of(ts, added_id)
    layer('DELETE')
    assert added_id not in ui.selected_layers and ui.selected_layers == {stack.active(ts).id}
    ts.active_layer = stack.index_of(ts, by_name(ts, 'B').id)
    ui.selected_layers = {'gone', added_id}
    layer('GROUP')
    folder = stack.active(ts)
    assert folder.type == 'FOLDER' and by_name(ts, 'B').parent_id == folder.id, visible(ts)
    layer('UNGROUP')

    # Duplicate selects the copy alone: Bypass Selected leaves the original.
    ts.active_layer = stack.index_of(ts, by_name(ts, 'C').id)
    layer('DUPLICATE')
    copy = stack.active(ts)
    assert copy.name == 'C Copy' and ui.selected_layers == {copy.id}
    assert performance._selection(bpy.context, ts) == {copy.id}
    performance.set_bypass(ts, performance._selection(bpy.context, ts), True)
    assert by_name(ts, 'C Copy').bypass and not by_name(ts, 'C').bypass
    performance.set_bypass(ts, {copy.id}, False)
    layer('DELETE')
    assert not any(item.name == 'C Copy' for item in ts.layers)

    # A multi-selection that already holds the new active layer survives.
    ui.selected_layers = {by_name(ts, 'A').id, by_name(ts, 'B').id}
    ts.active_layer = stack.index_of(ts, by_name(ts, 'A').id)
    assert ui.selected_layers == {by_name(ts, 'A').id, by_name(ts, 'B').id}
    ts.active_layer = stack.index_of(ts, by_name(ts, 'C').id)
    assert ui.selected_layers == {by_name(ts, 'C').id}
    print('LAYER_LIST_SELECTION_OK', flush=True)

    # --- Up and Down step through the visible hierarchy -------------------
    # Storage interleaves a folder's children with other layers (Parent never
    # reorders storage); the raw swap changed nothing on screen.
    ts.active_layer = stack.index_of(ts, by_name(ts, 'C').id)
    layer('FOLDER')
    folder_id = stack.active(ts).id
    for name in ('A', 'B'):
        ts.active_layer = stack.index_of(ts, by_name(ts, name).id)
        layer('PARENT', folder=folder_id)
    assert visible(ts) == [('Folder', 0), ('B', 1), ('A', 1), ('C', 0)], visible(ts)
    ts.active_layer = stack.index_of(ts, by_name(ts, 'C').id)
    layer('UP')
    assert visible(ts) == [('Folder', 0), ('B', 1), ('A', 1), ('C', 1)], visible(ts)
    assert stack.active(ts).name == 'C' and by_name(ts, 'C').parent_id == folder_id
    layer('UP')
    assert visible(ts) == [('Folder', 0), ('B', 1), ('C', 1), ('A', 1)], visible(ts)
    layer('UP')
    assert visible(ts) == [('Folder', 0), ('C', 1), ('B', 1), ('A', 1)], visible(ts)
    layer('UP')
    assert visible(ts) == [('C', 0), ('Folder', 0), ('B', 1), ('A', 1)], visible(ts)
    assert by_name(ts, 'C').parent_id == ''
    serial = ts.edit_serial
    layer('UP')   # already on top: nothing to do, nothing invalidated
    assert visible(ts) == [('C', 0), ('Folder', 0), ('B', 1), ('A', 1)] and ts.edit_serial == serial
    assert stack.active(ts).name == 'C'
    # Down retraces the same steps; the last child leaves before passing the next row.
    layer('DOWN')
    assert visible(ts) == [('Folder', 0), ('C', 1), ('B', 1), ('A', 1)], visible(ts)
    layer('DOWN')
    layer('DOWN')
    assert visible(ts) == [('Folder', 0), ('B', 1), ('A', 1), ('C', 1)], visible(ts)
    layer('DOWN')
    assert visible(ts) == [('Folder', 0), ('B', 1), ('A', 1), ('C', 0)], visible(ts)
    serial = ts.edit_serial
    layer('DOWN')
    assert visible(ts) == [('Folder', 0), ('B', 1), ('A', 1), ('C', 0)] and ts.edit_serial == serial
    # A folder moves with its subtree, and past a sibling in one press.
    ts.active_layer = stack.index_of(ts, folder_id)
    layer('DOWN')
    assert visible(ts) == [('C', 0), ('Folder', 0), ('B', 1), ('A', 1)], visible(ts)
    assert stack.active(ts).id == folder_id
    # Under a collapsed folder the stack still moves in storage.
    by_name(ts, 'Folder').expanded = False
    ts.active_layer = stack.index_of(ts, by_name(ts, 'A').id)
    order = [item.name for item in ts.layers]
    layer('DOWN' if stack.index_of(ts, by_name(ts, 'A').id) == len(ts.layers) - 1 else 'UP')
    assert [item.name for item in ts.layers] != order
    by_name(ts, 'Folder').expanded = True
    print('LAYER_LIST_STEP_OK', flush=True)

    # --- The mask flag never outlives the layer it was selected on ---------
    ts.active_layer = stack.index_of(ts, by_name(ts, 'C').id)
    assert bpy.ops.open_painter.mask(action='ADD', source='FILL') == {'FINISHED'}
    assert ui.mask_selected and by_name(ts, 'C').masks
    layer('FILL')
    assert not stack.active(ts).masks and not ui.mask_selected
    ts.active_layer = stack.index_of(ts, by_name(ts, 'C').id)
    ui.mask_selected = True
    layer('UP')   # the same layer keeps its mask selected
    assert stack.active(ts).name == 'C' and ui.mask_selected
    layer('DUPLICATE')
    assert stack.active(ts).name == 'C Copy' and stack.active(ts).masks and not ui.mask_selected
    layer('DELETE')
    ts.active_layer = stack.index_of(ts, by_name(ts, 'C').id)
    ui.mask_selected = True
    layer('DELETE')   # whichever layer becomes active, its content is shown
    assert not ui.mask_selected
    # Undo restores the stack without callbacks; the flag and selection are
    # brought back in step with it.
    ts_id = ts.id
    ts.active_layer = stack.index_of(ts, by_name(ts, 'A').id)
    bpy.ops.ed.undo_push(message='before mask')
    assert bpy.ops.open_painter.mask(action='ADD', source='FILL') == {'FINISHED'}
    assert ui.mask_selected
    bpy.ops.ed.undo_push(message='mask added')
    stack.add(ts, 'FILL', 'Undone')
    undone_id = stack.active(ts).id
    ui.selected_layers = {undone_id}
    bpy.ops.ed.undo_push(message='layer added')
    assert bpy.ops.ed.undo() == {'FINISHED'}
    ts = next(item for item in bpy.context.scene.open_painter.texture_sets if item.id == ts_id)
    assert not any(item.id == undone_id for item in ts.layers)
    assert ui.selected_layers == {stack.active(ts).id}, ui.selected_layers
    assert ui.mask_selected and stack.active(ts).masks
    assert bpy.ops.ed.undo() == {'FINISHED'}
    ts = next(item for item in bpy.context.scene.open_painter.texture_sets if item.id == ts_id)
    assert not stack.active(ts).masks and not ui.mask_selected
    print('LAYER_LIST_MASK_FLAG_OK', flush=True)
    print('LAYER_LIST_OPS_PASS', flush=True)


area = next(area for area in bpy.context.screen.areas if area.type == 'VIEW_3D')
region = next(region for region in area.regions if region.type == 'WINDOW')
with bpy.context.temp_override(area=area, region=region):
    run()
open_painter.unregister()
