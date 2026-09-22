"""Page the ordinary companion inspector for evidence without changing the viewport.

The caller owns layer/mask selection and opens the existing Properties inspector.
This module only uses normal View2D scroll/pan and screenshot operators. It never
changes material values, panel implementations, camera framing or area dimensions.
"""
from pathlib import Path

import bpy

from capture_evidence import safe_name, sha256


def _bounds(region):
    low = region.view2d.region_to_view(0, 0)
    high = region.view2d.region_to_view(region.width, region.height)
    return [float(low[0]), float(low[1]), float(high[0]), float(high[1])]


def _same(a, b, tolerance=.05):
    return max(abs(x - y) for x, y in zip(a, b)) <= tolerance


def capture_inspector_pages(context, painter_area, output_dir, stem, *, max_pages=6):
    """Return actual screenshots, bounded page coverage and scroll-restoration evidence."""
    from open_painter.ui import companion
    inspector = companion.companion_for(context.screen)
    if inspector is None or inspector.spaces.active.context != "TOOL":
        return dict(status="FAILED", views=[], errors=["Open the normal existing Properties inspector before UI capture"])
    region = next(r for r in inspector.regions if r.type == "WINDOW")
    window = context.window
    output = Path(output_dir)
    (output / "ui").mkdir(parents=True, exist_ok=True)
    original = _bounds(region)
    size = [painter_area.width, painter_area.height]
    views, errors, restoration = [], [], {}
    coverage = "NOT_TESTED"

    def redraw():
        inspector.tag_redraw()
        painter_area.tag_redraw()
        with bpy.context.temp_override(window=window, area=inspector, region=region):
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)

    def scroll(direction):
        old = _bounds(region)
        with bpy.context.temp_override(window=window, area=inspector, region=region):
            getattr(bpy.ops.view2d, "scroll_" + direction)(page=True)
        redraw()
        return not _same(old, _bounds(region))

    try:
        redraw()
        for _ in range(32):
            if not scroll("up"):
                break
        else:
            raise RuntimeError("Inspector top was not reached within the bounded scroll limit")
        for page in range(max(1, int(max_pages))):
            relative = Path("ui") / (safe_name(stem) + f"-page-{page:02d}.png")
            with bpy.context.temp_override(window=window, area=inspector, region=region):
                bpy.ops.screen.screenshot(filepath=str(output / relative))
            views.append(dict(path=relative.as_posix(), sha256=sha256(output / relative),
                              page=page, scroll_bounds=_bounds(region), inspector="NATIVE_PROPERTIES_TOOL"))
            if not scroll("down"):
                coverage = "TOP_TO_BOTTOM"
                break
        else:
            coverage = "TRUNCATED_AT_DECLARED_PAGE_CAP"
            errors.append(f"Inspector exceeds the declared {max_pages}-page evidence cap")
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        # Native pan uses pixel deltas. Determine its direction from a measured
        # first move, then converge rather than assume a Blender-specific sign.
        try:
            direction = 1
            for _ in range(5):
                current = _bounds(region)
                if _same(original, current):
                    break
                scale = (current[3] - current[1]) / max(1, region.height)
                delta = int(round((original[1] - current[1]) / scale))
                if not delta:
                    break
                with bpy.context.temp_override(window=window, area=inspector, region=region):
                    bpy.ops.view2d.pan(deltax=0, deltay=direction * delta)
                redraw()
                moved = _bounds(region)[1] - current[1]
                if moved * (original[1] - current[1]) < 0:
                    direction *= -1
            restored = _bounds(region)
            restoration = dict(status="RESTORED" if _same(original, restored) else "PARTIAL_UI_ONLY",
                               before=original, after=restored,
                               limitation=None if _same(original, restored) else
                               "Native View2D clamps or rounds pixel scrolling; only transient Properties scroll differs")
        except Exception as exc:
            restoration = dict(status="NOT_RESTORED_UI_ONLY", before=original, after=_bounds(region),
                               limitation=f"Native Properties scroll restoration unavailable: {type(exc).__name__}: {exc}")
    unchanged_size = size == [painter_area.width, painter_area.height]
    if not unchanged_size:
        errors.append("UI capture changed Painter viewport dimensions")
    return dict(status="FAILED" if errors else "CAPTURED", views=views, errors=errors,
                coverage=coverage, page_cap=max_pages, viewport_not_resized=unchanged_size,
                scroll_restoration=restoration)
