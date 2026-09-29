"""Portable C ABI bindings and the Blender host-Metal scheduling adapter.

Importing this package does not import bpy. The graph/lifecycle adapter is
activated by add-on registration and evaluation on the context-owning thread.
"""

from .core import (
    OP_ABI_VERSION,
    Blend,
    Capabilities,
    Channel,
    FrameLease,
    Metrics,
    NativeCore,
    NativeError,
    Status,
)
from .loader import find_library, load_library

__all__ = [
    "OP_ABI_VERSION",
    "Blend",
    "Capabilities",
    "Channel",
    "FrameLease",
    "Metrics",
    "NativeCore",
    "NativeError",
    "Status",
    "find_library",
    "load_library",
]
