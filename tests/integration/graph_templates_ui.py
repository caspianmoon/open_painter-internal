"""Real-window graph template menus, node controls, search, and shader-group editing."""
import json
import os
import sys
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import layer_stack as stack, texture_set_manager as sets
from open_painter.graph import mirror
from open_painter.graph.tree import TREE_ID

REPORT = Path(os.environ.get('OP_GRAPH_TEMPLATES_UI_REPORT', str(ROOT / '.blender-test/graph-templates-ui.json')))
report = {'steps': []}


def run():
    assert not bpy.app.background
    open_painter.register()
    bpy.context.preferences.view.show_splash = False
    window = bpy.context.window
    window.event_simulate(type='ESC', value='PRESS')
    yield .5
    ts = sets.create(bpy.context.scene, 'Graph Templates', 512)
    ts.preview_backend = 'CPU'
    sets.assign(bpy.context.scene, ts, bpy.context.object)
    area = max((a for a in window.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    area.ui_type = TREE_ID
    tree = mirror.ensure_tree(ts)
    area.spaces.active.node_tree = tree
    area.spaces.active.pin = True
    area.spaces.active.show_region_ui = False
    region = next(r for r in area.regions if r.type == 'WINDOW')
    yield .6
    with bpy.context.temp_override(area=area, region=region):
        bpy.ops.wm.call_menu(name='OP_MT_graph_templates')
    yield .6
    window.event_simulate(type='ESC', value='PRESS')
    yield .4
    with bpy.context.temp_override(area=area, region=region):
        bpy.ops.open_painter.graph_template(template='LAYERED_GRUNGE')
    yield .6
    assert any(node.bl_idname == 'OPMaskGroupNode' for node in tree.nodes)
    assert all(node.dimensions.x > 0 for node in tree.nodes if hasattr(node, 'op_id'))
    report['steps'].append('templates menu and nested group controls draw')
    with bpy.context.temp_override(area=area, region=region):
        report['view_all'] = list(bpy.ops.node.view_all())
    yield 1.2
    report['nodes'] = {node.name: {'location': list(node.location_absolute), 'size': list(node.dimensions)}
                       for node in tree.nodes}
    report['view'] = [region.view2d.region_to_view(0, 0), region.view2d.region_to_view(region.width, region.height)]
    with bpy.context.temp_override(area=area, region=region):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
    yield .3
    shot = REPORT.with_suffix('.png')
    bpy.ops.screen.screenshot(filepath=str(shot))
    report['screenshot'] = str(shot)

    with bpy.context.temp_override(area=area, region=region):
        bpy.ops.open_painter.graph_search('INVOKE_DEFAULT')
    yield .6
    window.event_simulate(type='ESC', value='PRESS')
    yield .4
    with bpy.context.temp_override(area=area, region=region):
        assert bpy.ops.open_painter.graph_search(node='PROCEDURAL_FILL:BRUSHED_METAL_LINES') == {'FINISHED'}
    yield .6
    fill_id = stack.active(ts).id
    assert stack.channel_for(stack.active(ts), 'ROUGHNESS').procedural == 'BRUSHED_METAL_LINES'
    with bpy.context.temp_override(area=area, region=region):
        bpy.ops.open_painter.graph_add(kind='MESH', choice='NORMAL_DIRECTION')
        bpy.ops.open_painter.graph_add(kind='MASK', choice='STROKES')
    yield .6
    assert {'OPMeshMaskNode', 'OPStrokeMaskNode'} <= {node.bl_idname for node in tree.nodes}
    report['steps'].append('search creates procedural fill; new mesh and stroke controls draw')
    with bpy.context.temp_override(area=area, region=region):
        assert bpy.ops.open_painter.graph_add_image() == {'FINISHED'}
    yield .5
    image_node = next(n for n in tree.nodes if n.bl_idname == 'OPImageNode')
    assert image_node.outputs and image_node.outputs[0].identifier == 'out'
    assert image_node.dimensions.x > 0 and image_node.dimensions.y > 0
    report['steps'].append('image node picker, mapping controls and output draw')

    # Adjacent selected layer nodes become one folder, with their identities intact.
    ids = {layer.id for layer in ts.layers if layer.type == 'FILL'}
    with mirror.syncing():
        for node in tree.nodes:
            node.select = getattr(node, 'op_id', '') in ids
        tree.nodes.active = mirror.nodes_by_id(tree)[fill_id]
    with bpy.context.temp_override(area=area, region=region):
        assert bpy.ops.open_painter.graph_group() == {'FINISHED'}
    yield .5
    folder = stack.active(ts)
    assert folder.type == 'FOLDER' and folder.group_mode == 'PASSTHROUGH'
    assert {layer.id for layer in ts.layers if layer.parent_id == folder.id} == ids
    report['steps'].append('selected layers group without losing their identity')

    with bpy.context.temp_override(area=area, region=region):
        bpy.ops.open_painter.graph_template(template='NOISE_RAMP')
    yield .6
    mask = next(mask for layer in ts.layers for mask in layer.masks if mask.source == 'GRAPH')
    group = mask.node_group
    from open_painter.procedural_graph import is_native
    assert is_native(group) and group.bl_idname == TREE_ID, 'a template creates a Mask Graph in the Open Painter Graph'
    count = len(window.screen.areas)
    space = area.spaces.active
    with bpy.context.temp_override(area=area, region=region):
        assert bpy.ops.open_painter.mask_graph_edit(tree=group.name) == {'FINISHED'}
    yield .8
    # One Open Painter editor: the Mask Graph is entered inside the texture set's graph.
    assert len(window.screen.areas) == count, 'Edit Mask Graph opened another editor'
    assert space.edit_tree == group and space.node_tree == tree and len(space.path) == 2, list(space.path)
    # Framed once the editor drew the graph: every node of the template is in view.
    view = [region.view2d.region_to_view(0, 0), region.view2d.region_to_view(region.width, region.height)]
    for node in group.nodes:
        x, y = node.location
        assert view[0][0] <= x <= view[1][0] and view[0][1] <= y <= view[1][1], (node.name, (x, y), view)
    shot = ROOT / '.blender-test' / 'graph-templates-mask-graph.png'
    with bpy.context.temp_override(area=area, region=region):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
    bpy.ops.screen.screenshot(filepath=str(shot))
    report['mask_graph_screenshot'] = str(shot)
    with bpy.context.temp_override(area=area, region=region):
        # The older Edit Shader Group entry point finds the graph already open.
        assert bpy.ops.open_painter.graph_edit_shader(mask_id=mask.id) == {'FINISHED'}
    yield .4
    assert len(window.screen.areas) == count and space.edit_tree == group
    with bpy.context.temp_override(area=area, region=region):
        assert bpy.ops.node.tree_path_parent() == {'FINISHED'}   # Tab / the header's Back
    yield .4
    assert space.edit_tree == tree and len(space.path) == 1
    report['steps'].append('Edit Mask Graph enters the mask\'s graph in the same Open Painter editor; Back returns')

steps = run()


def tick():
    try:
        return next(steps)
    except StopIteration:
        report['status'] = 'PASS'
    except Exception:
        report['status'], report['error'] = 'FAIL', traceback.format_exc()
        traceback.print_exc()
    report['blender'] = bpy.app.version_string
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2) + '\n')
    print('GRAPH_TEMPLATES_UI_' + report['status'], flush=True)
    bpy.ops.wm.quit_blender()


bpy.app.timers.register(tick, first_interval=1)
