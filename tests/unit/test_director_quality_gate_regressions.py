#!/usr/bin/env python3
"""Regression specifications for Material Director's quality gate.

Run against a local Open Painter checkout:
    python test_director_quality_gate_regressions.py --repo /path/to/open_painter

These use synthetic review records, not fabricated benchmark results. They import
and exercise the checkout's real scoring.py. Several tests are intended to fail
on the inspected 2a9ff0d branch until its quality gates are implemented.
This file has been syntax-checked, but not run against a repository checkout here.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
from pathlib import Path
import sys
import unittest
from types import ModuleType

SCORING: ModuleType


def load_scoring(repo: Path) -> ModuleType:
    path = repo.resolve() / "tests/benchmarks/material_director/scoring.py"
    if not path.is_file():
        raise FileNotFoundError(f"No Material Director scoring module at {path}")
    spec = importlib.util.spec_from_file_location("director_scoring_under_test", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def passed_check() -> dict:
    return {"passed": True, "evidence": ["synthetic-unit-test-record"]}


def records() -> tuple[dict, dict, dict]:
    """A fully delivered, reviewed, unassisted synthetic session."""
    names = ("create", "refine_1", "refine_2")
    case = {"id": "SYNTHETIC", "requirements": ["Material matches brief"]}
    result = {
        "run_id": "SYNTHETIC-H-1",
        "status": "COMPLETED",
        "attempted": True,
        "artist_intervention_count": 0,
        "stages": [
            {"name": name, "status": "APPLIED", "applied": True,
             "visible_complete": True}
            for name in names
        ],
        "checks": {key: passed_check() for key in SCORING.WORKFLOW_CHECKS},
        "hard_failures": [],
        "failures": [],
    }
    review = {
        "reviewers": [
            {"id": "synthetic-rater", "independent": True,
             "is_generating_ai": False, "role": "material_artist"}
        ],
        "acceptance_checks": {"AC-01": passed_check()},
        "stages": {
            name: {
                "acceptance": passed_check(),
                "ratings": [
                    {"reviewer_id": "synthetic-rater",
                     "evidence": ["synthetic-unit-test-record"],
                     "dimensions": {dimension: 5 for dimension in SCORING.DIMENSIONS}}
                ],
            }
            for name in names
        },
    }
    return result, case, review


def score(records_: tuple[dict, dict, dict]) -> dict:
    result, case, review = records_
    return SCORING.score_session(result, case, review)


SCORING = load_scoring(Path(__file__).resolve().parents[2])


class QualityGateRegressions(unittest.TestCase):
    def test_positive_control_can_pass(self) -> None:
        self.assertEqual(score(records())["verdict"], "PASS")

    def test_zero_final_quality_cannot_pass(self) -> None:
        data = records()
        rating = data[2]["stages"]["refine_2"]["ratings"][0]
        rating["dimensions"] = {dimension: 0 for dimension in SCORING.DIMENSIONS}
        self.assertNotEqual(score(data)["verdict"], "PASS")

    def test_unrealistic_final_appearance_cannot_hide_in_other_scores(self) -> None:
        data = records()
        data[2]["stages"]["refine_2"]["ratings"][0]["dimensions"]["material_appearance"] = 0
        self.assertNotEqual(score(data)["verdict"], "PASS")

    def test_bad_create_cannot_be_erased_by_later_revisions(self) -> None:
        data = records()
        data[2]["stages"]["create"]["ratings"][0]["dimensions"] = {
            dimension: 0 for dimension in SCORING.DIMENSIONS
        }
        self.assertNotEqual(score(data)["verdict"], "PASS")

    def test_failed_stage_acceptance_prevents_session_pass(self) -> None:
        data = records()
        data[2]["stages"]["refine_1"]["acceptance"]["passed"] = False
        self.assertNotEqual(score(data)["verdict"], "PASS")

    def test_missing_create_review_is_not_full_workflow_pass(self) -> None:
        data = records()
        data[2]["stages"]["create"]["ratings"] = []
        self.assertNotEqual(score(data)["verdict"], "PASS")

    def test_missing_final_review_remains_unscored(self) -> None:
        data = records()
        data[2]["stages"]["refine_2"]["ratings"] = []
        self.assertEqual(score(data)["verdict"], "NOT_SCORED")

    def test_preservation_failure_cannot_be_overridden_by_high_scores(self) -> None:
        data = records()
        data[0]["checks"]["preservation"]["passed"] = False
        self.assertNotEqual(score(data)["verdict"], "PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    args = parser.parse_args()
    global SCORING
    try:
        SCORING = load_scoring(args.repo)
    except (OSError, ImportError) as exc:
        parser.error(str(exc))
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(QualityGateRegressions)
    outcome = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if outcome.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
