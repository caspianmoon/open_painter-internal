"""Anchor points: create them in a stack, pick them from consumers, navigate both ways."""
import bpy

from ..constants import CHANNEL_ITEMS
from ..core.layer_stack import active, channel_for, invalidate
from ..core.project_manager import active_set, require_set
from ..evaluation import anchors
from ..evaluation.anchors import CONSUMER_FIELDS, CONSUMER_LABELS
from ..utils.ids import new_id
from .operators import SafeOperator
from .texture_operations import SearchPicker, _channel, _layer, _redraw

TARGETS = [(key, CONSUMER_LABELS[key], "") for key in CONSUMER_FIELDS]
_items = []


def _select_layer(context, ts, layer_id, mask):
    from . import selection
    target = selection.select(context, layer_id, selection.MASK if mask else selection.CONTENT, ts=ts)
    return target.layer


def _consumer(context, ts, layer, target):
    """(owner, field) of the active consumer for a picker target."""
    if target == "CHANNEL":
        if layer.type != "FILL":
            raise ValueError("Anchored content belongs on fill layers. Paint layers keep painted pixels.")
        return _channel(context, layer), "anchor"
    if not layer.masks:
        raise ValueError("Add a mask first.")
    mask = layer.masks[max(0, min(layer.active_mask, len(layer.masks) - 1))]
    if target == "REFERENCE_MASK" and mask.source != "REFERENCE":
        raise ValueError("Select a Published Input mask first.")
    if target != "REFERENCE_MASK" and mask.source != "GENERATOR":
        raise ValueError("Select a generator mask first.")
    return mask, CONSUMER_FIELDS[target]


def _is_above(ts, anchor, layer, target, item):
    layer_index = next(i for i, candidate in enumerate(ts.layers) if candidate.id == layer.id)
    if anchor.layer_id != layer.id:
        return anchors.sort_key(ts, anchor)[0] > layer_index
    if anchor.kind in anchors.COMPOSITE_KINDS:
        return True   # a layer's own blended output always depends on the layer
    if target == "CHANNEL":
        return anchor.kind == "CHANNEL" and anchor.channel == item.channel
    if anchor.kind != "MASK":
        return False
    return anchor.position >= next(i for i, mask in enumerate(layer.masks) if mask.id == item.id)


def _eligible(anchor, target):
    composite = anchor.kind in anchors.COMPOSITE_KINDS   # carries every channel
    if target == "DETAIL_HEIGHT":
        return composite or anchor.kind == "MASK" or anchor.channel == "HEIGHT"
    if target == "DETAIL_NORMAL":
        return composite or (anchor.kind == "CHANNEL" and anchor.channel == "NORMAL")
    return True


def anchor_items(self, context):
    global _items
    # Blender keeps pointers to dynamic enum strings: retain these tuples.
    _items = [("NONE", "None (clear)", "Remove the anchor reference")]
    ts = active_set(context) if context else None
    layer = active(ts) if ts else None
    if layer is None:
        return _items
    try:
        item, _field = _consumer(context, ts, layer, self.target)
    except ValueError:
        # Scripted calls may convert `choice` before `target`: list every anchor.
        item = None
    registry = anchors.index(ts)
    for anchor in sorted(registry.values(), key=lambda entry: anchors.sort_key(ts, entry)):
        if item is not None and not _eligible(anchor, self.target):
            continue
        above = item is not None and _is_above(ts, anchor, layer, self.target, item)
        _items.append((anchor.id, anchors.label(ts, anchor) + (" (above)" if above else ""),
                       "Captured above this consumer; fine unless it depends on this layer" if above
                       else "Reference this anchor point"))
    return _items


def _next_name(ts):
    names = {anchor.name for anchor in anchors.index(ts).values()}
    count = len(names) + 1
    while f"Anchor Point {count}" in names:
        count += 1
    return f"Anchor Point {count}"


def broken_labels(ts, identifiers):
    """Consumer labels for anchors about to disappear; compute before mutating collections."""
    return [f"{layer.name} ({CONSUMER_LABELS[kind]})"
            for identifier in identifiers for layer, kind, _ in anchors.consumers(ts, identifier)]


def report_broken(operator, context, labels):
    if not labels:
        return
    message = (f"Broke {len(labels)} reference{'s' if len(labels) != 1 else ''}: "
               + ", ".join(labels[:6]) + (" …" if len(labels) > 6 else ""))
    context.scene.open_painter.status = message
    operator.report({"WARNING"}, message)


class OP_OT_anchor(SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.anchor"
    bl_label = "Add Anchor Point"
    bl_description = "Publish the stack result at this position for other layers to reference"
    bl_options = {"REGISTER", "UNDO"}
    action: bpy.props.EnumProperty(items=[("ADD_MASK", "Mask Stack", "Anchor the mask result at this position"),
                                          ("ADD_CHANNEL", "Channel Filters", "Anchor the channel content after the filters above")])

    def run(self, context):
        ts, layer = _layer(context)
        if self.action == "ADD_MASK":
            mask = layer.masks.add()
            mask.id, mask.name = new_id(), _next_name(ts)
            mask.source = "ANCHOR"
            layer.active_mask = len(layer.masks) - 1
            _redraw(context, ts, mask=True)
            from ..painting.paint_session import synchronize
            synchronize(context, mask=True)
            return
        prop = _channel(context, layer)
        effect = prop.effects.add()
        effect.id, effect.name = new_id(), _next_name(ts)
        effect.kind = "ANCHOR"
        prop.active_effect = len(prop.effects) - 1
        _redraw(context, ts, mask=False)


class OP_OT_anchor_pick(SearchPicker, SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.anchor_pick"
    bl_label = "Choose Anchor Point"
    bl_description = "Reference an anchor point; any acyclic reference is allowed"
    bl_options = {"REGISTER", "UNDO"}
    bl_property = "choice"
    choice: bpy.props.EnumProperty(name="Anchor", items=anchor_items)
    target: bpy.props.EnumProperty(items=TARGETS)
    # SKIP_SAVE: the picker button leaves `clear` unset, so it must not inherit
    # the X button's last-used True and wipe the field instead of searching.
    clear: bpy.props.BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        if self.clear:
            return self.execute(context)
        return SearchPicker.invoke(self, context, event)

    def run(self, context):
        ts, layer = _layer(context)
        owner, field = _consumer(context, ts, layer, self.target)
        previous = getattr(owner, field)
        setattr(owner, field, "" if self.clear or self.choice == "NONE" else self.choice)
        from ..evaluation.references import issues
        errors = issues(ts)
        if errors:
            setattr(owner, field, previous)
            raise ValueError(" | ".join(errors))
        _redraw(context, ts, mask=self.target != "CHANNEL")


class OP_OT_anchor_locate(SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.anchor_locate"
    bl_label = "Locate Anchor"
    bl_description = "Select the layer and stack item that publish this anchor point"
    bl_options = {"REGISTER", "UNDO"}
    anchor_id: bpy.props.StringProperty()

    def run(self, context):
        ts = require_set(context)
        anchor = anchors.index(ts).get(self.anchor_id)
        if anchor is None:
            raise ValueError("Anchor point is missing. Pick another anchor or clear the reference.")
        layer = _select_layer(context, ts, anchor.layer_id, anchor.kind == "MASK")
        if anchor.kind in anchors.COMPOSITE_KINDS:
            return   # the layer itself publishes; there is no stack item to select
        if anchor.kind == "MASK":
            layer.active_mask = next(i for i, mask in enumerate(layer.masks) if mask.id == self.anchor_id)
            return
        project = context.scene.open_painter
        setattr(project, "paint_channel" if layer.type == "PAINT" else "properties_channel", anchor.channel)
        prop = channel_for(layer, anchor.channel)
        prop.active_effect = next(i for i, effect in enumerate(prop.effects) if effect.id == self.anchor_id)


class OP_OT_anchor_select_consumer(SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.anchor_select_consumer"
    bl_label = "Select Anchor Consumer"
    bl_description = "Select the layer, mask or channel that references this anchor point"
    bl_options = {"REGISTER", "UNDO"}
    layer_id: bpy.props.StringProperty()
    kind: bpy.props.EnumProperty(items=TARGETS)
    key: bpy.props.StringProperty(description="Mask id, or the channel name for channel content")

    def run(self, context):
        ts = require_set(context)
        layer = _select_layer(context, ts, self.layer_id, self.kind != "CHANNEL")
        if self.kind == "CHANNEL":
            project = context.scene.open_painter
            setattr(project, "paint_channel" if layer.type == "PAINT" else "properties_channel", self.key)
            return
        layer.active_mask = next((i for i, mask in enumerate(layer.masks) if mask.id == self.key), layer.active_mask)


def draw_anchor_field(layout, ts, target, anchor_id, text="Anchor"):
    """Picker button with the anchor's name, a locate button and a clear button."""
    anchor = anchors.index(ts).get(anchor_id) if anchor_id else None
    row = layout.row(align=True)
    if anchor_id and anchor is None:
        row.alert = True
        op = row.operator("open_painter.anchor_pick", text="Missing anchor: pick again", icon="ERROR")
    else:
        op = row.operator("open_painter.anchor_pick", icon="ANCHOR_CENTER",
                          text=f"{anchor.name} · {anchor.layer.name}" if anchor else f"Choose {text}")
    op.target = target
    if anchor:
        row.operator("open_painter.anchor_locate", text="", icon="VIEWZOOM").anchor_id = anchor_id
    if anchor_id:
        op = row.operator("open_painter.anchor_pick", text="", icon="X")
        op.target, op.clear = target, True
    return anchor


CHANNEL_CHOICES = [("SAME", "Same Channel", "Read the channel this content belongs to")] + [
    (key, label, "") for key, label, *_ in CHANNEL_ITEMS]


class OP_OT_anchor_channel(SafeOperator, bpy.types.Operator):
    bl_idname = "open_painter.anchor_channel"
    bl_label = "Anchor Channel"
    bl_description = "Which channel of a published layer output or folder composite this content reads"
    bl_options = {"REGISTER", "UNDO"}
    bl_property = "channel"
    channel: bpy.props.EnumProperty(items=CHANNEL_CHOICES)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def run(self, context):
        ts, layer = _layer(context)
        prop = _channel(context, layer)
        prop.anchor_channel = "" if self.channel == "SAME" else self.channel
        _redraw(context, ts, mask=False)


def draw_anchor_channel(layout, anchor, owner):
    """Channel selector for a composite anchor consumer (fill content or reference mask)."""
    if anchor is None or anchor.kind not in anchors.COMPOSITE_KINDS:
        return
    if hasattr(owner, "reference_channel"):
        row = layout.row(align=True)
        row.prop(owner, "reference_channel", text="Channel")
        if owner.reference_channel == "MASK":
            row.label(text="", icon="INFO")
            layout.label(text=f"Composite anchors have no mask stack; reading {anchors.DEFAULT_CHANNEL.replace('_', ' ').title()}")
        return
    label = dict((key, name) for key, name, _ in CHANNEL_CHOICES).get(owner.anchor_channel or "SAME", "Same Channel")
    layout.operator("open_painter.anchor_channel", text=f"Channel: {label}", icon="NODE_COMPOSITING")


def draw_publish_controls(layout, ts, layer):
    """Publish toggles with the anchors' consumers, on the layer properties."""
    box = layout.box()
    box.label(text="Published Outputs", icon="ANCHOR_CENTER")
    row = box.row(align=True)
    row.prop(layer, "publish_output", text="Layer Output", toggle=True)
    if layer.type == "FOLDER":
        row.prop(layer, "publish_group", text="Folder Composite", toggle=True)
    for flag, identifier in (("publish_output", anchors.output_id(layer.id)),
                             ("publish_group", anchors.group_id(layer.id))):
        if getattr(layer, flag, False) and (flag == "publish_output" or layer.type == "FOLDER"):
            draw_used_by(box, ts, identifier)


def draw_used_by(layout, ts, anchor_id):
    users = anchors.consumers(ts, anchor_id)
    box = layout.box()
    box.label(text=f"Used by {len(users)}" if users else "Not referenced yet", icon="DECORATE_LINKED")
    if not users:
        box.label(text="Pick it from a Published Input mask, a fill channel's Content or Micro Details.")
        return
    for layer, kind, item in users:
        row = box.row(align=True)
        detail = item.channel.replace("_", " ").title() if kind == "CHANNEL" else item.name
        row.label(text=f"{layer.name} · {CONSUMER_LABELS[kind]} ({detail})",
                  icon="MATERIAL" if kind == "CHANNEL" else "MOD_MASK")
        op = row.operator("open_painter.anchor_select_consumer", text="", icon="RESTRICT_SELECT_OFF")
        op.layer_id, op.kind, op.key = layer.id, kind, (item.channel if kind == "CHANNEL" else item.id)


def draw_anchor_settings(layout, ts, item):
    """Shared by anchor masks and anchor effects: name and find-references."""
    layout.prop(item, "name", text="Anchor Name")
    draw_used_by(layout, ts, item.id)


CLASSES = (OP_OT_anchor, OP_OT_anchor_pick, OP_OT_anchor_locate, OP_OT_anchor_select_consumer,
           OP_OT_anchor_channel)
