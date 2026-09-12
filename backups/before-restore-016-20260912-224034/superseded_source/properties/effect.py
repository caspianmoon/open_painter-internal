"""Persistent, non-destructive color adjustments shared by content and mask stacks."""

import bpy

from ..constants import CHANNEL_ITEMS


def changed(self, context):
    project = getattr(self.id_data, "open_painter", None)
    if not project:
        return
    for ts in project.texture_sets:
        for layer in ts.layers:
            if any(
                e.id == self.id for effects in (layer.effects, layer.mask_effects) for e in effects
            ):
                # Invalidate old and new affected channels when channel selection changes.
                from ..core.layer_stack import invalidate

                invalidate(ts)
                return


class OP_Effect(bpy.types.PropertyGroup):
    id: bpy.props.StringProperty()
    name: bpy.props.StringProperty(name="Name", default="Effect")
    enabled: bpy.props.BoolProperty(name="Enabled", default=True, update=changed)
    type: bpy.props.EnumProperty(
        name="Effect",
        items=[
            ("LEVELS", "Levels", "Adjust tonal range"),
            ("FILTER", "Filter", "Non-destructive color adjustment"),
        ],
        update=changed,
    )
    channel: bpy.props.EnumProperty(name="Affected Channel", items=CHANNEL_ITEMS, update=changed)
    component: bpy.props.EnumProperty(
        name="Colors",
        items=[("RGB", "RGB", ""), ("R", "Red", ""), ("G", "Green", ""), ("B", "Blue", "")],
        update=changed,
    )
    opacity: bpy.props.FloatProperty(name="Effect Opacity", default=1, min=0, max=1, update=changed)
    input_black: bpy.props.FloatProperty(
        name="Input Black", default=0, soft_min=0, soft_max=1, update=changed
    )
    input_white: bpy.props.FloatProperty(
        name="Input White", default=1, soft_min=0, soft_max=1, update=changed
    )
    gamma: bpy.props.FloatProperty(name="Gamma", default=1, min=0.01, max=10, update=changed)
    output_black: bpy.props.FloatProperty(
        name="Output Black", default=0, soft_min=0, soft_max=1, update=changed
    )
    output_white: bpy.props.FloatProperty(
        name="Output White", default=1, soft_min=0, soft_max=1, update=changed
    )
    clamp: bpy.props.BoolProperty(name="Clamp", default=True, update=changed)
    filter: bpy.props.EnumProperty(
        name="Filter",
        items=[
            ("INVERT", "Invert", "Invert color values"),
            ("GRAYSCALE", "Grayscale", "Linear luminance"),
            ("BRIGHTNESS_CONTRAST", "Brightness / Contrast", "Adjust around middle gray"),
        ],
        update=changed,
    )
    brightness: bpy.props.FloatProperty(name="Brightness", default=0, min=-1, max=1, update=changed)
    contrast: bpy.props.FloatProperty(name="Contrast", default=1, min=0, max=10, update=changed)
