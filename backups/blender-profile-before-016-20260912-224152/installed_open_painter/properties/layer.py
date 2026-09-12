import bpy

from ..constants import BLEND_ITEMS
from .channel import OP_Channel
from .mask import OP_Mask


def changed(self, context):
    project = getattr(self.id_data, "open_painter", None)
    if project:
        for ts in project.texture_sets:
            identifier = getattr(self, "id", "")
            belongs = identifier == ts.id or any(
                item.id == identifier or any(m.id == identifier for m in item.masks)
                for item in ts.layers
            )
            if belongs:
                ts.dirty = True
                ts.edit_serial += 1
                for output in ts.outputs:
                    output.dirty = True


class OP_Layer(bpy.types.PropertyGroup):
    id: bpy.props.StringProperty()
    name: bpy.props.StringProperty(default="Layer")
    type: bpy.props.EnumProperty(
        items=[
            ("PAINT", "Paint", "Raster channel images"),
            ("FILL", "Fill", "Constant or texture fill"),
            ("FOLDER", "Folder", "Group layers"),
        ]
    )
    visible: bpy.props.BoolProperty(default=True, update=changed)
    opacity: bpy.props.FloatProperty(name="Opacity", default=1, min=0, max=1, update=changed)
    blend_mode: bpy.props.EnumProperty(
        name="Blend",
        items=BLEND_ITEMS,
        update=changed,
    )
    group_mode: bpy.props.EnumProperty(
        name="Folder Composition",
        update=changed,
        items=[
            ("ISOLATED", "Isolated", "Blend children within the folder"),
            ("PASSTHROUGH", "Passthrough", "Child blend modes see layers below the folder"),
        ],
    )
    parent_id: bpy.props.StringProperty(update=changed)
    order: bpy.props.IntProperty()
    channels: bpy.props.CollectionProperty(type=OP_Channel)
    dirty: bpy.props.BoolProperty(default=True)
    masks: bpy.props.CollectionProperty(type=OP_Mask)
    active_mask: bpy.props.IntProperty()
    expanded: bpy.props.BoolProperty(name="Expand Group", default=True)
