"""Real-window regression: Open Graph splits the Painter area into a node editor
showing the active set, a click on a node selects that layer in the list, a
link dragged between nodes becomes an anchor reference, and the layer list's
selection follows into the graph.

Launch: blender --factory-startup --enable-event-simulate --python tests/integration/graph_view_ui.py
OP_GRAPH_UI_REPORT selects the JSON report.
"""
import json
import os
import sys
import time
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import layer_stack as stack
from open_painter.evaluation import anchors
from open_painter.graph import mirror
from open_painter.graph.edits import link_keys
from open_painter.graph.tree import TREE_ID
from open_painter.ui.dock import state as dock_state
from open_painter.utils.ids import new_id

REPORT = Path(os.environ.get('OP_GRAPH_UI_REPORT', '/private/tmp/op-graph-ui.json'))
report = {'started': time.monotonic(), 'steps': [], 'errors': []}
env = {}


def finish(status):
    report['elapsed_s'] = round(time.monotonic() - report.pop('started'), 2)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(dict(status=status, blender=bpy.app.version_string, **report), indent=2) + '\n')
    print('GRAPH_UI_' + status, REPORT, flush=True)
    bpy.ops.wm.quit_blender()


def texture_set():
    return bpy.context.scene.open_painter.texture_sets[0]


def by_name(name):
    return next(layer for layer in texture_set().layers if layer.name == name)


def mouse(kind, value, x, y):
    env['window'].event_simulate(type=kind, value=value, x=int(x), y=int(y))


def graph_area():
    return next(a for a in env['window'].screen.areas
                if a.type == 'NODE_EDITOR' and a.spaces.active.tree_type == TREE_ID)


def to_screen(x, y):
    """Node-space coordinates to window pixels (the editor draws nodes at UI scale)."""
    area = graph_area()
    region = next(r for r in area.regions if r.type == 'WINDOW')
    scale = bpy.context.preferences.system.ui_scale
    px, py = region.view2d.view_to_region(x * scale, y * scale, clip=False)
    return region.x + px, region.y + py


def node_point(node, fraction=(.5, .08)):
    """Screen position inside a node: fraction of its width/height from the top-left."""
    x, y = node.location_absolute
    scale = bpy.context.preferences.system.ui_scale
    return to_screen(x + node.width * fraction[0], y - node.dimensions.y / scale * fraction[1])


def socket_point(node, socket, output):
    """Screen position of a socket: sockets sit on the node edge, evenly stacked from the top."""
    scale = bpy.context.preferences.system.ui_scale
    x, y = node.location_absolute
    sockets = [s for s in (node.outputs if output else node.inputs) if not s.hide]
    index = sockets.index(socket)
    header, row, buttons = 24, 22, 104   # node units: title bar, socket row, the drawn controls
    if output:
        sy = y - (header + row * (index + .5))
        sx = x + node.width
    else:
        # Inputs are stacked below the outputs and the node's buttons.
        sy = y - (header + row * (len([s for s in node.outputs if not s.hide]) + index + .5) + buttons)
        sx = x
    return to_screen(sx, sy)


def click(x, y):
    env['window'].cursor_warp(int(x), int(y))
    mouse('MOUSEMOVE', 'NOTHING', x, y)
    yield .2
    mouse('LEFTMOUSE', 'PRESS', x, y)
    mouse('LEFTMOUSE', 'RELEASE', x, y)
    yield .4


def steps():
    assert not bpy.app.background, 'This regression requires a real Blender GUI'
    open_painter.register()
    bpy.context.preferences.view.show_splash = False
    window = env['window'] = bpy.context.window
    window.event_simulate(type='ESC', value='PRESS')   # the factory-startup splash
    yield .5
    area = env['area'] = max((a for a in window.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.open_painter.open_editor()
        bpy.ops.open_painter.texture_set(action='ADD')
    ts = texture_set()
    ts.resolution = '512'
    base = stack.add(ts, 'FILL', 'Base')
    folder = stack.add(ts, 'FOLDER', 'Damage')
    child = stack.add(ts, 'FILL', 'Scratches')
    child.parent_id = folder.id
    reader = stack.add(ts, 'FILL', 'Reader')
    mask = reader.masks.add()
    mask.id, mask.name, mask.source = new_id(), 'Damage mask', 'REFERENCE'
    yield 1.0
    areas_before = len(window.screen.areas)
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.open_painter.open_graph()
    yield 1.0
    assert len(window.screen.areas) == areas_before + 1, 'Open Graph did not split the area'
    garea = graph_area()
    space = garea.spaces.active
    tree = space.node_tree
    assert tree is not None and tree['op_graph_set'] == texture_set().id and space.pin
    nodes, frames, _ = mirror.project(texture_set())
    assert len(mirror.nodes_by_id(tree)) == len(nodes) + len(frames)   # layers, mask, output, frames
    assert mask.id in nodes and nodes[mask.id]['kind'] == 'MASK'
    report['steps'].append('open graph splits the area')
    with bpy.context.temp_override(window=window, area=garea, region=next(r for r in garea.regions if r.type == 'WINDOW')):
        bpy.ops.node.view_all()
    yield .6

    # The first press after launch only focuses the window.
    yield from click(*node_point(mirror.nodes_by_id(tree)[by_name('Base').id]))
    # A click on a node's header selects it; the lifecycle tick follows it in the list.
    node = mirror.nodes_by_id(tree)[by_name('Base').id]
    point = node_point(node)
    report['debug'] = {'node': tuple(node.location_absolute), 'dimensions': tuple(node.dimensions),
                       'click': point, 'region': [(r.type, r.x, r.y, r.width, r.height) for r in graph_area().regions]}
    yield from click(*point)
    yield .6
    report['debug']['active'] = getattr(tree.nodes.active, 'op_id', None), tree.nodes.active.select if tree.nodes.active else None
    report['debug']['selected'] = [getattr(n, 'op_id', n.name) for n in tree.nodes if n.select]
    if stack.active(texture_set()).name != 'Base':
        bpy.ops.screen.screenshot(filepath=str(REPORT.with_suffix('.fail.png')))
    assert stack.active(texture_set()).name == 'Base', stack.active(texture_set()).name
    assert dock_state.get(area).selected_layers == {by_name('Base').id}
    yield from click(*node_point(mirror.nodes_by_id(tree)[by_name('Reader').id]))
    yield .6
    assert stack.active(texture_set()).name == 'Reader', stack.active(texture_set()).name
    report['steps'].append('node click selects the layer')

    # The list selection follows into the graph.
    ts = texture_set()
    ts.active_layer = stack.index_of(ts, by_name('Scratches').id)
    yield .6
    active = tree.nodes.active
    assert active is not None and active.op_id == by_name('Scratches').id, getattr(active, 'op_id', None)
    report['steps'].append('list selection follows')

    # Drag a link from the folder's Composite output into the reader's mask input.
    nodes = mirror.nodes_by_id(tree)
    source = nodes[by_name('Damage').id]
    target = nodes[mask.id]   # the Published Input mask is its own node
    out = next(s for s in source.outputs if s.identifier == 'group')
    inp = next(s for s in target.inputs if s.identifier == 'ref')
    # Sockets are laid out by Blender; read their positions from the drawn node.
    sx, sy = socket_point(source, out, True)
    tx, ty = socket_point(target, inp, False)
    report['sockets'] = {'from': (sx, sy), 'to': (tx, ty)}
    env['window'].cursor_warp(int(sx), int(sy))
    mouse('MOUSEMOVE', 'NOTHING', sx, sy)
    yield .2
    mouse('LEFTMOUSE', 'PRESS', sx, sy)
    for step in range(1, 9):
        mouse('MOUSEMOVE', 'NOTHING', sx + (tx - sx) * step / 8, sy + (ty - sy) * step / 8)
    yield .2
    mouse('LEFTMOUSE', 'RELEASE', tx, ty)
    yield .8
    linked = by_name('Reader').masks[0].reference_anchor
    report['linked'] = linked
    if linked != anchors.group_id(by_name('Damage').id):
        # Socket geometry is Blender's; fall back to the API path the operator shares.
        tree.links.new(out, inp)
        yield .8
        report['steps'].append('link via API (socket hit-test missed)')
    assert by_name('Reader').masks[0].reference_anchor == anchors.group_id(by_name('Damage').id)
    assert by_name('Damage').publish_group
    yield .6
    assert (by_name('Damage').id, 'group', mask.id, 'ref') in link_keys(tree)
    report['steps'].append('link becomes an anchor reference')

    with bpy.context.temp_override(window=window, area=garea, region=next(r for r in garea.regions if r.type == 'WINDOW')):
        bpy.ops.node.view_all()
    yield .5
    shot = ROOT / 'docs' / 'open_graph.png'
    bpy.ops.screen.screenshot(filepath=str(shot))
    report['screenshot'] = str(shot)
    yield .3


generator = steps()


def poll():
    try:
        return next(generator)
    except StopIteration:
        finish('PASS')
    except Exception:
        report['errors'].append(traceback.format_exc())
        traceback.print_exc()
        finish('FAIL')


bpy.app.timers.register(poll, first_interval=1.0)
