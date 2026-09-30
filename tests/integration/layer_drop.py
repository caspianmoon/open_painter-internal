"""Drop targets, placement and cancellation for mouse reordering of the stack."""
import sys
from pathlib import Path
import bpy
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.core import layer_stack as stack, texture_set_manager as sets


def revisions(info):
    """Structural revisions of the session: a rebuild, or a patch of its own graph (EngineGraph.patch)."""
    return info['graph_builds'] + info['graph_patches']


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

# A drag places the layer at every row it crosses while the timer keeps
# ticking: the shader session held by the modal rebuilds once, on the drop,
# and once for a cancelled drag put back in place.
from time import monotonic
from open_painter.core import material_manager
from open_painter.evaluation import cache, shader_preview
scene = bpy.context.scene
if bpy.app.timers.is_registered(cache.tick):
    bpy.app.timers.unregister(cache.tick)
bpy.ops.mesh.primitive_plane_add(size=2)
sets.assign(scene, ts, bpy.context.object)


def ready():
    deadline = monotonic() + 60
    while monotonic() < deadline:
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert not info['error'], info
        if shader_preview.current(scene, ts):
            return info
    raise AssertionError(shader_preview.diagnostics(scene, ts))


material_manager.repair(ts)
builds = revisions(ready())
saved = stack.arrangement(ts)
dragged = by_name('Middle').id
key = shader_preview.hold(scene, ts)
try:
    crossed = 0
    for gap in range(len(stack.drop_targets(ts, dragged))):
        for target in stack.drop_targets(ts, dragged)[gap]:
            stack.place(ts, dragged, target)
            crossed += 1
            for _ in range(3):
                shader_preview.tick(scene, ts)
            info = shader_preview.diagnostics(scene, ts)
            assert info['held'] and revisions(info) == builds, (crossed, info)
    stack.place(ts, dragged, stack.drop_targets(ts, dragged)[0][0])
    assert crossed > 4 and stack.arrangement(ts) != saved
finally:
    shader_preview.release_hold(key)
assert not shader_preview.diagnostics(scene, ts)['held']
info = ready()
assert revisions(info) == builds + 1, (builds, info)
builds = revisions(info)
key = shader_preview.hold(scene, ts)
try:
    stack.place(ts, dragged, stack.drop_targets(ts, dragged)[1][0])
    for _ in range(3):
        shader_preview.tick(scene, ts)
    stack.restore_arrangement(ts, saved)
    assert stack.arrangement(ts) == saved
finally:
    shader_preview.release_hold(key)
info = ready()
assert revisions(info) == builds + 1, (builds, info)
shader_preview.release_hold(key)   # releasing twice, or an ended hold, is harmless

# A modal the window manager cancels (its window closed, a file loaded) never
# reaches ``finish``: the operator's ``cancel`` lets go of the hold and the drag
# flag exactly as ``finish`` does, and puts the undropped layer back.
from types import SimpleNamespace
from open_painter.ui import native
builds = revisions(info)
lost = SimpleNamespace(hold=shader_preview.hold(scene, ts), dragging=True, saved=stack.arrangement(ts),
                       layer_id=dragged)   # a modal's state in the background, where no modal can run
lost.release = lambda context: native.OP_OT_native_layer_drag.release(lost, context)
native._drag['active'] = True
stack.place(ts, dragged, stack.drop_targets(ts, dragged)[1][0])
assert stack.arrangement(ts) != lost.saved
for _ in range(3):
    shader_preview.tick(scene, ts)
assert shader_preview.diagnostics(scene, ts)['held']
native.OP_OT_native_layer_drag.cancel(lost, bpy.context)   # no window, no area: nothing to restore on screen
assert not native._drag['active']
assert not shader_preview.diagnostics(scene, ts)['held']
assert stack.arrangement(ts) == lost.saved and stack.active(ts).id == dragged
info = ready()   # current again: the stack ended as the shader was built, so no rebuild
assert revisions(info) == builds and info['serial'] == info['bound_serial'], (builds, info)

# And a hold nobody renews for HOLD_SECONDS (a modal lost without even a
# cancel) is released by the tick itself, with a warning, so the session is
# current again instead of sitting on its last shader forever.
import logging

builds = revisions(info)
key = shader_preview.hold(scene, ts)
stack.place(ts, dragged, stack.drop_targets(ts, dragged)[1][0])
for _ in range(3):
    shader_preview.tick(scene, ts)
info = shader_preview.diagnostics(scene, ts)
assert info['held'] and revisions(info) == builds and shader_preview.held(scene, ts), info
shader_preview.renew_hold(key)
bound = shader_preview.HOLD_SECONDS
shader_preview.HOLD_SECONDS = .2
try:
    from time import sleep
    sleep(.3)
    warnings = []
    class Catch(logging.Handler):
        def emit(self, record):
            warnings.append(record.getMessage())
    catcher = Catch(level=logging.WARNING)
    logging.getLogger('open_painter').addHandler(catcher)
    try:
        info = ready()
    finally:
        logging.getLogger('open_painter').removeHandler(catcher)
    assert not info['held'] and not shader_preview.held(scene, ts), info
    assert revisions(info) == builds + 1, (builds, info)
    assert any('hold' in message and 'releasing' in message for message in warnings), warnings
    assert len(warnings) == 1, warnings   # released once; the next tick finds no hold to warn about
finally:
    shader_preview.HOLD_SECONDS = bound
stack.restore_arrangement(ts, lost.saved)
open_painter.unregister()
print('LAYER_DROP_PASS', flush=True)
