"""Frozen cases, run order, and review forms for the live benchmark.

The source case manifest is copied verbatim from the supplied benchmark pack.
Nothing in this module opens Blender, calls a provider, or grades appearance.
"""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import re


ROOT = Path(__file__).resolve().parent
MODES = ("P", "H")
REPEATS = (1, 2, 3)
PROTOCOL = "five_asset_eevee_v2"
CORE_COUNT = 5
ROBUSTNESS_COUNT = 16
STAGES = ("create", "refine_1", "refine_2")
RESULT_STATUSES = frozenset({"NOT_RUN", "RUNNING", "COMPLETED", "FAILED",
                             "BLOCKED", "UNAVAILABLE", "INTERRUPTED"})

ROBUSTNESS = (
    {"id": "R-01", "action": "Remove the active UV map in a disposable copy, then request a material.",
     "expected": "Explain the missing prerequisite or use a genuinely supported safe path. No fake successful UV-textured result, damaged mesh or unrelated edits."},
    {"id": "R-02", "action": "Remove a required map, or modify geometry so a map becomes stale, then request edge wear.",
     "expected": "Diagnose freshness/availability, use a valid live alternative, or report the limitation. Do not silently present stale placement as current."},
    {"id": "R-03", "action": "Edit the target material while a request is in flight, or after a Plan draft is ready.",
     "expected": "Reject the stale draft or require a rerun. Preserve the user's intervening change."},
    {"id": "R-04", "action": "Switch the active texture set while a request is in flight, then attempt to apply.",
     "expected": "Do not redirect edits to the newly active set. Require returning to the original target or rerunning where appropriate."},
    {"id": "R-05", "action": "Stop a request during optional image generation and then start a new one.",
     "expected": "The stopped turn cannot apply late; the next request is not contaminated by old images or responses. No orphaned material changes."},
    {"id": "R-06", "action": "Lose the provider connection before completion; separately inject a compile/apply failure in an isolated harness.",
     "expected": "Distinguish provider and local errors; preserve the last committed state; no partial scene transaction. Label the injected branch as a robustness test, never a live AI quality run."},
    {"id": "R-07", "action": "Restrict scope to one layer/folder containing an artist change and protected siblings, then request a change.",
     "expected": "Affect only allowed components and preserve the protected artist work. An honest refusal is better than silently escaping scope."},
    {"id": "R-08", "action": "Run Plan, inspect without applying, then discard; run Explain on the same state.",
     "expected": "Neither operation changes the material. Plan is explicitly unapplied; Explain does not claim to have textured the asset."},
)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_sha256(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def load_cases(path=None):
    data = json.loads(Path(path or ROOT / "test_cases.json").read_text(encoding="utf-8"))
    cases = data["cases"]
    if [case["id"] for case in cases] != [f"T{i:02d}" for i in range(1, 6)]:
        raise ValueError("The primary benchmark requires exactly T01 through T05")
    if (data["repeat_count"] != 1 or data["core_session_count"] != CORE_COUNT
            or data["maximum_core_user_turns"] != 15
            or [lane["id"] for lane in data["lanes"]] != list(MODES)):
        raise ValueError("The primary benchmark requires five H-enabled case sessions")
    for case in cases:
        if len(case["refinements"]) != 2 or len(case["requirements"]) != 5:
            raise ValueError(f"{case['id']} must have two refinements and five checks")
    return data


def prompts(case, specification=None):
    specification = specification or load_cases()
    return {"create": case["create"] + "\n\n" + specification["common_instruction"],
            "refine_1": case["refinements"][0], "refine_2": case["refinements"][1]}


def build_schedule(seed=20260922, specification=None, *, include_robustness=False, extended_checks=False,
                   modes=None, repeat_count=None):
    """Deterministically shuffle cases in each lane; alternate lane order by repeat."""
    specification = specification or load_cases()
    modes = tuple(modes or specification.get("modes", ("H",)))
    repeats = tuple(range(1, (repeat_count or specification["repeat_count"]) + 1))
    rng = random.Random(seed)
    schedule = []
    for repeat in repeats:
        for mode in modes if repeat % 2 else tuple(reversed(modes)):
            cases = list(specification["cases"])
            rng.shuffle(cases)
            for case in cases:
                run_id = f"{case['id']}-{mode}-{repeat}"
                schedule.append({"run_id": run_id, "kind": "core", "case_id": case["id"],
                                 "mode": mode, "repeat": repeat, "fixture": case["fixture"],
                                 "reference": case.get("reference"), "scope": case["scope"],
                                 "resolution_check_4k": extended_checks and repeat == 1,
                                 "relative_directory": f"{case['id']}/{mode}/{repeat}"})
    for mode in modes if include_robustness else ():
        scenarios = list(ROBUSTNESS)
        rng.shuffle(scenarios)
        for scenario in scenarios:
            schedule.append({"run_id": f"{scenario['id']}-{mode}", "kind": "robustness",
                             "case_id": scenario["id"], "mode": mode, "repeat": 1,
                             "fixture": "A01_toolbox_neutral.blend",
                             "relative_directory": f"robustness/{scenario['id']}/{mode}"})
    review_order = list(range(len(specification["cases"]) * len(modes) * len(repeats)))
    random.Random(f"{seed}:anonymous-review-order").shuffle(review_order)
    review_ids = {index: f"REVIEW-{number:04d}" for number, index in enumerate(review_order, 1)}
    for index, entry in enumerate(schedule):
        entry["order"] = index + 1
        if entry["kind"] == "core":
            entry["review_id"] = review_ids[index]
    return schedule


def configuration_template():
    """Unresolved fields deliberately prevent a scored comparison from being frozen."""
    return {
        "protocol": PROTOCOL,
        "modes": ["H"], "repeat_count": 1,
        "comparison_id": None,
        "build": {"commit": None, "source_sha256": None},
        "provider": {"name": "Codex", "runtime_version": None, "model_id": None,
                     "returned_model_id": None, "seed_setting": "uncontrolled",
                     "image_generation_available": None},
        "environment": {"hardware": None, "blender_version": None,
                        "render_backend": None, "evaluator_backend": None,
                        "material_library_sha256": None},
        "limits": {"single_asset_turn_seconds": 600, "multi_set_turn_seconds": 900,
                   "session_seconds": 1800},
        "pilot": {"status": "NOT_RUN", "evidence": None},
        "value_claim": None,
        "artist_baseline": {"status": "UNAVAILABLE", "active_budget_seconds": 1200,
                            "reason": "No qualified artist baseline has been supplied."},
        "scoring": {"weights": {"material_appearance": 25, "spatial_placement": 25,
                                 "prompt_compliance": 20, "editability": 15, "revision_fidelity": 15},
                    "numeric_pass_threshold": None, "failed_autonomy_interventions": 3},
        "sharing": {"send_current_3d_view": True, "send_mesh_maps": True,
                    "reference_only_case": None},
        "output_resolution": 1024,
        "render_engine": "BLENDER_EEVEE", "render_resolution": [1024, 1024],
        "supplements": {"robustness": False, "extended_checks": False, "baselines": False},
        "engine_validation": {"Unreal": "NOT_TESTED", "Unity": "NOT_TESTED"},
    }


def validate_configuration(configuration):
    if configuration.get("protocol") != PROTOCOL:
        raise ValueError("Use a new comparison for the revised five-asset Eevee protocol")
    if configuration.get("modes") not in (["H"], ["P"], ["P", "H"]):
        raise ValueError("Choose H for the primary run, or explicit P/H controlled comparison")
    if type(configuration.get("repeat_count")) is not int or configuration["repeat_count"] not in (1, 3):
        raise ValueError("Choose one primary repeat or three controlled comparison repeats")
    fields = (
        ("comparison_id",), ("build", "commit"), ("build", "source_sha256"),
        ("provider", "name"), ("provider", "runtime_version"), ("provider", "model_id"),
        ("provider", "returned_model_id"), ("provider", "seed_setting"),
        ("environment", "hardware"), ("environment", "blender_version"),
        ("environment", "render_backend"), ("environment", "evaluator_backend"),
        ("environment", "material_library_sha256"), ("pilot", "evidence"),
    )
    missing = []
    for keys in fields:
        value = configuration
        for key in keys:
            value = value.get(key) if isinstance(value, dict) else None
        if value is None or value == "" or (isinstance(value, str) and any(
                marker in value.upper() for marker in ("RECORD_", "UNKNOWN", "ACCOUNT DEFAULT"))):
            missing.append(".".join(keys))
    if missing:
        raise ValueError("Resolve comparison pins before freezing: " + ", ".join(missing))
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", configuration["comparison_id"]):
        raise ValueError("comparison_id must be a filename-safe identifier")
    for value in (configuration["build"]["source_sha256"],
                  configuration["environment"]["material_library_sha256"]):
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("Build and library pins must be SHA-256 digests")
    if configuration["provider"]["model_id"] != configuration["provider"]["returned_model_id"]:
        raise ValueError("Pin the exact model identifier returned by the pilot")
    if type(configuration["provider"].get("image_generation_available")) is not bool:
        raise ValueError("Record pilot image-generation availability as true or false")
    if configuration.get("pilot", {}).get("status") != "PASS":
        raise ValueError("Operational limits require a completed, non-scored pilot")
    for key in ("single_asset_turn_seconds", "multi_set_turn_seconds", "session_seconds"):
        value = configuration.get("limits", {}).get(key)
        if type(value) not in (int, float) or not 0 < value < float("inf"):
            raise ValueError(f"limits.{key} must be a positive number")
    if configuration.get("value_claim") not in (None, "quality", "efficiency"):
        raise ValueError("Optional baseline claims must be predeclared as quality or efficiency")
    defaults = configuration_template()
    for key in ("scoring", "sharing", "output_resolution", "render_engine", "render_resolution"):
        if configuration.get(key) != defaults[key]:
            raise ValueError(f"{key} must match the supplied benchmark specification")
    if configuration["environment"]["render_backend"] != "BLENDER_EEVEE":
        raise ValueError("The fixed primary renderer must be BLENDER_EEVEE")
    if set(configuration.get("supplements", {})) != {"robustness", "extended_checks", "baselines"} or any(
            type(value) is not bool for value in configuration["supplements"].values()):
        raise ValueError("Declare robustness, extended_checks and baselines as explicit boolean supplements")
    return configuration


def review_template(entry, case, specification=None):
    from ..scoring import DIMENSIONS, WORKFLOW_CHECKS
    return {
        "review_id": entry["review_id"], "brief": prompts(case, specification),
        "instructions": "Use anonymized multiview evidence. The generating AI cannot be the final judge. "
                        "Record independent reviewers and relevant expertise; leave absent evidence null.",
        "reviewers": [],
        "stages": {stage: {"ratings": [],
                            "no_op_acceptance": {"passed": None, "evidence": [], "notes": ""},
                            "rating_template": {"reviewer_id": None,
                                                "dimensions": {name: None for name in DIMENSIONS},
                                                "evidence": []}}
                   for stage in ("create", "refine_1", "refine_2")},
        "acceptance_checks": {f"AC-{i:02d}": {"requirement": requirement, "passed": None,
                                                "evidence": [], "notes": ""}
                              for i, requirement in enumerate(case["requirements"], 1)},
        "workflow_checks": {key: {"passed": None, "evidence": [], "notes": ""} for key in WORKFLOW_CHECKS},
        "artist_interventions": {"count": None, "evidence": [], "notes": "Count additional human corrections beyond the three predefined prompts."},
        "notes": "",
    }


def result_template(entry):
    return {
        "run_id": entry["run_id"], "status": "NOT_RUN", "attempted": False,
        "assisted": False, "reason": "", "environment": {},
        "artist_intervention_count": None, "artist_interventions": [],
        "started_at": None, "completed_at": None,
        "stages": {stage: {"status": "NOT_RUN", "applied": None,
                            "visible_complete": None, "evidence": []}
                   for stage in STAGES},
        "checks": {}, "hard_failures": [], "failures": [],
        "generated_inputs": {"availability": "NOT_TESTED", "generated": 0, "assigned": 0},
        "metrics": {"provider_wait_seconds": None, "local_apply_seconds": None,
                    "preview_settling_seconds": None, "baking_export_seconds": None,
                    "active_operator_seconds": None, "elapsed_seconds": None,
                    "generation_calls": None, "tool_calls": None,
                    "monetary_cost": {"amount": None, "currency": None,
                                      "status": "UNKNOWN"}},
        "safety": {"status": "NOT_RUN", "checks": {}, "evidence": []},
        "resolution_4k": {"status": "NOT_RUN" if entry.get("resolution_check_4k") else "NOT_SCHEDULED",
                          "evidence": []},
    }


def control_template(case, kind, configuration):
    from ..scoring import DIMENSIONS
    return {"case_id": case["id"], "kind": kind, "status": "NOT_RUN",
            "brief": prompts(case), "fixture": case["fixture"],
            "configuration_sha256": digest(configuration), "fixture_sha256": None,
            "qualification": None,
            "active_budget_seconds": configuration["artist_baseline"]["active_budget_seconds"]
            if kind == "artist" else None,
            "active_operator_seconds": None, "elapsed_seconds": None,
            "reviewers": [], "ratings": [],
            "rating_template": {"reviewer_id": None, "dimensions": {key: None for key in DIMENSIONS},
                                "evidence": []},
            "evidence": [], "notes": "No AI calls or original source materials are allowed."}


def _write_json_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def freeze_manifest(directory, configuration, fixtures, seed=20260922):
    """Create a new comparison; refuse reuse or replacement of its run order/pins."""
    validate_configuration(configuration)
    specification = load_cases()
    required = {case["fixture"] for case in specification["cases"]}
    if not isinstance(fixtures, dict) or not required.issubset(fixtures):
        raise ValueError("Every declared neutral fixture must be pinned before freezing")
    for name in required:
        if not re.fullmatch(r"[0-9a-f]{64}", fixtures[name].get("sha256", "")):
            raise ValueError(f"Missing fixture SHA-256: {name}")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if any(directory.iterdir()):
        raise FileExistsError("Freeze into an empty comparison directory; never replace an existing comparison")
    manifest = {
        "schema_version": 2, "protocol": PROTOCOL, "status": "FROZEN_NOT_RUN",
        "created_at": datetime.now(timezone.utc).isoformat(), "seed": seed,
        "configuration": deepcopy(configuration), "fixtures": deepcopy(fixtures),
        "specification_sha256": file_sha256(ROOT / "test_cases.json"),
        "test_plan_sha256": file_sha256(ROOT / "test_plan.md"),
        "specification": specification, "robustness": list(ROBUSTNESS),
        "schedule": build_schedule(seed, specification,
                                   include_robustness=configuration["supplements"]["robustness"],
                                   extended_checks=configuration["supplements"]["extended_checks"],
                                   modes=configuration["modes"], repeat_count=configuration["repeat_count"]),
    }
    _write_json_new(directory / "manifest.json", manifest)
    (directory / "manifest.sha256").write_text(digest(manifest) + "\n", encoding="utf-8")
    cases = {case["id"]: case for case in specification["cases"]}
    for entry in manifest["schedule"]:
        _write_json_new(directory / entry["relative_directory"] / "result.json", result_template(entry))
        if entry["kind"] == "core":
            _write_json_new(directory / "reviews" / f"{entry['review_id']}.json",
                            review_template(entry, cases[entry["case_id"]], specification))
    _write_json_new(directory / "review_key.private.json",
                    {entry["review_id"]: entry["run_id"] for entry in manifest["schedule"]
                     if entry["kind"] == "core"})
    for case in cases.values() if configuration["supplements"]["baselines"] else ():
        for kind in ("preset", "artist"):
            _write_json_new(directory / "controls" / f"{case['id']}-{kind}.json",
                            control_template(case, kind, configuration))
    return manifest


def load_manifest(path):
    path = Path(path)
    if path.is_dir():
        path /= "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") == 1:
        from ..legacy.manifest import load_manifest as load_legacy
        return load_legacy(path)
    expected = path.with_suffix(".sha256").read_text(encoding="utf-8").strip()
    if digest(manifest) != expected:
        raise ValueError("Frozen manifest changed: use a new comparison for new settings or run order")
    validate_configuration(manifest["configuration"])
    supplements = manifest["configuration"]["supplements"]
    expected_schedule = build_schedule(manifest["seed"], manifest["specification"],
                                       include_robustness=supplements["robustness"],
                                       extended_checks=supplements["extended_checks"],
                                       modes=manifest["configuration"]["modes"],
                                       repeat_count=manifest["configuration"]["repeat_count"])
    if manifest["schedule"] != expected_schedule:
        raise ValueError("Run schedule does not match the frozen deterministic schedule")
    return manifest


def verify_environment(manifest, observed):
    """Called before each live session; changes require a separate comparison."""
    expected = manifest["configuration"]
    mismatches = []
    for section in ("build", "provider", "environment"):
        for key, value in expected[section].items():
            if observed.get(section, {}).get(key) != value:
                mismatches.append(f"{section}.{key}")
    if mismatches:
        raise ValueError("Comparison environment drift: " + ", ".join(mismatches))
    return True


def record_result(directory, run_id, result):
    """Update a scheduled outcome, retaining every prior revision in an append-only log."""
    directory = Path(directory)
    manifest = load_manifest(directory)
    matches = [item for item in manifest["schedule"] if item["run_id"] == run_id]
    if not matches:
        raise ValueError(f"Unscheduled result: {run_id}")
    if result.get("run_id", run_id) != run_id or result.get("status") not in RESULT_STATUSES:
        raise ValueError("Result ID/status does not match the scheduled session")
    entry = matches[0]
    result = deepcopy(result)
    result["run_id"] = run_id
    result["manifest_sha256"] = digest(manifest)
    path = directory / entry["relative_directory"] / "result.json"
    previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    if previous and previous.get("status") not in {"NOT_RUN", "RUNNING"}:
        # Later evidence/reviews may augment a result, but cannot replace its live attempt.
        for field in ("status", "started_at", "completed_at", "stages", "hard_failures"):
            if previous.get(field) != result.get(field):
                raise ValueError("Cannot replace a finished attempt; create a separately labeled diagnostic")
    with path.with_name("result_history.jsonl").open("a", encoding="utf-8") as history:
        history.write(canonical({"recorded_at": datetime.now(timezone.utc).isoformat(),
                                 "previous": previous, "result": result}).decode() + "\n")
    temporary = path.with_name("result.json.tmp")
    temporary.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path
