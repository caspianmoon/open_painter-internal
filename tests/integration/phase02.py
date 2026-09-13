import sys
from pathlib import Path
import bpy

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "src"))
import open_painter
from open_painter.core import texture_set_manager as sets, layer_stack as stack

open_painter.register()
ts = sets.create(bpy.context.scene)
folder_id = stack.add(ts, "FOLDER").id
for i in range(9):
    layer = stack.add(ts, "FILL", f"Layer {i}")
    stack.parent(ts, layer, folder_id)
assert len(ts.layers) == 10
layer_id = layer.id
stack.move(ts, 9, -1)
assert ts.layers[8].id == layer_id
try:
    stack.parent(ts, ts.layers[0], folder_id)
    raise AssertionError("Cycle accepted")
except ValueError:
    pass
copy = stack.duplicate(ts, 0)
assert len(ts.layers) == 20
assert sum(l.parent_id == copy.id for l in ts.layers) == 9
saved_order = [item.id for item in ts.layers]
path = root / ".blender-test" / "layers.blend"
path.parent.mkdir(exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=str(path))
bpy.ops.wm.open_mainfile(filepath=str(path))
ts = bpy.context.scene.open_painter.texture_sets[0]
assert [item.id for item in ts.layers] == saved_order
assert layer_id in saved_order
stack.remove(ts, 0)
assert not ts.layers[0].parent_id
open_painter.unregister()
print("PHASE 2 PASS: ten layers, ordering, folder copy, cycle rejection, persistence")
