"""Reopen actual weapon checkpoints after testing; never resubmit an AI prompt.

blender --background --factory-startup --disable-autoexec --python this_file -- OUTPUT
Control probes change only the reopened in-memory copy and never save it.
"""
import json
import sys
from pathlib import Path

import bpy

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[2] / "src")]
import open_painter
from open_painter.authoring import document
from capture_evidence import _source_images, fixed_rig_check, material_state
from prepare_fixtures import object_snapshot
from runtime import sha256, write_json


def verify(root):
    open_painter.register()
    results = {}
    for weapon in ("assault_rifle", "smg"):
        output = root / weapon
        audit = json.loads((root / "fixtures" / (weapon + ".json")).read_text(encoding="utf-8"))
        records = []
        for state_path in sorted(output.glob("*_state.json")):
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if not state.get("saved_state"):
                continue
            path = output / state["saved_state"]["path"]
            record = dict(checkpoint=path.name, status="PASS", errors=[], sets=[])
            try:
                assert sha256(path) == state["saved_state"]["sha256"], "Checkpoint checksum differs"
                bpy.ops.wm.open_mainfile(filepath=str(path), use_scripts=False, load_ui=False)
                scene = bpy.context.scene
                config = json.loads(scene["md_benchmark_config"])
                assert fixed_rig_check(scene, config)["status"] == "PASS", "Frozen rig changed"
                objects = [object_snapshot(obj) for obj in bpy.data.collections["TEST_MODEL"].objects]
                assert objects == audit["prepared_objects"], "Frozen mesh/UV state changed"
                actual = material_state(scene, config)
                for ts in scene.open_painter.texture_sets:
                    expected = state["state"]["texture_sets"][ts.id]
                    observed = actual["texture_sets"][ts.id]
                    assert observed["semantic_sha256"] == expected["semantic_sha256"], "Reopened authoring state differs"
                    sources = _source_images(document.snapshot(ts))
                    missing = [image.name for image in sources.values() if not image.packed_file]
                    assert not missing, "Unpacked source images: " + repr(missing)
                    controls = []
                    # Ordinary scalar controls prove persistence/editability of
                    # the actual authored components, without adding any recipe.
                    before = document.canonical(document.snapshot(ts))
                    for layer in ts.layers:
                        owners = [(layer, "opacity", "layer")]
                        owners.extend((mask, "strength", "mask") for mask in layer.masks
                                      if hasattr(mask, "strength"))
                        for owner, field, kind in owners:
                            original = getattr(owner, field)
                            target = .25 if abs(original - .25) > .01 else .75
                            try:
                                setattr(owner, field, target)
                                assert abs(getattr(owner, field) - target) < 1e-6, "Control is not writable"
                                assert document.canonical(document.snapshot(ts)) != before, "Control absent from document"
                            finally:
                                setattr(owner, field, original)
                            assert document.canonical(document.snapshot(ts)) == before, "Control failed to restore"
                            controls.append(dict(kind=kind, layer=layer.name, owner=owner.name, field=field))
                    record["sets"].append(dict(name=ts.name, layers=len(ts.layers),
                        masks=sum(len(layer.masks) for layer in ts.layers), packed_source_count=len(sources),
                        controls_verified=controls,
                        limitation="Writable saved controls verified; individual visual influence is not graded by this probe"))
                record["checkpoint_unchanged_on_disk"] = sha256(path) == state["saved_state"]["sha256"]
                assert record["checkpoint_unchanged_on_disk"]
            except Exception as exc:
                record["status"] = "FAIL"
                record["errors"].append(str(exc))
            records.append(record)
        results[weapon] = dict(status="PASS" if records and all(r["status"] == "PASS" for r in records) else "FAIL",
                               checkpoints=records, ai_calls=0, files_saved=False)
        write_json(output / "reopen_verification.json", results[weapon])
    write_json(root / "reopen_verification.json", results)
    print("WEAPON_REOPEN", json.dumps({w: r["status"] for w, r in results.items()}), flush=True)
    if any(r["status"] != "PASS" for r in results.values()):
        raise RuntimeError("Checkpoint verification failed; see reopen_verification.json")


if __name__ == "__main__":
    verify(Path(sys.argv[sys.argv.index("--") + 1]).resolve())
