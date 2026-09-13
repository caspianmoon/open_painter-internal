"""Texture-space primitives. No Blender runtime or shader graph is required.

Procedurals accept global coordinates, so callers may evaluate arbitrary tiles.
Neighborhood filters accept a complete image or a caller-supplied halo; they never
wrap one edge of a tile onto the opposite edge.
"""

from . import filters, procedurals

__all__ = ("filters", "procedurals")
