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
            "numeric_pass_threshold": None}


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
    invalid_input = checks["input_integrity"] is False or checks["evidence_delivery"] is False
    stages = {}
    for name in ("create", "refine_1", "refine_2"):
        stage = stage_map(result).get(name, {})
        stage_review = review.get("stages", {}).get(name, {})
        # A legitimate unchanged state requires explicit evaluator confirmation. Provider prose
        # is not proof: an evaluator/operator must confirm the no-op with evidence.
        accepted_no_op = name != "create" and (
            (stage.get("no_op_confirmed") is True and bool(stage.get("no_op_evidence")))
            or _check(stage_review.get("no_op_acceptance")) is True)
        delivered = (stage.get("applied") is True or accepted_no_op) and stage.get("visible_complete") is True
        ratings = stage_review.get("ratings", [])
        rating = reviewed_score(ratings, review.get("reviewers", [])) if delivered and not invalid_input else {
            "status": "NOT_SCORED", "score": None, "dimensions": {},
            "reviewer_count": 0, "errors": [], "limitations": ["Fixture/input prerequisite failed" if invalid_input else "No completed visible output"]}
        stages[name] = {"delivery": delivered, "execution_status": stage.get("status", "NOT_RUN"),
                        "confirmed_no_op": accepted_no_op,
                        "review": rating,
                        "acceptance": _check(stage_review.get("acceptance"))}
    final_review = stages["refine_2"]["review"]
    all_checks = list(checks.values()) + list(acceptance.values())
    final_delivered = all(stages[stage]["delivery"] for stage in stages)
    intervention_count = interventions(result, review)
    if hard_failures or (intervention_count is not None and intervention_count >= 3):
        verdict = "FAIL"
    elif invalid_input:
        verdict = "BLOCKED"
    elif status in {"NOT_RUN", "RUNNING", "BLOCKED", "UNAVAILABLE", "INTERRUPTED"}:
        verdict = status
    elif status == "FAILED" or not final_delivered or False in all_checks:
        verdict = "FAIL"
    elif final_review["score"] is None or None in all_checks or intervention_count is None:
        verdict = "NOT_SCORED"
    elif result.get("assisted") and intervention_count == 0:
        verdict = "NOT_SCORED"
    else:
        verdict = "PASS" if intervention_count == 0 else "MINOR_ASSISTANCE" if intervention_count == 1 else "SIGNIFICANT_ASSISTANCE"
    generated = result.get("generated_inputs", {})
    return {
        "run_id": result.get("run_id"), "status": status, "verdict": verdict,
        "passed": verdict == "PASS", "attempted": result.get("attempted", False),
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
        from ..legacy.scoring import summarize as legacy_summary
        return legacy_summary(manifest, results, baselines, reviews)
    results, reviews = results or {}, reviews or {}
    cases = {case["id"]: case for case in manifest["specification"]["cases"]}
    scheduled = {entry["run_id"] for entry in manifest["schedule"]}
    if set(results) - scheduled:
        raise ValueError("Unscheduled retries or diagnostics cannot enter the frozen comparison")
    core, robustness = [], []
    for entry in manifest["schedule"]:
        result = results.get(entry["run_id"], {"run_id": entry["run_id"], "status": "NOT_RUN"})
        if result.get("run_id") != entry["run_id"]:
            raise ValueError("Result ID and frozen schedule disagree")
        record = (score_session(result, cases[entry["case_id"]], reviews.get(entry["review_id"]))
                  if entry["kind"] == "core" else score_robustness(result))
        record.update({key: entry[key] for key in ("case_id", "mode", "repeat", "order")})
        if entry["kind"] == "core":
            record["review_id"] = entry["review_id"]
            core.append(record)
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
    return {
        "schema_version": 2, "protocol": manifest["protocol"], "comparison_id": manifest["configuration"]["comparison_id"],
        "configuration": manifest["configuration"], "core_scheduled": len(core), "robustness_scheduled": len(robustness),
        "modes": modes, "core_results": core, "robustness_results": robustness,
        "failures": [record for record in core + robustness if record["verdict"] in {"FAIL", "UNAVAILABLE", "BLOCKED", "INTERRUPTED"}],
        "supplements": manifest["configuration"]["supplements"],
        "limitations": [
            "Only texturing receives points; fixed Eevee setup is a prerequisite.",
            "The revised protocol declares no numeric pass threshold; weighted scores and evidenced requirement decisions are reported separately.",
            "Zero artist corrections is an autonomous success only when actual material requirements and primary checks pass.",
            "Unmeasured intervention counts, active operator time, reviews and costs remain unknown.",
            "Blocked and unavailable attempts remain in scheduled denominators and receive no invented aesthetic score.",
            "Offline/export/4K and robustness are optional supplemental evidence; missing supplemental checks do not fail a primary texturing result.",
        ],
    }
