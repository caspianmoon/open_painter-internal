"""Cycles bake device choice: a GPU when one is enabled, CPU otherwise.

Only the staging scene's device is chosen here. Device *preferences* (the
compute backend and the per-device toggles) belong to the artist and are read,
never written. Detection is conservative and exception-safe: a Blender without
the Cycles add-on, a profile with no enabled GPU device, or a driver that fails
the query all resolve to CPU, so a bake always has a device to run on.

The device list saved in the preferences answers the common case without
calling ``_cycles.available_devices()``, which Blender warns can crash on
unstable drivers. Enumeration happens at most once per backend per session, and
only for a profile whose list has never been filled.
"""
from ..utils.logging import logger

GPU_BACKENDS = {'CUDA', 'OPTIX', 'HIP', 'METAL', 'ONEAPI'}
AUTO = 'AUTO'
CPU = 'CPU'
SCENE = 'SCENE'
_PROBES = {}   # backend -> bool; the one path that has to enumerate devices

_SCENE_FALLBACK = ('The scene device is GPU but no enabled Cycles GPU device was found; the bake runs on CPU. '
                   'Enable one under Preferences > System > Cycles Render Devices, or set Bake Device to CPU.')


def _cycles_preferences():
    import bpy
    addon = bpy.context.preferences.addons.get('cycles')
    return addon.preferences if addon is not None else None


def _backend(preferences):
    return getattr(preferences, 'compute_device_type', '') or 'NONE'


def _probe(preferences, backend):
    """One enumeration, cached per backend, for a profile never configured.

    ``preferences.devices`` is filled by Blender's own device refresh and saved
    between sessions, so it is empty only before the profile has been set up.
    The probe is the call the preferences UI itself makes; it updates the saved
    list as a side effect, after which the fast path answers without probing.
    """
    if backend not in _PROBES:
        try:
            _PROBES[backend] = int(preferences.get_num_gpu_devices()) > 0
        except Exception as exc:
            logger.warning('Cycles GPU query failed (%s); baking on CPU', exc)
            _PROBES[backend] = False
    return _PROBES[backend]


def gpu_available(preferences=None):
    """Whether Cycles has an enabled GPU device for its configured backend.

    ``preferences`` is the test seam; by default the live Cycles preferences
    are read. Every failure answers False rather than raising, so the caller
    always has CPU to fall back on.
    """
    try:
        preferences = _cycles_preferences() if preferences is None else preferences
        if preferences is None:
            return False
        backend = _backend(preferences)
        if backend not in GPU_BACKENDS:
            return False
        entries = [device for device in preferences.devices if getattr(device, 'type', '') == backend]
        if entries:
            # The saved list decides; a disabled device stays disabled, and no
            # driver enumeration runs on the common configured machine.
            return any(bool(getattr(device, 'use', False)) for device in entries)
        return _probe(preferences, backend)
    except Exception as exc:
        logger.warning('Cycles GPU detection failed (%s); baking on CPU', exc)
        return False


def resolve(scene, preference):
    """(device, note) for one bake: the staging device and a fallback note.

    ``AUTO`` asks for a GPU whenever one is enabled; ``SCENE`` follows the
    artist's scene device; ``CPU`` always stays on the processor. A GPU request
    that cannot be met falls back to CPU instead of handing Cycles a device it
    would reject at bake time. Without a GPU, AUTO is doing exactly what it
    says, so it stays quiet; SCENE asked for a GPU through the scene and gets
    a note naming the fallback for the operator log. The note is None whenever
    the request was met or no GPU was asked for.
    """
    if preference == AUTO:
        wanted = 'GPU'
    elif preference == SCENE:
        wanted = getattr(getattr(scene, 'cycles', None), 'device', 'CPU') or 'CPU'
    else:
        wanted = 'CPU'
    if wanted != 'GPU':
        return CPU, None
    if gpu_available():
        return 'GPU', None
    if preference == AUTO:
        return CPU, None   # no enabled GPU is an expected Auto outcome, not a fallback to report
    return CPU, _SCENE_FALLBACK
