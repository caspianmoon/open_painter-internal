"""Weapon evidence inventory and untrusted archive boundary regressions."""
import importlib.util
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/benchmarks/material_director"))
from view_inventory import inventory

spec = importlib.util.spec_from_file_location("benchmark_weapons", ROOT / "scripts/benchmark_weapons.py")
weapons = importlib.util.module_from_spec(spec)
spec.loader.exec_module(weapons)


class WeaponProtocolTests(unittest.TestCase):
    def test_revised_protocol_keeps_exact_initial_prompt_and_one_followup(self):
        from weapon_protocol import PROMPTS, SURFACE_PROMPTS, unattempted_stages
        stages = unattempted_stages('Not started', True)
        self.assertEqual(len(stages), 2)
        self.assertEqual(SURFACE_PROMPTS[0], PROMPTS[0])
        self.assertEqual(stages[1]['prompt'], 'This still looks too empty and procedural. Make it look like a professionally textured hero weapon from a AAA post-apocalyptic game.')
        self.assertEqual(len(unattempted_stages('Not started')), 3)

    def test_every_state_including_failure_has_all_five_views(self):
        plan = inventory({"protocol": "two_weapon_v1"})
        for label in ("before", "after_create", "after_refine_1", "after_refine_2",
                      "failure_create", "failure_refine_1", "failure_refine_2"):
            self.assertEqual({v for v, _ in plan[label]}, {"left", "right", "front", "rear", "close"})
        names = [name for views in plan.values() for _, name in views]
        self.assertEqual(len(names), len(set(names)))

    def test_existing_benchmark_inventory_unchanged(self):
        self.assertEqual(inventory()["before"], [("front", "00_before.png")])
        self.assertEqual(len(inventory({"protocol_version": 3, "case_id": "T04"})["after_create"]), 5)

    def test_archive_traversal_and_symlink_are_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for member in ("open_painter_weapon_test/../../escaped", "/tmp/escaped",
                           "open_painter_weapon_test/../escaped", "open_painter_weapon_test\\..\\escaped"):
                archive = root / "bad.zip"
                with zipfile.ZipFile(archive, "w") as handle:
                    handle.writestr(member, "data")
                with self.assertRaises(ValueError):
                    weapons.extract(archive, root / "out")
                self.assertFalse((root / "out").exists())
            with zipfile.ZipFile(archive, "w") as handle:
                info = zipfile.ZipInfo("open_painter_weapon_test/link")
                info.external_attr = 0o120777 << 16
                handle.writestr(info, "/tmp/escaped")
            with self.assertRaises(ValueError):
                weapons.extract(archive, root / "out")


if __name__ == "__main__":
    unittest.main()
