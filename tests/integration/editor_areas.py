"""Painter area bookkeeping follows Blender's own area lifecycle.

Re-picking the editor type from the 2D backend keeps the area mounted, and
areas Blender frees behind the add-on's back (Close Area, Join) are forgotten
before a Split can reuse their addresses.
"""

import sys
from pathlib import Path

import bpy

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "src"))
import open_painter
from open_painter.ui import editor
from open_painter.ui.dock import state

open_painter.register()


def records(screen):
    return [(r["index"], r["backend"]) for r in screen.get("open_painter_areas", [])]


def run():
    window = bpy.context.window
    screen = window.screen
    area = next(a for a in screen.areas if a.type == "VIEW_3D")
    key = area.as_pointer()
    index = list(screen.areas).index(area)

    # Choosing 'Open Painter' from the editor-type dropdown while the area
    # shows the 2D backend: the same scan that mounts it must keep it.
    editor.mount(window, area)
    editor.set_view(area, "IMAGE_EDITOR")
    area.type = "IMAGE_EDITOR"
    editor.scan(window)
    assert editor.is_area(area) and editor._active[key] == "IMAGE_EDITOR"
    area.ui_type = editor.EDITOR_ID
    assert area.type == "NODE_EDITOR"
    editor.scan(window)
    assert area.type == "VIEW_3D", area.type
    assert editor.is_area(area), "re-picking the editor type dropped the area"
    assert editor._active[key] == "VIEW_3D" and editor._previous[key] == "VIEW_3D"
    assert key in editor._spaces and (index, "VIEW_3D") in records(screen)
    editor.scan(window)
    assert editor.is_area(area), "the mounted area must survive a further scan"

    # Blender's Close Area frees the ScrArea and its Space without close():
    # the tick after it must forget the entry, so a later Split landing a
    # plain viewport on the freed addresses has nothing to inherit.
    other = next(a for a in screen.areas if a.type not in {"VIEW_3D", "IMAGE_EDITOR"})
    other.type = "VIEW_3D"
    dead = max(a.as_pointer() for s in bpy.data.screens for a in s.areas) + 4096
    editor._active[dead] = "VIEW_3D"
    editor._previous[dead] = "VIEW_3D"
    editor._spaces[dead] = dead + 64
    state.states[dead] = state.DockState(tab="LAYERS")
    editor.scan(window)
    assert dead not in editor._active and dead not in editor._previous
    assert dead not in editor._spaces and dead not in state.states
    assert not editor.is_area(other), "a freed entry was adopted by a plain viewport"
    assert other.as_pointer() not in state.states
    assert editor.is_area(area) and (index, "VIEW_3D") in records(screen)
    other_index = list(screen.areas).index(other)
    assert (other_index, "VIEW_3D") not in records(screen)

    # Entries of areas in screens no window shows (other workspaces) stay.
    hidden = next(s for s in bpy.data.screens if s != screen and any(a.type == "VIEW_3D" for a in s.areas))
    hidden_area = next(a for a in hidden.areas if a.type == "VIEW_3D")
    editor._active[hidden_area.as_pointer()] = "VIEW_3D"
    editor._previous[hidden_area.as_pointer()] = "VIEW_3D"
    editor._spaces[hidden_area.as_pointer()] = hidden_area.spaces.active.as_pointer()
    editor.scan(window)
    assert editor.is_area(hidden_area), "a Painter area in another workspace was forgotten"
    editor.save(None)
    assert records(hidden) == [(list(hidden.areas).index(hidden_area), "VIEW_3D")], records(hidden)

    # Un-maximizing frees the temp area and hands the Space back to the
    # original area in one operator: the follow still wins over pruning.
    ui = state.get(area)
    ui.tab = "LAYERS"
    temp = dead + 4096
    editor._active[temp] = editor._active.pop(key)
    editor._previous[temp] = editor._previous.pop(key)
    editor._spaces[temp] = editor._spaces.pop(key)
    state.states[temp] = state.states.pop(key)
    editor.scan(window)
    assert editor.is_area(area) and temp not in editor._active, editor._active
    assert state.get(area).tab == "LAYERS" and temp not in state.states
    assert editor._spaces[key] == area.spaces.active.as_pointer()

    open_painter.unregister()
    print("EDITOR_AREAS_PASS: re-picking the editor keeps the area; freed areas are pruned before a split can inherit them; other workspaces and un-maximize keep theirs", flush=True)


run()
