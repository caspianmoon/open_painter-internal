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

# A copy carries exact channel opacities: the derived percent control is not replayed over them.
stack.channel_for(child, 'METALLIC').opacity = .375
child_copy = stack.duplicate(ts, stack.index_of(ts, child.id))
assert abs(stack.channel_for(child_copy, 'METALLIC').opacity - .375) < 1e-6, stack.channel_for(child_copy, 'METALLIC').opacity

# A copy carries the strokes painted since the last save: paint canvases are packed by the
# save handler only, and Image.copy() reads the packed bytes, not the live pixels.
import numpy as np
from open_painter.core import image_manager as images
from open_painter.painting import channel_paint
painted = stack.add(ts, 'PAINT', 'Painted')
painted_id = painted.id
canvas = channel_paint.target(ts, painted, 'BASE_COLOR')
images.write(canvas, np.full((512, 512, 4), (.7, .7, .7, 1), np.float32), pack=False)
assert canvas.is_dirty and canvas.packed_file is None
painted_mask = painted.masks.add()
painted_mask.id, painted_mask.source = 'painted-mask', 'PAINT'
painted_mask.image = images.create('Painted mask', 512, (0, 0, 0, 1))
painted_mask.image.pack()
images.write(painted_mask.image, np.full((512, 512, 4), (.3, .3, .3, 1), np.float32), pack=False)
painted_copy = stack.duplicate(ts, stack.index_of(ts, painted_id))
copy_canvas = stack.channel_for(painted_copy, 'BASE_COLOR').image
assert copy_canvas != canvas and abs(images.pixels(copy_canvas)[0, 0, 0] - .7) < 1e-3, images.pixels(copy_canvas)[0, 0]
assert abs(images.pixels(painted_copy.masks[0].image)[0, 0, 0] - .3) < 1e-3, 'strokes after the last pack are on the mask copy'
set_copy = sets.duplicate(bpy.context.scene, 0)
copied_painted = next(l for l in set_copy.layers if l.name == 'Painted')
assert abs(images.pixels(stack.channel_for(copied_painted, 'BASE_COLOR').image)[0, 0, 0] - .7) < 1e-3
assert abs(images.pixels(copied_painted.masks[0].image)[0, 0, 0] - .3) < 1e-3
bpy.context.scene.open_painter.texture_sets.remove(1)
ts = bpy.context.scene.open_painter.texture_sets[0]

# Replaying a snapshot must not walk the set once per property: a copy of N layers into a
# stack of L layers would cost N x hundreds x L reads (seconds for a folder, minutes for a set).
from open_painter.evaluation import references, shader_preview
walks = {'dependents': 0, 'owners': 0}
original_dependents, original_owners = references.dependent_channels, shader_preview._owners
def counted_dependents(*args, **kwargs):
    walks['dependents'] += 1
    return original_dependents(*args, **kwargs)
def counted_owners(*args, **kwargs):
    walks['owners'] += 1
    return original_owners(*args, **kwargs)
references.dependent_channels, shader_preview._owners = counted_dependents, counted_owners
try:
    folder_copy = stack.duplicate(ts, stack.index_of(ts, folder_id))
    assert folder_copy.type == 'FOLDER'
    set_copy = sets.duplicate(bpy.context.scene, 0)
finally:
    references.dependent_channels, shader_preview._owners = original_dependents, original_owners
assert walks['dependents'] == 0, walks
assert walks['owners'] <= 8, walks   # one notify per copy (plus set creation), not one per restored property
assert len(set_copy.layers) == len(bpy.context.scene.open_painter.texture_sets[0].layers)
bpy.context.scene.open_painter.texture_sets.remove(1)
open_painter.unregister()
print('LAYER_WORKFLOWS_PASS', flush=True)
