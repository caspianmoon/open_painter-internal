"""Bake back: generated bolts on a panel bake into NORMAL and AO; the maps are non-flat under
the bolts, the bake settings are restored, provenance goes Stale after a regenerate."""
import sys
from pathlib import Path

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import open_painter
from open_painter.baking import bake_back
from open_painter.core import image_manager as images, layer_stack as stack, texture_set_manager as sets
from open_painter.core.published_outputs import publish_mask
from open_painter.geometry import provenance
from open_painter.geometry_effects import manager
from open_painter.utils.ids import new_id

open_painter.register()
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_plane_add(size=2)
panel = bpy.context.object
ts = sets.create(scene, 'Panel', 512); sets.assign(scene, ts, panel)
ts.preview_backend = 'CPU'
layer = stack.add(ts, 'FILL', 'Bolts')
mask = layer.masks.add(); mask.id, mask.source = new_id(), 'PAINT'
spots = images.create('Bolt spots', 512)
data = np.zeros((512, 512, 4), np.float32); data[..., 3] = 1
for cy, cx in ((128, 128), (128, 384), (384, 128), (384, 384)):
    data[cy - 6:cy + 6, cx - 6:cx + 6, :3] = 1.0
images.write(spots, data, pack=True)
mask.image = spots
bpy.context.view_layer.update()
published = publish_mask(ts, layer)
helper = manager.create(ts, panel, published, 'BOLT', density=4000, size=.08, seed=1)
assert helper is not None and 0 < helper['instance_count'] <= 40, helper['instance_count']
saved = (ts.bake.selected_to_active, ts.bake.ray_distance, len(ts.bake.high_objects), ts.bake.normal, ts.bake.ao)
scene.render.engine = 'CYCLES'; scene.cycles.device = 'CPU'; scene.cycles.samples = 4
bpy.context.view_layer.update()
bake_back.run(bpy.context, ts, [helper], then='HIDE')
# Map toggles restored; the helper stays a listed source (with the ray distance that reached it)
# so the maps read current until the helper regenerates.
assert (ts.bake.normal, ts.bake.ao) == (saved[3], saved[4])
assert ts.bake.ray_distance >= saved[1]
assert len(ts.bake.high_objects) == saved[2] + 1 and ts.bake.high_objects[-1].object == helper
normal = images.pixels(next(m.image for m in ts.baked_maps if m.kind == 'NORMAL'))
ao = images.pixels(next(m.image for m in ts.baked_maps if m.kind == 'AO'))
flat = np.abs(normal[..., :2] - .5).max(axis=-1)
assert flat[128 - 20:128 + 20, 128 - 20:128 + 20].max() > .1, 'no relief under a bolt'
assert flat[256 - 10:256 + 10, 256 - 10:256 + 10].max() < .02, 'relief where there is no bolt'
assert ao[128 - 20:128 + 20, 128 - 20:128 + 20, 0].min() < ao[256, 256, 0] - .05, 'no occlusion under a bolt'
assert helper.hide_viewport and helper.hide_render
assert next(m.image for m in ts.baked_maps if m.kind == 'NORMAL').get('open_painter_bake_back')
status = provenance.status(ts, next(m for m in ts.baked_maps if m.kind == 'NORMAL'))
assert not status.startswith('Stale'), status
print('BAKE_BACK maps and settings: PASS', status, flush=True)
helper.hide_viewport = helper.hide_render = False
replacement = manager.regenerate(ts, helper)
assert replacement is not None
status = provenance.status(ts, next(m for m in ts.baked_maps if m.kind == 'NORMAL'))
assert status.startswith('Stale'), status   # the regenerated helper is a new source
bake_back.run(bpy.context, ts, [replacement], then='REMOVE')
assert not any(ref.object is None for ref in ts.bake.high_objects)
assert not any(obj.get(manager.TAG) == 'INSTANCE_HELPER' for obj in scene.objects)
print('BAKE_BACK_PASS', flush=True)
open_painter.unregister()
