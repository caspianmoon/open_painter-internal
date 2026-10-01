"""Post-session persistence, actual export, and plain-PBR reconstruction evidence.

Offline subprocess (after the provider process has exited):
  blender --background --factory-startup --disable-autoexec --python verify_final.py -- \
      --output SESSION_DIRECTORY [--skip-renders] [--skip-export] [--skip-4k]

Use a fresh empty CODEX_HOME. This script never deletes an account/cache and never
calls a provider. Renders and exported maps are evidence for reviewers; successful
file writing is not treated as proof of appearance fidelity or engine validation.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import resource
import sys
import time
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PRODUCT_ROOT = Path(os.environ.get("OP_MD_PRODUCT_ROOT", ROOT)).resolve()
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from capture_evidence import (capture_checkpoint, capture_channels, compare_checkpoints,
                              config_for, material_state, render_views, safe_name, sha256,
                              write_json)


def _pack_sources(scene):
    from capture_evidence import _source_images
    from open_painter.authoring.document import snapshot
    from open_painter.core.image_manager import pack_image
    errors = []
    for ts in scene.open_painter.texture_sets:
        images = _source_images(snapshot(ts))
        images.update({entry.image.name: entry.image for entry in ts.baked_maps if entry.image})
        for image in images.values():
            try:
                pack_image(image)
            except Exception as exc:
                errors.append(dict(image=image.name, error=f"{type(exc).__name__}: {exc}"))
    return errors


def _resolve_control(scene, spec):
    """Declared ordinary RNA controls only; no eval, Python snippets, or proposals."""
    ts = next(item for item in scene.open_painter.texture_sets if item.id == spec["texture_set_id"])
    layer = next(item for item in ts.layers if item.id == spec["layer_id"])
    if spec.get("mask_id"):
        owner = next(item for item in layer.masks if item.id == spec["mask_id"])
    elif spec.get("channel"):
        owner = next(item for item in layer.channels if item.channel == spec["channel"])
    else:
        owner = layer
    field = spec["field"]
    prop = owner.bl_rna.properties.get(field)
    if prop is None or prop.is_readonly or prop.type not in {"BOOLEAN", "FLOAT", "INT", "ENUM"}:
        raise ValueError("Probe requires a writable scalar/vector ordinary layer/channel/mask control")
    return ts, owner, field


def editability_probes(context, output_dir, config=None):
    """Mutate one declared ordinary control at a time, capture, restore in finally."""
    from open_painter.authoring import document
    from open_painter.evaluation.shader_preview import authoring_batch
    config = config_for(context.scene, config)
    probes = config.get("editability_probes", [])
    if not probes:
        return dict(status="NOT_TESTED", reason="Reviewer must map required components to actual layer/channel/mask IDs; names are not proof")
    base = material_state(context.scene, config)
    results = []
    for index, spec in enumerate(probes):
        result = dict(control=spec)
        ts = desired = None
        try:
            ts, owner, field = _resolve_control(context.scene, spec)
            desired = document.snapshot(ts)
            original = getattr(owner, field)
            result["before_value"] = list(original) if getattr(owner.bl_rna.properties[field], "is_array", False) else original
            with authoring_batch(ts):
                setattr(owner, field, spec["value"])
            captured = capture_checkpoint(context, output_dir, f"probe_{index + 1}", config=config)
            result["checkpoint"] = captured["state_path"]
            result["status"] = "CAPTURED"
            result["independence"] = "REVIEW_REQUIRED"
            if spec.get("protected_set_ids"):
                final = Path(output_dir) / "after_refine_2_state.json"
                result["preservation"] = compare_checkpoints(final, captured,
                    protected_set_ids=spec["protected_set_ids"], exact_channels=True)
        except Exception as exc:
            result.update(status="BLOCKED", error=f"{type(exc).__name__}: {exc}")
        finally:
            if ts is not None and desired is not None:
                with authoring_batch(ts):
                    document.apply(ts, desired)
            result["restored"] = material_state(context.scene, config) == base
        results.append(result)
    return dict(status="FAIL" if any(not item["restored"] for item in results) else "CAPTURED", probes=results)


def undo_check(context, output_dir, config=None):
    """Public Undo Last AI Change; compare every set and all decoded source pixels."""
    import bpy
    from open_painter.authoring.transactions import history
    config = config_for(context.scene, config)
    reference = Path(output_dir) / "after_refine_1_state.json"
    if not reference.exists():
        return dict(status="NOT_TESTED", reason="Refine 1 checkpoint absent")
    receipt_path = Path(output_dir) / "refine_2_receipt.json"
    if receipt_path.exists() and not json.loads(receipt_path.read_text(encoding="utf-8")):
        final_reference = Path(output_dir) / "after_refine_2_state.json"
        comparison = compare_checkpoints(reference, final_reference, exact_channels=True) if final_reference.exists() else None
        return dict(status="NOT_TESTED", reason="Refine 2 committed no transaction; Undo would reverse an earlier instruction",
                    refine_2_unchanged=comparison, operator_executed=False,
                    no_op_acceptance="REVIEW_REQUIRED: an unchanged result may be appropriate or an unmet refinement")
    target_ids = config.get("target_set_ids", [ts.id for ts in context.scene.open_painter.texture_sets])
    candidates = [(index, ts) for index, ts in enumerate(context.scene.open_painter.texture_sets)
                  if ts.id in target_ids and any(not item.get("reverted") for item in history(ts))]
    if not candidates:
        return dict(status="NOT_TESTED", reason="No committed AI transaction to undo")
    context.scene.open_painter.active_texture_set = candidates[0][0]
    latest = next(item for item in reversed(history(candidates[0][1])) if not item.get("reverted"))
    started = time.monotonic()
    response = bpy.ops.open_painter.director_undo()
    if "FINISHED" not in response:
        return dict(status="FAIL", reason="Public Undo Last AI Change operator failed", operator_result=sorted(response))
    checkpoint = capture_checkpoint(context, output_dir, "after_undo", config=config)
    comparison = compare_checkpoints(reference, checkpoint, exact_channels=True)
    return dict(status=comparison["status"], comparison=comparison,
                grouped=bool(latest.get("scene_operation")), affected_sets=latest.get("scene_targets", [candidates[0][1].id]),
                seconds=time.monotonic() - started)


def export_final(context, output_dir, config=None, *, resolution=2048):
    """Actual production exporter with separate PNG16 OpenGL PBR channels."""
    from open_painter.export.exporter import export, definitions_for
    config = config_for(context.scene, config)
    ids = config.get("target_set_ids", [ts.id for ts in context.scene.open_painter.texture_sets])
    folder = Path(output_dir) / ("export" if resolution == 2048 else f"export_{resolution}")
    result = dict(status="CAPTURED", preset="BLENDER", resolution=resolution, format="PNG", bit_depth=16,
                  normal_convention="OpenGL +Y tangent space; authored height combined into normal by exporter",
                  colorspace={"BaseColor": "sRGB", "Emission": "sRGB", "other": "Non-Color"},
                  packing="Separate channels", alpha="separate Opacity", padding="AUTOMATIC",
                  engine_validation={"Unreal": "NOT_TESTED", "Unity": "NOT_TESTED", "Godot": "NOT_TESTED"}, sets={})
    for ts in context.scene.open_painter.texture_sets:
        if ts.id not in ids:
            continue
        settings = SimpleNamespace(directory=str(folder / safe_name(ts.id)), resolution=str(resolution),
            preset="BLENDER", file_format="PNG", bit_depth="16", prefix="", suffix="",
            padding_mode="AUTOMATIC", padding_pixels=16, last_report="", last_report_detail="")
        started = time.monotonic()
        entry = dict(name=ts.name, source_resolution=int(ts.resolution), files={},
                     assignments=[dict(object=ref.object.name, slot=ref.slot_index) for ref in ts.assignments if ref.object],
                     surface={key: getattr(ts, key) for key in ("anisotropy", "coat_weight", "coat_roughness", "height_distance")},
                     preview_ao=ts.preview_ao)
        result["sets"][ts.id] = entry
        try:
            paths = export(context, ts, settings, overwrite=True)
            for (label, channel), path in zip(definitions_for(settings), paths):
                entry["files"][channel] = dict(path=str(path), sha256=sha256(path), label=label)
            entry["status"] = "EXPORTED"
            entry["padding_report"] = json.loads(settings.last_report_detail or "{}")
        except Exception as exc:
            entry.update(status="FAILED", error=f"{type(exc).__name__}: {exc}")
            result["status"] = "FAILED"
        entry["seconds"] = time.monotonic() - started
    if not result["sets"]:
        result.update(status="BLOCKED", reason="No declared target texture sets available for export")
    write_json(folder / "manifest.json", result)
    return result


def reconstruct(context, output_dir, export_manifest, config=None):
    """A new Scene with copied neutral geometry and plain PBR nodes only.

    Written via libraries.write with only this Scene as root, so the file has no
    Open Painter scene, layer stack, managed material graph, or request-cache path.
    """
    import bpy
    config = config_for(context.scene, config)
    if any(item["status"] != "EXPORTED" for item in export_manifest["sets"].values()):
        return dict(status="BLOCKED", reason="Export did not complete for every target set")
    source = context.scene
    destination = bpy.data.scenes.new("Benchmark plain PBR reconstruction")
    copies, material_copies, images = {}, {}, []
    world = source.world.copy() if source.world else None
    destination.world = world
    destination.render.engine = source.render.engine
    for name in ("resolution_x", "resolution_y", "resolution_percentage", "film_transparent", "pixel_aspect_x", "pixel_aspect_y"):
        setattr(destination.render, name, getattr(source.render, name))
    for name in ("view_transform", "look", "exposure", "gamma"):
        setattr(destination.view_settings, name, getattr(source.view_settings, name))
    destination.display_settings.display_device = source.display_settings.display_device
    if hasattr(source, "cycles"):
        destination.cycles.samples = source.cycles.samples
        destination.cycles.use_denoising = source.cycles.use_denoising
    lighting = {}
    for name in config.get("relight_collection_names", []):
        collection = bpy.data.collections.new("REIMPORT_" + name)
        destination.collection.children.link(collection)
        collection.hide_render = bpy.data.collections[name].hide_render
        lighting[name] = collection
    for obj in source.objects:
        duplicate = obj.copy()
        if obj.data:
            duplicate.data = obj.data.copy()
        collection = next((lighting[item.name] for item in obj.users_collection if item.name in lighting), destination.collection)
        collection.objects.link(duplicate)
        copies[obj.name] = duplicate
        if obj.type == "MESH":
            for index, material in enumerate(obj.data.materials):
                if material:
                    if material.name not in material_copies:
                        material_copies[material.name] = material.copy()
                    copied = material_copies[material.name]
                    duplicate.data.materials[index] = copied
    for name, duplicate in copies.items():
        parent = source.objects[name].parent
        if parent:
            duplicate.parent = copies.get(parent.name)
    if source.camera:
        destination.camera = copies[source.camera.name]
    for identifier, record in export_manifest["sets"].items():
        material = bpy.data.materials.new("Plain PBR " + record["name"])
        material.use_nodes = True
        nodes, links = material.node_tree.nodes, material.node_tree.links
        nodes.clear()
        output, shader = nodes.new("ShaderNodeOutputMaterial"), nodes.new("ShaderNodeBsdfPrincipled")
        links.new(shader.outputs["BSDF"], output.inputs["Surface"])
        for key, socket in (("anisotropy", "Anisotropic"), ("coat_weight", "Coat Weight"), ("coat_roughness", "Coat Roughness")):
            shader.inputs[socket].default_value = record["surface"][key]
        shader.inputs["Emission Strength"].default_value = 1
        tangent = nodes.new("ShaderNodeTangent")
        tangent.direction_type = "UV_MAP"
        links.new(tangent.outputs["Tangent"], shader.inputs["Tangent"])
        textures = {}
        for channel, item in record["files"].items():
            image = bpy.data.images.load(item["path"], check_existing=False)
            image.colorspace_settings.name = "sRGB" if channel in {"BASE_COLOR", "EMISSION"} else "Non-Color"
            image.pack()
            image.filepath_raw = "//export/" + safe_name(identifier) + "/" + Path(item["path"]).name
            images.append(image)
            node = nodes.new("ShaderNodeTexImage")
            node.name, node.image = channel, image
            node.interpolation, node.extension = "Linear", "EXTEND"
            textures[channel] = node
        for channel, socket in (("BASE_COLOR", "Base Color"), ("ROUGHNESS", "Roughness"),
                                ("METALLIC", "Metallic"), ("EMISSION", "Emission Color"), ("OPACITY", "Alpha")):
            links.new(textures[channel].outputs["Color"], shader.inputs[socket])
        normal = nodes.new("ShaderNodeNormalMap")
        normal.space = "TANGENT"
        links.new(textures["NORMAL"].outputs["Color"], normal.inputs["Color"])
        links.new(normal.outputs["Normal"], shader.inputs["Normal"])
        # Match the product's optional AO preview multiply only in its applicable
        # non-indirect renderers. AO is not inserted into physically lit Cycles.
        if record["preview_ao"] and source.render.engine != "CYCLES":
            ao = nodes.new("ShaderNodeMixRGB")
            ao.blend_type, ao.inputs[0].default_value = "MULTIPLY", 1
            links.new(textures["BASE_COLOR"].outputs["Color"], ao.inputs[1])
            links.new(textures["AO"].outputs["Color"], ao.inputs[2])
            links.new(ao.outputs[0], shader.inputs["Base Color"])
        for assignment in record["assignments"]:
            copies[assignment["object"]].data.materials[assignment["slot"]] = material
    # Shared object names receive Blender suffixes; fixed-camera mapping stays explicit.
    reconstructed_config = dict(config, camera_names=[copies[name].name for name in config.get("camera_names", [])],
        close_camera_names=[copies[name].name for name in config.get("close_camera_names", [])],
        underside_hide_objects=[copies[name].name for name in config.get("underside_hide_objects", []) if name in copies],
        relight_collection_names=[lighting[name].name for name in config.get("relight_collection_names", [])])
    destination["md_benchmark_config"] = json.dumps(reconstructed_config)
    if destination.open_painter.texture_sets:
        raise RuntimeError("Independent reconstruction unexpectedly has Open Painter texture sets")
    for obj in destination.objects:
        if obj.type == "MESH":
            for material in obj.data.materials:
                if material and material.get("open_painter_managed"):
                    raise RuntimeError("Independent reconstruction retains a managed material")
    # Discard unreferenced copies of managed shaders before writing the new scene.
    for material in material_copies.values():
        if material.users == 0:
            bpy.data.materials.remove(material)
    output_path = Path(output_dir) / "export_reimport.blend"
    # Blender 5.2 needs fresh view-layer bases synchronized before a partial
    # Scene write; copying an unsynchronized new Scene can crash in C++.
    for view_layer in destination.view_layers:
        view_layer.update()
    scene_name = destination.name
    bpy.data.libraries.write(str(output_path), {destination}, path_remap="RELATIVE", fake_user=True, compress=True)
    # A partial library has no saved active window/scene and Blender opens it to
    # an empty scene. Promote the isolated data into a normal main file, then
    # restore the scored final scene before returning to the caller.
    try:
        bpy.ops.wm.open_mainfile(filepath=str(output_path), load_ui=False, use_scripts=False)
        destination = bpy.data.scenes.get(scene_name)
        if destination is None or not any(obj.type == "MESH" for obj in destination.objects):
            raise RuntimeError("Reconstruction scene or geometry missing after independent reopen")
        context.window.scene = destination
        for candidate in list(bpy.data.scenes):
            if candidate != destination:
                bpy.data.scenes.remove(candidate)
        bpy.ops.wm.save_as_mainfile(filepath=str(output_path), check_existing=False)
        views = render_views(context, output_dir, "export_reimport", reconstructed_config)
        relight = render_views(context, output_dir, "export_reimport_relight", reconstructed_config, relight=True)
    finally:
        bpy.ops.wm.open_mainfile(filepath=str(Path(output_dir) / "final.blend"), load_ui=False, use_scripts=False)
    render_status = "CAPTURED" if views["status"] == relight["status"] == "CAPTURED" else "PARTIAL"
    return dict(status=render_status, path=str(output_path), sha256=sha256(output_path), views=views, relight=relight,
                appearance_fidelity="REVIEW_REQUIRED", engine_validation="NOT_TESTED",
                note="Plain Principled BSDF; tangent normal includes height once; exported Height is not applied again. Constant coat/anisotropy recorded separately from PBR maps.")


def resolution_check(context, output_dir, config=None):
    import bpy
    from open_painter.authoring import document
    config = config_for(context.scene, config)
    if int(config.get("repeat", 0)) != 1:
        return dict(status="NOT_SCHEDULED", reason="4K is predeclared only for repeat 1; no AI reroll")
    sets = [ts for ts in context.scene.open_painter.texture_sets
            if ts.id in config.get("target_set_ids", [item.id for item in context.scene.open_painter.texture_sets])]
    def authored(ts):
        def normalize(value):
            if isinstance(value, dict):
                derived = {"procedural_scale", "noise_scale", "radius", "scale"} if value.get("units") == "PHYSICAL" else set()
                return {key: normalize(item) for key, item in value.items() if key not in derived}
            if isinstance(value, list):
                return [normalize(item) for item in value]
            return value
        return normalize(document.encode(document.snapshot(ts), digest=True))
    before = {ts.id: (ts.resolution, authored(ts)) for ts in sets}
    start, memory_before = time.monotonic(), resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    try:
        for ts in sets:
            ts.resolution = "4096"
        channels = capture_channels(context, output_dir, "resolution_4k", config)
        views = render_views(context, output_dir, "resolution_4k", config)
        exported = export_final(context, output_dir, config, resolution=4096)
        unchanged = all(authored(ts) == before[ts.id][1] for ts in sets)
        complete = unchanged and channels["status"] == "CAPTURED" and exported["status"] == "CAPTURED"
        status = "FAILED" if not complete or views["status"] == "FAILED" else "CAPTURED" if views["status"] == "CAPTURED" else "PARTIAL"
        return dict(status=status,
                    channels=channels, views=views, export=exported, authored_stack_unchanged=unchanged,
                    seconds=time.monotonic() - start, memory_peak_before=memory_before,
                    memory_peak_after=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                    memory_unit="bytes" if sys.platform == "darwin" else "KiB", ai_reroll=False,
                    gpu_memory=dict(status="NOT_TESTED", reason="No portable per-process GPU allocation counter used"),
                    semantic_comparison="Physical-unit controls retain authored metre values; their automatically resolved texture-unit fields are excluded",
                    preview_measurement="CPU evaluator and fixed render wall time; interactive shader settling NOT_TESTED")
    finally:
        for ts in sets:
            ts.resolution = before[ts.id][0]


def finalize_session(context, output_dir, *, config=None):
    """Save scored result first; provider-independent checks run in a separate process."""
    import bpy
    config = config_for(context.scene, config)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    packing_errors = _pack_sources(context.scene)
    final = output / "final.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(final), check_existing=False)
    result = dict(status="SAVED", final_blend=dict(path=str(final), sha256=sha256(final)),
                  packing_errors=packing_errors, configuration=config,
                  offline=dict(status="NOT_TESTED", reason="Run verify_final.py after provider exits in an empty isolated CODEX_HOME"))
    if config.get("primary_evidence"):
        from view_inventory import filenames
        expected = filenames(config)
        present = {filename: sha256(output / filename) for filename in expected if (output / filename).is_file()}
        missing = [filename for filename in expected if filename not in present]
        result["primary_evidence"] = dict(status="CAPTURED" if not missing else "INCOMPLETE", files=present, missing=missing,
                                           independent_visual_review="REQUIRED", editability_review="REQUIRED")
        result["offline"] = dict(status="NOT_SCHEDULED", reason="Supplemental check; excluded from the primary Eevee benchmark")
        result["extended_checks"] = dict(status="NOT_SCHEDULED", reason="Offline, export, undo and 4K are opt-in supplemental evidence")
    write_json(output / "finalization.json", result)
    return result


def verify_offline(output_dir, *, render=True, export=True, check_4k=True):
    import bpy
    output = Path(output_dir).resolve()
    final = output / "final.blend"
    config = json.loads((output / "finalization.json").read_text(encoding="utf-8")).get("configuration", {})
    config["render_views"] = render and config.get("render_views", True)
    provider_home = os.environ.get("CODEX_HOME")
    isolation = bool(provider_home and Path(provider_home).is_dir() and not any(Path(provider_home).iterdir()))
    report = dict(provider_isolation=dict(status="PASS" if isolation else "BLOCKED",
                                         reason="Empty explicit CODEX_HOME; no provider calls" if isolation else "Explicit empty isolated CODEX_HOME is required; user caches are never removed"))
    if not isolation:
        write_json(output / "offline_verification.json", report)
        return report
    try:
        bpy.ops.wm.open_mainfile(filepath=str(final), load_ui=False, use_scripts=False)
        from open_painter.ai import session
        session.disconnect()
        checkpoint = capture_checkpoint(bpy.context, output, "offline_reopen", config=config)
        report["persistence"] = compare_checkpoints(output / "after_refine_2_state.json", checkpoint, exact_channels=True)
        generated = [item for ts in checkpoint["state"]["texture_sets"].values() for item in ts["sources"].values() if item["generated_id"]]
        report["generated_inputs"] = dict(status=("PASS" if all(item["packed"] for item in generated) else "FAIL") if generated else "NOT_PRESENT",
                                          count=len(generated), all_packed=all(item["packed"] for item in generated))
        def attempt(key, call):
            try:
                report[key] = call()
            except Exception as exc:
                report[key] = dict(status="FAILED", error=f"{type(exc).__name__}: {exc}")
        attempt("editability", lambda: editability_probes(bpy.context, output, config))
        bpy.ops.wm.open_mainfile(filepath=str(final), load_ui=False, use_scripts=False)
        attempt("undo", lambda: undo_check(bpy.context, output, config))
        bpy.ops.wm.open_mainfile(filepath=str(final), load_ui=False, use_scripts=False)
        if export:
            attempt("export", lambda: export_final(bpy.context, output, config))
            attempt("source_relight", lambda: render_views(bpy.context, output, "source_relight", config, relight=True))
            if report["export"]["status"] == "CAPTURED":
                attempt("reconstruction", lambda: reconstruct(bpy.context, output, report["export"], config))
            else:
                report["reconstruction"] = dict(status="BLOCKED", reason="Actual production export did not complete")
        else:
            report["export"] = dict(status="NOT_TESTED", reason="Explicitly disabled")
        if check_4k:
            attempt("resolution_4k", lambda: resolution_check(bpy.context, output, config))
        else:
            report["resolution_4k"] = dict(status="NOT_TESTED", reason="Explicitly disabled")
    except Exception as exc:
        report["error"] = dict(status="FAILED", reason=f"{type(exc).__name__}: {exc}")
    write_json(output / "offline_verification.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--skip-renders", action="store_true")
    parser.add_argument("--skip-export", action="store_true")
    parser.add_argument("--skip-4k", action="store_true")
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    args = parser.parse_args(argv)
    sys.path.insert(0, str(PRODUCT_ROOT / "src"))
    import open_painter
    open_painter.register()
    report = verify_offline(args.output, render=not args.skip_renders, export=not args.skip_export, check_4k=not args.skip_4k)
    if "error" in report or report["provider_isolation"]["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
