"""Drop targets, placement and cancellation for mouse reordering of the stack."""
import sys
from pathlib import Path
import bpy
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.core import layer_stack as stack, texture_set_manager as sets
open_painter.register()
ts = sets.create(bpy.context.scene, 'Layer drop', 512)


def by_name(name):
    return next(layer for layer in ts.layers if layer.name == name)


def visible():
    return [(ts.layers[index].name, depth) for index, depth in stack.visible_rows(ts)]


for name in ['Bottom', 'Middle', 'Top']:
    stack.add(ts, 'FILL', name)
assert visible() == [('Top', 0), ('Middle', 0), ('Bottom', 0)]

# A flat stack: one candidate per gap, above the following row or at the end.
targets = stack.drop_targets(ts, by_name('Top').id)
assert [[(depth, kind) for depth, _, kind, _ in gap] for gap in targets] == [
    [(0, 'ABOVE')], [(0, 'BELOW')], [(0, 'BELOW')]]
assert stack.slot(ts, by_name('Top').id) == (0, 0)
stack.place(ts, by_name('Top').id, targets[2][0])
assert visible() == [('Middle', 0), ('Bottom', 0), ('Top', 0)]
assert stack.active(ts).name == 'Top'
assert stack.slot(ts, by_name('Top').id) == (2, 0)
stack.place(ts, by_name('Top').id, stack.drop_targets(ts, by_name('Top').id)[0][0])
assert visible() == [('Top', 0), ('Middle', 0), ('Bottom', 0)]

# A folder above Top: the gap below it offers its first-child slot and root.
ts.active_layer = stack.index_of(ts, by_name('Top').id)
folder = stack.add(ts, 'FOLDER', 'Folder')
folder_id = folder.id
assert visible() == [('Folder', 0), ('Top', 0), ('Middle', 0), ('Bottom', 0)]
targets = stack.drop_targets(ts, by_name('Top').id)
assert [[(depth, kind) for depth, _, kind, _ in gap] for gap in targets] == [
    [(0, 'ABOVE')], [(1, 'FIRST_CHILD'), (0, 'BELOW')], [(0, 'BELOW')], [(0, 'BELOW')]]
assert stack.slot(ts, by_name('Top').id) == (1, 0)
stack.place(ts, by_name('Top').id, targets[1][0])
assert by_name('Top').parent_id == folder_id
assert visible() == [('Folder', 0), ('Top', 1), ('Middle', 0), ('Bottom', 0)]

# Middle joins the folder below Top, then the closing edge of the folder
# offers both depths and Top leaves through it.
targets = stack.drop_targets(ts, by_name('Middle').id)
assert [[(depth, kind) for depth, _, kind, _ in gap] for gap in targets] == [
    [(0, 'ABOVE')], [(1, 'FIRST_CHILD')], [(1, 'BELOW'), (0, 'BELOW')], [(0, 'BELOW')]]
stack.place(ts, by_name('Middle').id, targets[2][0])
assert visible() == [('Folder', 0), ('Top', 1), ('Middle', 1), ('Bottom', 0)]
targets = stack.drop_targets(ts, by_name('Top').id)
assert [[(depth, kind) for depth, _, kind, _ in gap] for gap in targets] == [
    [(0, 'ABOVE')], [(1, 'FIRST_CHILD')], [(1, 'BELOW'), (0, 'BELOW')], [(0, 'BELOW')]]
stack.place(ts, by_name('Top').id, targets[2][1])
assert by_name('Top').parent_id == ''
assert visible() == [('Folder', 0), ('Middle', 1), ('Top', 0), ('Bottom', 0)]

# A folder drags with its subtree, never into itself, and a collapsed folder
# is passed as one row.
targets = stack.drop_targets(ts, folder_id)
assert [[(depth, kind) for depth, _, kind, _ in gap] for gap in targets] == [
    [(0, 'ABOVE')], [(0, 'BELOW')], [(0, 'BELOW')]]
stack.place(ts, folder_id, targets[2][0])
assert visible() == [('Top', 0), ('Bottom', 0), ('Folder', 0), ('Middle', 1)]
by_name('Folder').expanded = False
assert visible() == [('Top', 0), ('Bottom', 0), ('Folder', 0)]
targets = stack.drop_targets(ts, by_name('Bottom').id)
assert [[(depth, kind) for depth, _, kind, _ in gap] for gap in targets] == [
    [(0, 'ABOVE')], [(0, 'BELOW')], [(0, 'BELOW')]]
stack.place(ts, by_name('Bottom').id, targets[2][0])
assert visible() == [('Top', 0), ('Folder', 0), ('Bottom', 0)]
by_name('Folder').expanded = True
assert visible() == [('Top', 0), ('Folder', 0), ('Middle', 1), ('Bottom', 0)]

# An empty expanded folder still accepts a first child.
stack.place(ts, by_name('Middle').id, stack.drop_targets(ts, by_name('Middle').id)[3][0])
assert visible() == [('Top', 0), ('Folder', 0), ('Bottom', 0), ('Middle', 0)]
targets = stack.drop_targets(ts, by_name('Top').id)
assert [[(depth, kind) for depth, _, kind, _ in gap] for gap in targets] == [
    [(0, 'ABOVE')], [(1, 'FIRST_CHILD'), (0, 'BELOW')], [(0, 'BELOW')], [(0, 'BELOW')]]
stack.place(ts, by_name('Top').id, targets[1][0])
assert visible() == [('Folder', 0), ('Top', 1), ('Bottom', 0), ('Middle', 0)]

# Cancelling restores order and parents exactly, and the edit serial moved.
saved = stack.arrangement(ts)
serial = ts.edit_serial
stack.place(ts, by_name('Middle').id, stack.drop_targets(ts, by_name('Middle').id)[1][0])
assert visible() == [('Folder', 0), ('Middle', 1), ('Top', 1), ('Bottom', 0)]
stack.restore_arrangement(ts, saved)
assert stack.arrangement(ts) == saved
assert visible() == [('Folder', 0), ('Top', 1), ('Bottom', 0), ('Middle', 0)]
assert ts.edit_serial > serial

# The opacity control is a whole percent backed by the float channel opacity.
channel = stack.channel_for(by_name('Top'), 'BASE_COLOR')
channel.opacity_percent = 35
assert abs(channel.opacity - .35) < 1e-6 and channel.opacity_percent == 35
channel.opacity = .333
assert channel.opacity_percent == 33
open_painter.unregister()
print('LAYER_DROP_PASS', flush=True)
