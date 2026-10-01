"""Opt-in benchmark CLI. Routine CI never opens an account or starts live inference."""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time

# The repository wrapper historically imports this module by its bare filename.
# Import the benchmark as a package so scoring/report relative imports work too.
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from material_director.manifest import (ROBUSTNESS, canonical, configuration_template, digest,
    freeze_manifest, load_cases, load_manifest, record_result, result_template)
from material_director.runtime import (ROOT, AuthRefreshRequired, isolated_login, preflight_login, redact,
    run_blender, sha256, source_fingerprint as legacy_source_fingerprint, write_json)
from material_director.scoring import WORKFLOW_CHECKS, stage_map

HERE = Path(__file__).resolve().parent
BLENDER = "/Applications/Blender.app/Contents/MacOS/Blender"


def _install_cancel_handlers(cancel_event):
    """Leave unknown C-owned Blender handlers intact: Python cannot restore them."""
    import signal
    import threading
    previous = {}
    if threading.current_thread() is not threading.main_thread():
        return previous
    for sig in (signal.SIGINT, signal.SIGTERM):
        handler = signal.getsignal(sig)
        if callable(handler) or handler in (signal.SIG_DFL, signal.SIG_IGN):
            signal.signal(sig, lambda *_: cancel_event.set())
            previous[sig] = handler
    return previous


def _auth_control(directory, status, *, minimum_seconds, next_run_ids=(), detail=None):
    """Scheduler state is separate from attempted session/model outcomes."""
    path = Path(directory) / "execution_control.json"
    previous = _read(path)
    event = dict(status=status, time_utc=datetime.now(timezone.utc).isoformat(),
        minimum_validity_seconds=minimum_seconds, next_run_ids=list(next_run_ids),
        automatic_refresh=False, message=detail)
    write_json(path, dict(status=status, events=[*previous.get("events", []), event]))


def _preflight_wave(directory, auth_source, *, minimum_seconds, next_run_ids=()):
    try:
        preflight_login(auth_source, minimum_seconds=minimum_seconds)
    except AuthRefreshRequired as exc:
        _auth_control(directory,"AUTH_REFRESH_REQUIRED",minimum_seconds=minimum_seconds,
                      next_run_ids=next_run_ids,detail=str(exc))
        print("AUTH_REFRESH_REQUIRED: scheduling paused before the next attempt; renew the normal managed login and resume.",flush=True)
        return False
    _auth_control(directory,"READY",minimum_seconds=minimum_seconds,next_run_ids=next_run_ids)
    return True


def fixture_records(directory):
    directory = Path(directory).resolve()
    records = {}
    for path in sorted(directory.glob("*.blend")):
        metadata = path.with_suffix(".json")
        report = json.loads(metadata.read_text(encoding="utf-8")) if metadata.exists() else {}
        measured = sha256(path)
        records[path.name] = dict(path=str(path), sha256=sha256(path),
            status=report.get("status", "UNVERIFIED") if report.get("sha256") == measured else "UNVERIFIED",
            metadata=path.with_suffix(".json").name,
            metadata_sha256=sha256(metadata) if metadata.exists() else None)
    reference = directory / "R01_fabric_reference.png"
    if reference.exists():
        records[reference.name] = dict(path=str(reference), sha256=sha256(reference), status="READY")
    return records


def runtime_version(binary):
    result = subprocess.run([binary, "--version"], capture_output=True, text=True, check=True, timeout=10)
    return result.stdout.strip()


def hardware_record():
    return dict(system=platform.system(), architecture=platform.machine(), cpu=platform.processor())


def library_fingerprint(checkout=ROOT):
    """Pin all installed library asset bytes, including images and serialized recipes."""
    checkout = Path(checkout)
    records = {str(path.relative_to(checkout)): sha256(path)
               for base in (checkout / "src/open_painter/assets", checkout / "src/open_painter/smart_materials")
               for path in sorted(base.rglob("*")) if path.is_file() and "__pycache__" not in path.parts}
    return digest(records)


def product_fingerprint(checkout):
    """Pin a product independently from the shared v3 benchmark harness."""
    checkout = Path(checkout).resolve()
    source = checkout / "src/open_painter"
    if not source.is_dir():
        raise ValueError("Product checkout has no Open Painter source")
    files = {str(p.relative_to(checkout)): sha256(p) for p in sorted(source.rglob("*"))
             if p.is_file() and "__pycache__" not in p.parts}
    pin = _read(checkout / ".benchmark_product_pin.json")
    if pin:
        if pin.get("source_sha256") != digest(files):
            raise ValueError("Frozen product snapshot bytes changed")
        commit = pin["commit"]
    else:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=checkout, capture_output=True, text=True,
                                check=True, timeout=10).stdout.strip()
    return dict(commit=commit, source_sha256=digest(files))


def source_fingerprint(checkout=ROOT):
    """Executed product and harness bytes; unrelated docs/reports/tests cannot abort a run."""
    checkout = Path(checkout).resolve()
    harness = checkout / "tests/benchmarks/material_director"
    files = {str(p.relative_to(checkout)): sha256(p)
             for base in (checkout / "src/open_painter", harness) for p in sorted(base.rglob("*"))
             if p.is_file() and "__pycache__" not in p.parts
             and not (p.name.startswith("test_") and p.suffix == ".py") and p.suffix != ".pyc"
             and not (p.suffix == ".md" and p.is_relative_to(harness))}
    return dict(commit=product_fingerprint(checkout)["commit"], source_sha256=digest(files), files=files,
                fingerprint_scheme="executed_product_and_harness_v3")


def snapshot_runtime(destination):
    """Copy one exact product/harness for all smoke turns; never backport intelligence."""
    destination = Path(destination)
    before = source_fingerprint()
    product = product_fingerprint(ROOT)
    destination.mkdir(parents=True, exist_ok=False)
    for relative in ("src/open_painter", "tests/benchmarks/material_director"):
        shutil.copytree(ROOT / relative, destination / relative, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    write_json(destination / ".benchmark_product_pin.json", product)
    copied = source_fingerprint(destination)
    if copied["source_sha256"] != before["source_sha256"] or source_fingerprint()["source_sha256"] != before["source_sha256"]:
        raise ValueError("Source changed while freezing diagnostic runtime; retain this incomplete snapshot and retry separately")
    write_json(destination / "runtime_snapshot.json", {k:v for k,v in copied.items() if k != "files"})
    return copied


def _read(path, default=None):
    return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else ({} if default is None else default)


def _status_check(record, evidence, *, pass_statuses=("PASS",), reason=None):
    status = record.get("status", "NOT_TESTED") if isinstance(record, dict) else "NOT_TESTED"
    passed = True if status in pass_statuses else False if status in {"FAIL", "FAILED"} else None
    return dict(passed=passed, evidence=evidence if record else [],
                status=status, reason=reason or record.get("reason", "") if isinstance(record, dict) else reason or "")


def _input_checks(result, job, out):
    """Verify the actual outgoing images/settings, including on-demand scene map replies."""
    families = {"AO", "CURVATURE", "WORLD_NORMAL", "POSITION", "THICKNESS"}
    if job["case"]["id"].startswith("MD-"):
        families.add("NORMAL")
    allowed_ids = {"viewport", "target_view", "reference", *("map:" + kind for kind in families)}
    if job.get("protocol_version", 2) >= 3:
        allowed_ids.update({"candidate:front", "candidate:back"})
        target_ids = result.get("fixture", {}).get("metadata", {}).get("target_set_ids", [])
        allowed_ids.update("candidate:target:" + identifier for identifier in target_ids)
        allowed_ids.update(identifier + ":" + channel for identifier in target_ids
                           for channel in ("BASE_COLOR", "ROUGHNESS", "METALLIC", "HEIGHT", "NORMAL"))
    wants_reference = job["case"]["id"] == "MD-08"  # Archived protocol only.
    input_log = out / "input_manifest.jsonl"
    attachments_log = [json.loads(line) for line in input_log.read_text(encoding="utf-8").splitlines() if line.strip()] if input_log.exists() else []
    tool_replies = {}
    trace = out / "provider_and_tool_trace.jsonl"
    if trace.exists():
        for line in trace.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            data = item.get("data", {})
            if item.get("direction") == "host" and data.get("action") == "tool_result" and isinstance(data.get("result"), dict):
                tool_replies[data.get("call_id")] = data["result"]
    outcomes = []
    for name, stage in stage_map(result).items():
        if name not in {"create", "refine_1", "refine_2"} or stage.get("status") in {None, "NOT_RUN", "SKIPPED_DEPENDENCY"}:
            continue
        records = [record for record in attachments_log if record.get("turn") == name]
        if stage.get("submitted") is False and not records:
            outcomes.append(dict(stage=name, status="NOT_APPLICABLE", passed=None, errors=[],
                cause_code="REQUEST_NOT_SUBMITTED", material_map_targets=[], outgoing_attachment_batches=0))
            continue
        input_path = out / stage.get("input_manifest", name + "_input.json")
        payload = _read(input_path)
        context = payload.get("current_context", {})
        visual = context.get("visual_context", {})
        settings = stage.get("actual_settings", {})
        errors, inspected = [], set()
        if stage.get("submitted") is False:
            errors.append("Stage is marked unsubmitted but has actual outgoing attachments")
        initial = [record for record in records if record.get("action") == "run"]
        if len(initial) != 1:
            errors.append("Exactly one actual outgoing Create/Refine image manifest is required")
        if visual.get("view_sent") is not True or visual.get("maps_sent") is not True:
            errors.append("Current view and mesh maps were not both enabled in the actual outgoing context")
        if settings.get("generate_textures") is not (job["mode"] == "H"):
            errors.append("Actual Generate Surface Textures setting differs from the scheduled mode")
        if settings.get("model") != job["model_id"]:
            errors.append("Actual requested model differs from the frozen selected identifier")
        for record in records:
            attachments = record.get("attachments", [])
            identifiers = [item.get("id") for item in attachments]
            is_initial = record.get("action") == "run"
            if any(identifier not in allowed_ids and not (job.get("protocol_version", 2) >= 3 and
                   isinstance(identifier, str) and (re.fullmatch(r"region:(?:region:)?[0-9a-f]{32}", identifier)
                   or re.fullmatch(r"candidate:compare:(?:PREVIOUS|CURRENT):candidate:(?:front|back)", identifier)))
                   for identifier in identifiers) or len(identifiers) != len(set(identifiers)):
                errors.append("Unexpected or duplicate outgoing image IDs")
            if ("reference" in identifiers) != wants_reference:
                errors.append("The fixed R01 reference must appear only in MD-08")
            if is_initial and "viewport" not in identifiers:
                errors.append("The actual instruction omitted its model viewport")
            image_context = context if is_initial else tool_replies.get(record.get("call_id"), {})
            meta = {item.get("id"): item for item in image_context.get("visual_inputs", [])}
            present_maps = {identifier.split(":", 1)[1] for identifier in identifiers
                            if isinstance(identifier, str) and identifier.startswith("map:")}
            if is_initial or image_context.get("visual_context", {}).get("maps_sent") is True or present_maps:
                if present_maps != families:
                    errors.append("The actual image payload omitted required fresh mesh-map families")
                elif image_context.get("target", {}).get("id"):
                    inspected.add(image_context["target"]["id"])
            for attachment in attachments:
                path = (out / attachment.get("path", "")).resolve()
                if not path.is_relative_to(out.resolve()) or not path.is_file() or sha256(path) != attachment.get("sha256"):
                    errors.append("Outgoing image bytes are missing or differ from the captured payload")
                identifier = attachment.get("id", "")
                if identifier in {"viewport", "target_view"} and attachment.get("sanity", {}).get("passed") is not True:
                    errors.append("The sent model view is blank/unusable or has no recorded input sanity check")
                if identifier.startswith("map:") and meta.get(identifier, {}).get("provenance_status") != "Ready":
                    errors.append("A sent map lacks current Ready provenance")
        if wants_reference:
            if visual.get("reference_sent") is not True or not job.get("reference_sha256"):
                errors.append("The reference source was not pinned and confirmed in the outgoing context")
            elif sha256(job["reference"]) != job["reference_sha256"]:
                errors.append("The selected reference source changed during the session")
        elif visual.get("reference_sent") is not False:
            errors.append("An undeclared appearance reference was present")
        if name == "create" and settings.get("scene_scope") is True:
            targets = {item["id"] for item in context.get("scene_materials", [])}
            if targets and not targets.issubset(inspected):
                errors.append("Scene Create did not receive map images for every declared target")
        outcomes.append(dict(stage=name, status="PASS" if not errors else "FAIL", passed=not errors, errors=errors,
                             material_map_targets=sorted(inspected), outgoing_attachment_batches=len(records)))
    assessed = [item for item in outcomes if item.get("status") != "NOT_APPLICABLE"]
    passed = all(item["passed"] for item in assessed) if assessed else None
    return dict(passed=passed, status="PASS" if passed is True else "FAIL" if passed is False else "NOT_APPLICABLE", stages=outcomes)


def _evidence_delivery(result, job, out):
    """A claimed completed primary session must have its actual, intact evidence."""
    if job.get("kind", "core") != "core" or not job.get("case", {}).get("id", "").startswith("T") or job.get("quick"):
        return dict(passed=None, evidence=[], status="NOT_SCHEDULED")
    if result.get("status") != "COMPLETED" and not (out / "finalization.json").exists():
        return dict(passed=False if result.get("evidence_failures") else None,
                    evidence=["result.json"], status="INCOMPLETE",
                    reason="The session did not complete; missing later turns are not invented AI quality scores")
    errors = []
    verified = []
    expected_channels = {"BASE_COLOR", "ROUGHNESS", "METALLIC", "NORMAL", "HEIGHT", "AO", "EMISSION", "OPACITY"}

    def artifact(record, *, hashed=True):
        supplied = record.get("path") or ""
        path = Path(supplied)
        path = path.resolve() if path.is_absolute() else (out / path).resolve()
        if not supplied or not path.is_relative_to(out.resolve()) or not path.is_file():
            errors.append("Missing or outside-session evidence file: " + str(supplied))
            return False
        if hashed and (not record.get("sha256") or sha256(path) != record["sha256"]):
            errors.append("Evidence hash mismatch: " + path.name)
            return False
        if job.get("protocol_version", 2) >= 3 and path.suffix.lower() == ".png":
            from material_director.png_validation import decode_png
            try:
                decode_png(path.read_bytes(), (1024,1024) if path.name in filenames(view_config) else None)
            except Exception as exc:
                errors.append("Evidence PNG decode failed: " + path.name + ": " + str(exc))
                return False
        verified.append(str(path.relative_to(out.resolve())))
        return True

    finalization = _read(out / "finalization.json")
    if finalization.get("status") != "SAVED" or finalization.get("packing_errors"):
        errors.append("Final material was not successfully saved and packed")
    artifact(dict(finalization.get("final_blend", {}), path="final.blend"))
    inventory = finalization.get("primary_evidence", {})
    if inventory.get("status") != "CAPTURED":
        errors.append("Primary ten-image inventory was not captured")
    from material_director.view_inventory import filenames, inventory as view_inventory
    view_config = dict(case_id=job["case"]["id"], protocol_version=job.get("protocol_version", 2))
    for filename in filenames(view_config):
        artifact(dict(path=filename, sha256=inventory.get("files", {}).get(filename)))
    for label in ("before", "after_create", "after_refine_1", "after_refine_2"):
        path = out / (label + "_state.json")
        checkpoint = _read(path)
        if not checkpoint:
            errors.append("Required checkpoint is missing: " + path.name)
            continue
        verified.append(path.name)
        local_errors = len(errors)
        for component in ("channels", "views", "ui_layer_mask_views"):
            if checkpoint.get(component, {}).get("status") != "CAPTURED":
                errors.append(label + ": " + component + " did not complete")
        target_ids = {identifier for identifier, value in checkpoint.get("state", {}).get("texture_sets", {}).items()
                      if value.get("target")}
        if not target_ids:
            errors.append(label + ": no target material state was captured")
        channels = checkpoint.get("channels", {}).get("sets", {})
        for identifier in target_ids:
            recorded = channels.get(identifier, {})
            if recorded.get("status") != "CAPTURED" or set(recorded.get("channels", {})) != expected_channels:
                errors.append(label + ": incomplete channel set for " + identifier)
            for value in recorded.get("channels", {}).values():
                if value.get("status", "CAPTURED") != "CAPTURED" or value.get("finite") is not True:
                    errors.append(label + ": failed/nonfinite evaluated channel")
                v3 = job.get("protocol_version", 2) >= 3
                artifact(value, hashed=v3)
                artifact(dict(path=value.get("preview", ""), sha256=value.get("preview_sha256")), hashed=v3)
            if job.get("protocol_version", 2) >= 3:
                artifact(recorded.get("valid_samples", {}))
                artifact(recorded.get("surface_samples", {}))
                for effect in recorded.get("effect_masks", {}).values():
                    artifact(effect)
                    if effect.get("finite") is not True:
                        errors.append(label + ": nonfinite evaluated effect mask")
                    control = effect.get("effect_control",{})
                    if control.get("status") == "MEASURED":
                        artifact(control.get("source_field",{}))
                        if control.get("source_field",{}).get("finite") is not True:
                            errors.append(label + ": nonfinite pre-opacity effect source")
        views = checkpoint.get("views", {})
        expected_views = len(view_inventory(view_config)[label])
        if len(views.get("views", [])) != expected_views or views.get("engine") != "BLENDER_EEVEE" or views.get("resolution") != [1024, 1024]:
            errors.append(label + ": incorrect primary Eevee view inventory")
        for view in views.get("views", []):
            artifact(view)
        ui = checkpoint.get("ui_layer_mask_views", {}).get("views", [])
        if not target_ids.issubset({view.get("texture_set_id") for view in ui}):
            errors.append(label + ": native layer/mask UI evidence is missing a target set")
        for view in ui:
            artifact(view)
        if label != "before":
            name = label.removeprefix("after_")
            stage = stage_map(result).get(name, {})
            receipt_path = out / (name + "_receipt.json")
            receipts = _read(receipt_path, None) if receipt_path.is_file() else None
            if not isinstance(receipts, list) or (stage.get("applied") is True and not receipts):
                errors.append(label + ": the claimed apply has no saved change receipt")
            elif receipt_path.exists():
                verified.append(receipt_path.name)
            if len(errors) != local_errors:
                stage["visible_complete"] = False
    assessment = dict(passed=not errors, status="PASS" if not errors else "BLOCKED",
                      evidence=["evidence_delivery.json"], verified_files=sorted(set(verified)), errors=errors)
    write_json(out / "evidence_delivery.json", assessment)
    if errors and result.get("status") == "COMPLETED":
        # V3 keeps product execution intact when evidence capture fails.
        if job.get("protocol_version", 2) < 3:
            result["status"] = "BLOCKED"
        result["evidence_status"] = "CAPTURE_FAILED"
        result["reason"] = "Required primary evidence did not complete; inspect evidence_delivery.json"
        result.setdefault("failures", []).append(dict(category="evidence", message=result["reason"],
                                                     evidence=["evidence_delivery.json"]))
    return assessment


def _checks(result, job, out):
    """Normalize machine evidence while leaving artistic judgments explicitly pending."""
    checks = result.setdefault("checks", {})
    for key in WORKFLOW_CHECKS:
        checks.setdefault(key, dict(passed=None, evidence=[], reason="Required evidence/review is pending"))
    checks["evidence_delivery"] = _evidence_delivery(result, job, out)
    offline = result.get("offline_verification", {})
    finalization = result.get("finalization", {}) or _read(out / "finalization.json")
    if finalization:
        result["finalization"] = finalization
    offline_evidence = ["offline_verification.json"] if (out / "offline_verification.json").exists() else []
    if offline:
        checks["undo"] = _status_check(offline.get("undo", {}), offline_evidence)
        persistence = _status_check(offline.get("persistence", {}), offline_evidence)
        isolation = offline.get("provider_isolation", {}).get("status") == "PASS"
        packed = offline.get("generated_inputs", {}).get("status") in {"PASS", "NOT_PRESENT"}
        if persistence["passed"] is True and (not isolation or not packed or finalization.get("packing_errors")):
            persistence.update(passed=False if finalization.get("packing_errors") or offline.get("generated_inputs", {}).get("status") == "FAIL" else None,
                               reason="Offline isolation, generated-image packing and decoded preservation must all be verified")
        checks["save_reopen_offline"] = persistence
        editability = offline.get("editability", {})
        checks["editability_probe"] = _status_check(editability, offline_evidence,
            reason="Ordinary-control independence requires the recorded probe and independent review")
        exported, reconstruction = offline.get("export", {}), offline.get("reconstruction", {})
        export_failed = exported.get("status") in {"FAIL", "FAILED"} or reconstruction.get("status") in {"FAIL", "FAILED"}
        checks["export_reimport"] = dict(passed=False if export_failed else None, evidence=offline_evidence,
            status="FAIL" if export_failed else "REVIEW_REQUIRED",
            reason="Export/reconstruction files establish handoff structure; fixed-light/relight appearance fidelity still needs review")
        result["resolution_4k"] = dict(offline.get("resolution_4k", {}), evidence=offline_evidence)
    if job.get("kind") == "robustness":
        raw = {key: value for key, value in checks.items() if key not in WORKFLOW_CHECKS}
        if result.get("diagnosis_review_required"):
            raw["safe_prerequisite_diagnosis"] = dict(passed=None, evidence=["provider_and_tool_trace.jsonl"],
                reason="Independent review must confirm the actual missing-map/UV diagnosis or safe alternative")
        values = [item.get("passed") for item in raw.values()]
        status = "FAIL" if False in values or result.get("hard_failures") else "PASS" if values and all(value is True for value in values) else "NOT_SCORED"
        result["safety"] = dict(status=status, checks=raw, evidence=["provider_and_tool_trace.jsonl", "result.json"])
        return
    before = _read(out / "before_state.json")
    preparation = _read(Path(job["fixture"]).with_suffix(".json"))
    if before:
        target_sets = before.get("state", {}).get("texture_sets", {})
        maps_ready = bool(target_sets) and all(
            item.get("maps") and all(entry.get("status") == "Ready" for entry in item["maps"])
            for item in target_sets.values() if item.get("target"))
        shared = _input_checks(result, job, out)
        write_json(out / "sharing_checks.json", shared)
        input_ok = (result.get("fixture", {}).get("sha256") == job["fixture_sha256"]
                    and preparation.get("sha256") == job["fixture_sha256"]
                    and preparation.get("status") == "READY" and maps_ready and shared["passed"])
        checks["input_integrity"] = dict(passed=input_ok,
            evidence=["before_state.json", "job.json", "fixture_preparation.json", "sharing_checks.json", "input_manifest.jsonl"],
            reason="Frozen neutral fixture/fresh maps and actual sent viewport, map images, reference and P/H settings checked")
        write_json(out / "fixture_preparation.json", redact(preparation))
        base = before["state"]
        structural = []
        declared_protected = {item["id"]: item.get("protected_layer_ids", []) for item in preparation.get("texture_sets", [])}
        for stage in stage_map(result).values():
            checkpoint = stage.get("checkpoint")
            if not checkpoint:
                continue
            saved = _read(out / checkpoint)
            current = saved.get("state", {})
            if not current:
                continue
            old_scene, new_scene = base.get("scene", {}), current.get("scene", {})
            scene_change_keys = sorted(key for key in set(old_scene) | set(new_scene)
                                       if old_scene.get(key) != new_scene.get(key))
            scene_unchanged = not scene_change_keys
            protected = True
            for identifier, layer_ids in declared_protected.items():
                a = {layer["id"]: layer for layer in base.get("texture_sets", {}).get(identifier, {}).get("semantic", {}).get("layers", [])}
                b = {layer["id"]: layer for layer in current.get("texture_sets", {}).get(identifier, {}).get("semantic", {}).get("layers", [])}
                protected = protected and all(key in a and key in b and a[key] == b[key] for key in layer_ids)
            structural.append(dict(stage=stage["name"], scene_unchanged=scene_unchanged,
                                   scene_change_keys=scene_change_keys,
                                   protected_artist_layers_unchanged=protected, checkpoint=checkpoint))
            stage["assigned_generated_image_ids"] = sorted({image["generated_id"]
                for texture_set in current.get("texture_sets", {}).values()
                for image in texture_set.get("sources", {}).values() if image.get("generated_id")})
        write_json(out / "preservation_checks.json", dict(structural=structural,
            refinement_component_buffers="REVIEW_REQUIRED; declare semantic regions/components and compare fixed evaluator samples"))
        if structural:
            structural_pass = all(item["scene_unchanged"] and item["protected_artist_layers_unchanged"] for item in structural)
            checks["scene_and_artist_preservation"] = dict(passed=structural_pass, evidence=["preservation_checks.json"])
            checks["preservation"] = dict(passed=None if structural_pass else False,
                evidence=["preservation_checks.json"],
                reason="Geometry, guard shaders and protected artist layers checked; requested refinement component buffers require independent assessment")
            if not structural_pass:
                result.setdefault("hard_failures", []).append(dict(kind="unauthorized_edit", evidence=["preservation_checks.json"]))


def _metrics(result):
    stages = list(stage_map(result).values())
    generated = result.get("actual_generated_inputs", [])
    result["attempted"] = any(stage.get("status") not in {None, "NOT_RUN", "SKIPPED_DEPENDENCY"} for stage in stages)
    count = result.get("artist_intervention_count", result.get("manual_artistic_interventions"))
    result["artist_intervention_count"] = count if type(count) is int and count >= 0 else None
    offline = result.get("offline_verification", {})
    assigned = offline.get("generated_inputs", {}).get("count")
    assigned_ids = {identifier for stage in stages for identifier in stage.get("assigned_generated_image_ids", [])}
    assignment_observed = any("assigned_generated_image_ids" in stage for stage in stages)
    result["generated_inputs"] = dict(
        availability="DISABLED" if result["mode"] == "P" else "AVAILABLE" if generated else "BLOCKED"
                     if result.get("generation_available") is False else "NOT_OBSERVED",
        generated=len(generated), assigned=len(assigned_ids) if assignment_observed else assigned if isinstance(assigned, int) else 0,
        final_packed_image_datablocks=assigned,
        assignment_verified=assignment_observed or isinstance(assigned, int),
        reused_source_ids=sorted({identifier for stage in stages[1:] for identifier in stage.get("assigned_generated_image_ids", [])
                                 if identifier in set(stages[0].get("assigned_generated_image_ids", []))}) if stages else [])
    # The offline evidence, rather than the toggle or model prose, establishes
    # packed assignments. Preserve raw evidence when its schema is extended.
    def total(key):
        values = [stage[key] for stage in stages if isinstance(stage.get(key), (int, float))]
        return sum(values) if values else None
    result["metrics"] = dict(
        provider_wait_seconds=total("provider_wait_seconds"),
        local_apply_seconds=total("local_application_seconds"),
        preview_settling_seconds=total("preview_settling_seconds"),
        baking_export_seconds=result.get("offline_seconds"),
        active_operator_seconds=result.get("active_operator_seconds"),
        active_operator_measurement="UNKNOWN unless explicitly measured; automation does not prove zero operator effort",
        elapsed_seconds=result.get("elapsed_seconds"), generation_calls=total("generation_call_count"),
        tool_calls=total("tool_call_count"),
        monetary_cost=dict(amount=None, currency=None, status="UNKNOWN_NOT_EXPOSED"))
    tool_timings = [item for stage in stages for item in stage.get("tool_timings", [])]
    candidate_times = [item["seconds"] for item in tool_timings if item.get("name") == "preview_proposal"]
    result["metrics"].update(peak_process_rss_bytes=result.get("resources", {}).get("peak_rss_bytes"),
        concurrent_jobs=result.get("resources", {}).get("concurrent_jobs", 1),
        candidate_preview_calls=len(candidate_times) if tool_timings else None,
        candidate_preview_seconds=sum(candidate_times) if candidate_times else None,
        candidate_timing_basis="Observed tool-response wall time including compile, CPU channels and Eevee context capture; not a GPU hardware counter")
    for failure in result.get("hard_failures", []):
        if isinstance(failure, dict) and "kind" not in failure and "category" in failure:
            failure["kind"] = failure["category"]
    return result


def execute(job, *, blender=BLENDER, codex=None, use_login=False, auth_source=None, quick=False, cancel_event=None):
    if not use_login:
        raise ValueError("Live account use is opt-in: pass --use-codex-login")
    # Acquire and validate the temporary access-only cache before writing any
    # attempt marker. A credential race after wave preflight is still unattempted.
    with isolated_login(auth_source, minimum_seconds=job["limits"]["session_seconds"] + 120) as auth:
        return _execute_authenticated(job, auth=auth, blender=blender, codex=codex, quick=quick, cancel_event=cancel_event)


def _execute_authenticated(job, *, auth, blender, codex, quick=False, cancel_event=None):
    codex = shutil.which(codex or "codex") or codex
    if not codex or not Path(codex).is_file():
        raise ValueError("A supported Codex executable is required")
    out = Path(job["output"]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if (out / "result.json").exists():
        existing = json.loads((out / "result.json").read_text(encoding="utf-8"))
        if (existing.get("status") not in {"NOT_RUN", "RUNNING"} or existing.get("attempted")
                or any(stage.get("status") not in {None, "NOT_RUN"} for stage in stage_map(existing).values())):
            raise ValueError("This attempt already has evidence. Use another comparison/pilot directory; do not overwrite an attempted run")
    actual_runtime = runtime_version(codex)
    if job.get("runtime_version") and job["runtime_version"] != actual_runtime:
        raise ValueError("Codex runtime changed from the frozen comparison")
    # A launcher crash before the first provider turn must not make this attempt
    # silently rerunnable. The immutable marker distinguishes it from a template.
    with (out / "attempt.json").open("x") as handle:
        json.dump(dict(run_id=job["run_id"], started_at=datetime.now(timezone.utc).isoformat(),
                       model_id=job["model_id"], mode=job["mode"], quick=quick,
                       fixture_sha256=job["fixture_sha256"]), handle, indent=2)
    job["build"] = product_fingerprint(job["product_root"]) if job.get("product_root") else source_fingerprint()
    job["runtime_version"] = actual_runtime
    job["evidence"] = dict(job.get("evidence", {}), render_views=not quick, capture_channels=not quick)
    job["quick"] = quick
    job["output"] = str(out)
    fixture_report = Path(job["fixture"]).with_suffix(".json")
    if fixture_report.exists():
        write_json(out / "fixture_preparation.json", redact(_read(fixture_report)))
    write_json(out / "job.json", job)
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="painter-benchmark-profile-") as profile:
            env = dict(os.environ, BLENDER_USER_RESOURCES=profile,
                TMPDIR=profile,
                OP_MD_BENCHMARK_JOB=str((out / "job.json").resolve()),
                OP_MD_BENCHMARK_AUTH=str(auth), OP_MD_BENCHMARK_CODEX=str(codex),
                OP_MD_BENCHMARK_CONTROL=str(Path(profile) / "owned-process.json"))
            if job.get("product_root"):
                env["OP_MD_PRODUCT_ROOT"] = job["product_root"]
            command = [blender, "--factory-startup", "--disable-autoexec", "--online-mode", "--python-exit-code", "1",
                       "--python", str(Path(job.get("harness_root", HERE)) / "run_session.py")]
            if sys.platform == "darwin":
                command += ["--", "-ApplePersistenceIgnoreState", "YES"]
            try:
                code = run_blender(command, env=env, timeout=job["limits"]["session_seconds"],
                                   log=out / "blender.log", cancel_event=cancel_event)
            finally:
                control = Path(profile) / "owned-process.json"
                if control.exists() and os.name != "nt":
                    import signal
                    pid = json.loads(control.read_text(encoding="utf-8")).get("companion_pid")
                    if isinstance(pid, int) and pid > 1:
                        try:
                            if os.getpgid(pid) == pid:
                                os.killpg(pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
        # Both the access-only cache and live Blender profile are gone before
        # offline reconstruction. Never touch the user's normal provider cache.
        if (out / "result.json").exists():
            result = json.loads((out / "result.json").read_text(encoding="utf-8"))
        else:
            result = dict(run_id=job["run_id"], case_id=job["case"]["id"], mode=job["mode"], repeat=job.get("repeat", 1),
                          status="FAILED", stages=[], hard_failures=[], checks={}, failures=[])
        if code:
            result["status"] = "FAILED"
            result.setdefault("failures", []).append(dict(category="infrastructure", message=f"Blender exited with code {code}; see log"))
            if code < 0:
                result.setdefault("hard_failures", []).append(dict(category="crash", signal=-code))
        if ((out / "final.blend").exists() and job.get("kind", "core") == "core"
                and job.get("supplements", {}).get("extended_checks", False)):
            offline_started = time.monotonic()
            with tempfile.TemporaryDirectory(prefix="painter-benchmark-offline-") as profile:
                env = dict(os.environ, BLENDER_USER_RESOURCES=str(Path(profile) / "blender"),
                           TMPDIR=profile,
                           CODEX_HOME=str(Path(profile) / "no-provider-cache"),
                           OP_MD_PRODUCT_ROOT=str(job.get("product_root", ROOT)))
                Path(env["CODEX_HOME"]).mkdir()
                args = [blender, "--background", "--factory-startup", "--disable-autoexec", "--python-exit-code", "1",
                        "--python", str(Path(job.get("harness_root", HERE)) / "verify_final.py"), "--", "--output", str(out)]
                if quick:
                    args += ["--skip-renders", "--skip-export", "--skip-4k"]
                remaining = job["limits"]["session_seconds"] - (time.monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError("Session cap exhausted before offline evidence capture")
                offline_code = run_blender(args, env=env,
                    timeout=min(remaining, job.get("evidence", {}).get("offline_cap_seconds", remaining)),
                    log=out / "offline.log", cancel_event=cancel_event)
            result["offline_seconds"] = round(time.monotonic() - offline_started, 3)
            evidence = out / "offline_verification.json"
            if evidence.exists():
                result["offline_verification"] = json.loads(evidence.read_text(encoding="utf-8"))
            if offline_code:
                result.setdefault("failures", []).append(dict(category="export_or_persistence", message=f"Offline verifier exited {offline_code}; see offline.log"))
    except (RuntimeError, OSError, subprocess.SubprocessError, TimeoutError) as exc:
        result = json.loads((out / "result.json").read_text(encoding="utf-8")) if (out / "result.json").exists() else dict(
            run_id=job["run_id"], case_id=job["case"]["id"], mode=job["mode"], repeat=job.get("repeat", 1),
            stages=[], checks={}, hard_failures=[], failures=[])
        attempted = any(stage.get("status") not in {None, "NOT_RUN"} for stage in stage_map(result).values())
        result["status"] = "FAILED" if attempted else "UNAVAILABLE"
        if cancel_event is not None and cancel_event.is_set():
            result["status"] = "INTERRUPTED"
            result["cancelled"] = True
        result.setdefault("failures", []).append(dict(category="infrastructure", message=str(exc)))
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    for key, value in (("mode", job["mode"]), ("case_id", job["case"]["id"]), ("repeat", job["repeat"]), ("run_id", job["run_id"])):
        result.setdefault(key, value)
    result["pilot"] = job.get("pilot", False)
    result["budget_policy"] = dict(harness=job.get("limits"), product=job.get("product_deadlines"),
        limitation="SET native requests retain the ordinary 180-second product deadline; SET hybrid requests have 900 seconds. Harness caps may be lower. Compare product builds within the same mode/scope.")
    result["quick"] = quick
    result["started_at"] = _read(out / "attempt.json").get("started_at")
    result["completed_at"] = datetime.now(timezone.utc).isoformat()
    result["generation_available"] = job.get("generation_available")
    from material_director.png_validation import decoder_backend
    result.setdefault("environment", {}).update(hardware=hardware_record(), material_library_sha256=library_fingerprint(job.get("product_root", ROOT)),
                                               png_decoder=decoder_backend())
    try:
        _checks(result, job, out)
    except (ValueError, KeyError, OSError, TypeError) as exc:
        result.setdefault("failures", []).append(dict(category="infrastructure", phase="evidence_normalization", message=str(exc)))
        result.setdefault("checks", {})["input_integrity"] = dict(passed=False, evidence=["result.json"],
            reason="Evidence could not be verified; preserve raw artifacts and diagnose this attempt")
        if result.get("status") == "COMPLETED":
            result["status"] = "BLOCKED"
    _metrics(result)
    if job.get("protocol_version", 2) >= 3:
        from material_director.outcomes import adapt_result, terminal_execution, skip_dependencies
        result = adapt_result(result)
        for stage in result["stages"]:
            if stage.get("status") == "RUNNING" and result.get("status") not in {"RUNNING", "NOT_RUN"}:
                stage["execution"] = {"state": "CANCELLED" if result.get("cancelled") else "PROVIDER_FAILED",
                    "cause_code": "BENCHMARK_RUN_CANCELLED" if result.get("cancelled") else "HOST_TERMINATED_BEFORE_TERMINAL_EVENT"}
                if stage["name"] in {"create", "refine_1", "refine_2"}:
                    skip_dependencies(result["stages"], stage["name"])
        result["record_version"] = 3
        states = [stage["execution"]["state"] for stage in result["stages"]]
        result["execution_summary"] = "APPLIED" if len(states) == 3 and all(s == "APPLIED" for s in states) else next(
            (s for s in states if s not in {"APPLIED", "SKIPPED_DEPENDENCY"}), "NOT_RUN")
        if result["status"] == "COMPLETED" and any(s in {"PROVIDER_FAILED", "VALIDATION_FAILED", "NEEDS_INPUT",
                "CANCELLED", "SETUP_FAILED", "SKIPPED_DEPENDENCY"} for s in states):
            result["status"] = "FAILED"
        result["quality"] = {"state": "NOT_REVIEWED" if any(s.get("applied") for s in result["stages"]) else "NOT_ASSESSABLE"}
    result = redact(result)
    result["evidence_normalization_seconds"] = round(time.monotonic() - started - result["elapsed_seconds"], 3)
    result["total_delivery_seconds"] = round(time.monotonic() - started, 3)
    write_json(out / "result.json", result)
    if job.get("protocol_version", 2) >= 3:
        write_json(out / "terminal_attempt.json", result)
    # Raw stdout is useful locally; public logs get the same path/credential
    # redaction as the structured trace before packaging or review export.
    for name in ("blender.log", "offline.log"):
        path = out / name
        if path.exists():
            path.write_text(redact(path.read_text(errors="replace", encoding="utf-8")), encoding="utf-8")
    return result


def make_job(case, *, mode, repeat, fixture, output, model, limits=None, kind="core", reference=None, run_id=None):
    spec = load_cases()
    return dict(run_id=run_id or f"{case['id']}-{mode}-{repeat}", kind=kind, protocol_version=3, protocol="five_asset_eevee_v3",
        supplements=dict(robustness=False, extended_checks=False, baselines=False),
        case=case, common_instruction=spec["common_instruction"], mode=mode, repeat=repeat,
        fixture=str(Path(fixture).resolve()), fixture_sha256=sha256(fixture),
        reference=str(Path(reference).resolve()) if reference else "", output=str(Path(output).resolve()), model_id=model,
        reference_sha256=sha256(reference) if reference else None,
        evidence=dict(protocol_version=3), correction_enabled=True,
        product_deadlines=configuration_template()["product_deadlines"],
        limits=limits or dict(single_asset_turn_seconds=600, multi_set_turn_seconds=900, session_seconds=1800))


def _verify_evidence(configuration, fixture=None):
    evidence = deepcopy(configuration.get("evidence", {}))
    annotation_path = evidence.get("annotations_manifest")
    if annotation_path:
        path = Path(annotation_path)
        if not path.is_file() or sha256(path) != evidence.get("annotations_sha256"):
            raise ValueError("Evaluator-only annotation manifest changed or has no matching frozen SHA-256")
        from material_director.annotations import verify_frozen
        verify_frozen(path, expected_sha256=evidence["annotations_sha256"], fixture_sha256=fixture)
    return evidence


def _verify_pilot(configuration):
    path = Path(configuration.get("pilot", {}).get("evidence") or "")
    if not path.is_file():
        raise ValueError("pilot.evidence must identify an actual non-scored pilot result.json")
    result = _read(path)
    if configuration.get("protocol") == "five_asset_eevee_v3":
        if result.get("status") != "INSPECTED" or {item.get("case_id") for item in result.get("cases", [])} != {f"T{i:02d}" for i in range(1, 6)}:
            raise ValueError("V3 requires an inspected five-case diagnostic smoke index before the campaign")
        if len(result["cases"]) != 5:
            raise ValueError("Retain exactly one initial smoke attempt per case; retries remain separate diagnostics")
        for item in result["cases"]:
            result_path = Path(item.get("result_path", ""))
            if not result_path.is_absolute():
                result_path = path.parent / result_path
            smoke = _read(result_path)
            job = _read(result_path.with_name("job.json"))
            if (smoke.get("pilot") is not True or smoke.get("quick") is not False or not item.get("inspection_notes")
                    or smoke.get("status") not in {"COMPLETED", "FAILED", "BLOCKED", "UNAVAILABLE"}
                    or job.get("protocol_version") != 3 or job.get("limits") != configuration["limits"]):
                raise ValueError("Smoke case lacks full-evidence terminal records and inspection: " + item["case_id"])
            if smoke.get("attempted") is not True or not any(stage.get("submitted") is True for stage in stage_map(smoke).values()):
                raise ValueError("Five-case live smoke requires an actual submitted provider instruction per case: " + item["case_id"])
            provider = smoke.get("provider", {})
            for key in ("returned_model_id", "runtime_version"):
                if provider.get(key) != configuration["provider"].get(key):
                    raise ValueError("Smoke provider pin differs from the frozen comparison: " + key)
            improved = configuration.get("builds", {}).get("improved")
            environment = smoke.get("environment", {})
            if not improved or job.get("build") != improved or environment.get("build") != improved:
                raise ValueError("Smoke improved product differs from the frozen comparison")
            for observed, frozen in (("blender_version", "blender_version"),
                                     ("render_engine", "render_backend"),
                                     ("evaluation", "evaluator_backend"),
                                     ("hardware", "hardware"),
                                     ("material_library_sha256", "material_library_sha256"),
                                     ("png_decoder", "png_decoder")):
                if environment.get(observed) != configuration["environment"].get(frozen):
                    raise ValueError("Smoke environment pin differs from the frozen comparison: " + frozen)
        return
    if result.get("pilot") is not True or result.get("quick") is not False or result.get("status") != "COMPLETED":
        raise ValueError("Freeze requires a completed full-evidence non-scored pilot; quick plumbing checks are insufficient")
    provider = result.get("provider", {})
    for key in ("runtime_version", "returned_model_id"):
        if provider.get(key) != configuration["provider"].get(key):
            raise ValueError("Pilot provider pin differs: " + key)
    job = _read(path.with_name("job.json"))
    if job.get("limits") != configuration["limits"]:
        raise ValueError("Operational limits were not confirmed by the cited pilot")
    if result.get("environment", {}).get("blender_version") != configuration["environment"]["blender_version"]:
        raise ValueError("Pilot Blender version differs from the frozen environment")
    if configuration.get("protocol") == "five_asset_eevee_v2":
        if job.get("protocol_version") != 2 or result.get("environment", {}).get("render_engine") != "BLENDER_EEVEE":
            raise ValueError("The revised primary protocol requires a pilot on the fixed Eevee harness")
        if result.get("checks", {}).get("input_integrity", {}).get("passed") is not True:
            raise ValueError("Pilot must verify actual usable model images, maps and immutable fixture input")
        expected = load_cases()["evidence_files"]
        if any(not path.with_name(name).is_file() for name in expected):
            raise ValueError("Pilot must capture all ten standard primary Eevee evidence images")
        finalization = result.get("finalization", {})
        final_path = path.with_name("final.blend")
        if (finalization.get("packing_errors") or not final_path.is_file()
                or sha256(final_path) != finalization.get("final_blend", {}).get("sha256")):
            raise ValueError("Pilot final material must be saved and packed with a matching file checksum")


def _verify_run_pins(configuration, codex):
    if configuration.get("protocol") == "five_asset_eevee_v3":
        from material_director.png_validation import decoder_backend
        if configuration.get("environment",{}).get("png_decoder") != decoder_backend():
            raise ValueError("PNG decoding backend or native library changed from the frozen comparison")
    current = source_fingerprint() if configuration.get("protocol") == "five_asset_eevee_v3" else legacy_source_fingerprint()
    if any(current[key] != configuration["build"][key] for key in ("commit", "source_sha256")):
        raise ValueError("Executed source changed during the comparison; start a separate build instead of pooling runs")
    if hardware_record() != configuration["environment"]["hardware"]:
        raise ValueError("Hardware changed from the frozen comparison")
    if library_fingerprint() != configuration["environment"]["material_library_sha256"]:
        raise ValueError("Installed material library changed from the frozen comparison")
    executable = shutil.which(codex or "codex") or codex
    if not executable or runtime_version(executable) != configuration["provider"]["runtime_version"]:
        raise ValueError("Codex runtime changed from the frozen comparison")
    return executable


def _import_review(directory, source):
    """Merge distinct human ratings without exposing the private run key in packets."""
    directory = Path(directory)
    frozen = load_manifest(directory)
    incoming = _read(source)
    review_id = incoming.get("review_id")
    entry = next((entry for entry in frozen["schedule"] if entry.get("review_id") == review_id), None)
    if not entry:
        raise ValueError("The review ID is not in this frozen comparison")
    path = directory / "reviews" / f"{review_id}.json"
    previous = _read(path)
    if incoming.get("brief") != previous.get("brief"):
        raise ValueError("The review must retain the exact supplied brief")
    merged = deepcopy(previous)
    registry = {reviewer["id"]: reviewer for reviewer in merged.get("reviewers", [])}
    for reviewer in incoming.get("reviewers", []):
        identifier = reviewer.get("id")
        if not identifier or reviewer.get("independent") is not True or reviewer.get("is_generating_ai") is not False:
            raise ValueError("Imported ratings require an identified independent human reviewer")
        if identifier in registry and registry[identifier] != reviewer:
            raise ValueError("Reviewer identity changed; use a distinct reviewer ID")
        registry[identifier] = reviewer
    merged["reviewers"] = list(registry.values())
    for stage in ("create", "refine_1", "refine_2"):
        incoming_stage = incoming.get("stages", {}).get(stage, {})
        current = merged["stages"][stage]
        ratings = {record["reviewer_id"]: record for record in current.get("ratings", [])}
        for rating in incoming_stage.get("ratings", []):
            from material_director.scoring import reviewed_score
            assessed = reviewed_score([rating], merged["reviewers"])
            if assessed["score"] is None:
                raise ValueError("Invalid reviewer rating: " + "; ".join(assessed["errors"]))
            identifier = rating["reviewer_id"]
            if identifier in ratings and ratings[identifier] != rating:
                raise ValueError("An existing independent rating cannot be silently replaced")
            ratings[identifier] = rating
        current["ratings"] = list(ratings.values())
        for acceptance_key in ("no_op_acceptance", "acceptance"):
            accepted = incoming_stage.get(acceptance_key, {})
            if accepted.get("passed") is not None:
                if type(accepted.get("passed")) is not bool or not accepted.get("evidence"):
                    raise ValueError("Stage acceptance requires a boolean and recorded evidence")
                current[acceptance_key] = accepted
    for group in ("acceptance_checks", "workflow_checks"):
        for key, check in incoming.get(group, {}).items():
            allowed = set(previous.get("acceptance_checks", {})) if group == "acceptance_checks" else set(WORKFLOW_CHECKS)
            if key not in allowed:
                raise ValueError("Review contains an undeclared acceptance/workflow check")
            if check.get("passed") is None:
                continue
            if type(check.get("passed")) is not bool or not check.get("evidence"):
                raise ValueError("Review checks require a boolean and recorded evidence")
            if group == "acceptance_checks" and check.get("requirement", previous[group][key]["requirement"]) != previous[group][key]["requirement"]:
                raise ValueError("Review must retain each exact case acceptance requirement")
            merged.setdefault(group, {})[key] = check
    intervention = incoming.get("artist_interventions", {})
    if intervention.get("count") is not None:
        count = intervention["count"]
        if type(count) is not int or count < 0 or not intervention.get("evidence"):
            raise ValueError("Artist correction counts require a nonnegative integer and evidence")
        previous_count = merged.get("artist_interventions", {}).get("count")
        if type(previous_count) is int and count < previous_count:
            raise ValueError("An observed artist correction cannot disappear from the review record")
        merged["artist_interventions"] = intervention
    with path.with_suffix(".history.jsonl").open("a") as stream:
        stream.write(canonical(dict(previous=previous, incoming=incoming)).decode() + "\n")
    write_json(path, merged)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    config = commands.add_parser("config")
    config.add_argument("--output", type=Path, required=True)
    config.add_argument("--baseline-checkout", type=Path)
    pilot = commands.add_parser("pilot")
    pilot.add_argument("--fixtures", type=Path, required=True)
    pilot.add_argument("--output", type=Path, required=True)
    pilot.add_argument("--case", default="T01")
    pilot.add_argument("--mode", choices=("P", "H"), default="H")
    pilot.add_argument("--model", required=True)
    pilot.add_argument("--quick", action="store_true", help="Non-scored plumbing pilot only; skip channel/renders/export/4K evidence")
    pilot.add_argument("--extended-checks", action="store_true", help="Opt in to supplemental offline/export/4K checks")
    smoke = commands.add_parser("smoke", help="Five diagnostic H sessions; never a scored comparison")
    smoke.add_argument("--fixtures", type=Path, required=True)
    smoke.add_argument("--output", type=Path, required=True)
    smoke.add_argument("--model", required=True)
    smoke.add_argument("--limits", type=Path, help="JSON containing identical per-turn/session caps for smoke and campaign")
    smoke.add_argument("--jobs", type=int, choices=(1, 2), default=1)
    smoke.add_argument("--concurrency-evidence", type=Path, help="Required actual no-provider isolation proof for --jobs 2")
    smoke.add_argument("--resume", action="store_true", help="Resume genuinely unattempted cases from the same immutable diagnostic snapshot")
    freeze = commands.add_parser("freeze")
    freeze.add_argument("--config", type=Path, required=True)
    freeze.add_argument("--fixtures", type=Path, required=True)
    freeze.add_argument("--output", type=Path, required=True)
    freeze.add_argument("--evidence-config", type=Path, help="Evaluator-only frozen annotations/settings JSON; never shared with the Director")
    freeze.add_argument("--controlled-matrix", action="store_true", help="Optional P/H × three-repeat comparison instead of five primary H sessions")
    freeze.add_argument("--extended-checks", action="store_true", help="Predeclare supplemental offline/export/4K checks")
    freeze.add_argument("--include-robustness", action="store_true", help="Add the archived eight safety scenarios for each selected mode")
    freeze.add_argument("--correction-ablation", action="store_true", help="Add 30 improved correction-disabled sessions")
    freeze.add_argument("--jobs", type=int, choices=(1,2))
    freeze.add_argument("--concurrency-evidence", type=Path)
    run = commands.add_parser("run")
    run.add_argument("--comparison", type=Path, required=True)
    run.add_argument("--fixtures", type=Path, required=True)
    run.add_argument("--max-sessions", type=int)
    run.add_argument("--kind", choices=("all", "core", "robustness"), default="all")
    run.add_argument("--baseline-checkout", type=Path)
    run.add_argument("--improved-checkout", type=Path, default=ROOT)
    run.add_argument("--jobs", type=int, choices=(1,2), help="Must match the frozen execution policy")
    recovery = commands.add_parser("recover-evidence", help="Capture a saved checkpoint offline without running the provider")
    recovery.add_argument("--session", type=Path, required=True)
    recovery.add_argument("--state", choices=("failure", "before", "after_create", "after_refine_1", "after_refine_2", "final"), required=True)
    recovery.add_argument("--output", type=Path, required=True)
    recovery.add_argument("--blender", default=BLENDER)
    recovery.add_argument("--product-checkout", type=Path, help="Relocated product snapshot; v3 must match the recorded product digest")
    for command in (pilot, run, smoke):
        command.add_argument("--blender", default=BLENDER)
        command.add_argument("--codex", default=shutil.which("codex"))
        command.add_argument("--use-codex-login", action="store_true", required=True)
        command.add_argument("--auth-source", type=Path, help="Optional explicitly chosen Codex auth.json; never logged or modified")
    for name in ("report", "review-pack"):
        command = commands.add_parser(name)
        command.add_argument("--comparison", type=Path, required=True)
    review_import = commands.add_parser("review-import")
    review_import.add_argument("--comparison", type=Path, required=True)
    review_import.add_argument("--review", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "config":
        value = configuration_template()
        value["build"].update({k:v for k,v in source_fingerprint().items() if k != "files"})
        value["builds"]["improved"] = product_fingerprint(ROOT)
        if args.baseline_checkout:
            value["builds"]["baseline"] = product_fingerprint(args.baseline_checkout)
        value["environment"]["hardware"] = hardware_record()
        value["environment"]["material_library_sha256"] = library_fingerprint()
        value["environment"]["evaluator_backend"] = "Open Painter LayerEvaluator CPU"
        value["environment"]["render_backend"] = "BLENDER_EEVEE"
        write_json(args.output, value)
        print(args.output)
    elif args.command == "pilot":
        case = next((c for c in load_cases()["cases"] if c["id"] == args.case), None)
        kind = "core"
        if case is None and args.case in {scenario["id"] for scenario in ROBUSTNESS}:
            case = dict(id=args.case, create="", refinements=[])
            case["fixture"] = "A01_toolbox_neutral.blend"
            kind = "robustness"
        if case is None:
            parser.error("Unknown benchmark case")
        job = make_job(case, mode=args.mode, repeat=1, fixture=args.fixtures / case["fixture"], output=args.output,
                       model=args.model, kind=kind, reference=args.fixtures / "R01_fabric_reference.png" if args.case == "MD-08" else None)
        job["pilot"] = True
        job["supplements"]["extended_checks"] = args.extended_checks
        result = execute(job, blender=args.blender, codex=args.codex, use_login=args.use_codex_login,
                         auth_source=args.auth_source, quick=args.quick)
        print(json.dumps(dict(run_id=result["run_id"], status=result["status"], pilot=True, seconds=result["elapsed_seconds"])))
        return 0 if result["status"] == "COMPLETED" else 1
    elif args.command == "smoke":
        from concurrent.futures import ThreadPoolExecutor
        import threading
        import signal
        proof = _read(args.concurrency_evidence) if args.concurrency_evidence else None
        if args.jobs == 2 and (not proof or proof.get("status") != "PASS" or proof.get("processes") != 2 or proof.get("isolated_capture") is not True):
            parser.error("Two-process smoke requires an actual passed --concurrency-evidence proof")
        output = args.output.resolve()
        existing = output.exists() and any(p.name != "execution_control.json" for p in output.iterdir())
        if existing and not args.resume:
            parser.error("Smoke requires a fresh empty output directory; retain all prior diagnostics")
        if args.resume and not (output / "manifest.json").is_file():
            parser.error("--resume requires an existing immutable diagnostic manifest")
        output.mkdir(parents=True, exist_ok=True)
        limits = _read(args.limits) if args.limits else configuration_template()["limits"]
        if not _preflight_wave(output,args.auth_source,minimum_seconds=limits["session_seconds"]+120):
            return 3
        snapshot = output / "_runtime"
        product = source_fingerprint(snapshot) if args.resume else snapshot_runtime(snapshot)
        specification = load_cases()
        entries = [dict(run_id=f"{case['id']}-smoke-H-1", kind="core", case_id=case["id"], mode="H", repeat=1,
                        build_id="improved", relative_directory=f"{case['id']}/H/1") for case in specification["cases"]]
        diagnostic = dict(schema_version=3, protocol="five_asset_eevee_v3", status="DIAGNOSTIC_NOT_COMPARISON",
            specification=specification, schedule=entries, fixtures=fixture_records(args.fixtures),
            configuration=dict(comparison_id=output.name, build={k:v for k,v in product.items() if k!='files'},
                               execution=dict(jobs=args.jobs,policy="DIAGNOSTIC_WAVES" if args.jobs == 2 else "SERIAL",
                                   concurrency_proof=proof, concurrency_proof_sha256=digest(proof) if proof else None),
                               provider=dict(model_id=args.model), limits=limits,
                               product_deadlines=configuration_template()["product_deadlines"]))
        if args.resume:
            original = _read(output / "manifest.json")
            if original != diagnostic or source_fingerprint()["source_sha256"] != product["source_sha256"]:
                parser.error("Smoke resume requires identical fixture, model, budget, execution policy and immutable runtime pins")
        else:
            write_json(output / "manifest.json", diagnostic)
        index = _read(output / "smoke_index.json") or dict(status="AWAITING_INSPECTION", artistic_scores="NOT_SCORED", reviewer="PENDING", cases=[])
        jobs = []
        for entry, case in zip(entries, specification["cases"]):
            directory = output / entry["relative_directory"]
            if (directory / "attempt.json").exists() or _read(directory / "result.json").get("status", "NOT_RUN") != "NOT_RUN":
                continue
            if source_fingerprint(snapshot)["source_sha256"] != product["source_sha256"]:
                raise ValueError("Frozen product/harness snapshot changed during diagnostic smoke")
            job = make_job(case, mode="H", repeat=1, fixture=args.fixtures / case["fixture"], output=output / entry["relative_directory"],
                           model=args.model, limits=limits, run_id=entry["run_id"])
            job.update(pilot=True, build_id="improved", correction_enabled=True)
            job.update(product_root=str(snapshot), harness_root=str(snapshot / "tests/benchmarks/material_director"))
            job.update(concurrent_jobs=args.jobs,wave_id=len(jobs)//args.jobs+1)
            jobs.append((entry,job))
        cancel_event = threading.Event()
        previous_signals = _install_cancel_handlers(cancel_event)
        pool = ThreadPoolExecutor(max_workers=args.jobs)
        auth_paused = False
        try:
            for offset in range(0,len(jobs),args.jobs):
                if cancel_event.is_set():
                    break
                if not _preflight_wave(output,args.auth_source,minimum_seconds=limits["session_seconds"]+120,
                        next_run_ids=[entry["run_id"] for entry,_ in jobs[offset:offset+args.jobs]]):
                    auth_paused = True
                    break
                wave = []
                for entry,job in jobs[offset:offset+args.jobs]:
                    print("SMOKE", entry["run_id"], flush=True)
                    wave.append((entry,pool.submit(execute,job,blender=args.blender,codex=args.codex,
                        use_login=args.use_codex_login,auth_source=args.auth_source,cancel_event=cancel_event)))
                for entry,future in wave:
                    try:
                        result=future.result()
                    except AuthRefreshRequired as exc:
                        auth_paused = True
                        _auth_control(output,"AUTH_REFRESH_REQUIRED",minimum_seconds=limits["session_seconds"]+120,
                            next_run_ids=[entry["run_id"]],detail=str(exc))
                        continue
                    index["cases"].append(dict(case_id=entry["case_id"],result_path=entry["relative_directory"]+"/result.json",
                        execution_status=result["status"],inspection_notes=None))
                    write_json(output / "smoke_index.json", index)
                    print("SMOKE_RESULT",entry["run_id"],result["status"],flush=True)
                if auth_paused:
                    break
        except BaseException:
            cancel_event.set()
            raise
        finally:
            pool.shutdown(wait=True)
            for sig,previous in previous_signals.items():
                signal.signal(sig,previous)
        print(output / "smoke_index.json")
        return 3 if auth_paused else 0
    elif args.command == "freeze":
        configuration = json.loads(args.config.read_text(encoding="utf-8"))
        if args.controlled_matrix:
            configuration.update(modes=["P", "H"], repeat_count=3)
        if args.extended_checks:
            configuration["supplements"]["extended_checks"] = True
        if args.include_robustness:
            configuration["supplements"]["robustness"] = True
        if args.correction_ablation:
            configuration["correction_ablation"] = True
        if args.jobs is not None:
            configuration.setdefault("execution", deepcopy(configuration_template()["execution"]))
            configuration["execution"]["jobs"] = args.jobs
            configuration["execution"]["policy"] = "PAIRED_WAVES" if args.jobs == 2 else "SERIAL"
        if args.concurrency_evidence:
            proof = _read(args.concurrency_evidence)
            configuration["execution"].update(concurrency_evidence=str(args.concurrency_evidence.resolve()),
                concurrency_evidence_sha256=sha256(args.concurrency_evidence), concurrency_proof=proof,
                concurrency_proof_sha256=digest(proof))
        if args.evidence_config:
            configuration["evidence"] = _read(args.evidence_config)
        _verify_evidence(configuration)
        _verify_pilot(configuration)
        current = source_fingerprint()
        if configuration["build"]["source_sha256"] != current["source_sha256"]:
            parser.error("Build fingerprint changed; review and update the configuration before freezing")
        records = fixture_records(args.fixtures)
        if any(r["status"] != "READY" for r in records.values()):
            parser.error("Every fixture must have a READY preparation report")
        if configuration["environment"]["hardware"] != hardware_record():
            parser.error("Hardware must match the current observed config record")
        if configuration["environment"]["material_library_sha256"] != library_fingerprint():
            parser.error("Library pin differs from the installed library bytes")
        from material_director.png_validation import decoder_backend
        if configuration["environment"].get("png_decoder") != decoder_backend():
            parser.error("PNG decoder pin differs from the installed backend")
        freeze_manifest(args.output, configuration, records)
        print(args.output / "manifest.json")
    elif args.command == "run":
        if args.max_sessions is not None and args.max_sessions < 1:
            parser.error("--max-sessions must be positive")
        manifest = load_manifest(args.comparison / "manifest.json")
        configuration = manifest["configuration"]
        jobs = configuration.get("execution", {}).get("jobs", 1)
        if args.jobs is not None and args.jobs != jobs:
            parser.error("--jobs differs from the preregistered execution policy")
        from concurrent.futures import ThreadPoolExecutor
        import signal
        import threading
        cancel_event = threading.Event()
        previous_signals = _install_cancel_handlers(cancel_event)
        pool = ThreadPoolExecutor(max_workers=jobs)
        pending = []
        wave_number = 0
        auth_paused = False

        def drain_wave():
            nonlocal pending, auth_paused
            # Report once per completed wave; shared report files are never
            # written concurrently by worker threads.
            for item, future in pending:
                try:
                    result = future.result()
                except AuthRefreshRequired as exc:
                    auth_paused = True
                    path = args.comparison / item["relative_directory"]
                    if (path / "attempt.json").exists():
                        raise RuntimeError("Authentication control failure unexpectedly followed an attempt marker") from exc
                    _auth_control(args.comparison,"AUTH_REFRESH_REQUIRED",
                        minimum_seconds=configuration["limits"]["session_seconds"]+120,next_run_ids=[item["run_id"]],detail=str(exc))
                    record_result(args.comparison,item["run_id"],result_template(item))
                    continue
                except Exception as exc:
                    path = args.comparison / item["relative_directory"] / "result.json"
                    result = _read(path, result_template(item))
                    result.update(status="FAILED", reason="Session launcher exception: " + repr(exc))
                    result.setdefault("failures", []).append(dict(category="infrastructure", cause_code="SESSION_LAUNCH_EXCEPTION", message=repr(exc)))
                    write_json(path, result)
                record_result(args.comparison, item["run_id"], result)
                print("RESULT", item["run_id"], result["status"], flush=True)
            from material_director.report import write_report
            if pending:
                write_report(args.comparison)
            pending = []

        try:
            count = 0
            for entry in manifest["schedule"]:
                if cancel_event.is_set():
                    break
                if args.kind != "all" and entry["kind"] != args.kind:
                    continue
                directory = args.comparison / entry["relative_directory"]
                if (directory / "result.json").exists():
                    previous = json.loads((directory / "result.json").read_text(encoding="utf-8"))
                    if previous.get("status") != "NOT_RUN":
                        continue
                if args.max_sessions is not None and count >= args.max_sessions:
                    break
                if len(pending) == jobs:
                    drain_wave()
                    if auth_paused:
                        break
                if not pending:
                    if not _preflight_wave(args.comparison,args.auth_source,
                            minimum_seconds=configuration["limits"]["session_seconds"]+120,next_run_ids=[entry["run_id"]]):
                        auth_paused = True
                        break
                    wave_number += 1
                codex = _verify_run_pins(configuration, args.codex)
                product_root = None
                if manifest.get("schema_version", 2) >= 3:
                    product_root = args.baseline_checkout if entry["build_id"] == "baseline" else args.improved_checkout
                    if product_root is None:
                        parser.error("V3 baseline sessions require --baseline-checkout with the pinned unmodified product")
                    if product_fingerprint(product_root) != configuration["builds"][entry["build_id"]]:
                        parser.error("Product checkout differs from the frozen " + entry["build_id"] + " pin")
                evidence = _verify_evidence(configuration, manifest["fixtures"][entry["fixture"]]["sha256"])
                case = next((c for c in manifest["specification"]["cases"] if c["id"] == entry["case_id"]),
                            dict(id=entry["case_id"], create="", refinements=[]))
                fixture = args.fixtures / entry["fixture"]
                pinned = manifest["fixtures"][entry["fixture"]]
                if sha256(fixture) != pinned["sha256"]:
                    parser.error("Fixture changed since freezing: " + entry["fixture"])
                metadata = fixture.with_suffix(".json")
                if pinned.get("metadata_sha256") and sha256(metadata) != pinned["metadata_sha256"]:
                    parser.error("Fixture preparation report changed since freezing: " + metadata.name)
                if entry["case_id"] == "MD-08" and sha256(args.fixtures / "R01_fabric_reference.png") != manifest["fixtures"]["R01_fabric_reference.png"]["sha256"]:
                    parser.error("Reference image changed since freezing")
                job = make_job(case, mode=entry["mode"], repeat=entry["repeat"], fixture=fixture, output=directory,
                    model=configuration["provider"]["model_id"], limits=configuration["limits"], kind=entry["kind"],
                    reference=args.fixtures / "R01_fabric_reference.png" if entry["case_id"] == "MD-08" else None,
                    run_id=entry["run_id"])
                job["runtime_version"] = configuration["provider"]["runtime_version"]
                job["protocol_version"] = manifest["schema_version"]
                job["protocol"] = manifest["protocol"]
                job["build_id"] = entry.get("build_id")
                job["correction_enabled"] = entry.get("correction_enabled", False)
                job.update(concurrent_jobs=jobs, wave_id=wave_number,
                           concurrency_policy=configuration.get("execution", {"jobs":1,"policy":"SERIAL"}))
                if product_root is not None:
                    job["product_root"] = str(product_root.resolve())
                job["supplements"] = configuration.get("supplements", dict(extended_checks=True))
                job["generation_available"] = configuration["provider"]["image_generation_available"]
                job["expected_environment"] = configuration["environment"]
                job["evidence"] = dict(evidence, protocol_version=manifest["schema_version"])
                job["common_instruction"] = manifest["specification"]["common_instruction"]
                job["comparison_sha256"] = digest(manifest)
                running = result_template(entry)
                running["status"] = "RUNNING"
                record_result(args.comparison, entry["run_id"], running)
                print("RUN", entry["run_id"], flush=True)
                pending.append((entry, pool.submit(execute, job, blender=args.blender, codex=codex,
                                                  use_login=args.use_codex_login, auth_source=args.auth_source,
                                                  cancel_event=cancel_event)))
                count += 1
        finally:
            if sys.exc_info()[0] is not None:
                cancel_event.set()
            try:
                drain_wave()
            finally:
                pool.shutdown(wait=True)
                for sig, previous in previous_signals.items():
                    signal.signal(sig, previous)
        if auth_paused:
            return 3
    elif args.command == "recover-evidence":
        session_dir, output = args.session.resolve(), args.output.resolve()
        if output.exists() and any(output.iterdir()):
            parser.error("Evidence recovery requires a new empty directory")
        output.mkdir(parents=True, exist_ok=True)
        job, result = _read(session_dir / "job.json"), _read(session_dir / "result.json")
        product_root = args.product_checkout or Path(job.get("product_root", ROOT))
        product_build = product_fingerprint(product_root)
        if job.get("protocol_version", 2) >= 3 and product_build != job.get("build"):
            parser.error("V3 recovery requires the exact originally recorded product snapshot")
        state = session_dir / (args.state + ".blend")
        if args.state == "failure": pinned = result.get("failure_state", {}).get("sha256")
        elif args.state == "final": pinned = result.get("finalization", {}).get("final_blend", {}).get("sha256")
        else: pinned = _read(session_dir / (args.state + "_state.json")).get("saved_state", {}).get("sha256")
        if not state.is_file() or not pinned or sha256(state) != pinned:
            parser.error("Recovery requires an existing checkpoint with its original recorded checksum")
        request = dict(state=str(state), state_sha256=pinned, output=str(output), original_run=result.get("run_id"),
            product_build=product_build, product_provenance="ORIGINAL_PRODUCT_VERIFIED" if job.get("protocol_version", 2) >= 3 else
                "HISTORICAL_DIAGNOSTIC_CURRENT_PRODUCT; original historical product equivalence is not established",
            label="recovered_" + args.state, config=dict(job.get("evidence", {}), render_views=True, capture_channels=True,
                                                     case_id=job.get("case", {}).get("id")))
        request_path = output / "recovery_request.json"
        write_json(request_path, request)
        with tempfile.TemporaryDirectory(prefix="director-recovery-offline-") as profile:
            env = dict(os.environ, CODEX_HOME=profile, BLENDER_USER_RESOURCES=profile,
                       TMPDIR=profile,
                       OP_MD_PRODUCT_ROOT=str(product_root.resolve()),
                       OP_MD_EVIDENCE_RECOVERY=str(request_path))
            for key in ("OPENAI_API_KEY", "OP_MD_BENCHMARK_AUTH", "OP_MD_BENCHMARK_CODEX"):
                env.pop(key, None)
            code = run_blender([args.blender, "--factory-startup", "--disable-autoexec", "--offline-mode",
                "--python-exit-code", "1", "--python", str(HERE / "recover_evidence.py")], env=env, timeout=600, log=output / "recovery.log")
        if code != 0:
            recovered = _read(output / "recovery.json")
            recovered.update(status="CAPTURE_FAILED", cause_code="BLENDER_RECOVERY_PROCESS_FAILED", exit_code=code,
                             provider_connected=False, ai_success=False, role="RECOVERED_EVIDENCE",
                             checkpoint_bytes_unchanged=sha256(state) == pinned)
            write_json(output / "recovery.json", recovered)
        print(output / "recovery.json")
        return 0 if code == 0 and _read(output / "recovery.json").get("status") == "CAPTURED_VERIFIED" else 1
    elif args.command == "report":
        from material_director.report import write_report
        write_report(args.comparison)
        print(args.comparison / "report.md")
    elif args.command == "review-pack":
        from material_director.report import export_review_packets
        print(export_review_packets(args.comparison))
    else:
        from material_director.report import write_report
        print(_import_review(args.comparison, args.review))
        write_report(args.comparison)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
