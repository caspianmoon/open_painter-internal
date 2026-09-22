"""No-provider ordinary Refine preparation on a retained live-created material.

Uses an existing file read-only; does not generate, apply, render or save materials.
Override OP_DIRECTOR_CONTEXT_FIXTURE to check another saved authoring stack.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests/unit")]
import open_painter
from open_painter.ai import session
from open_painter.authoring import context, document, schema
from test_director_context_compaction import expand_context

fixture = Path(os.environ.get("OP_DIRECTOR_CONTEXT_FIXTURE", str(ROOT /
    ".blender-test/material-director-benchmark/remedy-v3-smoke-04/T01/H/1/after_create.blend")))
output = Path(os.environ.get("OP_DIRECTOR_CONTEXT_OUTPUT", str(ROOT /
    ".blender-test/material-director-remedy-validation/context-compaction")))
output.mkdir(parents=True, exist_ok=True)
source_hash = hashlib.sha256(fixture.read_bytes()).hexdigest()
open_painter.register()
bpy.ops.wm.open_mainfile(filepath=str(fixture), use_scripts=False)
assert session.connection is None
project = bpy.context.scene.open_painter
ts = project.texture_sets[project.active_texture_set]
before = document.canonical(document.snapshot(ts))
history_before = ts.director_history
settings = bpy.context.window_manager.open_painter_director
settings.mode, settings.scope = "REFINE", "SET"
settings.prompt = "Change only the body's blue paint to burgundy. Preserve every other material and mask."
settings.send_view = settings.send_maps = True
captured = []
original_compact = context.compact


def observe_compaction(value):
    captured.append(deepcopy(value))
    return original_compact(value)


with patch.object(context, "compact", side_effect=observe_compaction):
    payload = session.dry_payload(bpy.context)
body = json.loads(payload["input"])
provider_context = body["current_context"]
assert len(captured) == 1 and "context_encoding" in provider_context
expanded = expand_context(provider_context)
restored = {key: expanded[key] for key in captured[0]}
assert json.dumps(restored, sort_keys=True) == json.dumps(captured[0], sort_keys=True)
context_bytes = len(json.dumps(provider_context, allow_nan=False).encode())
assert context_bytes < schema.MAX_BYTES, (context_bytes, schema.MAX_BYTES)
assert document.canonical(document.snapshot(ts)) == before
assert ts.director_history == history_before
assert hashlib.sha256(fixture.read_bytes()).hexdigest() == source_hash
assert session.connection is None and payload["action"] == "run" and body["mode"] == "REFINE"
report = dict(status="PASS", provider_calls=0, material_changes_applied=False,
    fixture=str(fixture), fixture_sha256=source_hash, cap_bytes=schema.MAX_BYTES,
    full_authoring_context_bytes=len(json.dumps(captured[0], allow_nan=False).encode()),
    outgoing_context_bytes=context_bytes, outgoing_input_bytes=len(payload["input"].encode()),
    layers=len(provider_context["layers"]), regions=len(provider_context["regions"]),
    lossless_reconstruction=True, document_and_receipts_unchanged=True, original_file_unchanged=True,
    request_preparation="Public session.dry_payload; same capture/compose as Run, dry visual attachments")
(output / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
(output / "provider_context.json").write_text(json.dumps(provider_context, indent=2) + "\n")
print("SAVED_STACK_CONTEXT_COMPACTION_PASS", json.dumps(report), flush=True)
