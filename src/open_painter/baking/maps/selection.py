"""Current mesh selection rasterized into a UV mask (Ucupaint's selection bake idea).

Selected faces write 1, everything else 0; alpha is assigned-UV coverage. The
selection is read from the base mesh (edit mode is flushed first) and applied
to evaluated polygons by index, so it needs a modifier stack that keeps the
face count. Nothing is written to the mesh; face indices are stored on the
image so the selection can be restored later.
"""
import numpy as np


def polygon_flags(target):
    """Return one 0/1 float per evaluated polygon, or raise when unmappable."""
    obj = target["object"]
    base = obj.data
    if obj.mode == "EDIT":
        obj.update_from_editmode()
    mesh = target["mesh"]
    if len(mesh.polygons) != len(base.polygons):
        raise ValueError(f"{obj.name}: modifiers change the face count, so the selection cannot be mapped. "
                         "Disable topology-changing modifiers or apply them to a copy.")
    # Face flags are authoritative: edit mode flushes vertex/edge selection to
    # faces itself, and the primitive operators leave stray vertex flags behind.
    flags = np.zeros(len(base.polygons), bool)
    base.polygons.foreach_get("select", flags)
    return flags.astype(np.float32)
