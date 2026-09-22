"""Verify frozen weapon presentation and capture neutral evidence before inference."""
import json
import sys
from pathlib import Path

import bpy

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[2] / "src")]
import open_painter
from capture_evidence import fixed_rig_check, render_primary_views
from prepare_fixtures import fixed_rig_record
from runtime import write_json

open_painter.register()
root = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
lighting = None
for weapon in ("assault_rifle", "smg"):
    fixture = root / "fixtures" / (weapon + ".blend")
    bpy.ops.wm.open_mainfile(filepath=str(fixture), use_scripts=False)
    scene = bpy.context.scene
    config = json.loads(scene["md_benchmark_config"])
    assert fixed_rig_check(scene, config)["status"] == "PASS"
    record = fixed_rig_record(scene, config)
    shared = {key: record[key] for key in ("lights", "world", "render", "eevee", "color_management", "units")}
    if lighting is not None:
        assert shared == lighting, "Weapons use different lighting/exposure"
    lighting = shared
    before = record
    out = root / "preflight" / weapon
    result = render_primary_views(bpy.context, out, "before", config)
    assert result["status"] == "CAPTURED", result
    assert {v["pose"] for v in result["views"]} == {"left", "right", "front", "rear", "close"}
    assert fixed_rig_record(scene, config) == before, "Capture altered the rig"
    # Detect drift rather than silently repairing it.
    original = scene.view_settings.exposure
    scene.view_settings.exposure += 1
    assert fixed_rig_check(scene, config)["status"] == "FAIL"
    scene.view_settings.exposure = original
    write_json(out / "verification.json", dict(status="PASS", views=result, ai_calls=0,
                                               identical_lighting=True, drift_rejected=True))
print("WEAPON_FIXTURES_PASS", flush=True)
