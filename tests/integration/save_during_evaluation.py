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
open_painter.unregister()
print('SAVE_DURING_EVALUATION_PASS')
