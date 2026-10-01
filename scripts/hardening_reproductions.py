"""Reproduce the 0.13.0 audit findings against the checkout's real modules (hardening plan H0).

    python scripts/hardening_reproductions.py [--out .blender-test/quality/hardening/<label>.json]

Runs, in disposable directories and with the current ``open_painter.export.transaction`` and
``open_painter.export.padding``:

1. cleanup failure after a backup was removed (existing A and B, new C);
2. a competing file created after the final existence check with ``overwrite=False``;
3. an unreadable journal;
4. Fill Empty Space on sparse corner-island atlases (128/256/512/1024, 8-square island).

Each case records the concrete outcome (file bytes/hashes, unresolved texel counts), so the
same script documents the failure before the fix and the behaviour after it. It never
asserts; the regressions live in tests/unit/test_export_transaction.py and
tests/unit/test_export_padding.py.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

import numpy as np

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from open_painter.export import padding, transaction  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--out", type=Path, default=root / ".blender-test/quality/hardening/reproductions.json")
args = parser.parse_args()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def snapshot(folder):
    return {p.name: sha(p) for p in sorted(Path(folder).iterdir()) if p.is_file()}


def revision():
    try:
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True).stdout.strip()
        return {"commit": head, "local_modifications": bool(dirty), "modified": dirty.splitlines()[:40]}
    except OSError:
        return {}


record = {"revision": revision(), "python": sys.version.split()[0], "numpy": np.__version__,
          "transaction_module": str(Path(transaction.__file__)), "padding_module": str(Path(padding.__file__)), "cases": {}}


def case(name, fn):
    try:
        record["cases"][name] = fn()
    except Exception:
        record["cases"][name] = {"exception": traceback.format_exc()}
    print(name, json.dumps(record["cases"][name], default=str)[:400], flush=True)


def stage(folder, name, payload):
    path = Path(folder) / name
    path.write_bytes(payload)
    return path


def make_transaction(destinations, staged):
    tx = transaction.Transaction()
    for destination, source in zip(destinations, staged):
        tx.add(destination, source)
    return tx


def commit(tx, overwrite):
    """Commit through whichever API the checkout has; return (destinations, error)."""
    try:
        result = tx.commit(overwrite=overwrite)
        return getattr(result, "destinations", result), None
    except Exception as error:
        return None, f"{type(error).__name__}: {error}"


# ---- 1. cleanup failure after a backup was removed -------------------------------------------------
def cleanup_failure():
    with tempfile.TemporaryDirectory(prefix="op-h0-cleanup-") as tmp:
        out, staging = Path(tmp) / "out", Path(tmp) / "staging"
        out.mkdir(); staging.mkdir()
        old_a, old_b = b"old A" * 100, b"old B" * 100
        (out / "A.png").write_bytes(old_a); (out / "B.png").write_bytes(old_b)
        new_a, new_b, new_c = b"new A" * 100, b"new B" * 100, b"new C" * 100
        staged = [stage(staging, "A.png", new_a), stage(staging, "B.png", new_b), stage(staging, "C.png", new_c)]
        tx = make_transaction([out / "A.png", out / "B.png", out / "C.png"], staged)
        real_unlink = Path.unlink
        state = {"count": 0}

        def failing_unlink(self, missing_ok=False):
            if ".op-backup-" in self.name:
                state["count"] += 1
                if state["count"] == 2:          # A's backup went; B's removal fails
                    raise OSError("injected: cleanup failed removing the second backup")
            return real_unlink(self, missing_ok=missing_ok)
        Path.unlink = failing_unlink
        try:
            destinations, error = commit(tx, overwrite=True)
        finally:
            Path.unlink = real_unlink
        after_commit = snapshot(out)
        recovery = None
        try:
            recovery = transaction.recover(out)
        except Exception as exc:
            recovery = f"{type(exc).__name__}: {exc}"
        after_recovery = snapshot(out)
        expected = {"A.png": hashlib.sha256(new_a).hexdigest(), "B.png": hashlib.sha256(new_b).hexdigest(),
                    "C.png": hashlib.sha256(new_c).hexdigest()}
        return {"commit_error": error, "after_commit": after_commit, "recovery": recovery, "after_recovery": after_recovery,
                "expected_all_new": expected,
                "verdict": "PASS" if {k: v for k, v in after_recovery.items() if k in expected} == expected else "FAIL: mixed or missing outputs"}


# ---- 2. competing writer after the final existence check --------------------------------------------
def competing_writer():
    with tempfile.TemporaryDirectory(prefix="op-h0-race-") as tmp:
        out, staging = Path(tmp) / "out", Path(tmp) / "staging"
        out.mkdir(); staging.mkdir()
        foreign = b"someone else's file"
        staged = [stage(staging, "A.png", b"ours" * 50)]
        tx = make_transaction([out / "A.png"], staged)
        real_copy = transaction._copy_flushed

        def racing_copy(source, target):
            # The competitor lands between the validation and the publication of A.
            destination = Path(str(target).split(".op-staging-")[0])
            if destination.name == "A.png" and not destination.exists():
                destination.write_bytes(foreign)
            return real_copy(source, target)
        transaction._copy_flushed = racing_copy
        try:
            destinations, error = commit(tx, overwrite=False)
        finally:
            transaction._copy_flushed = real_copy
        current = (out / "A.png").read_bytes() if (out / "A.png").exists() else None
        return {"commit_error": error, "foreign_intact": current == foreign, "current_sha256": sha(out / "A.png") if current is not None else None,
                "leftovers": sorted(p.name for p in out.iterdir() if p.name != "A.png"),
                "verdict": "PASS" if current == foreign and error else "FAIL: the competing file was overwritten or the export did not refuse"}


# ---- 3. unreadable journal ------------------------------------------------------------------------------
def unreadable_journal():
    with tempfile.TemporaryDirectory(prefix="op-h0-journal-") as tmp:
        out = Path(tmp) / "out"
        out.mkdir()
        (out / "A.png").write_bytes(b"old A")
        (out / "A.png.op-backup-deadbeef").write_bytes(b"backup of A")
        journal = out / transaction.JOURNAL
        journal.write_bytes(b'{"transaction": "deadbeef", "items": [truncated')
        try:
            result = transaction.recover(out)
            error = None
        except Exception as exc:
            result, error = None, f"{type(exc).__name__}: {exc}"
        return {"recover_result": result, "error": error, "journal_kept": journal.exists(),
                "backup_kept": (out / "A.png.op-backup-deadbeef").exists(),
                "verdict": "PASS" if (error and journal.exists() and (out / "A.png.op-backup-deadbeef").exists())
                else "FAIL: the unreadable journal was discarded silently"}


# ---- 4. sparse corner-island fill ------------------------------------------------------------------------
def sparse_fill():
    results = {}
    for n in (128, 256, 512, 1024):
        covered = np.zeros((n, n), bool)
        covered[:8, :8] = True
        island = np.where(covered, 0, -1).astype(np.int32)
        donor, d2 = padding.nearest_donors(covered, island, "FILL")
        unresolved = int((~covered & (donor < 0)).sum())
        valid_pointers = bool((donor[donor >= 0] < n * n).all()) and bool(covered.ravel()[donor[donor >= 0]].all()) if (donor >= 0).any() else True
        results[str(n)] = {"unresolved_empty_texels": unresolved, "donors_point_at_valid_texels": valid_pointers,
                           "verdict": "PASS" if unresolved == 0 and valid_pointers else "FAIL"}
    return results


case("cleanup_failure_after_backup_removed", cleanup_failure)
case("competing_writer_after_validation", competing_writer)
case("unreadable_journal", unreadable_journal)
case("sparse_corner_island_fill", sparse_fill)
args.out.parent.mkdir(parents=True, exist_ok=True)
args.out.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
print("HARDENING_REPRODUCTIONS", args.out, flush=True)
