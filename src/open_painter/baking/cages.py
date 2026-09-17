"""Editable projection cages built from owned evaluated copies."""

import math
import bpy
import numpy as np
from mathutils import Matrix

from ..constants import OWNER
from .pairing import groups, reference_target
from .render_geometry import scope

CAGE_MARKER = "open_painter_bake_cage"


def topology_signature(mesh):
    """Cycles cage correspondence requires ordered polygon/vertex mapping.

    Polygon starts plus the loop vertex indices describe the ordered corner
    lists exactly; reading them in bulk keeps a render-subdivided target
    (millions of polygons) to a fraction of a second instead of a Python
    tuple per face.
    """
    starts = np.empty(len(mesh.polygons), dtype=np.int32)
    mesh.polygons.foreach_get("loop_start", starts)
    corners = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", corners)
    return (len(mesh.vertices), len(mesh.polygons), len(mesh.loops), starts.tobytes(), corners.tobytes())


def validate_topology(low, cage, depsgraph=None):
    """Reject correspondence mismatches using the meshes' render modifiers.

    The optional depsgraph argument remains accepted for existing callers;
    viewport-only geometry cannot establish render-time cage correspondence.
    """
    if low is None or cage is None or low.type != "MESH" or cage.type != "MESH":
        raise ValueError("Low-poly target and custom cage must both be meshes.")
    with scope(bpy.context, (low, cage)) as frozen:
        if topology_signature(frozen[low].data) != topology_signature(frozen[cage].data):
            raise ValueError(f"Custom cage '{cage.name}' topology must match the evaluated low-poly mesh '{low.name}'.")
    return True


def create(context, ts, offset):
    """Stage world-space cages, then publish pointers as one transaction.

    Normals are transformed by inverse transpose and normalized so the offset
    remains a world-space distance under nonuniform object scales. No source
    names, geometry, transforms, materials or selection are changed.
    """
    if not math.isfinite(offset) or offset < 0:
        raise ValueError("Cage offset must be a finite nonnegative world-space distance.")
    resolved = groups(ts, validate_cages=False)
    lows = tuple(group.low for group in resolved)
    refs = tuple(ts.bake.high_objects)
    routing = {}
    if len(lows) > 1:
        for ref in refs:
            target = reference_target(ref, lows)
            if target is None:
                raise ValueError(f"Choose a target for '{ref.object.name}' before creating multiple cages.")
            routing.setdefault(target, []).append(ref)
        if any(low not in routing for low in lows):
            raise ValueError("Each low-poly mesh needs a source row to store its custom cage.")
    for low in lows:
        if low.name not in context.view_layer.objects:
            raise ValueError(f"'{low.name}' is not in the active view layer.")
    created, meshes = [], []
    previous = (ts.bake.cage_mode, ts.bake.cage_object)
    old_refs = [(ref, getattr(ref, "target", None), getattr(ref, "cage", None)) for ref in refs]
    try:
        with scope(context, lows) as frozen:
            for low in lows:
                mesh = frozen[low].data.copy()
                meshes.append(mesh)
                if not mesh.vertices or not mesh.polygons:
                    raise ValueError(f"'{low.name}' has no evaluated surface for a cage.")
                world = frozen[low].matrix_world.copy()
                if abs(world.to_3x3().determinant()) < 1e-12:
                    raise ValueError(f"'{low.name}' has a singular transform; its cage cannot be offset.")
                normal_transform = world.to_3x3().inverted().transposed()
                coordinates = [(world @ vertex.co) + (normal_transform @ vertex.normal).normalized() * offset
                               for vertex in mesh.vertices]
                for vertex, coordinate in zip(mesh.vertices, coordinates):
                    vertex.co = coordinate
                mesh.update()
                mesh.materials.clear()
                mesh[OWNER] = True
                mesh[CAGE_MARKER] = True
                cage = bpy.data.objects.new(f"{low.name} Cage", mesh)
                created.append(cage)
                cage[OWNER] = True
                cage[CAGE_MARKER] = low.name_full
                cage.matrix_world = Matrix.Identity(4)
                cage.display_type = "WIRE"
                cage.show_in_front = True
                cage.hide_render = True
                context.scene.collection.objects.link(cage)
                if topology_signature(frozen[low].data) != topology_signature(mesh):
                    raise ValueError(f"Cage creation changed polygon correspondence for '{low.name}'.")
        context.view_layer.update()
        for low, cage in zip(lows, created):
            targets = routing.get(low, refs if len(lows) == 1 else ())
            for ref in targets:
                ref.target, ref.cage = low, cage
        ts.bake.cage_object = created[0] if len(created) == 1 else None
        ts.bake.cage_mode = "CUSTOM"
        return tuple(created)
    except Exception:
        ts.bake.cage_mode, ts.bake.cage_object = previous
        for ref, target, cage in old_refs:
            ref.target, ref.cage = target, cage
        for cage in created:
            bpy.data.objects.remove(cage, do_unlink=True)
        for mesh in meshes:
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        raise
