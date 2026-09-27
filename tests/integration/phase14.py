"""Cooperative jobs, cancellation and blank layer invalidation."""

import sys
from pathlib import Path
from time import monotonic
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
from open_painter.evaluation import cache
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.painting.channel_paint import target

open_painter.register()
scene = bpy.context.scene
ts = sets.create(scene, "Cooperative", 512)
ts.preview_backend = 'CPU'  # This regression exercises explicit raster scheduling.
base = stack.add(ts, "FILL")
LayerEvaluator(scene).evaluate_texture_set(ts.id)
serial = ts.edit_serial
before = [o.revision for o in ts.outputs]
stack.add(ts, "PAINT")
stack.add(ts, "FOLDER")
assert not ts.dirty and ts.edit_serial == serial
assert [o.revision for o in ts.outputs] == before
paint = stack.add(ts, "PAINT")
image = target(ts, paint, "BASE_COLOR")
values = np.zeros((512, 512, 4), np.float32)
values[80:130, 100:170] = (0.2, 0.1, 0.05, 0.5)
images.write(image, values, pack=False)
# Inject a paused real job, then edit the stack. The timer must discard it.
job = LayerEvaluator(scene).channel_steps(ts.id, "BASE_COLOR")
next(job)
cache._jobs[cache.key_for(scene, ts)] = ("BASE_COLOR", job)
cache._pending[cache.key_for(scene, ts)] = (ts.edit_serial, monotonic() - 1)
paint.opacity = 0.3
cache.tick()
assert cache.key_for(scene, ts) not in cache._jobs
cache._pending[cache.key_for(scene, ts)] = (ts.edit_serial, monotonic() - 1)
for _ in range(2000):
    cache.tick()
    if not ts.dirty:
        break
assert not ts.dirty
out = next(o for o in ts.outputs if o.channel == "BASE_COLOR")
actual = images.pixels(out.image)
# Premultiplied .2/.5 becomes .4; effective alpha=.5*.3=.15.
np.testing.assert_allclose(
    actual[100, 120, :3],
    (0.5 * 0.85 + 0.4 * 0.15, 0.5 * 0.85 + 0.2 * 0.15, 0.5 * 0.85 + 0.1 * 0.15),
    atol=1e-6,
)
np.testing.assert_allclose(actual[0, 0, :3], (0.5, 0.5, 0.5), atol=1e-6)
LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
np.testing.assert_allclose(images.pixels(out.image), actual, atol=1e-6)
# Regional raster updates must retain untouched rows and match a full composite.
values[300:302, 90:95] = (0.1, 0.2, 0.3, 0.5)
images.write(image, values, pack=False)
from open_painter.painting.channel_paint import mark_changed

mark_changed(ts, paint, "BASE_COLOR")
cache._regions[cache.key_for(scene, ts)] = (ts.edit_serial, "BASE_COLOR", 300, 302, image.as_pointer(), values)
cache._pending[cache.key_for(scene, ts)] = (ts.edit_serial, monotonic() - 1)
for _ in range(1000):
    cache.tick()
    if not ts.dirty:
        break
regional = images.pixels(out.image)
np.testing.assert_array_equal(regional[:300], actual[:300])
LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
np.testing.assert_allclose(images.pixels(out.image), regional, atol=1e-6)
# Fill creation dirties exactly its enabled core channels.
stack.add(ts, "FILL")
assert {o.channel for o in ts.outputs if o.dirty} == set(stack.FILL_DEFAULT_CHANNELS)
open_painter.unregister()
print(
    "PHASE 14 PASS: empty layers preserve output revisions, fill dirties only enabled channels, stale jobs cancel, cooperative pixels match full evaluation and premultiplied reference"
)
