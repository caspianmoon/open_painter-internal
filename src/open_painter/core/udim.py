"""One authoritative texture set per UDIM tile, preserving the artist's UV map.

Faces are routed through owned material slots. CPU evaluation and shader preview
translate the source UV by the tile origin; image pixels remain ordinary managed
float images, so paint, save and export never depend on tiled Image.pixels access.
"""
from collections import defaultdict

import bpy
import numpy as np

from ..geometry.udim import face_tile, tile_origin
from ..utils.ids import new_id


def source_uv(mesh, ts):
    name = getattr(ts, 'udim_source_uv', '')
    uv = mesh.uv_layers.get(name) if name else next(
        (layer for layer in mesh.uv_layers if layer.active_render), mesh.uv_layers.active)
    if uv is None:
        if not name:
            raise ValueError('A render UV map is required')
        raise ValueError(f"UDIM source UV map {name!r} is missing; restore it before painting or exporting")
    return uv


def local_uv(mesh, ts, faces=None):
    """Read local tile coordinates without editing authoring geometry."""
    uv = source_uv(mesh, ts)
    values = np.empty((len(mesh.loops), 2), np.float32)
    uv.data.foreach_get('uv', values.ravel())
    number = getattr(ts, 'udim_tile', 0)
    if number:
        for polygon in (mesh.polygons if faces is None else (mesh.polygons[int(i)] for i in faces)):
            actual = face_tile(values[list(polygon.loop_indices)])
            if actual != number:
                raise ValueError(f"Face {polygon.index + 1} moved from UDIM {number} to {actual}; "
                                 "restore its UV tile or recreate its UDIM sets")
        values -= tile_origin(number)
    return values


def preflight(scene, objects):
    """Validate every face and assignment before creating any datablock."""
    objects = list(dict.fromkeys(objects))
    if not objects:
        raise ValueError("Select UV-unwrapped meshes to create UDIM texture sets")
    layouts, names = [], set()
    for obj in objects:
        if obj.type != 'MESH' or obj.mode != 'OBJECT':
            raise ValueError("Select mesh objects in Object Mode")
        if obj.data.users > 1 or obj.library or obj.data.library:
            raise ValueError(f"Make {obj.name} local and single-user before creating UDIM sets")
        if any(ref.object == obj for ts in scene.open_painter.texture_sets for ref in ts.assignments):
            raise ValueError(f"{obj.name} already has texture-set assignments; use an unassigned mesh")
        uv = next((layer for layer in obj.data.uv_layers if layer.active_render), obj.data.uv_layers.active)
        if uv is None:
            raise ValueError(f"Unwrap {obj.name} before creating UDIM sets")
        names.add(uv.name)
        groups = defaultdict(list)
        for polygon in obj.data.polygons:
            try:
                number = face_tile([uv.data[i].uv[:] for i in polygon.loop_indices])
            except ValueError as exc:
                raise ValueError(f"{obj.name}, face {polygon.index + 1}: {exc}") from exc
            if obj.material_slots and polygon.material_index >= len(obj.material_slots):
                raise ValueError(f"{obj.name} has an invalid material slot")
            groups[(number, polygon.material_index)].append(polygon.index)
        if not groups:
            raise ValueError(f"{obj.name} has no faces")
        layouts.append((obj, groups))
    if len(names) != 1:
        raise ValueError("Selected meshes must use the same render UV map name for shared UDIM sets")
    return layouts, names.pop()


def validate(ts):
    if not getattr(ts, 'udim_tile', 0):
        return
    tile_origin(ts.udim_tile)
    for ref in ts.assignments:
        obj = ref.object
        if obj is None or obj.type != 'MESH' or ref.slot_index >= len(obj.material_slots):
            raise ValueError('A UDIM target or assigned material slot is missing')
        faces = [p.index for p in obj.data.polygons if p.material_index == ref.slot_index]
        if not faces:
            raise ValueError(f'{obj.name}: UDIM {ts.udim_tile} has no assigned faces')
        local_uv(obj.data, ts, faces)


def create(scene, objects, name='UDIM', resolution=2048):
    """Create tile sets atomically; source UVs, selection and original slots survive."""
    from . import texture_set_manager as sets, material_manager
    from ..constants import RESOLUTIONS
    if str(resolution) not in {item[0] for item in RESOLUTIONS}:
        raise ValueError('Choose a supported texture resolution')
    layouts, uv_name = preflight(scene, objects)
    numbers = sorted({tile for _, groups in layouts for tile, _ in groups})
    project = scene.open_painter
    active_before = project.active_texture_set
    before = [(obj, [p.material_index for p in obj.data.polygons], len(obj.material_slots),
               obj.active_material_index) for obj, _ in layouts]
    made, materials = [], []
    group_id = new_id()
    try:
        for number in numbers:
            ts = sets.create(scene, f'{name} · {number}', resolution)
            made.append(ts.id)
            materials.append(ts.material)
            ts.udim_tile, ts.udim_group, ts.udim_name = number, group_id, name
            ts.udim_source_uv = uv_name
            for obj, groups in layouts:
                if not obj.material_slots:
                    obj.data.materials.append(None)  # original unassigned slot, never paint data
                for (tile, original_slot), faces in sorted(groups.items()):
                    if tile != number:
                        continue
                    original_material = obj.material_slots[original_slot].material
                    slot = len(obj.material_slots)
                    obj.data.materials.append(ts.material)
                    # ``assign`` also owns the physical-size invalidation.
                    sets.assign(scene, ts, obj, slot)
                    ref = ts.assignments[-1]
                    ref.original_material = original_material
                    ref.udim_original_slot = original_slot
                    for face in faces:
                        obj.data.polygons[face].material_index = slot
                    obj.data.update()
            from ..evaluation import physical
            physical.forget(ts)
            physical.sync(ts)
            material_manager.repair(ts)
        scene.view_layers[0].update()
        # Adding another set can reallocate every RNA reference: return fresh handles.
        result = [sets._set_by_id(project, identifier) for identifier in made]
        if len(result) > 1:
            from .shared_stacks import link
            link(result)
        project.active_texture_set = next(i for i, item in enumerate(project.texture_sets) if item.id == made[0])
        return result
    except Exception:
        for identifier in reversed(made):
            index = next((i for i, item in enumerate(project.texture_sets) if item.id == identifier), None)
            if index is not None:
                sets.remove(scene, index)
        for obj, indices, count, active in before:
            obj.data.polygons.foreach_set('material_index', indices)
            while len(obj.data.materials) > count:
                obj.data.materials.pop(index=len(obj.data.materials) - 1)
            obj.active_material_index = active
            obj.data.update()
        for material in materials:
            if material and material.users == 0:
                bpy.data.materials.remove(material)
        project.active_texture_set = active_before
        raise


def restore_assignment(ts, ref):
    """Restore only faces still routed through this owned slot; retain other edits."""
    obj, original = ref.object, ref.udim_original_slot
    if obj is None or original < 0 or ref.slot_index >= len(obj.material_slots):
        return
    if obj.material_slots[ref.slot_index].material != ts.material:
        return
    if original >= len(obj.material_slots):
        raise ValueError(f"{obj.name}: original UDIM material slot is missing")
    for polygon in obj.data.polygons:
        if polygon.material_index == ref.slot_index:
            polygon.material_index = original
    obj.data.update()


def paint_uv(project, obj, members):
    """Give native painting local UVs, without changing the saved source coordinates.

    The temporary active UV selection is persisted with the existing face-selection
    backup so Finish after save/reload can restore it. Other material slots keep their UVs.
    """
    tiled = [ts for ts in members if ts.udim_tile]
    if not tiled:
        return
    mesh = obj.data
    source = source_uv(mesh, tiled[0])
    values = np.empty((len(mesh.loops), 2), np.float32)
    source.data.foreach_get('uv', values.ravel())
    for ts in tiled:
        if ts.udim_source_uv != source.name:
            raise ValueError("Paint Across Sets requires matching UDIM source UV maps")
        slots = {ref.slot_index for ref in ts.assignments if ref.object == obj}
        faces = [p.index for p in mesh.polygons if p.material_index in slots]
        local = local_uv(mesh, ts, faces)
        for face in faces:
            indices = list(mesh.polygons[face].loop_indices)
            values[indices] = local[indices]
    name = mesh.get('open_painter_udim_paint_uv', '')
    original = mesh.uv_layers.active.name if mesh.uv_layers.active else ''
    working = mesh.uv_layers.get(name) if name else None
    if working is None:
        working = mesh.uv_layers.new(name='Open Painter UDIM Paint')
        mesh['open_painter_udim_paint_uv'] = working.name
    project.paint_original_uv = original
    mesh['open_painter_udim_original_uv'] = original
    working.data.foreach_set('uv', values.ravel())
    mesh.uv_layers.active = working
    mesh.update()


def restore_paint_uv(project, obj):
    if obj is not None and obj.type == 'MESH' and project.paint_original_uv:
        uv = obj.data.uv_layers.get(project.paint_original_uv)
        if uv:
            obj.data.uv_layers.active = uv
        if 'open_painter_udim_original_uv' in obj.data:
            del obj.data['open_painter_udim_original_uv']
    project.paint_original_uv = ''
