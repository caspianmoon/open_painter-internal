"""Synthetic evidence-integrity tests, never Blender output or quality ratings.

The non-PNG payloads are deliberately opaque fixture bytes: these checks verify
the captured-file hash boundary, not NumPy decoding or material appearance.
"""
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/benchmarks"))
from material_director import runner
from material_director.view_inventory import inventory


def png(side=32, color=b"\x80\x90\xa0"):
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", side, side, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\0" + color * side) * side))
            + chunk(b"IEND", b""))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def complete_fixture(directory):
    """Build all four checkpoints and three applied-stage receipt inventories."""
    channels = ("BASE_COLOR", "ROUGHNESS", "METALLIC", "NORMAL", "HEIGHT", "AO", "EMISSION", "OPACITY")
    job = dict(kind="core", protocol_version=3, case=dict(id="T01"), quick=False)
    result = dict(status="COMPLETED", stages=[dict(name=name, status="APPLIED", applied=True,
        visible_complete=True) for name in ("create", "refine_1", "refine_2")])
    primary = {}
    raw = b"SYNTHETIC-HASH-BOUNDARY-NOT-A-BLENDER-OR-NUMPY-ARTIFACT"
    image, primary_image = png(), png(1024)

    def artifact(relative, data=raw):
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return dict(path=relative, sha256=hashlib.sha256(data).hexdigest())

    artifacts = {}
    for label, poses in inventory(dict(protocol_version=3, case_id="T01")).items():
        views = []
        for pose, filename in poses:
            record = artifact(filename, primary_image)
            primary[filename] = record["sha256"]
            views.append(dict(record, pose=pose))
        content = {}
        for channel in channels:
            record = artifact(f"{label}/{channel}.npy")
            preview = artifact(f"{label}/{channel}.png", image)
            content[channel] = dict(record, preview=preview["path"], preview_sha256=preview["sha256"],
                                    finite=True, status="CAPTURED")
        valid = artifact(f"{label}/valid.npy")
        samples = artifact(f"{label}/surface_samples.npz")
        effect = dict(artifact(f"{label}/effect_mask.npy"), finite=True)
        ui = dict(artifact(f"{label}/native_ui.png", image), texture_set_id="synthetic-ts")
        checkpoint = dict(state=dict(texture_sets={"synthetic-ts": dict(target=True)}),
            channels=dict(status="CAPTURED", sets={"synthetic-ts": dict(status="CAPTURED",
                channels=content, valid_samples=valid, surface_samples=samples,
                effect_masks={"synthetic-effect": effect})}),
            views=dict(status="CAPTURED", engine="BLENDER_EEVEE", resolution=[1024, 1024], views=views),
            ui_layer_mask_views=dict(status="CAPTURED", views=[ui]))
        write_json(directory / (label + "_state.json"), checkpoint)
        artifacts[label] = dict(channel=content["BASE_COLOR"]["path"], preview=content["BASE_COLOR"]["preview"],
                                validity=valid["path"], surface_sample=samples["path"], effect_mask=effect["path"])
        if label != "before":
            write_json(directory / (label.removeprefix("after_") + "_receipt.json"), [{"id": "synthetic-receipt"}])
    final = artifact("final.blend")
    write_json(directory / "finalization.json", dict(status="SAVED", packing_errors=[], final_blend=final,
        primary_evidence=dict(status="CAPTURED", files=primary)))
    return result, job, artifacts


class CapturedEvidenceHashes(unittest.TestCase):
    def test_complete_synthetic_inventory_passes_integrity_without_inventing_quality(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            result, job, _ = complete_fixture(directory)
            checked = runner._evidence_delivery(result, job, directory)
            self.assertTrue(checked["passed"], checked["errors"])
            self.assertEqual(checked["status"], "PASS")
            self.assertNotIn("quality", checked)
            self.assertNotIn("quality", result)
            self.assertTrue(all(stage["visible_complete"] for stage in result["stages"]))

    def test_changed_captured_bytes_fail_each_buffer_family(self):
        for family in ("channel", "preview", "effect_mask", "surface_sample", "validity"):
            with self.subTest(family=family), tempfile.TemporaryDirectory() as folder:
                directory = Path(folder)
                result, job, artifacts = complete_fixture(directory)
                path = directory / artifacts["after_refine_2"][family]
                path.write_bytes(png(color=b"\xa0\x80\x90") if family == "preview"
                                 else path.read_bytes() + b"changed-after-capture")
                checked = runner._evidence_delivery(result, job, directory)
                self.assertFalse(checked["passed"])
                self.assertTrue(any("hash mismatch" in error.lower() and path.name in error for error in checked["errors"]), checked)
                self.assertFalse(result["stages"][2]["visible_complete"])
                self.assertTrue(result["stages"][2]["applied"], "Evidence corruption must not rewrite committed execution")
                self.assertEqual(result["status"], "COMPLETED")
                self.assertEqual(result["evidence_status"], "CAPTURE_FAILED")

    def test_missing_original_channel_hash_cannot_be_blessed_at_packaging_time(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            result, job, _ = complete_fixture(directory)
            path = directory / "after_create_state.json"
            checkpoint = json.loads(path.read_text(encoding="utf-8"))
            del checkpoint["channels"]["sets"]["synthetic-ts"]["channels"]["BASE_COLOR"]["sha256"]
            write_json(path, checkpoint)
            checked = runner._evidence_delivery(result, job, directory)
            self.assertFalse(checked["passed"])
            self.assertTrue(any("hash mismatch" in error.lower() for error in checked["errors"]))


if __name__ == "__main__":
    unittest.main()
