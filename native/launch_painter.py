"""Compatibility entry point; the native app initializes its own UI at startup."""
import bpy
if not hasattr(bpy.types.Material, 'painter_layers'):
    raise RuntimeError('Launch the compiled OpenPainter application with its bundled runtime.')
print('Open Painter is initialized by the native application.', flush=True)
