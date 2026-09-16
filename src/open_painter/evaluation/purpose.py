"""Whether CPU evaluation currently serves the preview or the authoritative output.

Bypassed layers (0.9.0) exist only for the shader preview. The CPU evaluator
skips them when it works for the preview — boundary factories stepped by the
shader runtime and layer-cache jobs — and evaluates them for everything else:
export, bakes, published outputs and 2D composite snapshots.
"""
from contextlib import contextmanager

_depth = 0


@contextmanager
def preview():
    global _depth
    _depth += 1
    try:
        yield
    finally:
        _depth -= 1


def is_preview():
    return _depth > 0
