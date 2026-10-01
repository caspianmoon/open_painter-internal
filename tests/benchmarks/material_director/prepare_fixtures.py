"""Build neutral, geometry-only benchmark fixtures in Blender (never AI output).

Blender --background --factory-startup --disable-autoexec --python this_file -- \
  --assets A01 --output .blender-test/material-director-benchmark/fixtures-eevee-v1

Source slot partitions are retained exactly. No semantic masks or groups are
created. The adjacent JSON is the evaluator's audit, not a Director attachment.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import time
import shutil

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))
from assets import DEFAULT_MANIFEST, DEFAULT_ROOT, digest

GEOMETRY_ATTRIBUTES = {"position", "material_index", "sharp_edge", "sharp_face", "custom_normal",
                       "uv_seam", "crease_edge", "crease_vert", "bevel_weight_edge", "bevel_weight_vert"}
PREPARATION_CODE_SHA256 = digest(__file__)


def export_reference(manifest_path, asset_root, output_directory):
    """Place the pinned external reference beside fixtures; never load into Blender."""
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    entry = next(item for item in manifest["assets"] if item["id"] == "R01")
    source = Path(asset_root) / entry["entrypoint"]
    expected = next(item["sha256"] for item in entry["files"] if item["path"] == entry["entrypoint"])
    if digest(source) != expected:
        raise ValueError("The pinned R01 reference changed")
    output_directory = Path(output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)
    destination = output_directory / "R01_fabric_reference.png"
    if destination.exists() and digest(destination) != expected:
        raise FileExistsError("An existing fixture reference differs; choose a new output directory")
    if not destination.exists():
        shutil.copyfile(source, destination)
    if digest(destination) != expected:
        raise ValueError("Reference copy verification failed")
    return dict(path=str(destination), sha256=expected, dimensions=entry["dimensions"],
                usage="External visual reference only, attached only for MD-08")


def _json_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def object_snapshot(obj):
    """Stable hashes of frozen geometry, all UV layouts, transforms and partitions."""
    import numpy as np
    mesh = obj.data
    mesh.calc_loop_triangles()
    coordinates = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", coordinates)
    vertices = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", vertices)
    loop_total = np.empty(len(mesh.polygons), dtype=np.int32)
    mesh.polygons.foreach_get("loop_total", loop_total)
    slots = np.empty(len(mesh.polygons), dtype=np.int32)
    mesh.polygons.foreach_get("material_index", slots)
    corner_normals = np.empty(len(mesh.corner_normals) * 3, dtype=np.float32)
    mesh.corner_normals.foreach_get("vector", corner_normals)
    geometry_attributes = {}
    for attr in mesh.attributes:
        if attr.name not in GEOMETRY_ATTRIBUTES or not len(attr.data):
            continue
        sample = attr.data[0]
        field = next((name for name in ("value", "vector", "color") if hasattr(sample, name)), None)
        if not field:
            continue
        value = getattr(sample, field)
        width = len(value) if hasattr(value, "__len__") else 1
        array = np.empty(len(attr.data) * width, dtype=np.float32 if "FLOAT" in attr.data_type else np.int32)
        attr.data.foreach_get(field, array)
        geometry_attributes[attr.name] = dict(domain=attr.domain, data_type=attr.data_type,
                                              sha256=hashlib.sha256(array.tobytes()).hexdigest())
    uv_hashes = {}
    for layer in mesh.uv_layers:
        uv = np.empty(len(layer.data) * 2, dtype=np.float32)
        layer.data.foreach_get("uv", uv)
        uv_hashes[layer.name] = hashlib.sha256(uv.tobytes()).hexdigest()
    return dict(name=obj.name, vertices=len(mesh.vertices), polygons=len(mesh.polygons),
                triangles=len(mesh.loop_triangles), dimensions=list(obj.dimensions),
                matrix_world=[list(row) for row in obj.matrix_world],
                transform_sha256=_json_hash([list(row) for row in obj.matrix_world]),
                geometry_sha256=hashlib.sha256(coordinates.tobytes() + vertices.tobytes() + loop_total.tobytes()).hexdigest(),
                uv_sha256=uv_hashes, material_partition_sha256=hashlib.sha256(slots.tobytes()).hexdigest(),
                corner_normals_sha256=hashlib.sha256(corner_normals.tobytes()).hexdigest(),
                geometry_attributes=geometry_attributes,
                active_uv=mesh.uv_layers.active.name if mesh.uv_layers.active else None,
                color_attribute_count=len(mesh.color_attributes), vertex_group_count=len(obj.vertex_groups))


def _load_geometry(entry, asset_root, collection):
    import bpy
    source = (Path(asset_root) / entry["entrypoint"]).resolve()
    expected = next(f for f in entry["files"] if f["path"] == entry["entrypoint"])
    if digest(source) != expected["sha256"]:
        raise ValueError(f"Frozen asset changed: {source}")
    previous_images = set(bpy.data.images)
    with bpy.data.libraries.load(str(source), link=False) as (available, loaded):
        loaded.objects = list(available.objects)
    imported = [obj for obj in loaded.objects if obj]
    for obj in imported:
        if obj.name not in bpy.context.scene.objects:
            collection.objects.link(obj)
    wanted = set(entry.get("mesh_objects", []))
    meshes = [obj for obj in imported if obj.type == "MESH" and obj.data.polygons
              and (not wanted or obj.name in wanted)]
    if wanted and wanted != {obj.name for obj in meshes}:
        raise ValueError(f"The frozen source object selection changed for {entry['id']}")
    if not meshes:
        raise ValueError(f"No mesh geometry in {source}")
    # Make modifier references evaluable before freezing. No source Python runs.
    for obj in imported:
        obj.hide_set(False)
        obj.hide_viewport = False
    for image in set(bpy.data.images) - previous_images:
        if image.source == "FILE" and not image.packed_file:
            absolute = Path(bpy.path.abspath(image.filepath))
            if not absolute.exists():
                matches = list(source.parent.rglob(Path(image.filepath).name))
                if len(matches) == 1:
                    image.filepath = str(matches[0])
            if any(mod.type == "DISPLACE" and mod.texture and mod.texture.type == "IMAGE"
                   and mod.texture.image == image for obj in meshes for mod in obj.modifiers):
                if not Path(bpy.path.abspath(image.filepath)).exists() and not image.packed_file:
                    raise ValueError(f"Missing geometry displacement image: {image.name}")
                image.reload()
    disclosure = []
    for obj in meshes:
        original_name = obj.name
        modifiers = [dict(name=mod.name, type=mod.type, show_viewport=mod.show_viewport,
                          show_render=mod.show_render,
                          texture=getattr(getattr(mod, "texture", None), "name", None)) for mod in obj.modifiers]
        for modifier in obj.modifiers:
            modifier.show_viewport = modifier.show_render
            if modifier.type == "SUBSURF":
                modifier.levels = modifier.render_levels
        bpy.context.view_layer.update()
        graph = bpy.context.evaluated_depsgraph_get()
        evaluated = obj.evaluated_get(graph)
        frozen = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=graph)
        frozen_partition = [poly.material_index for poly in frozen.polygons]
        matrix = evaluated.matrix_world.copy()
        before = object_snapshot(obj)
        source_slots = [slot.material.name if slot.material else None for slot in obj.material_slots]
        obj.modifiers.clear()
        obj.constraints.clear()
        obj.animation_data_clear()
        obj.parent = None
        obj.data = frozen
        obj.matrix_world = matrix
        obj.name = f"{entry['id']}_{original_name}"
        obj.hide_render = False
        evaluated_snapshot = object_snapshot(obj)
        # Freeze geometry first, then remove every appearance attribute and group.
        colors = [item.name for item in frozen.color_attributes]
        for item in list(frozen.color_attributes):
            frozen.color_attributes.remove(item)
        groups = [group.name for group in obj.vertex_groups]
        obj.vertex_groups.clear()
        for key in list(obj.keys()):
            del obj[key]
        for key in list(frozen.keys()):
            del frozen[key]
        uv_names = {uv.name for uv in frozen.uv_layers}
        # Blender stores geometry semantics in generic attributes too. They
        # are not appearance masks; dropping material_index collapses partitions.
        removed_attributes = []
        for attr in list(frozen.attributes):
            if (attr.name not in uv_names | GEOMETRY_ATTRIBUTES and not attr.is_required
                    and (not attr.name.startswith(".") or attr.name in {".sculpt_mask", ".sculpt_face_set"})):
                removed_attributes.append(attr.name)
                frozen.attributes.remove(attr)
        if not frozen.uv_layers:
            raise ValueError(f"SETUP BLOCKED: {obj.name} has no source UV map; no automatic unwrap permitted")
        if frozen.shape_keys:
            raise ValueError("Unfrozen shape key data")
        if [poly.material_index for poly in frozen.polygons] != frozen_partition:
            raise ValueError("Fixture cleanup altered source material partitions")
        frozen_snapshot = object_snapshot(obj)
        invariants = ("vertices", "polygons", "triangles", "geometry_sha256", "uv_sha256",
                      "material_partition_sha256", "corner_normals_sha256", "geometry_attributes")
        for invariant in invariants:
            if evaluated_snapshot[invariant] != frozen_snapshot[invariant]:
                raise ValueError(f"Fixture cleanup altered {invariant} on {obj.name}")
        disclosure.append(dict(object=obj.name, source_object=original_name, source_material_slots=source_slots,
                               source_snapshot=before, evaluated_snapshot=evaluated_snapshot,
                               frozen_snapshot=frozen_snapshot, geometry_cleanup_invariants="PASS",
                               modifiers_frozen=modifiers, removed_vertex_colors=colors,
                               removed_vertex_groups=groups, removed_attributes=removed_attributes))
    for obj in imported:
        if obj not in meshes:
            bpy.data.objects.remove(obj, do_unlink=True)
    return meshes, disclosure


def _neutralize(meshes):
    """Replace appearance while preserving source material identities/slot boundaries."""
    import bpy
    mapping, bindings, removed = {}, {}, []
    for obj in meshes:
        occupied = {poly.material_index for poly in obj.data.polygons}
        if not obj.data.materials:
            obj.data.materials.append(None)
        for index, slot in enumerate(obj.material_slots):
            original = slot.material
            key = original.as_pointer() if original else (obj.name, index)
            if key not in mapping:
                material = bpy.data.materials.new(f"BENCH_NEUTRAL_{original.name if original else obj.name}")
                material.diffuse_color = (.18, .18, .18, 1)
                material.use_nodes = True
                bsdf = material.node_tree.nodes.get("Principled BSDF")
                bsdf.inputs["Base Color"].default_value = (.18, .18, .18, 1)
                bsdf.inputs["Roughness"].default_value = .6
                mapping[key] = material
                if original:
                    removed.append(original.name)
            slot.material = mapping[key]
            if index in occupied:
                bindings.setdefault(key, []).append((obj, index))
    keep = set(mapping.values())
    for material in list(bpy.data.materials):
        if material not in keep:
            bpy.data.materials.remove(material, do_unlink=True)
    images = [dict(name=image.name, packed=bool(image.packed_file), source=image.source)
              for image in bpy.data.images]
    for store in (bpy.data.textures, bpy.data.images, bpy.data.node_groups, bpy.data.texts):
        for item in list(store):
            store.remove(item, do_unlink=True)
    # Detached source meshes must not retain old named appearance attributes.
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    # Appending mesh objects can pull modifier dependencies and their unused
    # datablocks. No source rig/world survives even as an unlinked datablock.
    for store in (bpy.data.cameras, bpy.data.lights, bpy.data.worlds):
        for item in list(store):
            if item.users == 0:
                store.remove(item)
    return bindings, dict(removed_materials=removed, removed_images=images,
                         source_images_remaining=len(bpy.data.images), semantic_masks_added=False)


def _bounds(objects):
    from mathutils import Vector
    points = [obj.matrix_world @ Vector(corner) for obj in objects for corner in obj.bound_box]
    lower = Vector(tuple(min(p[axis] for p in points) for axis in range(3)))
    upper = Vector(tuple(max(p[axis] for p in points) for axis in range(3)))
    return lower, upper


PRIMARY_ASSETS = ("A01", "A02", "A05", "A04", "A06")
MAP_RESOLUTION = 1024
MIN_ATLAS_OCCUPANCY = .15
RIG_VERSION = "fixed-eevee-v1"


def normalize_model(objects):
    """Apply one uniform asset transform; keep source physical dimensions in audit.

    Applying the transform to mesh coordinates leaves unit object scales and
    preserves relative placements, including all six rocks and both boots.
    Product physical operators currently use world units, not scene unit_scale.
    """
    import bpy
    from mathutils import Matrix, Vector
    bpy.context.view_layer.update()
    low, high = _bounds(objects)
    extent = max(high - low)
    if extent <= 0:
        raise ValueError("Empty model bounds")
    center = (low + high) / 2
    factor = 2.0 / extent
    transform = Matrix.Translation(Vector((0, 0, 1))) @ Matrix.Scale(factor, 4) @ Matrix.Translation(-center)
    source = dict(bounds=[list(low), list(high)], dimensions=list(high - low),
                  source_meters_per_benchmark_unit=1.0 / factor, uniform_scale=factor,
                  target_center=[0, 0, 1], target_max_dimension=2,
                  physical_scale_note="Open Painter physical operators use normalized world units; original meter dimensions remain in this audit.")
    for obj in sorted(objects, key=lambda item: item.name):
        obj.data.transform(transform @ obj.matrix_world)
        obj.matrix_world = Matrix.Identity(4)
        obj.data.update()
    bpy.context.view_layer.update()
    source["normalized_bounds"] = [list(v) for v in _bounds(objects)]
    return source


def _repair_geometry(objects):
    """Repair only detected coincident vertices and inconsistent face windings."""
    import bpy
    import bmesh
    import numpy as np
    from open_painter.baking.doctor import _face_normals
    changes = []
    for obj in sorted(objects, key=lambda item: item.name):
        mesh = obj.data
        before = object_snapshot(obj)
        bm = bmesh.new()
        bm.from_mesh(mesh)
        try:
            # Same fixed normalized tolerance for every fixture. UV loop data and
            # real material indices survive bmesh welding; no region labels added.
            distance = max(1e-6, max(obj.dimensions) * 1e-6)
            doubles = bmesh.ops.find_doubles(bm, verts=list(bm.verts), dist=distance)["targetmap"]
            if doubles:
                bmesh.ops.weld_verts(bm, targetmap=doubles)
                bm.to_mesh(mesh)
                mesh.update()
                changes.append(dict(object=obj.name, operation="weld_coincident_vertices", distance=distance,
                                    vertices_removed=before["vertices"] - len(mesh.vertices)))
            # A scan artifact can be a tiny triangle dangling from an otherwise
            # manifold edge. Its third vertex has no other face and its winding
            # flips on every normal recalculation. Removing that flap restores
            # the underlying two-face edge; it does not create a surface hole.
            area_limit = max(obj.dimensions) ** 2 * 1e-4
            flaps = [face for face in bm.faces if len(face.verts) == 3
                     and sum(edge.is_boundary for edge in face.edges) == 2
                     and any(len(edge.link_faces) > 2 for edge in face.edges)
                     and any(len(vertex.link_faces) == 1 for vertex in face.verts)
                     and face.calc_area() <= area_limit]
            if flaps:
                removed = [dict(face=face.index, area=face.calc_area()) for face in flaps]
                bmesh.ops.delete(bm, geom=flaps, context="FACES_ONLY")
                loose = [vertex for vertex in bm.verts if not vertex.link_faces]
                if loose:
                    bmesh.ops.delete(bm, geom=loose, context="VERTS")
                bm.to_mesh(mesh)
                mesh.update()
                changes.append(dict(object=obj.name, operation="remove_tiny_nonmanifold_dangling_triangles",
                                    area_limit=area_limit, faces_removed=removed))
        finally:
            bm.free()
        before_normals = _face_normals(mesh)
        bm = bmesh.new()
        bm.from_mesh(mesh)
        temp = bpy.data.meshes.new("BENCH_NORMAL_PREFLIGHT")
        try:
            bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
            bm.to_mesh(temp)
            after_normals = _face_normals(temp)
            flips = np.flatnonzero(np.einsum("ij,ij->i", before_normals, after_normals) < -1e-6)
            if len(flips):
                bm.to_mesh(mesh)
                # A reversed loop cannot keep an old custom normal meaningfully.
                # Rebuild only on objects whose winding actually needed repair.
                if mesh.has_custom_normals:
                    mesh.normals_split_custom_set([(0, 0, 0)] * len(mesh.loops))
                mesh.update()
                changes.append(dict(object=obj.name, operation="recalculate_consistent_outward_normals",
                                    faces_changed=len(flips)))
        finally:
            bm.free()
            bpy.data.meshes.remove(temp)
        if before != object_snapshot(obj):
            changes.append(dict(object=obj.name, operation="geometry_repair_audit",
                                before=before, after=object_snapshot(obj)))
    return changes


def _uv_operator(ts, *, unwrap=False):
    """Pack actual source islands jointly across a set, retaining slot boundaries."""
    import bpy
    from math import radians
    bpy.ops.object.select_all(action="DESELECT")
    objects = sorted({ref.object for ref in ts.assignments}, key=lambda obj: obj.name)
    slots = {obj.name: {ref.slot_index for ref in ts.assignments if ref.object == obj}
             for obj in objects}
    for obj in objects:
        obj.select_set(True)
        for polygon in obj.data.polygons:
            polygon.select = polygon.material_index in slots[obj.name]
    bpy.context.view_layer.objects.active = objects[0]
    scene = bpy.context.scene
    prior_sync = scene.tool_settings.use_uv_select_sync
    prior_mode = tuple(scene.tool_settings.mesh_select_mode)
    scene.tool_settings.use_uv_select_sync = True
    scene.tool_settings.mesh_select_mode = (False, False, True)
    try:
        bpy.ops.object.mode_set(mode="EDIT")
        if unwrap:
            bpy.ops.uv.smart_project(angle_limit=radians(66), island_margin=.008,
                                     area_weight=0, correct_aspect=True, scale_to_bounds=True)
        bpy.ops.uv.select_all(action="SELECT")
        # ACTIVE_UDIM in the blank scene is 1001. CLOSEST_UDIM would preserve
        # source charts spread across tiles and leave the required 0–1 map bad.
        bpy.ops.uv.pack_islands(udim_source="ACTIVE_UDIM", rotate=False, scale=True,
                                merge_overlap=False, margin_method="FRACTION", margin=.008)
    finally:
        if bpy.context.object and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        scene.tool_settings.use_uv_select_sync = prior_sync
        scene.tool_settings.mesh_select_mode = prior_mode
    bpy.context.view_layer.update()


def repair_preflight(texture_sets):
    """Pack source charts and isolate only their proven defective polygons.

    Scan meshes can have a handful of folded UV faces. Re-unwrapping every
    triangle destroys atlas utilization, so the source charts survive and only
    exact warning/error faces get new tiny independent islands in a reserved
    strip. This supplies no semantic segmentation or material targeting.
    """
    from open_painter.baking import doctor
    from open_painter.geometry.texel_density import measure
    changes = []
    for ts in texture_sets:
        before = doctor.examine(ts, "FULL")
        issues = [f for f in before.findings if f.check == "uvs" and f.severity in {"ERROR", "WARNING"}]
        if not issues:
            continue
        baseline = {ref.object.name: object_snapshot(ref.object)["uv_sha256"] for ref in ts.assignments}
        _uv_operator(ts)
        packed = doctor.examine(ts, "FULL")
        remaining = [f for f in packed.findings if f.check == "uvs" and f.severity in {"ERROR", "WARNING"}]
        action = "pack_source_islands_without_rotation"
        repaired_faces = []
        if remaining:
            repaired_faces = _isolate_bad_uv_faces(ts, remaining)
            action += ";isolate_only_invalid_uv_polygons_in_reserved_strip"
        changes.append(dict(texture_set=ts.name, operation=action, margin=.008,
                            trigger_findings=[asdict(f) for f in issues],
                            repaired_uv_faces=repaired_faces, before_uv=baseline,
                            after_uv={ref.object.name: object_snapshot(ref.object)["uv_sha256"] for ref in ts.assignments},
                            final_atlas_occupancy=measure(ts)["sampled_occupancy"]))
    return changes


def _isolate_bad_uv_faces(ts, findings):
    """Repair geometric UV defects only; reserve 18% width for these few faces."""
    import bpy
    import math
    import numpy as np
    objects = {ref.object.name: ref.object for ref in ts.assignments}
    pairs = sorted({(f.object_name, index) for f in findings for index in f.faces})
    if not pairs or any(name not in objects for name, _ in pairs):
        raise ValueError("UV issue cannot be addressed by an explicit source-face repair")
    total_faces = sum(len(obj.data.polygons) for obj in objects.values())
    if len(pairs) > max(200, total_faces * .05):
        raise ValueError("More than five percent of source faces require UV repair; choose a better source asset")
    # Nondefective packed charts keep shape and relative positions under one
    # uniform transform. This leaves a separate vertical strip for bad faces.
    arrays = {}
    for name, obj in objects.items():
        uv = np.empty((len(obj.data.loops), 2), np.float32)
        obj.data.uv_layers.active.data.foreach_get("uv", uv.ravel())
        slots = {ref.slot_index for ref in ts.assignments if ref.object == obj}
        loops = np.array([loop for face in obj.data.polygons if face.material_index in slots
                          for loop in face.loop_indices], np.int32)
        uv[loops] = uv[loops] * .80 + .008
        arrays[name] = uv
    width, height = .168, .984
    columns = max(1, math.ceil(math.sqrt(len(pairs) * width / height)))
    rows = math.ceil(len(pairs) / columns)
    cell_width, cell_height = width / columns, height / rows
    if min(cell_width, cell_height) * int(ts.resolution) < 5:
        raise ValueError("Insufficient texture resolution for valid independent repair islands")
    records = []
    for ordinal, (name, index) in enumerate(pairs):
        obj = objects[name]
        face = obj.data.polygons[index]
        count = len(face.loop_indices)
        if count < 3:
            raise ValueError("Degenerate source polygon requires geometry repair")
        # A convex regular polygon guarantees a nonoverlapping UV triangulation
        # even where the source polygon's UVs fold back over themselves. Only
        # these already-invalid face corners are replaced.
        angles = np.arange(count, dtype=np.float64) * (2 * math.pi / count)
        polygon = np.stack((np.cos(angles), np.sin(angles)), axis=-1)
        radius = min(cell_width, cell_height) / 2 - 1 / int(ts.resolution)
        center = np.array((.824 + (ordinal % columns + .5) * cell_width,
                           .008 + (ordinal // columns + .5) * cell_height))
        arrays[name][list(face.loop_indices)] = polygon * radius + center
        records.append(dict(object=name, face=index, corners=count))
    for name, uv in arrays.items():
        objects[name].data.uv_layers.active.data.foreach_set("uv", uv.ravel())
        objects[name].data.update()
    bpy.context.view_layer.update()
    return records

def _node_record(tree):
    nodes = []
    for node in sorted(tree.nodes, key=lambda item: item.name):
        inputs = {}
        for socket in node.inputs:
            if hasattr(socket, "default_value"):
                value = socket.default_value
                if isinstance(value, (str, bool, int, float)):
                    inputs[socket.identifier] = value
                elif hasattr(value, "__iter__"):
                    inputs[socket.identifier] = list(value)
        nodes.append(dict(name=node.name, type=node.bl_idname, inputs=inputs))
    links = sorted([link.from_node.name, link.from_socket.identifier, link.to_node.name, link.to_socket.identifier]
                   for link in tree.links)
    return dict(nodes=nodes, links=links)


def fixed_rig_record(scene, config):
    """Serializable immutable rig values. Active pose/output filepath are omitted.

    The harness may apply only a declared camera pose and restore it. All allowed
    poses are included here; evidence additionally guards the active camera pose.
    """
    import bpy
    camera = bpy.data.objects["BENCHMARK_CAMERA"]
    lights = []
    for name in ("Key", "Fill", "Rim"):
        obj = bpy.data.objects[name]
        data = obj.data
        lights.append(dict(name=name, matrix_world=[list(row) for row in obj.matrix_world],
                           type=data.type, energy=data.energy, color=list(data.color),
                           shape=data.shape, size=data.size, size_y=data.size_y,
                           use_shadow=data.use_shadow, hide_render=obj.hide_render,
                           hide_viewport=obj.hide_viewport, hide_select=obj.hide_select,
                           lock_location=list(obj.lock_location), lock_rotation=list(obj.lock_rotation),
                           lock_scale=list(obj.lock_scale)))
    render_names = ("engine", "resolution_x", "resolution_y", "resolution_percentage",
                    "film_transparent", "use_compositing", "use_sequencer", "pixel_aspect_x", "pixel_aspect_y")
    eevee_names = ("taa_samples", "taa_render_samples", "use_taa_reprojection", "use_shadow_jitter_viewport",
                   "use_shadows", "shadow_ray_count", "shadow_step_count", "use_raytracing", "ray_tracing_method")
    return dict(version=RIG_VERSION, scene_name=scene.name,
                render={key: getattr(scene.render, key) for key in render_names},
                eevee={key: getattr(scene.eevee, key) for key in eevee_names if hasattr(scene.eevee, key)},
                color_management=dict(display_device=scene.display_settings.display_device,
                                      view_transform=scene.view_settings.view_transform, look=scene.view_settings.look,
                                      exposure=scene.view_settings.exposure, gamma=scene.view_settings.gamma,
                                      use_curve_mapping=scene.view_settings.use_curve_mapping),
                world=dict(name=scene.world.name, use_nodes=scene.world.use_nodes, nodes=_node_record(scene.world.node_tree)),
                lights=lights, light_collection=dict(name="BENCHMARK_LIGHTS",
                    hide_render=bpy.data.collections["BENCHMARK_LIGHTS"].hide_render,
                    hide_viewport=bpy.data.collections["BENCHMARK_LIGHTS"].hide_viewport),
                camera=dict(name=camera.name, clip_start=camera.data.clip_start, clip_end=camera.data.clip_end,
                            sensor_width=camera.data.sensor_width, sensor_height=camera.data.sensor_height,
                            shift_x=camera.data.shift_x, shift_y=camera.data.shift_y,
                            dof_enabled=camera.data.dof.use_dof, hide_select=camera.hide_select,
                            lock_location=list(camera.lock_location), lock_rotation=list(camera.lock_rotation),
                            lock_scale=list(camera.lock_scale)),
                camera_poses=config["camera_poses"], units=dict(system=scene.unit_settings.system,
                                                             scale_length=scene.unit_settings.scale_length))


def rig_signature(scene, config):
    return _json_hash(fixed_rig_record(scene, config))


def _studio(scene, objects):
    """One model-independent rig. Never inspect asset bounds to move camera/lights."""
    import bpy
    from mathutils import Vector
    scene.name = "BENCHMARK_SCENE"
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0
    scene.render.engine = "BLENDER_EEVEE"
    scene.eevee.taa_samples = 64
    scene.eevee.taa_render_samples = 64
    scene.eevee.use_taa_reprojection = False
    scene.eevee.use_shadow_jitter_viewport = False
    scene.eevee.use_shadows = True
    scene.eevee.use_raytracing = False
    scene.render.resolution_x = scene.render.resolution_y = 1024
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.render.use_compositing = False
    scene.render.use_sequencer = False
    if hasattr(scene, "use_nodes"):
        scene.use_nodes = False
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "8"
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.view_settings.exposure = 0
    scene.view_settings.gamma = 1
    scene.view_settings.use_curve_mapping = False
    scene.world = bpy.data.worlds.new("BENCHMARK_WORLD")
    scene.world.use_nodes = True
    scene.world.node_tree.nodes.get("Background").inputs[0].default_value = (.18, .18, .18, 1)
    scene.world.node_tree.nodes.get("Background").inputs[1].default_value = .35
    collection = bpy.data.collections.new("BENCHMARK_LIGHTS")
    scene.collection.children.link(collection)
    center = Vector((0, 0, 1))
    for name, location, energy, size in (("Key", (-3, -4, 6), 1100, 4),
                                       ("Fill", (4, -2, 3), 650, 4),
                                       ("Rim", (1, 4, 5), 1000, 3)):
        data = bpy.data.lights.new(name, "AREA")
        data.energy, data.shape, data.size = energy, "DISK", size
        obj = bpy.data.objects.new(name, data)
        collection.objects.link(obj)
        obj.location = location
        obj.rotation_euler = (center - obj.location).to_track_quat("-Z", "Y").to_euler()
        obj.lock_location = obj.lock_rotation = obj.lock_scale = (True, True, True)
        obj.hide_select = True
    data = bpy.data.cameras.new("BENCHMARK_CAMERA")
    camera = bpy.data.objects.new("BENCHMARK_CAMERA", data)
    scene.collection.objects.link(camera)
    data.type, data.ortho_scale, data.lens = "ORTHO", 3.2, 50
    data.clip_start, data.clip_end = .01, 100
    scene.camera = camera
    poses = {}
    for name, location, scale in (("front", (4, -6, 3.4), 3.2),
                                  ("back", (-4, 6, 3.4), 3.2),
                                  ("close", (4, -6, 3.4), 1.8)):
        camera.location = location
        camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
        bpy.context.view_layer.update()
        poses[name] = dict(matrix_world=[list(row) for row in camera.matrix_world],
                           type="ORTHO", ortho_scale=scale, lens=50)
    from mathutils import Matrix
    camera.matrix_world = Matrix(poses["front"]["matrix_world"])
    data.ortho_scale = poses["front"]["ortho_scale"]
    camera.lock_location = camera.lock_rotation = camera.lock_scale = (True, True, True)
    camera.hide_select = True
    config = dict(camera_name=camera.name, camera_names=[camera.name], close_camera_names=[],
                  camera_poses=poses, input_camera_pose="front", input_camera_name=camera.name,
                  primary_evidence=True, render_engine=scene.render.engine, render_size=[1024, 1024],
                  fixed_light_names=["Key", "Fill", "Rim"], relight_collection_names=[],
                  guard_object_names=[], studio_version=RIG_VERSION)
    config["rig_signature"] = rig_signature(scene, config)
    return config

def _image_hash(image):
    import numpy as np
    pixels = np.empty(len(image.pixels), dtype=np.float32)
    image.pixels.foreach_get(pixels)
    return hashlib.sha256(pixels.tobytes()).hexdigest()


def _open_rig(output_directory):
    """Create a neutral rig exactly once, then reload that same immutable file."""
    import bpy
    directory = Path(output_directory) / "_infrastructure"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "benchmark_rig.blend"
    metadata = path.with_suffix(".json")
    if path.exists():
        audit = json.loads(metadata.read_text(encoding="utf-8"))
        if digest(path) != audit["sha256"]:
            raise ValueError("The fixed benchmark rig was modified")
        bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False, use_scripts=False)
        config = json.loads(bpy.context.scene["md_benchmark_config"])
        if config["studio_version"] != RIG_VERSION or rig_signature(bpy.context.scene, config) != config["rig_signature"]:
            raise ValueError("The fixed benchmark rig differs from its manifest")
    else:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        scene = bpy.context.scene
        collection = bpy.data.collections.new("TEST_MODEL")
        scene.collection.children.link(collection)
        config = _studio(scene, [])
        scene["md_benchmark_config"] = json.dumps(config)
        bpy.ops.wm.save_as_mainfile(filepath=str(path), check_existing=False)
        audit = dict(path=str(path), sha256=digest(path), config=config,
                     rig_record=fixed_rig_record(scene, config))
        metadata.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    return config, dict(path=str(path), sha256=digest(path))


def _strict_findings(diagnosis):
    # Seam marking is an authoring convention; absent marked seams do not make
    # the UV layout invalid. All geometry/UV warning/error findings block setup.
    return [finding for finding in diagnosis.findings
            if finding.severity == "ERROR"
            or (finding.severity == "WARNING" and finding.check != "seams")
            or (finding.check in {"uvs", "normals", "doubles"} and "skipped" in finding.message.lower())]


def prepare_fixture(asset_ids, manifest_path, asset_root, output_path, *, samples=32, bake=True):
    """Prepare exactly one of the five clean primary fixtures, without Cycles.

    Repairable source defects are repaired deterministically before the test.
    Unresolved setup defects are FAIL, never a Material Director result.
    """
    import bpy
    import open_painter
    from open_painter.core import texture_set_manager as sets, layer_stack
    from open_painter.authoring import document
    from open_painter.baking import bake_manager, doctor
    from open_painter.geometry.provenance import status
    from open_painter.geometry.texel_density import measure
    from open_painter.evaluation import shader_preview
    from cpu_maps import bake_ao, model_tree

    output_path = Path(output_path).resolve()
    if samples != 32:
        raise ValueError("The primary protocol freezes AO samples at 32")
    if len(asset_ids) != 1 or asset_ids[0] not in PRIMARY_ASSETS:
        raise ValueError("Primary fixtures contain exactly one of A01,A02,A05,A04,A06")
    if output_path.exists():
        raise FileExistsError(f"Frozen fixture exists; choose a new output directory: {output_path}")
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    entry = next(item for item in manifest["assets"] if item["id"] == asset_ids[0])
    if entry.get("status") != "DOWNLOADED_VERIFIED":
        raise ValueError(f"Download and verify {entry['id']} first")
    report = dict(schema_version=2, protocol=RIG_VERSION, fixture_id=output_path.stem,
                  status="PREPARING", asset_ids=list(asset_ids), resolution=MAP_RESOLUTION,
                  setup_only=True, ai_calls=0, source_appearance_reused=False, cycles_used=False,
                  preparation_script_sha256=PREPARATION_CODE_SHA256,
                  asset_manifest_sha256=digest(manifest_path),
                  source_files=[dict(id=entry["id"], path=entry["entrypoint"],
                                     sha256=next(f["sha256"] for f in entry["files"] if f["path"] == entry["entrypoint"]))],
                  objects=[], texture_sets=[], blockers=[], warnings=[], repairs=[])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path = output_path.with_suffix(".json")
    started = time.monotonic()
    def save_report():
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    if not hasattr(bpy.types.Scene, "open_painter"):
        open_painter.register()
    try:
        studio, report["fixed_rig_file"] = _open_rig(output_path.parent)
        scene = bpy.context.scene
        shader_preview.clear()
        objects, disclosures = _load_geometry(entry, asset_root, bpy.data.collections["TEST_MODEL"])
        report["source_disclosures"] = disclosures
        report["normalization"] = normalize_model(objects)
        report["repairs"].extend(_repair_geometry(objects))
        bindings, report["contamination_removal"] = _neutralize(objects)
        set_ids = []
        for refs in bindings.values():
            source_name = refs[0][0].material_slots[refs[0][1]].material.name.removeprefix("BENCH_NEUTRAL_")
            ts = sets.create(scene, source_name, MAP_RESOLUTION)
            for obj, index in refs:
                sets.assign(scene, ts, obj, slot_index=index)
            layer = layer_stack.add(ts, "FILL", "Protected artist adjustment")
            for channel in layer.channels:
                channel.enabled = False
            layer.visible = False
            ts.director_locks = json.dumps([["layers", layer.id]])
            ts.bake.selected_to_active = False
            ts.bake.normal = ts.bake.ao = False
            ts.bake.curvature = ts.bake.position = ts.bake.world_normal = ts.bake.thickness = True
            ts.bake.samples = samples
            ts.bake.ao_distance = .2
            set_ids.append(ts.id)
        report["repairs"].extend(repair_preflight(scene.open_painter.texture_sets))
        config = dict(schema_version=2, fixture_id=output_path.stem, asset_ids=list(asset_ids),
                      target_set_ids=set_ids, semantic_annotations_supplied=False,
                      source_disclosures=[dict(object=d["object"], source_material_slots=d["source_material_slots"])
                                          for d in disclosures], **studio)
        scene["md_benchmark_config"] = json.dumps(config)
        report["config"] = config
        report["rig_record"] = fixed_rig_record(scene, config)
        if rig_signature(scene, config) != config["rig_signature"]:
            raise ValueError("Asset import changed the fixed infrastructure")
        # Check every set before allocating any bake work.
        for ts in scene.open_painter.texture_sets:
            diagnosis = doctor.examine(ts, "FULL")
            item = dict(id=ts.id, name=ts.name, resolution=int(ts.resolution),
                        assignments=[dict(object=r.object.name, slot=r.slot_index) for r in ts.assignments],
                        protected_layer_ids=[layer.id for layer in ts.layers], maps=[],
                        diagnostics=dict(summary=diagnosis.summary(), findings=[asdict(f) for f in diagnosis.findings]),
                        texel_density=measure(ts),
                        bake_settings=dict(samples=samples, selected_to_active=False, ao_distance=.2,
                                           mean_curvature_distance=ts.bake.mean_curvature_distance,
                                           curvature_angle=ts.bake.curvature_angle, curvature_width=ts.bake.curvature_width))
            report["texture_sets"].append(item)
            if item["texel_density"]["sampled_occupancy"] < MIN_ATLAS_OCCUPANCY:
                report["blockers"].append(f"{ts.name}: usable UV atlas occupancy is below {MIN_ATLAS_OCCUPANCY:.0%}")
            report["blockers"].extend(f"{ts.name}: {f.message}" for f in _strict_findings(diagnosis))
            report["warnings"].extend(f"{ts.name}: {f.message}" for f in diagnosis.findings
                                      if f.severity == "INFO")
        if report["blockers"]:
            raise ValueError("Source geometry/UV preflight failed after deterministic repair")
        if not bake:
            raise ValueError("Fresh maps skipped; this diagnostic fixture is not runnable")
        tree = model_tree(scene)
        for index, ts in enumerate(scene.open_painter.texture_sets):
            item = report["texture_sets"][index]
            scene.open_painter.active_texture_set = index
            bpy.ops.object.select_all(action="DESELECT")
            for ref in ts.assignments:
                ref.object.select_set(True)
            bpy.context.view_layer.objects.active = ts.assignments[0].object
            start = time.monotonic()
            for kind in bake_manager.bake_steps(bpy.context, ts):
                if scene.render.engine != "BLENDER_EEVEE":
                    raise ValueError("A geometry bake changed the fixed Eevee renderer")
                print("FIXTURE_BAKE", output_path.stem, ts.name, kind, flush=True)
            image = bake_ao(ts, tree=tree)
            bake_manager.register_map(ts, "AO", image)
            print("FIXTURE_BAKE", output_path.stem, ts.name, "AO_CPU_BVH", flush=True)
            item["bake_seconds"] = round(time.monotonic() - start, 3)
            item["maps"] = [dict(kind=m.kind, name=m.image.name, size=list(m.image.size),
                                 status=status(ts, m), decoded_sha256=_image_hash(m.image),
                                 metadata={key: m.image[key] for key in m.image.keys()
                                           if isinstance(m.image[key], (str, bool, int, float))})
                            for m in ts.baked_maps if m.image]
            required = {"AO", "CURVATURE", "POSITION", "WORLD_NORMAL", "THICKNESS"}
            if not required <= {m["kind"] for m in item["maps"]} or not all(m["status"] == "Ready" for m in item["maps"]):
                raise ValueError(f"{ts.name}: required fresh CPU geometry maps missing or stale")
            item["material_semantic_sha256"] = hashlib.sha256(document.canonical(document.snapshot(ts)).encode()).hexdigest()
            save_report()
        report["objects"] = [object_snapshot(obj) for obj in objects]
        report["guard_objects"], report["protected_materials"] = [], []
        report["protection"] = "Each target set has one locked invisible artist adjustment; no extra mesh pollutes the fixed studio."
        scene.open_painter.active_texture_set = 0
        bpy.ops.object.select_all(action="DESELECT")
        for obj in objects:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = objects[0]
        if rig_signature(scene, config) != config["rig_signature"]:
            raise ValueError("Preparation changed the fixed rig")
        bpy.ops.wm.save_as_mainfile(filepath=str(output_path), check_existing=False)
        report["path"], report["sha256"] = str(output_path), digest(output_path)
        if digest(Path(asset_root) / entry["entrypoint"]) != report["source_files"][0]["sha256"]:
            raise ValueError("Original source file was modified")
        report["originals_unchanged"], report["status"] = True, "READY"
    except Exception as exc:
        report["status"] = "FAIL"
        report["blockers"].append(f"{type(exc).__name__}: {exc}")
        save_report()
        raise
    save_report()
    print("FIXTURE_" + report["status"], str(output_path), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset-manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--assets-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--output", type=Path, default=ROOT / ".blender-test/material-director-benchmark/fixtures-eevee-v1")
    parser.add_argument("--assets", default=",".join(PRIMARY_ASSETS))
    parser.add_argument("--samples", type=int, default=32)
    parser.add_argument("--skip-bakes", action="store_true", help="Diagnostic only; emits FAIL and no runnable fixture")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:])
    manifest = json.loads(args.asset_manifest.read_text(encoding="utf-8"))
    reports = []
    for identifier in filter(None, args.assets.split(",")):
        entry = next(e for e in manifest["assets"] if e["id"] == identifier)
        reports.append(prepare_fixture([identifier], args.asset_manifest, args.assets_root, args.output / entry["fixture"],
                                       samples=args.samples, bake=not args.skip_bakes))
    # Rebuild from all ready reports so separate invocations produce one index.
    all_reports = [json.loads(path.read_text(encoding="utf-8")) for path in args.output.glob("A*.json")]
    fixtures = [dict(fixture_id=r["fixture_id"], path=r["path"], sha256=r["sha256"],
                     asset_ids=r["asset_ids"], rig_signature=r["config"]["rig_signature"])
                for r in all_reports if r.get("status") == "READY"]
    signatures = {item["rig_signature"] for item in fixtures}
    if len(signatures) != 1:
        raise ValueError("Fixture rig signatures differ")
    (args.output / "fixture_index.json").write_text(json.dumps(dict(protocol=RIG_VERSION, fixtures=fixtures), indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
