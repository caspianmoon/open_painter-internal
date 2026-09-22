"""Evidence-aware scoring. Missing outputs and reviews never become aesthetic zeroes."""

from collections import Counter
from math import isfinite
from statistics import mean, median


DIMENSIONS = {
    "material_appearance": 25,
    "spatial_placement": 25,
    "prompt_compliance": 20,
    "editability": 15,
    "revision_fidelity": 15,
}
WORKFLOW_CHECKS = ("input_integrity", "evidence_delivery", "preservation", "editability_probe")
HARD_FAILURES = frozenset({"crash", "unauthorized_edit", "lost_artist_work", "corrupted_save",
                          "missing_final_material_after_claimed_apply", "falsely_claimed_apply"})
DESTRUCTIVE_FAILURES = frozenset({"unauthorized_edit", "lost_artist_work", "corrupted_save",
                                 "cross_target_edit", "partial_scene_transaction"})
FAILURE_CATEGORIES = frozenset({"missing_capability", "perception_context", "planning_instruction_following",
                                "surface_generation_quality", "material_construction_evaluation",
                                "transaction_persistence", "export", "infrastructure"})

# Versioned policy. Calibration is declared separately from independent human
# ratings; synthetic unit tests exercise the gate without inventing reviews.
QUALITY_POLICY = {"version": "director_quality_v1", "final_overall_minimum": 80,
                  "final_appearance_minimum": 4, "final_dimension_minimum": 3,
                  "stage_appearance_minimum": 3, "require_all_stage_reviews": True,
                  "require_all_stage_acceptance": True}


def _number(value):
    return type(value) in (int, float) and isfinite(value)


def interventions(result, review=None):
    value = result.get("artist_intervention_count", result.get("manual_artistic_interventions"))
    value = value if type(value) is int and value >= 0 else None
    verified = (review or {}).get("artist_interventions", {})
    manual = verified.get("count")
    if type(manual) is int and manual >= 0 and verified.get("evidence"):
        value = max(value, manual) if value is not None else manual
    return value


def intervention_class(count):
    if count is None:
        return "UNKNOWN"
    return "UNASSISTED" if count == 0 else "MINOR_ASSISTANCE" if count == 1 else "SIGNIFICANT_ASSISTANCE" if count == 2 else "FAILED_AUTONOMOUS_WORKFLOW"


def _check(value):
    """Evidence is mandatory: an unsupported PASS is an incomplete assessment."""
    if not isinstance(value, dict) or not value.get("evidence"):
        return None
    return value.get("passed") if type(value.get("passed")) is bool else None


def stage_map(result):
    """The live runner emits ordered stages; hand-authored records may use a mapping."""
    stages = result.get("stages", {})
    if isinstance(stages, list):
        names = [stage.get("name") for stage in stages]
        if len(names) != len(set(names)) or any(not name for name in names):
            raise ValueError("Stage records need unique names")
        return {stage["name"]: stage for stage in stages}
    return stages


def reviewed_score(ratings, reviewers):
    """Median of independent reviewer ratings; one reviewer is permitted and disclosed."""
    records = {record.get("id"): record for record in reviewers if isinstance(record, dict)}
    values = {dimension: [] for dimension in DIMENSIONS}
    used = []
    errors = []
    for rating in ratings:
        reviewer_id = rating.get("reviewer_id")
        reviewer = records.get(reviewer_id)
        if reviewer_id in used:
            errors.append(f"Duplicate reviewer: {reviewer_id}")
            continue
        if (not reviewer or reviewer.get("independent") is not True
                or reviewer.get("is_generating_ai") is not False):
            errors.append(f"Reviewer independence/identity is unverified: {reviewer_id}")
            continue
        if not rating.get("evidence"):
            errors.append(f"Reviewer evidence is missing: {reviewer_id}")
            continue
        dimensions = rating.get("dimensions", {})
        if set(dimensions) != set(DIMENSIONS) or any(
                not _number(value) or not 0 <= value <= 5 for value in dimensions.values()):
            errors.append(f"Each reviewer must rate all five dimensions between 0 and 5: {reviewer_id}")
            continue
        used.append(reviewer_id)
        for dimension, value in dimensions.items():
            values[dimension].append(value)
    if not used or errors:
        return {"status": "NOT_SCORED", "score": None, "dimensions": {},
                "reviewer_count": len(used), "errors": errors, "limitations": []}
    medians = {dimension: median(scores) for dimension, scores in values.items()}
    total = sum(medians[dimension] / 5 * weight for dimension, weight in DIMENSIONS.items())
    limitations = []
    if len(used) < 3:
        limitations.append(f"Only {len(used)} independent reviewer(s); no three-reviewer consensus")
    roles = {records[reviewer_id].get("role") for reviewer_id in used}
    if "material_artist" not in roles:
        limitations.append("No identified experienced material/texture artist reviewer")
    if "game_asset_usability" not in roles:
        limitations.append("No identified game-asset usability reviewer")
    return {"status": "SCORED", "score": round(total, 4), "dimensions": medians,
            "reviewer_count": len(used), "reviewer_ids": used,
            "errors": [], "limitations": limitations,
            "numeric_pass_threshold": 80, "raw_ratings": ratings,
            "dimension_ranges": {dimension: [min(scores), max(scores)] for dimension, scores in values.items()}}


def _hard_failures(result):
    failures = list(result.get("hard_failures", []))
    for stage_name, stage in stage_map(result).items():
        if stage.get("claimed_apply") is True and stage.get("applied") is not True and stage.get("visible_complete") is False:
            failures.append({"kind": "missing_final_material_after_claimed_apply", "stage": stage_name,
                             "evidence": stage.get("evidence", [])})
    return failures


def score_session(result, case, review=None, *, policy=None):
    """A delivered session only passes after its independent review and handoff checks."""
    review = review or {}
    policy = policy or QUALITY_POLICY
    if policy != QUALITY_POLICY:
        raise ValueError("Unknown quality policy; declare and implement a new policy version")
    status = result.get("status", "NOT_RUN")
    hard_failures = _hard_failures(result)
    checks = {key: _check(result.get("checks", {}).get(key)) for key in WORKFLOW_CHECKS}
    # A reviewer can finish genuinely pending artistic/probe assessments. A
    # failed measured invariant cannot be overwritten by a favorable opinion.
    for key, measured in checks.items():
        manual = _check(review.get("workflow_checks", {}).get(key))
        if measured is None and manual is not None:
            checks[key] = manual
        elif manual is False:
            checks[key] = False
    acceptance = {f"AC-{index:02d}": _check(review.get("acceptance_checks", {}).get(f"AC-{index:02d}"))
                  for index in range(1, len(case["requirements"]) + 1)}
    invalid_input = checks["input_integrity"] is False
    evidence_failed = checks["evidence_delivery"] is False
    stages = {}
    for name in ("create", "refine_1", "refine_2"):
        stage = stage_map(result).get(name, {})
        stage_review = review.get("stages", {}).get(name, {})
        # A legitimate unchanged state requires explicit evaluator confirmation. Provider prose
        # is not proof: an evaluator/operator must confirm the no-op with evidence.
        accepted_no_op = name != "create" and (
            (stage.get("no_op_confirmed") is True and bool(stage.get("no_op_evidence")))
            or _check(stage_review.get("no_op_acceptance")) is True)
        execution = stage.get("execution", {})
        execution_state = execution.get("state", stage.get("status")) if isinstance(execution, dict) else execution
        role = stage.get("evidence", {}).get("role") if isinstance(stage.get("evidence"), dict) else None
        failed_execution = execution_state in {"PROVIDER_FAILED", "CANCELLED", "NEEDS_INPUT", "VALIDATION_FAILED",
            "SETUP_FAILED", "SKIPPED_DEPENDENCY", "FAILED", "UNAVAILABLE", "BLOCKED", "INTERRUPTED"}
        failed_evidence_role = role in {"FAILURE_STATE", "UNCHANGED_AFTER_ATTEMPT", "RECOVERED_EVIDENCE"}
        if failed_execution or failed_evidence_role:
            accepted_no_op = False
        delivered = (stage.get("applied") is True or accepted_no_op) and stage.get("visible_complete") is True
        delivered = delivered and not failed_execution and not failed_evidence_role
        ratings = stage_review.get("ratings", [])
        rating = reviewed_score(ratings, review.get("reviewers", [])) if delivered and not invalid_input else {
            "status": "NOT_SCORED", "score": None, "dimensions": {},
            "reviewer_count": 0, "errors": [], "limitations": ["Fixture/input prerequisite failed" if invalid_input else "No completed visible output"]}
        stages[name] = {"delivery": delivered, "execution_status": execution_state or "NOT_RUN",
                        "confirmed_no_op": accepted_no_op,
                        "review": rating,
                        "acceptance": _check(stage_review.get("acceptance"))}
    final_review = stages["refine_2"]["review"]
    all_checks = list(checks.values()) + list(acceptance.values()) + [s["acceptance"] for s in stages.values()]
    final_delivered = all(stages[stage]["delivery"] for stage in stages)
    intervention_count = interventions(result, review)
    quality_failures = []
    for name, stage in stages.items():
        rating = stage["review"]
        if rating["score"] is not None and rating["dimensions"]["material_appearance"] < policy["stage_appearance_minimum"]:
            quality_failures.append({"stage": name, "code": "STAGE_APPEARANCE_BELOW_GATE"})
        if stage["acceptance"] is False:
            quality_failures.append({"stage": name, "code": "STAGE_ACCEPTANCE_FAILED"})
    if final_review["score"] is not None:
        if final_review["score"] < policy["final_overall_minimum"]:
            quality_failures.append({"stage": "refine_2", "code": "FINAL_OVERALL_BELOW_GATE"})
        if final_review["dimensions"]["material_appearance"] < policy["final_appearance_minimum"]:
            quality_failures.append({"stage": "refine_2", "code": "FINAL_APPEARANCE_BELOW_GATE"})
        if any(v < policy["final_dimension_minimum"] for v in final_review["dimensions"].values()):
            quality_failures.append({"stage": "refine_2", "code": "FINAL_DIMENSION_BELOW_GATE"})
    missing_reviews = any(stage["review"]["score"] is None for stage in stages.values())
    for name, stage in stages.items():
        failures = [item for item in quality_failures if item.get("stage") == name]
        stage["quality_failures"] = failures
        stage["quality_status"] = ("NOT_ASSESSABLE" if not stage["delivery"] or invalid_input else
            "FAIL" if failures else "NOT_REVIEWED" if stage["review"]["score"] is None or stage["acceptance"] is None else "PASS")
    if hard_failures or (intervention_count is not None and intervention_count >= 3):
        verdict = "FAIL"
    elif invalid_input and not any(stage.get("submitted") for stage in stage_map(result).values()):
        verdict = "BLOCKED"
    elif quality_failures or evidence_failed:
        verdict = "FAIL"
    elif status in {"NOT_RUN", "RUNNING", "BLOCKED", "UNAVAILABLE", "INTERRUPTED"}:
        verdict = status
    elif status == "FAILED" or not final_delivered or False in all_checks:
        verdict = "FAIL"
    elif missing_reviews or None in all_checks or intervention_count is None:
        verdict = "NOT_SCORED"
    elif result.get("assisted") and intervention_count == 0:
        verdict = "NOT_SCORED"
    else:
        verdict = "PASS" if intervention_count == 0 else "MINOR_ASSISTANCE" if intervention_count == 1 else "SIGNIFICANT_ASSISTANCE"
    generated = result.get("generated_inputs", {})
    return {
        "run_id": result.get("run_id"), "status": status, "verdict": verdict,
        "passed": verdict == "PASS", "attempted": result.get("attempted", False),
        "quality_status": "NOT_ASSESSABLE" if not stages["refine_2"]["delivery"] or invalid_input else
                          "FAIL" if quality_failures or False in all_checks else "NOT_REVIEWED" if missing_reviews or None in all_checks else "PASS",
        "quality_policy": policy, "quality_failures": quality_failures,
        "conditional_quality_eligible": stages["refine_2"]["delivery"] and not invalid_input,
        "artist_intervention_count": intervention_count,
        "intervention_class": intervention_class(intervention_count),
        "final_score": final_review["score"], "final_dimensions": final_review["dimensions"],
        "stages": stages, "acceptance_checks": acceptance, "workflow_checks": checks,
        "hard_failures": hard_failures, "failures": result.get("failures", []),
        "reason": result.get("reason") or ("Fixture/input prerequisite failed; no AI aesthetic score is assigned" if invalid_input else ""),
        "metrics": result.get("metrics", {}),
        "resolution_4k": result.get("resolution_4k", {"status": "NOT_RUN"}),
        "semantic_region_delivery": result.get("semantic_region_delivery", {"status": "NOT_SCORED"}),
        "generated_inputs": generated,
        "actual_generated_input": generated.get("assigned", 0) > 0,
        "generation_capability": "BLOCKED" if generated.get("availability") in {"BLOCKED", "UNAVAILABLE"}
                                 else generated.get("availability", "NOT_TESTED"),
    }


def score_robustness(result):
    safety = result.get("safety", {})
    checks = {key: _check(value) for key, value in safety.get("checks", {}).items()}
    hard = _hard_failures(result)
    status = result.get("status", "NOT_RUN")
    if hard or False in checks.values() or safety.get("status") == "FAIL":
        verdict = "FAIL"
    elif status in {"NOT_RUN", "RUNNING", "BLOCKED", "UNAVAILABLE", "INTERRUPTED"}:
        verdict = status
    elif checks and all(value is True for value in checks.values()) and safety.get("status") == "PASS":
        verdict = "PASS"
    else:
        verdict = "NOT_SCORED"
    return {"run_id": result.get("run_id"), "status": status, "verdict": verdict,
            "passed": verdict == "PASS", "checks": checks,
            "hard_failures": hard, "failures": result.get("failures", []),
            "reason": result.get("reason", ""), "metrics": result.get("metrics", {})}


def _stats(values):
    values = [value for value in values if _number(value)]
    return {"count": len(values), "mean": round(mean(values), 4) if values else None,
            "median": median(values) if values else None,
            "range": [min(values), max(values)] if values else None}


def summarize(manifest, results, baselines=None, reviews=None):
    if manifest.get("schema_version") == 1:
        from .legacy.scoring import summarize as legacy_summary
        return legacy_summary(manifest, results, baselines, reviews)
    if manifest.get("schema_version") == 2:
        from .v2.scoring import summarize as v2_summary
        return v2_summary(manifest, results, baselines, reviews)
    results, reviews = results or {}, reviews or {}
    cases = {case["id"]: case for case in manifest["specification"]["cases"]}
    scheduled = {entry["run_id"] for entry in manifest["schedule"]}
    if set(results) - scheduled:
        raise ValueError("Unscheduled retries or diagnostics cannot enter the frozen comparison")
    core, ablation, robustness = [], [], []
    for entry in manifest["schedule"]:
        result = results.get(entry["run_id"], {"run_id": entry["run_id"], "status": "NOT_RUN"})
        if result.get("run_id") != entry["run_id"]:
            raise ValueError("Result ID and frozen schedule disagree")
        record = (score_session(result, cases[entry["case_id"]], reviews.get(entry["review_id"]))
                  if entry["kind"] == "core" else score_robustness(result))
        record.update({key: entry[key] for key in ("case_id", "mode", "repeat", "order")})
        record.update(build_id=entry.get("build_id"), correction_enabled=entry.get("correction_enabled"))
        if entry["kind"] == "core":
            record["review_id"] = entry["review_id"]
            record["ablation"] = entry.get("ablation")
            (ablation if entry.get("ablation") == "correction" else core).append(record)
        else:
            robustness.append(record)
    modes = {}
    for mode in manifest["configuration"]["modes"]:
        records = [record for record in core if record["mode"] == mode]
        per_case = {}
        for case_id in cases:
            group = [record for record in records if record["case_id"] == case_id]
            per_case[case_id] = {
                "autonomous_successes": sum(record["passed"] for record in group), "scheduled": len(group),
                "individual": [{"run_id": record["run_id"], "verdict": record["verdict"],
                                "score": record["final_score"], "interventions": record["artist_intervention_count"]}
                               for record in group],
                "final_quality": _stats(record["final_score"] for record in group),
                "first_pass_quality": _stats(record["stages"]["create"]["review"]["score"] for record in group),
                "refine_1_quality": _stats(record["stages"]["refine_1"]["review"]["score"] for record in group),
                "interventions": dict(Counter(record["intervention_class"] for record in group)),
            }
        generated = [record for record in records if record["actual_generated_input"]]
        modes[mode] = {
            "autonomous_successes": sum(record["passed"] for record in records), "scheduled": len(records),
            "attempted": sum(record["attempted"] for record in records),
            "statuses": dict(Counter(record["status"] for record in records)),
            "verdicts": dict(Counter(record["verdict"] for record in records)),
            "conditional_final_quality": _stats(record["final_score"] for record in records if record["conditional_quality_eligible"]),
            "interventions": dict(Counter(record["intervention_class"] for record in records)),
            "actual_generated_input_subset": {"count": len(generated),
                                               "autonomous_successes": sum(record["passed"] for record in generated),
                                               "quality": _stats(record["final_score"] for record in generated)},
            "generation_blocked": sum(record["generation_capability"] == "BLOCKED" for record in records),
            "workflow_outcomes": {key: {"passed": sum(record["workflow_checks"][key] is True for record in records),
                                       "failed": sum(record["workflow_checks"][key] is False for record in records),
                                       "pending": sum(record["workflow_checks"][key] is None for record in records)}
                                  for key in WORKFLOW_CHECKS},
            "stage_outcomes": {stage: {"delivered": sum(record["stages"][stage]["delivery"] for record in records),
                                       "quality": _stats(record["stages"][stage]["review"]["score"] for record in records)}
                               for stage in ("create", "refine_1", "refine_2")},
            "timings": {key: _stats(record["metrics"].get(key) for record in records)
                        for key in ("provider_wait_seconds", "local_apply_seconds", "preview_settling_seconds",
                                    "baking_export_seconds", "active_operator_seconds", "elapsed_seconds")},
            "cases": per_case,
        }
    groups = {}
    for record in core + ablation:
        key = f"{record.get('build_id') or 'unspecified'}/{record['mode']}/correction-{'on' if record.get('correction_enabled') else 'off'}"
        groups.setdefault(key, []).append(record)
    comparison_groups = {key: dict(planned=len(records), attempted=sum(r["attempted"] for r in records),
        assessable_appearance=sum(r["conditional_quality_eligible"] for r in records),
        workflow_passes=sum(r["passed"] for r in records),
        final_quality=_stats(r["final_score"] for r in records),
        initial_quality=_stats(r["stages"]["create"]["review"]["score"] for r in records),
        outcomes=dict(Counter(r["verdict"] for r in records)),
        elapsed_seconds=_stats(r["metrics"].get("elapsed_seconds") for r in records),
        peak_process_rss_bytes=_stats(r["metrics"].get("peak_process_rss_bytes") for r in records),
        candidate_preview_seconds=_stats(r["metrics"].get("candidate_preview_seconds") for r in records),
        tool_calls=_stats(r["metrics"].get("tool_calls") for r in records)) for key, records in groups.items()}
    return {
        "schema_version": 3, "protocol": manifest["protocol"], "comparison_id": manifest["configuration"]["comparison_id"],
        "denominators": {"planned": len(core + ablation), "attempted": sum(r["attempted"] for r in core + ablation),
                         "assessable_appearance": sum(r["conditional_quality_eligible"] for r in core + ablation)},
        "primary_denominators": {"planned": len(core), "attempted": sum(r["attempted"] for r in core),
                         "assessable_appearance": sum(r["conditional_quality_eligible"] for r in core)},
        "ablation_denominators": {"planned": len(ablation), "attempted": sum(r["attempted"] for r in ablation),
                         "assessable_appearance": sum(r["conditional_quality_eligible"] for r in ablation)},
        "configuration": manifest["configuration"], "core_scheduled": len(core), "robustness_scheduled": len(robustness),
        "ablation_scheduled": len(ablation), "ablation_results": ablation,
        "modes": modes, "comparison_groups": comparison_groups, "core_results": core, "robustness_results": robustness,
        "failures": [record for record in core + ablation + robustness if record["verdict"] in {"FAIL", "UNAVAILABLE", "BLOCKED", "INTERRUPTED"}],
        "supplements": manifest["configuration"]["supplements"],
        "limitations": [
            "Only texturing receives points; fixed Eevee setup is a prerequisite.",
            "Concurrency is frozen; paired-wave timings share a device and must not be compared as uncontended smoke performance.",
            "Primary P/H aggregates include the 60 baseline/improved sessions only. Correction-off ablation runs in a separate randomized phase and is never pooled into primary means.",
            "Frozen director_quality_v1 gates apply to all stages; independent raw ratings and acceptance checks remain visible.",
            "Zero artist corrections is an autonomous success only when actual material requirements and primary checks pass.",
            "Unmeasured intervention counts, active operator time, reviews and costs remain unknown.",
            "Blocked and unavailable attempts remain in scheduled denominators and receive no invented aesthetic score.",
            "Offline/export/4K and robustness are optional supplemental evidence; missing supplemental checks do not fail a primary texturing result.",
        ],
    }
