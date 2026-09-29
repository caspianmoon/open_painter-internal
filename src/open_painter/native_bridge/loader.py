"""Load an explicit development artifact or a hash-verified bundled library.

Never search PATH, the working directory, or system library directories.
An explicit missing path is an error, not a request to load a different build.
The C library is kept loaded; no manual dlclose/FreeLibrary is used.
"""
import ctypes
import hashlib
import json
import os
import platform
from pathlib import Path

from .core import OP_ABI_VERSION

_EXTENSION = {"Darwin": ".dylib", "Windows": ".dll"}.get(platform.system(), ".so")
_FILENAMES = ("libopenpainter" + _EXTENSION, "openpainter" + _EXTENSION)
BUNDLE = Path(__file__).resolve().parent / "bin"


def find_library(explicit=None):
    """Resolve an explicit developer path, otherwise a platform bundle only."""
    requested = explicit if explicit is not None else os.environ.get("OP_LIBOPENPAINTER_PATH")
    if requested is not None:
        path = Path(requested).expanduser().resolve()
        return path if path.is_file() else None
    directory = BUNDLE / f"{platform.system().lower()}-{platform.machine().lower()}"
    for name in _FILENAMES:
        path = directory / name
        if path.is_file():
            return path.resolve()
    return None


def load_library(path=None, *, expected_sha256=None):
    """Verify bundle identity before loading, then verify the C ABI before binding."""
    from . import core
    explicit = path is not None or "OP_LIBOPENPAINTER_PATH" in os.environ
    found = find_library(path)
    if found is None:
        raise RuntimeError("Native library unavailable; the existing painting engine remains active")
    if not explicit:
        manifest = json.loads(found.with_suffix(found.suffix + '.json').read_text(encoding='utf-8'))
        if manifest.get('abi_version') != OP_ABI_VERSION:
            raise RuntimeError("Bundled native manifest ABI mismatch")
        expected_sha256 = manifest['sha256']
    digest = hashlib.sha256(found.read_bytes()).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise RuntimeError("Native library SHA-256 mismatch")
    # Absolute path; Windows only searches the DLL directory and system32.
    lib = ctypes.CDLL(str(found), **({'winmode': 0x00000100 | 0x00000800}
                                   if os.name == 'nt' else {}))
    lib.op_get_abi_version.argtypes = []
    lib.op_get_abi_version.restype = ctypes.c_uint32
    version = lib.op_get_abi_version()
    if version != OP_ABI_VERSION:
        raise RuntimeError(f"Native ABI {version}; adapter requires {OP_ABI_VERSION}")
    core.bind(lib)
    binding = found.parent / ('_native.pyd' if os.name == 'nt' else '_native.abi3.so')
    if binding.is_file():
        import sys
        if sys.version_info < (3, 11):
            raise RuntimeError("The native binding requires CPython 3.11 or newer")
        if not explicit:
            expected_binding = manifest.get('binding_sha256')
            if expected_binding is None or hashlib.sha256(binding.read_bytes()).hexdigest() != expected_binding:
                raise RuntimeError("Bundled Python binding SHA-256 mismatch")
        import importlib.util
        identity = hashlib.sha256(binding.read_bytes()).hexdigest()
        name = __package__ + '.binary_' + identity + '._native'
        module = sys.modules.get(name)
        if module is None:
            spec = importlib.util.spec_from_file_location(name, binding)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            sys.modules[name] = module
        if module.abi() != OP_ABI_VERSION:
            raise RuntimeError("Python binding ABI mismatch")
        lib._op_binding = module
    elif not explicit:
        raise RuntimeError("The native package is missing its compiled Python binding")
    return lib, found
