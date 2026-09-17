"""Packing unchanged buffers is skipped; native writes still reach saved pixels."""
import sys
from pathlib import Path
import bpy
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.core import image_manager as images
from open_painter.evaluation import metrics

open_painter.register()
image = images.create('Revision packing', 32)
data = np.ones((32, 32, 4), np.float32)
images.write(image, data)
with metrics.Metrics() as observed:
    images.pack_image(image)
assert observed.counts['pack_calls'] == 0
assert observed.counts['pack_unchanged_skips'] == 1
# Native paint does not advance the Python writer's revision counter.
revision = image['open_painter_pixel_revision']
data[7:9, 3:10, :3] = .37
image.pixels.foreach_set(data.ravel())
image.update()
assert image['open_painter_pixel_revision'] == revision
with metrics.Metrics() as observed:
    images.pack_image(image)
assert observed.counts['pack_calls'] == 1
path = Path(__file__).resolve().parents[2] / '.blender-test/packing-revision.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(path))
bpy.ops.wm.open_mainfile(filepath=str(path))
np.testing.assert_array_equal(images.pixels(bpy.data.images['Revision packing']), data)

# An artist texture whose file vanished cannot be packed; the managed canvases
# after it in bpy.data.images must still be packed, or they reload blank (0.9.7).
from open_painter.core import layer_stack as stack, texture_set_manager as sets
scene = bpy.context.scene
bpy.ops.mesh.primitive_plane_add(size=2)
ts = sets.create(scene, 'Pack failures', 512)
sets.assign(scene, ts, bpy.context.object)
wood_path = Path(__file__).resolve().parents[2] / '.blender-test/aa wood.png'
wood_source = bpy.data.images.new('wood source', 16, 16)
wood_source.filepath_raw = str(wood_path)
wood_source.file_format = 'PNG'
wood_source.save()
bpy.data.images.remove(wood_source)
wood = bpy.data.images.load(str(wood_path))
wood.pixels[0]   # the buffer loads on first use, as it does when the layer is rendered
assert wood.has_data and wood.source == 'FILE'
fill = stack.add(ts, 'FILL', 'Wood')
stack.channel_for(fill, 'BASE_COLOR').image = wood
paint_id = stack.add(ts, 'PAINT', 'Strokes').id
from open_painter.painting.channel_paint import target as paint_target
canvas = paint_target(ts, next(layer for layer in ts.layers if layer.id == paint_id), 'BASE_COLOR')
canvas.name = 'zz strokes'   # sorts after the texture that fails to pack
painted = np.ones((512, 512, 4), np.float32) * .42
canvas.pixels.foreach_set(painted.ravel())   # a native stroke: no writer revision, is_dirty only
canvas.update()
wood_path.unlink()
from open_painter.evaluation import cache
cache.on_save(None)   # must not raise: the handler would die on the missing file
assert canvas.packed_file is not None, 'the managed canvas after the failing texture was not packed'
assert 'Could not pack' in scene.open_painter.status and wood.name in scene.open_painter.status, scene.open_painter.status
path = Path(__file__).resolve().parents[2] / '.blender-test/packing-failure.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(path))
bpy.ops.wm.open_mainfile(filepath=str(path))
reloaded = bpy.data.images['zz strokes']
assert reloaded.packed_file is not None and reloaded.has_data
assert abs(float(images.pixels(reloaded)[..., 0].mean()) - .42) < 1e-3, 'strokes were lost on reload'
open_painter.unregister()
print('SAVE_DURING_EVALUATION_PASS')
