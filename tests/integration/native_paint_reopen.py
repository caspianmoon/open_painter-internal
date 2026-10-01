"""Reopen authored stroke data in a fresh process, then evaluate with C scheduling."""
import json
import os
from pathlib import Path
import sys

import bpy
import gpu
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.evaluation import shader_preview
from open_painter.painting import stroke_raster, strokes

report_file = Path(os.environ.get('OP_VECTOR_BRUSH_UI_REPORT', str(ROOT / '.blender-test/native/metal-integration/native-brush-ui.json')))
report = json.loads(report_file.read_text())
assert report['status'] == 'PASS'
open_painter.register()
gpu.init()
bpy.ops.wm.open_mainfile(filepath=report['project'])
scene = bpy.context.scene
ts = scene.open_painter.texture_sets[0]
layer = ts.layers[0]
records = strokes.strokes_for(scene.open_painter, layer.stroke_key)
assert len(records) == 1
assert len(stroke_raster.unpack_samples(records[0].samples)) == report['samples']
for _ in range(300):
    shader_preview.tick(scene, ts)
    info = shader_preview.diagnostics(scene, ts)
    assert not info['error'], info
    if shader_preview.current(scene, ts):
        break
assert shader_preview.current(scene, ts), info
assert info['engine']['execution_backend'] == 'c17-host-' + gpu.platform.backend_type_get().lower(), info
image = next(o.image for o in ts.outputs if o.channel == 'BASE_COLOR')
values = np.array(gpu.texture.from_image(image).read(), np.float32).reshape(-1, 4)
assert np.ptp(values[:, 0]) > .1
print('NATIVE_PAINT_REOPEN_PASS', report['samples'], ts.resolution)
open_painter.unregister()
