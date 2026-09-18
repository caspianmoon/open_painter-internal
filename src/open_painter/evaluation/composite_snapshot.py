"""Explicit, one-channel CPU snapshots for the Image Editor.

Blender's Image Editor needs pixels. Native material authoring does not produce
composite images after slider edits; opening/refreshing this view is the explicit
raster consumer, and its result remains visibly a snapshot afterward.
"""
from ..constants import CHANNELS
from . import metrics

MARKER = 'open_painter_composite_snapshot'


def request(scene, ts, channel):
    if channel not in CHANNELS:
        raise ValueError('Select a valid composite channel')
    from ..core.image_manager import output
    target = output(ts, channel)
    if target.dirty or not target.image.get(MARKER):
        from .evaluator import LayerEvaluator
        image = LayerEvaluator(scene).evaluate_channel(ts.id, channel)
        if image is None:
            raise RuntimeError('The material changed while preparing its snapshot; refresh again')
        metrics.count('composite_snapshot_evaluations')
    target.image[MARKER] = True
    target.image['open_painter_snapshot_serial'] = ts.edit_serial
    return target.image


def stale(ts, image):
    if image is None or not image.get(MARKER):
        return False
    target = next((out for out in ts.outputs if out.image == image), None)
    return target is None or target.dirty or tuple(image.size) != (int(ts.resolution),) * 2


def for_view(scene, ts, channel):
    # Active native painting always displays its authored canvas, including masks.
    if scene.open_painter.paint_image:
        return scene.open_painter.paint_image
    if ts is None:
        return None
    from ..ui import mask_view
    view = mask_view.active(ts)
    if view is not None and mask_view.image_of(view) is not None:
        return mask_view.image_of(view)   # View Mask: the 2D editor shows the same snapshot
    from .shader_preview import requested
    if requested(ts):
        return request(scene, ts, channel)
    from .live_display import displayed_image
    return displayed_image(scene, ts, channel)
