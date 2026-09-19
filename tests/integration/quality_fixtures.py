"""Fixtures for the add-on quality remedy (painting, seams, channels, export safety).

Every fixture is built procedurally in the current scene and returns the ids and the
analytic reference the tests compare against. Nothing needs a purchased asset.

- channel_plate: constant colour and scalar patches, a sloped normal, a height ramp and an
  alpha ramp on a plane, so channel swaps, gamma mistakes, clipping and accidental writes
  show up as numbers.
- seam_object: a beveled cube and a curved strip, both with disconnected UV islands and a
  continuous world-space colour field painted through the geometry, plus an asymmetric
  height marker; distinguishes true seams from intended boundaries.
- difficult_atlas: hand-authored UV layout on separate quads — a large island, a tiny one,
  islands flush with the tile border, two deliberately close neighbours, a mirrored island,
  a known gap and an intentional overlap.
- surface_isolation: thin parallel surfaces, a recess, disconnected nearby pieces, occluded
  faces, two material slots (one assigned) and an unassigned object.
- transparency_card: cutouts and soft alpha edges with bright RGB under transparent texels.
- production_prop: three texture sets on three objects, 25+ layers of mixed painted and
  procedural content, at 2K (or 4K).

Helpers: ``paint_field`` writes a world-position-derived value into a paint layer's channel
image through the set's geometry (the same UV rasteriser the evaluator uses), so the
authored field is continuous across UV seams by construction; ``ownership`` returns the
covered texels and their island ids at a resolution (the reference for export padding).
"""
import math

import bpy
import numpy as np

from open_painter.core import image_manager as images, layer_stack as stack, texture_set_manager as sets
from open_painter.geometry.snapshots import targets
from open_painter.geometry.uv_rasterizer import interpolate, rasterize


def _mesh_object(name, verts, faces, uvs=None, location=(0, 0, 0)):
    """A mesh from lists; ``uvs`` is one (u, v) per face corner, in face order."""
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    if uvs is not None:
        layer = mesh.uv_layers.new(name="UVMap")
        flat = [coord for face in uvs for coord in face]
        for loop, (u, v) in zip(layer.data, flat):
            loop.uv = (u, v)
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _unwrap(obj, method="SMART", margin=0.02):
    bpy.context.view_layer.objects.active = obj
    for other in bpy.context.view_layer.objects:
        other.select_set(other == obj)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    if method == "SMART":
        bpy.ops.uv.smart_project(island_margin=margin, angle_limit=math.radians(66))
    else:
        bpy.ops.uv.unwrap(margin=margin)
    bpy.ops.object.mode_set(mode="OBJECT")


def _set(scene, name, obj, resolution):
    ts = sets.create(scene, name, int(resolution))
    sets.assign(scene, ts, obj)
    bpy.context.view_layer.update()
    return ts


def paint_layer_image(ts, layer, channel):
    from open_painter.painting.channel_paint import target
    return target(ts, layer, channel)


def paint_field(ts, layer, channel, field, alpha=None):
    """Write ``field(positions (K,3), normals (K,3)) -> (K,3)`` into a paint layer's channel image
    through the set's geometry; texels outside the islands stay untouched (alpha 0)."""
    n = int(ts.resolution)
    image = paint_layer_image(ts, layer, channel)
    data = images.pixels(image)
    with targets(ts) as entries:
        for entry in entries:
            uv = entry["uv"][entry["tri_loops"]]
            rows, cols, tri, weights = rasterize(uv, n)
            if not len(rows):
                continue
            positions = interpolate(weights, entry["positions"][entry["tri_vertices"][tri]])
            normals = interpolate(weights, entry["world_normals"][entry["tri_loops"][tri]])
            values = np.asarray(field(positions, normals), np.float32)
            data[rows, cols, :3] = values
            data[rows, cols, 3] = 1.0 if alpha is None else np.asarray(alpha(positions, normals), np.float32)
    images.write(image, data)
    return image


def ownership(ts, n):
    """Reference coverage and island ids: (covered (n,n) bool, island (n,n) int32, -1 outside)."""
    from open_painter.baking.maps.utility import _islands
    covered = np.zeros((n, n), bool)
    island = np.full((n, n), -1, np.int32)
    base = 0
    with targets(ts) as entries:
        for entry in entries:
            rows, cols, tri, _ = rasterize(entry["uv"][entry["tri_loops"]], n)
            labels, _ = _islands(entry)
            roots, inverse = np.unique(labels, return_inverse=True)
            skip = int(len(roots) > 0 and roots[0] < 0)
            of_face = np.where(labels >= 0, inverse.astype(np.int64) - skip + base, -1)
            base += len(roots) - skip
            if len(rows):
                covered[rows, cols] = True
                island[rows, cols] = of_face[entry["tri_polygons"][tri]]
    return covered, island


# ---- fixtures --------------------------------------------------------------------------------------

def channel_plate(scene, resolution=512):
    """A plane whose channels carry known patches: returns (ts, expected) where ``expected``
    maps channel -> (n, n, 3) reference values in the composited output."""
    obj = _mesh_object("Channel plate", [(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)], [(0, 1, 2, 3)],
                       uvs=[[(0, 0), (1, 0), (1, 1), (0, 1)]])
    ts = _set(scene, "Plate", obj, resolution)
    layer = stack.add(ts, "PAINT", "Patches")
    n = int(resolution)
    u = (np.arange(n) + .5) / n
    uu, vv = np.meshgrid(u, u)
    expected = {}
    # Base colour: four constant patches (quadrants) with distinct linear values.
    color = np.zeros((n, n, 3), np.float32)
    color[(vv < .5) & (uu < .5)] = (.8, .1, .05)
    color[(vv < .5) & (uu >= .5)] = (.05, .6, .1)
    color[(vv >= .5) & (uu < .5)] = (.1, .1, .9)
    color[(vv >= .5) & (uu >= .5)] = (.5, .5, .5)
    expected["BASE_COLOR"] = color
    # Scalars: roughness = u ramp, metallic = v ramp, height = v ramp centred on .5, AO = 1 - u/2.
    expected["ROUGHNESS"] = np.repeat(uu[..., None], 3, -1).astype(np.float32)
    expected["METALLIC"] = np.repeat(vv[..., None], 3, -1).astype(np.float32)
    expected["HEIGHT"] = np.repeat((.25 + .5 * vv)[..., None], 3, -1).astype(np.float32)
    expected["AO"] = np.repeat((1 - .5 * uu)[..., None], 3, -1).astype(np.float32)
    # Sloped normal: tilted 20° about the V axis (a constant tangent normal).
    tilt = math.radians(20)
    expected["NORMAL"] = np.broadcast_to(np.array((math.sin(tilt) * .5 + .5, .5, math.cos(tilt) * .5 + .5), np.float32),
                                         (n, n, 3)).copy()
    # Opacity: an alpha ramp along U with a hard cutout hole in the middle.
    opacity = uu.copy()
    opacity[((uu - .5) ** 2 + (vv - .5) ** 2) < .04] = 0
    expected["OPACITY"] = np.repeat(opacity[..., None], 3, -1).astype(np.float32)
    for channel, values in expected.items():
        image = paint_layer_image(ts, layer, channel)
        data = np.ones((n, n, 4), np.float32)
        data[..., :3] = values
        images.write(image, data)
    ts.height_distance = .05
    return ts, expected


def seam_object(scene, resolution=1024, bevel=.06):
    """A beveled cube and a curved strip in one set, smart-projected into disconnected islands.
    Returns (ts, field) where ``field(positions, normals)`` is the continuous colour painted
    through the geometry into the 'Field' paint layer, and 'Marker' carries an asymmetric
    height ramp along world X."""
    bpy.ops.mesh.primitive_cube_add(size=1.6, location=(0, 0, 0))
    cube = bpy.context.object
    cube.name = "Seam cube"
    modifier = cube.modifiers.new("Bevel", "BEVEL")
    modifier.width, modifier.segments = bevel, 2
    bpy.ops.object.modifier_apply(modifier=modifier.name)
    bpy.ops.mesh.primitive_cylinder_add(vertices=48, radius=1.2, depth=.5, location=(0, 0, 1.6), end_fill_type="NOTHING")
    strip = bpy.context.object
    strip.name = "Seam strip"
    # Half the cylinder: a curved strip whose UV islands must cut somewhere.
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.object.mode_set(mode="OBJECT")
    for polygon in strip.data.polygons:
        polygon.select = polygon.center.y < 0
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.delete(type="FACE")
    bpy.ops.object.mode_set(mode="OBJECT")
    for obj in (cube, strip):
        for polygon in obj.data.polygons:
            polygon.use_smooth = True
        _unwrap(obj)
    cube.select_set(True)
    strip.select_set(True)
    bpy.context.view_layer.objects.active = cube
    bpy.ops.object.join()
    obj = bpy.context.object
    obj.name = "Seam object"
    ts = _set(scene, "Seams", obj, resolution)

    def field(positions, normals):
        p = np.asarray(positions, np.float32)
        return np.stack((np.clip(.5 + .3 * np.sin(2.5 * p[:, 0]), 0, 1),
                         np.clip(.5 + .3 * np.cos(2.0 * p[:, 1] + 1.0), 0, 1),
                         np.clip(.35 + .25 * p[:, 2], 0, 1)), axis=1)

    layer = stack.add(ts, "PAINT", "Field")
    paint_field(ts, layer, "BASE_COLOR", field)
    paint_field(ts, layer, "ROUGHNESS", lambda p, n: np.repeat(np.clip(.3 + .35 * (np.asarray(p)[:, 1] + 1), 0, 1)[:, None], 3, 1))
    marker = stack.add(ts, "PAINT", "Marker")
    paint_field(ts, marker, "HEIGHT", lambda p, n: np.repeat(np.clip(.5 + .15 * np.asarray(p)[:, 0], 0, 1)[:, None], 3, 1))
    ts.height_distance = .05
    return ts, field


def difficult_atlas(scene, resolution=512):
    """Separate quads with a hand-authored UV layout; returns (ts, islands) where ``islands``
    names each quad's UV rectangle and role."""
    n = int(resolution)
    texel = 1.0 / n
    quads = {
        "large": ((.05, .05), (.55, .55)),
        "tiny": ((.60, .06), (.60 + 3 * texel, .06 + 3 * texel)),
        "border_low": ((0.0, .62), (.30, .90)),
        "border_high": ((.70, .70), (1.0, 1.0)),
        "close_a": ((.60, .20), (.78, .38)),
        "close_b": ((.78 + 2 * texel, .20), (.96, .38)),
        "mirrored": ((.62, .44), (.80, .62)),
        "overlap_a": ((.35, .68), (.50, .83)),
        "overlap_b": ((.42, .75), (.57, .90)),
    }
    verts, faces, uvs = [], [], []
    for index, (name, ((u0, v0), (u1, v1))) in enumerate(quads.items()):
        x = index * 2.5
        base = len(verts)
        verts += [(x - 1, -1, 0), (x + 1, -1, 0), (x + 1, 1, 0), (x - 1, 1, 0)]
        faces.append((base, base + 1, base + 2, base + 3))
        corners = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
        if name == "mirrored":
            corners = [(u1, v0), (u0, v0), (u0, v1), (u1, v1)]
        uvs.append(corners)
    obj = _mesh_object("Difficult atlas", verts, faces, uvs=uvs)
    ts = _set(scene, "Atlas", obj, resolution)
    return ts, quads


def surface_isolation(scene, resolution=512):
    """Returns (ts, parts): the assigned object has a thin double wall, a recess and an
    occluded face; a disconnected nearby piece and an unassigned object sit beside it, and a
    second material slot on the assigned object is not part of the set."""
    parts = {}
    # A box with an inset recess on top and two material slots: the recess floor is slot 1.
    bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 0))
    box = bpy.context.object
    box.name = "Isolation box"
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.object.mode_set(mode="OBJECT")
    top = max(box.data.polygons, key=lambda p: p.center.z)
    top.select = True
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.inset(thickness=.4, depth=0)
    bpy.ops.mesh.extrude_region_move(TRANSFORM_OT_translate={"value": (0, 0, -.5)})
    bpy.ops.object.mode_set(mode="OBJECT")
    mat_a, mat_b = bpy.data.materials.new("Iso A"), bpy.data.materials.new("Iso B")
    box.data.materials.append(mat_a)
    box.data.materials.append(mat_b)
    floor = min((p for p in box.data.polygons if p.normal.z > .9 and p.center.z < .9), key=lambda p: p.center.z)
    floor.material_index = 1
    parts["recess_floor"] = floor.index
    # A thin parallel plate 8 mm above the box's top rim, same normal: it covers the rim's
    # +X side (x .6-1.0), reaches over the recess (x .4-.6) and past the box (x 1.0-1.2).
    bpy.ops.mesh.primitive_plane_add(size=.8, location=(.8, 0, 1.008))
    plate = bpy.context.object
    plate.name = "Isolation plate"
    # A disconnected nearby piece: a small cube touching the box's side.
    bpy.ops.mesh.primitive_cube_add(size=.5, location=(1.26, 0, 0))
    piece = bpy.context.object
    piece.name = "Isolation piece"
    # Join the plate and the piece into the assigned object; the box keeps its own slots.
    box.select_set(True); plate.select_set(True); piece.select_set(True)
    bpy.context.view_layer.objects.active = box
    bpy.ops.object.join()
    assigned = bpy.context.object
    assigned.name = "Isolation object"
    # Joining objects without materials adds an empty slot: the plate and the piece belong to slot 0.
    for polygon in assigned.data.polygons:
        if polygon.material_index >= 2:
            polygon.material_index = 0
    while len(assigned.data.materials) > 2:
        assigned.data.materials.pop(index=2)
    _unwrap(assigned)     # one packed layout for the whole object
    # An unassigned object next to it.
    bpy.ops.mesh.primitive_cube_add(size=1, location=(-1.8, 0, 0))
    stranger = bpy.context.object
    stranger.name = "Unassigned neighbour"
    _unwrap(stranger)
    ts = sets.create(scene, "Isolation", int(resolution))
    # Assign only slot 0 of the assigned object.
    sets.assign(scene, ts, assigned)
    for ref in list(ts.assignments):
        if ref.slot_index != 0:
            ts.assignments.remove(list(ts.assignments).index(ref))
    bpy.context.view_layer.update()
    parts.update(assigned=assigned.name, unassigned=stranger.name, slot_unassigned=1)
    return ts, parts


def shelf(scene, resolution=512, location=(0, 0, 0)):
    """An occluded recess in one connected surface, all in one material slot: a lip (top sheet
    at z=.2, x from -1 to .6) joined through a vertical wall at x=.6 to a floor at z=0 (x from 0
    to 1). Seen from above, the floor under the lip (x .0-.6) is hidden and faces the same way as
    the lip; the floor past the wall (x .6-1) is open. Returns (ts, parts) with polygon indices."""
    verts = [(-1, -1, .2), (.6, -1, .2), (.6, 1, .2), (-1, 1, .2),       # lip 0-3
             (.6, -1, 0), (.6, 1, 0),                                     # wall foot 4-5
             (0, -1, 0), (0, 1, 0), (1, -1, 0), (1, 1, 0)]                # floor 6-9
    # The floor is split at x=.6 so it shares the wall's foot edge: hidden and open quads.
    faces = [(0, 1, 2, 3),          # lip, facing up
             (1, 4, 5, 2),          # wall at x=.6, facing +X
             (6, 4, 5, 7),          # floor under the lip (x 0-.6), facing up
             (4, 8, 9, 5)]          # open floor (x .6-1), facing up
    obj = _mesh_object("Shelf", verts, faces, location=location)
    _unwrap(obj)
    ts = _set(scene, "Shelf", obj, resolution)
    polygons = obj.data.polygons
    parts = {"lip": 0, "wall": 1, "hidden_floor": 2, "open_floor": 3,
             "object": obj.name}
    assert polygons[parts["lip"]].normal.z > .9 and polygons[parts["hidden_floor"]].normal.z > .9, \
        [tuple(polygon.normal) for polygon in polygons]
    return ts, parts


def transparency_card(scene, resolution=512):
    """A plane with bright colour under a cutout and a soft alpha edge; returns (ts, expected_opacity)."""
    obj = _mesh_object("Transparency card", [(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)], [(0, 1, 2, 3)],
                       uvs=[[(.1, .1), (.9, .1), (.9, .9), (.1, .9)]])
    ts = _set(scene, "Card", obj, resolution)
    layer = stack.add(ts, "PAINT", "Card")
    n = int(resolution)
    u = (np.arange(n) + .5) / n
    uu, vv = np.meshgrid(u, u)
    color = np.ones((n, n, 4), np.float32)
    color[..., :3] = np.stack((.95 * np.ones_like(uu), .85 * np.ones_like(uu), .1 * np.ones_like(uu)), -1)
    images.write(paint_layer_image(ts, layer, "BASE_COLOR"), color)
    opacity = np.ones((n, n), np.float32)
    opacity[((uu - .35) ** 2 + (vv - .5) ** 2) < .02] = 0                     # a hard cutout hole
    soft = np.clip((uu - .55) / .25, 0, 1)                                     # a soft edge toward U = .8
    opacity = np.where(uu > .55, 1 - soft, opacity)
    data = np.ones((n, n, 4), np.float32)
    data[..., :3] = opacity[..., None]
    images.write(paint_layer_image(ts, layer, "OPACITY"), data)
    return ts, opacity


def production_prop(scene, resolution=2048, layers=25):
    """Three sets on three objects; the first carries ``layers`` mixed layers. Returns the sets."""
    import ui_fixture
    bpy.ops.mesh.primitive_cube_add(size=1.6, location=(0, 0, 0))
    body = bpy.context.object
    body.name = "Prop body"
    modifier = body.modifiers.new("Bevel", "BEVEL")
    modifier.width, modifier.segments = .05, 2
    bpy.ops.object.modifier_apply(modifier=modifier.name)
    bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=.4, depth=1.2, location=(1.6, 0, 0))
    barrel = bpy.context.object
    barrel.name = "Prop barrel"
    bpy.ops.mesh.primitive_plane_add(size=1.5, location=(-1.6, 0, 0))
    plate = bpy.context.object
    plate.name = "Prop plate"
    for obj in (body, barrel, plate):
        for polygon in obj.data.polygons:
            polygon.use_smooth = obj is barrel
        _unwrap(obj)
    result = []
    for name, obj in (("Body", body), ("Barrel", barrel), ("Plate", plate)):
        ts = sets.create(scene, name, int(resolution))
        sets.assign(scene, ts, obj)
        result.append(ts)
    bpy.context.view_layer.update()
    body_set = result[0]
    ui_fixture.bake_synthetic(body_set)
    # Procedural fills with generator masks, like the large timing fixture, then painted content.
    folder = None
    for index in range(layers - 3):
        if index % 8 == 0:
            folder = stack.add(body_set, "FOLDER", f"Group {index // 8}")
            folder_id = folder.id
        layer = stack.add(body_set, "FILL", f"Fill {index}", parent_id=folder_id)
        prop = stack.channel_for(layer, "BASE_COLOR")
        prop.procedural, prop.procedural_scale, prop.procedural_seed = "FBM", 4 + index, index
        prop.color = (.2 + .03 * (index % 7), .3, .5 - .02 * (index % 5), 1)
        mask = layer.masks.add()
        from open_painter.utils.ids import new_id
        mask.id, mask.source, mask.generator, mask.amount, mask.noise_scale = new_id(), "GENERATOR", ("EDGE_WEAR", "CAVITY_DIRT", "DUST")[index % 3], .3, 20 + index
    n = int(resolution)
    painted = stack.add(body_set, "PAINT", "Hand paint")
    paint_field(body_set, painted, "BASE_COLOR", lambda p, nrm: np.stack((.6 * np.ones(len(p)), .2 * np.ones(len(p)), .1 * np.ones(len(p))), 1),
                alpha=lambda p, nrm: (np.asarray(p)[:, 2] > .3).astype(np.float32))
    stack.add(body_set, "PAINT", "Mud paint")
    stack.add(body_set, "PAINT", "Marks")
    for ts in result[1:]:
        layer = stack.add(ts, "FILL", f"{ts.name} base")
        stack.channel_for(layer, "BASE_COLOR").color = (.4, .4, .42, 1)
        stack.add(ts, "PAINT", f"{ts.name} paint")
    stack.invalidate(body_set)
    return result
