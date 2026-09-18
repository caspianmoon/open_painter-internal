"""One editing target for Layers, Nodes, the inspector, the shelf and the assistant.

The document already records *which* set, layer, mask and channel filter are active
(``project.active_texture_set``, ``ts.active_layer``, ``layer.active_mask``,
``channel.active_effect``, ``project.properties_channel``). What *kind* of thing the
artist is editing — the layer's content, its mask stack or one of its channel
filters — is presentation state of the Painter area (``DockState.selection_kind``).
Everything that reads or changes the selection goes through ``resolve`` and
``select`` so the layer list, the graph, the inspector, the shelf and the Material
Director agree on the same target, resolved from stable ids, never from row indices.

Three channel choices stay separate on purpose (see docs/ui-refactor.md):
``project.display_channel`` is what the 2D composite snapshot shows,
``project.blend_channel`` is the channel whose blend and opacity the layer rows edit,
and ``project.paint_channel`` (with ``paint_mask``) is where the brush writes.
"""
from dataclasses import dataclass, field

from ..core.layer_stack import active, channel_for
from ..core.project_manager import active_set

CONTENT, MASK, FILTER = "CONTENT", "MASK", "FILTER"
KIND_LABELS = {"NONE": "Nothing selected", "SET": "Texture Set", "MATERIAL": "Smart Material",
               "FOLDER": "Folder", "FILL": "Fill Layer", "PAINT": "Paint Layer", "MASK": "Mask",
               "FILTER": "Filter"}
KIND_ICONS = {"NONE": "BLANK1", "SET": "TEXTURE", "MATERIAL": "MATERIAL", "FOLDER": "FILE_FOLDER",
              "FILL": "MATERIAL", "PAINT": "BRUSH_DATA", "MASK": "MOD_MASK", "FILTER": "MODIFIER"}


@dataclass
class PaintDestination:
    """Where a brush stroke lands for the current target, or why it cannot."""
    kind: str = "NONE"          # 'LAYER', 'MASK' or 'NONE'
    set_id: str = ""
    layer_id: str = ""
    mask_id: str = ""
    channel: str = ""
    image: object = None
    reason: str = ""            # why nothing is paintable (kind == 'NONE')
    live: bool = False          # a native paint session is running on this destination

    @property
    def label(self):
        if self.kind == "MASK":
            return "Mask"
        if self.kind == "LAYER":
            return self.channel.replace("_", " ").title()
        return self.reason or "Not paintable"


@dataclass
class Target:
    kind: str = "NONE"
    ts: object = None
    layer: object = None
    mask: object = None
    channel: object = None     # the inspected channel property group of a fill layer
    effect: object = None      # the selected channel filter (kind == 'FILTER')
    texture_set_id: str = ""
    layer_id: str = ""
    mask_id: str = ""
    effect_id: str = ""
    channel_name: str = ""
    display_channel: str = ""
    blend_channel: str = ""
    paint: PaintDestination = field(default_factory=PaintDestination)
    path: list = field(default_factory=list)   # [(label, icon), ...] from the set down to the item

    @property
    def label(self):
        return self.path[-1][0] if self.path else KIND_LABELS["NONE"]

    @property
    def icon(self):
        return KIND_ICONS.get(self.kind, "BLANK1")

    @property
    def is_mask(self):
        return self.kind == "MASK"


def is_material(layer):
    """A folder that is a smart-material instance: applied from the catalog or a saved module."""
    return layer is not None and layer.type == "FOLDER" and bool(layer.module_id)


def painter_area(context):
    """The Painter area whose presentation state owns the selection.

    The Painter area itself when the call comes from one; otherwise the first Painter
    area of the same screen (a companion inspector follows the session it was opened
    from, never another window's). None outside any window.
    """
    from . import editor
    area = getattr(context, "area", None)
    if area is not None and editor.is_area(area):
        return area
    screen = getattr(context, "screen", None)
    if screen is not None:
        for candidate in screen.areas:
            if editor.is_area(candidate):
                return candidate
    return area


def session_area(context):
    """The Painter area of this screen, or None when the screen shows no Painter session."""
    from . import editor
    area = getattr(context, "area", None)
    if area is not None and editor.is_area(area):
        return area
    screen = getattr(context, "screen", None)
    if screen is not None:
        return next((candidate for candidate in screen.areas if editor.is_area(candidate)), None)
    return None


def ui_state(context):
    """The DockState carrying the selection kind for this context (shared fallback without areas)."""
    from .dock import state
    area = painter_area(context)
    if area is None:
        return state.states.setdefault(0, state.DockState())
    return state.get(area)


def selected_mask(layer):
    if layer is None or not layer.masks:
        return None
    return layer.masks[max(0, min(layer.active_mask, len(layer.masks) - 1))]


def selected_effect(prop):
    if prop is None or not prop.effects:
        return None
    return prop.effects[max(0, min(prop.active_effect, len(prop.effects) - 1))]


def ancestors(ts, layer):
    """Folders above ``layer``, outermost first."""
    by_id = {item.id: item for item in ts.layers}
    chain, cursor, seen = [], layer.parent_id if layer else "", set()
    while cursor and cursor in by_id and cursor not in seen:
        seen.add(cursor)
        chain.append(by_id[cursor])
        cursor = by_id[cursor].parent_id
    return list(reversed(chain))


def kind_of(layer, ui):
    if layer is None:
        return "SET"
    if ui.selection_kind == MASK and layer.masks:
        return "MASK"
    if ui.selection_kind == FILTER and layer.type != "FOLDER":
        return "FILTER"
    if layer.type == "FOLDER":
        return "MATERIAL" if is_material(layer) else "FOLDER"
    return layer.type


def paint_destination(context, ts, layer, kind):
    """The brush destination the target implies; explains itself when there is none."""
    from ..painting.paint_session import paintable
    project = context.scene.open_painter
    result = PaintDestination(set_id=ts.id if ts else "")
    if ts is None or layer is None:
        result.reason = "Select a layer"
        return result
    if kind == "MASK":
        mask = selected_mask(layer)
        if mask is not None and mask.source == "PAINT" and paintable(ts, layer, True):
            result.kind, result.layer_id, result.mask_id, result.image = "MASK", layer.id, mask.id, mask.image
        elif mask is not None and mask.source != "PAINT":
            result.reason = f"{mask.name} is procedural: Add Paint to Mask"
        else:
            result.reason = "Hidden layers cannot be painted"
    elif layer.type == "PAINT":
        if paintable(ts, layer, False):
            prop = channel_for(layer, project.paint_channel)
            result.kind, result.layer_id, result.channel = "LAYER", layer.id, project.paint_channel
            result.image = prop.image if prop is not None else None
        else:
            result.reason = "Hidden layers cannot be painted"
    elif layer.type == "FILL":
        result.reason = "Fill layers hold values, not strokes; paint on a mask or a paint layer"
    else:
        result.reason = "Select a paint layer or a mask inside the folder"
    if result.kind != "NONE" and project.paint_image is not None:
        result.live = (project.paint_set_id, project.paint_layer_id, project.paint_mask_id) == (
            ts.id, layer.id, result.mask_id)
    return result


def resolve(context, ts=None):
    """The complete editing target of the Painter session this context belongs to."""
    project = context.scene.open_painter
    ui = ui_state(context)
    ts = ts if ts is not None else active_set(context)
    target = Target(display_channel=project.display_channel, blend_channel=project.blend_channel)
    if ts is None:
        target.path = [("No texture set", "TEXTURE")]
        return target
    target.ts, target.texture_set_id = ts, ts.id
    layer = active(ts)
    # A mask flag on a layer without masks resolves to the content (a valid fallback);
    # the flag itself stays: it is the intent that the next shelf asset becomes a mask.
    kind = kind_of(layer, ui)
    target.kind = kind
    target.path = [(ts.name, "TEXTURE")]
    if layer is None:
        return target
    target.layer, target.layer_id = layer, layer.id
    for folder in ancestors(ts, layer):
        target.path.append((folder.name, "MATERIAL" if is_material(folder) else "FILE_FOLDER"))
    target.path.append((layer.name, KIND_ICONS["MATERIAL" if is_material(layer) else layer.type]))
    if layer.type != "FOLDER":
        name = project.paint_channel if layer.type == "PAINT" else project.properties_channel
        target.channel = channel_for(layer, name)
        target.channel_name = name
    if kind == "MASK":
        mask = selected_mask(layer)
        target.mask, target.mask_id = mask, mask.id
        target.path.append((mask.name or mask.source.title(), "MOD_MASK"))
    elif kind == "FILTER":
        effect = selected_effect(target.channel)
        if effect is None:
            ui.selection_kind = CONTENT
            target.kind = kind_of(layer, ui)
        else:
            target.effect, target.effect_id = effect, effect.id
            target.path.append((effect.name or effect.kind.replace("_", " ").title(), "MODIFIER"))
    target.paint = paint_destination(context, ts, layer, target.kind)
    return target


def redraw(context):
    screen = getattr(context, "screen", None)
    if screen is None:
        return
    for area in screen.areas:
        if area.type in {"VIEW_3D", "IMAGE_EDITOR", "PROPERTIES", "NODE_EDITOR"}:
            area.tag_redraw()


def select(context, layer_id, kind=CONTENT, *, mask_id=None, channel=None, effect_id=None,
           ts=None, synchronize=True):
    """Make ``layer_id`` (and optionally one of its masks or channel filters) the target.

    Ids, not indices: a reordered stack, a deleted sibling or an undone insertion cannot
    redirect the selection. Returns the resolved target. Raises when the layer is gone.
    """
    ts = ts if ts is not None else active_set(context)
    if ts is None:
        raise ValueError("Create a texture set first.")
    index = next((i for i, item in enumerate(ts.layers) if item.id == layer_id), None)
    if index is None:
        raise ValueError("That layer no longer exists.")
    project = context.scene.open_painter
    ui = ui_state(context)
    if ts.active_layer != index:
        ts.active_layer = index
    layer = ts.layers[index]
    if mask_id:
        position = next((i for i, item in enumerate(layer.masks) if item.id == mask_id), None)
        if position is None:
            raise ValueError("That mask no longer exists.")
        layer.active_mask = position
        kind = MASK
    if channel and layer.type != "FOLDER":
        prop = channel_for(layer, channel)
        if prop is not None:
            if layer.type == "PAINT":
                project.paint_channel = channel
            else:
                project.properties_channel = channel
            if effect_id:
                slot = next((i for i, item in enumerate(prop.effects) if item.id == effect_id), None)
                if slot is None:
                    raise ValueError("That filter no longer exists.")
                prop.active_effect = slot
                kind = FILTER
    if kind == MASK and not layer.masks:
        kind = CONTENT
    # A target chosen from outside the list (a component row, a node, the shelf's target)
    # must be visible in the list: its folders open so the row can show (0.12.2).
    for folder in ancestors(ts, layer):
        if not folder.expanded:
            folder.expanded = True
    ui.selection_kind = kind
    ui.selected_layers = {layer_id}
    ui.properties_scroll = 0
    # One session, one selection: every Painter area of this screen (3D and 2D side by
    # side, a companion inspector) shows the same target.
    from .dock import state
    for area in _session_areas(context):
        if area.as_pointer() in state.states:
            other = state.states[area.as_pointer()]
            other.selection_kind, other.selected_layers = kind, {layer_id}
    if synchronize:
        from ..painting.paint_session import synchronize as follow
        follow(context, mask=kind == MASK)
    redraw(context)
    return resolve(context, ts)


def _session_areas(context):
    """Painter areas of this screen: one session shares one selection kind."""
    from . import editor
    screen = getattr(context, "screen", None)
    return [area for area in screen.areas if editor.is_area(area)] if screen is not None else []


def select_content(context, layer_id, **kw):
    return select(context, layer_id, CONTENT, **kw)


def select_mask(context, layer_id, mask_id=None, **kw):
    return select(context, layer_id, MASK, mask_id=mask_id, **kw)


def clear_stale(context, ts=None):
    """After deletion or undo: drop selection flags that point at nothing (a valid fallback)."""
    ts = ts if ts is not None else active_set(context)
    ui = ui_state(context)
    layer = active(ts) if ts is not None else None
    existing = {item.id for item in ts.layers} if ts is not None else set()
    ui.selected_layers = {identifier for identifier in ui.selected_layers if identifier in existing} \
        or ({layer.id} if layer is not None else set())
    if layer is None or not layer.masks:
        if ui.selection_kind == MASK:
            ui.selection_kind = CONTENT
    if layer is not None and layer.masks:
        layer.active_mask = max(0, min(layer.active_mask, len(layer.masks) - 1))


def breadcrumb_text(target, separator=" › "):
    return separator.join(label for label, _ in target.path)


def draw_breadcrumb(layout, target, *, compact=False):
    """The contextual path: Body › Worn Painted Metal › Blue Paint › Chips Mask."""
    if not target.path:
        return
    if compact or len(target.path) > 4:
        # Narrow sidebars: the set and the item, ancestors in the tooltip-friendly label.
        head = target.path[0][0]
        tail = target.path[-1]
        row = layout.row(align=True)
        row.label(text=head, icon=target.path[0][1])
        if len(target.path) > 1:
            row.label(text="›")
            row.label(text=tail[0], icon=tail[1])
        return
    row = layout.row(align=True)
    for index, (label, icon) in enumerate(target.path):
        if index:
            row.label(text="›")
        row.label(text=label, icon=icon)
