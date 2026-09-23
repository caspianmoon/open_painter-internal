"""Explicitly opted-in 3D-view context; no screen UI, file writes or image datablocks."""

import base64
from contextlib import contextmanager, nullcontext
import struct
import zlib


def png(width, height, rgba):
    def chunk(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data) & 0xffffffff)
    stride = width * 4
    rows = b"".join(b"\0" + rgba[y * stride:(y + 1) * stride] for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!2I5B", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


@contextmanager
def isolated(context, targets, assignments=None, region_faces=None, region_topologies=None):
    """Isolate exact assigned faces using disposable evaluated-mesh copies.

    Source geometry, UVs, material slots, modifiers and selection are never edited.
    The objects-only form remains useful to callers explicitly asking for objects.
    """
    # Hiding a base drops its selected flag on the next view-layer evaluation, and
    # unhiding never brings it back, so the artist's selection is put back by hand.
    hidden, copies, meshes, collection = [], [], [], None
    active = context.view_layer.objects.active
    try:
        if assignments is not None:
            import bpy
            import bmesh
            depsgraph = context.evaluated_depsgraph_get()
            collection = bpy.data.collections.new('Open Painter temporary target view')
            context.scene.collection.children.link(collection)
            for obj in targets:
                if region_faces is not None and obj not in region_faces:
                    continue
                slots = {ref.slot_index for ref in assignments if ref.object == obj}
                evaluated = obj.evaluated_get(depsgraph)
                mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=depsgraph)
                meshes.append(mesh)
                chosen = region_faces.get(obj) if region_faces is not None else None
                if chosen is not None and region_topologies is not None:
                    from ..authoring.regions import topology
                    if topology(mesh) != region_topologies.get(obj):
                        raise ValueError('Region topology changed before capture; inspect the current regions again')
                # Object-linked material overrides must follow the copied faces too.
                for index, slot in enumerate(obj.material_slots):
                    if index < len(mesh.materials):
                        mesh.materials[index] = slot.material
                bm = bmesh.new()
                try:
                    bm.from_mesh(mesh)
                    bm.faces.ensure_lookup_table()
                    bm.faces.index_update()
                    bmesh.ops.delete(bm, geom=[face for face in bm.faces if face.material_index not in slots
                                             or (chosen is not None and face.index not in chosen)], context='FACES')
                    bmesh.ops.delete(bm, geom=[vertex for vertex in bm.verts if not vertex.link_faces], context='VERTS')
                    bm.to_mesh(mesh)
                finally:
                    bm.free()
                if not len(mesh.polygons):
                    raise ValueError('This texture-set assignment has no visible evaluated faces')
                copy = bpy.data.objects.new('Open Painter isolated target', mesh)
                copies.append(copy)
                collection.objects.link(copy)
                copy.matrix_world = obj.matrix_world.copy()
            if not copies:
                raise ValueError('The selected region does not belong to this texture set')
        geometry_types = {'MESH', 'CURVE', 'SURFACE', 'META', 'FONT', 'VOLUME', 'POINTCLOUD', 'GREASEPENCIL'}
        for obj in list(context.view_layer.objects):
            if obj.type in geometry_types and obj not in copies and (assignments is not None or obj not in targets) and not obj.hide_get():
                hidden.append((obj, obj.select_get()))
                obj.hide_set(True)
        context.view_layer.update()
        yield copies if assignments is not None else targets
    finally:
        if collection is not None:
            import bpy
            for obj in copies:
                bpy.data.objects.remove(obj, do_unlink=True)
            for mesh in meshes:
                bpy.data.meshes.remove(mesh)
            bpy.data.collections.remove(collection)
        for obj, selected in hidden:
            obj.hide_set(False)
            obj.select_set(selected)
        context.view_layer.objects.active = active
        if hidden:
            context.view_layer.update()


def framing_view(context):
    """Stable inputs for automatically framed evidence, independent of UI redraws.

    RegionView3D.window_matrix is a cached display matrix: resizing a viewport,
    camera-border zoom and offscreen drawing can replace it after a tool returns.
    Framed evidence owns its aspect, fit and clip planes, so use authored camera
    orientation/lens or viewport rotation/lens instead of that transient cache.
    """
    from mathutils import Matrix
    from types import SimpleNamespace
    space = context.space_data
    view = space.region_3d
    camera = space.camera if space.use_local_camera else context.scene.camera
    if view.view_perspective == 'CAMERA' and camera is not None:
        camera = camera.evaluated_get(context.evaluated_depsgraph_get())
        rotation = camera.matrix_world.to_quaternion().inverted().to_matrix()
        perspective = camera.data.type != 'ORTHO'
        sensor = camera.data.sensor_height if camera.data.sensor_fit == 'VERTICAL' else camera.data.sensor_width
        focal_y = 2. * camera.data.lens / max(.001, sensor)
    else:
        rotation = view.view_rotation.inverted().to_matrix()
        perspective = view.view_perspective != 'ORTHO'
        focal_y = 2. * space.lens / 32.
    projection = Matrix.Identity(4)
    if perspective:
        projection[0][0] = projection[1][1] = focal_y
        projection[3][3] = 0.
    return SimpleNamespace(view_matrix=rotation.to_4x4(), window_matrix=projection)


def principal_directions(context):
    """The six labelled review directions, derived from the artist's own viewing frame.

    Candidate review and surface observation must agree on what 'front' means, so
    both read the outward direction of the current view, flattened to horizontal.
    """
    from mathutils import Vector
    outward = framing_view(context).view_matrix.to_3x3().inverted() @ Vector((0, 0, 1))
    outward.z = 0
    outward = outward.normalized() if outward.length > .001 else Vector((0, -1, 0))
    sideward = Vector((-outward.y, outward.x, 0))
    return [('front', outward), ('back', -outward), ('left', -sideward), ('right', sideward),
            ('top', Vector((0, 0, 1))), ('bottom', Vector((0, 0, -1)))]


@contextmanager
def preserve_view(space):
    """Offscreen inspection borrows the editor, never its authored view state.

    Blender can update RegionView3D while drawing with explicit matrices. Restore
    its input properties, not the derived/cached window or perspective matrices.
    """
    view = space.region_3d
    saved = {key: getattr(view, key) for key in (
        'view_rotation', 'view_location', 'view_distance', 'view_perspective',
        'view_camera_offset', 'view_camera_zoom')}
    saved = {key: (value.copy() if hasattr(value, 'copy') else
                   tuple(value) if not isinstance(value, str) and hasattr(value, '__len__') else value)
             for key, value in saved.items()}
    try:
        yield
    finally:
        for key, value in saved.items():
            current = getattr(view, key)
            # Avoid setter-induced quaternion normalization on unchanged views.
            if current != value:
                setattr(view, key, value)


def draw_view(offscreen, scene, view_layer, space, region, matrix, projection):
    with preserve_view(space):
        offscreen.draw_view3d(scene, view_layer, space, region, matrix, projection,
                             do_color_management=True, draw_background=True)


def view_identity(view):
    """Ignore float32 evaluation noise, while retaining authored view changes."""
    return {name: [[round(float(value), 5) for value in row] for row in matrix]
            for name, matrix in (('view', view.view_matrix), ('projection', view.window_matrix))}


def framing_matrices(view, points, aspect):
    """Explicit offscreen matrices work even when the viewport is in CAMERA mode.

    Moving RegionView3D.view_location does not reframe a camera view. These matrices
    retain the viewing orientation and perspective while leaving its camera alone.
    """
    from mathutils import Matrix, Vector
    import math
    if not points:
        raise ValueError('The target has no points to frame')
    lower = Vector(tuple(min(point[i] for point in points) for i in range(3)))
    upper = Vector(tuple(max(point[i] for point in points) for i in range(3)))
    center = (lower + upper) * .5
    radius = max(.00001, max((point - center).length for point in points))
    original = view.window_matrix
    perspective = abs(original[3][3]) < .5
    rotation = view.view_matrix.to_3x3()
    if perspective:
        fy = max(.1, abs(original[1][1]))
        fx = fy / aspect
        half_angle = min(math.atan(1. / fx), math.atan(1. / fy))
        distance = radius / math.sin(half_angle) * 1.15
        near, far = max(radius * .001, 1e-7), distance + radius * 4.
        projection = Matrix(((fx, 0, 0, 0), (0, fy, 0, 0),
                             (0, 0, -(far + near) / (far - near), -2 * far * near / (far - near)),
                             (0, 0, -1, 0)))
    else:
        extent = radius * 1.15 * max(1., 1. / aspect)
        distance = radius * 3.
        near, far = radius * .001, radius * 8.
        projection = Matrix(((1. / (extent * aspect), 0, 0, 0), (0, 1. / extent, 0, 0),
                             (0, 0, -2. / (far - near), -(far + near) / (far - near)), (0, 0, 0, 1)))
    eye = center + rotation.inverted_safe() @ Vector((0., 0., distance))
    matrix = rotation.to_4x4()
    matrix.translation = -(rotation @ eye)
    return matrix, projection


def capture(context, ts, *, max_side=512, isolate=False, frame_target=False, region_faces=None, region_topologies=None,
            orbit=0., closeup=False):
    import bpy
    import gpu
    import numpy as np
    from ..evaluation import shader_preview
    if bpy.app.background or context.area is None or context.area.type != "VIEW_3D":
        raise ValueError("Send the current 3D view from a visible Painter viewport")
    space = context.area.spaces.active
    region = next(r for r in context.area.regions if r.type == "WINDOW")
    if space.shading.type not in {"MATERIAL", "RENDERED"}:
        raise ValueError("Use Material Preview or Rendered shading before sending the 3D view")
    if ts.preview_backend == "LIVE" and not shader_preview.current(context.scene, ts):
        raise ValueError("Wait for the material preview to finish before sending the 3D view")
    from ..generators.scene_fields import signature
    for layer in ts.layers:
        if not layer.visible:
            continue
        for mask in layer.masks:
            if not mask.enabled or not (mask.source == "SCENE" or
                                       (mask.source == "GENERATOR" and mask.edge_source == "LIVE")):
                continue
            field = mask.influence
            if field.error and field.error != "Analysing…":
                raise ValueError(f"Fix scene analysis for {layer.name} > {mask.name}: {field.error}")
            if field.mode == "LIVE" and (not field.image or field.provenance != repr(signature(ts, field))):
                raise ValueError("Wait for scene analysis to finish before sending the 3D view")
    scale = min(1., max_side / max(region.width, region.height))  # never upsample a small region
    width, height = max(1, round(region.width * scale)), max(1, round(region.height * scale))
    offscreen = gpu.types.GPUOffScreen(width, height, format="RGBA8")
    overlays = space.overlay.show_overlays
    view = space.region_3d
    targets = set(region_faces) if region_faces is not None else {ref.object for ref in ts.assignments if ref.object}
    try:
        with isolated(context, targets, ts.assignments, region_faces, region_topologies) if isolate else nullcontext(targets) as shown:
            view_matrix, projection_matrix = view.view_matrix.copy(), view.window_matrix.copy()
            if isolate and frame_target:
                from mathutils import Vector
                points = [obj.matrix_world @ Vector(corner) for obj in shown for corner in obj.bound_box]
                view_matrix, projection_matrix = framing_matrices(framing_view(context), points, width / height)
            if orbit:
                from mathutils import Matrix, Vector
                points = [obj.matrix_world @ Vector(corner) for obj in shown for corner in obj.bound_box]
                center = sum(points, Vector()) / len(points)
                view_matrix = view_matrix @ Matrix.Translation(center) @ Matrix.Rotation(orbit, 4, 'Z') @ Matrix.Translation(-center)
            if closeup:
                projection_matrix[0][0] *= 1.8
                projection_matrix[1][1] *= 1.8
            space.overlay.show_overlays = False
            with context.temp_override(area=context.area, region=region):
                draw_view(offscreen, context.scene, context.view_layer, space, region,
                          view_matrix, projection_matrix)
            with offscreen.bind():
                pixels = gpu.state.active_framebuffer_get().read_color(0, 0, width, height, 4, 0, "UBYTE")
                data = np.asarray(pixels, dtype=np.uint8).reshape(height, width, 4)[::-1].tobytes()
    finally:
        space.overlay.show_overlays = overlays
        offscreen.free()
    return "data:image/png;base64," + base64.b64encode(png(width, height, data)).decode()
