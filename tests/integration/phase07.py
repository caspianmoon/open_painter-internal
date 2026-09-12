import sys
from pathlib import Path
import bpy
import numpy as np

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "src"))
import open_painter
from open_painter.core import (
    texture_set_manager as sets,
    layer_stack as stack,
    image_manager as images,
)
from open_painter.smart_materials import serializer, loader

open_painter.register()
ts = sets.create(bpy.context.scene)
ts.resolution = "512"
folder_id = stack.add(ts, "FOLDER").id
stack.add(ts, "PAINT")
stack.parent(ts, ts.layers[1], folder_id)
p = stack.channel_for(ts.layers[1], "ROUGHNESS")
p.enabled = True
p.image = images.create("Test Painted", 512)
values = np.ones((512, 512, 4), dtype=np.float32)
values[..., :3] = 0.2345
images.write(p.image, values)
bpy.ops.open_painter.mask(action="ADD", source="PAINT")
path = root / ".blender-test" / "roundtrip.openpaintmaterial"
path.unlink(missing_ok=True)
serializer.save(ts, path)
original_id = ts.layers[0].id
other = sets.create(bpy.context.scene, "Other")
other.resolution = "512"
loader.apply(other, path)
assert other.layers[0].id != original_id
assert other.layers[1].parent_id == other.layers[0].id
p = stack.channel_for(other.layers[1], "ROUGHNESS")
assert np.allclose(images.pixels(p.image), values, atol=1e-5)
assert other.layers[1].masks[0].image.packed_file
for path in (root / "src/open_painter/assets/smart_materials").glob("*.openpaintmaterial"):
    loader.apply(other, path)
open_painter.unregister()
print("PHASE 7 PASS: folder/parameter round trip, independent EXR assets, three demo materials")
