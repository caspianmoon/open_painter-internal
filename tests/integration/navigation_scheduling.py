"""Navigation defers idle canvas scans without dropping short native strokes.

Background mode cannot invoke orbit operators, so their active state is supplied
to the scheduler; actual Blender images and CPU output pixels exercise catch-up.
"""
from pathlib import Path
import sys
from time import monotonic
from types import SimpleNamespace
from unittest.mock import patch

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import open_painter
from open_painter.core import image_manager as images, layer_stack as stack, texture_set_manager as sets
from open_painter.evaluation import cache
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.painting import paint_session
from open_painter.painting.channel_paint import target

open_painter.register()
scene = bpy.context.scene
ts = sets.create(scene, 'Navigation scan', 512)
ts.preview_backend = 'CPU'
layer = stack.add(ts, 'PAINT')
image = target(ts, layer, 'ROUGHNESS')
evaluator = LayerEvaluator(scene)
evaluator.evaluate_texture_set(ts.id)
project = scene.open_painter
project.paint_image = image
project.paint_set_id, project.paint_layer_id = ts.id, layer.id
project.paint_channel = project.paint_target_channel = 'ROUGHNESS'
key = scene.as_pointer()
paint_session._rasters[key] = (image.as_pointer(), images.pixels(image))

# Verify the identifiers against the installed Blender RNA, then feed the same
# operator records Window.modal_operators exposes to the navigation detector.
for identifier in cache._NAVIGATION_OPERATORS:
    category, name = identifier.split('_OT_')
    operator = getattr(getattr(bpy.ops, category.lower()), name)
    assert operator.get_rna_type().identifier == identifier, identifier
    window = SimpleNamespace(modal_operators=[SimpleNamespace(bl_idname=identifier)])
    assert cache._modal_navigation([window]), identifier
assert not cache._modal_navigation([SimpleNamespace(modal_operators=[
    SimpleNamespace(bl_idname='PAINT_OT_image_paint')])])


def write_native(value):
    raster = images.pixels(image)
    raster[20:32, 30:44] = (value, value, value, 1.0)
    image.pixels.foreach_set(raster.ravel())
    image.update()
    assert image.is_dirty
    paint_session._checks[key] = (0.0, None)  # the fallback scan is already due


def settle(expected):
    cache._pending[cache.key_for(scene, ts)] = (ts.edit_serial, monotonic() - 1)
    for _ in range(1000):
        cache.tick()
        if not ts.dirty:
            break
    assert not ts.dirty, 'deferred paint did not reach the output'
    output = images.pixels(images.output(ts, 'ROUGHNESS').image)
    np.testing.assert_allclose(output[20:32, 30:44, :3], expected, atol=1e-6)


# A quick stroke completed between scheduler ticks; orbit starts before the
# fallback scan catches it. Orbit must not trigger a full-image read or advance
# the scan timestamp, and the next ordinary tick must still detect that stroke.
write_native(.23)
serial = ts.edit_serial
with patch.object(cache, '_modal_navigation', return_value=True), \
        patch.object(paint_session, 'pixels', side_effect=AssertionError('canvas read during orbit')):
    for _ in range(5):
        cache.tick()
assert ts.edit_serial == serial and not ts.dirty
assert paint_session._checks[key][0] == 0.0
cache.tick()
assert ts.edit_serial > serial, 'unobserved native stroke was lost during navigation'
settle(.23)

# Explicit flushes remain authoritative even when a navigation operator is up.
write_native(.57)
with patch.object(cache, '_modal_navigation', return_value=True):
    assert paint_session.detect(scene, force=True)
settle(.57)

# A stroke that the scheduler observed also gets its forced release scan; the
# new idle gate must not postpone that scan if the user immediately starts orbit.
write_native(.81)
cache._paint_input[key] = (False, monotonic() - .2)
with patch.object(cache, '_modal_navigation', return_value=True):
    cache.tick()
assert key not in cache._paint_input
settle(.81)

project.paint_image = None
open_painter.unregister()
print('NAVIGATION_SCHEDULING_PASS', flush=True)
