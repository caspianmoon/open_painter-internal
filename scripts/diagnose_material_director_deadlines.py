#!/usr/bin/env python3
"""Annotate source-confirmed local deadlines without rewriting frozen outcomes.

This posthoc diagnostic is not part of the frozen evaluator. It reads completed
campaign records, verifies their producing product, and writes only a separate
sidecar. It never grades materials, changes failure counts, or calls a provider.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

VERSION = "source_linked_local_deadline_v1"
MESSAGE = "The instruction timed out. Your material was not changed."
OUTPUT_NAME = "posthoc_deadline_diagnostics.json"
MAX_RECORD_BYTES = 128 * 1024 * 1024
POLICY = {"scene_scope_seconds": 1800, "set_hybrid_seconds": 900, "set_native_seconds": 180}
DEADLINE_ASSIGNMENT = "self.deadline = time.monotonic() + (1800 if message.get('scene_scope') else 900 if self.generate_textures else 180)"
GENERATE_ASSIGNMENT = "self.generate_textures = message.get('generate_textures', True)"
EXPIRY_BRANCH = """if self.request_id and now > self.deadline:
    request_id = self.request_id
    self.interrupt()
    self.emit(dict(event='error', request_id=request_id, message='The instruction timed out. Your material was not changed.'))
"""


class Unverified(ValueError):
    """Evidence is insufficient for this deliberately narrow derivation."""


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def read_bytes(path):
    path = Path(path)
    before = path.stat()
    if before.st_size > MAX_RECORD_BYTES:
        raise Unverified("Evidence file exceeds bounded diagnostic reader: " + str(path))
    data = path.read_bytes()
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise Unverified("Evidence changed during read: " + str(path))
    return data, {"path": str(path.resolve()), "sha256": hashlib.sha256(data).hexdigest()}


def read_json(path):
    data, evidence = read_bytes(path)
    return json.loads(data), evidence


def ast_equal(node, source):
    return ast.dump(node, include_attributes=False) == ast.dump(ast.parse(source).body[0], include_attributes=False)


def prove_source(root, expected):
    """Do not import/execute the product. Recognize only this exact known policy."""
    root = Path(root).resolve()
    pin, pin_evidence = read_json(root / ".benchmark_product_pin.json")
    if pin != expected:
        raise Unverified("Product snapshot pin does not match frozen build")
    files = {}
    for path in sorted((root / "src/open_painter").rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            _, evidence = read_bytes(path)
            files[str(path.relative_to(root))] = evidence["sha256"]
    if not files or digest(files) != expected.get("source_sha256"):
        raise Unverified("Product bytes do not match frozen build")
    path = root / "src/open_painter/ai/codex_client.py"
    data, source_evidence = read_bytes(path)
    if source_evidence["sha256"] != files[str(path.relative_to(root))]:
        raise Unverified("Producing source changed during verification")
    source = data.decode("utf-8")
    tree = ast.parse(source)
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "CodexClient"]
    if len(classes) != 1:
        raise Unverified("Unrecognized CodexClient source")
    declared_methods = [n for n in classes[0].body if isinstance(n, ast.FunctionDef)]
    if any(sum(n.name == name for n in declared_methods) != 1 for name in ("run", "poll")):
        raise Unverified("Ambiguous deadline methods")
    methods = {n.name: n for n in declared_methods}
    run, poll = methods.get("run"), methods.get("poll")
    if run is None or poll is None:
        raise Unverified("Unrecognized deadline methods")
    assignments = [n for n in run.body if ast_equal(n, DEADLINE_ASSIGNMENT)]
    generation = [n for n in run.body if ast_equal(n, GENERATE_ASSIGNMENT)]
    branches = [n for n in poll.body if ast_equal(n, EXPIRY_BRANCH)]
    clocks = [n for n in poll.body if ast_equal(n, "now = time.monotonic()")]
    literals = [n for n in ast.walk(tree) if isinstance(n, ast.Constant) and n.value == MESSAGE]
    if not (len(assignments) == len(generation) == len(branches) == len(clocks) == len(literals) == 1):
        raise Unverified("Local deadline source branch is not the recognized exact implementation")
    if generation[0].lineno >= assignments[0].lineno or clocks[0].lineno >= branches[0].lineno:
        raise Unverified("Deadline source statement ordering changed")
    for attribute in ("deadline", "generate_textures"):
        writes = [n for n in ast.walk(run) if isinstance(n, ast.Attribute)
                  and isinstance(n.ctx, ast.Store) and n.attr == attribute]
        if len(writes) != 1:
            raise Unverified("Deadline settings are overwritten inside run")
    lines = source.splitlines()
    def excerpt(node):
        return {"first_line": node.lineno, "last_line": node.end_lineno,
                "text": "\n".join(lines[node.lineno - 1:node.end_lineno])}
    return {"status": "VERIFIED", "product": expected, "product_root": str(root),
            "pin": pin_evidence, "source": source_evidence,
            "deadline_assignment": excerpt(assignments[0]), "expiry_branch": excerpt(branches[0]),
            "recognized_budget_seconds": dict(POLICY), "product_files_verified": len(files)}


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def summarize_event(line, row):
    data = row["data"]
    # Do not copy prompts, model prose, tool payloads or authentication metadata.
    return {"line": line, "time_utc": row.get("time_utc"), "monotonic": row.get("monotonic"),
            "direction": row.get("direction"), "data": {k: data[k] for k in
            ("action", "event", "request_id", "message", "status", "scene_scope", "generate_textures") if k in data}}


def diagnose_stage(stage, result, job, rows, proof):
    request_id = stage.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        raise Unverified("Missing stage request ID")
    if result.get("status") not in {"FAILED", "BLOCKED", "INTERRUPTED"}:
        raise Unverified("Session is not a recorded failed/interrupted outcome")
    if stage.get("execution", {}).get("state") != "PROVIDER_FAILED":
        raise Unverified("Stage is not a recorded provider failure")
    if stage.get("applied") is not False or stage.get("receipt_count") != 0:
        raise Unverified("Stage does not establish zero committed receipts")
    structured = stage.get("structured_outcome", {})
    if structured and (structured.get("request_id") != request_id or structured.get("reason") != MESSAGE
                       or structured.get("execution") != "PROVIDER_FAILED"):
        raise Unverified("Structured outcome does not match request-scoped deadline error")
    errors = [e for e in stage.get("provider_errors", []) if e.get("event") == "error"
              and e.get("request_id") == request_id and e.get("message") == MESSAGE]
    if len(errors) != 1:
        raise Unverified("Missing unique request-scoped protocol error in result")
    runs = [(i, row) for i, row in rows if row.get("direction") == "host"
            and row["data"].get("action") == "run" and row["data"].get("request_id") == request_id]
    events = [(i, row) for i, row in rows if row.get("direction") == "provider"
              and row["data"].get("event") == "error" and row["data"].get("request_id") == request_id
              and row["data"].get("message") == MESSAGE]
    if len(runs) != 1 or len(events) != 1:
        raise Unverified("Trace lacks unique matching host run and deadline protocol event")
    run_line, run = runs[0]
    error_line, error = events[0]
    data = run["data"]
    scope = job.get("case", {}).get("scope")
    mode = job.get("mode")
    if scope not in {"SET", "SCENE"} or mode not in {"P", "H"}:
        raise Unverified("Unrecognized frozen mode/scope")
    scene, generated = data.get("scene_scope"), data.get("generate_textures")
    if type(scene) is not bool or type(generated) is not bool or scene != (scope == "SCENE") or generated != (mode == "H"):
        raise Unverified("Actual host run settings do not match frozen mode/scope")
    settings = stage.get("actual_settings", {})
    if settings.get("scene_scope") is not scene or settings.get("generate_textures") is not generated:
        raise Unverified("Result actual settings disagree with trace")
    budget = POLICY["scene_scope_seconds" if scene else "set_hybrid_seconds" if generated else "set_native_seconds"]
    if not all(finite_number(row.get("monotonic")) for row in (run, error)) or error_line <= run_line:
        raise Unverified("Missing or unordered monotonic trace timestamps")
    interval = error["monotonic"] - run["monotonic"]
    if interval < budget:
        raise Unverified("Error arrived before the source-confirmed local deadline")
    later_runs = [i for i, row in rows if run_line < i < error_line and row.get("direction") == "host"
                  and row["data"].get("action") == "run"]
    if later_runs:
        raise Unverified("Overlapping host requests make deadline attribution ambiguous")
    next_run = next((i for i, row in rows if i > error_line and row.get("direction") == "host"
                    and row["data"].get("action") == "run"), math.inf)
    completion = next(((i, row) for i, row in rows if error_line < i < next_run and row.get("direction") == "provider"
                       and row["data"].get("event") == "completed" and row["data"].get("status") == "interrupted"
                       and row["data"].get("request_id") in (None, request_id)), None)
    return {"stage": stage.get("name"), "request_id": request_id,
            "failure_origin": "PRODUCT_LOCAL_DEADLINE", "diagnostic_cause": "NATIVE_INSTRUCTION_DEADLINE_EXCEEDED",
            "configured_seconds": budget, "observed_request_interval_seconds": interval,
            "interval_meaning": "Host run to local error; includes tool work and is not provider wait time",
            "source_execution": stage.get("execution"), "source_structured_execution": structured.get("execution"),
            "source_structured_cause": structured.get("cause_code"), "source_stage_status": stage.get("status"),
            "source_session_status": result.get("status"), "source_applied": stage.get("applied"),
            "source_receipt_count": stage.get("receipt_count"), "source_evidence": stage.get("evidence"),
            "source_quality": stage.get("quality"), "source_provider_wait_seconds": stage.get("provider_wait_seconds"),
            "host_run": summarize_event(run_line, run), "deadline_error": summarize_event(error_line, error),
            "following_interrupted_completion": summarize_event(*completion) if completion else None,
            "completion_caveat": "An unscoped interrupted completion is contextual only; the scoped error establishes attribution",
            "source_proof_key": proof["product"]["source_sha256"],
            "basis": "Pinned source branch plus exact request-scoped protocol event, not model prose"}


def inspect_campaign(comparison):
    comparison = Path(comparison).resolve()
    manifest, manifest_evidence = read_json(comparison / "manifest.json")
    if manifest.get("schema_version") != 3 or not isinstance(manifest.get("schedule"), list):
        raise Unverified("Expected an existing frozen v3 campaign")
    manifest_digest = digest(manifest)
    configuration = manifest["configuration"]
    if any(configuration.get("product_deadlines", {}).get(k) != v for k, v in POLICY.items()):
        raise Unverified("Frozen product deadline policy is not recognized")
    source_proofs, source_cache, records = {}, {}, []
    for scheduled in manifest["schedule"]:
        record = {k: scheduled.get(k) for k in ("run_id", "case_id", "build_id", "kind", "mode", "repeat", "relative_directory")}
        record.update(status="PENDING", diagnostics=[], unverified_candidates=[])
        records.append(record)
        directory = (comparison / scheduled["relative_directory"]).resolve()
        if not directory.is_relative_to(comparison):
            record.update(status="UNVERIFIED", reason="Scheduled directory escapes campaign")
            continue
        result_path = directory / "result.json"
        if not result_path.exists():
            record["reason"] = "No finalized result available at this snapshot"
            continue
        try:
            result, result_evidence = read_json(result_path)
            if result.get("manifest_sha256") is None:
                record["reason"] = "Host normalization/publication is not finished"
                continue
            job, job_evidence = read_json(directory / "job.json")
            terminal, terminal_evidence = read_json(directory / "terminal_attempt.json")
            record["evidence"] = {"result": result_evidence, "job": job_evidence, "terminal_attempt": terminal_evidence}
            if result.get("manifest_sha256") != manifest_digest or job.get("comparison_sha256") != manifest_digest:
                raise Unverified("Result/job does not bind to this frozen manifest")
            if {k: v for k, v in result.items() if k != "manifest_sha256"} != terminal:
                raise Unverified("Published result differs from terminal attempt")
            if not result.get("completed_at"):
                raise Unverified("Missing terminal completion timestamp")
            for key in ("run_id", "mode", "repeat"):
                if result.get(key) != scheduled.get(key) or job.get(key) != scheduled.get(key):
                    raise Unverified("Result/job/schedule identity mismatch: " + key)
            if result.get("case_id") != scheduled.get("case_id") or job.get("case", {}).get("id") != scheduled.get("case_id"):
                raise Unverified("Case identity mismatch")
            if job.get("build_id") != scheduled.get("build_id") or job.get("kind") != scheduled.get("kind"):
                raise Unverified("Build or phase identity mismatch")
            if job.get("case", {}).get("scope") != scheduled.get("scope"):
                raise Unverified("Frozen scope mismatch")
            build_id = scheduled["build_id"]
            expected = configuration["builds"][build_id]
            if job.get("build") != expected or any(job.get("product_deadlines", {}).get(k) != v for k, v in POLICY.items()):
                raise Unverified("Job product/deadline pin differs from manifest")
            record.update(status="INSPECTED", source_status=result.get("status"),
                source_execution_summary=result.get("execution_summary"), source_quality=result.get("quality"))
            stages = result.get("stages", [])
            if not isinstance(stages, list):
                raise Unverified("Expected versioned stage list")
            candidates = [stage for stage in stages if any(e.get("event") == "error" and e.get("message") == MESSAGE
                          for e in stage.get("provider_errors", []))]
            if not candidates:
                continue
            cache_key = (str(Path(job["product_root"]).resolve()), digest(expected))
            if cache_key not in source_cache:
                try:
                    source_cache[cache_key] = prove_source(cache_key[0], expected)
                except (OSError, ValueError, SyntaxError) as exc:
                    source_cache[cache_key] = {"status": "UNVERIFIED", "reason": str(exc)}
            proof = source_cache[cache_key]
            if proof["status"] != "VERIFIED":
                raise Unverified(proof["reason"])
            source_proofs[expected["source_sha256"]] = proof
            trace_bytes, trace_evidence = read_bytes(directory / "provider_and_tool_trace.jsonl")
            record["evidence"]["trace"] = trace_evidence
            rows = [(i, json.loads(line)) for i, line in enumerate(trace_bytes.splitlines(), 1) if line.strip()]
            if any(not isinstance(row.get("data"), dict) for _, row in rows):
                raise Unverified("Malformed trace record")
            for stage in candidates:
                try:
                    record["diagnostics"].append(diagnose_stage(stage, result, job, rows, proof))
                except Unverified as exc:
                    record["unverified_candidates"].append({"stage": stage.get("name"), "reason": str(exc)})
            # A running campaign is safe to inspect; reject publication races.
            for evidence in record["evidence"].values():
                if read_bytes(evidence["path"])[1]["sha256"] != evidence["sha256"]:
                    raise Unverified("Evidence changed during diagnostic inspection")
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            record.update(status="UNVERIFIED", reason=str(exc), diagnostics=[])
    if read_bytes(manifest_evidence["path"])[1]["sha256"] != manifest_evidence["sha256"]:
        raise Unverified("Manifest changed during diagnostic inspection")
    _, script_evidence = read_bytes(__file__)
    diagnostics = [d for record in records for d in record["diagnostics"]]
    statuses = Counter(record["status"] for record in records)
    groups = Counter((r["kind"], r["build_id"], r["mode"]) for r in records for _ in r["diagnostics"])
    return {"schema_version": 1, "diagnostic_version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(), "comparison": str(comparison),
        "status": "SNAPSHOT_WITH_UNVERIFIED_RECORDS" if statuses["UNVERIFIED"] else "SNAPSHOT",
        "disclosure": "Posthoc technical attribution only. Frozen execution labels, quality, failure denominators and dependency skips remain unchanged. No inference from AI prose; absence of this annotation does not establish a remote provider failure.",
        "implementation": script_evidence, "manifest": {**manifest_evidence, "canonical_sha256": manifest_digest},
        "source_proofs": source_proofs, "counts": {"scheduled_sessions": len(records),
            "inspected_sessions": statuses["INSPECTED"], "pending_sessions": statuses["PENDING"],
            "unverified_sessions": statuses["UNVERIFIED"], "confirmed_local_deadline_stages": len(diagnostics),
            "sessions_with_confirmed_local_deadline": sum(bool(r["diagnostics"]) for r in records),
            "unverified_candidate_stages": sum(len(r["unverified_candidates"]) for r in records)},
        "diagnostic_counts_by_group": [{"kind": k[0], "build_id": k[1], "mode": k[2], "local_deadline_stages": n}
                                      for k, n in sorted(groups.items())], "runs": records}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison", required=True, type=Path)
    parser.add_argument("--output", type=Path, help="Must be the campaign's posthoc_deadline_diagnostics.json")
    args = parser.parse_args(argv)
    comparison = args.comparison.resolve()
    output = args.output or comparison / OUTPUT_NAME
    if output.resolve() != comparison / OUTPUT_NAME or output.is_symlink():
        parser.error("Only the separate campaign posthoc_deadline_diagnostics.json may be written")
    report = inspect_campaign(comparison)
    # Atomic sidecar refresh permits an end-of-campaign rerun without touching
    # originals. No file in the product or an attempted session is ever opened
    # for writing.
    with tempfile.NamedTemporaryFile(mode="w", dir=comparison, prefix=".deadline-diagnostic-", suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write("\n")
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    print(json.dumps({"output": str(output.resolve()), "status": report["status"], **report["counts"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
