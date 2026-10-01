"""Numeric infrastructure checks; synthetic arrays are not AI quality evidence.

Run with Blender's bundled Python (includes NumPy), or a Python with NumPy.
"""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys

import numpy as np

import annotations as subject


class AnnotationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fixture = "a" * 64

    def tearDown(self):
        self.temp.cleanup()

    def bundle(self, region, measurements):
        spec = dict(schema_version=1, annotator="Synthetic unit-test evaluator",
                    declared_before_ai_outputs=True, fixtures=[dict(fixture="test.blend", fixture_sha256=self.fixture,
                    regions=[dict(id="target", texture_set_id="set", **region)], measurements=measurements)])
        source = self.root / "draft.json"
        source.write_text(json.dumps(spec), encoding="utf-8")
        return subject.freeze(source, self.root / "frozen")

    def checkpoint(self, label, values):
        values = np.asarray(values, dtype="<f4")
        path = self.root / (label + ".npy")
        valid = self.root / (label + "_valid.npy")
        np.save(path, values, allow_pickle=False)
        np.save(valid, np.ones(values.shape[:2], bool), allow_pickle=False)
        result = dict(label=label, state_path=str(self.root / (label + "_state.json")),
            channels=dict(sets={"set": dict(channels={"ROUGHNESS": dict(path=str(path),
                decoded=dict(sha256=hashlib.sha256(values.tobytes()).hexdigest()))}, valid_samples=dict(path=str(valid)))}))
        Path(result["state_path"]).write_text(json.dumps(result), encoding="utf-8")
        return result

    def test_surface_weighted_coverage_and_spill(self):
        np.savez(self.root / "region.npz", uv=np.array([[.25,.25],[.75,.25],[.25,.75],[.75,.75]]),
                 weight=np.array([9.,1.,1.,1.]), member=np.array([True,True,False,False]))
        config = self.bundle(dict(kind="surface_samples", path="region.npz"), [dict(id="coverage", kind="coverage",
            stage="after_create", region_id="target", channel="ROUGHNESS", selector=dict(minimum=[.9], maximum=[1.]))])
        checkpoint = self.checkpoint("after_create", [[[1],[0]],[[1],[0]]])
        result = subject.evaluate_checkpoint(checkpoint, config, fixture_sha256=self.fixture)["measurements"]["coverage"]
        self.assertEqual(result["status"], "MEASURED")
        self.assertAlmostEqual(result["coverage"], .9)
        self.assertAlmostEqual(result["spill_fraction_of_selected"], .1)
        self.assertEqual(result["weighting"], "surface_area")

    def test_preservation_ignores_only_declared_outside_region(self):
        np.save(self.root / "region.npy", np.array([[True,False],[True,False]]))
        config = self.bundle(dict(kind="uv_mask", path="region.npy"), [dict(id="preserve", kind="preservation",
            before="after_create", after="after_refine_1", region_id="target", channel="ROUGHNESS")])
        self.checkpoint("after_create", np.zeros((2,2,1)))
        after = self.checkpoint("after_refine_1", [[[0],[1]],[[0],[1]]])
        self.assertEqual(subject.evaluate_checkpoint(after, config, fixture_sha256=self.fixture)["status"], "MEASURED")
        after = self.checkpoint("after_refine_1", [[[.1],[1]],[[0],[1]]])
        value = subject.evaluate_checkpoint(after, config, fixture_sha256=self.fixture)
        self.assertEqual(value["status"], "FAIL")
        self.assertEqual(value["measurements"]["preserve"]["within_fraction"], .5)

    def test_band_count_uses_frozen_height_bins(self):
        heights = (np.arange(100) + .5) / 100
        np.savez(self.root / "region.npz", uv=np.stack((np.full(100,.25),heights),axis=-1),
                 weight=np.ones(100), member=np.ones(100,bool), height=heights)
        config = self.bundle(dict(kind="surface_samples", path="region.npz"), [dict(id="bands", kind="bands",
            stage="after_create", region_id="target", channel="ROUGHNESS", bins=100,
            selector=dict(minimum=[.9],maximum=[1.]))])
        values = np.zeros((100,2,1)); values[20:25]=1; values[60:65]=1
        result = subject.evaluate_checkpoint(self.checkpoint("after_create",values),config,
            fixture_sha256=self.fixture)["measurements"]["bands"]
        self.assertEqual(result["band_count"],2)
        self.assertAlmostEqual(result["bands"][0]["center"],.225)
        self.assertAlmostEqual(result["bands"][1]["width"],.05)

    def test_dependency_drift_and_wrong_fixture_rejected(self):
        np.save(self.root / "region.npy", np.ones((2,2),bool))
        config = self.bundle(dict(kind="uv_mask",path="region.npy"),[])
        with self.assertRaises(ValueError):
            subject.verify_frozen(config["annotations_manifest"], fixture_sha256="b"*64)
        dependency = next((self.root / "frozen/regions").glob("*.npy"))
        with dependency.open("ab") as handle: handle.write(b"modified")
        with self.assertRaises(ValueError):
            subject.verify_frozen(config["annotations_manifest"])

    def test_no_silent_mask_resizing(self):
        np.save(self.root / "region.npy",np.ones((2,2),bool))
        config = self.bundle(dict(kind="uv_mask",path="region.npy"),[dict(id="coverage",kind="coverage",
            stage="after_create",region_id="target",channel="ROUGHNESS",selector=dict(minimum=[0.],maximum=[1.]))])
        result = subject.evaluate_checkpoint(self.checkpoint("after_create",np.zeros((4,4,1))),config,
            fixture_sha256=self.fixture)
        self.assertEqual(result["status"],"BLOCKED")

    def test_frozen_verification_needs_only_standard_library(self):
        np.save(self.root / "region.npy",np.ones((2,2),bool))
        config = self.bundle(dict(kind="uv_mask",path="region.npy"),[])
        subprocess.run([sys.executable,"-S","-c",
            "import sys;sys.path.insert(0,sys.argv[1]);import annotations;annotations.verify_frozen(sys.argv[2])",
            str(Path(__file__).resolve().parent),config["annotations_manifest"]],check=True)

    def test_probe_binding_cannot_invent_layer_ids(self):
        fixture = dict(editability_probe_requirements=[dict(id="paint",component="paint",field="value",value=.7,channel="ROUGHNESS")])
        checkpoint = dict(state=dict(texture_sets={"set":dict(semantic=dict(layers=[dict(id="layer",masks=[])]))}))
        binding = dict(bindings=[dict(requirement_id="paint",texture_set_id="set",layer_id="wrong")])
        with self.assertRaises(ValueError):subject.validate_probe_mapping(binding,checkpoint,fixture)
        binding["bindings"][0]["layer_id"]="layer"
        self.assertEqual(subject.validate_probe_mapping(binding,checkpoint,fixture)["editability_probes"][0]["value"],.7)


if __name__ == "__main__":
    unittest.main()
