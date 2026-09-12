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
    mask_selected: bool = False
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
    ui_scale: float = 1
    show_assets: bool = True
    show_properties: bool = True
    selected_layers: set = field(default_factory=set)
    layer_drag: object = None
    layer_drag_start: tuple = (0, 0)
    layer_drag_moved: bool = False


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
