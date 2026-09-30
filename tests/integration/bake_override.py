"""Rebakes publish stable names/bindings, retain undo data, and isolate failure."""
from pathlib import Path
import sys
from unittest.mock import patch

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.baking import cycles_baker
from open_painter.baking.bake_manager import bake_selected, register_map
from open_painter.baking.maps.curvature import curvature_steps
from open_painter.baking.maps import utility
from open_painter.core import texture_set_manager as sets, image_manager as images, layer_stack as stack

open_painter.register()
scene = bpy.context.scene
ts = sets.create(scene, 'Override', 512)
ts.preview_backend = 'CPU'
sets.assign(scene, ts, bpy.context.object)
ts.bake.device = 'CPU'
ts.bake.normal, ts.bake.ao, ts.bake.curvature = False, False, False
ts.bake.position = True
bake_selected(bpy.context, ts)
first = next(m.image for m in ts.baked_maps if m.kind == 'POSITION')
canonical = first.name
before = images.pixels(first).copy()
layer = stack.add(ts)
channel = stack.channel_for(layer, 'BASE_COLOR')
channel.image = first
# An unrelated artist material must keep the image it explicitly selected.
material = bpy.data.materials.new('Artist material')
material.use_nodes = True
node = material.node_tree.nodes.new('ShaderNodeTexImage')
node.image = first
bake_selected(bpy.context, ts)
second = next(m.image for m in ts.baked_maps if m.kind == 'POSITION')
assert second != first and second.name == canonical
assert channel.image == second and node.image == first
np.testing.assert_array_equal(images.pixels(first), before)
assert not first.use_fake_user and second.packed_file
assert second.get('open_painter_pixel_revision', 0) > first.get('open_painter_pixel_revision', 0)
# A write failure cannot alter the published result, its size, or provenance.
entry = next(m for m in ts.baked_maps if m.kind == 'POSITION')
provenance = entry.provenance
before = images.pixels(second).copy()
image_ids = {image.as_pointer() for image in bpy.data.images}
with patch.object(utility.images, 'write', side_effect=RuntimeError('write failed')):
    try:
        bake_selected(bpy.context, ts)
    except RuntimeError:
        pass
    else:
        raise AssertionError('write failure was swallowed')
assert {image.as_pointer() for image in bpy.data.images} == image_ids
assert entry.image == second and entry.provenance == provenance
np.testing.assert_array_equal(images.pixels(second), before)

# Cycles failures do not clear or resize the live map even with supersampling.
normal = images.create('Override_BakedNormal', 32, (.2, .3, .4, 1))
register_map(ts, 'NORMAL', normal)
normal_before = images.pixels(normal).copy()
ts.bake.supersample = '2'
with patch.object(cycles_baker, '_run_bake', side_effect=RuntimeError('Cycles failed')):
    try:
        cycles_baker.bake(bpy.context, ts, 'NORMAL')
    except RuntimeError:
        pass
    else:
        raise AssertionError('Cycles failure was swallowed')
np.testing.assert_array_equal(images.pixels(normal), normal_before)
assert tuple(normal.size) == (32, 32)

# A cancelled edge-map job must discard new reaches and keep all previous maps.
old = images.create('Override_BakedCurvatureThin', 32, (.3, .3, .3, 1))
register_map(ts, 'CURVATURE_THIN', old)
old_pixels = images.pixels(old).copy()
from open_painter.baking.maps import curvature
produced = []
def fake_reach(context, target, kind, radius):
    image = images.create('temporary reach', 32)
    produced.append(image.name)
    return image
with patch.object(curvature, 'bake_curvature_reach', side_effect=fake_reach):
    job = curvature_steps(bpy.context, ts)
    next(job)
    next(job)
    job.close()
assert produced and all(name not in bpy.data.images for name in produced)
np.testing.assert_array_equal(images.pixels(old), old_pixels)
open_painter.unregister()
print('BAKE_OVERRIDE_PASS', flush=True)
