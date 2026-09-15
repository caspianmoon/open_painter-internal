"""Explicitly opted-in 3D-view context; no screen UI, file writes or image datablocks."""

import base64
import struct
import zlib


def png(width, height, rgba):
    def chunk(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data) & 0xffffffff)
    stride = width * 4
    rows = b"".join(b"\0" + rgba[y * stride:(y + 1) * stride] for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!2I5B", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def capture(context, ts, *, max_side=512, isolate=False, frame_target=False):
    import bpy
    import gpu
    import numpy as np
    from ..evaluation import shader_preview
    if bpy.app.background or context.area.type != "VIEW_3D":
        raise ValueError("Send the current 3D view from a visible Painter viewport")
    space = context.area.spaces.active
    region = next(r for r in context.area.regions if r.type == "WINDOW")
    if space.shading.type not in {"MATERIAL", "RENDERED"}:
        raise ValueError("Use Material Preview or Rendered shading before sending the 3D view")
    if ts.preview_backend == "LIVE" and not shader_preview.current(context.scene, ts):
        raise ValueError("Wait for the material preview to finish before sending the 3D view")
    from ..generators.scene_fields import signature
    for layer in ts.layers:
        if not layer.visible:
            continue
        for mask in layer.masks:
            if not mask.enabled or not (mask.source == "SCENE" or
                                       (mask.source == "GENERATOR" and mask.edge_source == "LIVE")):
                continue
            field = mask.influence
            if field.error and field.error != "Analysing…":
                raise ValueError(f"Fix scene analysis for {layer.name} > {mask.name}: {field.error}")
            if field.mode == "LIVE" and (not field.image or field.provenance != repr(signature(ts, field))):
                raise ValueError("Wait for scene analysis to finish before sending the 3D view")
    scale = max_side / max(region.width, region.height)
    width, height = max(1, round(region.width * scale)), max(1, round(region.height * scale))
    offscreen = gpu.types.GPUOffScreen(width, height, format="RGBA8")
    overlays = space.overlay.show_overlays
    view = space.region_3d
    old_location, old_distance = view.view_location.copy(), view.view_distance
    hidden = []
    try:
        if isolate:
            targets = {ref.object for ref in ts.assignments if ref.object}
            for obj in context.view_layer.objects:
                if obj.type == "MESH" and obj not in targets and not obj.hide_get():
                    hidden.append(obj)
                    obj.hide_set(True)
            context.view_layer.update()
            if frame_target:
                from mathutils import Vector
                points = [obj.matrix_world @ Vector(corner) for obj in targets for corner in obj.bound_box]
                center = sum(points, Vector()) / len(points)
                view.view_location = center
                view.view_distance = max(.001, max((p - center).length for p in points) * 3.2)
                view.update()
        space.overlay.show_overlays = False
        with context.temp_override(area=context.area, region=region):
            offscreen.draw_view3d(context.scene, context.view_layer, space, region,
                                 space.region_3d.view_matrix, space.region_3d.window_matrix,
                                 do_color_management=True, draw_background=True)
        with offscreen.bind():
            pixels = gpu.state.active_framebuffer_get().read_color(0, 0, width, height, 4, 0, "UBYTE")
            data = np.asarray(pixels, dtype=np.uint8).reshape(height, width, 4)[::-1].tobytes()
    finally:
        view.view_location, view.view_distance = old_location, old_distance
        view.update()
        for obj in hidden:
            obj.hide_set(False)
        if hidden:
            context.view_layer.update()
        space.overlay.show_overlays = overlays
        offscreen.free()
    return "data:image/png;base64," + base64.b64encode(png(width, height, data)).decode()
