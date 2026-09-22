"""Verify corrected source/relief remain packed and unchanged after reopen; then undo."""
import json
from pathlib import Path
import sys
import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'src'))
import open_painter
from open_painter.authoring import generated_surfaces, surface_replacement, transactions
from open_painter.core import image_manager

open_painter.register()
folder = ROOT/'.blender-test/director-correspondence'
packet = json.loads((folder/'packet.json').read_text())
bpy.ops.wm.open_mainfile(filepath=str(folder/'corrected.blend'), use_scripts=False)
ts = bpy.context.scene.open_painter.texture_sets[0]
layer = ts.layers[0]
base = next(p for p in layer.channels if p.channel == 'BASE_COLOR')
reference = next(image for image in bpy.data.images if image.get('director_image_id') == packet['sources'][0]['reference_image_id']
                 and image.colorspace_settings.name == 'sRGB' and not image.get('director_surface_derivation'))
assert base.image.packed_file and reference.packed_file
old, corrected = image_manager.pixels(reference), image_manager.pixels(base.image)
mask = surface_replacement.window_mask(corrected.shape[:2], packet['sources'][0]['source_windows'])
assert np.array_equal(old[mask == 0], corrected[mask == 0])
assert np.max(abs(old[mask == 1]-corrected[mask == 1])) > .1
rough = next(p for p in layer.channels if p.channel == 'ROUGHNESS')
assert rough.detail_image.packed_file
original_detail = generated_surfaces.scalar_detail(old)
np.testing.assert_allclose(image_manager.pixels(rough.detail_image)[...,0][mask == 0], original_detail[mask == 0], atol=1e-6)
assert len(transactions.history(ts)) == 1
transactions.undo(ts)
assert len(ts.layers) == 0
print('DIRECTOR_CORRESPONDENCE_REOPEN_PASS: packed local edits, preserved detail, exact single undo')
