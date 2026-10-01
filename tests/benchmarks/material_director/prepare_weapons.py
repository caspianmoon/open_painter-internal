"""Prepare source weapon meshes once; never invoke AI or change physical scale."""
import json
import sys
from dataclasses import asdict
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(ROOT / "src"), str(HERE)]
import open_painter
from open_painter.baking import doctor
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import shader_preview
from prepare_fixtures import (
    _bounds, _isolate_bad_uv_faces, _neutralize, object_snapshot, rig_signature,
)
from runtime import sha256, write_json


def prepare(source_root, output, rig):
    open_painter.register()
    manifest = json.loads((source_root / "manifest.json").read_text(encoding="utf-8"))
    rig_hash = sha256(rig)
    for asset in manifest["assets"]:
        destination = output / (asset["folder"] + ".blend")
        if destination.exists():
            raise FileExistsError(destination)
        entry = next(f for f in asset["files"] if f["path"].endswith(".blend"))
        source = source_root / asset["folder"] / entry["path"]
        assert sha256(source) == entry["sha256"], "Source checksum mismatch"
        bpy.ops.wm.open_mainfile(filepath=str(rig), load_ui=False, use_scripts=False)
        scene = bpy.context.scene
        config = json.loads(scene["md_benchmark_config"])
        collection = bpy.data.collections["TEST_MODEL"]
        # Append objects as data, link only meshes. Source scenes, cameras,
        # lights and World settings are never linked into the test environment.
        with bpy.data.libraries.load(str(source), link=False) as (available, loaded):
            loaded.objects = list(available.objects)
        objects = [o for o in loaded.objects if o and o.type == "MESH" and o.data.polygons]
        if not objects:
            raise ValueError("No source meshes")
        audit = dict(asset=asset, source_sha256=sha256(source), rig_sha256=rig_hash,
                     status="PREPARING", repairs=[], source_objects=[], prepared_objects=[],
                     scale_factor=1, source_scripts_executed=False, cycles_used=False)
        for index, obj in enumerate(objects):
            collection.objects.link(obj)
            bpy.context.view_layer.update()
            audit["source_objects"].append(object_snapshot(obj))
            if obj.modifiers or obj.constraints or obj.parent or obj.animation_data:
                raise ValueError("Source has unevaluated dependencies; preparation needs explicit review")
            obj.name = f"Part_{index + 1:02}"
            obj.data.name = f"Mesh_{index + 1:02}"
            obj.hide_render = obj.hide_viewport = False
            obj.hide_set(False)
            obj.vertex_groups.clear()
            for key in list(obj.keys()):
                del obj[key]
            for layer in list(obj.data.color_attributes):
                obj.data.color_attributes.remove(layer)
            if not obj.data.uv_layers:
                obj.data.uv_layers.new(name="UVMap")
                audit["repairs"].append(dict(object=obj.name, reason="Missing source UV map"))
        for obj in loaded.objects:
            if obj and obj not in objects:
                bpy.data.objects.remove(obj, do_unlink=True)
        bindings, audit["appearance_removed"] = _neutralize(objects)
        for index, refs in enumerate(bindings.values()):
            ts = sets.create(scene, f"Slot_{index + 1:02}", 1024)
            for obj, slot in refs:
                obj.material_slots[slot].material.name = f"Neutral_{index + 1:02}"
                sets.assign(scene, ts, obj, slot_index=slot)
                refs_original = ts.assignments[-1].original_material
                if refs_original:
                    refs_original.name = f"Neutral_{index + 1:02}"
            ts.bake.selected_to_active = False
        # Bake Doctor examines real UV area, tile range and overlapping charts.
        # Unwrap once only if that diagnosis demonstrates a UV defect.
        diagnoses = [doctor.examine(ts, "FULL") for ts in scene.open_painter.texture_sets]
        audit["uv_before"] = [[asdict(f) for f in d.findings] for d in diagnoses]
        if audit["repairs"] or any(f.check == "uvs" and f.severity in {"ERROR", "WARNING"}
                                    for d in diagnoses for f in d.findings):
            bpy.ops.object.select_all(action="DESELECT")
            for obj in objects:
                obj.select_set(True)
            bpy.context.view_layer.objects.active = objects[0]
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.uv.smart_project(angle_limit=1.1519173, island_margin=.015)
            bpy.ops.object.mode_set(mode="OBJECT")
            audit["repairs"].append(dict(operation="smart_project", island_margin=.015,
                                        reason="UV diagnostic defects", geometry_changed=False))
        diagnoses = [doctor.examine(ts, "FULL") for ts in scene.open_painter.texture_sets]
        for ts, diagnosis in zip(scene.open_painter.texture_sets, diagnoses):
            remaining = [f for f in diagnosis.findings if f.check == "uvs"
                         and f.severity in {"ERROR", "WARNING"}]
            if remaining:
                repaired = _isolate_bad_uv_faces(ts, remaining)
                audit["repairs"].append(dict(operation="isolate_defective_uv_faces", faces=repaired))
        diagnoses = [doctor.examine(ts, "FULL") for ts in scene.open_painter.texture_sets]
        audit["uv_after"] = [[asdict(f) for f in d.findings] for d in diagnoses]
        defects = [asdict(f) for d in diagnoses for f in d.findings
                   if f.check == "uvs" and f.severity in {"ERROR", "WARNING"}]
        zero_area = []
        for finding in list(defects):
            obj = bpy.data.objects.get(finding["object_name"])
            if (finding["message"] == "Degenerate geometry or UV triangles" and obj
                    and finding["faces"]
                    and all(obj.data.polygons[i].area < 1e-12 for i in finding["faces"])):
                zero_area.append(finding)
                defects.remove(finding)
        audit["preserved_zero_area_source_faces"] = zero_area
        if defects:
            write_json(destination.with_suffix(".json"), audit)
            raise ValueError("UV verification failed after preparation: " + repr(defects))
        bpy.context.view_layer.update()
        low, high = _bounds(objects)
        center = (low + high) / 2
        extent = max(high - low)
        audit["bounds"] = [list(low), list(high)]
        # Both imported weapons point down world -Y, with world Z up.
        # Fit the camera to source bounds; never normalize or translate geometry.
        camera = scene.camera
        up = Vector((0, 0, 1))
        poses = {}
        directions = dict(left=(1, 0, .12), right=(-1, 0, .12), front=(0, -1, .08),
                          rear=(0, 1, .08), close=(1, -.35, .2))
        for name, direction in directions.items():
            location = center + Vector(direction).normalized() * max(2, extent * 3)
            camera.location = location
            forward = (center - location).normalized()
            right = forward.cross(up).normalized()
            camera.rotation_euler = Matrix((right, right.cross(forward), -forward)).transposed().to_euler()
            bpy.context.view_layer.update()
            poses[name] = dict(matrix_world=[list(row) for row in camera.matrix_world],
                               type="ORTHO", lens=50, ortho_scale=extent * (.60 if name == "close" else 1.18))
        config.update(protocol="two_weapon_v1", protocol_version=2, camera_poses=poses,
                      input_camera_pose="left", target_set_ids=[ts.id for ts in scene.open_painter.texture_sets],
                      source_scale_preserved=True, source_appearance_reused=False,
                      semantic_annotations_supplied=False)
        camera.matrix_world = Matrix(poses["left"]["matrix_world"])
        camera.data.ortho_scale = poses["left"]["ortho_scale"]
        bpy.context.view_layer.update()
        config["rig_signature"] = rig_signature(scene, config)
        scene["md_benchmark_config"] = json.dumps(config)
        bpy.ops.object.select_all(action="DESELECT")
        for obj in objects:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = objects[0]
        scene.open_painter.active_texture_set = 0
        for ts in scene.open_painter.texture_sets:
            shader_preview.tick(scene, ts)
        audit["prepared_objects"] = [object_snapshot(obj) for obj in objects]
        for before, after in zip(audit["source_objects"], audit["prepared_objects"]):
            for key in ("geometry_sha256", "transform_sha256", "material_partition_sha256"):
                assert before[key] == after[key], key + " changed during preparation"
        audit.update(status="READY", config=config, mesh_maps="Not prebaked; ordinary visual context and live geometry tools available")
        bpy.ops.wm.save_as_mainfile(filepath=str(destination), check_existing=False)
        audit["sha256"] = sha256(destination)
        write_json(destination.with_suffix(".json"), audit)
        print("WEAPON_PREPARED", asset["folder"], audit["bounds"], flush=True)


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:]
    prepare(*(Path(arg).resolve() for arg in args))
