"""Lazy allocation and dirty routing for future replicated channel strokes."""

from ..core.image_manager import create
from ..core.layer_stack import channel_for, invalidate


def target(ts, layer, channel, mask=False):
    if mask:
        if not layer.masks:
            raise ValueError("Add a painted mask first.")
        selected = layer.masks[min(layer.active_mask, len(layer.masks) - 1)]
        if selected.source != "PAINT" or not selected.image:
            raise ValueError("Select a painted mask to paint.")
        return selected.image
    if layer.type != "PAINT":
        raise ValueError("Select a Paint Layer to paint channel content.")
    prop = channel_for(layer, channel)
    if prop is None:
        raise ValueError("The layer has no selected channel.")
    if not prop.enabled:
        prop.enabled = True
    if not prop.image:
        prop.image = create(
            f"{ts.name}_{layer.name}_{channel}",
            int(ts.resolution),
            color_data=channel in {"BASE_COLOR", "EMISSION"},
        )
        # Managed images are packed by the save handler, not on brush startup.
    return prop.image


def mark_changed(ts, layer, channel, mask=False):
    if mask:
        invalidate(ts)
    else:
        prop = channel_for(layer, channel)
        if prop:
            prop.dirty = True
        layer.dirty = True
        ts.dirty = True
        serial = ts.edit_serial
        ts.edit_serial += 1
        # Pixels change nothing structural: answers memoised per structure (the readers
        # below, the geometry uses) stand through a stroke.
        from ..evaluation.structure_memo import numeric_edit
        numeric_edit(ts, serial)
        from ..evaluation.references import dependent_channels
        affected = dependent_channels(ts, layer.id, channel) | {channel}
        for out in ts.outputs:
            if out.channel in affected:
                out.dirty = True
