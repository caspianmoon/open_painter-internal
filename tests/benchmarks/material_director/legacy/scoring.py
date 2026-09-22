"""Evidence-aware scoring. Missing outputs and reviews never become aesthetic zeroes."""

from collections import Counter
from math import isfinite
from statistics import mean, median


DIMENSIONS = {
    "material_appearance": 25,
    "brief_compliance": 20,
    "spatial_placement": 20,
    "editable_structure": 15,
    "refinement_fidelity": 10,
    "handoff_integrity": 10,
}
WORKFLOW_CHECKS = ("input_integrity", "preservation", "editability_probe", "undo",
                   "save_reopen_offline", "export_reimport")
HARD_FAILURES = frozenset({"crash", "unauthorized_edit", "lost_artist_work", "corrupted_save",
                          "missing_final_material_after_claimed_apply", "falsely_claimed_apply"})
DESTRUCTIVE_FAILURES = frozenset({"unauthorized_edit", "lost_artist_work", "corrupted_save",
                                 "cross_target_edit", "partial_scene_transaction"})
FAILURE_CATEGORIES = frozenset({"missing_capability", "perception_context", "planning_instruction_following",
                                "surface_generation_quality", "material_construction_evaluation",
                                "transaction_persistence", "export", "infrastructure"})


def _number(value):
    return type(value) in (int, float) and isfinite(value)


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
            errors.append(f"Each reviewer must rate all six dimensions between 0 and 5: {reviewer_id}")
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
            "meets_numeric_thresholds": total >= 80 and min(medians.values()) >= 3}


def _hard_failures(result):
    failures = list(result.get("hard_failures", []))
    for stage_name, stage in stage_map(result).items():
        if stage.get("claimed_apply") is True and stage.get("visible_complete") is False:
            failures.append({"kind": "missing_final_material_after_claimed_apply", "stage": stage_name,
                             "evidence": stage.get("evidence", [])})
    return failures


def score_session(result, case, review=None):
    """A delivered session only passes after its independent review and handoff checks."""
    review = review or {}
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
    stages = {}
    for name in ("create", "refine_1", "refine_2"):
        stage = stage_map(result).get(name, {})
        stage_review = review.get("stages", {}).get(name, {})
        # MD-01 can correctly leave absent rust unchanged. Response-only provider prose
        # is not proof: an evaluator/operator must confirm the no-op with evidence.
        accepted_no_op = name != "create" and (
            (stage.get("no_op_confirmed") is True and bool(stage.get("no_op_evidence")))
            or _check(stage_review.get("no_op_acceptance")) is True)
        delivered = (stage.get("applied") is True or accepted_no_op) and stage.get("visible_complete") is True
        ratings = stage_review.get("ratings", [])
        rating = reviewed_score(ratings, review.get("reviewers", [])) if delivered else {
            "status": "NOT_SCORED", "score": None, "dimensions": {},
            "reviewer_count": 0, "errors": [], "limitations": ["No completed visible output"]}
        stages[name] = {"delivery": delivered, "execution_status": stage.get("status", "NOT_RUN"),
                        "confirmed_no_op": accepted_no_op,
                        "review": rating,
                        "acceptance": (rating.get("meets_numeric_thresholds")
                                       if rating["score"] is not None else None)}
    final_review = stages["refine_2"]["review"]
    all_checks = list(checks.values()) + list(acceptance.values())
    final_delivered = all(stages[stage]["delivery"] for stage in stages)
    if hard_failures or result.get("assisted"):
        verdict = "FAIL" if hard_failures else "ASSISTED_EXCLUDED"
    elif status in {"NOT_RUN", "RUNNING", "BLOCKED", "UNAVAILABLE", "INTERRUPTED"}:
        verdict = status
    elif status == "FAILED" or not final_delivered or False in all_checks:
        verdict = "FAIL"
    elif final_review["score"] is None or None in all_checks:
        verdict = "NOT_SCORED"
    elif not final_review["meets_numeric_thresholds"]:
        verdict = "FAIL"
    else:
        verdict = "PASS"
    generated = result.get("generated_inputs", {})
    return {
        "run_id": result.get("run_id"), "status": status, "verdict": verdict,
        "passed": verdict == "PASS", "attempted": result.get("attempted", False),
        "conditional_quality_eligible": stages["refine_2"]["delivery"] and not result.get("assisted"),
        "final_score": final_review["score"], "final_dimensions": final_review["dimensions"],
        "stages": stages, "acceptance_checks": acceptance, "workflow_checks": checks,
        "hard_failures": hard_failures, "failures": result.get("failures", []),
        "reason": result.get("reason", ""), "metrics": result.get("metrics", {}),
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


def _gate(passed, completed, required, total):
    if passed >= required:
        return "PASS"
    if passed + total - completed < required:
        return "FAIL"
    return "PENDING"


def _destructive(records):
    findings = []
    for record in records:
        for failure in record.get("hard_failures", []):
            kind = failure.get("kind") if isinstance(failure, dict) else failure
            if kind in DESTRUCTIVE_FAILURES or (isinstance(failure, dict) and failure.get("destructive")):
                findings.append({"run_id": record["run_id"], "failure": failure})
    return findings


def score_control(control):
    if not control or control.get("status") != "COMPLETED":
        return {"status": "NOT_SCORED", "score": None, "reason": "Matched control has not been completed"}
    if not control.get("evidence") or not control.get("fixture_sha256"):
        return {"status": "NOT_SCORED", "score": None, "reason": "Control evidence or fixture pin is missing"}
    return reviewed_score(control.get("ratings", []), control.get("reviewers", []))


def summarize(manifest, results, baselines=None, reviews=None):
    """Use the entire immutable schedule, including absent results, for every denominator."""
    results = results or {}
    baselines = baselines or {}
    reviews = reviews or {}
    cases = {case["id"]: case for case in manifest["specification"]["cases"]}
    scheduled_ids = {entry["run_id"] for entry in manifest["schedule"]}
    extras = sorted(set(results) - scheduled_ids)
    if extras:
        raise ValueError("Unscheduled/diagnostic results cannot enter the core comparison: " + ", ".join(extras))
    core, robustness = [], []
    for entry in manifest["schedule"]:
        result = results.get(entry["run_id"], {"run_id": entry["run_id"], "status": "NOT_RUN"})
        if result.get("run_id") != entry["run_id"]:
            raise ValueError("Result key and run_id disagree")
        if entry["kind"] == "core":
            scored = score_session(result, cases[entry["case_id"]], reviews.get(entry["review_id"]))
            scored.update({key: entry[key] for key in ("case_id", "mode", "repeat", "review_id", "order")})
            core.append(scored)
        else:
            scored = score_robustness(result)
            scored.update({key: entry[key] for key in ("case_id", "mode", "order")})
            robustness.append(scored)
    modes = {}
    for mode in ("P", "H"):
        sessions = [record for record in core if record["mode"] == mode]
        robustness_records = [record for record in robustness if record["mode"] == mode]
        per_case = {}
        comparison_passes, comparisons_scored = 0, 0
        claim = manifest["configuration"]["value_claim"]
        for case_id in cases:
            group = [record for record in sessions if record["case_id"] == case_id]
            passed = sum(record["passed"] for record in group)
            final_stats = _stats(record["final_score"] for record in group
                                 if record["conditional_quality_eligible"])
            control = baselines.get(f"{case_id}-preset", {})
            control_score = score_control(control)
            value = {"status": "NOT_SCORED", "claim": claim, "control_score": control_score.get("score"),
                     "reason": "All three repeat scores and matched preset control are required"}
            if final_stats["count"] == 3 and control_score["score"] is not None:
                matched = (control.get("configuration_sha256") == _configuration_digest(manifest)
                           and control.get("fixture_sha256") == manifest["fixtures"][cases[case_id]["fixture"]]["sha256"])
                if not matched:
                    value["reason"] = "Control does not match the frozen configuration/fixture"
                elif claim == "quality":
                    difference = final_stats["mean"] - control_score["score"]
                    value.update(status="PASS" if difference >= 10 else "FAIL", difference=difference, reason="")
                else:
                    operator = _stats(record["metrics"].get("active_operator_seconds") for record in group)
                    baseline_seconds = control.get("active_operator_seconds")
                    if operator["count"] == 3 and _number(baseline_seconds) and baseline_seconds > 0:
                        difference = final_stats["mean"] - control_score["score"]
                        ratio = operator["mean"] / baseline_seconds
                        passed_value = difference >= -5 and ratio <= 0.5
                        value.update(status="PASS" if passed_value else "FAIL", difference=difference,
                                     operator_time_ratio=ratio, reason="")
                    else:
                        value["reason"] = "Measured active operator time is required for the efficiency claim"
            comparisons_scored += value["status"] in {"PASS", "FAIL"}
            comparison_passes += value["status"] == "PASS"
            settled = sum(record["verdict"] in {"PASS", "FAIL", "UNAVAILABLE", "BLOCKED", "INTERRUPTED", "ASSISTED_EXCLUDED"}
                          for record in group)
            per_case[case_id] = {
                "passed": passed, "scheduled": 3,
                "delivery_gate": _gate(passed, settled, 2, 3),
                "individual": [{"run_id": record["run_id"], "verdict": record["verdict"],
                                "score": record["final_score"]} for record in group],
                "conditional_final_quality": final_stats,
                "first_pass_quality": _stats(record["stages"]["create"]["review"]["score"] for record in group),
                "refine_1_quality": _stats(record["stages"]["refine_1"]["review"]["score"] for record in group),
                "value_comparison": value,
            }
        total_passes = sum(record["passed"] for record in sessions)
        settled = sum(record["verdict"] in {"PASS", "FAIL", "BLOCKED", "UNAVAILABLE", "INTERRUPTED", "ASSISTED_EXCLUDED"}
                      for record in sessions)
        overall_delivery = _gate(total_passes, settled, 20, 24)
        case_gates = [value["delivery_gate"] for value in per_case.values()]
        if "FAIL" in case_gates:
            overall_delivery = "FAIL"
        elif overall_delivery == "PASS" and any(gate != "PASS" for gate in case_gates):
            overall_delivery = "PENDING"
        destructive = _destructive(sessions + robustness_records)
        robustness_passes = sum(record["passed"] for record in robustness_records)
        preservation_failed = any(record["workflow_checks"]["preservation"] is False for record in sessions)
        preservation_known = all(
            record["workflow_checks"]["preservation"] is True
            or (record["status"] in {"UNAVAILABLE", "BLOCKED"} and not record["attempted"])
            for record in sessions)
        if destructive or preservation_failed or any(record["verdict"] == "FAIL" for record in robustness_records):
            safety_gate = "FAIL"
        elif robustness_passes == 8 and preservation_known:
            safety_gate = "PASS"
        else:
            safety_gate = "PENDING"
        value_gate = _gate(comparison_passes, comparisons_scored, 6, 8)
        gates = (overall_delivery, safety_gate, value_gate)
        beta = "FAIL" if "FAIL" in gates else "PASS" if all(gate == "PASS" for gate in gates) else "PENDING"
        actual_generation = [record for record in sessions if record["actual_generated_input"]]
        workflow_outcomes = {key: {"passed": sum(record["workflow_checks"][key] is True for record in sessions),
                                   "failed": sum(record["workflow_checks"][key] is False for record in sessions),
                                   "pending": sum(record["workflow_checks"][key] is None for record in sessions),
                                   "scheduled": 24} for key in WORKFLOW_CHECKS}
        stage_outcomes = {}
        for stage in ("create", "refine_1", "refine_2"):
            stage_outcomes[stage] = {
                "delivered": sum(record["stages"][stage]["delivery"] for record in sessions),
                "numeric_acceptance": sum(record["stages"][stage]["acceptance"] is True for record in sessions),
                "independently_scored": sum(record["stages"][stage]["review"]["score"] is not None for record in sessions),
                "scheduled": 24,
                "conditional_quality": _stats(record["stages"][stage]["review"]["score"] for record in sessions),
            }
        modes[mode] = {
            "passed": total_passes, "scheduled": 24, "delivery_rate": total_passes / 24,
            "attempted": sum(record["attempted"] for record in sessions),
            "statuses": dict(Counter(record["status"] for record in sessions)),
            "verdicts": dict(Counter(record["verdict"] for record in sessions)),
            "conditional_final_quality": _stats(record["final_score"] for record in sessions
                                                if record["conditional_quality_eligible"]),
            "actual_generated_input_subset": {"count": len(actual_generation),
                                               "passed": sum(record["passed"] for record in actual_generation),
                                               "conditional_final_quality": _stats(record["final_score"] for record in actual_generation)},
            "generation_blocked": sum(record["generation_capability"] == "BLOCKED" for record in sessions),
            "stage_outcomes": stage_outcomes, "workflow_outcomes": workflow_outcomes,
            "timings": {key: _stats(record["metrics"].get(key) for record in sessions)
                        for key in ("provider_wait_seconds", "local_apply_seconds", "preview_settling_seconds",
                                    "baking_export_seconds", "active_operator_seconds", "elapsed_seconds")},
            "robustness": {"passed": robustness_passes, "scheduled": 8,
                           "verdicts": dict(Counter(record["verdict"] for record in robustness_records))},
            "destructive_failures": destructive,
            "gates": {"delivery": overall_delivery, "dependable_workflow": safety_gate,
                      "predeclared_value": value_gate, "useful_beta": beta},
            "value_cases_passed": comparison_passes, "value_cases_scored": comparisons_scored,
            "value_cases_scheduled": 8, "cases": per_case,
        }
    return {"schema_version": 1, "comparison_id": manifest["configuration"]["comparison_id"],
            "configuration": manifest["configuration"], "core_scheduled": len(core),
            "robustness_scheduled": len(robustness), "modes": modes,
            "core_results": core, "robustness_results": robustness,
            "resolution_checks": [{"run_id": record["run_id"], "case_id": record["case_id"],
                                   "mode": record["mode"], "result": record["resolution_4k"]}
                                  for record in core if record["repeat"] == 1],
            "failures": [{"run_id": record["run_id"], "verdict": record["verdict"],
                          "reason": record["reason"], "failures": record["failures"],
                          "hard_failures": record["hard_failures"]}
                         for record in core + robustness if record["verdict"] in {"FAIL", "BLOCKED", "UNAVAILABLE", "INTERRUPTED"}],
            "artist_baseline": manifest["configuration"]["artist_baseline"],
            "engine_validation": manifest["configuration"].get("engine_validation", {}),
            "limitations": ["Three repeats are a screening sample, not a precise reliability estimate.",
                            "No engine compatibility is established by a Blender PBR reconstruction.",
                            "Missing reviews, baselines and outputs remain unscored; blocked sessions remain in delivery denominators."]}


def _configuration_digest(manifest):
    from .manifest import digest
    return digest(manifest["configuration"])
