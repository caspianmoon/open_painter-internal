import bpy

from ..constants import BLEND_ITEMS
from .channel import OP_Channel
from .mask import OP_Mask


def changed(self, context):
    _changed(self, context, True)


def numeric(self, context):
    """A uniform edit: the shader binds it without re-deriving graph structure."""
    _changed(self, context, False)


def _changed(self, context, structural):
    project = getattr(self.id_data, "open_painter", None)
    if project:
        pointer = self.as_pointer()
        for ts in project.texture_sets:
            owned = next((layer for layer in ts.layers if layer.as_pointer() == pointer or
                          any(mask.as_pointer() == pointer for mask in layer.masks)), None)
            belongs = ts.as_pointer() == pointer or owned is not None
            if belongs:
                previous_dirty = {output.channel: output.dirty for output in ts.outputs}
                ts.dirty = True
                ts.edit_serial += 1
                from ..evaluation.references import dependent_channels
                affected = ({prop.channel for prop in owned.channels if prop.enabled}
                            | dependent_channels(ts, owned.id) if owned else
                            {output.channel for output in ts.outputs})
                if ts.preview_backend == 'CPU':
                    affected.update(output.channel for output in ts.outputs)
                for output in ts.outputs:
                    if output.channel in affected:
                        output.dirty = True
                from ..evaluation.shader_preview import notify
                notify(self, context, previous_dirty=previous_dirty, structural=structural)
                return


class OP_Layer(bpy.types.PropertyGroup):
    effect_semantic: bpy.props.EnumProperty(name='Age Response',items=[(k,k.replace('_',' ').title(),'Explicit artistic effect role')
        for k in ('NONE','DIRT','DUST','FADE','SCRATCHES','OXIDATION','COATING_LOSS')])
    id: bpy.props.StringProperty()
    name: bpy.props.StringProperty(default="Layer")
    type: bpy.props.EnumProperty(
        items=[
            ("PAINT", "Paint", "Raster channel images"),
            ("FILL", "Fill", "Constant or texture fill"),
            ("FOLDER", "Folder", "Group layers"),
        ]
    )
    visible: bpy.props.BoolProperty(default=True, update=numeric)
    opacity: bpy.props.FloatProperty(name="Opacity", default=1, min=0, max=1, update=numeric)
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
    # Published outputs: derived anchor ids "<id>:out" (this layer blended over
    # its backdrop) and "<id>:group" (a folder's child composite before it blends).
    publish_output: bpy.props.BoolProperty(name="Publish Output", default=False, update=changed,
        description="Publish this layer's blended result as an anchor other layers can read")
    publish_group: bpy.props.BoolProperty(name="Publish Folder Composite", default=False, update=changed,
        description="Publish the folder's child composite, before the folder itself blends, as an anchor")
    # Material module identity on an instance root folder (0.4.1). Metadata only:
    # ids inside the instance are "<module_instance>.<module id>".
    module_id: bpy.props.StringProperty(description="Module definition this folder is an instance of")
    module_version: bpy.props.IntProperty(default=0, description="Module version this instance was inserted from or updated to")
    module_source: bpy.props.StringProperty(description="library:<file> or catalog:<recipe> the module came from")
    module_instance: bpy.props.StringProperty(description="Namespace token of this instance's ids")
    module_name: bpy.props.StringProperty()
    module_detached: bpy.props.BoolProperty(default=False, name="Detached",
        description="Keep the module provenance but exclude this instance from updates")
