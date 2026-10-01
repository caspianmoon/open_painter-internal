"""Digests of large pixel and mesh buffers.

hashlib.sha256 measured about 85 MB/s in Blender 5.2's bundled Python on Apple silicon, so
one 2048² float map took 0.8 s and a Material Director candidate fingerprint of 26 maps
took 20 s. CPython's own SHA-256 gives identical digests about four times faster there,
and BLAKE2b is about eleven times faster for digests that never leave the process. With the
native engine active, libopenpainter's SHA-256 runs at about 1.7 GB/s (the arm64 SHA2
instructions), again with identical digests.
"""

import hashlib
import time

_sha256 = None


def _faster_sha256():
    """hashlib's (OpenSSL) or CPython's built-in SHA-256, whichever is faster on this build.

    Both produce identical digests; an OpenSSL with hardware SHA support stays the choice."""
    try:
        from _sha2 import sha256 as builtin
    except ImportError:
        return hashlib.sha256
    sample = bytes(1 << 22)
    timings = {}
    for candidate in (hashlib.sha256, builtin):
        start = time.perf_counter()
        candidate(sample).digest()
        timings[candidate] = time.perf_counter() - start
    return min(timings, key=timings.get)


def _native():
    """libopenpainter's SHA-256 (the arm64 SHA2 instructions) once the native engine is active."""
    try:
        from ..native_bridge.lifecycle import cpu_library
        lib = cpu_library()
    except Exception:
        return None
    return lib if lib is not None and hasattr(lib, 'op_sha256_update') else None


def sha256(data=b""):
    """A SHA-256 hash object, the same as hashlib.sha256(data), from the faster implementation.

    Short one-shot inputs stay in Python, where a native call costs more than the hash."""
    global _sha256
    size = memoryview(data).nbytes
    lib = _native() if size >= 1 << 16 or not size else None
    if lib is not None:
        from ..native_bridge.digest import Sha256
        return Sha256(lib, data)
    if _sha256 is None:
        _sha256 = _faster_sha256()
    return _sha256(data)


def fingerprint(data=b""):
    """A 256-bit BLAKE2b hash object for in-process comparison only: never persist or publish
    its value as SHA-256."""
    return hashlib.blake2b(data, digest_size=32)
