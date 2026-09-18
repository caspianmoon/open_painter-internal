"""Transient hit targets and UI selection; project content remains in scene RNA."""

from dataclasses import dataclass, field


@dataclass
class DockState:
    hits: list = field(default_factory=list)
    tab: str = "MATERIALS"
    query: str = ""
    search: bool = False
    layer_scroll: int = 0
    texture_scroll: int = 0
    asset_scroll: int = 0
    properties_scroll: int = 0
    properties_limit: float = 0
    # What the Painter session edits on the active layer: its content, its mask stack
    # or one of its channel filters (ui/selection.py resolves the complete target).
    selection_kind: str = "CONTENT"
    drag: object = None
    drag_original: float = 0
    drag_mouse: float = 0
    message: str = ""
    hover: object = None
    hover_since: float = 0
    mouse: tuple = (0, 0)
    width: int = 0
    height: int = 0
    left: int = 248
    right: int = 338
    bottom: float = 22
    asset_columns: int = 2
    ui_scale: float = 1
    show_assets: bool = True
    show_properties: bool = True
    selected_layers: set = field(default_factory=set)
    layer_drag: object = None
    layer_drag_start: tuple = (0, 0)
    layer_drag_moved: bool = False
    # Asset the shelf has selected but not applied yet (Phase 3): its catalog key.
    pending_asset: str = ""

    @property
    def mask_selected(self):
        return self.selection_kind == "MASK"

    @mask_selected.setter
    def mask_selected(self, value):
        self.selection_kind = "MASK" if value else "CONTENT"


states = {}
windows = set()


def get(area):
    return states.setdefault(area.as_pointer(), DockState())


def is_painter(context):
    from ..editor import is_area

    return is_area(context.area)


def reset():
    states.clear()
    windows.clear()
