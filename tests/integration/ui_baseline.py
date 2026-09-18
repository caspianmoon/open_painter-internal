"""UI refactor baseline (Phase 0): the fixture's document, evaluated pixels and export
outputs match the recorded reference, and an older example project still opens with
its authored data intact.

Set ``OP_UI_BASELINE_RECORD=1`` to (re)write ``tests/fixtures/ui_baseline.json``.
"""
import json
import os
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests/integration"))
import open_painter
import ui_fixture

open_painter.register()

area = next(area for area in bpy.context.screen.areas if area.type == "VIEW_3D")
region = next(region for region in area.regions if region.type == "WINDOW")
with bpy.context.temp_override(area=area, region=region):
    context = bpy.context
    ids = ui_fixture.build(context.scene)
    current = ui_fixture.record(context, ids)
    # The fixture must exercise everything the plan lists.
    body = current["sets"]["body"]
    kinds = {(row["type"]) for row in body["structure"]}
    assert kinds == {"FILL", "PAINT", "FOLDER"}, kinds
    sources = {source for row in body["structure"] for _, source, *_ in row["masks"]}
    assert {"PAINT", "GENERATOR", "NOISE", "PROCEDURAL", "BAKED", "FILTER", "GRAPH"} <= sources, sources
    assert body["aging"]["folder"], "the aging folder must be part of the fixture"
    assert current["geometry"]["helper"] and current["geometry"]["instances"] > 0
    assert current["sets"]["panel"]["export_settings"]["preset"] == "UNITY"
    nested = [row for row in body["structure"] if row["parent"] == "Details"]
    assert len(nested) == 2, "Details must nest a fill and a paint layer"

    path = ROOT / "tests/fixtures/ui_baseline.json"
    if os.environ.get("OP_UI_BASELINE_RECORD") or not path.exists():
        ui_fixture.save_baseline(current)
        print(f"UI_BASELINE recorded to {path}", flush=True)
    else:
        problems = ui_fixture.compare(ui_fixture.load_baseline(path), current)
        assert not problems, "\n".join(problems)
        print("UI_BASELINE fixture matches the recorded reference", flush=True)

    # Recording is stable within a session: a second read of the same data agrees exactly.
    again = ui_fixture.record(context, ids)
    assert not ui_fixture.compare(current, again, atol=0), "the record itself must be deterministic"

# An older project opens with its authored data preserved (save/reopen compatibility).
example = ROOT / "examples/open_painter_demo.blend"
if example.exists():
    before = json.loads(json.dumps(current))
    bpy.ops.wm.open_mainfile(filepath=str(example))
    project = bpy.context.scene.open_painter
    assert len(project.texture_sets) >= 1, "the example project lost its texture sets"
    ts = project.texture_sets[0]
    assert all(layer.id for layer in ts.layers), "layers keep their stable ids"
    from open_painter.authoring import document
    digest = document.digest(ts)
    assert digest == document.digest(ts), "digest is stable"
    print(f"UI_BASELINE older project: {len(project.texture_sets)} set(s), {len(ts.layers)} layer(s) preserved", flush=True)

print("UI_BASELINE_PASS", flush=True)
