"""Blender BLF text rasterization, shared by CPU export and GPU mask sources."""
import json
import os

import numpy as np

_snapshots = {}


def clear():
    # Never remove an image that a live or undo-restorable text source owns.
    import bpy
    for saved in _snapshots.values():
        image = saved.image
        try:
            if image is not None and image.users == 0:
                bpy.data.images.remove(image)
        except ReferenceError:
            pass
    _snapshots.clear()


def signature(settings):
    return (settings.content, settings.font, settings.size, settings.alignment,
            settings.spacing, settings.line_spacing, tuple(settings.offset))


def ensure(settings, n):
    if getattr(settings, 'schema_version', 1) != 1:
        raise ValueError('Unsupported text source version')
    import blf
    import bpy
    import imbuf
    if hasattr(settings, 'replaced'):
        # Export jobs capture immutable property snapshots. Reuse their live
        # source only while it still has the captured settings.
        owner = settings.id_data
        from ..core.layer_store import ATTRIBUTE
        if isinstance(owner, bpy.types.Palette):   # a mask's text, in its set's layer store
            candidates = [m.text for l in getattr(owner, ATTRIBUTE).layers for m in l.masks]
        elif hasattr(owner, 'open_painter'):
            candidates = [m.text for t in owner.open_painter.texture_sets for l in t.layers for m in l.masks]
        else:
            candidates = [node.text for node in owner.nodes if node.bl_idname == 'OPMGText']
        live = next((item for item in candidates if item.as_pointer() == settings.as_pointer() and
                     signature(item) == signature(settings)), None)
        if live is not None:
            return ensure(live, n)
        from types import SimpleNamespace
        key = (signature(settings), n)
        saved = _snapshots.get(key)
        if saved is None:
            if len(_snapshots) >= 8:
                clear()
            names = ('content', 'font', 'size', 'alignment', 'spacing', 'line_spacing', 'offset', 'image', 'raster_signature')
            saved = _snapshots[key] = SimpleNamespace(**{name: getattr(settings, name) for name in names})
            # A stale snapshot must never overwrite the live source's saved mask.
            if saved.image is not None and not (saved.font and not os.path.isfile(bpy.path.abspath(saved.font))):
                saved.image = saved.image.copy()
        return ensure(saved, n)
    path = bpy.path.abspath(settings.font) if settings.font else ''
    if path and not os.path.isfile(path):
        if settings.image is not None:
            return settings.image
        raise ValueError('The text font is missing and this source has no saved mask')
    stamp = json.dumps((signature(settings), n, os.stat(path).st_mtime_ns if path else None))
    if settings.image is not None and settings.raster_signature == stamp:
        return settings.image
    font = blf.load(path) if path else 0
    if font < 0:
        if settings.image is not None:
            return settings.image
        raise ValueError('Blender could not load the text font')
    buffer = imbuf.new((n, n), buffer_type='FLOAT')
    try:
        blf.size(font, max(1., settings.size * n))
        blf.color(font, 1., 1., 1., 1.)
        lines = settings.content.replace('\\n', '\n').split('\n')
        advance = settings.size * n * settings.line_spacing
        baseline = settings.offset[1] * n + (len(lines) - 1) * advance * .5
        with blf.bind_imbuf(font, buffer):
            for index, line in enumerate(lines):
                widths = [blf.dimensions(font, ch)[0] for ch in line]
                gap = settings.spacing * settings.size * n
                width = sum(widths) + max(0, len(line) - 1) * gap
                factor = {'LEFT': 0., 'CENTER': .5, 'RIGHT': 1.}[settings.alignment]
                x = settings.offset[0] * n - width * factor
                y = baseline - index * advance
                for char, step in zip(line, widths):
                    blf.position(font, x, y, 0)
                    blf.draw_buffer(font, char)
                    x += step + gap
        with buffer.with_buffer() as view:
            data = np.array(view, dtype=np.float32, copy=True)
        # Coverage is a scalar field, including transparent background. Output
        # alpha stays opaque so all existing grayscale source paths agree.
        data[..., :3] = data[..., 3:4]
        data[..., 3] = 1
        image = settings.image
        if image is None or image.users > 1:
            image = bpy.data.images.new('OP Text Mask', width=n, height=n, alpha=True, float_buffer=True)
            image['open_painter_owner'] = True
            settings.image = image
        elif tuple(image.size) != (n, n):
            image.scale(n, n)
        image.colorspace_settings.name = 'Non-Color'
        image.pixels.foreach_set(data.ravel())
        image.update()
        image.pack()
        settings.raster_signature = stamp
        from .dependencies import image_written
        image_written(image)
        return image
    finally:
        buffer.free()
        if path:
            blf.unload(path)


def draw(layout, settings):
    for name in ('content', 'font', 'size', 'alignment', 'spacing', 'line_spacing', 'offset'):
        layout.prop(settings, name)
