"""Actual Blender replay of the archived numeric tool event; no provider calls.

This exercises transport observation and durable evidence, not artistic quality.
Run with Blender --background --factory-startup --python-exit-code 1 --python FILE.
"""
import copy
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bpy

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(HERE), str(ROOT / "src")]
import run_session
from runtime import redact, sha256, write_json


def main():
    source = Path(os.environ.get("OP_MD_TRANSPORT_REPLAY_TRACE", str(ROOT /
        ".blender-test/material-director-benchmark/remedy-v3-smoke-03/T01/H/1/provider_and_tool_trace.jsonl")))
    records = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
    event = next(record["data"] for record in records
        if record.get("direction") == "provider"
        and record.get("data", {}).get("event") == "tool"
        and type(record["data"].get("call_id")) is int)
    output = Path(os.environ.get("OP_MD_TRANSPORT_PROBE_OUTPUT", str(ROOT /
        ".blender-test/material-director-benchmark/transport-observer-probe")))
    output.mkdir(parents=True, exist_ok=True)
    assert run_session.session.connection is None, "Replay must not connect to a provider"
    assert redact({0: {"token": "synthetic-private-value"}}) == {"0": {"token": "<REDACTED>"}}

    def new_run(name):
        job = dict(output=str(output / name), run_id=name, case={"id": "T01",
            "create": "Synthetic transport replay only", "refinements": []},
            mode="H", common_instruction="No inference", model_id="unused-no-provider")
        run = run_session.LiveRun(job)
        run.turn = dict(name="create", status="RUNNING", request_id=event["request_id"],
            started_monotonic=time.monotonic(), submitted=True)
        run.result["stages"].append(run.turn)
        run.observe_event(copy.deepcopy(event))
        persisted = json.loads((run.out / "result.json").read_text(encoding="utf-8"))
        assert persisted["stages"][0]["tool_call_count"] == 1
        assert str(event["call_id"]) in run.tool_started
        assert "tool_started" not in persisted and "tool_started" not in persisted["stages"][0]
        reply = dict(action="tool_result", request_id=event["request_id"],
            call_id=event["call_id"], success=True,
            result={"probe": "Synthetic response; original tool was not executed"})
        run.observe_send(reply)
        persisted = json.loads((run.out / "result.json").read_text(encoding="utf-8"))
        timing = persisted["stages"][0]["tool_timings"]
        assert len(timing) == 1 and timing[0]["call_id"] == event["call_id"]
        assert timing[0]["name"] == event["tool"] and timing[0]["success"] is True
        assert timing[0]["seconds"] >= 0 and not run.tool_started
        trace = [json.loads(line) for line in (run.out / "provider_and_tool_trace.jsonl").read_text(encoding="utf-8").splitlines()]
        assert trace[-2]["direction"] == "provider" and trace[-2]["data"] == redact(event)
        assert trace[-1]["direction"] == "host" and trace[-1]["data"] == redact(reply)
        return run

    replay = new_run("numeric-tool-event")
    forced = new_run("terminal-save-failure")
    disconnect, quit_blender = Mock(), Mock()
    fake_bpy = SimpleNamespace(ops=SimpleNamespace(wm=SimpleNamespace(quit_blender=quit_blender)))
    with patch.object(forced, "save", side_effect=OSError("Synthetic terminal result write failure")), \
            patch.object(run_session.session, "disconnect", disconnect), patch.object(run_session, "bpy", fake_bpy):
        forced.finish("FAILED", "infrastructure", "Synthetic terminal failure")
    disconnect.assert_called_once_with()
    quit_blender.assert_called_once_with()
    assert forced.phase == "finished"
    final = json.loads((forced.out / "result.json").read_text(encoding="utf-8"))
    emergency = json.loads((forced.out / "terminal_emergency.json").read_text(encoding="utf-8"))
    assert final["status"] == "FAILED" and final["stages"][0]["tool_timings"]
    assert emergency["failures"][0]["cause_code"] == "TERMINAL_EVIDENCE_WRITE_FAILED"

    unavailable = new_run("all-terminal-writes-fail")
    durable = (unavailable.out / "result.json").read_bytes()
    disconnect.reset_mock(); quit_blender.reset_mock()
    with patch.object(run_session, "write_json", side_effect=OSError("Synthetic unavailable output")), \
            patch.object(run_session.session, "disconnect", disconnect), patch.object(run_session, "bpy", fake_bpy):
        unavailable.finish("FAILED", "infrastructure", "Synthetic output unavailable")
    disconnect.assert_called_once_with()
    quit_blender.assert_called_once_with()
    assert unavailable.phase == "finished" and (unavailable.out / "result.json").read_bytes() == durable
    assert run_session.session.connection is None
    report = dict(status="PASS", kind="NO_PROVIDER_BLENDER_TRANSPORT_REPLAY", blender=bpy.app.version_string,
        provider_calls=0, original_tool_executed=False, artistic_score=None,
        source_trace=str(source), source_trace_sha256=sha256(source),
        call_id=event["call_id"], tool=event["tool"],
        numeric_event_and_result_persist=True, timing_persist=True,
        failed_terminal_save_cleanup=True, all_writes_failed_cleanup=True,
        last_durable_result_preserved=True, output=str(replay.out))
    write_json(output / "verification.json", report)
    print("TRANSPORT_OBSERVER_BLENDER_PASS", json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
