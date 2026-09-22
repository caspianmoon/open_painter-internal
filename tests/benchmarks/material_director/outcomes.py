"""Versioned execution/evidence/quality records; no status-prose inference."""
from copy import deepcopy

STAGES = ("create", "refine_1", "refine_2")
EXECUTION = frozenset({"NOT_RUN", "RUNNING", "APPLIED", "RESPONSE_ONLY", "NEEDS_INPUT",
    "VALIDATION_FAILED", "PROVIDER_FAILED", "CANCELLED", "SKIPPED_DEPENDENCY", "SETUP_FAILED"})
EVIDENCE = frozenset({"CAPTURED_VERIFIED", "CAPTURE_FAILED", "ARTIFACT_MISSING", "HASH_MISMATCH",
    "NOT_SCHEDULED", "NOT_APPLICABLE"})
QUALITY = frozenset({"NOT_REVIEWED", "PASS", "FAIL", "NOT_ASSESSABLE"})


def terminal_execution(stage, structured=None):
    """Classify observed protocol events, never a model's human-readable prose."""
    structured = structured or stage.get("structured_outcome") or {}
    state, code = structured.get("execution_state", structured.get("execution")), structured.get("cause_code")
    if state in EXECUTION and code:
        return {"state": state, "cause_code": code}
    if stage.get("applied") is True or stage.get("receipt_count", 0) > 0:
        return {"state": "APPLIED", "cause_code": "RECEIPT_COMMITTED"}
    if stage.get("provider_status") in {"failed", "error"} or stage.get("provider_errors"):
        return {"state": "PROVIDER_FAILED", "cause_code": "PROVIDER_TERMINAL_FAILED"}
    if stage.get("provider_status") in {"cancelled", "interrupted"}:
        return {"state": "CANCELLED", "cause_code": "PROVIDER_CANCELLED"}
    if stage.get("validation_errors"):
        return {"state": "VALIDATION_FAILED", "cause_code": "PROPOSAL_VALIDATION_FAILED"}
    if stage.get("submitted") is False:
        return {"state": "SETUP_FAILED", "cause_code": "REQUEST_NOT_SUBMITTED"}
    return {"state": "RESPONSE_ONLY", "cause_code": "NO_PROPOSAL_OR_RECEIPT"}


def skip_dependencies(stages, failed_name):
    """Append only never-run dependent instructions, retaining their dependency."""
    known = {stage["name"] for stage in stages}
    for name in STAGES[STAGES.index(failed_name) + 1:]:
        if name not in known:
            stages.append({"name": name, "status": "SKIPPED_DEPENDENCY", "submitted": False,
                "applied": False, "visible_complete": False,
                "execution": {"state": "SKIPPED_DEPENDENCY", "cause_code": "PREREQUISITE_STAGE_FAILED",
                              "dependency_stage": failed_name},
                "evidence": {"state": "NOT_APPLICABLE", "role": "NO_ATTEMPT"},
                "quality": {"state": "NOT_ASSESSABLE"}})


def adapt_result(record):
    """Read v1/v2 records without mutating them or changing historical scores."""
    value = deepcopy(record)
    version = value.get("record_version", 2)
    if version not in (1, 2, 3):
        raise ValueError("Unsupported benchmark result record version")
    rows = value.get("stages", [])
    if isinstance(rows, dict):
        rows = [dict(row, name=name) for name, row in rows.items()]
    for row in rows:
        if "execution" not in row:
            if row.get("status") in {"NOT_RUN", "RUNNING"}:
                row["execution"] = {"state": row["status"], "cause_code": "NO_TERMINAL_EVENT"}
            else:
                row["execution"] = terminal_execution(row)
        row.setdefault("evidence", {"state": "NOT_SCHEDULED"})
        if not isinstance(row["evidence"], dict):
            row["legacy_evidence_paths"] = row["evidence"]
            row["evidence"] = {"state": "NOT_SCHEDULED"}
        row.setdefault("quality", {"state": "NOT_REVIEWED" if row["execution"]["state"] == "APPLIED" else "NOT_ASSESSABLE"})
    failed = next((r for r in rows if r["name"] == "create" and r["execution"]["state"] not in
                  {"APPLIED", "NOT_RUN", "RUNNING"}), None)
    if failed:
        # Old templates may already contain empty rows; replace only empty
        # dependent placeholders in this in-memory adapter, never archive files.
        rows = [r for r in rows if r["name"] == "create" or r["execution"]["state"] != "NOT_RUN"]
        skip_dependencies(rows, "create")
    value.update(stages=rows, source_record_version=version, adapter_version="execution_evidence_v1")
    return value
