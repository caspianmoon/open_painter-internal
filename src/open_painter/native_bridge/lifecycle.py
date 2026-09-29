"""Native host-Metal scheduling and generation teardown on Blender's thread.

Project recipes and GPU resources retain their existing host owners. ctypes
never manually unloads a library while an adapter may still reference it.
"""
import os
import weakref

from .capabilities import select_backend
from .core import NativeCore
from .loader import find_library, load_library

_core = None
_report = select_backend()
_handlers = []
_graphs = weakref.WeakSet()
_library = None
_draw_handle = None


def diagnostics():
    return dict(_report)


def close(*_args):
    global _core
    for graph in list(_graphs):
        graph.close()
    _graphs.clear()
    if _core is not None:
        _core.close()
        _core = None


def _restore(*_args):
    # Destroying contexts and plans is a generation barrier.
    # No old document or queued callback is carried into restored recipe data.
    close()
    _report.update(native_loaded=_library is not None, native_has_gpu=False)


def register():
    global _core, _report, _library, _draw_handle
    import bpy
    from bpy.app.handlers import persistent
    unregister()
    _report = select_backend()
    _library = None
    try:
        if find_library() is not None:
            lib, path = load_library()
            _library = lib
            _core = NativeCore(lib)
            caps = _core.capabilities()
            _report = select_backend(native_loaded=True, has_gpu=caps.has_gpu)
            _report['library'] = str(path)
    except (OSError, RuntimeError, ValueError, KeyError, AttributeError) as exc:
        close()
        _library = None
        _report['load_error'] = str(exc)
    callback = persistent(_restore)
    for name in ('load_pre', 'undo_pre', 'redo_pre'):
        handlers = getattr(bpy.app.handlers, name)
        handlers.append(callback)
        _handlers.append((handlers, callback))
    if _library is not None and not bpy.app.background:
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(_before_view, (), 'WINDOW', 'PRE_VIEW')


def unregister():
    global _library, _report, _draw_handle
    if _draw_handle is not None:
        import bpy
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        _draw_handle = None
    close()
    _library = None
    while _handlers:
        handlers, callback = _handlers.pop()
        if callback in handlers:
            handlers.remove(callback)
    _report = select_backend()


def track(graph):
    _graphs.add(graph)


def active_library():
    """Already selected context's library, without importing Blender or creating a device."""
    return _library if (_report.get('selected') == 'c17-host-metal'
                        and os.environ.get('OP_NATIVE_ENGINE', '1') != '0') else None


def execution_library(backend):
    """The public host Metal executor; other platforms retain their current path."""
    if backend != 'METAL' or os.environ.get('OP_NATIVE_ENGINE', '1') == '0':
        _report.update(selected='legacy', host_backend=backend,
                       reason='Host native scheduling is disabled or unavailable for this backend')
        return None
    if _library is None or not hasattr(_library, 'op_graph_evaluate'):
        return None
    _report.update(selected='c17-host-metal', host_backend=backend,
                   host_gpu=True, fully_migrated=False,
                   compiled_binding=bool(getattr(_library, '_op_binding', None)),
                   gpu_device_owner='Blender',
                   gpu_submission=('compiled-public-host-api' if getattr(_library, '_op_binding', None) else 'python-development-adapter'),
                   reason='C17 shader, paint, geometry, cache and revision core with public host Metal execution')
    return _library


def restore_presentations(scene_uid=None):
    if active_library() is None:
        return
    for item in tuple(_graphs):
        if hasattr(item, 'restore_evicted') and (scene_uid is None or item.scene_uid == scene_uid):
            item.restore_evicted()


def _before_view():
    import bpy
    try:
        restore_presentations(bpy.context.scene.session_uid)
    except Exception as error:
        # A lost graphics context cannot publish a falsely current revision.
        _report['presentation_error'] = str(error)
        from ..utils.logging import logger
        logger.error('Native viewport texture recovery failed', exc_info=True)
