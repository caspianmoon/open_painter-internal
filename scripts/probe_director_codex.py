"""Probe the pinned runtime against a local fake Responses endpoint, without account tokens.

Usage: python3 scripts/probe_director_codex.py --codex /path/to/codex
The real app-server runs; only the upstream model is replaced. No Blender required.
"""

import argparse
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from open_painter.ai.codex_client import CodexClient
from open_painter.authoring.context import tools, INSTRUCTIONS
from open_painter.authoring.schema import SCHEMA, validate
from open_painter.ai.transport import Connection

parser = argparse.ArgumentParser()
parser.add_argument("--codex", required=True)
args = parser.parse_args()
requests = []
SUMMARY = "**Reading the material**\n\nStart from the selected set's context."
NOTE = "I'll read the material first."
ANSWER = "The selected material is editable."


class Model(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        import gzip
        data = self.rfile.read(int(self.headers["Content-Length"]))
        if self.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
        requests.append(json.loads(data))
        before = []   # Progress the artist sees before the item: a reasoning summary and a note.
        if len(requests) == 1:
            item = dict(type="function_call", id="call_1", call_id="call_1", name="get_context", arguments="{}", status="completed")
            reasoning = dict(type="reasoning", id="rs_1", summary=[dict(type="summary_text", text=SUMMARY)])
            note = dict(type="message", id="msg_note", role="assistant", status="completed", phase="commentary",
                        content=[dict(type="output_text", text=NOTE, annotations=[])])
            before = [
                ("response.output_item.added", dict(output_index=0, item=dict(reasoning, summary=[]))),
                ("response.reasoning_summary_part.added", dict(item_id="rs_1", output_index=0, summary_index=0,
                                                              part=dict(type="summary_text", text=""))),
                ("response.reasoning_summary_text.delta", dict(item_id="rs_1", output_index=0, summary_index=0, delta=SUMMARY[:12])),
                ("response.reasoning_summary_text.delta", dict(item_id="rs_1", output_index=0, summary_index=0, delta=SUMMARY[12:])),
                ("response.output_item.done", dict(output_index=0, item=reasoning)),
                ("response.output_item.added", dict(output_index=1, item=dict(note, status="in_progress", content=[]))),
                ("response.output_text.delta", dict(item_id="msg_note", output_index=1, content_index=0, delta=NOTE)),
                ("response.output_item.done", dict(output_index=1, item=note))]
        elif len(requests) == 2:
            proposal = dict(version=1, summary="Steel", commands=[dict(op="add_layer", id="new:steel", name="Steel", kind="FILL")])
            item = dict(type="function_call", id="call_2", call_id="call_2", name="propose_changes",
                        arguments=json.dumps(dict(proposal_json=json.dumps(proposal))), status="completed")
        else:
            item = dict(type="message", id="msg_1", role="assistant", status="completed", phase="final_answer",
                        content=[dict(type="output_text", text=ANSWER, annotations=[])])
        output = [values["item"] for kind, values in before if kind == "response.output_item.done"] + [item]
        index = len(output) - 1
        response = dict(id=f"resp_{len(requests)}", object="response", created_at=1, model="gpt-5.5",
                        status="completed", output=output, usage=dict(input_tokens=1, output_tokens=1, total_tokens=2))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for kind, values in (
            ("response.created", dict(response=dict(response, status="in_progress", output=[]))),
            *before,
            ("response.output_item.added", dict(output_index=index, item=item)),
            ("response.output_item.done", dict(output_index=index, item=item)),
            ("response.completed", dict(response=response)),
        ):
            self.wfile.write(("event: " + kind + "\ndata: " + json.dumps(dict(type=kind, **values)) + "\n\n").encode())
        self.wfile.flush()


with tempfile.TemporaryDirectory(prefix="painter-capability-probe-") as directory:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    events = []
    options = {"model_provider": "painter_probe", "model": "gpt-5.5",
               "model_providers.painter_probe.name": "Painter capability probe",
               "model_providers.painter_probe.base_url": f"http://127.0.0.1:{server.server_port}/v1",
               "model_providers.painter_probe.wire_api": "responses",
               "model_providers.painter_probe.requires_openai_auth": False}
    client = CodexClient(args.codex, directory, events.append, overrides=options, credentials="ephemeral")
    try:
        deadline = time.monotonic() + 30
        started = False
        completed = False
        calls = 0
        seen = []
        while time.monotonic() < deadline and not completed:
            client.poll()
            if client.initialized and not client.pending and not started:
                # This fixture uses a credential-free local provider. No account login.
                client.account = {"type": "apiKey"}
                client.run(dict(request_id="probe", tools=tools(), instructions=INSTRUCTIONS,
                                input="Inspect the current Painter context", model="gpt-5.5"))
                started = True
            while events:
                event = events.pop(0)
                seen.append(event)
                if event["event"] == "error":
                    raise AssertionError(event["message"])
                if event["event"] == "tool":
                    calls += 1
                    if event["tool"] == "get_context":
                        result = dict(layers=[], command_schema=SCHEMA)
                    else:
                        assert event["tool"] == "propose_changes", event
                        proposal = validate(event["arguments"]["proposal_json"])
                        assert proposal["commands"][0]["op"] == "add_layer"
                        result = dict(valid=True, applied=False)
                    client.tool_result(dict(call_id=event["call_id"], result=result))
                if event["event"] == "completed":
                    assert event["status"] == "completed", event
                    completed = True
            time.sleep(.01)
        assert completed and calls == 2 and len(requests) == 3, (completed, calls, len(requests))
        # The artist follows the turn: reasoning summaries and progress notes reach
        # Blender as they stream, apart from the reply.
        assert requests[0].get("reasoning", {}).get("summary") == "auto", requests[0].get("reasoning")
        streamed = {kind: "".join(e["text"] for e in seen if e["event"] == kind) for kind in ("thinking", "commentary", "text")}
        assert streamed == dict(thinking=SUMMARY, commentary=NOTE, text=ANSWER), streamed
        assert next(e for e in seen if e["event"] == "turn_metadata")["time_limit"] == 900
        expected = {t["name"] for t in tools()}
        for request in requests:
            inventory = request["tools"]
            found = {t.get("name", t.get("function", {}).get("name", t.get("type"))) for t in inventory}
            # This runtime always advertises the read-only skills namespace and
            # a question tool. Neither grants Blender or environment execution.
            # Native imagegen is account/provider gated. This credential-free
            # fake provider does not advertise it; the live account test does.
            required = expected | {"skills", "request_user_input"}
            assert required <= found <= required | {"image_generation"}, found
            skills = next(t for t in inventory if t.get("name") == "skills")
            assert {t["name"] for t in skills["tools"]} == {"list", "read"}, skills
            proposal = next(t for t in inventory if t.get("name") == "propose_changes")
            assert proposal["parameters"]["properties"]["proposal_json"]["type"] == "string"
        assert not (Path(directory) / "auth.json").exists(), "Probe persisted authentication unexpectedly"
        report = dict(result="PASS", runtime=client.version, painter_tools=sorted(expected),
                      builtin_read_only_tools=["skills.list", "skills.read", "request_user_input"],
                      native_image_generation_advertised="image_generation" in found,
                      context_and_proposal_roundtrip=True, progress_streams=sorted(streamed))
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
    # Exercise the actual helper executable and authenticated socket too.
    connection = Connection(args.codex, Path(directory) / "companion", credentials="ephemeral")
    try:
        events = []
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            events.extend(connection.poll())
            error = next((e for e in events if e["event"] == "error"), None)
            assert not error, error
            if any(e["event"] == "account" for e in events): break
            time.sleep(.02)
        assert connection.authenticated
        assert any(e["event"] == "account" and not e["connected"] for e in events), events
        report["companion_handshake"] = True
    finally:
        connection.close()
    assert connection.process.poll() is not None
    report["companion_cleanup"] = True
    report["live_account_used"] = False
    print(json.dumps(report, indent=2))
