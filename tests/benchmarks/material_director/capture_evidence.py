"""Evaluator evidence for the live benchmark; never authors a Director proposal.

Run inside Blender after a provider turn settles. CPU channel buffers are saved
losslessly as little-endian float32 NPY alongside human-viewable PNGs. Photometric
quality, semantic placement, and editable controls require declared annotations
or a reviewer; a populated stack is never called a visual pass here.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import time


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return str(path)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def safe_name(value):
    return re.sub(r"[^\w.\-]+", "_", str(value)).strip("._") or "unnamed"


def config_for(scene, config=None):
    frozen = scene.get("md_benchmark_config", "{}")
    result = json.loads(frozen) if isinstance(frozen, str) else dict(frozen)
    result.update(config or {})
    return result


def _array_hash(value):
    import numpy as np
    array = np.ascontiguousarray(value)
    array = array.astype(array.dtype.newbyteorder("<"), copy=False)
    return dict(sha256=hashlib.sha256(array.tobytes()).hexdigest(),
                shape=list(array.shape), dtype=array.dtype.str)


def _rna_array(collection, field, width=1, dtype="float32"):
    import numpy as np
    values = np.empty(len(collection) * width, dtype=dtype)
    collection.foreach_get(field, values)
    return values.reshape((-1, width)) if width > 1 else values


def image_record(image):
    import numpy as np
    values = np.empty(len(image.pixels), np.float32)
    image.pixels.foreach_get(values)
    return dict(name=image.name, size=list(image.size), colorspace=image.colorspace_settings.name,
                packed=bool(image.packed_file), source=image.source,
                generated_id=image.get("director_image_id", ""),
                finite=bool(np.isfinite(values).all()), decoded=_array_hash(values))


def _simple(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "name_full"):
        return {"datablock": value.bl_rna.identifier, "name": value.name_full}
    try:
        return [_simple(item) for item in value]
    except TypeError:
        return str(value)


def _rna_values(item, skip=()):
    result = {}
    for prop in item.bl_rna.properties:
        key = prop.identifier
        if prop.is_readonly or key in {*skip, "rna_type", "id_data", "name", "name_full", "users",
                                      "tag", "use_fake_user", "use_extra_user"}:
            continue
        if prop.type in {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"}:
            try:
                result[key] = _simple(getattr(item, key))
            except (AttributeError, TypeError):
                pass
    return result


def material_record(material, visited=None):
    """Ordinary guard shader structure/defaults, including referenced image pixels."""
    visited = set() if visited is None else visited
    if material is None:
        return None
    def tree_record(tree):
        if tree is None or tree.as_pointer() in visited:
            return None
        visited.add(tree.as_pointer())
        nodes = []
        for node in tree.nodes:
            record = dict(name=node.name, type=node.bl_idname,
                          values=_rna_values(node, {"location", "width", "height", "dimensions", "select",
                                                   "label", "show_options", "show_preview", "show_texture"}),
                          inputs=[dict(name=s.name, identifier=s.identifier, value=_simple(s.default_value))
                                  for s in node.inputs if hasattr(s, "default_value")],
                          outputs=[dict(name=s.name, identifier=s.identifier, value=_simple(s.default_value))
                                   for s in node.outputs if hasattr(s, "default_value")])
            if getattr(node, "image", None):
                record["image"] = image_record(node.image)
            if getattr(node, "node_tree", None):
                record["group"] = tree_record(node.node_tree)
            if hasattr(node, "color_ramp"):
                record["ramp"] = [(e.position, list(e.color)) for e in node.color_ramp.elements]
            nodes.append(record)
        return dict(nodes=nodes, links=sorted((link.from_node.name, link.from_socket.identifier,
                                             link.to_node.name, link.to_socket.identifier) for link in tree.links))
    return dict(name=material.name, diffuse_color=list(getattr(material, "diffuse_color", getattr(material, "color", ()))),
                use_nodes=material.use_nodes, tree=tree_record(material.node_tree))


def scene_record(scene, target_set_ids=()):
    import bpy
    target_materials = {ts.material for ts in scene.open_painter.texture_sets if ts.id in target_set_ids}
    geometry, guards, rig = {}, {}, {}
    for obj in sorted(scene.objects, key=lambda item: item.name):
        matrix = [list(row) for row in obj.matrix_world]
        if obj.type == "MESH":
            mesh = obj.data
            geometry[obj.name] = dict(
                transform=matrix, vertices=_array_hash(_rna_array(mesh.vertices, "co", 3)),
                loops=_array_hash(_rna_array(mesh.loops, "vertex_index", dtype="int32")),
                polygons=_array_hash(_rna_array(mesh.polygons, "loop_total", dtype="int32")),
                assignments=_array_hash(_rna_array(mesh.polygons, "material_index", dtype="int32")),
                normals=_array_hash(_rna_array(mesh.vertices, "normal", 3)),
                smooth=_array_hash(_rna_array(mesh.polygons, "use_smooth", dtype="bool")),
                uvs={uv.name: _array_hash(_rna_array(uv.data, "uv", 2)) for uv in mesh.uv_layers},
                active_uv=mesh.uv_layers.active.name if mesh.uv_layers.active else None,
                modifiers=[dict(type=mod.type, values=_rna_values(mod)) for mod in obj.modifiers],
                dimensions=list(obj.dimensions), hide_render=obj.hide_render)
            for index, slot in enumerate(obj.material_slots):
                if slot.material not in target_materials:
                    guards[f"{obj.name}:{index}"] = material_record(slot.material)
        elif obj.type in {"CAMERA", "LIGHT"}:
            rig[obj.name] = dict(type=obj.type, matrix=matrix, data=_rna_values(obj.data), hide_render=obj.hide_render)
    return dict(geometry=geometry, guard_materials=guards, rig=rig,
                world=material_record(scene.world) if scene.world else None,
                render=dict(engine=scene.render.engine,
                            settings=_rna_values(scene.render, {"filepath"}),
                            image_settings=_rna_values(scene.render.image_settings),
                            engine_settings=_rna_values(scene.eevee) if hasattr(scene, "eevee") else {},
                            unit_settings=_rna_values(scene.unit_settings),
                            active_camera=scene.camera.name if scene.camera else None,
                            compositor_graph_present=bool(getattr(scene, "compositing_node_group", None)
                                                          or getattr(scene, "node_tree", None)),
                            collections={item.name: dict(hide_render=item.hide_render, hide_viewport=item.hide_viewport)
                                         for item in scene.collection.children}),
                color_management=dict(display=scene.display_settings.display_device,
                                      view=scene.view_settings.view_transform,
                                      look=scene.view_settings.look, exposure=scene.view_settings.exposure,
                                      gamma=scene.view_settings.gamma),
                blender_version=bpy.app.version_string)


def _source_images(document):
    seen = {}
    def walk(value):
        if isinstance(value, dict):
            for child in value.values():
                walk(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                walk(child)
        elif getattr(getattr(value, "bl_rna", None), "identifier", "") == "Image":
            seen[value.name] = value
    walk(document)
    return seen


def material_state(scene, config=None):
    from open_painter.authoring import document
    from open_painter.geometry.provenance import status
    config = config_for(scene, config)
    targets = config.get("target_set_ids", [ts.id for ts in scene.open_painter.texture_sets])
    sets = {}
    for ts in scene.open_painter.texture_sets:
        snapshot = document.snapshot(ts)
        sources = _source_images(snapshot)
        maps = []
        for entry in ts.baked_maps:
            maps.append(dict(kind=entry.kind, status=status(ts, entry),
                             image=image_record(entry.image) if entry.image else None))
        semantic = document.encode(snapshot, digest=True)
        sets[ts.id] = dict(id=ts.id, name=ts.name, target=ts.id in targets, resolution=int(ts.resolution),
                          semantic=semantic, semantic_sha256=stable_hash(semantic),
                          sources={name: image_record(image) for name, image in sources.items()},
                          maps=maps, assignments=[dict(object=ref.object.name if ref.object else None,
                                                      slot=ref.slot_index) for ref in ts.assignments])
    return dict(texture_sets=sets, scene=scene_record(scene, targets))


PRIMARY_VIEWS = {
    "before": [("front", "00_before.png")],
    "after_create": [("front", "01_create_front.png"), ("back", "02_create_back.png"), ("close", "03_create_close.png")],
    "after_refine_1": [("front", "04_refine1_front.png"), ("back", "05_refine1_back.png"), ("close", "06_refine1_close.png")],
    "after_refine_2": [("front", "07_refine2_front.png"), ("back", "08_refine2_back.png"), ("close", "09_refine2_close.png")],
}


def fixed_rig_check(scene, config):
    """Validate, never repair, the declared primary fixture presentation."""
    import numpy as np
    for view_layer in scene.view_layers:
        view_layer.update()
    errors = []
    cameras = [obj for obj in scene.objects if obj.type == "CAMERA"]
    lights = [obj for obj in scene.objects if obj.type == "LIGHT"]
    camera_name = config.get("camera_name", "BENCHMARK_CAMERA")
    if len(cameras) != 1 or cameras[0].name != camera_name or camera_name != "BENCHMARK_CAMERA":
        errors.append("Primary fixture requires exactly one BENCHMARK_CAMERA")
    if set(obj.name for obj in lights) != set(config.get("fixed_light_names", ["Key", "Fill", "Rim"])) or len(lights) != 3:
        errors.append("Primary fixture requires its three declared fixed lights")
    expected_engine = config.get("render_engine", "BLENDER_EEVEE_NEXT")
    if scene.render.engine != expected_engine or not scene.render.engine.startswith("BLENDER_EEVEE"):
        errors.append("Primary fixture must use the frozen Eevee renderer")
    if (scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage) != (1024, 1024, 100):
        errors.append("Primary render resolution must remain 1024 x 1024 at 100%")
    if list(config.get("render_size", [])) != [1024, 1024]:
        errors.append("Fixture metadata does not declare the fixed 1024-square image size")
    if scene.render.film_transparent:
        errors.append("Primary fixture requires an opaque background")
    # In Blender 5.2 the deprecated Scene.use_nodes can be True with no
    # compositor graph at all. Check actual graph presence and render flags.
    compositor = getattr(scene, "compositing_node_group", None) or getattr(scene, "node_tree", None)
    if compositor is not None or scene.render.use_compositing or scene.render.use_sequencer:
        errors.append("Primary fixture must disable compositor and sequencer post-processing")
    poses = config.get("camera_poses", {})
    expected_poses = {"left", "right", "front", "rear", "close"} if config.get("protocol") == "two_weapon_v1" else {"front", "back", "close"}
    if set(poses) != expected_poses:
        errors.append("Exactly these camera poses must be frozen: " + ", ".join(sorted(expected_poses)))
    for name, pose in poses.items():
        matrix = np.asarray(pose.get("matrix_world", []), dtype=np.float64)
        if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
            errors.append(f"Invalid frozen matrix for {name}")
        if pose.get("type") != "ORTHO" or not isinstance(pose.get("ortho_scale"), (float, int)) or pose["ortho_scale"] <= 0:
            errors.append(f"Invalid frozen orthographic pose for {name}")
    camera = scene.objects.get(camera_name)
    expected = poses.get(config.get("input_camera_pose", "front"))
    if camera and expected and np.asarray(expected.get("matrix_world", [])).shape == (4, 4):
        if not np.allclose(np.asarray(camera.matrix_world), expected["matrix_world"], rtol=0, atol=1e-6):
            errors.append("The current input camera transform differs from its frozen pose")
        for key in ("type", "ortho_scale", "lens"):
            if key in expected:
                actual = getattr(camera.data, key)
                if actual != expected[key] and not (isinstance(actual, (float, int)) and abs(actual - expected[key]) <= 1e-6):
                    errors.append(f"The current input camera {key} differs from its frozen pose")
    if scene.camera != camera:
        errors.append("Active scene camera differs from BENCHMARK_CAMERA")
    signature_check = dict(status="NOT_DECLARED")
    if config.get("rig_signature"):
        try:
            from prepare_fixtures import rig_signature
            actual_signature = rig_signature(scene, config)
            signature_check = dict(status="PASS" if actual_signature == config["rig_signature"] else "FAIL",
                                   actual=actual_signature, expected=config["rig_signature"])
            if signature_check["status"] != "PASS":
                errors.append("Fixed camera/light/world/color/renderer infrastructure differs from its frozen signature")
        except Exception as exc:
            signature_check = dict(status="FAIL", error=f"{type(exc).__name__}: {exc}")
            errors.append("Frozen rig signature could not be verified")
    return dict(status="PASS" if not errors else "FAIL", errors=errors,
                engine=scene.render.engine, camera=camera_name, light_names=sorted(obj.name for obj in lights),
                declared_rig_signature=config.get("rig_signature"),
                rig_signature_verification=signature_check,
                additional_guard="Checkpoint comparisons preserve exact lights/world/render settings and camera state")


def render_primary_views(context, output_dir, label, config):
    import bpy
    from mathutils import Matrix, Vector
    from view_inventory import inventory, EXTRA_POSES
    scene = context.scene
    validation = fixed_rig_check(scene, config)
    if validation["status"] != "PASS":
        return dict(status="FAILED", rig_validation=validation, views=[], errors=validation["errors"])
    poses = dict(config["camera_poses"])
    if config.get("protocol_version", 2) >= 3:
        for name, values in EXTRA_POSES.items():
            location, center = Vector(values["location"]), Vector(values["center"])
            matrix = (center-location).to_track_quat("-Z", "Y").to_matrix().to_4x4()
            matrix.translation = location
            poses[name] = dict(matrix_world=[list(row) for row in matrix], type="ORTHO", lens=50,
                               ortho_scale=values["ortho_scale"])
    capture_plan = inventory(config).get(label)
    output = Path(output_dir)
    if capture_plan is None:
        # Optional post-score probes remain outside the ten primary images.
        output = output / "supplemental_views" / safe_name(label)
        capture_plan = [(key, key + ".png") for key in ("front", "back", "close")]
    output.mkdir(parents=True, exist_ok=True)
    camera = scene.objects[config["camera_name"]]
    # Restore the original RNA transform components, not matrix_world. Assigning
    # a matrix decomposes it to Euler/quaternion and can accumulate tiny float
    # drift, which would make an exact protected-camera comparison fail.
    original_transform = {key: getattr(camera, key).copy() if hasattr(getattr(camera, key), "copy")
                          else list(getattr(camera, key)) for key in (
        "location", "rotation_euler", "rotation_quaternion", "rotation_axis_angle", "scale",
        "delta_location", "delta_rotation_euler", "delta_rotation_quaternion", "delta_scale")}
    original_rotation_mode = camera.rotation_mode
    original_camera_values = {key: getattr(camera.data, key) for key in ("type", "lens", "ortho_scale")}
    original_path = scene.render.filepath
    image_settings = scene.render.image_settings
    original_image_settings = {key: getattr(image_settings, key) for key in ("file_format", "color_mode", "color_depth")}
    started, records, errors = time.monotonic(), [], []
    try:
        image_settings.file_format, image_settings.color_mode, image_settings.color_depth = "PNG", "RGBA", "8"
        for name, filename in capture_plan:
            if time.monotonic() - started > config.get("render_phase_cap_seconds", 300):
                errors.append("Render phase cap reached between fixed poses")
                break
            pose = poses[name]
            camera.matrix_world = Matrix(pose["matrix_world"])
            for key in ("type", "lens", "ortho_scale"):
                if key in pose:
                    setattr(camera.data, key, pose[key])
            context.view_layer.update()
            destination = output / filename
            scene.render.filepath = str(destination)
            tick = time.monotonic()
            try:
                bpy.ops.render.render(write_still=True, scene=scene.name)
                records.append(dict(camera=camera.name, pose=name, path=str(destination), sha256=sha256(destination),
                                    distance="close" if name == "close" else "normal", seconds=time.monotonic() - tick))
            except Exception as exc:
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
    finally:
        camera.rotation_mode = original_rotation_mode
        for key, value in original_transform.items():
            setattr(camera, key, value)
        for key, value in original_camera_values.items():
            setattr(camera.data, key, value)
        scene.render.filepath = original_path
        for key, value in original_image_settings.items():
            setattr(image_settings, key, value)
        context.view_layer.update()
    restored = fixed_rig_check(scene, config)
    if restored["status"] != "PASS":
        errors.append("Fixed input camera/rig was not restored after evidence rendering")
    return dict(status="CAPTURED" if not errors else "FAILED", views=records, errors=errors,
                seconds=time.monotonic() - started, engine=scene.render.engine, resolution=[1024, 1024],
                rig_validation=validation, input_pose_restored=restored["status"] == "PASS",
                protocol="two_weapon_v1" if config.get("protocol") == "two_weapon_v1" else
                "five_asset_eevee_v3" if config.get("protocol_version", 2) >= 3 else "primary_fixed_eevee_one_camera_three_poses")


def render_views(context, output_dir, label, config=None, *, relight=False):
    """Render only frozen fixture cameras; never reframe per output."""
    import bpy
    config = config_for(context.scene, config)
    if not config.get("render_views", True):
        return dict(status="NOT_TESTED", reason="Render capture explicitly disabled")
    if config.get("primary_evidence"):
        if relight:
            return dict(status="NOT_SCHEDULED", reason="Primary benchmark freezes its three lights and world; alternate lighting is outside this protocol")
        return render_primary_views(context, output_dir, label, config)
    scene = context.scene
    names = [*config.get("camera_names", []), *config.get("close_camera_names", [])]
    if not names:
        names = sorted(obj.name for obj in scene.objects if obj.type == "CAMERA"
                       and obj.name.startswith(("BENCH_CAM_", "BENCH_CLOSE_")))
    if not names:
        return dict(status="BLOCKED", reason="Fixture has no declared fixed cameras")
    output = Path(output_dir) / "views" / safe_name(label)
    output.mkdir(parents=True, exist_ok=True)
    camera, path = scene.camera, scene.render.filepath
    image_settings = scene.render.image_settings
    old_format, old_mode = image_settings.file_format, image_settings.color_mode
    collection_states = {}
    if relight:
        lights = config.get("relight_collection_names", [])
        if len(lights) != 2 or any(name not in bpy.data.collections for name in lights):
            return dict(status="BLOCKED", reason="Two fixed lighting collections were not declared")
        for index, name in enumerate(lights):
            collection = bpy.data.collections[name]
            collection_states[name] = collection.hide_render
            collection.hide_render = index == 0
    started, views, errors = time.monotonic(), [], []
    try:
        image_settings.file_format, image_settings.color_mode = "PNG", "RGBA"
        for name in names:
            if time.monotonic() - started > config.get("render_phase_cap_seconds", 600):
                errors.append("Render phase cap reached between cameras; active Blender render is synchronous")
                break
            obj = scene.objects.get(name)
            if obj is None or obj.type != "CAMERA":
                errors.append(f"Declared camera missing: {name}")
                continue
            scene.camera = obj
            destination = output / (safe_name(name) + ".png")
            scene.render.filepath = str(destination)
            tick = time.monotonic()
            hidden = {}
            if "underside" in name.lower():
                for object_name in config.get("underside_hide_objects", []):
                    if object_name in scene.objects:
                        hidden[object_name] = scene.objects[object_name].hide_render
                        scene.objects[object_name].hide_render = True
            try:
                bpy.ops.render.render(write_still=True, scene=scene.name)
                views.append(dict(camera=name, path=str(destination), sha256=sha256(destination),
                                  distance="close" if name in config.get("close_camera_names", [])
                                  or name.startswith("BENCH_CLOSE_") else "normal",
                                  seconds=time.monotonic() - tick))
            except Exception as exc:
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
            finally:
                for object_name, value in hidden.items():
                    scene.objects[object_name].hide_render = value
    finally:
        scene.camera, scene.render.filepath = camera, path
        image_settings.file_format, image_settings.color_mode = old_format, old_mode
        for name, state in collection_states.items():
            bpy.data.collections[name].hide_render = state
    return dict(status="CAPTURED" if not errors else "FAILED", views=views, errors=errors,
                seconds=time.monotonic() - started, relight=relight,
                engine=scene.render.engine, resolution=[scene.render.resolution_x, scene.render.resolution_y])


def capture_channels(context, output_dir, label, config=None):
    import numpy as np
    from open_painter.constants import CHANNELS
    from open_painter.core import image_manager
    from open_painter.evaluation.evaluator import LayerEvaluator
    from open_painter.export import padding
    from open_painter.export.image_writer import write
    config = config_for(context.scene, config)
    if not config.get("capture_channels", True):
        return dict(status="NOT_TESTED", reason="Channel capture explicitly disabled")
    root = Path(output_dir) / "channels" / safe_name(label)
    root.mkdir(parents=True, exist_ok=True)
    evaluator = LayerEvaluator(context.scene)
    result, errors = {}, []
    wanted = config.get("target_set_ids", [ts.id for ts in context.scene.open_painter.texture_sets])
    for ts in context.scene.open_painter.texture_sets:
        if ts.id not in wanted and not config.get("capture_guard_set_channels", True):
            continue
        start = time.monotonic()
        entry = dict(name=ts.name, channels={}, resolution=int(ts.resolution))
        result[ts.id] = entry
        try:
            evaluator.evaluate_texture_set(ts.id, force=True)
            valid = padding.ownership(ts, int(ts.resolution)).covered
            margin = int(config.get("uv_boundary_exclusion_pixels", 1))
            # Conservative declared erosion: no wrapped samples from the opposite UV edge.
            inside = valid.copy()
            for _ in range(margin):
                previous = inside.copy()
                inside[:] = False
                inside[1:-1, 1:-1] = (previous[1:-1, 1:-1] & previous[:-2, 1:-1]
                                      & previous[2:, 1:-1] & previous[1:-1, :-2] & previous[1:-1, 2:])
            validity_path = root / f"{safe_name(ts.id)}_valid.npy"
            np.save(validity_path, inside, allow_pickle=False)
            entry["valid_samples"] = dict(path=str(validity_path), count=int(inside.sum()),
                                          sha256=sha256(validity_path),
                                          excluded_boundary_pixels=margin, decoded=_array_hash(inside))
            for channel in config.get("channels", CHANNELS):
                image = image_manager.output(ts, channel).image
                values = np.asarray(image_manager.pixels(image), dtype="<f4")
                basename = f"{safe_name(ts.id)}_{channel}"
                raw_path, png_path = root / (basename + ".npy"), root / (basename + ".png")
                np.save(raw_path, values, allow_pickle=False)
                finite = bool(np.isfinite(values).all())
                record = dict(path=str(raw_path), sha256=sha256(raw_path), preview=None, decoded=_array_hash(values), finite=finite)
                entry["channels"][channel] = record
                if not finite:
                    record.update(status="FAILED", error="Evaluator returned non-finite channel samples",
                                  nonfinite_samples=int((~np.isfinite(values)).sum()),
                                  preview_status="NOT_WRITTEN_NONFINITE")
                    # Keep the exact failing float buffer. Converting NaN/Inf to
                    # PNG would silently clamp it into misleading visual evidence.
                    errors.append(dict(texture_set_id=ts.id, channel=channel, error=record["error"]))
                    continue
                write(png_path, values[::-1], "PNG", 16, channel in {"BASE_COLOR", "EMISSION"})
                record.update(preview=str(png_path), preview_sha256=sha256(png_path), status="CAPTURED")
            if config.get("protocol_version", 2) >= 3:
                # This is the evaluated layer mask before layer opacity. Keeping
                # those separate makes "halve opacity" distinct from area edits.
                from open_painter.evaluation.mask_eval import evaluate_masks
                from open_painter.authoring import document
                from revision_measurements import sample_geometry, coverage, effect_control, measurement_policy
                policy = measurement_policy(config)
                entry["measurement_policy"] = policy
                authored = document.encode(document.snapshot(ts), digest=True)
                authored_layers = {item["id"]: item for item in authored.get("layers", [])}
                entry["effect_masks"] = {}
                samples = sample_geometry(ts)
                sample_path = root / f"{safe_name(ts.id)}_surface_samples.npz"
                np.savez_compressed(sample_path, **samples)
                entry["surface_samples"] = dict(path=str(sample_path), sha256=sha256(sample_path),
                    method="Four fixed interior samples per assigned triangle; every triangle's surface area remains in denominator",
                    area_units="WORLD_UNITS_SQUARED", convergence_status="NOT_ESTABLISHED",
                    relative_height_frame="Each object's assigned triangle Z bounds; not a universal band-placement frame")
                n = int(ts.resolution)
                for layer in ts.layers:
                    if not layer.masks:
                        continue
                    cache = {}
                    values = np.concatenate([evaluate_masks(ts, layer, n, y, min(y+128, n), cache, evaluator.geometry_cache)
                                             for y in range(0, n, 128)]).astype("<f4")
                    path = root / f"{safe_name(ts.id)}_{safe_name(layer.id)}_effect_mask.npy"
                    np.save(path, values, allow_pickle=False)
                    finite = bool(np.isfinite(values).all())
                    native = authored_layers.get(layer.id, {})
                    entry["effect_masks"][layer.id] = dict(layer_name=layer.name, path=str(path),
                        sha256=sha256(path), decoded=_array_hash(values), finite=finite,
                        layer_opacity=float(layer.opacity), source_mask=native.get("masks", []),
                        source_signature=stable_hash(native.get("masks", [])),
                        surface_coverage=coverage(values, samples,threshold=policy["mask_threshold"],
                            invalid_area_tolerance=policy["invalid_uv_area_max_fraction"]),
                        meaning="Evaluated whole layer mask before layer opacity; may be inverse coating coverage, not semantic effect occupancy")
                    control, source_field = effect_control(native,values)
                    control["source_signature"] = stable_hash(control.pop("source_document"))
                    if source_field is not None:
                        source_path = path if control["representation"] == "GENERIC_LAYER_MASK" else root / f"{safe_name(ts.id)}_{safe_name(layer.id)}_effect_source.npy"
                        if source_path != path:
                            np.save(source_path,source_field,allow_pickle=False)
                        control["source_field"] = dict(path=str(source_path),sha256=sha256(source_path),decoded=_array_hash(source_field),
                            finite=bool(np.isfinite(source_field).all()),surface_coverage=coverage(source_field,samples,
                                threshold=policy["mask_threshold"],invalid_area_tolerance=policy["invalid_uv_area_max_fraction"]))
                    entry["effect_masks"][layer.id]["effect_control"] = control
                    if not finite:
                        errors.append(dict(texture_set_id=ts.id, layer_id=layer.id, error="Nonfinite effect mask"))
            entry["status"] = "FAILED" if any(item["finite"] is False for item in entry["channels"].values()) else "CAPTURED"
        except Exception as exc:
            entry["status"], entry["error"] = "FAILED", f"{type(exc).__name__}: {exc}"
            errors.append(dict(texture_set_id=ts.id, error=entry["error"]))
        entry["seconds"] = time.monotonic() - start
    return dict(status=("CAPTURED" if result else "BLOCKED") if not errors else "FAILED", evaluator="Open Painter LayerEvaluator CPU",
                evaluator_implementation=type(evaluator).__module__ + "." + type(evaluator).__qualname__,
                origin="bottom-left", precision="float32", sets=result, errors=errors)


def capture_checkpoint(context, output_dir, label, *, config=None):
    started = time.monotonic()
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    result = dict(schema_version=1, label=label, state=material_state(context.scene, config))
    # Evaluate first so frozen renders cannot conceal a pending/failed material.
    result["channels"] = capture_channels(context, output, label, config)
    result["views"] = render_views(context, output, label, config)
    from open_painter.evaluation import shader_preview
    result["actual_preview_backends"] = {ts.id: dict(requested=getattr(ts, "preview_backend", "LIVE"),
        observed=shader_preview.diagnostics(context.scene, ts)) for ts in context.scene.open_painter.texture_sets}
    result["ui_layer_mask_views"] = dict(status="NOT_TESTED", reason="Requires visible UI capture; structured layer/mask state is included")
    result["placement"] = dict(status="NOT_TESTED", reason="Requires frozen evaluator-only region annotations and measurements")
    result["state_path"] = str(output / f"{safe_name(label)}_state.json")
    effective = config_for(context.scene, config)
    if effective.get("protocol_version", 2) >= 3 and label in {"after_refine_1", "after_refine_2"}:
        import numpy as np
        from revision_measurements import compare, measurement_policy
        policy = measurement_policy(effective)
        previous_label = "after_create" if label == "after_refine_1" else "after_refine_1"
        previous_path = output / (previous_label + "_state.json")
        comparisons = dict(previous_checkpoint=previous_path.name, texture_sets={}, independent_artistic_review="PENDING",
            measurement_policy=policy, no_edit_calibration=effective.get("no_edit_calibration",{"status":"NOT_SUPPLIED"}),
            area_estimator_limitation="Four fixed points per triangle; no convergence bound for fine chips or thin bands")
        previous = json.loads(previous_path.read_text(encoding="utf-8")) if previous_path.is_file() else {}
        for ts_id, current_set in result["channels"].get("sets", {}).items():
            previous_set = previous.get("channels", {}).get("sets", {}).get(ts_id, {})
            old_masks, masks = previous_set.get("effect_masks", {}), current_set.get("effect_masks", {})
            measured = dict(added_effect_ids=sorted(set(masks)-set(old_masks)), removed_effect_ids=sorted(set(old_masks)-set(masks)), effects={})
            sample_path = current_set.get("surface_samples", {}).get("path")
            if sample_path:
                with np.load(sample_path, allow_pickle=False) as loaded:
                    samples = {key: loaded[key] for key in loaded.files}
                for layer_id in sorted(set(masks)&set(old_masks)):
                    a,b = old_masks[layer_id],masks[layer_id]
                    first,last = a.get("effect_control",{}),b.get("effect_control",{})
                    if (first.get("status") != "MEASURED" or last.get("status") != "MEASURED"
                            or first.get("representation") != last.get("representation")
                            or first.get("opacity_control_path") != last.get("opacity_control_path")):
                        measured["effects"][layer_id] = dict(status="NOT_MEASURED",
                            reason="A stable typed source field and opacity control are required; composite or historical layer-mask evidence cannot substitute",
                            before=first,after=last)
                        continue
                    semantic = last.get("semantic")
                    minimum = policy["minimum_initial_moss_area_fraction"] if semantic == "MOSS" else 0
                    observation = compare(np.load(first["source_field"]["path"],allow_pickle=False),
                        np.load(last["source_field"]["path"],allow_pickle=False),samples,threshold=policy["mask_threshold"],
                        minimum_initial_fraction=minimum,tolerance=policy["mask_absolute_tolerance"],
                        invalid_area_tolerance=policy["invalid_uv_area_max_fraction"])
                    observation.update(source_signature_equal=first["source_signature"]==last["source_signature"],
                        before_opacity=first["opacity"],after_opacity=last["opacity"],
                        opacity_ratio=last["opacity"]/first["opacity"] if first["opacity"]>0 else None,
                        representation=last["representation"],semantic=semantic,opacity_control_path=last["opacity_control_path"],
                        semantic_assessment=last.get("semantic_assessment"))
                    measured["effects"][layer_id] = observation
            comparisons["texture_sets"][ts_id] = measured
        result["revision_measurements"] = comparisons
    if effective.get("annotations_manifest"):
        try:
            from annotations import evaluate_checkpoint
            result["placement"] = evaluate_checkpoint(result, effective,
                fixture_sha256=effective.get("fixture_sha256"))
        except Exception as exc:
            result["placement"] = dict(status="BLOCKED", reason=f"Annotation evaluation failed: {type(exc).__name__}: {exc}")
    result["seconds"] = time.monotonic() - started
    phases = [result[key]["status"] for key in ("channels", "views")]
    result["status"] = "FAILED" if "FAILED" in phases else "CAPTURED" if all(value == "CAPTURED" for value in phases) else "PARTIAL"
    write_json(result["state_path"], result)
    return result


def compare_buffers(before, after, *, valid=None, tolerance=1 / 255, minimum_fraction=.995):
    import numpy as np
    before, after = np.asarray(before), np.asarray(after)
    if before.shape != after.shape:
        return dict(status="FAIL", reason="Buffer shape changed", before=list(before.shape), after=list(after.shape))
    delta = np.abs(before.astype(np.float64) - after.astype(np.float64))
    finite = np.isfinite(before) & np.isfinite(after)
    if valid is not None:
        delta, finite = delta[valid], finite[valid]
    if not delta.size:
        return dict(status="NOT_TESTED", reason="No protected valid samples")
    per_sample = np.max(delta, axis=-1) if delta.ndim > 1 else delta
    within = (per_sample <= tolerance) & (np.all(finite, axis=-1) if finite.ndim > 1 else finite)
    fraction = float(within.mean())
    return dict(status="PASS" if fraction >= minimum_fraction else "FAIL",
                exact=bool(np.array_equal(before, after)), max_error=float(np.nanmax(delta)) if np.isfinite(delta).any() else None,
                within_fraction=fraction, samples=int(within.size), tolerance=tolerance,
                required_fraction=minimum_fraction, visible_unauthorized_change="REVIEW_REQUIRED")


def compare_checkpoints(before, after, *, protected_set_ids=None, exact_channels=False, compare_scene=True):
    """No automatic semantic-region selection: caller must declare protected sets."""
    import numpy as np
    before = json.loads(Path(before).read_text(encoding="utf-8")) if isinstance(before, (str, Path)) else before
    after = json.loads(Path(after).read_text(encoding="utf-8")) if isinstance(after, (str, Path)) else after
    a, b = before["state"], after["state"]
    ids = list(protected_set_ids) if protected_set_ids is not None else list(a["texture_sets"])
    checks = {}
    if compare_scene:
        for key in ("geometry", "guard_materials", "rig", "world", "render", "color_management"):
            if key not in a["scene"] or key not in b["scene"]:
                checks[key] = dict(status="NOT_TESTED", reason="Legacy checkpoint lacks this scene guard")
                continue
            checks[key] = dict(status="PASS" if stable_hash(a["scene"][key]) == stable_hash(b["scene"][key]) else "FAIL")
    for identifier in ids:
        old, new = a["texture_sets"].get(identifier), b["texture_sets"].get(identifier)
        record = {}
        checks[identifier] = record
        if old is None or new is None:
            record["presence"] = dict(status="FAIL", reason="Protected texture set missing")
            continue
        for key in ("semantic", "sources", "assignments", "maps"):
            # Packing/source metadata can change on save; compare decoded pixels + meaning.
            lhs, rhs = old[key], new[key]
            if key == "sources":
                lhs = {name: {k: item[k] for k in ("decoded", "colorspace", "size")} for name, item in lhs.items()}
                rhs = {name: {k: item[k] for k in ("decoded", "colorspace", "size")} for name, item in rhs.items()}
            elif key == "maps":
                def map_values(items):
                    return [{"kind": item["kind"], "status": item["status"],
                             "image": {k: item["image"][k] for k in ("decoded", "colorspace", "size")}
                             if item["image"] else None} for item in items]
                lhs, rhs = map_values(lhs), map_values(rhs)
            record[key] = dict(status="PASS" if stable_hash(lhs) == stable_hash(rhs) else "FAIL")
        ca = before.get("channels", {}).get("sets", {}).get(identifier, {})
        cb = after.get("channels", {}).get("sets", {}).get(identifier, {})
        if not ca.get("channels") or not cb.get("channels"):
            record["channels"] = dict(status="NOT_TESTED", reason="Evaluated channel evidence absent")
            continue
        valid = np.load(ca["valid_samples"]["path"], allow_pickle=False)
        valid &= np.load(cb["valid_samples"]["path"], allow_pickle=False)
        record["channels"] = {}
        for channel, old_channel in ca["channels"].items():
            new_channel = cb["channels"].get(channel)
            if new_channel is None:
                record["channels"][channel] = dict(status="FAIL", reason="Channel absent")
                continue
            record["channels"][channel] = compare_buffers(np.load(old_channel["path"], allow_pickle=False),
                np.load(new_channel["path"], allow_pickle=False), valid=valid,
                tolerance=0 if exact_channels else 1/255, minimum_fraction=1 if exact_channels else .995)
    def statuses(value):
        if isinstance(value, dict):
            if "status" in value:
                yield value["status"]
            for item in value.values():
                yield from statuses(item)
    found = list(statuses(checks))
    status = "FAIL" if "FAIL" in found else "NOT_TESTED" if "NOT_TESTED" in found else "PASS"
    return dict(status=status, checks=checks, protected_set_ids=ids)
