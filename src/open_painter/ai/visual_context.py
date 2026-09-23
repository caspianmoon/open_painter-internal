"""Labeled, bounded reference and mesh-map images for a single Codex instruction."""

import base64
import hashlib
from pathlib import Path

from .viewport import png

MAPS = (
    ("AO", "Ambient occlusion: white is open, dark is occluded; not base color or proof of water collection."),
    ("CURVATURE", "Convex edge exposure: white is a stronger convex crease or smooth bend, black is flat. This is NOT signed curvature and does not identify cavities."),
    ("NORMAL", "Tangent-space normal map: RGB encodes direction, not material color."),
    ("WORLD_NORMAL", "World normal: XYZ encoded as RGB = normal * 0.5 + 0.5; blue encodes world Z."),
    ("POSITION", "World position normalized to the assigned surface bounds: R=X, G=Y, B=Z."),
    ("THICKNESS", "Normalized inward thickness; alpha zero means missing opposing surface or outside UVs."),
)
MAX_REFERENCE_BYTES = 32 * 1024 * 1024
MAX_SOURCE_PIXELS = 64 * 1024 * 1024
MAX_ATTACHMENT_BYTES = 120000


def geometry_map_problems(texture_sets):
    """Shared by the native preparation panel and the sending boundary."""
    from ..geometry.provenance import status
    problems = []
    for ts in texture_sets:
        missing = []
        for kind, _ in MAPS:
            entry = next((m for m in ts.baked_maps if m.kind == kind and m.image), None)
            state = status(ts, entry) if entry else "Missing"
            if state != "Ready" or not entry.image.has_data or min(entry.image.size) < 1:
                missing.append(kind + " (" + state + ")")
        if missing:
            problems.append(ts.name + ": " + ", ".join(missing))
    return problems


def require_geometry_maps(texture_sets, *, send_maps, send_view):
    """Authoring must not silently proceed with absent or stale geometry evidence."""
    from ..authoring.schema import AuthoringError
    if not send_maps or not send_view:
        raise AuthoringError("Enable Send Mesh Maps and Send Current 3D View before texturing with Material Director.")
    problems = geometry_map_problems(texture_sets)
    if problems:
        raise AuthoringError("Create current geometry maps before texturing. Open Bake Mesh Maps and bake/rebake AO, Curvature, Normal, World Normal, Position and Thickness. "
                             + "; ".join(problems) + ". No instruction was sent.")
MAX_REFERENCE_ENCODED = 1600000
MAX_ATTACHMENTS = 12  # Reference, whole/isolated views, three extra views, six maps.


def thumbnail(image, *, side=384, budget=MAX_ATTACHMENT_BYTES):
    """Read pixels without copying/scaling Blender datablocks or altering source data."""
    import numpy as np
    width, height = image.size
    if not width or not height:
        raise ValueError("The selected image has no readable pixels")
    if width * height > MAX_SOURCE_PIXELS:
        raise ValueError("Image previews support sources of at most 64 megapixels")
    scale = min(1., side / max(width, height))
    w, h = max(1, round(width * scale)), max(1, round(height * scale))
    # One bounded float copy (1 GiB at 64 MP). Slicing image.pixels per row is
    # not cheaper: every slice converts the whole buffer, so a 4K map took seconds.
    original = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(original)
    original = original.reshape(height, width, 4)
    while True:
        x = (np.arange(w, dtype=np.float32) + .5) * width / w - .5
        y = (np.arange(h, dtype=np.float32) + .5) * height / h - .5
        x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
        fx, fy = (x - x0)[:, None], (y - y0)[:, None, None]
        x1, y1 = np.minimum(x0 + 1, width - 1), np.minimum(y0 + 1, height - 1)
        rgba = np.empty((h, w, 4), dtype=np.float32)
        for i in range(h):
            low, high = original[y0[i]], original[y1[i]]
            lo = low[x0] * (1 - fx) + low[x1] * fx
            hi = high[x0] * (1 - fx) + high[x1] * fx
            rgba[i] = lo * (1 - fy[i]) + hi * fy[i]
        rgba = np.nan_to_num(rgba, nan=0., posinf=1., neginf=0.)
        data = np.round(np.clip(rgba, 0, 1) * 255).astype(np.uint8)[::-1].tobytes()
        url = "data:image/png;base64," + base64.b64encode(png(w, h, data)).decode()
        if len(url) <= budget:
            return dict(url=url, width=w, height=h, sha256=hashlib.sha256(data).hexdigest())
        if min(w, h) < 64:
            raise ValueError("Image cannot fit within the visual-context budget")
        w, h = max(1, round(w * .75)), max(1, round(h * .75))


def reference(path):
    import bpy
    source = Path(bpy.path.abspath(path)).expanduser()
    if not source.is_file() or source.stat().st_size > MAX_REFERENCE_BYTES:
        raise ValueError("Choose a reference image file smaller than 32 MiB")
    if source.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tga"}:
        raise ValueError("Reference images support PNG, JPEG, WebP, BMP and TGA")
    image = bpy.data.images.load(str(source), check_existing=False)
    try:
        if image.size[0] * image.size[1] > 32 * 1024 * 1024:
            raise ValueError("Choose a reference image with at most 32 megapixels")
        return dict(id="reference", role="reference", label="Artist reference — visual appearance target",
                    meaning="Match material identity, palette, wear character and detail scale. This is not the current mesh, a UV layout, or a baked mesh map.",
                    **thumbnail(image, side=768, budget=MAX_REFERENCE_ENCODED))
    finally:
        bpy.data.images.remove(image)


def mesh_maps(ts):
    from ..geometry.provenance import status
    result, missing = [], []
    for kind, meaning in MAPS:
        entry = next((m for m in ts.baked_maps if m.kind == kind and m.image), None)
        if entry is None:
            missing.append(kind)
            continue
        result.append(dict(id="map:" + kind, role="mesh_map", kind=kind, label=kind + " mesh map in UV space",
                           meaning=meaning, provenance_status=status(ts, entry),
                           source_size=list(entry.image.size), **thumbnail(entry.image, side=256)))
    return result, missing


def manifest(attachments):
    return [{k: v for k, v in item.items() if k != "url"} for item in attachments]
