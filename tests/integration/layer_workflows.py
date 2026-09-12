"""Selected-layer insertion, subtree duplication and independent stack view."""
import sys
from pathlib import Path
import bpy
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.core import layer_stack as stack, texture_set_manager as sets
open_painter.register()
ts = sets.create(bpy.context.scene, 'Layer workflow', 512)
for name in ['Bottom', 'Middle', 'Top']:
    stack.add(ts, 'FILL', name)
ts.active_layer = 1
inserted = stack.add(ts, 'PAINT', 'Inserted')
assert [l.name for l in ts.layers] == ['Bottom', 'Middle', 'Inserted', 'Top']
assert stack.active(ts).id == inserted.id
ts.active_layer = 1
copy = stack.duplicate(ts, 1)
assert [l.name for l in ts.layers] == ['Bottom', 'Middle', 'Middle Copy', 'Inserted', 'Top']
assert stack.active(ts).id == copy.id
folder = stack.group(ts, {ts.layers[0].id, ts.layers[1].id})
folder_id = folder.id
folder_index = ts.active_layer
original_children = {l.id for l in ts.layers if l.parent_id == folder_id}
copy = stack.duplicate(ts, folder_index)
assert copy.type == 'FOLDER' and copy.id != folder_id
children = [l for l in ts.layers if l.parent_id == copy.id]
assert len(children) == 2 and all(l.id not in original_children for l in children)
assert stack.active(ts).id == copy.id
rows = [ts.layers[i].id for i, depth in stack.visible_rows(ts) if depth == 0]
assert rows.index(copy.id) + 1 == rows.index(folder_id)
ts.active_layer = next(i for i,l in enumerate(ts.layers) if l.id in original_children)
parent_id = ts.layers[ts.active_layer].parent_id
child = stack.add(ts, 'FILL', 'Nested', parent_id=parent_id)
assert child.parent_id == folder_id
project = bpy.context.scene.open_painter
project.paint_channel = 'ROUGHNESS'
project.blend_channel = 'BASE_COLOR'
serial = ts.edit_serial
project.blend_channel = 'METALLIC'
assert project.paint_channel == 'ROUGHNESS' and ts.edit_serial == serial
stack.channel_for(child, 'METALLIC').opacity = .25
assert stack.channel_for(child, 'BASE_COLOR').opacity == 1
assert ts.edit_serial > serial
open_painter.unregister()
print('LAYER_WORKFLOWS_PASS', flush=True)
