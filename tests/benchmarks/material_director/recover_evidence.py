"""Evidence-only Blender entry: no provider, instruction, regeneration or commit."""
import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
PRODUCT_ROOT = Path(os.environ.get("OP_MD_PRODUCT_ROOT", HERE.parents[2])).resolve()
sys.path[:0] = [str(PRODUCT_ROOT / "src"), str(HERE)]


def main():
    import bpy
    import open_painter
    from capture_evidence import capture_checkpoint, material_state
    from runtime import sha256, write_json
    request = json.loads(Path(os.environ["OP_MD_EVIDENCE_RECOVERY"]).read_text(encoding="utf-8"))
    state, output = Path(request["state"]), Path(request["output"])
    if sha256(state) != request["state_sha256"]:
        raise ValueError("Recovery checkpoint hash changed")
    open_painter.register()
    bpy.ops.wm.open_mainfile(filepath=str(state), use_scripts=False)
    record = dict(record_version=3, role="RECOVERED_EVIDENCE", ai_success=False,
                  provider_connected=False, original_run=request["original_run"],
                  product_build=request.get("product_build"), product_provenance=request.get("product_provenance"),
                  source_state=dict(path=str(state), sha256=request["state_sha256"]),
                  label=request["label"], status="RUNNING")
    write_json(output / "recovery.json", record)
    from open_painter.evaluation import shader_preview
    import time
    deadline, ready_frames = time.monotonic() + 120, 0
    before = material_state(bpy.context.scene)

    def capture_when_ready():
        nonlocal ready_frames
        try:
            ready = True
            for ts in bpy.context.scene.open_painter.texture_sets:
                shader_preview.tick(bpy.context.scene, ts)
                ready = ready and shader_preview.current(bpy.context.scene, ts)
            ready_frames = ready_frames + 1 if ready else 0
            if ready_frames < 5:
                if time.monotonic() > deadline:
                    raise TimeoutError("Saved material preview failed to settle for evidence recovery")
                return .1
            captured = capture_checkpoint(bpy.context, output, request["label"], config=request["config"])
            record.update(evidence=captured, status="CAPTURED_VERIFIED" if captured.get("views", {}).get("status") == "CAPTURED" else "CAPTURE_FAILED")
        except Exception as exc:
            record.update(status="CAPTURE_FAILED", cause_code="EVIDENCE_RECOVERY_EXCEPTION", error=repr(exc))
        record["material_unchanged"] = material_state(bpy.context.scene) == before
        record["checkpoint_bytes_unchanged"] = sha256(state) == request["state_sha256"]
        if not record["material_unchanged"] or not record["checkpoint_bytes_unchanged"]:
            record.update(status="CAPTURE_FAILED", cause_code="RECOVERY_MODIFIED_STATE")
        write_json(output / "recovery.json", record)
        bpy.ops.wm.quit_blender()
        return None
    bpy.app.timers.register(capture_when_ready, first_interval=.5)


if __name__ == "__main__":
    main()
