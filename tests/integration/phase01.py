"""Run using Blender --background --factory-startup --python this_file."""

import sys
from pathlib import Path
import bpy

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "src"))
import open_painter
from open_painter.core import texture_set_manager as sets
from open_painter.core.validation import validate

for _ in range(3):
    open_painter.register()
    assert hasattr(bpy.types.Scene, "open_painter")
    open_painter.unregister()
    assert not hasattr(bpy.types.Scene, "open_painter")
open_painter.register()
cube = bpy.context.object
original_name = cube.active_material.name
ts = sets.create(bpy.context.scene, "Cube")
sets.assign(bpy.context.scene, ts, cube)
assert not validate(ts), validate(ts)
ts_id = ts.id
path = root / "examples" / "phase01.blend"
bpy.ops.wm.save_as_mainfile(filepath=str(path))
bpy.ops.wm.open_mainfile(filepath=str(path))
ts = bpy.context.scene.open_painter.texture_sets[0]
assert ts.id == ts_id
assert ts.assignments[0].object.name == "Cube"
assert not validate(ts)
sets.unassign(ts, 0)
assert bpy.data.objects["Cube"].active_material.name == original_name
open_painter.unregister()
print("PHASE 0 + 1 PASS: registration, assignment, persistence, safe removal")
